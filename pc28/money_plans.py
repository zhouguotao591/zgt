"""Versioned sizing policies. Progress belongs to a robot subscription, not a signal."""
import json
from .model import utcnow, rounded_ratio

SCHEMA='''
CREATE TABLE IF NOT EXISTS money_plans(id INTEGER PRIMARY KEY,name TEXT NOT NULL,enabled INTEGER NOT NULL DEFAULT 1,version INTEGER NOT NULL DEFAULT 1,settings TEXT NOT NULL,archived INTEGER NOT NULL DEFAULT 0,created TEXT NOT NULL,updated TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS money_plan_versions(plan_id INTEGER,version INTEGER,settings TEXT NOT NULL,created TEXT NOT NULL,PRIMARY KEY(plan_id,version));
CREATE TABLE IF NOT EXISTS money_plan_states(subscription_id INTEGER PRIMARY KEY,plan_id INTEGER NOT NULL,version INTEGER NOT NULL,last_order INTEGER NOT NULL DEFAULT 0,state TEXT NOT NULL);
'''

def initialize(c):
 for sql in SCHEMA.split(';'):
  if sql.strip():c.execute(sql)
 for table,cols in [('robot_subscriptions',[('money_plan_id','INTEGER')]),('robot_outbox',[('subscription_id','INTEGER'),('money_plan_snapshot','TEXT')])]:
  fields={r[1] for r in c.execute('PRAGMA table_info('+table+')')}
  for name,kind in cols:
   if name not in fields:c.execute('ALTER TABLE '+table+' ADD COLUMN '+name+' '+kind)
 c.execute('CREATE INDEX IF NOT EXISTS robot_outbox_subscription ON robot_outbox(subscription_id,id)')

DEFAULTS=dict(mode='fixed',base_cents=10000,loss_increment_cents=10000,win_decrement_cents=10000,multiplier_hundredths=200,max_increase_steps=0,max_decrease_steps=0,min_cents=100,max_cents=2000000,stake_floor='market')

def validate(value):
 if not isinstance(value,dict) or set(value)-set(DEFAULTS):raise ValueError('金额方案参数无效')
 s={**DEFAULTS,**value}
 if s['mode'] not in ('fixed','additive','multiply','adjust'):raise ValueError('金额模式无效')
 for k in ('base_cents','min_cents','max_cents','loss_increment_cents','win_decrement_cents'):
  if type(s[k])!=int or not (100 if k in ('base_cents','min_cents','max_cents') else 0)<=s[k]<=2000000 or s[k]%100:raise ValueError('金额须为整数U，单笔范围1—20000U')
 if not s['min_cents']<=s['base_cents']<=s['max_cents']:raise ValueError('须满足最低金额 ≤ 基础金额 ≤ 最高金额')
 if type(s['multiplier_hundredths'])!=int or not 100<=s['multiplier_hundredths']<=10000:raise ValueError('亏后倍数须为1—100')
 for k in ('max_increase_steps','max_decrease_steps'):
  if type(s[k])!=int or not 0<=s[k]<=100000:raise ValueError('累计次数须为0—100000，0不限')
 if s['stake_floor'] not in ('market','base'):raise ValueError('最低金额口径无效')
 return s

def listing(service):
 rows=service.db.query('SELECT * FROM money_plans WHERE archived=0 ORDER BY id DESC')
 for r in rows:
  r['settings']=json.loads(r['settings']);r['subscriptions']=service.db.query('SELECT COUNT(*) n FROM robot_subscriptions WHERE money_plan_id=? AND enabled=1',(r['id'],))[0]['n']
 return {'rows':rows,'note':'每个机器人订阅独立推进；仅确认执行并结算的订单推进金额。修改方案后下笔从基础金额开始，已有任务使用原金额。'}

def save(service,body,user,ip):
 from .robot_accounts import integer,label,conflict
 key=body.get('id');action=body.get('action','save');now=utcnow()
 with service.lock,service.db.tx() as c:
  old=c.execute('SELECT * FROM money_plans WHERE id=? AND archived=0',(key,)).fetchone() if key else None
  if key:
   integer(key,'金额方案ID')
   if not old:raise ValueError('金额方案不存在')
   if body.get('version')!=old['version']:conflict()
  if action=='archive':
   if not old:raise ValueError('金额方案不存在')
   if c.execute('SELECT 1 FROM robot_subscriptions WHERE money_plan_id=? AND enabled=1',(key,)).fetchone():raise ValueError('请先解除有效订阅再删除金额方案')
   c.execute('UPDATE money_plans SET archived=1,enabled=0,version=version+1,updated=? WHERE id=?',(now,key))
  elif action=='save':
   name=label(body.get('name'));settings=validate(body.get('settings'));enabled=body.get('enabled',True)
   if type(enabled)!=bool:raise ValueError('启用状态无效')
   encoded=json.dumps(settings,ensure_ascii=False)
   if old:
    version=old['version']+1;c.execute('UPDATE money_plans SET name=?,enabled=?,version=?,settings=?,updated=? WHERE id=?',(name,int(enabled),version,encoded,now,key))
   else:
    if c.execute('SELECT COUNT(*) FROM money_plans WHERE archived=0').fetchone()[0]>=200:raise ValueError('最多200个金额方案')
    version=1;key=c.execute('INSERT INTO money_plans(name,enabled,settings,created,updated) VALUES(?,?,?,?,?)',(name,int(enabled),encoded,now,now)).lastrowid
   c.execute('INSERT INTO money_plan_versions VALUES(?,?,?,?)',(key,version,encoded,now))
  else:raise ValueError('金额方案操作无效')
  if user:service.auth.audit(c,user['id'],'money_plan_'+action,key,ip,{'version':body.get('version')})
 return {'ok':True,'id':key}

def available(c,key):
 row=c.execute('SELECT * FROM money_plans WHERE id=? AND enabled=1 AND archived=0',(key,)).fetchone()
 if not row:raise ValueError('请选择已启用的金额方案')
 return row

def advance(state,s,order):
 """Refund/void does not advance; win/loss counters accumulate independently."""
 if order['outcome'] not in ('win','loss'):return state
 win=order['outcome']=='win';stake=order['stake'];mode=s['mode'];state=dict(state)
 if mode=='fixed':next_stake=s['base_cents']
 elif mode in ('additive','multiply'):
  next_stake=s['base_cents'] if win else stake+s['loss_increment_cents'] if mode=='additive' else rounded_ratio(stake,s['multiplier_hundredths'])
 else:next_stake=stake-s['win_decrement_cents'] if win else stake+s['loss_increment_cents']
 if mode!='fixed':
  field='decreases' if win and mode=='adjust' else 'increases' if not win else None
  if field and (s['win_decrement_cents']>0 if field=='decreases' else s['multiplier_hundredths']>100 if mode=='multiply' else s['loss_increment_cents']>0):
   state[field]=state.get(field,0)+1;cap=s['max_decrease_steps' if field=='decreases' else 'max_increase_steps']
   if cap and state[field]>=cap:next_stake=s['base_cents'];state.update(increases=0,decreases=0)
 floor=max(s['min_cents'],s['base_cents'] if s['stake_floor']=='base' else 100)
 state['next_cents']=max(floor,min(s['max_cents'],((next_stake+99)//100)*100))
 return state

def quote(c,subscription):
 plan=available(c,subscription['money_plan_id']);sid=subscription['id'];raw=c.execute('SELECT * FROM money_plan_states WHERE subscription_id=?',(sid,)).fetchone()
 # Never size past an unconfirmed submission or a pending real settlement.
 unresolved=c.execute("""SELECT 1 FROM robot_outbox b LEFT JOIN member_orders o ON o.outbox_id=b.id
 LEFT JOIN robot_execution e ON e.outbox_id=b.id WHERE b.subscription_id=? AND b.is_test=0 AND
 (o.status='pending' OR o.id IS NULL AND (e.status IN ('unconfirmed','submitted','awaiting','executed') OR b.status IN ('pending','retry','sending','sent') AND COALESCE(e.status,'') NOT IN ('rejected','failed','timeout','expired','cancelled'))) LIMIT 1""",(sid,)).fetchone()
 if unresolved:raise ValueError('上一笔执行或结算尚未确认，金额进度等待核对')
 s=json.loads(plan['settings'])
 if not raw or raw['plan_id']!=plan['id'] or raw['version']!=plan['version']:
  last=c.execute('SELECT COALESCE(MAX(o.id),0) FROM member_orders o JOIN robot_outbox b ON b.id=o.outbox_id WHERE b.subscription_id=?',(sid,)).fetchone()[0];state={'next_cents':s['base_cents'],'increases':0,'decreases':0}
 else:
  last=raw['last_order'];state=json.loads(raw['state'])
  for order in c.execute("SELECT o.* FROM member_orders o JOIN robot_outbox b ON b.id=o.outbox_id WHERE b.subscription_id=? AND o.id>? AND o.status IN ('settled','void') ORDER BY o.id",(sid,last)).fetchall():
   state=advance(state,s,order);last=order['id']
 c.execute('INSERT INTO money_plan_states VALUES(?,?,?,?,?) ON CONFLICT(subscription_id) DO UPDATE SET plan_id=excluded.plan_id,version=excluded.version,last_order=excluded.last_order,state=excluded.state',(sid,plan['id'],plan['version'],last,json.dumps(state)))
 return state['next_cents'],json.dumps({'id':plan['id'],'name':plan['name'],'version':plan['version'],'settings':s,'state':state},ensure_ascii=False)
