"""Administrator controls for membership and this site's collector/research service."""
import copy,json,secrets,time
from .auth import AuthError,digest,hash_password,is_main_admin,member_scope,require_member_scope
from .model import utcnow

def paging(q):
 page=max(1,int(q.get('page',1)));size=int(q.get('page_size',20))
 if size not in (10,20,50,100):raise ValueError('每页条数须为10、20、50、100')
 return page,size

def page_rows(db,table,columns,q,where='1',args=()):
 page,size=paging(q);total=db.query('SELECT COUNT(*) n FROM '+table+' WHERE '+where,args)[0]['n'];pages=max(1,(total+size-1)//size);page=min(page,pages)
 rows=db.query('SELECT '+columns+' FROM '+table+' WHERE '+where+' ORDER BY id DESC LIMIT ? OFFSET ?',(*args,size,(page-1)*size))
 return {'rows':rows,'total':total,'page':page,'pages':pages,'page_size':size}

def get(service,path,q,user=None):
 db=service.db;auth=service.auth;scope=member_scope(user)
 if path=='ai':return service.ai.admin()
 if path=='algorithms':return service.algorithm_hall.listing(user['id'],admin=True)
 if path=='algorithm-detail':
  if 'backtest' in q:return service.algorithm_hall.backtest(q.get('id'),q['backtest'],service.cfg['odds'],q.get('interval','1h'),admin=True,cutoff=q.get('cutoff'))
  if 'interval' in q:return service.algorithm_hall.income(q.get('id'),q['interval'],admin=True)
  return service.algorithm_hall.detail(q.get('id'),q.get('page',1),q.get('page_size',20),admin=True,window=q.get('window'))
 if path=='administrator-members':
  if not is_main_admin(user):raise AuthError('仅 admin 可配置会员可见范围',403,'permission_denied')
  key=int(q.get('id',0));administrator=member_access_target(db,key)
  members=db.query("""SELECT u.id,u.username,u.active,
   EXISTS(SELECT 1 FROM web_member_access access WHERE access.admin_id=? AND access.member_id=u.id) AS assigned
   FROM web_users u WHERE u.role='member' ORDER BY u.username,u.id""",(key,))
  return {'administrator':administrator,'members':members}
 if path=='robot':
  from .robot_signals import ENDPOINT
  return {'platforms':service.robot.platforms(),'config':service.robot.config(),'endpoint':ENDPOINT,'token_configured':service.robot.secret.exists(),'strategies':service.robot.strategies(),'bindings':service.robot.bindings(scope),'members':service.robot.members(scope)}
 if path=='robot-accounts':return service.robot.account_list(q,scope)
 if path=='robot-catalog':return service.robot.catalog_list(scope)
 if path=='robot-account':
  key=int(q.get('id',0));check_resource_member(service,user,'robot_accounts','user_id',key)
  return service.robot.account_detail(key)
 if path=='robot-batch':
  key=int(q.get('id',0));check_resource_member(service,user,'robot_outbox','member_user_id',key)
  return service.robot.batch_detail(key)
 if path=='robot-records':return service.robot.records(q,scope)
 if path=='overview':
  member_filter='' if scope is None else ' AND u.id IN (SELECT member_id FROM web_member_access WHERE admin_id=?)'
  member_args=() if scope is None else (scope,)
  return {'members':db.query("SELECT COUNT(*) n FROM web_users u WHERE role='member'"+member_filter,member_args)[0]['n'],'active_members':db.query("SELECT COUNT(*) n FROM web_users u WHERE role='member' AND active=1"+member_filter,member_args)[0]['n'],'member_net_profit':db.query("SELECT COALESCE(SUM(o.pnl),0) n FROM member_orders o JOIN web_users u ON u.id=o.user_id WHERE u.role='member' AND o.status='settled'"+member_filter,member_args)[0]['n'],'usable_invites':db.query('SELECT COUNT(*) n FROM web_invites WHERE active=1 AND used<max_uses AND expires>? AND (? IS NULL OR created_by=?)',(int(time.time()),scope,scope))[0]['n'],'site':{'site_name':auth.setting('site_name'),'registration_enabled':auth.setting('registration_enabled')=='true','collector_enabled':service.cfg['collector']['enabled'],'interval_seconds':service.cfg['collector']['interval_seconds'],'maintenance_guard_enabled':service.cfg['collector'].get('maintenance_guard_enabled',True)},'count':db.query('SELECT COUNT(*) n FROM draws')[0]['n'],'latest':db.tail(1)[-1]['id'] if db.tail(1) else None,'conflicts':db.query('SELECT COUNT(*) n FROM conflicts WHERE resolved=0')[0]['n'],'gaps':db.gaps(),'tasks':list(service.tasks.values())[-20:],'jobs':db.query('SELECT * FROM jobs ORDER BY id DESC LIMIT 20')}
 if path in ('users','member-orders','member-ledger'):
  from . import member_orders
  return member_orders.users(service,q,user) if path=='users' else member_orders.listing(service,q,path=='member-ledger',user)
 if path=='invites':return page_rows(db,'web_invites','id,note,max_uses,used,expires,active,created,created_by',q,'1' if scope is None else 'created_by=?',() if scope is None else (scope,))
 if path=='audit':
  if scope is None:return page_rows(db,'web_auth_events','id,created,actor,action,target,ip,details',q)
  condition="""actor=? AND (
   action IN ('login','logout','password_change','invite_create','invite_revoke')
   OR (action IN ('member_create','member_capital_adjust','user_update','user_password_reset','member_mfa_policy','member_mfa_unbind')
       AND target IN (SELECT CAST(id AS TEXT) FROM web_users WHERE role='member' AND id IN (SELECT member_id FROM web_member_access WHERE admin_id=?))))"""
  return page_rows(db,'web_auth_events','id,created,actor,action,target,ip,details',q,condition,(scope,scope))
 raise ValueError('未知后台接口')

def member_access_target(db,key):
 rows=db.query("""SELECT id,username,active FROM web_users WHERE id=? AND role='admin'
  AND lower(username)!='admin' AND id NOT IN (SELECT user_id FROM web_admin_removed)""",(key,))
 if not rows:raise ValueError('管理员不存在')
 return rows[0]

def check_resource_member(service,user,table,column,key):
 scope=member_scope(user)
 if scope is not None and not service.db.query(
  f"SELECT 1 FROM {table} r JOIN web_users u ON u.id=r.{column} WHERE r.id=? AND u.role='member' AND u.id IN (SELECT member_id FROM web_member_access WHERE admin_id=?)",(key,scope)):
  raise AuthError('只能管理分配给自己的会员',403,'permission_denied')

def post(service,path,body,user,ip):
 db=service.db;auth=service.auth
 if path in ('strategy-publish','algorithm-publish'):
  from .algorithm_hall import BY_ID
  kind=path.split('-',1)[0]
  key=body.get('id');published=body.get('published')
  known=BY_ID if kind=='algorithm' else {r['id'] for r in service.robot.strategies()}
  if not isinstance(key,str) or key not in known or type(published)!=bool:raise ValueError('策略、算法或上架状态无效')
  with db.tx() as c:
   c.execute('INSERT INTO public_visibility VALUES(?,?,?) ON CONFLICT(kind,item_id) DO UPDATE SET published=excluded.published',(kind,key,int(published)))
   auth.audit(c,user['id'],kind+'_publish',key,ip,{'published':published})
  return {'ok':True,'published':published}
 if path=='administrator-members':
  if not is_main_admin(user):raise AuthError('仅 admin 可配置会员可见范围',403,'permission_denied')
  key=body.get('id');add=body.get('add');remove=body.get('remove')
  if type(key)!=int or any(not isinstance(ids,list) or any(type(uid)!=int or uid<1 for uid in ids) for ids in (add,remove)):
   raise ValueError('管理员或会员编号无效')
  add,remove=set(add),set(remove)
  if add&remove:raise ValueError('同一会员不能同时分配和取消')
  with service.lock,db.tx() as c:
   member_access_target(db,key)
   for uid in add|remove:
    if not c.execute("SELECT 1 FROM web_users WHERE id=? AND role='member'",(uid,)).fetchone():
     raise ValueError('会员不存在')
   c.executemany('INSERT OR IGNORE INTO web_member_access(admin_id,member_id) VALUES(?,?)',((key,uid) for uid in add))
   c.executemany('DELETE FROM web_member_access WHERE admin_id=? AND member_id=?',((key,uid) for uid in remove))
   auth.audit(c,user['id'],'administrator_members_update',key,ip,{'added':sorted(add),'removed':sorted(remove)})
  return {'ok':True}
 if path in ('member-create','member-capital'):
  from . import member_orders
  return member_orders.create(service,body,user,ip) if path=='member-create' else member_orders.adjust(service,body,user,ip)
 if member_scope(user) is not None:
  if path in ('robot-account-save','robot-account-control','robot-account-delete','robot-bot-save','robot-bot-control','robot-subscriptions') and body.get('id'):
   check_resource_member(service,user,'robot_accounts','user_id',body['id'])
  if (path=='robot-account-save' and not body.get('id')) or path=='robot-binding-save':
   check_resource_member(service,user,'web_users','id',body.get('user_id'))
  if path=='robot-account-save' and body.get('copy_from'):
   check_resource_member(service,user,'robot_accounts','user_id',body['copy_from'])
  if path in ('robot-binding-save','robot-binding-control') and body.get('id'):
   check_resource_member(service,user,'robot_bindings','user_id',body['id'])
  if path=='robot-account-bulk' and isinstance(body.get('items'),list):
   for item in body['items']:
    if isinstance(item,dict):check_resource_member(service,user,'robot_accounts','user_id',item.get('id'))
  if path=='robot-retry':check_resource_member(service,user,'robot_outbox','member_user_id',body.get('id'))
  if path=='robot-records-delete' and isinstance(body.get('ids'),list):
   for key in body['ids']:check_resource_member(service,user,'robot_outbox','member_user_id',key)
 if path=='robot-account-save':return service.robot.account_save(body,user,ip)
 if path=='robot-bot-risk':return service.robot.risk_action(body,user,ip)
 if path=='robot-account-bulk':return service.robot.account_bulk(body,user,ip)
 if path=='robot-catalog-save':return service.robot.catalog_save(body,user,ip)
 if path in ('robot-account-control','robot-account-delete','robot-bot-save','robot-bot-control','robot-subscriptions'):
  return service.robot.account_action(path[len('robot-'):],body,user,ip)
 if path=='ai-train':return service.ai.request_train()
 if path=='robot-platform-save':
  if body.get('id') and type(body.get('version'))!=int:raise AuthError('请刷新平台配置后再保存',409,'version_conflict')
  return service.robot.save_platform(body,user,ip)
 if path=='robot-binding-save':return service.robot.save_binding(body,user,ip)
 if path=='robot-binding-control':return service.robot.binding_control(body,user,ip)
 if path=='robot-config':return service.robot.save_config(body,user,ip)
 if path in ('robot-rule-save','robot-rule-control','robot-rule-delete','robot-strategy-control','robot-preview','robot-test'):raise ValueError('消息格式已固定；推送策略与金额请在会员订阅中配置')
 if path=='robot-retry':return service.robot.retry(body.get('id'),user,ip)
 if path=='robot-records-delete':return service.robot.delete_records(body,user,ip)
 with service.lock:
  if path=='invite-create':
   count=body.get('max_uses',1);days=body.get('expires_days',7);note=body.get('note','')
   if type(count)!=int or not 1<=count<=10000 or type(days)!=int or not 1<=days<=365 or not isinstance(note,str) or len(note)>100:raise ValueError('邀请码参数无效')
   code=secrets.token_urlsafe(18);expiry=int(time.time())+days*86400
   with db.tx() as c:
    cur=c.execute('INSERT INTO web_invites(code_hash,note,max_uses,expires,created,created_by) VALUES(?,?,?,?,?,?)',(digest(code),note,count,expiry,utcnow(),user['id']));iid=cur.lastrowid;auth.audit(c,user['id'],'invite_create',iid,ip,{'max_uses':count,'expires':expiry,'note':note})
   return {'ok':True,'id':iid,'code':code,'expires':expiry,'note':'邀请码仅显示这一次，请复制保存'}
  if path=='invite-revoke':
   with db.tx() as c:
    if not c.execute('SELECT 1 FROM web_invites WHERE id=?',(body.get('id'),)).fetchone():raise ValueError('邀请码不存在')
    c.execute('UPDATE web_invites SET active=0 WHERE id=?',(body['id'],));auth.audit(c,user['id'],'invite_revoke',body['id'],ip)
   return {'ok':True}
  if path in ('user-update','user-password'):
   uid=body.get('id')
   with db.tx() as c:
    require_member_scope(c,user,uid)
    target=c.execute('SELECT * FROM web_users WHERE id=?',(uid,)).fetchone()
    if not target:raise ValueError('会员不存在')
    if target['role']!='member':raise AuthError('管理员请使用管理员管理功能',403,'permission_denied')
    if path=='user-password':
     if uid==user['id']:raise ValueError('修改自己的密码请使用右上角账户入口')
     encoded=hash_password(body.get('new_password'));c.execute('UPDATE web_users SET password_hash=? WHERE id=?',(encoded,uid));c.execute('DELETE FROM web_sessions WHERE user_id=?',(uid,));auth.audit(c,user['id'],'user_password_reset',uid,ip)
    else:
     active=body.get('active',bool(target['active']));role=body.get('role',target['role'])
     if type(active)!=bool or role!='member':raise ValueError('账号状态或角色无效')
     if uid==user['id'] and (not active or role!='admin'):raise ValueError('不能停用或降级当前登录管理员')
     if target['role']=='admin' and target['active'] and (not active or role!='admin') and c.execute("SELECT COUNT(*) FROM web_users WHERE role='admin' AND active=1").fetchone()[0]<=1:raise ValueError('至少保留一位已启用管理员')
     c.execute('UPDATE web_users SET active=?,role=? WHERE id=?',(int(active),role,uid))
     if bool(target['active'])!=active or target['role']!=role:c.execute('DELETE FROM web_sessions WHERE user_id=?',(uid,))
     auth.audit(c,user['id'],'user_update',uid,ip,{'active':active,'role':role})
   return {'ok':True}
  if path=='site':
   allowed={'site_name','registration_enabled','collector_enabled','interval_seconds','maintenance_guard_enabled'}
   if set(body)!=allowed:raise ValueError('站点设置字段不完整或存在未知字段')
   if not isinstance(body['site_name'],str) or not 1<=len(body['site_name'].strip())<=40:raise ValueError('站点名称需要1—40个字符')
   for k in ('registration_enabled','collector_enabled','maintenance_guard_enabled'):
    if type(body[k])!=bool:raise ValueError('开关参数必须为布尔值')
   if type(body['interval_seconds'])!=int or not 15<=body['interval_seconds']<=300:raise ValueError('采集间隔需要15—300秒')
   cfg=copy.deepcopy(service.cfg);cfg['collector'].update(enabled=body['collector_enabled'],interval_seconds=body['interval_seconds'],maintenance_guard_enabled=body['maintenance_guard_enabled']);service.update_config(cfg)
   with db.tx() as c:
    c.execute("UPDATE web_auth_settings SET value=? WHERE key='site_name'",(body['site_name'].strip(),));c.execute("UPDATE web_auth_settings SET value=? WHERE key='registration_enabled'",(json.dumps(body['registration_enabled']),));auth.audit(c,user['id'],'site_update','site',ip,body)
   return {'ok':True}
  raise ValueError('未知后台操作')
