"""Order-created robot messages: bounded templates, durable outbox and stable event IDs."""
import copy,json,math,re,secrets,string,time,threading,urllib.request,urllib.error
from datetime import datetime,timezone
from zoneinfo import ZoneInfo
from .model import utcnow
from .bot_secret import SecretStore
from .robot_platforms import Platforms
from .robot_accounts import Accounts
from . import robot_risk
from . import order_confirmation
from .timing import regular_schedule
ENDPOINT='http://127.0.0.1:17890/api/v1/signals'
TABLES={'legacy':'orders','trend':'trend_orders','custom':'custom_orders'}
CONFIG={'enabled':False,'timeout_seconds':5,'max_attempts':3,'max_age_seconds':180,'send_interval_seconds':1,'account_id':0,'bot_key':0,'merge_wait_seconds':3,'delay_min_seconds':30,'delay_max_seconds':60}
DRAW_SEND_BUFFER_SECONDS=10

class SignalExpired(Exception):
 pass

def draw_deadline(c,target_draw,event_time,max_age,previous=None,latest=None):
 row=c.execute("SELECT value FROM runtime_state WHERE key='draw_timer'").fetchone()
 if row is None:return None
 try:
  plan=json.loads(row[0])
  draw_at=datetime.fromisoformat(plan['next_draw_at'])
  if latest is None:latest=c.execute('SELECT id,draw_utc FROM draws ORDER BY id DESC LIMIT 1').fetchone()
  if not latest or plan['next_draw_id']!=target_draw or target_draw!=latest['id']+1 or plan.get('inferred'):return None
  last_at=datetime.fromisoformat(latest['draw_utc'])
  if not draw_at.tzinfo or not last_at.tzinfo or draw_at<=last_at:return None
  if plan.get('resting') and not regular_schedule(plan,latest):return None
  draw_at=int(draw_at.timestamp())
 except (KeyError,ValueError,TypeError,OverflowError):
  return None
 expires_at=min(draw_at-DRAW_SEND_BUFFER_SECONDS,event_time+max_age)
 if previous is not None:expires_at=min(expires_at,previous)
 return {'target_draw':target_draw,'draw_at':draw_at,'expires_at':expires_at}
FIELDS={'strategy_name','strategy_id','engine','order_id','target_draw','cutoff_draw','direction','stake','stake_cents','created_at','beijing_time','cycle','step','symbol'}
DEFAULT_MESSAGE='{direction}{stake}'
DEFAULT_PAYLOAD={'symbol':'PC28','text':'{message}'}

def member_stake(setting,default_cents,simulation_cents):
 if setting is None:return default_cents
 if type(setting)==int:return setting # Existing fixed-amount subscriptions.
 if setting['mode']=='fixed':return setting['cents']
 # Ratio uses integer ten-thousandths; ceil to whole U without float rounding.
 return ((simulation_cents*setting['ratio_units']+999999)//1000000)*100
SCHEMA="""
CREATE TABLE IF NOT EXISTS robot_config(id INTEGER PRIMARY KEY CHECK(id=1),settings TEXT NOT NULL,installation TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS robot_cursors(engine TEXT PRIMARY KEY,last_id INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS robot_rules(id INTEGER PRIMARY KEY,name TEXT NOT NULL,enabled INTEGER NOT NULL,all_strategies INTEGER NOT NULL,strategy_ids TEXT NOT NULL,signal_type TEXT NOT NULL,message_template TEXT NOT NULL,payload_template TEXT NOT NULL,starts TEXT NOT NULL,created TEXT NOT NULL,updated TEXT NOT NULL,deleted INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS robot_strategy_push(strategy_id TEXT PRIMARY KEY,enabled INTEGER NOT NULL DEFAULT 0,starts TEXT NOT NULL,updated TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS robot_outbox(id INTEGER PRIMARY KEY,event_id TEXT NOT NULL UNIQUE,rule_id INTEGER,name TEXT NOT NULL,engine TEXT,order_id INTEGER,strategy_id TEXT,strategy_name TEXT,target_draw INTEGER,event_time INTEGER NOT NULL,created TEXT NOT NULL,request TEXT NOT NULL,message TEXT NOT NULL,status TEXT NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,next_attempt INTEGER NOT NULL DEFAULT 0,last_status INTEGER,last_error TEXT,finished TEXT,is_test INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS robot_outbox_due ON robot_outbox(status,next_attempt,id);
CREATE TABLE IF NOT EXISTS robot_batches(id TEXT PRIMARY KEY,request TEXT NOT NULL,created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS robot_batch_items(outbox_id INTEGER PRIMARY KEY,batch_id TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS robot_bindings(id INTEGER PRIMARY KEY,user_id INTEGER NOT NULL,account_id INTEGER NOT NULL,bot_key INTEGER NOT NULL,signal_type TEXT NOT NULL,enabled INTEGER NOT NULL DEFAULT 0,strategy_ids TEXT NOT NULL,starts TEXT NOT NULL,created TEXT NOT NULL,updated TEXT NOT NULL,deleted INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS robot_delivery_slots(recipient_id INTEGER NOT NULL,target_draw INTEGER NOT NULL,batch_id TEXT NOT NULL,PRIMARY KEY(recipient_id,target_draw));
CREATE TABLE IF NOT EXISTS robot_hidden_records(outbox_id INTEGER PRIMARY KEY,deleted_at TEXT NOT NULL);
"""

class NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,*args,**kwargs):return None

def receiver_error(response):
 # Show actionable validation errors, never arbitrary response text or credentials.
 base='接收端返回 HTTP '+str(response.code)
 if response.code==401:return base+'：平台密钥无效或信号源未启用，请核对该平台密钥'
 try:value=json.loads(response.read(4096).decode('utf-8')).get('error','')
 except Exception:return base
 known={'多账号信号必须指定 account_id','信号账号不存在','账号 ID 无效','未找到可用规则','信号匹配多个规则，请传入 bot_key 指定目标机器人 ID','信号时间戳超出5分钟或格式无效','event_id 必填且格式无效','signal_type 格式无效','当前执行已暂停','timestamp 必填（Unix秒）'}
 if value in known or re.fullmatch(r'缺少模板参数: [A-Za-z][A-Za-z0-9_]{0,80}',str(value)):return base+'：'+value
 return base

def text_template(template,fields):
 if not isinstance(template,str) or len(template)>8000:raise ValueError('模板必须为8000字以内的文本')
 try:
  for _,field,spec,conversion in string.Formatter().parse(template):
   if field is not None and (field not in fields or spec or conversion):raise ValueError('模板包含不支持的变量或格式：'+str(field))
 except (ValueError,KeyError) as e:raise ValueError('模板格式错误：'+str(e))
 return template

def render_text(template,context):return template.format_map({k:str(v) for k,v in context.items()})
def render_payload(value,context,depth=0):
 if depth>6:raise ValueError('payload模板层级过深')
 if isinstance(value,str):text_template(value,FIELDS|{'message'});return render_text(value,context)
 if isinstance(value,list):return [render_payload(x,context,depth+1) for x in value]
 if isinstance(value,dict):return {k:render_payload(v,context,depth+1) for k,v in value.items()}
 if value is None or type(value) in (int,bool):return value
 if type(value)==float and math.isfinite(value):return value
 raise ValueError('payload模板只支持有效JSON值')

def sample_context():return {'strategy_name':'示例策略（模拟）','strategy_id':'example','engine':'custom','order_id':1,'target_draw':3500001,'cutoff_draw':3500000,'direction':'大','stake':'10','stake_cents':1000,'created_at':utcnow(),'beijing_time':datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d %H:%M:%S'),'cycle':1,'step':1,'symbol':'PC28'}

def validate_rule(body):
 required={'name','enabled','all_strategies','strategy_ids','signal_type','message_template','payload_template'}
 if not isinstance(body,dict) or set(body)-required-{'id'} or required-set(body):raise ValueError('规则字段不完整或包含未知字段')
 rule={k:copy.deepcopy(body[k]) for k in required}
 if not isinstance(rule['name'],str) or not 1<=len(rule['name'].strip())<=60:raise ValueError('规则名称需要1—60个字符')
 if type(rule['enabled'])!=bool or type(rule['all_strategies'])!=bool:raise ValueError('开关参数无效')
 ids=rule['strategy_ids']
 if not isinstance(ids,list) or len(ids)>110 or any(not isinstance(x,str) or len(x)>80 for x in ids):raise ValueError('策略筛选无效')
 rule['strategy_ids']=sorted(set(ids))
 if not rule['all_strategies'] and not ids:raise ValueError('请至少选择一个策略')
 if not isinstance(rule['signal_type'],str) or not re.fullmatch('[A-Z][A-Z0-9_]{0,63}',rule['signal_type']):raise ValueError('信号类型须为大写字母、数字或下划线')
 if not isinstance(rule['message_template'],str) or not rule['message_template'].strip() or len(rule['message_template'])>4000:raise ValueError('消息模板需要1—4000个字符')
 text_template(rule['message_template'],FIELDS)
 if not isinstance(rule['payload_template'],dict):raise ValueError('payload模板必须是JSON对象')
 context=sample_context();context['message']=render_text(rule['message_template'],context);payload=render_payload(rule['payload_template'],context)
 if len(json.dumps(payload,ensure_ascii=False,allow_nan=False).encode())>20000:raise ValueError('payload模板超过20KB')
 rule['name']=rule['name'].strip();return rule

class RobotSignals(Accounts,Platforms):
 def __init__(self,service,secret=None):
  self.service=service;self.db=service.db;self.secret=secret or SecretStore(service.root);self.thread=None;self.installation=''
  with self.service.lock,self.db.tx() as c:
   for sql in SCHEMA.split(';'):
    if sql.strip():c.execute(sql)
   columns={r[1] for r in c.execute('PRAGMA table_info(robot_outbox)')}
   if 'recipient_id' not in columns:c.execute('ALTER TABLE robot_outbox ADD COLUMN recipient_id INTEGER NOT NULL DEFAULT 0')
   if 'recipient_name' not in columns:c.execute("ALTER TABLE robot_outbox ADD COLUMN recipient_name TEXT NOT NULL DEFAULT '站点默认'")
   if 'push_cents' not in columns:c.execute('ALTER TABLE robot_outbox ADD COLUMN push_cents INTEGER')
   for name,definition in [('scheduled_at','TEXT'),('sent_at','TEXT'),('accepted_at','TEXT'),('member_odds','TEXT'),('member_user_id','INTEGER')]:
    if name not in columns:c.execute('ALTER TABLE robot_outbox ADD COLUMN '+name+' '+definition)
   c.execute("UPDATE robot_outbox SET accepted_at=finished WHERE status='sent' AND accepted_at IS NULL AND finished IS NOT NULL")
   c.execute('DROP INDEX IF EXISTS robot_outbox_order')
   c.execute('CREATE UNIQUE INDEX IF NOT EXISTS robot_outbox_recipient_order ON robot_outbox(engine,order_id,recipient_id) WHERE is_test=0')
   binding_columns={r[1] for r in c.execute('PRAGMA table_info(robot_bindings)')}
   if 'default_cents' not in binding_columns:c.execute('ALTER TABLE robot_bindings ADD COLUMN default_cents INTEGER NOT NULL DEFAULT 0')
   if 'strategy_amounts' not in binding_columns:c.execute("ALTER TABLE robot_bindings ADD COLUMN strategy_amounts TEXT NOT NULL DEFAULT '{}'")
   c.execute('INSERT OR IGNORE INTO robot_config VALUES(1,?,?)',(json.dumps(CONFIG),secrets.token_hex(12)))
   for engine,table in TABLES.items():
    maximum=c.execute('SELECT COALESCE(MAX(id),0) FROM '+table).fetchone()[0];c.execute('INSERT OR IGNORE INTO robot_cursors VALUES(?,?)',(engine,maximum))
   self.installation=c.execute('SELECT installation FROM robot_config WHERE id=1').fetchone()[0]
  self.init_platforms()
  self.init_accounts()
 def config(self):return {**CONFIG,**json.loads(self.db.query('SELECT settings FROM robot_config WHERE id=1')[0]['settings'])}
 def maximums(self,c):return {engine:c.execute('SELECT COALESCE(MAX(id),0) FROM '+table).fetchone()[0] for engine,table in TABLES.items()}
 def strategies(self):
  from .strategies import catalog
  return [{k:r[k] for k in ('id','name','enabled')} for r in catalog(self.db,self.service.cfg)['strategies']]
 def push_strategies(self):
  flags={r['strategy_id']:bool(r['enabled']) for r in self.db.query('SELECT strategy_id,enabled FROM robot_strategy_push')}
  return [{**r,'push_enabled':flags.get(r['id'],False)} for r in self.strategies()]
 def members(self,scope=None):return self.db.query("SELECT id,username,active FROM web_users WHERE role='member' AND (? IS NULL OR id IN (SELECT member_id FROM web_member_access WHERE admin_id=?)) ORDER BY id",(scope,scope))
 def bindings(self,scope=None):
  rows=self.db.query('SELECT b.id,b.platform_id,p.name platform_name,b.user_id,u.username,u.active member_active,b.account_id,b.bot_key,b.signal_type,b.enabled,b.strategy_ids,b.default_cents,b.strategy_amounts FROM robot_bindings b JOIN web_users u ON u.id=b.user_id JOIN robot_platforms p ON p.id=b.platform_id WHERE b.deleted=0 AND (? IS NULL OR u.id IN (SELECT member_id FROM web_member_access WHERE admin_id=?)) ORDER BY b.id DESC',(scope,scope))
  for r in rows:r['strategy_ids']=json.loads(r['strategy_ids']);r['strategy_amounts']=json.loads(r['strategy_amounts']);r['enabled']=bool(r['enabled'])
  return rows
 def save_binding(self,body,user=None,ip=''):
  if set(body)-{'id','platform_id','user_id','account_id','bot_key','signal_type','enabled','strategy_ids','default_cents','strategy_amounts'}:raise ValueError('会员推送配置字段无效')
  for key in ('user_id','account_id','bot_key'):
   if type(body.get(key))!=int or not 1<=body[key]<=2147483647:raise ValueError('会员、接收账号和机器人 ID 必须为正整数')
  if type(body.get('enabled'))!=bool:raise ValueError('订阅开关无效')
  if not isinstance(body.get('signal_type'),str) or not re.fullmatch('[A-Z][A-Z0-9_]{1,63}',body['signal_type']):raise ValueError('信号类型无效')
  ids=body.get('strategy_ids');known={r['id'] for r in self.strategies()}
  if not isinstance(ids,list) or not ids or any(not isinstance(x,str) or x not in known for x in ids):raise ValueError('请至少选择一个有效策略')
  amounts=body.get('strategy_amounts',{});base=body.get('default_cents')
  if type(base)!=int or not 100<=base<=2000000:raise ValueError('会员默认每单金额须为1—20000U，不能使用模拟账户金额')
  if not isinstance(amounts,dict):raise ValueError('策略金额配置无效')
  for k,v in amounts.items():
   if k not in ids:raise ValueError('金额配置必须属于订阅策略')
   if type(v)==int and 100<=v<=2000000:continue
   if not isinstance(v,dict):raise ValueError('策略金额配置无效')
   if v.get('mode')=='fixed' and set(v)=={'mode','cents'} and type(v['cents'])==int and 100<=v['cents']<=2000000:continue
   if v.get('mode')=='ratio' and set(v)=={'mode','ratio_units'} and type(v['ratio_units'])==int and 1<=v['ratio_units']<=1000000:continue
   raise ValueError('固定金额须为1—20000U；比例须为0.0001—100倍，最多4位小数')
  key=body.get('id');now=utcnow();platform=body.get('platform_id',1)
  if type(platform)!=int or not self.db.query('SELECT id FROM robot_platforms WHERE id=?',(platform,)):raise ValueError('请选择有效的推送平台')
  with self.service.lock,self.db.tx() as c:
   if not c.execute("SELECT 1 FROM web_users WHERE id=? AND role='member'",(body['user_id'],)).fetchone():raise ValueError('会员不存在')
   if key and not c.execute('SELECT 1 FROM robot_bindings WHERE id=? AND deleted=0',(key,)).fetchone():raise ValueError('会员推送绑定不存在')
   if c.execute('SELECT 1 FROM robot_bindings WHERE deleted=0 AND platform_id=? AND (user_id=? OR (account_id=? AND bot_key=?)) AND id!=?',(platform,body['user_id'],body['account_id'],body['bot_key'],key or 0)).fetchone():raise ValueError('该会员或账号与机器人组合已绑定，不能重复配置')
   values=(body['user_id'],body['account_id'],body['bot_key'],body['signal_type'],int(body['enabled']),json.dumps(sorted(set(ids))),json.dumps(self.maximums(c)),now)
   if key:c.execute('UPDATE robot_bindings SET platform_id=?,user_id=?,account_id=?,bot_key=?,signal_type=?,enabled=?,strategy_ids=?,starts=?,updated=?,default_cents=?,strategy_amounts=? WHERE id=?', (platform,*values,base,json.dumps(amounts),key))
   else:key=c.execute('INSERT INTO robot_bindings(platform_id,user_id,account_id,bot_key,signal_type,enabled,strategy_ids,starts,updated,default_cents,strategy_amounts,created) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(platform,*values,base,json.dumps(amounts),now)).lastrowid
   c.execute('UPDATE robot_bindings SET platform_id=? WHERE id=?',(platform,key))
   c.execute("UPDATE robot_outbox SET status='cancelled',last_error='会员推送配置已更新',finished=? WHERE is_test=0 AND recipient_id IN (0,?) AND status IN ('pending','retry')",(now,key))
   self._import_binding(c,dict(c.execute('SELECT * FROM robot_bindings WHERE id=?',(key,)).fetchone()))
   if user:self.service.auth.audit(c,user['id'],'robot_binding_save',key,ip,{'user_id':body['user_id'],'enabled':body['enabled']})
  return {'ok':True,'id':key}
 def binding_control(self,body,user=None,ip=''):
  row=next((r for r in self.bindings() if r['id']==body.get('id')),None)
  if not row:raise ValueError('会员绑定不存在')
  return self.save_binding({k:body['enabled'] if k=='enabled' else row[k] for k in ('id','platform_id','user_id','account_id','bot_key','signal_type','enabled','strategy_ids','default_cents','strategy_amounts')},user,ip)
 def recipients(self,c,context,order):
  if not c.execute('SELECT 1 FROM robot_bindings WHERE deleted=0 LIMIT 1').fetchone():return []
  rows=[dict(r) for r in c.execute('SELECT b.*,u.username FROM robot_bindings b JOIN web_users u ON u.id=b.user_id WHERE b.enabled=1 AND b.deleted=0 AND b.default_cents>=100 AND u.active=1 AND u.role=\'member\'')]
  return [r for r in rows if c.execute('SELECT 1 FROM robot_platforms WHERE id=? AND enabled=1',(r['platform_id'],)).fetchone() and context['strategy_id'] in json.loads(r['strategy_ids']) and order['id']>json.loads(r['starts']).get(context['engine'],0)]
 def recipient_active(self,c,row):
  if not row['recipient_id']:return False
  binding=c.execute("SELECT b.* FROM robot_bindings b JOIN web_users u ON u.id=b.user_id WHERE b.id=? AND b.deleted=0 AND b.enabled=1 AND u.active=1 AND u.role='member'",(row['recipient_id'],)).fetchone()
  if binding:
   mapped=c.execute('SELECT a.enabled ae,a.deleted ad,b.enabled be,b.deleted bd FROM robot_binding_map m JOIN robot_accounts a ON a.id=m.account_id JOIN robot_bots b ON b.id=m.bot_id WHERE m.binding_id=?',(row['recipient_id'],)).fetchone()
   if mapped and (not mapped['ae'] or mapped['ad'] or not mapped['be'] or mapped['bd']):return False
  return bool(binding and binding['platform_id']==row['platform_id'] and c.execute('SELECT 1 FROM robot_platforms WHERE id=? AND enabled=1',(row['platform_id'],)).fetchone() and row['rule_id']==0 and row['push_cents'] is not None and 100<=row['push_cents']<=2000000 and row['strategy_id'] in json.loads(binding['strategy_ids']) and row['order_id']>json.loads(binding['starts']).get(row['engine'],0))
 def active_strategy(self,c,key):
  if key=='legacy':return bool(self.service.cfg['simulation']['enabled'])
  if key in ('size','parity'):return key in self.service.cfg['trend_simulation']['enabled_axes']
  row=c.execute('SELECT enabled FROM custom_strategies WHERE id=? AND id NOT IN (SELECT id FROM deleted_strategies)',(key,)).fetchone();return bool(row and row[0])
 def push_control(self,body,user=None,ip=''):
  key=body.get('id');enabled=body.get('enabled')
  if type(enabled)!=bool or key not in {r['id'] for r in self.strategies()}:raise ValueError('策略或推送开关无效')
  with self.service.lock,self.db.tx() as c:
   c.execute('INSERT INTO robot_strategy_push VALUES(?,?,?,?) ON CONFLICT(strategy_id) DO UPDATE SET enabled=excluded.enabled,starts=excluded.starts,updated=excluded.updated',(key,int(enabled),json.dumps(self.maximums(c)),utcnow()))
   if user:self.service.auth.audit(c,user['id'],'robot_strategy_push',key,ip,{'enabled':enabled})
  return {'ok':True}
 def defaults(self):return {'name':'模拟订单通知','enabled':True,'all_strategies':True,'strategy_ids':[],'signal_type':'BUY_SIGNAL','message_template':DEFAULT_MESSAGE,'payload_template':DEFAULT_PAYLOAD}
 def rules(self):
  result=self.db.query('SELECT * FROM robot_rules WHERE deleted=0 ORDER BY id DESC')
  for r in result:
   for k in ('strategy_ids','payload_template'):r[k]=json.loads(r[k])
   for k in ('enabled','all_strategies'):r[k]=bool(r[k])
   r.pop('starts',None)
  return result
 def save_config(self,body,user=None,ip=''):
  if not isinstance(body,dict) or set(body)-set(CONFIG)-{'token'} or set(CONFIG)-{'account_id','bot_key','merge_wait_seconds','delay_min_seconds','delay_max_seconds'}-set(body):raise ValueError('机器人设置字段无效')
  cfg={k:body.get(k,self.config()[k]) for k in CONFIG}
  if type(cfg['enabled'])!=bool:raise ValueError('启停参数无效')
  for key,low,high in [('timeout_seconds',1,15),('max_attempts',1,10),('max_age_seconds',30,600),('send_interval_seconds',1,60),('account_id',0,2147483647),('bot_key',0,2147483647),('merge_wait_seconds',0,30),('delay_min_seconds',1,59),('delay_max_seconds',2,60)]:
   if type(cfg[key])!=int or not low<=cfg[key]<=high:raise ValueError('机器人超时、重试、过期时间或发送间隔无效')
  if cfg['delay_min_seconds']>=cfg['delay_max_seconds']:raise ValueError('随机延迟须满足1≤最小秒数＜最大秒数≤60')
  if cfg['max_age_seconds']<=cfg['delay_max_seconds']+cfg['timeout_seconds']:raise ValueError('信号有效期必须大于延迟与请求超时之和')
  new_token=body.get('token')
  if new_token:self.secret.write(new_token)
  if cfg['enabled'] and not any(p['enabled'] and p['token_configured'] for p in self.platforms()):raise ValueError('请先配置并启用至少一个推送平台的密钥')
  with self.service.lock,self.db.tx() as c:
   old=json.loads(c.execute('SELECT settings FROM robot_config WHERE id=1').fetchone()[0]);c.execute('UPDATE robot_config SET settings=? WHERE id=1',(json.dumps(cfg),))
   if not cfg['enabled']:c.execute("UPDATE robot_outbox SET status='cancelled',last_error='机器人已停用',finished=? WHERE is_test=0 AND status IN ('pending','retry')",(utcnow(),))
   if cfg['enabled'] and not old['enabled']:
    for engine,maximum in self.maximums(c).items():c.execute('UPDATE robot_cursors SET last_id=? WHERE engine=?',(maximum,engine))
   if user:self.service.auth.audit(c,user['id'],'robot_config','robot',ip,{**cfg,'token_updated':bool(new_token)})
  return {'ok':True,'token_configured':self.secret.exists()}
 def save_rule(self,body,user=None,ip=''):
  rule=validate_rule(body);known={r['id'] for r in self.strategies()}
  if not rule['all_strategies'] and not set(rule['strategy_ids'])<=known:raise ValueError('所选策略不存在或已删除')
  now=utcnow();key=body.get('id')
  with self.service.lock,self.db.tx() as c:
   starts=json.dumps(self.maximums(c));values=(rule['name'],int(rule['enabled']),int(rule['all_strategies']),json.dumps(rule['strategy_ids']),rule['signal_type'],rule['message_template'],json.dumps(rule['payload_template'],ensure_ascii=False),starts,now)
   if key:
    if not c.execute('SELECT 1 FROM robot_rules WHERE id=? AND deleted=0',(key,)).fetchone():raise ValueError('规则不存在')
    c.execute('UPDATE robot_rules SET name=?,enabled=?,all_strategies=?,strategy_ids=?,signal_type=?,message_template=?,payload_template=?,starts=?,updated=? WHERE id=?',(*values,key));c.execute("UPDATE robot_outbox SET status='cancelled',last_error='规则已修改',finished=? WHERE rule_id=? AND is_test=0 AND status IN ('pending','retry')",(now,key))
   else:
    if c.execute('SELECT COUNT(*) FROM robot_rules WHERE deleted=0').fetchone()[0]>=50:raise ValueError('最多添加50条发送规则')
    key=c.execute('INSERT INTO robot_rules(name,enabled,all_strategies,strategy_ids,signal_type,message_template,payload_template,starts,updated,created) VALUES(?,?,?,?,?,?,?,?,?,?)',(*values,now)).lastrowid
   if user:self.service.auth.audit(c,user['id'],'robot_rule_save',key,ip,{'name':rule['name'],'enabled':rule['enabled']})
  return {'ok':True,'id':key}
 def control_rule(self,key,enabled=None,delete=False,user=None,ip=''):
  with self.service.lock,self.db.tx() as c:
   row=c.execute('SELECT * FROM robot_rules WHERE id=? AND deleted=0',(key,)).fetchone()
   if not row:raise ValueError('规则不存在')
   if not delete and type(enabled)!=bool:raise ValueError('开关无效')
   c.execute('UPDATE robot_rules SET enabled=?,deleted=?,starts=?,updated=? WHERE id=?',(0 if delete else int(enabled),int(delete),json.dumps(self.maximums(c)),utcnow(),key))
   c.execute("UPDATE robot_outbox SET status='cancelled',last_error='规则已停用或重新启用',finished=? WHERE rule_id=? AND is_test=0 AND status IN ('pending','retry')",(utcnow(),key))
   if user:self.service.auth.audit(c,user['id'],'robot_rule_delete' if delete else 'robot_rule_control',key,ip)
  return {'ok':True}
 def build_request(self,rule,context,event_id,signal_type=None):
  message=render_text(DEFAULT_MESSAGE,context);payload={'symbol':'PC28','text':message}
  cfg=self.config();wire={'event_id':event_id,'signal_type':signal_type or rule['signal_type'],'timestamp':int(time.time()),'payload':payload}
  if cfg['account_id']:wire['account_id']=cfg['account_id']
  if cfg['bot_key']:wire['bot_key']=cfg['bot_key']
  return wire,message
 def preview(self,body):
  rule=validate_rule(body);wire,message=self.build_request(rule,sample_context(),'preview-not-sent');return {'request':wire,'message':message,'endpoint':ENDPOINT}
 def context(self,c,engine,o):
  key='legacy' if engine=='legacy' else o['strategy'];name={'legacy':'原有反转策略','size':'大小策略','parity':'单双策略'}.get(key,key)
  if engine=='custom':
   row=c.execute('SELECT name FROM custom_strategies WHERE id=?',(key,)).fetchone();name=row[0] if row else key
  direction=json.loads(o['strategy_json'])['direction'] if engine=='legacy' else o['direction'];created=datetime.fromisoformat(o['created'])
  return {'strategy_name':name,'strategy_id':key,'engine':engine,'order_id':o['id'],'target_draw':o['target_draw'],'cutoff_draw':o['cutoff_draw'],'direction':direction,'stake':f"{o['stake']/100:.2f}".rstrip('0').rstrip('.'),'stake_cents':o['stake'],'created_at':o['created'],'beijing_time':created.astimezone(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d %H:%M:%S'),'cycle':o['round_id'] if engine=='legacy' else o['cycle'],'step':o['round_attempt'] if engine=='legacy' else o['step'],'symbol':'PC28'}
 def capture(self):
  now=int(time.time());added=0
  with self.service.lock,self.db.tx() as c:
   cfg=json.loads(c.execute('SELECT settings FROM robot_config WHERE id=1').fetchone()[0]);rules=[{**self.defaults(),'id':0,'starts':'{}','strategy_ids':'[]','payload_template':json.dumps(DEFAULT_PAYLOAD)}]
   for engine,table in TABLES.items():
    cursor=c.execute('SELECT last_id FROM robot_cursors WHERE engine=?',(engine,)).fetchone()[0]
    if not cfg['enabled'] or not rules:
     c.execute('UPDATE robot_cursors SET last_id=? WHERE engine=?',(c.execute('SELECT COALESCE(MAX(id),0) FROM '+table).fetchone()[0],engine));continue
    orders=[dict(r) for r in c.execute('SELECT * FROM '+table+' WHERE id>? ORDER BY id LIMIT 500',(cursor,))]
    for o in orders:
     c.execute('UPDATE robot_cursors SET last_id=? WHERE engine=?',(o['id'],engine))
     if engine=='custom' and c.execute("SELECT 1 FROM gap_order_tags WHERE engine='custom' AND order_id=?",(o['id'],)).fetchone():continue
     try:when=int(datetime.fromisoformat(o['created']).timestamp())
     except (TypeError,ValueError):continue
     if now-when>cfg['max_age_seconds'] or when>now+30:continue
     context=self.context(c,engine,o)
     self.capture_gates(c,context,o)
     if not self.active_strategy(c,context['strategy_id']):continue
     for rule in rules:
      if o['id']<=json.loads(rule['starts']).get(engine,0):continue
      if not rule['all_strategies'] and context['strategy_id'] not in json.loads(rule['strategy_ids']):continue
      active={**rule,'payload_template':json.loads(rule['payload_template'])}
      for recipient in self.recipients(c,context,o):
       own_context=context
       mapping=None
       if recipient['id']:
        mapping=c.execute('SELECT * FROM robot_binding_map WHERE binding_id=?',(recipient['id'],)).fetchone()
       cents=member_stake(json.loads(recipient['strategy_amounts']).get(context['strategy_id']),recipient['default_cents'],o['stake']);own_context={**context,'stake_cents':cents,'stake':f'{cents/100:.2f}'.rstrip('0').rstrip('.')}
       event=f"pc28-{self.installation}-{rule['id']}-{recipient['id']}-{engine}-{o['id']}-{o['target_draw']}";wire,message=self.build_request(active,own_context,event)
       if recipient['id']:wire.update(account_id=recipient['account_id'],bot_key=recipient['bot_key'],signal_type=recipient['signal_type'])
       valid=100<=own_context['stake_cents']<=2000000
       cur=c.execute("INSERT OR IGNORE INTO robot_outbox(event_id,rule_id,name,engine,order_id,strategy_id,strategy_name,target_draw,event_time,created,request,message,status,recipient_id,recipient_name,push_cents,last_error,platform_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(event,rule['id'],rule['name'],engine,o['id'],context['strategy_id'],context['strategy_name'],o['target_draw'],when,utcnow(),json.dumps(wire,ensure_ascii=False),message,'pending' if valid else 'failed',recipient['id'],recipient['username'],own_context['stake_cents'],'' if valid else '按比例计算的下单金额超出1—20000U，未发送',recipient['platform_id']));added+=cur.rowcount
       if cur.rowcount:
        oid=cur.lastrowid;c.execute('UPDATE robot_outbox SET member_odds=?,member_user_id=?,simulation_cents=? WHERE id=?',(json.dumps(self.service.cfg['odds']),recipient['user_id'],o['stake'],oid))
        if mapping:
         c.execute('UPDATE robot_outbox SET follow_account_id=?,bot_id=?,config_version=(SELECT version FROM robot_accounts WHERE id=?) WHERE id=?',(mapping['account_id'],mapping['bot_id'],mapping['account_id'],oid))
        sealed=c.execute('SELECT 1 FROM robot_delivery_slots WHERE recipient_id=? AND target_draw=?',(recipient['id'],o['target_draw'])).fetchone()
        if sealed:c.execute("UPDATE robot_outbox SET status='cancelled',last_error='合并窗口已关闭，晚到订单不重复发送',finished=? WHERE id=?",(utcnow(),oid))
        elif not valid or c.execute("SELECT 1 FROM robot_outbox WHERE recipient_id=? AND target_draw=? AND status='failed' AND push_cents NOT BETWEEN 100 AND 2000000",(recipient['id'],o['target_draw'])).fetchone():c.execute("UPDATE robot_outbox SET status='failed',last_error='策略金额超出1—20000U，接收目标本期整批未发送，请核对策略金额',finished=? WHERE recipient_id=? AND target_draw=? AND status IN ('pending','retry','failed')",(utcnow(),recipient['id'],o['target_draw']))
      break
  return added
 def queue_test(self,body,user=None,ip=''):
  rule=validate_rule(body['rule']);request_id=body.get('request_id','')
  if not isinstance(request_id,str) or not re.fullmatch('[A-Za-z0-9-]{8,80}',request_id):raise ValueError('测试请求编号无效')
  if not self.secret.exists():raise ValueError('请先配置机器人密钥')
  wire,message=self.build_request(rule,sample_context(),'pc28-test-'+request_id,'TEST_SIGNAL');wire['payload']['test']=True
  with self.db.tx() as c:
   c.execute("INSERT OR IGNORE INTO robot_outbox(event_id,name,engine,event_time,created,request,message,status,is_test) VALUES(?,?,'test',?,?,?,?, 'pending',1)",(wire['event_id'],'人工测试 · '+rule['name'],int(time.time()),utcnow(),json.dumps(wire,ensure_ascii=False),message))
   if user:self.service.auth.audit(c,user['id'],'robot_test','robot',ip)
  return {'ok':True,'event_id':wire['event_id'],'note':'测试已排队，信号类型为TEST_SIGNAL；请在发送记录查看接口接收结果'}
 def deliver_one(self,platform_id=None):
  now=int(time.time());row=None;cfg=self.config()
  with self.db.tx() as c:
   c.execute("UPDATE robot_outbox SET status='expired',last_error='已超过信号有效期，未发送',finished=? WHERE status IN ('pending','retry') AND event_time<?",(utcnow(),now-cfg['max_age_seconds']))
   c.execute("UPDATE robot_outbox SET status='retry',next_attempt=? WHERE status='sending' AND COALESCE(lease_until,0)<=?",(now,now))
   candidate=c.execute("SELECT o.* FROM robot_outbox o WHERE status IN ('pending','retry') AND next_attempt<=? AND (is_test=1 OR ?=1) AND (? IS NULL OR platform_id=?) AND NOT EXISTS(SELECT 1 FROM robot_outbox busy WHERE busy.status='sending' AND busy.platform_id=o.platform_id AND busy.recipient_id=o.recipient_id) ORDER BY id LIMIT 1",(now,int(cfg['enabled']),platform_id,platform_id)).fetchone()
   if not candidate:return False
   row=dict(candidate)
   # Freeze the destination while selecting the batch; a concurrent platform
   # edit must not redirect an in-flight order to a different tool.
   platform=next(p for p in self.platforms() if p['id']==row['platform_id'])
   if not row['is_test'] and (not self.recipient_active(c,row) or not self.active_strategy(c,row['strategy_id'])):c.execute("UPDATE robot_outbox SET status='cancelled',last_error='会员订阅、策略已停用或推送配置已变更',finished=? WHERE id=?",(utcnow(),row['id']));return True
   existing=c.execute('SELECT batch_id FROM robot_batch_items WHERE outbox_id=?',(row['id'],)).fetchone()
   if existing:
    batch_id=existing[0];members=[dict(r) for r in c.execute('SELECT o.* FROM robot_outbox o JOIN robot_batch_items i ON i.outbox_id=o.id WHERE i.batch_id=? ORDER BY o.id',(batch_id,))]
    if any(r['status'] not in ('pending','retry') or (not r['is_test'] and (not self.recipient_active(c,r) or not self.active_strategy(c,r['strategy_id']))) for r in members):
     c.execute("UPDATE robot_outbox SET status='cancelled',last_error='合并消息中的策略或推送已停用',finished=? WHERE id IN (SELECT outbox_id FROM robot_batch_items WHERE batch_id=?) AND status IN ('pending','retry')",(utcnow(),batch_id));return True
    wire=json.loads(c.execute('SELECT request FROM robot_batches WHERE id=?',(batch_id,)).fetchone()[0])
   else:
    # Allow one scheduler turn to finish creating all strategy orders for this draw.
    if not row['is_test']:
     slot=c.execute('SELECT batch_id FROM robot_delivery_slots WHERE recipient_id=? AND target_draw=?',(row['recipient_id'],row['target_draw'])).fetchone()
     if slot:
      c.execute("UPDATE robot_outbox SET status='cancelled',last_error='该会员本期合并消息已封存，迟到订单不重复发送',finished=? WHERE id=?",(utcnow(),row['id']));return True
     first=c.execute("SELECT MIN(created) FROM robot_outbox WHERE recipient_id=? AND target_draw=? AND status='pending'",(row['recipient_id'],row['target_draw'])).fetchone()[0]
     latest=c.execute("SELECT MAX(created) FROM robot_outbox WHERE recipient_id=? AND target_draw=? AND status='pending'",(row['recipient_id'],row['target_draw'])).fetchone()[0]
     if time.time()-datetime.fromisoformat(latest).timestamp()<cfg['merge_wait_seconds'] and time.time()-datetime.fromisoformat(first).timestamp()<10:return False
    members=[row]
    if not row['is_test']:
     for candidate in c.execute("SELECT * FROM robot_outbox WHERE target_draw=? AND recipient_id=? AND status='pending' AND is_test=0 AND attempts=0 AND id!=? AND id NOT IN (SELECT outbox_id FROM robot_batch_items) ORDER BY id",(row['target_draw'],row['recipient_id'],row['id'])):
      r=dict(candidate)
      if self.recipient_active(c,r) and self.active_strategy(c,r['strategy_id']):members.append(r)
    batch_id='pc28-batch-'+secrets.token_hex(16);wire=json.loads(row['request']);wire['event_id']=batch_id
    # Templates render once per order; message is the space-separated batch.
    message=' '.join(r['message'].strip() for r in members)
    if row['is_test']:wire=json.loads(row['request'])
    else:
     wire['payload']={'symbol':'PC28','text':message}
    c.execute('INSERT INTO robot_batches(id,request,created,endpoint) VALUES(?,?,?,?)',(batch_id,json.dumps(wire,ensure_ascii=False),utcnow(),platform['endpoint']))
    if not row['is_test']:c.execute('INSERT INTO robot_delivery_slots VALUES(?,?,?)',(row['recipient_id'],row['target_draw'],batch_id))
    for r in members:c.execute('INSERT INTO robot_batch_items VALUES(?,?)',(r['id'],batch_id))
   if not row['is_test']:
    from .maintenance import live_guard
    latest_draw=c.execute('SELECT * FROM draws ORDER BY id DESC LIMIT 1').fetchone()
    rest_reason=live_guard(c,dict(latest_draw) if latest_draw else {},datetime.now(timezone.utc),self.service.cfg)
    if rest_reason:
     c.execute("UPDATE robot_outbox SET status='cancelled',last_error=?,finished=? WHERE id IN (SELECT outbox_id FROM robot_batch_items WHERE batch_id=?)",('延迟发送前检查：'+rest_reason,utcnow(),batch_id));return True
    scheduled=row.get('scheduled_at')
    if not scheduled:
     latest_created=max(datetime.fromisoformat(r['created']).timestamp() for r in members)
     previous=c.execute("SELECT value FROM runtime_state WHERE key='robot_last_delay'").fetchone()
     choices=[n for n in range(cfg['delay_min_seconds'],cfg['delay_max_seconds']+1) if not previous or n!=int(previous[0])]
     chosen=choices[secrets.randbelow(len(choices))]
     c.execute("INSERT OR REPLACE INTO runtime_state(key,value,updated) VALUES('robot_last_delay',?,?)",(str(chosen),utcnow()))
     due=latest_created+chosen;scheduled=datetime.fromtimestamp(due,timezone.utc).isoformat()
     c.execute('UPDATE robot_outbox SET scheduled_at=?,next_attempt=MAX(next_attempt,?) WHERE id IN (SELECT outbox_id FROM robot_batch_items WHERE batch_id=?)',(scheduled,int(math.ceil(due)),batch_id))
    if datetime.fromisoformat(scheduled).timestamp()>time.time():return False
    event_time=min(r['event_time'] for r in members)
    previous=wire.get('expires_at')
    deadline=draw_deadline(c,row['target_draw'],event_time,cfg['max_age_seconds'],previous,latest_draw)
    if latest_draw and latest_draw['id']>=row['target_draw']:
     reason='延迟发送前该期已开奖，不再发送'
    elif min(event_time+cfg['max_age_seconds'],previous if previous is not None else math.inf)<=time.time():
     reason='超过本期有效期，未发送'
    elif deadline is None:
     c.execute("UPDATE robot_outbox SET last_error=?,next_attempt=? WHERE id IN (SELECT outbox_id FROM robot_batch_items WHERE batch_id=?)",
               ('等待确认目标期开奖时间',int(time.time())+3,batch_id))
     return True
    elif deadline['expires_at']<=time.time():
     reason='超过本期有效期，未发送'
    else:
     reason=''
    if reason:
     c.execute("UPDATE robot_outbox SET status='expired',last_error=?,finished=? WHERE id IN (SELECT outbox_id FROM robot_batch_items WHERE batch_id=?)",(reason,utcnow(),batch_id))
     for member in members:order_confirmation.alert(c,member,'发送过期',reason)
     return True
    wire.update(deadline)
    c.execute('UPDATE robot_batches SET request=? WHERE id=?',(json.dumps(wire,ensure_ascii=False),batch_id))
    if row.get('bot_id') is not None:
     risk_reason=robot_risk.admit(c,row['bot_id'],members)
     if risk_reason:
      c.execute("""UPDATE robot_outbox SET status='cancelled',last_error=?,finished=?
                   WHERE id IN (SELECT outbox_id FROM robot_batch_items WHERE batch_id=?)""",
                (risk_reason,utcnow(),batch_id));return True
    origin=self.service.cfg.get('deployment',{}).get('public_origin','')
    required=bool(platform.get('execution_required') or 'execution_callback' in wire)
    order_confirmation.attach(c,wire,members,required,origin+'/api/receiver/order-status')
    if required:wire['execution_callback']['platform_id']=row['platform_id']
    c.execute('UPDATE robot_batches SET request=? WHERE id=?',(json.dumps(wire,ensure_ascii=False),batch_id))
   frozen=c.execute('SELECT endpoint FROM robot_batches WHERE id=?',(batch_id,)).fetchone();endpoint=frozen['endpoint'] if frozen and frozen['endpoint'] else platform['endpoint']
   sent_at=utcnow()
   c.execute("UPDATE robot_outbox SET status='sending',attempts=attempts+1,lease_until=?,sent_at=? WHERE id IN (SELECT outbox_id FROM robot_batch_items WHERE batch_id=?)",(now+cfg['timeout_seconds']+30,sent_at,batch_id))
  status=None;error='';retry=False;attempted=False
  try:
   token=self.platform_secret(row['platform_id']).read();wire['timestamp']=int(time.time())
   if 'expires_at' in wire:
    wire['remaining_seconds']=wire['expires_at']-wire['timestamp']
    if wire['remaining_seconds']<=0:raise SignalExpired()
   data=json.dumps(wire,ensure_ascii=False,allow_nan=False).encode();req=urllib.request.Request(endpoint,data=data,headers={'Authorization':'Bearer '+token,'Content-Type':'application/json','Accept':'application/json'},method='POST');opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
   attempted=True
   with opener.open(req,timeout=cfg['timeout_seconds']) as response:status=response.status
  except SignalExpired:error='超过本期有效期，未发送';status=410
  except urllib.error.HTTPError as e:status=e.code;error=receiver_error(e);retry=status in (408,425,429) or status>=500
  except (urllib.error.URLError,TimeoutError,OSError):error='无法连接机器人接口或请求超时';retry=True
  except Exception:error='机器人密钥读取或请求构建失败'
  attempts=row['attempts']+1;success=status is not None and 200<=status<300
  state='sent' if success else 'expired' if status==410 and 'expires_at' in wire else 'retry' if retry and attempts<cfg['max_attempts'] else 'failed';delay=min(60,5*2**(attempts-1))
  with self.service.lock,self.db.tx() as c:
   finished=utcnow()
   c.execute('UPDATE robot_outbox SET status=?,last_status=?,last_error=?,next_attempt=?,finished=?,accepted_at=? WHERE id IN (SELECT outbox_id FROM robot_batch_items WHERE batch_id=?)',(state,status,'' if success else error,int(time.time())+delay,finished if state in ('sent','failed','expired') else None,finished if success else None,batch_id))
   if success:
    from .member_orders import import_signal,settle
    for member in c.execute('SELECT o.* FROM robot_outbox o JOIN robot_batch_items i ON i.outbox_id=o.id WHERE i.batch_id=? AND NOT EXISTS(SELECT 1 FROM robot_execution e WHERE e.outbox_id=o.id) ORDER BY o.id',(batch_id,)).fetchall():import_signal(c,self.service,member,batch_id,sent_at,finished)
    c.execute("""DELETE FROM robot_risk_holds WHERE outbox_id IN
      (SELECT i.outbox_id FROM robot_batch_items i JOIN member_orders m ON m.outbox_id=i.outbox_id WHERE i.batch_id=?)""",(batch_id,))
    settle(c)
   elif attempts==1 and (not attempted or status is not None and 400<=status<500 and not retry):
    c.execute('DELETE FROM robot_risk_holds WHERE outbox_id IN (SELECT outbox_id FROM robot_batch_items WHERE batch_id=?)',(batch_id,))
   if not success and state in ('failed','expired') and not row['is_test']:
    for member in members:
     if not attempted or attempts==1 and status is not None and 400<=status<500 and not retry:
      if order_confirmation.terminal(c,member,'rejected',error):continue
     order_confirmation.alert(c,member,'发送异常',error)
  return True
 def retry(self,key,user=None,ip=''):
  cfg=self.config()
  with self.db.tx() as c:
   row=c.execute("SELECT * FROM robot_outbox WHERE id=? AND status IN ('failed','retry') AND NOT EXISTS (SELECT 1 FROM robot_hidden_records h WHERE h.outbox_id=robot_outbox.id)",(key,)).fetchone()
   if not row:raise ValueError('该记录不能重试')
   if time.time()-row['event_time']>cfg['max_age_seconds']:raise ValueError('信号已过期，不再补发')
   if not row['is_test']:
    if not cfg['enabled']:raise ValueError('请先启用机器人推送')
    if not self.recipient_active(c,dict(row)):raise ValueError('会员推送已停用或配置已变更，不再补发旧订单')
   batch=c.execute('SELECT batch_id FROM robot_batch_items WHERE outbox_id=?',(key,)).fetchone()
   if batch:c.execute("UPDATE robot_outbox SET status='retry',attempts=0,next_attempt=0,last_error='' WHERE id IN (SELECT outbox_id FROM robot_batch_items WHERE batch_id=?) AND status IN ('failed','retry')",(batch[0],))
   else:c.execute("UPDATE robot_outbox SET status='retry',attempts=0,next_attempt=0,last_error='' WHERE id=?",(key,))
   if user:self.service.auth.audit(c,user['id'],'robot_retry',key,ip)
  return {'ok':True}
 def records(self,q,scope=None):
  page=max(1,int(q.get('page',1)));size=int(q.get('page_size',20))
  if size not in (10,20,50,100):raise ValueError('分页参数无效')
  status=q.get('status','all');allowed={'all','pending','sending','sent','retry','failed','expired','cancelled'}
  if status not in allowed:raise ValueError('发送状态无效')
  filters=['1'] if status=='all' else ['status=?'];args=[] if status=='all' else [status]
  for key,column in [('platform_id','platform_id'),('account_id','follow_account_id'),('bot_id','bot_id'),('user_id','member_user_id'),('draw','target_draw')]:
   if q.get(key):filters.append(column+'=?');args.append(int(q[key]))
  if scope is not None:
   filters.append("member_user_id IN (SELECT member_id FROM web_member_access WHERE admin_id=?)");args.append(scope)
  where=' AND '.join(filters);args=tuple(args)
  source="""(SELECT o.*,i.batch_id,COALESCE(i.batch_id,CASE WHEN o.is_test=1 THEN 'test:'||o.id ELSE 'queued:'||o.recipient_id||':'||o.target_draw||':'||o.status END) message_group FROM robot_outbox o LEFT JOIN robot_batch_items i ON i.outbox_id=o.id WHERE NOT EXISTS (SELECT 1 FROM robot_hidden_records h WHERE h.outbox_id=o.id) ORDER BY o.id)"""
  total=self.db.query('SELECT COUNT(*) n FROM (SELECT message_group FROM '+source+' WHERE '+where+' GROUP BY message_group)',args)[0]['n'];pages=max(1,(total+size-1)//size);page=min(page,pages)
  rows=self.db.query("SELECT MAX(id) id,MAX(event_id) event_id,MAX(name) name,GROUP_CONCAT(DISTINCT strategy_name) strategy_name,MAX(target_draw) target_draw,MIN(created) created,GROUP_CONCAT(message,' ') message,MAX(status) status,MAX(attempts) attempts,MAX(last_status) last_status,MAX(last_error) last_error,MAX(finished) finished,MAX(is_test) is_test,MAX(recipient_id) recipient_id,MAX(recipient_name) recipient_name,MAX(platform_id) platform_id,MAX(follow_account_id) follow_account_id,MAX(bot_id) bot_id,MAX(batch_id) batch_id,MIN(created) cached_at,MAX(scheduled_at) scheduled_at,MAX(sent_at) sent_at,MAX(accepted_at) accepted_at,COUNT(*) order_count FROM "+source+' WHERE '+where+' GROUP BY message_group ORDER BY MAX(id) DESC LIMIT ? OFFSET ?',(*args,size,(page-1)*size))
  names={p['id']:p['name'] for p in self.platforms()}
  for r in rows:
   r['platform_name']=names.get(r['platform_id'],'未知平台');a=self.db.query('SELECT name,external_account_id FROM robot_accounts WHERE id=?',(r['follow_account_id'],));b=self.db.query('SELECT name,external_bot_key FROM robot_bots WHERE id=?',(r['bot_id'],));r.update(account_name=a[0]['name'] if a else '历史账号',external_account_id=a[0]['external_account_id'] if a else None,bot_name=b[0]['name'] if b else '历史机器人',external_bot_key=b[0]['external_bot_key'] if b else None)
  return {'rows':rows,'total':total,'page':page,'pages':pages,'page_size':size}
 def batch_detail(self,key):
  row=self.db.query('SELECT o.*,i.batch_id FROM robot_outbox o LEFT JOIN robot_batch_items i ON i.outbox_id=o.id WHERE o.id=?',(key,))
  if not row:raise ValueError('发送记录不存在')
  row=row[0];batch=row['batch_id']
  selection='o.id IN (SELECT outbox_id FROM robot_batch_items WHERE batch_id=?)' if batch else 'o.id=?'
  items=self.db.query('SELECT o.id,o.id AS outbox_id,o.strategy_name,o.simulation_cents,o.push_cents,o.message,o.status,o.last_error,o.sent_at,o.accepted_at,e.status AS execution_status,e.detail AS execution_detail FROM robot_outbox o LEFT JOIN robot_execution e ON e.outbox_id=o.id WHERE '+selection+' ORDER BY o.id',(batch or row['id'],))
  wire=json.loads(row['request'])
  if batch:
   stored=self.db.query('SELECT request FROM robot_batches WHERE id=?',(batch,))
   if stored:wire=json.loads(stored[0]['request'])
  return {'batch_id':batch,'target_draw':row['target_draw'],'recipient_name':row['recipient_name'],'platform_id':row['platform_id'],'account_id':wire.get('account_id'),'bot_key':wire.get('bot_key'),'signal_type':wire.get('signal_type'),'event_id':wire.get('event_id'),'message':wire.get('payload',{}).get('text'),'items':items,'status':row['status'],'attempts':row['attempts'],'scheduled_at':row['scheduled_at'],'sent_at':row['sent_at'],'accepted_at':row['accepted_at'],'last_error':row['last_error']}
 def delete_records(self,body,user=None,ip=''):
  ids=body.get('ids')
  if not isinstance(ids,list) or not 1<=len(ids)<=100 or any(type(i)!=int or i<1 for i in ids):raise ValueError('请选择1—100条合并记录')
  terminal={'sent','failed','expired','cancelled'};targets=set()
  with self.service.lock,self.db.tx() as c:
   for key in set(ids):
    row=c.execute('SELECT o.*,i.batch_id FROM robot_outbox o LEFT JOIN robot_batch_items i ON i.outbox_id=o.id WHERE o.id=?',(key,)).fetchone()
    if not row:raise ValueError('记录不存在，请刷新列表')
    if row['batch_id']:members=c.execute('SELECT o.id,o.status FROM robot_outbox o JOIN robot_batch_items i ON i.outbox_id=o.id WHERE i.batch_id=?',(row['batch_id'],)).fetchall()
    elif row['is_test']:members=[row]
    else:members=c.execute('SELECT o.id,o.status FROM robot_outbox o WHERE o.recipient_id=? AND o.target_draw=? AND o.status=? AND o.is_test=0 AND NOT EXISTS (SELECT 1 FROM robot_batch_items i WHERE i.outbox_id=o.id)',(row['recipient_id'],row['target_draw'],row['status'])).fetchall()
    if any(r['status'] not in terminal for r in members):raise ValueError('排队、发送中或等待重试的消息不能删除，请等待发送结束')
    targets.update(r['id'] for r in members)
   now=utcnow()
   for key in targets:c.execute('INSERT OR IGNORE INTO robot_hidden_records VALUES(?,?)',(key,now))
   if user:self.service.auth.audit(c,user['id'],'robot_records_delete','robot',ip,{'selected_messages':len(set(ids)),'order_records':len(targets)})
  return {'ok':True,'deleted':len(set(ids))}
 def start(self):
  self.platform_workers={};self.thread=threading.Thread(target=self.run,name='pc28-robot-capture',daemon=True);self.thread.start()
 def run_platform(self,key):
  next_send=0
  while not self.service.stop.wait(.25):
   try:
    if time.monotonic()>=next_send and self.deliver_one(key):next_send=time.monotonic()+self.config()['send_interval_seconds']
   except Exception:
    self.db.log_job('robot','error',{'platform_id':key,'error':'平台发送任务异常，请检查发送记录'});self.service.stop.wait(2)
 def run(self):
  while not self.service.stop.wait(.5):
   try:
    self.capture()
    from .member_orders import sync
    sync(self.service)
    for platform in self.platforms():
     key=platform['id'];worker=self.platform_workers.get(key)
     if not worker or not worker.is_alive():
      worker=threading.Thread(target=self.run_platform,args=(key,),name='pc28-platform-'+str(key),daemon=True);self.platform_workers[key]=worker;worker.start()
   except Exception:
    self.db.log_job('robot','error',{'error':'机器人捕获任务异常，请检查发送记录'});self.service.stop.wait(2)

