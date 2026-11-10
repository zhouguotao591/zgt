"""Read-only views of a member's signal ledger."""
from contextlib import closing
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo
import json
import re

from .auth import AuthError

BJ = ZoneInfo('Asia/Shanghai')
API = {'dashboard', 'orders', 'order'}
ORDER_SOURCE = """member_orders o
 LEFT JOIN robot_accounts a ON a.id=o.follow_account_id AND a.user_id=o.user_id
 LEFT JOIN robot_bots b ON b.id=o.bot_id
 LEFT JOIN robot_platforms p ON p.id=o.platform_id"""
LABELS = """COALESCE(a.name, '历史账号 #' || o.follow_account_id, '账号归属缺失') account_name,
 COALESCE(b.name, '历史机器人 #' || o.bot_id, '机器人归属缺失') bot_name,
 COALESCE(p.name, '历史平台') platform_name"""
SUMMARY = """COUNT(*) total,
 COUNT(CASE WHEN status='settled' THEN 1 END) settled,
 COUNT(CASE WHEN status='pending' THEN 1 END) pending,
 COALESCE(SUM(CASE WHEN status='settled' THEN pnl END),0) net,
 COALESCE(SUM(CASE WHEN status='settled' THEN stake END),0) invested,
 COALESCE(SUM(CASE WHEN status='settled' THEN stake+pnl END),0) returned,
 COALESCE(SUM(CASE WHEN status='pending' THEN stake END),0) pending_amount"""


def midnight(day):
    return datetime.combine(day, time.min, BJ).astimezone(timezone.utc).isoformat()


def number(value):
    if not re.fullmatch(r'[1-9][0-9]{0,17}', value):
        raise ValueError('编号或分页参数无效')
    return int(value)


def ratio(net, principal):
    return net / principal if principal is not None and principal > 0 else None


def dashboard(c, uid):
    now = datetime.now(timezone.utc)
    today = now.astimezone(BJ).date()
    capital = c.execute('SELECT principal FROM member_accounts WHERE user_id=?', (uid,)).fetchone()
    principal = capital['principal'] if capital else None
    total = dict(c.execute('SELECT '+SUMMARY+' FROM member_orders WHERE user_id=?', (uid,)).fetchone())
    ledger = c.execute("""SELECT created,principal_after-principal_before delta FROM member_cash_ledger
                         WHERE user_id=? AND principal_after!=principal_before ORDER BY id""", (uid,)).fetchall()
    traceable = principal is not None and sum(r['delta'] for r in ledger) == principal
    periods = []
    for days in (1, 7, 30, 90):
        start = today - timedelta(days=days-1)
        boundary = midnight(start)
        row = c.execute("""SELECT COALESCE(SUM(CASE WHEN julianday(draw_time)<julianday(?) THEN pnl END),0) prior,
                           COALESCE(SUM(CASE WHEN julianday(draw_time)>=julianday(?) THEN pnl END),0) net,
                           COUNT(CASE WHEN julianday(draw_time)>=julianday(?) THEN 1 END) settled
                           FROM member_orders WHERE user_id=? AND status='settled'
                           AND julianday(draw_time)<=julianday(?)""", (boundary, boundary, boundary, uid, now.isoformat())).fetchone()
        movements = [r for r in ledger if datetime.fromisoformat(r['created']) >= datetime.fromisoformat(boundary)]
        opening = principal-sum(r['delta'] for r in movements)+row['prior'] if traceable else None
        periods.append({'days':days,'start':start.isoformat(),'end':today.isoformat(),
                        'opening':opening,'net':row['net'],'settled':row['settled'],
                        'roi':ratio(row['net'],opening) if not movements and row['settled'] else None})
    accounts = [dict(r) for r in c.execute("""WITH keys(id) AS (
        SELECT id FROM robot_accounts WHERE user_id=? UNION
        SELECT follow_account_id FROM member_orders WHERE user_id=?)
        SELECT k.id,COALESCE(a.name,'历史账号 #' || k.id,'账号归属缺失') name,
        COALESCE(a.enabled AND NOT a.deleted,0) enabled,
        COALESCE(p.name,'历史记录') platform_name, COALESCE(x.net,0) net,
        COALESCE(x.settled,0) settled,COALESCE(x.pending_amount,0) pending_amount
        FROM keys k LEFT JOIN robot_accounts a ON a.id=k.id AND a.user_id=?
        LEFT JOIN robot_platforms p ON p.id=a.platform_id
        LEFT JOIN (SELECT follow_account_id,SUM(CASE WHEN status='settled' THEN pnl END) net,
          COUNT(CASE WHEN status='settled' THEN 1 END) settled,
          SUM(CASE WHEN status='pending' THEN stake END) pending_amount
          FROM member_orders WHERE user_id=? GROUP BY follow_account_id) x ON x.follow_account_id IS k.id
        ORDER BY a.id IS NULL,a.deleted,k.id""", (uid,uid,uid,uid))]
    return {'total':total,'principal':principal,'settled_funds':principal+total['net'] if principal is not None else None,
            'roi':ratio(total['net'],principal) if total['settled'] else None,'periods':periods,'accounts':accounts,
            'as_of':now.isoformat()}


def orders(c, uid, q):
    page = number(q.get('page','1'))
    size = number(q.get('page_size','20'))
    if size not in (10,20,50,100):
        raise ValueError('每页条数无效')
    time_column = {'sent':'o.placed_at','draw':'o.draw_time'}.get(q.get('time','sent'))
    if not time_column:
        raise ValueError('时间口径无效')
    start = date.fromisoformat(q['start']) if q.get('start') else None
    end = date.fromisoformat(q['end']) if q.get('end') else None
    if any(d is not None and not 1900<=d.year<=9998 for d in (start,end)):
        raise ValueError('日期范围无效')
    if start and end and start>end:
        raise ValueError('开始日期不能晚于结束日期')
    where, args = ['o.user_id=?'], [uid]
    if start:
        where.append(f'julianday({time_column})>=julianday(?)')
        args.append(midnight(start))
    if end:
        where.append(f'julianday({time_column})<julianday(?)')
        args.append(midnight(end+timedelta(days=1)))
    if q.get('status'):
        if q['status'] not in ('pending','settled','void'):
            raise ValueError('订单状态无效')
        where.append('o.status=?')
        args.append(q['status'])
    accounts = [dict(r) for r in c.execute("""SELECT id,name FROM robot_accounts WHERE user_id=?
        UNION SELECT DISTINCT o.follow_account_id,COALESCE(a.name,'历史账号 #' || o.follow_account_id)
        FROM member_orders o LEFT JOIN robot_accounts a ON a.id=o.follow_account_id AND a.user_id=o.user_id
        WHERE o.user_id=? AND o.follow_account_id IS NOT NULL""", (uid,uid))]
    bots = [dict(r) for r in c.execute("""SELECT DISTINCT o.bot_id id,o.follow_account_id account_id,
        COALESCE(b.name,'历史机器人 #' || o.bot_id) name FROM member_orders o
        LEFT JOIN robot_bots b ON b.id=o.bot_id WHERE o.user_id=? AND o.bot_id IS NOT NULL
        UNION SELECT b.id,a.id,b.name FROM robot_subscriptions s
        JOIN robot_accounts a ON a.id=s.account_id AND a.user_id=?
        JOIN robot_bots b ON b.id=s.bot_id AND b.deleted=0
        WHERE s.enabled=1 AND a.deleted=0""", (uid,uid))]
    for key,column,options in [('accounts','follow_account_id',accounts),('bots','bot_id',bots)]:
        raw = q.get(key)
        if not raw:
            continue
        if len(raw)>4000:
            raise ValueError('筛选选项过多')
        ids = sorted({number(v) for v in raw.split(',')})
        if not set(ids).issubset({r['id'] for r in options}):
            raise AuthError('筛选范围无效',404)
        where.append(f"o.{column} IN ({','.join('?' for _ in ids)})")
        args.extend(ids)
    condition = ' WHERE '+' AND '.join(where)
    summary = dict(c.execute('SELECT '+SUMMARY+' FROM member_orders o'+condition,args).fetchone())
    pages = max(1,(summary['total']+size-1)//size)
    page = min(page,pages)
    rows = c.execute('SELECT o.id,o.target_draw,o.direction,o.stake,o.pnl,o.status,o.placed_at,o.draw_time,'+LABELS+
                     ' FROM '+ORDER_SOURCE+condition+f' ORDER BY julianday({time_column}) DESC,o.id DESC LIMIT ? OFFSET ?',
                     (*args,size,(page-1)*size)).fetchall()
    return {'rows':[dict(r) for r in rows],'summary':summary,'accounts':accounts,'bots':bots,
            'page':page,'pages':pages,'page_size':size}


def order(c, uid, q):
    row = c.execute("""SELECT o.id,o.target_draw,o.direction,o.stake,o.pnl,o.status,o.placed_at,o.draw_time,
         o.accepted_at,o.settled_at,o.result,o.total,o.outcome,o.batch_id,o.odds_json,
         d.a,d.b,d.c,
         (SELECT amount FROM member_cash_ledger l WHERE l.order_id=o.id AND l.kind='settle') credit,"""+
         LABELS+' FROM '+ORDER_SOURCE+' LEFT JOIN draws d ON d.id=o.target_draw WHERE o.id=? AND o.user_id=?',
         (number(q.get('id','')),uid)).fetchone()
    if not row:
        raise AuthError('订单不存在',404)
    result = dict(row)
    odds = json.loads(result.pop('odds_json'))
    from .model import settlement
    result['odds_factor'] = (settlement(result['stake'],result['direction'],result['total'],
                                      result['outcome'] in ('win','refund'),odds)['factor']
                             if result['status']=='settled' else None)
    if result['status']=='pending':
        result.update(a=None,b=None,c=None)
    return result


def read(db, user, resource, q):
    if not user or user['role']!='member':
        raise AuthError('请使用会员账号登录',401 if not user else 403)
    with closing(db.connect()) as c:
        c.execute('BEGIN')
        if resource=='dashboard':
            return dashboard(c,user['id'])
        return (orders if resource=='orders' else order)(c,user['id'],q)
