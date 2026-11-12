"""Durable administrator alerts with subscriptions by event type."""
from datetime import datetime, timedelta, timezone
import json
import re
import time
import urllib.request
from zoneinfo import ZoneInfo

from .bot_secret import SecretStore
from .model import utcnow

EVENTS = ('order', 'strategy', 'ratio', 'daily_ratio', 'daily_profit')
SCHEMA = """
CREATE TABLE IF NOT EXISTS notice_subscriptions(
 id INTEGER PRIMARY KEY,name TEXT NOT NULL,chat_id TEXT NOT NULL,
 events TEXT NOT NULL,enabled INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS notice_events(
 kind TEXT NOT NULL,event_key TEXT NOT NULL,created TEXT NOT NULL,PRIMARY KEY(kind,event_key));
CREATE TABLE IF NOT EXISTS notice_queue(
 id INTEGER PRIMARY KEY,subscription_id INTEGER NOT NULL,message TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'pending',attempts INTEGER NOT NULL DEFAULT 0,
 due INTEGER NOT NULL DEFAULT 0,error TEXT NOT NULL DEFAULT '',created TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS notice_due ON notice_queue(status,due,id);
"""


def enqueue(c, kind, key, message):
    if not c.execute('INSERT OR IGNORE INTO notice_events VALUES(?,?,?)',(kind,key,utcnow())).rowcount:
        return
    c.execute("""INSERT INTO notice_queue(subscription_id,message,created)
        SELECT id,?,? FROM notice_subscriptions WHERE enabled=1 AND mode='fixed'
        AND EXISTS(SELECT 1 FROM json_each(events) WHERE value=?)""",(message,utcnow(),kind))
    c.execute("""INSERT INTO notice_queue(subscription_id,message,created,subscriber_chat)
        SELECT s.id,?,?,m.chat_id FROM notice_subscriptions s
        JOIN notice_bot_subscribers m ON m.subscription_id=s.id
        WHERE s.enabled=1 AND s.mode='bot' AND m.active=1
        AND EXISTS(SELECT 1 FROM json_each(s.events) WHERE value=?)""",
        (message,utcnow(),kind))


def hourly_ratios(c, start, end):
    row=c.execute("""SELECT COUNT(*) n,SUM(size='大') big,SUM(parity='单') odd FROM draws d
        WHERE julianday(d.draw_utc)>=julianday(?) AND julianday(d.draw_utc)<julianday(?)
        AND d.verification IN ('full_keno','crosschecked_abc')
        AND NOT EXISTS(SELECT 1 FROM conflicts x WHERE x.draw_id=d.id AND x.resolved=0)""",
        (start.isoformat(),end.isoformat())).fetchone()
    n=row['n']
    if not n:return
    lines=[]
    for a,b,count in [('大','小',row['big']),('单','双',row['odd'])]:
        gap=abs(2*count-n)
        if gap*100>4*n:
            direction,amount=(a,count) if count*2>n else (b,n-count)
            lines.append(f'{a}{b}达标方向：{direction} · 占比 {amount/n:.2%}（{amount}/{n}期） · 两侧占比差 {gap*100/n:.2f} 个百分点（阈值：超过4个百分点）')
    if lines:
        start=start.astimezone(ZoneInfo('Asia/Shanghai'));end=end.astimezone(ZoneInfo('Asia/Shanghai'))
        enqueue(c,'ratio',start.isoformat(),f'每小时开奖占比 · 北京时间 {start:%Y-%m-%d %H:%M}–{end:%H:%M}\n总开奖期数：{n} 期\n大：{row["big"]} 期（{row["big"]/n:.2%}） · 小：{n-row["big"]} 期（{(n-row["big"])/n:.2%}）\n单：{row["odd"]} 期（{row["odd"]/n:.2%}） · 双：{n-row["odd"]} 期（{(n-row["odd"])/n:.2%}）\n'+'\n'.join(lines))


def daily_reports(c, start, end):
    date = start.strftime('%Y-%m-%d')
    draws = c.execute("""SELECT COUNT(*) n,COALESCE(SUM(size='大'),0) big,
        COALESCE(SUM(parity='单'),0) odd FROM draws d
        WHERE julianday(d.draw_utc)>=julianday(?) AND julianday(d.draw_utc)<julianday(?)
        AND d.verification IN ('full_keno','crosschecked_abc')
        AND NOT EXISTS(SELECT 1 FROM conflicts x WHERE x.draw_id=d.id AND x.resolved=0)""",
        (start.isoformat(),end.isoformat())).fetchone()
    n = draws['n']
    lines = [f'每日开奖占比 · {date}（北京时间）', f'已核验开奖：{n} 期']
    for label, count in [('大',draws['big']),('小',n-draws['big']),('单',draws['odd']),('双',n-draws['odd'])]:
        lines.append(f'{label}：{count} 期 · {count/n if n else 0:.2%}')
    enqueue(c, 'daily_ratio', date, '\n'.join(lines))
    rows = c.execute("""SELECT o.target_draw,o.draw_time,COUNT(*) n,SUM(o.stake) stake,
        SUM(o.pnl) pnl,SUM(o.pnl>0) wins FROM member_orders o
        LEFT JOIN robot_execution e ON e.outbox_id=o.outbox_id
        WHERE o.status='settled' AND o.pnl IS NOT NULL AND (e.outbox_id IS NULL OR e.status IN ('executed','draw_settled'))
        AND julianday(o.draw_time)>=julianday(?) AND julianday(o.draw_time)<julianday(?)
        GROUP BY o.target_draw,o.draw_time ORDER BY julianday(o.draw_time),o.target_draw""",
        (start.isoformat(),end.isoformat())).fetchall()
    count = stake = wins = net = peak = drawdown = 0
    for row in rows:
        count += row['n']; stake += row['stake']; wins += row['wins']; net += row['pnl']
        peak = max(peak, net)
        drawdown = max(drawdown, peak-net)
    message = '\n'.join([
        f'全体会员订单日报 · {date}（北京时间）',
        f'订单数：{count}', f'总计下单金额：{stake/100:.2f} U',
        f'盈利金额：{net/100:+.2f} U', f'盈利比：{net/stake if stake else 0:.2%}',
        f'胜率：{wins/count if count else 0:.2%}', f'最大盈利金额：{peak/100:.2f} U',
        f'单日最大利润回撤：{drawdown/100:.2f} U',
        '按开奖日期统计截至推送时已结算的有效会员订单明细。',
        '盈利比=净盈利/下单金额；胜率=盈利订单数/订单数。',
        '最大盈利为当日累计净盈利峰值；回撤按每期开奖合计盈亏计算。'])
    enqueue(c, 'daily_profit', date, message)


def schedule_daily(c, now):
    end = now.astimezone(ZoneInfo('Asia/Shanghai')).replace(hour=0,minute=0,second=0,microsecond=0)
    today = end.date().isoformat()
    previous = c.execute('SELECT day FROM notice_daily_state WHERE id=1').fetchone()
    if previous and previous['day'] < today:
        daily_reports(c, end-timedelta(days=1), end)
        c.execute('UPDATE notice_daily_state SET day=? WHERE id=1', (today,))


def strategy_alerts(c, cfg, now):
    """Hourly alerts distinguish observed losses/drawdown from configured limits."""
    from .trend_strategy import settings as trend_settings
    sources=[]
    if cfg['simulation']['enabled']:
        sources.append(('orders',None,'三十期买单反转策略',cfg['simulation']))
    for axis in cfg['trend_simulation'].get('enabled_axes',[]):
        sources.append(('trend_orders',axis,'大小策略' if axis=='size' else '单双策略',trend_settings(cfg,axis)))
    sources.extend(('custom_orders',r['id'],r['name'],json.loads(r['settings']))
                   for r in c.execute('SELECT id,name,settings FROM custom_strategies WHERE enabled=1'))
    for table,key,name,settings in sources:
        scope='' if key is None else ' AND o.strategy=?'
        args=() if key is None else (key,)
        if table=='custom_orders':
            scope+=" AND NOT EXISTS(SELECT 1 FROM gap_order_tags g WHERE g.engine='custom' AND g.order_id=o.id)"
        all_rows=c.execute(f"""SELECT o.id,o.pnl,o.target_draw,o.settled FROM {table} o
            WHERE o.status='settled' AND o.pnl IS NOT NULL AND julianday(o.settled)<=julianday(?){scope}
            ORDER BY o.target_draw,o.id""",(now.isoformat(),*args)).fetchall()
        if not all_rows:continue
        day_start=now.astimezone(timezone.utc).replace(hour=0,minute=0,second=0,microsecond=0)
        recent_cutoff=now-timedelta(days=1)
        rows=[r for r in reversed(all_rows) if datetime.fromisoformat(r['settled'])>=recent_cutoff][:20]
        streak=0
        for row in rows:
            if row['pnl']>=0:break
            streak+=1
        wins=sum(r['pnl']>0 for r in rows)
        equity=peak=max_dd=0
        for row in all_rows:
            equity+=row['pnl'];peak=max(peak,equity);max_dd=max(max_dd,peak-equity)
        drawdown=peak-equity
        daily=sum(r['pnl'] for r in all_rows if datetime.fromisoformat(r['settled'])>=day_start)
        reasons=[]
        if streak>=5:reasons.append(f'连续亏损 {streak} 笔（阈值≥5笔）')
        if len(rows)==20 and wins*100<35*20:reasons.append(f'近20单盈利率 {wins/20:.1%}（阈值<35%）')
        daily_limit=settings.get('daily_loss_limit',0)
        dd_limit=settings.get('max_drawdown',0)
        if daily_limit>0 and -daily>=round(daily_limit*100):
            reasons.append(f'当日净亏损 {-daily/100:.2f} U，达到单日亏损限额 {daily_limit:g} U（UTC日）')
        if dd_limit>0 and drawdown>=round(dd_limit*100):
            reasons.append(f'当前回撤 {drawdown/100:.2f} U，达到回撤限额 {dd_limit:g} U')
        if reasons:
            net=sum(r['pnl'] for r in rows)/100
            message='\n'.join([
                f"策略表现异常 · {name}（{key or 'legacy'}）",
                '异常：'+'；'.join(reasons),
                f"近24小时内最近 {len(rows)} 单净收益：{net:+.2f} U",
                f"累计已结算净收益：{equity/100:+.2f} U",
                f"当前回撤：{drawdown/100:.2f} U；历史最大回撤：{max_dd/100:.2f} U",
                f"截至第 {all_rows[-1]['target_draw']} 期 · 检查时间（北京）：{now.astimezone(ZoneInfo('Asia/Shanghai')):%Y-%m-%d %H:%M:%S}",
                '统计范围：策略实时模拟订单，排除历史补单；不代表会员实际账户收益。'])
            enqueue(c,'strategy',f"{table}:{key}:{now.strftime('%Y%m%d%H')}",message)


class Notifications:
    def __init__(self,service):
        self.service=service
        from .order_confirmation import SCHEMA as execution_schema
        with service.db.tx() as c:
            for sql in (SCHEMA+execution_schema).split(';'):
                if sql.strip():c.execute(sql)
            for table, name, definition in (
                ('notice_subscriptions','mode',"TEXT NOT NULL DEFAULT 'fixed'"),
                ('notice_subscriptions','bot_id','TEXT'),
                ('notice_subscriptions','update_offset','INTEGER NOT NULL DEFAULT 0'),
                ('notice_queue','subscriber_chat','TEXT'),
            ):
                if name not in {r[1] for r in c.execute(f'PRAGMA table_info({table})')}:
                    c.execute(f'ALTER TABLE {table} ADD COLUMN {name} {definition}')
            c.execute("""CREATE TABLE IF NOT EXISTS notice_bot_subscribers(
                subscription_id INTEGER NOT NULL, chat_id TEXT NOT NULL, active INTEGER NOT NULL,
                PRIMARY KEY(subscription_id,chat_id))""")
            c.execute("CREATE UNIQUE INDEX IF NOT EXISTS notice_bot_listener ON notice_subscriptions(bot_id) WHERE mode='bot'")
            c.execute('CREATE TABLE IF NOT EXISTS notice_daily_state(id INTEGER PRIMARY KEY,day TEXT NOT NULL)')
            if not c.execute('SELECT 1 FROM notice_daily_state WHERE id=1').fetchone():
                today=datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat()
                c.execute('INSERT INTO notice_daily_state VALUES(1,?)',(today,))
                for sub in c.execute('SELECT id,events FROM notice_subscriptions').fetchall():
                    events=set(json.loads(sub['events']))
                    if 'ratio' in events:events.add('daily_ratio')
                    if 'order' in events:events.add('daily_profit')
                    c.execute('UPDATE notice_subscriptions SET events=? WHERE id=?',(json.dumps(sorted(events)),sub['id']))

    def secret(self,key):
        secret=SecretStore(self.service.root)
        secret.path=secret.path.with_name(f'notice-bot-{key}.dpapi')
        return secret

    def listing(self):
        rows=self.service.db.query("""SELECT s.id,s.name,s.chat_id,s.events,s.enabled,s.mode,
            (SELECT COUNT(*) FROM notice_bot_subscribers m WHERE m.subscription_id=s.id AND m.active=1) subscriber_count
            FROM notice_subscriptions s ORDER BY s.id""")
        return {'subscriptions':[{**r,'events':json.loads(r['events']),'token_configured':self.secret(r['id']).exists()} for r in rows],
                'recent':self.service.db.query('SELECT id,subscription_id,status,attempts,error,created FROM notice_queue ORDER BY id DESC LIMIT 20')}

    def save(self,body,user,ip):
        key=body.get('id');name=body.get('name');chat=body.get('chat_id')
        events=body.get('events');enabled=body.get('enabled');token=body.get('token','')
        mode=body.get('mode','fixed')
        if (not isinstance(name,str) or not 1<=len(name.strip())<=60 or
            mode not in ('fixed','bot') or
            (mode=='fixed' and (not isinstance(chat,str) or not re.fullmatch(r'-?[1-9][0-9]{0,18}',chat))) or
            not isinstance(events,list) or not events or any(e not in EVENTS for e in events) or type(enabled)!=bool):
            raise ValueError('请填写名称、数字 Chat ID 并至少选择一类通知')
        if token and (not isinstance(token,str) or not re.fullmatch(r'[0-9]{4,20}:[A-Za-z0-9_-]{20,100}',token)):
            raise ValueError('Telegram Bot Token 格式无效')
        with self.service.db.tx() as c:
            previous = None
            if key is None:
                if not token:raise ValueError('新增订阅需填写 Bot Token')
                key=c.execute('INSERT INTO notice_subscriptions(name,chat_id,events,enabled) VALUES(?,?,?,0)',
                              (name.strip(),chat,'[]')).lastrowid
            else:
                previous = c.execute('SELECT * FROM notice_subscriptions WHERE id=?',(key,)).fetchone() if type(key)==int else None
                if previous is None:raise ValueError('通知订阅不存在')
            bot_id = token.split(':',1)[0] if token else previous['bot_id'] if previous else None
            if mode=='bot' and not bot_id:
                bot_id=self.secret(key).read().split(':',1)[0]
            if mode=='bot' and c.execute("SELECT 1 FROM notice_subscriptions WHERE mode='bot' AND bot_id=? AND id<>?",
                                         (bot_id,key)).fetchone():
                raise ValueError('此 Telegram Bot 已配置私聊订阅')
            if token:self.secret(key).write(token)
            if enabled and not self.secret(key).exists():raise ValueError('启用前请配置 Bot Token')
            offset=previous['update_offset'] if previous and previous['bot_id']==bot_id else 0
            if previous and previous['bot_id']!=bot_id:
                c.execute('DELETE FROM notice_bot_subscribers WHERE subscription_id=?',(key,))
            c.execute('UPDATE notice_subscriptions SET name=?,chat_id=?,events=?,enabled=?,mode=?,bot_id=?,update_offset=? WHERE id=?',
                      (name.strip(),chat if mode=='fixed' else '',json.dumps(sorted(set(events))),enabled,mode,bot_id,offset,key))
            c.execute("UPDATE notice_queue SET status='cancelled' WHERE subscription_id=? AND status='pending'",(key,))
            self.service.auth.audit(c,user['id'],'notice_subscription_save',key,ip,{'events':events,'enabled':enabled})
        return {'ok':True,'id':key}

    def poll_subscribers(self):
        subscriptions=self.service.db.query("SELECT id,bot_id,update_offset FROM notice_subscriptions WHERE enabled=1 AND mode='bot'")
        for sub in subscriptions:
            try:
                token=self.secret(sub['id']).read()
                request=urllib.request.Request('https://api.telegram.org/bot'+token+'/getUpdates',
                    data=json.dumps({'offset':sub['update_offset'],'allowed_updates':['message'],'timeout':0}).encode(),
                    headers={'Content-Type':'application/json'},method='POST')
                with urllib.request.urlopen(request,timeout=5) as response:
                    updates=json.load(response)['result']
                self.apply_updates(sub,updates)
            except Exception as exc:
                self.service.db.log_job('notifications','error',{'subscription_id':sub['id'],
                    'error_type':type(exc).__name__,'http_status':getattr(exc,'code',None)})

    def apply_updates(self, sub, updates):
        with self.service.db.tx() as c:
            current=c.execute("SELECT bot_id,update_offset FROM notice_subscriptions WHERE id=? AND enabled=1 AND mode='bot'",
                              (sub['id'],)).fetchone()
            if not current or tuple(current)!=(sub['bot_id'],sub['update_offset']):return
            offset=sub['update_offset']
            for update in sorted(updates,key=lambda item:item['update_id']):
                if update['update_id']<offset:continue
                offset=update['update_id']+1
                message=update.get('message',{})
                chat=message.get('chat',{})
                if chat.get('type')!='private' or type(chat.get('id')) is not int or chat['id']<=0:continue
                command=message.get('text','').split(maxsplit=1)
                if not command or command[0] not in ('/start','/subscribe','/stop','/unsubscribe'):continue
                active=int(command[0] in ('/start','/subscribe'))
                chat_id=str(chat['id'])
                c.execute("""INSERT INTO notice_bot_subscribers VALUES(?,?,?)
                    ON CONFLICT(subscription_id,chat_id) DO UPDATE SET active=excluded.active""",
                    (sub['id'],chat_id,active))
                if not active:
                    c.execute("""UPDATE notice_queue SET status='cancelled'
                        WHERE subscription_id=? AND subscriber_chat=? AND status='pending'""",(sub['id'],chat_id))
            c.execute('UPDATE notice_subscriptions SET update_offset=? WHERE id=?',(offset,sub['id']))

    def deliver(self):
        now=int(time.time())
        with self.service.db.tx() as c:
            row=c.execute("""SELECT q.*,COALESCE(q.subscriber_chat,s.chat_id) AS chat_id
                FROM notice_queue q JOIN notice_subscriptions s ON s.id=q.subscription_id
                WHERE q.status='pending' AND q.due<=? AND s.enabled=1
                AND ((s.mode='fixed' AND q.subscriber_chat IS NULL) OR
                     (s.mode='bot' AND EXISTS(SELECT 1 FROM notice_bot_subscribers m
                      WHERE m.subscription_id=s.id AND m.chat_id=q.subscriber_chat AND m.active=1)))
                ORDER BY q.id LIMIT 1""",(now,)).fetchone()
        if not row:return
        error=''
        try:
            token=self.secret(row['subscription_id']).read()
            request=urllib.request.Request('https://api.telegram.org/bot'+token+'/sendMessage',
                data=json.dumps({'chat_id':row['chat_id'],'text':row['message']}).encode(),
                headers={'Content-Type':'application/json'},method='POST')
            with urllib.request.urlopen(request,timeout=8) as response:
                if json.load(response).get('ok') is not True:error='Telegram 拒绝通知'
        except Exception as exc:
            error='通知发送失败 '+type(exc).__name__
        attempts=row['attempts']+1
        with self.service.db.tx() as c:
            c.execute("UPDATE notice_queue SET status=?,attempts=?,due=?,error=? WHERE id=? AND status='pending'",
                      ('sent' if not error else 'failed' if attempts>=5 else 'pending',
                       attempts,now+min(300,15*2**attempts),error,row['id']))

    def run(self):
        from .order_confirmation import expire
        hour=None
        day=None
        next_poll=0
        while not self.service.stop.wait(3):
            try:
                if time.monotonic()>=next_poll:
                    self.poll_subscribers()
                    next_poll=time.monotonic()+10
                now=datetime.now(timezone.utc)
                current=now.astimezone(ZoneInfo('Asia/Shanghai')).replace(minute=0,second=0,microsecond=0)
                with self.service.db.tx() as c:
                    expire(c)
                    if day!=current.date():
                        schedule_daily(c,now)
                    if hour!=current and (now.minute!=0 or now.second>=30):
                        if c.execute('SELECT 1 FROM notice_subscriptions WHERE enabled=1').fetchone():
                            hourly_ratios(c,current-timedelta(hours=1),current)
                            strategy_alerts(c,self.service.cfg,now)
                        hour=current
                day=current.date()
                self.deliver()
            except Exception as exc:
                self.service.db.log_job('notifications','error',{'error_type':type(exc).__name__})
