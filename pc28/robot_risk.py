"""Persistent stop-loss limits for each receiving robot."""

SCHEMA = """
CREATE TABLE IF NOT EXISTS robot_risk(
 bot_id INTEGER PRIMARY KEY, capital INTEGER NOT NULL, loss_mode TEXT NOT NULL,
 loss_limit INTEGER NOT NULL, profit_target INTEGER NOT NULL, trailing_bps INTEGER NOT NULL,
 baseline INTEGER NOT NULL, paused TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS robot_risk_holds(
 outbox_id INTEGER PRIMARY KEY, bot_id INTEGER NOT NULL, stake INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS robot_risk_holds_bot ON robot_risk_holds(bot_id);
"""


def migrate(c):
    fields = {r[1] for r in c.execute('PRAGMA table_info(robot_risk)')}
    for name, definition in (
        ('calibration_amount', 'INTEGER'), ('calibration_baseline', 'INTEGER'),
        ('calibration_pending', 'INTEGER NOT NULL DEFAULT 0'),
        ('calibrated_at', "TEXT NOT NULL DEFAULT ''"),
        ('fixed_stop_balance', 'INTEGER NOT NULL DEFAULT 0'),
        ('calibration_revision', 'INTEGER NOT NULL DEFAULT 0'),
        ('daily_loss_limit', 'INTEGER NOT NULL DEFAULT 0'),
        ('max_drawdown', 'INTEGER NOT NULL DEFAULT 0'),
        ('max_stake', 'INTEGER NOT NULL DEFAULT 0'),
        ('win_limit', 'INTEGER NOT NULL DEFAULT 0'),
        ('loss_streak_limit', 'INTEGER NOT NULL DEFAULT 0'),
        ('pause_draws', 'INTEGER NOT NULL DEFAULT 20'),
        ('pause_until_draw', 'INTEGER NOT NULL DEFAULT 0'),
        ('streak_baseline', 'INTEGER NOT NULL DEFAULT 0'),
        ('merged_loss_limit', 'INTEGER NOT NULL DEFAULT 0'),
        ('merged_pause_draws', 'INTEGER NOT NULL DEFAULT 20'),
        ('merged_baseline_draw', 'INTEGER NOT NULL DEFAULT 0'),
    ):
        if name not in fields:
            c.execute(f'ALTER TABLE robot_risk ADD COLUMN {name} {definition}')
    c.execute('''CREATE TABLE IF NOT EXISTS robot_calibration_orders(
        bot_id INTEGER NOT NULL,outbox_id INTEGER NOT NULL,PRIMARY KEY(bot_id,outbox_id))''')
    for rule in c.execute('SELECT * FROM robot_risk WHERE calibration_amount IS NOT NULL AND calibration_revision=0').fetchall():
        c.execute('''INSERT OR IGNORE INTO robot_calibration_orders
            SELECT ?,o.outbox_id FROM member_orders o JOIN member_cash_ledger l ON l.order_id=o.id
            WHERE o.bot_id=? AND l.kind='stake' AND (l.id<=? OR EXISTS(
                SELECT 1 FROM robot_outbox b WHERE b.id=o.outbox_id AND julianday(b.sent_at)<=julianday(?)))''',
            (rule['bot_id'],rule['bot_id'],rule['calibration_baseline'],rule['calibrated_at']))
        c.execute('''INSERT OR IGNORE INTO robot_calibration_orders
            SELECT h.bot_id,h.outbox_id FROM robot_risk_holds h JOIN robot_outbox o ON o.id=h.outbox_id
            WHERE h.bot_id=? AND julianday(o.sent_at)<=julianday(?)''',
            (rule['bot_id'],rule['calibrated_at']))
        c.execute("UPDATE robot_risk SET calibration_revision=1,paused=CASE WHEN paused<>'' THEN '校准基准已更新，请人工恢复订单' ELSE '' END WHERE bot_id=?",(rule['bot_id'],))


def config_values(body):
    from .robot_accounts import integer
    capital = integer(body.get('capital'), '配资（分）', 2, 1000000000000)
    mode = body.get('loss_mode')
    if mode not in ('percent', 'amount'):
        raise ValueError('请选择按比例或固定金额止损')
    limit = integer(body.get('loss_limit'), '最大亏损', 1, 5000 if mode == 'percent' else capital // 2)
    if mode == 'percent' and capital * limit < 10000:
        raise ValueError('按比例计算的最大亏损至少须为 0.01 U')
    target = integer(body.get('profit_target'), '盈利目标（分）', 1, 1000000000000)
    trailing = integer(body.get('trailing_bps'), '移动止损百分比基点', 1, 5000)
    return capital, mode, limit, target, trailing


def merged_streak(c, bot_id, baseline_draw):
    # A verified gap breaks continuity. Newer completed rounds still count.
    waiting = c.execute("SELECT target_draw,status FROM member_orders WHERE bot_id=? AND status IN ('pending','awaiting_result') AND target_draw>?", (bot_id, baseline_draw)).fetchall()
    blocked = {r['target_draw'] for r in waiting if r['status']=='pending'}
    gaps = {r['target_draw'] for r in waiting if r['status']=='awaiting_result'}
    blocked.update(r[0] for r in c.execute("SELECT o.target_draw FROM robot_risk_holds h JOIN robot_outbox o ON o.id=h.outbox_id WHERE h.bot_id=? AND o.target_draw>?", (bot_id, baseline_draw)))
    count=0;last=baseline_draw
    for row in c.execute("SELECT target_draw,COUNT(*) orders,SUM(CASE WHEN outcome='loss' THEN 1 ELSE 0 END) losses FROM member_orders WHERE bot_id=? AND status='settled' AND target_draw>? GROUP BY target_draw ORDER BY target_draw", (bot_id, baseline_draw)):
        if any(draw<=row['target_draw'] for draw in blocked):break
        if row['target_draw'] in gaps:
            count=0;last=row['target_draw']
            continue
        if any(last<draw<row['target_draw'] for draw in gaps):count=0
        count=count+1 if row['losses']==row['orders'] else 0
        last=row['target_draw']
    return count,last


UNCERTAIN_SQL="""(e.status='unconfirmed' OR e.status IN ('submitted','awaiting') AND
 (o.status IN ('failed','expired','cancelled') OR e.deadline<=CAST(strftime('%s','now') AS INTEGER))
 OR e.status IS NULL AND o.status IN ('failed','expired','cancelled','sent'))"""


def unresolved_orders(c,bot_id):
    return [dict(row) for row in c.execute("""SELECT h.outbox_id,o.target_draw,o.message,h.stake,
        o.sent_at,o.status transport_status,e.status execution_status,o.last_error
        FROM robot_risk_holds h JOIN robot_outbox o ON o.id=h.outbox_id
        LEFT JOIN robot_execution e ON e.outbox_id=h.outbox_id WHERE h.bot_id=? AND """+
        UNCERTAIN_SQL+' ORDER BY h.outbox_id',(bot_id,))]


def state(c, bot_id):
    rule = c.execute('SELECT * FROM robot_risk WHERE bot_id=?', (bot_id,)).fetchone()
    if not rule:
        return None
    calibrated = rule['calibration_amount'] is not None
    risk_capital = rule['calibration_amount'] if calibrated else rule['capital']
    ledger_start = rule['calibration_baseline'] if calibrated else rule['baseline']
    net = peak = 0
    for row in c.execute("""SELECT CASE WHEN o.outcome='execution_rejected' THEN 0 WHEN ? AND EXISTS(
        SELECT 1 FROM robot_calibration_orders s WHERE s.bot_id=o.bot_id AND s.outbox_id=o.outbox_id)
        THEN l.amount ELSE o.pnl END FROM member_orders o
        JOIN member_cash_ledger l ON l.order_id=o.id AND l.kind='settle'
        WHERE o.bot_id=? AND o.status IN ('settled','void') AND l.id>? ORDER BY l.id""",
        (calibrated, bot_id, ledger_start)):
        net += row[0]
        peak = max(peak, net)
    pending = c.execute("SELECT COALESCE(SUM(stake),0) FROM member_orders WHERE bot_id=? AND status IN ('pending','awaiting_result')",
                        (bot_id,)).fetchone()[0]
    held = c.execute('SELECT COALESCE(SUM(stake),0) FROM robot_risk_holds WHERE bot_id=?', (bot_id,)).fetchone()[0]
    risk_pending, risk_held = pending, held
    if calibrated:
        risk_pending=c.execute("""SELECT COALESCE(SUM(o.stake),0) FROM member_orders o WHERE o.bot_id=? AND o.status IN ('pending','awaiting_result')
            AND NOT EXISTS(SELECT 1 FROM robot_calibration_orders s WHERE s.bot_id=o.bot_id AND s.outbox_id=o.outbox_id)""",(bot_id,)).fetchone()[0]
        risk_held=c.execute('''SELECT COALESCE(SUM(h.stake),0) FROM robot_risk_holds h WHERE h.bot_id=?
            AND NOT EXISTS(SELECT 1 FROM robot_calibration_orders s WHERE s.bot_id=h.bot_id AND s.outbox_id=h.outbox_id)''',(bot_id,)).fetchone()[0]
    uncertain_orders=unresolved_orders(c,bot_id)
    unresolved=[row['outbox_id'] for row in uncertain_orders]
    limit = rule['loss_limit']
    if rule['loss_mode'] == 'percent':
        limit = risk_capital * limit // 10000
    floor = -min(limit, risk_capital // 2)
    if peak >= rule['profit_target']:
        floor = max(floor, peak - peak * rule['trailing_bps'] // 10000)
    funds = risk_capital + net
    from datetime import datetime,timezone
    from zoneinfo import ZoneInfo
    day=datetime.now(timezone.utc).astimezone(ZoneInfo('Asia/Shanghai')).date()
    daily=0;wins=losses=0;last_draw=last_order=0
    for order in c.execute("SELECT id,pnl,outcome,target_draw,settled_at FROM member_orders WHERE bot_id=? AND status='settled' ORDER BY id",(bot_id,)):
        if order['settled_at']:
            try:
                if datetime.fromisoformat(order['settled_at']).astimezone(ZoneInfo('Asia/Shanghai')).date()==day:daily+=order['pnl'] or 0
            except ValueError:pass
        if order['id']<=rule['streak_baseline']:continue
        if order['outcome']=='win':wins+=1;losses=0
        elif order['outcome']=='loss':losses+=1;wins=0
        last_draw=order['target_draw'];last_order=order['id']
    merged_losses,merged_last=merged_streak(c,bot_id,rule['merged_baseline_draw'])
    return {**dict(rule), 'merged_loss_streak':merged_losses,'merged_last_draw':merged_last, 'net': net, 'peak': peak, 'floor': floor,'daily_net':daily,
            'win_streak':wins,'loss_streak':losses,'last_draw':last_draw,'last_order':last_order,
            'risk_capital': risk_capital, 'funds': funds, 'available': funds-risk_pending-risk_held,
            'risk_pending': risk_pending, 'risk_held': risk_held,
            'pending': pending, 'held': held, 'uncertain': len(unresolved), 'unresolved': unresolved,'uncertain_orders':uncertain_orders}


def check(c, bot_id):
    risk = state(c, bot_id)
    if risk and risk['pause_until_draw']:
        latest=c.execute('SELECT COALESCE(MAX(id),0) FROM draws').fetchone()[0]
        if latest>=risk['pause_until_draw']:
            c.execute("UPDATE robot_risk SET paused='',pause_until_draw=0,streak_baseline=?,merged_baseline_draw=? WHERE bot_id=?",(risk['last_order'],latest,bot_id))
            risk=state(c,bot_id)
    if risk and not risk['paused']:
        reason = ('机器人可用金额不足，停止下单' if risk['available'] <= 0
                  else f"可用金额 {risk['available']/100:.2f} U 低于固定止损金额 {risk['fixed_stop_balance']/100:.2f} U"
                  if risk['fixed_stop_balance'] > 0 and risk['available'] < risk['fixed_stop_balance']
                  else '触及配资50%强制止损线' if risk['net'] <= -(risk['risk_capital'] // 2)
                  else '触及移动止损线' if risk['net'] <= risk['floor'] and risk['peak'] >= risk['profit_target']
                  else '触及固定止损线' if risk['net'] <= risk['floor']
                  else '触及机器人单日最大亏损' if risk['daily_loss_limit'] and risk['daily_net']<=-risk['daily_loss_limit']
                  else '触及机器人最大回撤' if risk['max_drawdown'] and risk['peak']-risk['net']>=risk['max_drawdown']
                  else '')
        if not reason and ((risk['win_limit'] and risk['win_streak']>=risk['win_limit']) or (risk['loss_streak_limit'] and risk['loss_streak']>=risk['loss_streak_limit'])):
            reason='机器人连续盈亏暂停';until=risk['last_draw']+risk['pause_draws']
            c.execute('UPDATE robot_risk SET pause_until_draw=? WHERE bot_id=?',(until,bot_id));risk['pause_until_draw']=until
        if not reason and risk['merged_loss_limit'] and risk['merged_loss_streak']>=risk['merged_loss_limit']:
            reason='机器人合并订单连续亏损暂停'
            until=risk['merged_last_draw']+risk['merged_pause_draws']
            c.execute('UPDATE robot_risk SET pause_until_draw=? WHERE bot_id=?',(until,bot_id));risk['pause_until_draw']=until
        if reason:
            c.execute('UPDATE robot_risk SET paused=? WHERE bot_id=?', (reason, bot_id))
            risk['paused'] = reason
    return risk


def baseline(c):
    return c.execute('SELECT COALESCE(MAX(id),0) FROM member_cash_ledger').fetchone()[0]


def save(c, bot_id, body):
    values = config_values(body)
    old=c.execute('SELECT merged_loss_limit FROM robot_risk WHERE bot_id=?',(bot_id,)).fetchone()
    from .robot_accounts import integer
    fixed_stop = integer(body.get('fixed_stop_balance', 0), '固定止损金额（分）', 0, 1000000000000)
    calibration = body.get('calibration_amount')
    if calibration is not None:
        calibration = integer(calibration, '校准可用金额（分）', 0, 1000000000000)
    c.execute("""INSERT INTO robot_risk(bot_id,capital,loss_mode,loss_limit,profit_target,trailing_bps,baseline)
        VALUES(?,?,?,?,?,?,?) ON CONFLICT(bot_id) DO UPDATE SET capital=excluded.capital,
        loss_mode=excluded.loss_mode,loss_limit=excluded.loss_limit,profit_target=excluded.profit_target,
        trailing_bps=excluded.trailing_bps""", (bot_id, *values, baseline(c)))
    # Omission preserves a previous absolute balance floor for older callers.
    if 'fixed_stop_balance' in body:
        c.execute('UPDATE robot_risk SET fixed_stop_balance=? WHERE bot_id=?', (fixed_stop, bot_id))
    for key in ('daily_loss_limit','max_drawdown','max_stake','win_limit','loss_streak_limit','pause_draws','merged_loss_limit','merged_pause_draws'):
        if key in body:
            value=integer(body[key],key,1 if key in ('pause_draws','merged_pause_draws') else 0,1000000000000 if key in ('daily_loss_limit','max_drawdown') else 2000000 if key=='max_stake' else 100000)
            c.execute('UPDATE robot_risk SET '+key+'=? WHERE bot_id=?',(value,bot_id))
    if body.get('merged_loss_limit',0)>0 and (not old or not old['merged_loss_limit']):
        c.execute('UPDATE robot_risk SET merged_baseline_draw=(SELECT COALESCE(MAX(id),0) FROM draws) WHERE bot_id=?',(bot_id,))
    if calibration is not None:
        calibrate(c, bot_id, calibration)
    # Capture attempts already in progress when the policy is first configured.
    c.execute("""INSERT OR IGNORE INTO robot_risk_holds
        SELECT id,bot_id,push_cents FROM robot_outbox WHERE bot_id=? AND is_test=0
        AND status IN ('sending','retry') AND attempts>0 AND push_cents>0""", (bot_id,))
    return check(c, bot_id)


def calibrate(c, bot_id, available):
    """The operator enters cash AFTER known open bets have been deducted."""
    from .model import utcnow
    pending = c.execute("SELECT COALESCE(SUM(stake),0) FROM member_orders WHERE bot_id=? AND status IN ('pending','awaiting_result')", (bot_id,)).fetchone()[0]
    c.execute('DELETE FROM robot_calibration_orders WHERE bot_id=?',(bot_id,))
    c.execute("INSERT INTO robot_calibration_orders SELECT bot_id,outbox_id FROM member_orders WHERE bot_id=? AND status IN ('pending','awaiting_result')",(bot_id,))
    c.execute('INSERT OR IGNORE INTO robot_calibration_orders SELECT bot_id,outbox_id FROM robot_risk_holds WHERE bot_id=?',(bot_id,))
    c.execute("UPDATE robot_risk SET calibration_revision=1,paused=CASE WHEN paused<>'' THEN '校准金额已更新，请人工恢复订单' ELSE '' END WHERE bot_id=?",(bot_id,))
    c.execute('UPDATE robot_risk SET calibration_amount=?,calibration_pending=?,calibration_baseline=?,calibrated_at=? WHERE bot_id=?',
              (available, pending, baseline(c), utcnow(), bot_id))


def admit(c, bot_id, members):
    risk = check(c, bot_id)
    if not risk:
        return ''
    if risk['paused']:
        return '机器人风控暂停：' + risk['paused']
    if risk['uncertain']:
        return '机器人存在待核对的发送记录'
    if any(not isinstance(row['push_cents'], int) or row['push_cents'] <= 0 for row in members):
        return '无法核验机器人批次金额'
    extra = sum(row['push_cents'] for row in members if not c.execute(
        'SELECT 1 FROM robot_risk_holds WHERE outbox_id=?', (row['id'],)).fetchone())
    if risk['max_stake'] and any(row['push_cents']>risk['max_stake'] for row in members):return '本批订单超过机器人单笔金额上限'
    exposure=risk['risk_pending']+risk['risk_held']+extra
    if risk['daily_loss_limit'] and risk['daily_net']-exposure < -risk['daily_loss_limit']:return '本批订单可能突破机器人单日亏损预算'
    if risk['max_drawdown'] and risk['peak']-risk['net']+exposure>risk['max_drawdown']:return '本批订单可能突破机器人回撤预算'
    if risk['available'] <= 0 or risk['available'] < extra:
        return '机器人可用金额不足，停止下单'
    if risk['fixed_stop_balance'] > 0 and risk['available'] - extra < risk['fixed_stop_balance']:
        return '本批订单可能突破固定余额止损线'
    if risk['net'] - risk['risk_pending'] - risk['risk_held'] - extra < risk['floor']:
        return '本批订单可能突破机器人止损线'
    c.executemany('INSERT OR IGNORE INTO robot_risk_holds VALUES(?,?,?)',
                  ((row['id'], bot_id, row['push_cents']) for row in members))
    return ''


def resume(c, bot_id, reset):
    risk = state(c, bot_id)
    if not risk:
        raise ValueError('请先配置机器人风控')
    if type(reset) is not bool:
        raise ValueError('请选择是否重新计算盈亏')
    if risk['uncertain']:
        raise ValueError('请先核对发送状态不明的订单')
    if risk['fixed_stop_balance'] > 0 and risk['available'] < risk['fixed_stop_balance']:
        raise ValueError('可用金额仍低于固定止损金额，请先校准实际资金或调整止损金额')
    if risk['available'] <= 0:
        raise ValueError('可用金额不足，请先等待订单结算或校准实际资金')
    if reset:
        if risk['calibration_amount'] is not None:
            # Resetting relative P&L must not replace calibrated money with nominal capital.
            calibrate(c, bot_id, risk['available'])
        else:
            c.execute('UPDATE robot_risk SET baseline=? WHERE bot_id=?', (baseline(c), bot_id))
        risk = state(c, bot_id)
    if risk['fixed_stop_balance'] > 0 and risk['available'] < risk['fixed_stop_balance']:
        raise ValueError('可用金额仍低于固定止损金额，请先校准实际资金或调整止损金额')
    if risk['net'] - risk['risk_pending'] - risk['risk_held'] <= risk['floor']:
        raise ValueError('仍处于止损线，请等待结算或选择重新计算盈亏')
    c.execute("UPDATE robot_risk SET paused='',pause_until_draw=0,streak_baseline=?,merged_baseline_draw=MAX(?,(SELECT COALESCE(MAX(id),0) FROM draws)) WHERE bot_id=?", (risk['last_order'],risk['merged_last_draw'],bot_id))
    return state(c, bot_id)


def reconcile_rejected(c, bot_id, outbox_id):
    execution=c.execute('SELECT status FROM robot_execution WHERE outbox_id=?',(outbox_id,)).fetchone()
    if outbox_id not in {row['outbox_id'] for row in unresolved_orders(c,bot_id)}:
        raise ValueError('订单尚未进入待核对状态')
    deleted = c.execute("""DELETE FROM robot_risk_holds WHERE bot_id=? AND outbox_id=?
        AND EXISTS(SELECT 1 FROM robot_outbox o WHERE o.id=robot_risk_holds.outbox_id
        AND o.status IN ('failed','expired','cancelled','sent'))
        AND NOT EXISTS(SELECT 1 FROM member_orders m WHERE m.outbox_id=robot_risk_holds.outbox_id)""",
        (bot_id, outbox_id)).rowcount
    if not deleted:
        raise ValueError('该记录不属于当前机器人、仍在发送或已入账')
    if execution:
        from .model import utcnow
        c.execute("UPDATE robot_execution SET status='rejected',detail='管理员核对接收端未执行',updated=? WHERE outbox_id=?",
                  (utcnow(),outbox_id))
