"""Administrator controls for membership and this site's collector/research service."""
import copy,json,secrets,time
from .auth import AuthError,digest,hash_password
from .model import utcnow

def paging(q):
 page=max(1,int(q.get('page',1)));size=int(q.get('page_size',20))
 if size not in (10,20,50,100):raise ValueError('每页条数须为10、20、50、100')
 return page,size

def page_rows(db,table,columns,q,where='1',args=()):
 page,size=paging(q);total=db.query('SELECT COUNT(*) n FROM '+table+' WHERE '+where,args)[0]['n'];pages=max(1,(total+size-1)//size);page=min(page,pages)
 rows=db.query('SELECT '+columns+' FROM '+table+' WHERE '+where+' ORDER BY id DESC LIMIT ? OFFSET ?',(*args,size,(page-1)*size))
 return {'rows':rows,'total':total,'page':page,'pages':pages,'page_size':size}

def get(service,path,q):
 db=service.db;auth=service.auth
 if path=='ai':return service.ai.admin()
 if path=='robot':
  from .robot_signals import ENDPOINT
  return {'platforms':service.robot.platforms(),'config':service.robot.config(),'endpoint':ENDPOINT,'token_configured':service.robot.secret.exists(),'strategies':service.robot.strategies(),'bindings':service.robot.bindings(),'members':service.robot.members()}
 if path=='robot-records':return service.robot.records(q)
 if path=='overview':
  return {'members':db.query('SELECT COUNT(*) n FROM web_users')[0]['n'],'active_members':db.query('SELECT COUNT(*) n FROM web_users WHERE active=1')[0]['n'],'usable_invites':db.query('SELECT COUNT(*) n FROM web_invites WHERE active=1 AND used<max_uses AND expires>?',(int(time.time()),))[0]['n'],'site':{'site_name':auth.setting('site_name'),'registration_enabled':auth.setting('registration_enabled')=='true','collector_enabled':service.cfg['collector']['enabled'],'interval_seconds':service.cfg['collector']['interval_seconds'],'maintenance_guard_enabled':service.cfg['collector'].get('maintenance_guard_enabled',True)},'count':db.query('SELECT COUNT(*) n FROM draws')[0]['n'],'latest':db.tail(1)[-1]['id'] if db.tail(1) else None,'conflicts':db.query('SELECT COUNT(*) n FROM conflicts WHERE resolved=0')[0]['n'],'gaps':db.gaps(),'tasks':list(service.tasks.values())[-20:],'jobs':db.query('SELECT * FROM jobs ORDER BY id DESC LIMIT 20')}
 if path=='users':
  term=str(q.get('q','')).strip()[:32];return page_rows(db,'web_users','id,username,role,active,created,last_login',q,'username LIKE ?',('%'+term+'%',))
 if path=='invites':return page_rows(db,'web_invites','id,note,max_uses,used,expires,active,created,created_by',q)
 if path=='audit':return page_rows(db,'web_auth_events','id,created,actor,action,target,ip,details',q)
 raise ValueError('未知后台接口')

def post(service,path,body,user,ip):
 db=service.db;auth=service.auth
 if path=='ai-train':return service.ai.request_train()
 if path=='robot-platform-save':return service.robot.save_platform(body,user,ip)
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
    target=c.execute('SELECT * FROM web_users WHERE id=?',(uid,)).fetchone()
    if not target:raise ValueError('会员不存在')
    if path=='user-password':
     if uid==user['id']:raise ValueError('修改自己的密码请使用右上角账户入口')
     encoded=hash_password(body.get('new_password'));c.execute('UPDATE web_users SET password_hash=? WHERE id=?',(encoded,uid));c.execute('DELETE FROM web_sessions WHERE user_id=?',(uid,));auth.audit(c,user['id'],'user_password_reset',uid,ip)
    else:
     active=body.get('active',bool(target['active']));role=body.get('role',target['role'])
     if type(active)!=bool or role not in ('admin','member'):raise ValueError('账号状态或角色无效')
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
