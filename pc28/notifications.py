"""Durable administrator alerts with subscriptions by event type."""
from datetime import datetime, timedelta, timezone
import json
import re
import time
import urllib.request
from zoneinfo import ZoneInfo

from .bot_secret import SecretStore
from .model import utcnow

EVENTS = ('order', 'strategy', 'ratio')
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
    public_message = {
        'order': '下单异常：有订单的执行状态异常，请在系统内查看。',
        'strategy': '策略表现异常：有策略触发异常条件，请在系统内查看。',
    }.get(kind, message)
    c.execute("""INSERT INTO notice_queue(subscription_id,message,created,subscriber_chat)
        SELECT s.id,?,?,m.chat_id FROM notice_subscriptions s
        JOIN notice_bot_subscribers m ON m.subscription_id=s.id
        WHERE s.enabled=1 AND s.mode='bot' AND m.active=1
        AND EXISTS(SELECT 1 FROM json_each(s.events) WHERE value=?)""",
        (public_message,utcnow(),kind))


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
            lines.append(f'{a} {count/n:.1%} / {b} {(n-count)/n:.1%} · 差 {gap*100/n:.2f} 个百分点')
    if lines:
        enqueue(c,'ratio',start.isoformat(),f'每小时开奖占比 · 北京时间 {start:%Y-%m-%d %H:%M}–{end:%H:%M} · {n} 期\n'+'\n'.join(lines))


def strategy_alerts(c, cfg, now):
    sources=[]
    if cfg['simulation']['enabled']:sources.append(('orders',None,'三十期买单反转策略'))
    for axis in cfg['trend_simulation'].get('enabled_axes',[]):
        sources.append(('trend_orders',axis,'大小策略' if axis=='size' else '单双策略'))
    sources.extend(('custom_orders',r['id'],r['name']) for r in c.execute('SELECT id,name FROM custom_strategies WHERE enabled=1'))
    for table,key,name in sources:
        scope='' if key is None else ' AND o.strategy=?'
        args=() if key is None else (key,)
        if table=='custom_orders':
            scope+=" AND NOT EXISTS(SELECT 1 FROM gap_order_tags g WHERE g.engine='custom' AND g.order_id=o.id)"
        rows=c.execute(f"""SELECT o.id,o.pnl,o.target_draw FROM {table} o WHERE o.status='settled'
            AND o.pnl IS NOT NULL AND julianday(o.settled)>=julianday(?){scope}
            ORDER BY o.target_draw DESC,o.id DESC LIMIT 20""",((now-timedelta(days=1)).isoformat(),*args)).fetchall()
        if not rows:continue
        streak=0
        for row in rows:
            if row['pnl']>=0:break
            streak+=1
        wins=sum(r['pnl']>0 for r in rows)
        reasons=[]
        if streak>=5:reasons.append(f'连续亏损 {streak} 笔')
        if len(rows)==20 and wins*100<35*20:reasons.append(f'近20单盈利率 {wins/20:.0%}')
        if reasons:
            net=sum(r['pnl'] for r in rows)/100
            enqueue(c,'strategy',f"{table}:{key}:{now.strftime('%Y%m%d%H')}",
                    f"策略表现异常 · {name}\n{'，'.join(reasons)} · 近 {len(rows)} 笔净收益 {net:+.2f} U\n截至第 {rows[0]['target_draw']} 期")


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
                    if hour!=current and (now.minute!=0 or now.second>=30):
                        if c.execute('SELECT 1 FROM notice_subscriptions WHERE enabled=1').fetchone():
                            hourly_ratios(c,current-timedelta(hours=1),current)
                            strategy_alerts(c,self.service.cfg,now)
                        hour=current
                self.deliver()
            except Exception as exc:
                self.service.db.log_job('notifications','error',{'error_type':type(exc).__name__})
