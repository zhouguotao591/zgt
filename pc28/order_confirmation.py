"""Receiver execution reports for individual orders in a signal batch."""
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


def attach(c, wire, rows, required, callback_url):
    wire['orders'] = [{'order_no': f"PC28-{row['id']}", 'direction_amount': row['message'],
                       'amount_cents': row['push_cents']} for row in rows]
    if required:
        wire['execution_callback'] = {'url': callback_url, 'version': 1}
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
        WHERE outbox_id=? AND status IN ('awaiting','unconfirmed')""",
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
    event_id = body.get('event_id')
    detail = body.get('detail', '')
    if (status not in ('executed','failed','timeout','rejected','cancelled','unknown')
        or not isinstance(order_no,str) or not order_no.startswith('PC28-')
        or not order_no[5:].isascii() or not order_no[5:].isdigit() or len(order_no)>23
        or not isinstance(event_id,str) or not isinstance(detail,str) or len(detail)>500):
        raise AuthError('回调字段无效')
    header = authorization or ''
    scoped = hmac.digest(token.encode(),f'{platform}\n{event_id}\n{order_no}'.encode(),'sha256').hex()
    if not header.isascii() or not (hmac.compare_digest(header,'Bearer '+token) or
                                    hmac.compare_digest(header,'PC28-Order '+scoped)):
        raise AuthError('无效回调认证',401)
    with service.lock,service.db.tx() as c:
        order = c.execute("""SELECT o.*,b.batch_id AS batch_key,r.request AS batch_request,e.status AS execution_status
            FROM robot_outbox o JOIN robot_execution e ON e.outbox_id=o.id
            JOIN robot_batch_items b ON b.outbox_id=o.id JOIN robot_batches r ON r.id=b.batch_id
            WHERE o.id=? AND o.platform_id=? AND o.attempts>0 AND o.is_test=0""",
            (int(order_no[5:]),platform)).fetchone()
        if not order or json.loads(order['batch_request']).get('event_id')!=event_id:
            raise AuthError('找不到与平台和事件编号匹配的订单',404)
        if order['execution_status']==status:
            return {'ok':True,'duplicate':True}
        if order['execution_status'] not in ('awaiting','unconfirmed'):
            raise AuthError('订单已终态，不能覆盖',409)
        if status=='unknown':
            if order['execution_status']=='awaiting':
                c.execute("UPDATE robot_execution SET status='unconfirmed',detail=?,updated=? WHERE outbox_id=?",(detail,utcnow(),order['id']))
                alert(c,order,'待核对',detail)
            return {'ok':True}
        if status=='executed':
            from .member_orders import import_signal,settle
            executed_at=body.get('executed_at')
            if type(executed_at) is not int or not 1<=executed_at<=time.time()+5:
                raise AuthError('缺少有效的执行时间')
            wire=json.loads(order['batch_request'])
            if executed_at>=wire['expires_at']:
                terminal(c,order,'rejected','接收端报告的执行时间已超过该单截止时间')
                return {'ok':True,'valid':False}
            placed=datetime.fromtimestamp(executed_at,timezone.utc).isoformat()
            import_signal(c,service,order,order['batch_key'],placed,utcnow())
            if not c.execute('SELECT 1 FROM member_orders WHERE outbox_id=?',(order['id'],)).fetchone():
                raise AuthError('无法核验会员订单归属',409)
            terminal(c,order,status,detail)
            settle(c)
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
