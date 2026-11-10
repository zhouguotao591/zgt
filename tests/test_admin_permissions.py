import unittest,json
import test_auth as fixture
from pc28.permissions import keys,grants,migrate
from pc28.auth import hash_password
from pc28.model import utcnow

class PermissionTests(unittest.TestCase):
 setUp=fixture.AuthTests.setUp
 tearDown=fixture.AuthTests.tearDown
 setup=fixture.AuthTests.setup
 def create(self,name='operator01'):
  code,d=self.admin.call('/api/admin/administrator-create',{'username':name,'password':'operator-password-123'});self.assertEqual(code,200,d);return d['id']
 def login(self,name='operator01'):
  c=fixture.Client(self.url,namespace='/admin');c.session()
  code,d=c.authenticate('login',username=name,password='operator-password-123');self.assertEqual(code,200,d)
  if not hasattr(self,'recovery'):self.recovery={}
  if d['status']=='mfa_verify_required':
   code,d=c.call('/api/auth/mfa/recover',{'code':self.recovery[name]});self.assertEqual(code,200,d)
   c.csrf=d['token']
  code,d=c.complete_enrollment();self.assertEqual(code,200,d)
  self.recovery[name]=d['recovery_codes'][0]
  return c
 def grant(self,uid,values):
  status,d=self.admin.call('/api/admin/permissions-save',{'id':uid,'grants':values});self.assertEqual(status,200,d)
 def test_public_visibility_changes_without_changing_execution_or_algorithm_data(self):
  self.setup()
  member,_=fixture.AuthTests.member(self,fixture.AuthTests.invite(self)['code'])
  def publish(path,key,published):
   code,result=self.admin.call('/api/admin/'+path,{'id':key,'published':published})
   self.assertEqual(code,200,result)
  strategies=self.admin.call('/api/strategies')[1]['strategies']
  strategy=strategies[0]
  publish('strategy-publish',strategy['id'],False)
  current=next(r for r in self.admin.call('/api/strategies')[1]['strategies'] if r['id']==strategy['id'])
  self.assertEqual(current['enabled'],strategy['enabled'])
  self.assertFalse(current['published'])
  self.assertNotIn(strategy['id'],[r['id'] for r in member.call('/api/strategy-center')[1]['strategies']])
  original=member.call('/api/algorithms')[1]['algorithms'][0]
  path='algorithm-detail?id='+original['id']
  detail=member.call('/api/'+path)[1]
  publish('algorithm-publish',original['id'],False)
  self.assertNotIn(original['id'],[r['id'] for r in member.call('/api/algorithms')[1]['algorithms']])
  self.assertNotEqual(member.call('/api/'+path)[0],200)
  self.assertEqual(self.admin.call('/api/admin/'+path)[1],detail)
  publish('algorithm-publish',original['id'],True)
  self.assertEqual(member.call('/api/algorithms')[1]['algorithms'][0],original)
 def test_defaults_readonly_actions_and_session_revocation(self):
  self.setup();uid=self.create();c=self.login()
  for path in ['/api/strategies','/api/admin/users','/api/admin/permissions?id='+str(uid),'/api/config']:
   self.assertEqual(c.call(path)[0],403,path)
  self.assertEqual(c.call('/api/admin/access')[1]['grants'],[])
  self.grant(uid,['page.strategies']);self.assertEqual(c.call('/api/strategies')[0],401)
  c=self.login();self.assertEqual(c.call('/api/strategies')[0],200)
  for path,body in [('/api/strategy-control',{'id':'legacy','enabled':False}),('/api/strategy-save',{'id':'legacy','settings':{}}),('/api/strategy-delete',{'id':'legacy'}),('/api/task',{'kind':'history_backtest','args':{}}),('/api/admin/member-capital',{}),('/api/config',{})]:
   self.assertEqual(c.call(path,body)[0],403,path)
  self.grant(uid,['page.strategies','strategy.control']);c=self.login();self.assertEqual(c.call('/api/strategy-control',{'id':'legacy','enabled':False})[0],200)
 def test_admin_hidden_and_all_alternate_routes_protected(self):
  self.setup()
  with self.s.db.tx() as c:c.execute("INSERT INTO web_users(username,password_hash,role,created) VALUES('admin',?,'admin',?)",(hash_password('protected-password-123'),utcnow()))
  uid=self.s.db.query("SELECT id FROM web_users WHERE username='admin'")[0]['id'];operator=self.create()
  status,d=self.admin.call('/api/admin/administrators');self.assertEqual(status,200);self.assertNotIn('admin',[r['username'] for r in d['rows']])
  self.assertEqual(self.admin.call('/api/admin/users')[1]['total'],0)
  for path,body in [('/api/admin/administrator-control',{'id':uid,'active':False}),('/api/admin/administrator-delete',{'id':uid}),('/api/admin/administrator-password',{'id':uid,'password':'replacement-password'}),('/api/admin/permissions-save',{'id':uid,'grants':[]}),('/api/admin/user-update',{'id':uid,'role':'member','active':False}),('/api/admin/user-password',{'id':uid,'new_password':'replacement-password'})]:
   self.assertEqual(self.admin.call(path,body)[0],403,path)
 def test_delegation_cannot_escalate_and_validates_parent_page(self):
  self.setup();uid=self.create();self.assertEqual(self.admin.call('/api/admin/administrator-create',{'username':'admin','password':'reserved-password-123'})[0],400);self.assertEqual(self.admin.call('/api/admin/permissions-save',{'id':uid,'grants':['member.capital']})[0],400)
  values=['page.admin-permissions','permission.save','page.admin-administrators','administrator.create'];self.grant(uid,values);c=self.login();target=self.create('operator02')
  self.assertEqual(c.call('/api/admin/permissions-save',{'id':target,'grants':['page.admin-users','member.capital']})[0],403)
  self.assertEqual(c.call('/api/admin/permissions-save',{'id':uid,'grants':values})[0],400)
  self.assertEqual(c.call('/api/admin/user-update',{'id':target,'role':'admin'})[0],403)
 def test_filter_buttons_enforced_for_queries(self):
  self.setup();uid=self.create();self.grant(uid,['page.admin-member-orders','page.admin-users']);c=self.login()
  self.assertEqual(c.call('/api/admin/member-orders')[0],200);self.assertEqual(c.call('/api/admin/member-orders?username=test')[0],403);self.assertEqual(c.call('/api/admin/users?q=test')[0],403)
  self.grant(uid,['page.admin-member-orders','button.mf-orders-filter.submit']);c=self.login();self.assertEqual(c.call('/api/admin/member-orders?username=test')[0],200)
 def test_platform_edit_does_not_bypass_separate_control(self):
  self.setup();uid=self.create();self.grant(uid,['page.admin-robot','robot.platform.edit']);c=self.login();platform=self.admin.call('/api/admin/robot')[1]['platforms'][0]
  body={k:platform[k] for k in ('id','name','endpoint','version')};body['enabled']=False
  self.assertEqual(c.call('/api/admin/robot-platform-save',body)[0],403)
  self.grant(uid,['page.admin-robot','robot.platform.control']);c=self.login();self.assertEqual(c.call('/api/admin/robot-platform-save',body)[0],200)
 def test_readonly_pages_have_no_cross_page_access(self):
  self.setup();uid=self.create();self.grant(uid,['page.simulation']);c=self.login()
  self.assertEqual(c.call('/api/simulation-summary')[0],200);self.assertEqual(c.call('/api/strategies')[0],403);self.assertEqual(c.call('/api/config')[0],403);self.assertEqual(c.call('/api/strategy-profit-curve?id=legacy')[0],403)
  self.assertEqual(c.call('/api/admin/robot')[0],403);self.assertEqual(c.call('/api/status')[1]['account'],{})
 def test_account_actions_require_individual_permissions_and_versions(self):
  self.setup_username='admin';self.setup();uid=self.create()
  with self.s.db.tx() as db:member=db.execute("INSERT INTO web_users(username,password_hash,role,created) VALUES('account_fixture','unused','member',?)",(utcnow(),)).lastrowid
  body={'user_id':member,'platform_id':1,'external_account_id':789,'name':'测试账号','enabled':False,'bots':[{'name':'综合机器人','external_bot_key':1,'signal_type':'BUY_SYSTEM','enabled':True}],'subscriptions':[{'strategy_id':'size','bot_index':0,'amount_mode':'fixed','fixed_cents':10000}]}
  self.grant(uid,['page.admin-robot']);c=self.login()
  self.assertEqual(c.call('/api/admin/robot-accounts')[0],200)
  self.assertEqual(c.call('/api/admin/robot-account-save',body)[0],403)
  status,result=self.admin.call('/api/admin/robot-account-save',body);self.assertEqual(status,200,result);aid=result['id']
  self.assertEqual(self.admin.call('/api/admin/administrator-members',{'id':uid,'add':[member],'remove':[]})[0],200)
  self.grant(uid,['page.admin-robot','robot.account.edit']);c=self.login();a=c.call('/api/admin/robot-account?id='+str(aid))[1]
  update={'id':aid,'version':a['version'],'name':'改名','enabled':False}
  self.assertEqual(c.call('/api/admin/robot-account-save',{**update,'enabled':True})[0],403)
  self.assertEqual(c.call('/api/admin/robot-account-control',{'id':aid,'version':a['version'],'enabled':True})[0],403)
  self.assertEqual(c.call('/api/admin/robot-account-save',update)[0],200)
  self.assertEqual(c.call('/api/admin/robot-account-save',update)[0],409)
  self.assertEqual(c.call('/api/admin/robot-account-save',{'id':aid,'name':'无版本','enabled':False})[0],409)
  self.assertEqual(c.call('/api/admin/robot-subscriptions',{'id':aid,'version':a['version'],'subscriptions':[]})[0],403)
  self.assertEqual(c.call('/api/admin/robot-account-bulk',{'operation':'delete','items':[{'id':aid,'version':a['version']}]})[0],403)
 def test_member_manager_assignment_is_root_only_and_scopes_access(self):
  self.setup_username='admin';self.setup()
  first=self.create();second=self.create('operator02')
  permissions=['page.admin-users','member.edit','member.capital','member.ledger','page.admin-member-orders','page.admin-robot','page.admin-overview']
  self.grant(first,permissions);self.grant(second,permissions)
  a=self.login();b=self.login('operator02')
  uid=self.admin.call('/api/admin/member-create',{'username':'assigned_member','password':'fixture-password-123'})[1]['id']
  assignment={'id':first,'add':[uid],'remove':[]}
  self.assertEqual(a.call('/api/admin/administrator-members',assignment)[0],403)
  self.assertEqual(a.call('/api/admin/administrator-members?id='+str(first))[0],403)
  self.assertEqual(a.call('/api/admin/users')[1]['total'],0)
  self.assertEqual(self.admin.call('/api/admin/administrator-members',assignment)[0],200)
  self.assertEqual(a.call('/api/admin/users')[1]['rows'][0]['id'],uid)
  self.assertEqual(b.call('/api/admin/users')[1]['total'],0)
  self.assertEqual(a.call('/api/admin/overview')[1]['members'],1)
  self.assertEqual(b.call('/api/admin/overview')[1]['members'],0)
  self.assertEqual(a.call('/api/admin/robot')[1]['members'][0]['id'],uid)
  self.assertEqual(b.call('/api/admin/robot')[1]['members'],[])
  with self.s.db.tx() as db:
   db.execute("INSERT INTO member_orders(user_id,outbox_id,target_draw,direction,stake,placed_at,accepted_at,odds_json) VALUES(?,9000,999,'大',100,?,?,'{}')",(uid,utcnow(),utcnow()))
  self.assertEqual(a.call('/api/admin/member-orders')[1]['total'],1)
  self.assertEqual(b.call('/api/admin/member-orders')[1]['total'],0)
  change={'id':uid,'amount':'100','mode':'add','note':'test','request_id':'manager-test-123'}
  self.assertEqual(b.call('/api/admin/member-capital',change)[0],403)
  self.assertEqual(a.call('/api/admin/member-capital',change)[0],200)
  self.assertEqual(a.call('/api/admin/member-ledger?user_id='+str(uid))[1]['total'],1)
  self.assertEqual(b.call('/api/admin/member-ledger?user_id='+str(uid))[1]['total'],0)
  self.assertEqual(self.admin.call('/api/admin/administrator-members',{'id':second,'add':[uid],'remove':[]})[0],200)
  self.assertEqual(a.call('/api/admin/users')[1]['total'],1)
  self.assertEqual(self.admin.call('/api/admin/administrator-members',{'id':first,'add':[],'remove':[uid]})[0],200)
  self.assertEqual(a.call('/api/admin/user-update',{'id':uid,'active':False})[0],403)
  self.assertEqual(b.call('/api/admin/users')[1]['rows'][0]['id'],uid)
 def test_creator_and_inviter_receive_default_access(self):
  self.setup_username='admin';self.setup()
  first=self.create();second=self.create('operator02')
  permissions=['page.admin-users','member.create','page.admin-invites','invite.create']
  self.grant(first,permissions);self.grant(second,permissions)
  a=self.login();b=self.login('operator02')
  result=a.call('/api/admin/member-create',{'username':'created_by_operator','password':'fixture-password-123'})
  self.assertEqual(result[0],200)
  result=a.call('/api/admin/invite-create',{'max_uses':1,'expires_days':1,'note':'operator invite'})
  self.assertEqual(result[0],200)
  fixture.AuthTests.member(self,result[1]['code'],'invited_by_operator')
  self.assertEqual({r['username'] for r in a.call('/api/admin/users')[1]['rows']},{'created_by_operator','invited_by_operator'})
  self.assertEqual(b.call('/api/admin/users')[1]['total'],0)
  self.assertEqual(self.admin.call('/api/admin/users')[1]['total'],2)
  self.assertEqual(b.call('/api/admin/invites')[1]['total'],0)
 def test_disable_password_delete_audit_and_restart_preserves_grants(self):
  self.setup();uid=self.create();c=self.login();self.grant(uid,['page.admin-users']);migrate(self.s.auth);self.assertEqual(json.loads(self.s.db.query('SELECT grants FROM web_admin_permissions WHERE user_id=?',(uid,))[0]['grants']),['page.admin-users'])
  c=self.login();self.assertEqual(self.admin.call('/api/admin/administrator-control',{'id':uid,'active':False})[0],200);self.assertEqual(c.call('/api/admin/users')[0],401)
  self.assertEqual(self.admin.call('/api/admin/administrator-control',{'id':uid,'active':True})[0],200);c=self.login()
  self.assertEqual(self.admin.call('/api/admin/administrator-password',{'id':uid,'password':'new-operator-password'})[0],200);self.assertEqual(c.call('/api/admin/users')[0],401)
  self.assertEqual(self.admin.call('/api/admin/administrator-delete',{'id':uid})[0],200);r=self.s.db.query('SELECT role,active FROM web_users WHERE id=?',(uid,))[0];self.assertEqual(r,{'role':'admin','active':0})
  events=self.s.db.query('SELECT action,details FROM web_auth_events WHERE target=?',(str(uid),));self.assertTrue(any(r['action']=='permissions_save' for r in events));self.assertNotIn('new-operator-password',str(events))

if __name__=='__main__':unittest.main()
