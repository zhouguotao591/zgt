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
    pending = c.execute("SELECT COALESCE(SUM(stake),0) FROM member_orders WHERE bot_id=? AND status='pending'",
                        (bot_id,)).fetchone()[0]
    held = c.execute('SELECT COALESCE(SUM(stake),0) FROM robot_risk_holds WHERE bot_id=?', (bot_id,)).fetchone()[0]
    risk_pending, risk_held = pending, held
    if calibrated:
        risk_pending=c.execute("""SELECT COALESCE(SUM(o.stake),0) FROM member_orders o WHERE o.bot_id=? AND o.status='pending'
            AND NOT EXISTS(SELECT 1 FROM robot_calibration_orders s WHERE s.bot_id=o.bot_id AND s.outbox_id=o.outbox_id)""",(bot_id,)).fetchone()[0]
        risk_held=c.execute('''SELECT COALESCE(SUM(h.stake),0) FROM robot_risk_holds h WHERE h.bot_id=?
            AND NOT EXISTS(SELECT 1 FROM robot_calibration_orders s WHERE s.bot_id=h.bot_id AND s.outbox_id=h.outbox_id)''',(bot_id,)).fetchone()[0]
    unresolved = [row[0] for row in c.execute("""SELECT h.outbox_id FROM robot_risk_holds h JOIN robot_outbox o ON o.id=h.outbox_id
        LEFT JOIN robot_execution e ON e.outbox_id=h.outbox_id WHERE h.bot_id=?
        AND (e.status='unconfirmed' OR e.status IS NULL AND o.status IN ('failed','expired','cancelled','sent'))
        ORDER BY h.outbox_id""", (bot_id,))]
    limit = rule['loss_limit']
    if rule['loss_mode'] == 'percent':
        limit = risk_capital * limit // 10000
    floor = -min(limit, risk_capital // 2)
    if peak >= rule['profit_target']:
        floor = max(floor, peak - peak * rule['trailing_bps'] // 10000)
    funds = risk_capital + net
    return {**dict(rule), 'net': net, 'peak': peak, 'floor': floor,
            'risk_capital': risk_capital, 'funds': funds, 'available': funds-risk_pending-risk_held,
            'risk_pending': risk_pending, 'risk_held': risk_held,
            'pending': pending, 'held': held, 'uncertain': len(unresolved), 'unresolved': unresolved}


def check(c, bot_id):
    risk = state(c, bot_id)
    if risk and not risk['paused']:
        reason = ('机器人可用金额不足，停止下单' if risk['available'] <= 0
                  else f"可用金额 {risk['available']/100:.2f} U 低于固定止损金额 {risk['fixed_stop_balance']/100:.2f} U"
                  if risk['fixed_stop_balance'] > 0 and risk['available'] < risk['fixed_stop_balance']
                  else '触及配资50%强制止损线' if risk['net'] <= -(risk['risk_capital'] // 2)
                  else '触及移动止损线' if risk['net'] <= risk['floor'] and risk['peak'] >= risk['profit_target']
                  else '触及固定止损线' if risk['net'] <= risk['floor'] else '')
        if reason:
            c.execute('UPDATE robot_risk SET paused=? WHERE bot_id=?', (reason, bot_id))
            risk['paused'] = reason
    return risk


def baseline(c):
    return c.execute('SELECT COALESCE(MAX(id),0) FROM member_cash_ledger').fetchone()[0]


def save(c, bot_id, body):
    values = config_values(body)
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
    pending = c.execute("SELECT COALESCE(SUM(stake),0) FROM member_orders WHERE bot_id=? AND status='pending'", (bot_id,)).fetchone()[0]
    c.execute('DELETE FROM robot_calibration_orders WHERE bot_id=?',(bot_id,))
    c.execute("INSERT INTO robot_calibration_orders SELECT bot_id,outbox_id FROM member_orders WHERE bot_id=? AND status='pending'",(bot_id,))
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
    c.execute("UPDATE robot_risk SET paused='' WHERE bot_id=?", (bot_id,))
    return state(c, bot_id)


def reconcile_rejected(c, bot_id, outbox_id):
    execution=c.execute('SELECT status FROM robot_execution WHERE outbox_id=?',(outbox_id,)).fetchone()
    if execution and execution['status']!='unconfirmed':
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
