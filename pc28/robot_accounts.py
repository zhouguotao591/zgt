"""Normalized operator-managed receiving accounts, bots and versioned subscriptions.
Legacy bindings are durable delivery targets, not a second capture pipeline.
"""
import json,re,sqlite3,secrets
from .auth import AuthError
from .model import utcnow
from . import robot_risk

SCHEMA="""
CREATE TABLE IF NOT EXISTS robot_accounts(id INTEGER PRIMARY KEY,user_id INTEGER NOT NULL,platform_id INTEGER NOT NULL,external_account_id INTEGER NOT NULL,name TEXT NOT NULL,enabled INTEGER NOT NULL DEFAULT 0,default_bot_id INTEGER,version INTEGER NOT NULL DEFAULT 1,deleted INTEGER NOT NULL DEFAULT 0,created TEXT NOT NULL,updated TEXT NOT NULL,UNIQUE(platform_id,external_account_id));
CREATE TABLE IF NOT EXISTS robot_bots(id INTEGER PRIMARY KEY,account_id INTEGER NOT NULL,external_bot_key INTEGER NOT NULL,name TEXT NOT NULL,signal_type TEXT NOT NULL,enabled INTEGER NOT NULL DEFAULT 1,version INTEGER NOT NULL DEFAULT 1,deleted INTEGER NOT NULL DEFAULT 0,binding_id INTEGER UNIQUE,created TEXT NOT NULL,updated TEXT NOT NULL,UNIQUE(account_id,external_bot_key));
CREATE TABLE IF NOT EXISTS robot_subscriptions(id INTEGER PRIMARY KEY,account_id INTEGER NOT NULL,strategy_id TEXT NOT NULL,bot_id INTEGER NOT NULL,amount_mode TEXT NOT NULL,fixed_cents INTEGER NOT NULL DEFAULT 10000,ratio_units INTEGER NOT NULL DEFAULT 10000,enabled INTEGER NOT NULL DEFAULT 1,version INTEGER NOT NULL DEFAULT 1,starts TEXT NOT NULL,legacy_fraction INTEGER NOT NULL DEFAULT 0,UNIQUE(account_id,bot_id,strategy_id));
CREATE TABLE IF NOT EXISTS robot_binding_map(binding_id INTEGER PRIMARY KEY,account_id INTEGER NOT NULL,bot_id INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS robot_account_order_slots(engine TEXT NOT NULL,order_id INTEGER NOT NULL,account_id INTEGER NOT NULL,outbox_id INTEGER NOT NULL,PRIMARY KEY(engine,order_id,account_id));
CREATE TABLE IF NOT EXISTS robot_capture_skips(engine TEXT,order_id INTEGER,account_id INTEGER,bot_id INTEGER,strategy_id TEXT,target_draw INTEGER,reason TEXT,created TEXT,PRIMARY KEY(engine,order_id,account_id,strategy_id));
CREATE TABLE IF NOT EXISTS robot_account_migrations(name TEXT PRIMARY KEY,created TEXT NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS robot_conflict_binding ON robot_binding_map(binding_id);
CREATE TABLE IF NOT EXISTS robot_account_conflicts(id INTEGER PRIMARY KEY,platform_id INTEGER,external_account_id INTEGER,binding_id INTEGER UNIQUE,reason TEXT NOT NULL,created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS robot_catalog(id INTEGER PRIMARY KEY,platform_id INTEGER NOT NULL,name TEXT NOT NULL,version INTEGER NOT NULL DEFAULT 1,created TEXT NOT NULL,updated TEXT NOT NULL);
"""
def integer(value,label,lo=1,hi=2147483647):
 if type(value)!=int or not lo<=value<=hi:raise ValueError(label+'须为'+str(lo)+'—'+str(hi)+'的整数')
 return value
def flag(value):
 if type(value)!=bool:raise ValueError('启停状态无效')
 return int(value)
def label(value):
 if not isinstance(value,str) or not 1<=len(value.strip())<=50:raise ValueError('名称须为1—50个字符')
 return value.strip()
def conflict():raise AuthError('配置已被其他管理员修改，请刷新后重试',409,'version_conflict')

class Accounts:
 def init_accounts(self):
  with self.service.lock,self.db.tx() as c:
   for sql in (SCHEMA+robot_risk.SCHEMA).split(';'):
    if sql.strip():c.execute(sql)
   robot_risk.migrate(c)
   subscription_schema=c.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='robot_subscriptions'").fetchone()[0]
   if 'UNIQUE(account_id,strategy_id)' in subscription_schema:
    c.execute('ALTER TABLE robot_subscriptions RENAME TO robot_subscriptions_v1')
    c.execute(next(sql for sql in SCHEMA.split(';') if 'CREATE TABLE IF NOT EXISTS robot_subscriptions(' in sql))
    fields=','.join(r[1] for r in c.execute('PRAGMA table_info(robot_subscriptions)'))
    c.execute('INSERT INTO robot_subscriptions('+fields+') SELECT '+fields+' FROM robot_subscriptions_v1')
    c.execute('DROP TABLE robot_subscriptions_v1')
   from .money_plans import initialize as initialize_money
   initialize_money(c)
   if 'default_cents' not in {r['name'] for r in c.execute('PRAGMA table_info(robot_accounts)')}:c.execute('ALTER TABLE robot_accounts ADD COLUMN default_cents INTEGER NOT NULL DEFAULT 10000')
   if 'catalog_id' not in {r['name'] for r in c.execute('PRAGMA table_info(robot_bots)')}:
    c.execute('ALTER TABLE robot_bots ADD COLUMN catalog_id INTEGER REFERENCES robot_catalog(id)')
   c.execute('CREATE UNIQUE INDEX IF NOT EXISTS robot_account_catalog ON robot_bots(account_id,catalog_id) WHERE catalog_id IS NOT NULL AND deleted=0')
   c.execute('DROP INDEX IF EXISTS robot_binding_platform_user')
   columns={r['name'] for r in c.execute('PRAGMA table_info(robot_outbox)')}
   for key,definition in [('follow_account_id','INTEGER'),('bot_id','INTEGER'),('simulation_cents','INTEGER'),('config_version','INTEGER'),('lease_until','INTEGER')]:
    if key not in columns:c.execute('ALTER TABLE robot_outbox ADD COLUMN '+key+' '+definition)
   batch_columns={r['name'] for r in c.execute('PRAGMA table_info(robot_batches)')}
   if 'endpoint' not in batch_columns:c.execute('ALTER TABLE robot_batches ADD COLUMN endpoint TEXT')
   # Each binding keeps its old identity, delivery slot and outbox history.
   for row in c.execute('SELECT * FROM robot_bindings WHERE deleted=0 AND id NOT IN (SELECT binding_id FROM robot_binding_map)').fetchall():self._import_binding(c,dict(row),migration=True)
   for bot in c.execute('SELECT b.*,a.platform_id FROM robot_bots b JOIN robot_accounts a ON a.id=b.account_id WHERE b.catalog_id IS NULL').fetchall():
    catalog_id=c.execute('INSERT INTO robot_catalog(platform_id,name,created,updated) VALUES(?,?,?,?)',(bot['platform_id'],bot['name'],bot['created'],bot['updated'])).lastrowid
    c.execute('UPDATE robot_bots SET catalog_id=? WHERE id=?',(catalog_id,bot['id']))
   c.execute("UPDATE robot_outbox SET follow_account_id=(SELECT account_id FROM robot_binding_map m WHERE m.binding_id=recipient_id),bot_id=(SELECT bot_id FROM robot_binding_map m WHERE m.binding_id=recipient_id) WHERE follow_account_id IS NULL AND EXISTS(SELECT 1 FROM robot_binding_map m JOIN robot_accounts a ON a.id=m.account_id WHERE m.binding_id=recipient_id AND a.platform_id=robot_outbox.platform_id AND a.external_account_id=CASE WHEN json_valid(request) THEN json_extract(request,'$.account_id') ELSE NULL END AND (member_user_id IS NULL OR a.user_id=member_user_id))")
   c.execute('INSERT OR IGNORE INTO robot_account_order_slots SELECT engine,order_id,follow_account_id,MIN(id) FROM robot_outbox WHERE is_test=0 AND follow_account_id IS NOT NULL AND order_id IS NOT NULL GROUP BY engine,order_id,follow_account_id')
   c.execute("INSERT OR IGNORE INTO robot_account_migrations VALUES('normalized-v1',?)",(utcnow(),))
 def _import_binding(self,c,b,migration=False):
  now=utcnow();mapping=c.execute('SELECT * FROM robot_binding_map WHERE binding_id=?',(b['id'],)).fetchone()
  if mapping:
   a=c.execute('SELECT * FROM robot_accounts WHERE id=?',(mapping['account_id'],)).fetchone()
   if (a['platform_id'],a['user_id'],a['external_account_id'])!=(b['platform_id'],b['user_id'],b['account_id']):raise ValueError('已有绑定的会员、平台及外部账号不可改投，请新增账号')
   aid=a['id'];bid=mapping['bot_id'];c.execute('UPDATE robot_bots SET enabled=?,signal_type=?,version=version+1,updated=? WHERE id=?',(b['enabled'],b['signal_type'],now,bid))
   c.execute('UPDATE robot_accounts SET version=version+1,updated=? WHERE id=?',(now,aid))
   if c.execute('SELECT COUNT(*) FROM robot_bots WHERE account_id=? AND deleted=0',(aid,)).fetchone()[0]==1:c.execute('UPDATE robot_accounts SET enabled=? WHERE id=?',(b['enabled'],aid))
  else:
   a=c.execute('SELECT * FROM robot_accounts WHERE platform_id=? AND external_account_id=?',(b['platform_id'],b['account_id'])).fetchone()
   if a and a['user_id']!=b['user_id']:
    if not migration:raise ValueError('该平台账号已经归属其他会员')
    c.execute('UPDATE robot_accounts SET enabled=0 WHERE id=?',(a['id'],));c.execute('UPDATE robot_bindings SET enabled=0 WHERE platform_id=? AND account_id=?',(b['platform_id'],b['account_id']));c.execute('INSERT OR IGNORE INTO robot_account_conflicts(platform_id,external_account_id,binding_id,reason,created) VALUES(?,?,?,?,?)',(b['platform_id'],b['account_id'],b['id'],'同平台账号归属冲突，目标已停用，请核对',now));return
   aid=a['id'] if a else c.execute('INSERT INTO robot_accounts(user_id,platform_id,external_account_id,name,enabled,created,updated) VALUES(?,?,?,?,?,?,?)',(b['user_id'],b['platform_id'],b['account_id'],'账号 '+str(b['account_id']),b['enabled'],now,now)).lastrowid
   bid=c.execute('INSERT INTO robot_bots(account_id,external_bot_key,name,signal_type,enabled,binding_id,created,updated) VALUES(?,?,?,?,?,?,?,?)',(aid,b['bot_key'],'机器人 '+str(b['bot_key']),b['signal_type'],b['enabled'],b['id'],now,now)).lastrowid
   c.execute('INSERT INTO robot_binding_map VALUES(?,?,?)',(b['id'],aid,bid));c.execute('UPDATE robot_accounts SET default_bot_id=COALESCE(default_bot_id,?) WHERE id=?',(bid,aid))
  amounts=json.loads(b['strategy_amounts']);selected=json.loads(b['strategy_ids']);starts=json.loads(b['starts'])
  if migration:starts={k:max(starts.get(k,0),v) for k,v in self.maximums(c).items()}
  for old in c.execute('SELECT strategy_id FROM robot_subscriptions WHERE account_id=? AND bot_id=?',(aid,bid)).fetchall():
   if old[0] not in selected:c.execute('UPDATE robot_subscriptions SET enabled=0,version=version+1 WHERE account_id=? AND bot_id=? AND strategy_id=?',(aid,bid,old[0]))
  for sid in selected:
   setting=amounts.get(sid,b['default_cents']);mode='fixed' if type(setting)==int else setting['mode'];fixed=setting if type(setting)==int else setting.get('cents',10000);ratio=setting.get('ratio_units',10000) if isinstance(setting,dict) else 10000
   c.execute('INSERT INTO robot_subscriptions(account_id,strategy_id,bot_id,amount_mode,fixed_cents,ratio_units,enabled,starts,legacy_fraction) VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(account_id,bot_id,strategy_id) DO UPDATE SET amount_mode=excluded.amount_mode,fixed_cents=excluded.fixed_cents,ratio_units=excluded.ratio_units,enabled=excluded.enabled,starts=excluded.starts,legacy_fraction=excluded.legacy_fraction,version=version+1',(aid,sid,bid,mode,fixed,ratio,b['enabled'],json.dumps(starts),int(fixed%100!=0)))
  if migration:
   c.execute('UPDATE robot_bindings SET starts=? WHERE id=?',(json.dumps(starts),b['id']))
   # A cutover cancels only unstarted old tasks; accepted and in-flight history stays intact.
   c.execute("UPDATE robot_outbox SET status='cancelled',last_error='账号迁移：旧待发送任务不补发',finished=? WHERE recipient_id=? AND status IN ('pending','retry')",(now,b['id']))
 def capture_gates(self,c,context,order):
  rows=c.execute('SELECT s.*,a.enabled ae,a.deleted ad,a.user_id,a.platform_id,b.enabled be,b.deleted bd,p.enabled pe,u.active ua FROM robot_subscriptions s JOIN robot_accounts a ON a.id=s.account_id JOIN robot_bots b ON b.id=s.bot_id JOIN robot_platforms p ON p.id=a.platform_id JOIN web_users u ON u.id=a.user_id WHERE s.strategy_id=?',(context['strategy_id'],)).fetchall()
  for row in rows:
   if order['id']<=json.loads(row['starts']).get(context['engine'],0):continue
   reasons=[]
   for condition,message in [(not row['pe'],'平台停用'),(not row['ua'],'会员停用'),(not row['ae'] or row['ad'],'账号停用或归档'),(not row['be'] or row['bd'],'机器人停用或归档'),(not row['enabled'],'策略订阅停用'),(not self.active_strategy(c,context['strategy_id']),'模拟策略停用')]:
    if condition:reasons.append(message)
   if reasons:c.execute('INSERT OR IGNORE INTO robot_capture_skips VALUES(?,?,?,?,?,?,?,?)',(context['engine'],order['id'],row['account_id'],row['bot_id'],context['strategy_id'],order['target_draw'],'；'.join(reasons),utcnow()))
 def _account(self,c,key,version=None):
  row=c.execute('SELECT * FROM robot_accounts WHERE id=? AND deleted=0',(key,)).fetchone()
  if not row:raise ValueError('下单账号不存在')
  if version is not None and (type(version)!=int or row['version']!=version):conflict()
  return row
 def _cancel(self,c,bindings,reason):
  for bid in bindings:c.execute("UPDATE robot_outbox SET status='cancelled',last_error=?,finished=? WHERE recipient_id=? AND status IN ('pending','retry')",(reason,utcnow(),bid))
 def _audit(self,c,user,action,target,ip,details):
  if user:self.service.auth.audit(c,user['id'],action,target,ip,{**details,'request_id':secrets.token_hex(12)})
 def _snapshot(self,c,key):
  a=c.execute('SELECT * FROM robot_accounts WHERE id=?',(key,)).fetchone()
  return {'account':dict(a) if a else None,'bots':[dict(r) for r in c.execute('SELECT * FROM robot_bots WHERE account_id=? ORDER BY id',(key,))],'subscriptions':[dict(r) for r in c.execute('SELECT * FROM robot_subscriptions WHERE account_id=? ORDER BY id',(key,))]}
 def _materialize(self,c,aid):
  a=self._account(c,aid);now=utcnow()
  for bot in c.execute('SELECT * FROM robot_bots WHERE account_id=? AND deleted=0',(aid,)).fetchall():
   subs=c.execute('SELECT * FROM robot_subscriptions WHERE account_id=? AND bot_id=? AND enabled=1 ORDER BY strategy_id',(aid,bot['id'])).fetchall();ids=[s['strategy_id'] for s in subs];amounts={s['strategy_id']:({'mode':'plan','plan_id':s['money_plan_id']} if s['amount_mode']=='plan' else {'mode':'fixed','cents':s['fixed_cents']} if s['amount_mode']=='fixed' else {'mode':'ratio','ratio_units':s['ratio_units']}) for s in subs};enabled=int(a['enabled'] and bot['enabled'] and bool(ids));old=c.execute('SELECT * FROM robot_bindings WHERE id=?',(bot['binding_id'],)).fetchone() if bot['binding_id'] else None
   if old and not old['deleted'] and (old['signal_type'],old['enabled'],json.loads(old['strategy_ids']),json.loads(old['strategy_amounts']))==(bot['signal_type'],enabled,ids,amounts):continue
   starts=json.dumps(self.maximums(c));base=10000
   if old:
    binding=old['id'];c.execute('UPDATE robot_bindings SET deleted=0,enabled=?,signal_type=?,strategy_ids=?,strategy_amounts=?,starts=?,updated=?,default_cents=? WHERE id=?',(enabled,bot['signal_type'],json.dumps(ids),json.dumps(amounts),starts,now,base,binding));self._cancel(c,[binding],'账号、机器人或订阅已变更，取消旧批次')
   else:
    binding=c.execute('INSERT INTO robot_bindings(user_id,platform_id,account_id,bot_key,signal_type,enabled,strategy_ids,strategy_amounts,default_cents,starts,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(a['user_id'],a['platform_id'],a['external_account_id'],bot['external_bot_key'],bot['signal_type'],enabled,json.dumps(ids),json.dumps(amounts),base,starts,now,now)).lastrowid
    c.execute('UPDATE robot_bots SET binding_id=? WHERE id=?',(binding,bot['id']));c.execute('INSERT INTO robot_binding_map VALUES(?,?,?)',(binding,aid,bot['id']))
 def _bot(self,c,aid,value):
  name=label(value.get('name'));external=integer(value.get('external_bot_key'),'机器人ID');signal=value.get('signal_type','BUY_SYSTEM');enabled=flag(value.get('enabled',True))
  if not isinstance(signal,str) or not re.fullmatch('[A-Z][A-Z0-9_]{1,63}',signal):raise ValueError('信号类型格式无效')
  key=value.get('id');old=c.execute('SELECT * FROM robot_bots WHERE id=? AND account_id=? AND deleted=0',(key,aid)).fetchone() if key else None
  if key and not old:raise ValueError('机器人不存在或不属于此账号')
  if old and value.get('version')!=old['version']:conflict()
  if old and external!=old['external_bot_key']:raise ValueError('机器人ID不可改投，请新增机器人')
  duplicate=c.execute('SELECT id,deleted FROM robot_bots WHERE account_id=? AND external_bot_key=? AND id!=?',(aid,external,key or 0)).fetchone()
  if duplicate and not duplicate['deleted']:raise ValueError('该账号内机器人ID已存在，请在列表编辑现有机器人')
  if duplicate:
   # Reuse the delivery target so historical draw deduplication still applies.
   key=duplicate['id']
  catalog_id=value.get('catalog_id')
  previous=c.execute('SELECT catalog_id FROM robot_bots WHERE id=?',(key,)).fetchone() if key else None
  if catalog_id is None and previous:catalog_id=previous['catalog_id']
  if catalog_id is not None:
   catalog_id=integer(catalog_id,'机器人目录ID')
   catalog=c.execute('SELECT r.name FROM robot_catalog r JOIN robot_accounts a ON a.platform_id=r.platform_id WHERE r.id=? AND a.id=?',(catalog_id,aid)).fetchone()
   if not catalog:
    raise ValueError('请选择该推送平台的机器人')
   if value.get('catalog_id') is not None:name=catalog['name']
   if c.execute('SELECT 1 FROM robot_bots WHERE account_id=? AND catalog_id=? AND deleted=0 AND id!=?',(aid,catalog_id,key or 0)).fetchone():
    raise ValueError('此账号已关联该机器人，请编辑现有订阅')
  now=utcnow()
  if catalog_id is None:
   catalog_id=c.execute('INSERT INTO robot_catalog(platform_id,name,created,updated) SELECT platform_id,?,?,? FROM robot_accounts WHERE id=?',(name,now,now,aid)).lastrowid
  if old or duplicate:c.execute('UPDATE robot_bots SET deleted=0,name=?,signal_type=?,enabled=?,version=version+1,updated=?,catalog_id=? WHERE id=?',(name,signal,enabled,now,catalog_id,key))
  else:key=c.execute('INSERT INTO robot_bots(account_id,external_bot_key,name,signal_type,enabled,created,updated,catalog_id) VALUES(?,?,?,?,?,?,?,?)',(aid,external,name,signal,enabled,now,now,catalog_id)).lastrowid
  return key
 def catalog_list(self,scope=None):
  return self.db.query("""SELECT r.*,p.name AS platform_name,
    (SELECT COUNT(DISTINCT s.account_id) FROM robot_subscriptions s
     JOIN robot_bots b ON b.id=s.bot_id JOIN robot_accounts a ON a.id=s.account_id
     WHERE b.catalog_id=r.id AND b.deleted=0 AND a.deleted=0 AND s.enabled=1
     AND (? IS NULL OR a.user_id IN (SELECT member_id FROM web_member_access WHERE admin_id=?))) AS subscriber_count
    FROM robot_catalog r JOIN robot_platforms p ON p.id=r.platform_id ORDER BY r.id DESC""",(scope,scope))
 def catalog_save(self,body,user,ip):
  name=label(body.get('name'));now=utcnow()
  with self.service.lock,self.db.tx() as c:
   key=body.get('id')
   if key:
    old=c.execute('SELECT * FROM robot_catalog WHERE id=?',(integer(key,'机器人目录ID'),)).fetchone()
    if not old:raise ValueError('机器人不存在')
    if body.get('version')!=old['version']:conflict()
    c.execute('UPDATE robot_catalog SET name=?,version=version+1,updated=? WHERE id=?',(name,now,key))
   else:
    platform=integer(body.get('platform_id'),'平台ID')
    if not c.execute('SELECT 1 FROM robot_platforms WHERE id=?',(platform,)).fetchone():raise ValueError('平台不存在')
    key=c.execute('INSERT INTO robot_catalog(platform_id,name,created,updated) VALUES(?,?,?,?)',(platform,name,now,now)).lastrowid
   self._audit(c,user,'robot_catalog_save',key,ip,{'name':name})
  return {'ok':True,'id':key}
 def _subscriptions(self,c,a,values,bot_ids=None,scope_bot_id=None):
  if not isinstance(values,list) or len(values)>100:raise ValueError('订阅列表无效')
  if scope_bot_id is not None:
   scope_bot_id=integer(scope_bot_id,'机器人ID')
   if not c.execute('SELECT 1 FROM robot_bots WHERE id=? AND account_id=? AND deleted=0',(scope_bot_id,a['id'])).fetchone():
    raise ValueError('接收机器人必须属于此账号')
  known={r['id'] for r in self.strategies()};seen=set();now=utcnow()
  for value in values:
   sid=value.get('strategy_id')
   if sid not in known:raise ValueError('策略无效')
   bid=scope_bot_id if scope_bot_id is not None else (bot_ids[integer(value.get('bot_index',0),'机器人序号',0,len(bot_ids)-1)] if bot_ids else value.get('bot_id',a['default_bot_id']))
   if not c.execute('SELECT 1 FROM robot_bots WHERE id=? AND account_id=? AND deleted=0',(bid,a['id'])).fetchone():raise ValueError('接收机器人必须属于此账号')
   pair=(bid,sid)
   if pair in seen:raise ValueError('同一机器人的策略重复')
   seen.add(pair)
   mode=value.get('amount_mode');old=c.execute('SELECT * FROM robot_subscriptions WHERE account_id=? AND bot_id=? AND strategy_id=?',(a['id'],bid,sid)).fetchone();fixed=value.get('fixed_cents',10000);ratio=value.get('ratio_units',10000)
   plan_id=None
   if mode=='plan':
    from .money_plans import available
    plan_id=integer(value.get('money_plan_id'),'金额方案ID');available(c,plan_id)
   elif mode=='fixed':
    integer(fixed,'固定金额分',100,2000000)
    if fixed%100 and not(old and old['legacy_fraction'] and old['fixed_cents']==fixed):raise ValueError('新固定金额须为1—20000整数U；旧小数配置可原值保留')
   elif mode=='ratio':integer(ratio,'比例万分位',1,1000000)
   else:raise ValueError('请选择金额方案、固定金额或模拟比例')
   enabled=flag(value.get('enabled',True));setting=(bid,mode,fixed,ratio,enabled)
   if old and (old['bot_id'],old['amount_mode'],old['fixed_cents'],old['ratio_units'],old['enabled'])==setting and old['money_plan_id']==plan_id:continue
   starts=json.dumps(self.maximums(c));c.execute('INSERT INTO robot_subscriptions(account_id,strategy_id,bot_id,amount_mode,fixed_cents,ratio_units,enabled,starts) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(account_id,bot_id,strategy_id) DO UPDATE SET amount_mode=excluded.amount_mode,fixed_cents=excluded.fixed_cents,ratio_units=excluded.ratio_units,enabled=excluded.enabled,starts=excluded.starts,version=version+1',(a['id'],sid,*setting,starts))
   c.execute('UPDATE robot_subscriptions SET money_plan_id=? WHERE account_id=? AND bot_id=? AND strategy_id=?',(plan_id,a['id'],bid,sid))
   if old and (old['amount_mode']!=mode or old['money_plan_id']!=plan_id):c.execute('DELETE FROM money_plan_states WHERE subscription_id=?',(old['id'],))
  for row in c.execute('SELECT bot_id,strategy_id FROM robot_subscriptions WHERE account_id=? AND enabled=1 AND (? IS NULL OR bot_id=?)',(a['id'],scope_bot_id,scope_bot_id)).fetchall():
   if tuple(row) not in seen:c.execute('UPDATE robot_subscriptions SET enabled=0,version=version+1 WHERE account_id=? AND bot_id=? AND strategy_id=?',(a['id'],*row))
 def account_detail(self,key):
  rows=self.db.query("SELECT a.*,u.username,p.name platform_name FROM robot_accounts a JOIN web_users u ON u.id=a.user_id JOIN robot_platforms p ON p.id=a.platform_id WHERE a.id=? AND a.deleted=0",(key,))
  if not rows:raise ValueError('下单账号不存在')
  a=rows[0];a['bots']=self.db.query('SELECT b.*,r.name AS catalog_name FROM robot_bots b LEFT JOIN robot_catalog r ON r.id=b.catalog_id WHERE b.account_id=? AND b.deleted=0 ORDER BY b.id',(key,));a['subscriptions']=self.db.query('SELECT * FROM robot_subscriptions WHERE account_id=? ORDER BY id',(key,));a['recent_skips']=self.db.query('SELECT target_draw,strategy_id,reason,created FROM robot_capture_skips WHERE account_id=? ORDER BY created DESC LIMIT 10',(key,));a['sending']=self.db.query("SELECT COUNT(*) n FROM robot_outbox WHERE follow_account_id=? AND status='sending'",(key,))[0]['n']
  from contextlib import closing
  with closing(self.db.connect()) as c:
   c.execute('BEGIN')
   for bot in a['bots']:bot['risk']=robot_risk.state(c,bot['id'])
  return a
 def risk_action(self,body,user,ip):
  key=integer(body.get('id'),'账号ID');bid=integer(body.get('bot_id'),'机器人ID')
  with self.service.lock,self.db.tx() as c:
   a=self._account(c,key)
   from .auth import require_member_scope
   require_member_scope(c,user,a['user_id'])
   bot=c.execute('SELECT * FROM robot_bots WHERE id=? AND account_id=? AND deleted=0',(bid,key)).fetchone()
   if not bot:raise ValueError('机器人不存在或不属于此账号')
   if body.get('bot_version')!=bot['version']:conflict()
   before=robot_risk.state(c,bid)
   action=body.get('action')
   if action=='save':after=robot_risk.save(c,bid,body)
   elif action=='resume':
    if not before or not before['paused']:raise ValueError('该机器人未被风控暂停')
    after=robot_risk.resume(c,bid,body.get('reset'))
    c.execute("""UPDATE robot_outbox SET status='cancelled',last_error='风控恢复后不补发旧订单',finished=?
      WHERE bot_id=? AND is_test=0 AND status IN ('pending','retry') AND attempts=0""",(utcnow(),bid))
   elif action=='reconcile':
    if body.get('confirmed') is not True:raise ValueError('请确认接收端未执行此订单')
    robot_risk.reconcile_rejected(c,bid,integer(body.get('outbox_id'),'发送记录ID'))
    after=robot_risk.state(c,bid)
   else:raise ValueError('未知机器人风控操作')
   c.execute('UPDATE robot_bots SET version=version+1,updated=? WHERE id=?',(utcnow(),bid))
   self._audit(c,user,'robot_risk_'+action,bid,ip,{'before':before,'after':after,'reset':body.get('reset'),'outbox_id':body.get('outbox_id')})
  return {'ok':True,'risk':after}
 def account_list(self,q,scope=None):
  from .admin import paging
  page,size=paging(q);where=['a.deleted=0'];args=[]
  if scope is not None:where.append('u.id IN (SELECT member_id FROM web_member_access WHERE admin_id=?)');args.append(scope)
  for field,column in [('user_id','a.user_id'),('platform_id','a.platform_id')]:
   if q.get(field):where.append(column+'=?');args.append(integer(int(q[field]),field))
  if q.get('q'):where.append('(a.name LIKE ? OR u.username LIKE ? OR CAST(a.external_account_id AS TEXT) LIKE ?)');args.extend(['%'+str(q['q'])[:50]+'%']*3)
  if q.get('enabled') in ('0','1'):where.append('a.enabled=?');args.append(int(q['enabled']))
  clause=' AND '.join(where);source='robot_accounts a JOIN web_users u ON u.id=a.user_id JOIN robot_platforms p ON p.id=a.platform_id';total=self.db.query('SELECT COUNT(*) n FROM '+source+' WHERE '+clause,args)[0]['n'];pages=max(1,(total+size-1)//size);page=min(page,pages)
  rows=self.db.query('SELECT a.*,u.username,u.active member_active,p.name platform_name,(SELECT COUNT(*) FROM robot_bots b WHERE b.account_id=a.id AND b.deleted=0) bot_count,(SELECT COUNT(*) FROM robot_subscriptions s WHERE s.account_id=a.id AND s.enabled=1) subscription_count FROM '+source+' WHERE '+clause+' ORDER BY a.id DESC LIMIT ? OFFSET ?',(*args,size,(page-1)*size))
  return {'rows':rows,'total':total,'page':page,'pages':pages,'page_size':size,'conflicts':self.db.query('SELECT * FROM robot_account_conflicts ORDER BY id DESC LIMIT 50') if scope is None else []}
 def account_save(self,body,user,ip):
  with self.service.lock,self.db.tx() as c:
   now=utcnow();key=body.get('id');before=self._snapshot(c,key) if key else None;name=label(body.get('name'));enabled=flag(body.get('enabled',False))
   if key:
    a=self._account(c,key,body.get('version',-1))
    for field in ('user_id','platform_id','external_account_id'):
     if field in body and body[field]!=a[field]:raise ValueError('归属和外部账号不可改投，请新增账号')
    default=body.get('default_bot_id',a['default_bot_id'])
    if not c.execute('SELECT 1 FROM robot_bots WHERE id=? AND account_id=? AND deleted=0',(default,key)).fetchone():raise ValueError('默认机器人必须属于此账号')
    default_cents=integer(body.get('default_cents',a['default_cents']),'账号默认金额分',100,2000000)
    if default_cents%100:raise ValueError('账号默认金额须为整数U')
    c.execute('UPDATE robot_accounts SET name=?,enabled=?,default_bot_id=?,default_cents=?,version=version+1,updated=? WHERE id=?',(name,enabled,default,default_cents,now,key))
   else:
    uid=integer(body.get('user_id'),'会员ID');platform=integer(body.get('platform_id'),'平台ID');external=integer(body.get('external_account_id'),'外部账号ID')
    if not c.execute("SELECT 1 FROM web_users WHERE id=? AND role='member'",(uid,)).fetchone():raise ValueError('会员不存在')
    if not c.execute('SELECT 1 FROM robot_platforms WHERE id=?',(platform,)).fetchone():raise ValueError('平台不存在')
    if c.execute('SELECT 1 FROM robot_accounts WHERE platform_id=? AND external_account_id=?',(platform,external)).fetchone():raise ValueError('该平台外部账号已存在，请勿重复归属')
    default_cents=integer(body.get('default_cents',10000),'账号默认金额分',100,2000000)
    if default_cents%100:raise ValueError('账号默认金额须为整数U')
    key=c.execute('INSERT INTO robot_accounts(user_id,platform_id,external_account_id,name,enabled,created,updated,default_cents) VALUES(?,?,?,?,?,?,?,?)',(uid,platform,external,name,enabled,now,now,default_cents)).lastrowid
    bots=body.get('bots',[body.get('bot',{})])
    if not isinstance(bots,list) or not 1<=len(bots)<=20:raise ValueError('请配置1—20个机器人')
    bids=[self._bot(c,key,v) for v in bots];c.execute('UPDATE robot_accounts SET default_bot_id=? WHERE id=?',(bids[0],key));a=self._account(c,key)
    self._subscriptions(c,a,body.get('subscriptions',[]),bids)
    if not c.execute('SELECT 1 FROM robot_subscriptions WHERE account_id=? AND enabled=1',(key,)).fetchone():raise ValueError('新增账号至少订阅一个策略')
   self._materialize(c,key);self._audit(c,user,'robot_account_save',key,ip,{'before':before,'after':self._snapshot(c,key),'copy_from':body.get('copy_from')})
  return {'ok':True,'id':key}
 def account_action(self,kind,body,user,ip):
  key=integer(body.get('id'),'账号ID')
  with self.service.lock,self.db.tx() as c:
   a=self._account(c,key,body.get('version',-1));before=self._snapshot(c,key);now=utcnow()
   if kind=='subscriptions':
    scope=integer(body['bot_id'],'机器人ID') if 'bot_id' in body else None
    self._subscriptions(c,a,body.get('subscriptions'),scope_bot_id=scope)
   elif kind=='bot-save':
    bid=self._bot(c,key,body['bot'])
    if body.get('make_default'):c.execute('UPDATE robot_accounts SET default_bot_id=? WHERE id=?',(bid,key))
   elif kind=='bot-control':
    b=c.execute('SELECT * FROM robot_bots WHERE id=? AND account_id=? AND deleted=0',(body.get('bot_id'),key)).fetchone()
    if not b:raise ValueError('机器人不存在')
    if body.get('bot_version')!=b['version']:conflict()
    enabled=flag(body.get('enabled',False));deleted=flag(body.get('deleted',False))
    if deleted and c.execute('SELECT 1 FROM robot_subscriptions WHERE bot_id=? AND enabled=1',(b['id'],)).fetchone():raise ValueError('请先迁移或停用该机器人的订阅，再删除')
    if deleted and a['default_bot_id']==b['id']:raise ValueError('请先指定其他默认机器人')
    c.execute('UPDATE robot_bots SET enabled=?,deleted=?,version=version+1,updated=? WHERE id=?',(enabled,deleted,now,b['id']))
    if deleted:self._cancel(c,[b['binding_id']],'机器人已删除');c.execute('UPDATE robot_bindings SET enabled=0,deleted=1 WHERE id=?',(b['binding_id'],))
   elif kind=='account-control':c.execute('UPDATE robot_accounts SET enabled=? WHERE id=?',(flag(body.get('enabled')),key))
   elif kind=='account-delete':
    c.execute('UPDATE robot_accounts SET enabled=0,deleted=1 WHERE id=?',(key,));bindings=[r[0] for r in c.execute('SELECT binding_id FROM robot_bots WHERE account_id=?',(key,))];self._cancel(c,bindings,'下单账号已归档');c.execute('UPDATE robot_bindings SET enabled=0,deleted=1 WHERE id IN (SELECT binding_id FROM robot_bots WHERE account_id=?)',(key,))
   else:raise ValueError('未知账号操作')
   c.execute('UPDATE robot_accounts SET version=version+1,updated=? WHERE id=?',(now,key))
   if kind!='account-delete':self._materialize(c,key)
   self._audit(c,user,'robot_'+kind.replace('-','_'),key,ip,{'before':before,'after':self._snapshot(c,key),'sending_not_recalled':True})
  return {'ok':True,'id':key}
 def account_bulk(self,body,user,ip):
  items=body.get('items');operation=body.get('operation')
  if not isinstance(items,list) or not 1<=len(items)<=100 or operation not in ('enable','disable','delete','subscriptions'):raise ValueError('批量操作须选择1—100个账号及有效操作')
  results=[]
  for item in items:
   try:
    args={**item,**({'subscriptions':body.get('subscriptions')} if operation=='subscriptions' else {'enabled':operation=='enable'})};self.account_action('subscriptions' if operation=='subscriptions' else 'account-delete' if operation=='delete' else 'account-control',args,user,ip);results.append({'id':item.get('id'),'ok':True})
   except (ValueError,AuthError) as e:results.append({'id':item.get('id'),'ok':False,'error':str(e)})
  return {'results':results,'success':sum(r['ok'] for r in results)}
