"""Apply receiver batch reports to the corresponding member orders."""
import hmac
import json
import time
from datetime import datetime,timezone

from .auth import AuthError
from .model import utcnow

SCHEMA = """
CREATE TABLE IF NOT EXISTS robot_execution(
 outbox_id INTEGER PRIMARY KEY, status TEXT NOT NULL DEFAULT 'awaiting',
 detail TEXT NOT NULL DEFAULT '', updated TEXT NOT NULL, deadline INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS robot_execution_due ON robot_execution(status,deadline);
"""
UI_SUBMISSION_ATTEMPTED = 'UI 自动化发送结果不确定，禁止自动重试：已尝试提交消息，结果不确定，请人工核对'


def attach(c, wire, rows, required, callback_url):
    if not required:
        wire.pop('orders', None)
        return
    if required:
        wire['orders'] = [{'order_no': f"PC28-{row['id']}", 'direction_amount': row['message'],
                           'amount_cents': row['push_cents']} for row in rows]
        version = wire.get('execution_callback', {}).get('version', 2)
        wire['execution_callback'] = {'url': callback_url, 'version': version}
        c.executemany("""INSERT OR IGNORE INTO robot_execution(outbox_id,updated,deadline)
            VALUES(?,?,?)""", ((row['id'], utcnow(), wire['draw_at'] + 120) for row in rows))
        c.executemany("""INSERT OR IGNORE INTO robot_risk_holds VALUES(?,?,?)""",
                      ((row['id'], row['bot_id'], row['push_cents']) for row in rows if row['bot_id']))


def alert(c, order, status, detail):
    from .notifications import enqueue
    bot = c.execute('SELECT name FROM robot_bots WHERE id=?', (order['bot_id'],)).fetchone()
    name = bot[0] if bot else order['name']
    message = (f"下单异常 · {status}\n会员 {order['recipient_name']} · 机器人 {name}\n"
               f"订单 PC28-{order['id']} · 第 {order['target_draw']} 期 · {order['message']}")
    enqueue(c, 'order', f"{order['id']}:{status}", message + ('\n' + detail if detail else ''))


def terminal(c, order, status, detail):
    changed = c.execute("""UPDATE robot_execution SET status=?,detail=?,updated=?
        WHERE outbox_id=? AND status IN ('awaiting','unconfirmed','submitted')""",
        (status, detail, utcnow(), order['id'])).rowcount
    if changed:
        c.execute('DELETE FROM robot_risk_holds WHERE outbox_id=?', (order['id'],))
        if status != 'executed':
            alert(c, order, status, detail)
    return bool(changed)


def receive(service, authorization, body):
    platform = body.get('platform_id')
    if type(platform) is not int or platform <= 0:
        raise AuthError('无效回调认证', 401)
    try:
        token = service.robot.platform_secret(platform).read()
    except (OSError, ValueError):
        raise AuthError('无效回调认证', 401) from None
    status = body.get('status')
    order_no = body.get('order_no')
    batch_id = body.get('batch_id')
    event_id = body.get('event_id')
    detail = body.get('detail', '')
    batch_mode = batch_id is not None
    valid_order = (isinstance(order_no,str) and order_no.startswith('PC28-')
                   and order_no[5:].isascii() and order_no[5:].isdigit() and len(order_no)<=23)
    if (status not in ('executed','failed','timeout','rejected','cancelled','unknown')
        or not isinstance(event_id,str) or not 1<=len(event_id)<=100
        or not isinstance(detail,str) or len(detail)>500
        or (batch_mode and (batch_id!=event_id or order_no is not None))
        or (not batch_mode and not valid_order)):
        raise AuthError('回调字段无效')
    header = authorization or ''
    identity = batch_id if batch_mode else order_no
    scoped = hmac.digest(token.encode(),f'{platform}\n{event_id}\n{identity}'.encode(),'sha256').hex()
    if not header.isascii() or not (hmac.compare_digest(header,'Bearer '+token) or
                                    hmac.compare_digest(header,'PC28-Order '+scoped)):
        raise AuthError('无效回调认证',401)
    with service.lock,service.db.tx() as c:
        orders = c.execute("""SELECT o.*,b.batch_id AS batch_key,r.request AS batch_request,e.status AS execution_status
            FROM robot_outbox o JOIN robot_execution e ON e.outbox_id=o.id
            JOIN robot_batch_items b ON b.outbox_id=o.id JOIN robot_batches r ON r.id=b.batch_id
            WHERE o.platform_id=? AND o.attempts>0 AND o.is_test=0 AND """ +
            ("b.batch_id=?" if batch_mode else "o.id=?") + " ORDER BY o.id",
            (platform,batch_id if batch_mode else int(order_no[5:]))).fetchall()
        wire = json.loads(orders[0]['batch_request']) if orders else {}
        if wire.get('event_id')!=event_id or wire.get('execution_callback',{}).get('version')!=(2 if batch_mode else 1):
            raise AuthError('找不到与平台和事件编号匹配的订单或批次',404)
        if batch_mode:
            expected = sorted(item['order_no'] for item in wire['orders'])
            if expected != sorted(f"PC28-{row['id']}" for row in orders):
                raise AuthError('批次订单明细不一致，需人工核对',409)
        results = [_apply(c,service,order,wire,status,detail,body) for order in orders]
        if status=='executed' and any(r.get('valid',True) and not r.get('duplicate') for r in results):
            from .member_orders import settle
            settle(c)
        if batch_mode:
            return {'ok':True,'orders':len(orders),'duplicate':all(r.get('duplicate') for r in results),
                    'valid':all(r.get('valid',True) for r in results)}
        return results[0]


def _apply(c, service, order, wire, status, detail, body):
        if status=='unknown' and detail.strip()==UI_SUBMISSION_ATTEMPTED:
            if order['execution_status']=='submitted':
                return {'ok':True,'duplicate':True}
            if order['execution_status'] in ('awaiting','unconfirmed'):
                c.execute("UPDATE robot_execution SET status='submitted',detail='',updated=? WHERE outbox_id=?",
                          (utcnow(),order['id']))
                return {'ok':True}
        if order['execution_status']==status:
            return {'ok':True,'duplicate':True}
        if order['execution_status'] not in ('awaiting','unconfirmed','submitted'):
            raise AuthError('订单已终态，不能覆盖',409)
        if status=='unknown':
            if order['execution_status']=='awaiting':
                c.execute("UPDATE robot_execution SET status='unconfirmed',detail=?,updated=? WHERE outbox_id=?",(detail,utcnow(),order['id']))
                alert(c,order,'待核对',detail)
            return {'ok':True}
        if status=='executed':
            from .member_orders import import_signal
            executed_at=body.get('executed_at')
            if type(executed_at) is not int or not 1<=executed_at<=time.time()+5:
                raise AuthError('缺少有效的执行时间')
            if executed_at>=wire['expires_at']:
                terminal(c,order,'rejected','接收端报告的执行时间已超过该单截止时间')
                return {'ok':True,'valid':False}
            placed=datetime.fromtimestamp(executed_at,timezone.utc).isoformat()
            import_signal(c,service,order,order['batch_key'],placed,utcnow())
            if not c.execute('SELECT 1 FROM member_orders WHERE outbox_id=?',(order['id'],)).fetchone():
                raise AuthError('无法核验会员订单归属',409)
            terminal(c,order,status,detail)
        else:
            terminal(c,order,status,detail)
        return {'ok':True}


def expire(c, now=None):
    now=int(time.time()) if now is None else now
    for row in c.execute("""SELECT o.* FROM robot_execution e JOIN robot_outbox o ON o.id=e.outbox_id
        WHERE e.status='awaiting' AND e.deadline<=? LIMIT 100""",(now,)).fetchall():
        detail='未收到执行终态，订单不计入有效记录，待核对'
        c.execute("UPDATE robot_execution SET status='unconfirmed',detail=?,updated=? WHERE outbox_id=?",
                  (detail,utcnow(),row['id']))
        alert(c,row,'回调超时',detail)
