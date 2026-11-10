import base64,unittest,tempfile,copy,json,threading,http.cookiejar,urllib.request,urllib.error,time,shutil
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from pathlib import Path
from pc28.config import DEFAULT
from pc28.service import Service
from pc28.server import LocalServer
from pc28.auth import hash_password,check_password
from pc28.mfa import code_at

class Client:
 def __init__(self,url,namespace=''):self.url=url;self.namespace=namespace;self.jar=http.cookiejar.CookieJar();self.http=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar));self.csrf=''
 def call(self,path,body=None,headers=None):
  if path.startswith('/api/'):path=self.namespace+path
  h={'Content-Type':'application/json','X-PC28-Token':self.csrf};h.update(headers or {})
  req=urllib.request.Request(self.url+path,data=json.dumps(body).encode() if body is not None else None,headers=h)
  try:
   with self.http.open(req) as r:
    raw=r.read();return r.status,json.loads(raw) if 'json' in r.headers['Content-Type'] else raw.decode()
  except urllib.error.HTTPError as e:
   raw=e.read();return e.code,json.loads(raw)
 def session(self):
  status,d=self.call('/api/session');self.csrf=d['token'];return d
 def captcha(self):
  with patch('pc28.auth.secrets.randbelow',return_value=1):status,d=self.call('/api/auth/captcha')
  assert status==200,d
  return {'captcha_id':d['id'],'captcha':'11111'}
 def authenticate(self,kind,**body):
  body.update(self.captcha());status,d=self.call('/api/auth/'+kind,body)
  if status==200:self.csrf=d['token']
  return status,d
 def complete_enrollment(self):
  status,data=self.call('/api/auth/mfa/start',{})
  assert status==200,data
  self.totp_secret=base64.b32decode(data['key'])
  self.enrollment_code=self.totp()
  status,data=self.call('/api/auth/mfa/confirm',{'code':self.enrollment_code})
  if status==200:self.csrf=data['token']
  return status,data
 def totp(self,offset=0):
  return code_at(self.totp_secret,int(time.time())//30+offset)

class AuthTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent/'data')
  self.root=Path(self.tmp.name)
  cfg=copy.deepcopy(DEFAULT);cfg['collector']['enabled']=False
  (self.root/'config.json').write_text(json.dumps(cfg))
  self.s=Service(self.root)
  self.s.cfg['port']=0
  self.server=LocalServer(self.s)
  self.s.cfg['port']=self.server.server_port
  self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
  self.url=f'http://127.0.0.1:{self.server.server_port}'
  self.admin=Client(self.url,namespace='/admin');self.admin.session()
  shutil.copytree(Path(__file__).resolve().parent.parent/'web',self.root/'web')
  self.s.auth.mfa.key_path.parent.mkdir(parents=True,exist_ok=True)
  self.s.auth.mfa.key_path.write_bytes(bytes(32))
 def tearDown(self):self.server.shutdown();self.server.server_close();self.tmp.cleanup()
 def setup(self):
  code=(self.root/'data/admin-setup-code.txt').read_text();status,d=self.admin.authenticate('setup',username=getattr(self,'setup_username','siteadmin'),password='admin-password-123',setup_code=code);self.assertEqual(status,200,d)
  self.assertEqual(d['status'],'mfa_enroll_required')
  status,d=self.admin.complete_enrollment();self.assertEqual(status,200,d);return d
 def invite(self,max_uses=1):
  status,d=self.admin.call('/api/admin/invite-create',{'max_uses':max_uses,'expires_days':1,'note':'test'});self.assertEqual(status,200,d);return d
 def member(self,code,name='member01'):
  client=Client(self.url);client.session();status,d=client.authenticate('register',username=name,password='member-password-123',invite_code=code);self.assertEqual(status,200,d);return client,d
 def test_setup_requires_local_secret_and_no_bootstrap_repeat(self):
  status,_=self.admin.authenticate('setup',username='siteadmin',password='admin-password-123',setup_code='wrong');self.assertEqual(status,400);d=self.setup();self.assertEqual(d['user']['role'],'admin')
  status,_=self.admin.authenticate('setup',username='otheradmin',password='other-password-123',setup_code=(self.root/'data/admin-setup-code.txt').read_text());self.assertEqual(status,409)
  self.assertEqual(self.admin.call('/api/admin/overview')[0],200)
 def test_login_cookies_renew_in_each_scope_and_expired_session_stays_expired(self):
  from pc28.auth import LOGIN_SESSION_SECONDS,digest
  self.setup()
  public,_=self.member(self.invite()['code'])
  center=Client(self.url,'/member')
  center.session()
  self.assertEqual(center.authenticate('login',username='member01',password='member-password-123')[0],200)
  for client,scope,path in ((public,'member','/'),(self.admin,'admin','/admin'),(center,'member_center','/member')):
   with self.subTest(scope=scope):
    cookie=next(iter(client.jar))
    self.assertEqual(cookie.path,path)
    self.assertTrue(cookie.has_nonstandard_attr('HttpOnly'))
    self.assertAlmostEqual(cookie.expires,time.time()+LOGIN_SESSION_SECONDS,delta=5)
    key=digest(cookie.value)
    with self.s.db.tx() as c:
     c.execute('UPDATE web_sessions SET expires=? WHERE token_hash=?',(int(time.time())+100,key))
    self.assertIsNotNone(client.session()['user'])
    expires=self.s.db.query('SELECT expires FROM web_sessions WHERE token_hash=?',(key,))[0]['expires']
    self.assertAlmostEqual(expires,time.time()+LOGIN_SESSION_SECONDS,delta=5)
    self.assertEqual(next(iter(client.jar)).value,cookie.value)
    self.assertEqual(next(iter(client.jar)).expires,expires)
    with self.s.db.tx() as c:c.execute('UPDATE web_sessions SET expires=0 WHERE token_hash=?',(key,))
    self.assertIsNone(client.session()['user'])
    guest=next(iter(client.jar))
    self.assertNotEqual(guest.value,cookie.value)
    self.assertAlmostEqual(guest.expires,time.time()+1800,delta=5)
 def test_mfa_member_policy_unbind_and_rebind(self):
  self.setup()
  public,account=self.member(self.invite()['code'])
  uid=account['user']['id']
  center=Client(self.url,'/member')
  credentials={'username':'member01','password':'member-password-123'}
  center.session()
  self.assertEqual(center.authenticate('login',**credentials)[1]['user']['id'],uid)
  self.assertEqual(public.call('/member/api/dashboard')[0],401)
  self.assertEqual(public.call('/api/member/dashboard')[0],410)
  self.assertEqual(center.call('/api/dashboard')[0],200)
  policy='/api/admin/member-mfa-policy'
  self.assertEqual(self.admin.call(policy,{'id':uid,'enabled':True})[0],200)
  self.assertEqual(center.call('/api/dashboard')[0],401)
  self.assertEqual(public.session()['user']['id'],uid)
  center.session()
  self.assertEqual(center.authenticate('login',**credentials)[1]['status'],'mfa_enroll_required')
  self.assertIsNone(center.session()['user'])
  self.assertEqual(center.call('/api/dashboard')[0],401)
  status,bound=center.complete_enrollment()
  self.assertEqual(status,200,bound)
  self.assertEqual(len(bound['recovery_codes']),10)
  old_secret=center.totp_secret
  self.assertEqual(center.call('/api/auth/mfa/status')[1],{'bound':True,'required':True})
  self.assertEqual(self.admin.call(policy,{'id':uid,'enabled':False})[0],200)
  center.session()
  self.assertEqual(center.authenticate('login',**credentials)[1]['user']['id'],uid)
  self.assertEqual(center.call('/api/auth/mfa/status')[1],{'bound':True,'required':False})
  self.assertEqual(self.admin.call(policy,{'id':uid,'enabled':True})[0],200)
  center.session()
  self.assertEqual(center.authenticate('login',**credentials)[1]['status'],'mfa_verify_required')
  self.assertEqual(center.call('/api/auth/mfa/verify',{'code':center.enrollment_code})[0],401)
  unbind='/api/admin/member-mfa-unbind'
  self.assertEqual(self.admin.call(unbind,{'id':uid,'code':'123','reason':'测试换机'})[0],401)
  self.assertEqual(self.admin.call(unbind,{'id':uid,'code':self.admin.totp(1),'reason':'测试换机'})[0],200)
  self.assertEqual(center.call('/api/auth/mfa/recover',{'code':bound['recovery_codes'][0]})[0],403)
  center.session()
  self.assertEqual(center.authenticate('login',**credentials)[1]['status'],'mfa_enroll_required')
  self.assertEqual(center.complete_enrollment()[0],200)
  self.assertNotEqual(center.totp_secret,old_secret)
  self.assertEqual(center.call('/api/dashboard')[0],200)

 def test_admin_login_recovery_and_revoked_challenge(self):
  initial=self.setup()
  admin=Client(self.url,'/admin')
  credentials={'username':'siteadmin','password':'admin-password-123'}
  admin.session()
  self.assertEqual(admin.authenticate('login',**credentials)[1]['status'],'mfa_verify_required')
  self.assertIsNone(admin.session()['user'])
  self.assertEqual(admin.call('/api/admin/overview')[0],401)
  self.assertEqual(admin.call('/api/auth/mfa/verify',{'code':self.admin.enrollment_code})[0],401)
  status,recovery=admin.call('/api/auth/mfa/recover',{'code':initial['recovery_codes'][0]})
  self.assertEqual(status,200,recovery)
  self.assertEqual(recovery['status'],'mfa_rotate_required')
  admin.csrf=recovery['token']
  self.assertEqual(admin.call('/api/admin/overview')[0],401)
  self.assertEqual(admin.complete_enrollment()[0],200)
  self.assertEqual(admin.call('/api/admin/overview')[0],200)
  self.assertEqual(self.admin.call('/api/admin/overview')[0],401)
  clients=[Client(self.url,'/admin') for _ in range(2)]
  for client in clients:
   client.session()
   self.assertEqual(client.authenticate('login',**credentials)[1]['status'],'mfa_verify_required')
  next_code=admin.totp(1)
  with ThreadPoolExecutor(max_workers=2) as pool:
   statuses=list(pool.map(lambda client:client.call('/api/auth/mfa/verify',{'code':next_code})[0],clients))
  self.assertEqual(sorted(statuses),[200,401])
 def test_robot_admin_only_and_secret_never_returned(self):
  self.setup();guest=Client(self.url);guest.session();self.assertEqual(guest.call('/api/admin/robot')[0],403);self.assertEqual(guest.call('/admin/api/admin/robot')[0],401);self.assertEqual(guest.call('/robot.js')[0],403)
  status,d=self.admin.call('/api/admin/robot');self.assertEqual(status,200);self.assertFalse(d['config']['enabled']);self.assertNotIn('rules',d);self.assertNotIn('defaults',d);self.assertNotIn('token',d)
  status,page=self.admin.call('/admin');self.assertEqual(status,200);self.assertIn('robot-bindings',page);self.assertNotIn('id="robot-strategies"',page);self.assertNotIn('id="robot-rules"',page);self.assertIn('/admin/assets/robot.js',page)
 def test_ai_public_statistics_admin_training_only(self):
  self.setup();guest=Client(self.url);status,d=guest.call('/api/ai-analysis');self.assertEqual(status,200);self.assertEqual([r['axis'] for r in d['cards']],['size','parity']);self.assertIsNone(d['cards'][0]['probability']);self.assertNotIn('artifact',json.dumps(d));self.assertEqual(guest.call('/api/admin/ai-train',{})[0],403);self.assertIn(guest.call('/admin/api/admin/ai-train',{})[0],(401,403))
  status,page=guest.call('/');self.assertIn('data-tab="ai-analysis"',page);self.assertIn('/ai-analysis.js',page);self.assertEqual(guest.call('/ai-admin.js')[0],403);self.assertEqual(self.admin.call('/api/admin/ai')[0],200)
 def test_strategy_center_member_projection_hides_parameters(self):
  self.setup();guest=Client(self.url);self.assertEqual(guest.call('/api/strategy-center')[0],401)
  member,_=self.member(self.invite()['code']);status,d=member.call('/api/strategy-center');self.assertEqual(status,200);self.assertGreater(len(d['strategies']),0)
  self.assertEqual(set(d['strategies'][0]),{'id','name','enabled','state','base_stake','stats'})
  for row in d['strategies']:
   self.assertNotIn('settings',row);self.assertNotIn('rules',row);self.assertNotIn('curve',row['stats'])
  from pc28.strategies import catalog
  rows=catalog(self.s.db,self.s.cfg)['strategies'];row=rows[0];summary={'settings':row['settings'],'window_returns':{'one_month':{'net':123.45,'bets':42,'coverage':{'requested_start':'2026-10-01T00:00:00+00:00','requested_end':'2026-11-01T00:00:00+00:00','secret_internal_field':'do-not-expose'}}}}
  with self.s.db.tx() as c:c.execute('INSERT INTO strategy_backtest_summary(strategy,run_id,created,summary) VALUES(?,?,?,?)',(row['id'],'test-run','2026-11-01T00:00:00+00:00',json.dumps(summary)))
  status,d=member.call('/api/strategy-center');self.assertEqual(status,200);self.assertNotIn('month_backtest',json.dumps(d));self.assertNotIn('secret_internal_field',json.dumps(d))
  status,page=guest.call('/');self.assertEqual(status,200);self.assertLess(page.index('data-tab="algorithm-hall"'),page.index('data-tab="strategy-center"'));self.assertIn('/strategy-center.js',page)
  self.assertEqual(member.call('/api/strategies')[0],403);self.assertEqual(member.call('/api/strategy-save',{})[0],403)
 def test_captcha_binding_expiry_reuse_and_no_text_answer(self):
  self.setup();guest=Client(self.url);guest.session();cap=guest.captcha();other=Client(self.url);other.session()
  body={'username':'siteadmin','password':'admin-password-123',**cap}
  self.assertEqual(other.call('/api/auth/login',body)[0],400)
  status,d=guest.call('/api/auth/login',body);self.assertEqual(status,200,d)
  self.assertEqual(guest.call('/api/auth/login',body)[0],403)
  guest.session();cap=guest.captcha()
  with self.s.db.tx() as c:c.execute('UPDATE web_captchas SET expires=0 WHERE id=?',(cap['captcha_id'],))
  self.assertEqual(guest.call('/api/auth/login',{'username':'siteadmin','password':'admin-password-123',**cap})[0],400)
  with patch('pc28.auth.secrets.randbelow',return_value=1):status,d=guest.call('/api/auth/captcha')
  self.assertNotIn('answer',d);self.assertTrue(d['image'].startswith('data:image/png;base64,'))
 def test_invite_limit_revocation_expiry_and_role_injection(self):
  self.setup();inv=self.invite();member,d=self.member(inv['code']);self.assertEqual(d['user']['role'],'member')
  guest=Client(self.url);guest.session();status,_=guest.authenticate('register',username='member02',password='member-password-123',invite_code=inv['code'],role='admin');self.assertEqual(status,400)
  inv2=self.invite();self.admin.call('/api/admin/invite-revoke',{'id':inv2['id']});status,_=guest.authenticate('register',username='member03',password='member-password-123',invite_code=inv2['code']);self.assertEqual(status,400)
  inv3=self.invite()
  with self.s.db.tx() as c:c.execute('UPDATE web_invites SET expires=0 WHERE id=?',(inv3['id'],))
  status,_=guest.authenticate('register',username='member04',password='member-password-123',invite_code=inv3['code']);self.assertEqual(status,400)
  listing=self.admin.call('/api/admin/invites')[1];self.assertNotIn('code_hash',str(listing));self.assertNotIn(inv['code'],str(listing))
 def test_member_api_rbac_and_unauth_denial(self):
  self.setup();guest=Client(self.url);guest.session();self.assertEqual(guest.call('/api/status')[0],200);member,_=self.member(self.invite()['code'])
  for path in ('/api/config','/api/strategies','/api/history-backtests','/api/history-backtest-result?id=x','/api/admin/users','/exports/notfound.csv','/strategies.js'):
   self.assertEqual(member.call(path)[0],403,path)
  for path,body in [('/api/strategy-control',{'id':'legacy','enabled':True}),('/api/task',{'kind':'collect'}),('/api/config',{}),('/api/shutdown',{}),('/api/admin/invite-create',{})]:self.assertEqual(member.call(path,body)[0],403,path)
  for path in ('/api/status','/api/tools?view=trend','/api/algorithms','/api/draws'):
   self.assertEqual(member.call(path)[0],200,path)
  status,d=member.call('/api/status');self.assertEqual(d['jobs'],[]);self.assertNotIn('state',d['account'])
 def test_disable_reset_logout_and_per_session_csrf(self):
  self.setup();member,d=self.member(self.invite()['code']);uid=d['user']['id']
  self.assertEqual(member.call('/api/auth/logout',{},headers={'X-PC28-Token':self.admin.csrf})[0],403)
  self.assertEqual(self.admin.call('/api/admin/user-update',{'id':uid,'active':False})[0],200);self.assertEqual(member.call('/api/algorithms')[0],401)
  self.admin.call('/api/admin/user-update',{'id':uid,'active':True});member.session();self.assertEqual(member.authenticate('login',username='member01',password='member-password-123')[0],200)
  self.admin.call('/api/admin/user-password',{'id':uid,'new_password':'new-password-456'});self.assertEqual(member.call('/api/algorithms')[0],401);member.session();self.assertEqual(member.authenticate('login',username='member01',password='new-password-456')[0],200)
  self.assertEqual(member.call('/api/auth/logout',{})[0],200);self.assertEqual(member.call('/api/algorithms')[0],401)
  self.assertEqual(self.admin.call('/api/admin/user-update',{'id':1,'active':False})[0],403)
 def test_registration_switch_and_password_hash(self):
  self.setup();status,d=self.admin.call('/api/admin/overview');site=d['site'];site['registration_enabled']=False;self.assertEqual(self.admin.call('/api/admin/site',site)[0],200)
  guest=Client(self.url);guest.session();self.assertEqual(guest.authenticate('register',username='member01',password='member-password-123',invite_code=self.invite()['code'])[0],403)
  row=self.s.db.query('SELECT password_hash FROM web_users')[0];self.assertNotIn('admin-password-123',row['password_hash']);self.assertTrue(check_password('admin-password-123',row['password_hash']))
 def test_local_control_only_shutdown_and_login_throttle(self):
  self.setup();guest=Client(self.url);guest.session();control=(self.root/'data/local-control-token.txt').read_text()
  self.assertEqual(guest.call('/api/strategy-control',{'id':'legacy','enabled':True},headers={'X-PC28-Control':control})[0],401)
  for i in range(10):self.assertEqual(guest.authenticate('login',username='siteadmin',password='wrong-password')[0],401)
  self.assertEqual(guest.authenticate('login',username='siteadmin',password='admin-password-123')[0],429)

 def test_member_algorithm_hall_favorites_authorization(self):
  self.setup();member,d=self.member(self.invite()['code']);status,listing=member.call('/api/algorithms');self.assertEqual(status,200);self.assertEqual(len(listing['algorithms']),28)
  body={'id':'size.follow.1','favorite':True,'user_id':1};self.assertEqual(member.call('/api/algorithm-favorite',body)[0],200)
  self.assertEqual(self.s.db.query('SELECT user_id FROM algorithm_favorites')[0]['user_id'],d['user']['id'])
  self.assertEqual(member.call('/api/algorithm-detail?id=size.follow.1')[0],200)
  self.assertEqual(member.call('/api/strategy-control',{'id':'legacy','enabled':True})[0],403)

 def test_public_frontend_and_independent_admin_scope(self):
  self.setup();guest=Client(self.url)
  status,html=guest.call('/');self.assertEqual(status,200);self.assertNotIn('href="/admin"',html)
  for endpoint in ('/api/status','/api/tools?view=trend','/api/draws'):
   self.assertEqual(guest.call(endpoint)[0],200)
  self.assertEqual(guest.call('/api/algorithms')[0],401)
  status,admin_html=self.admin.call('/admin');self.assertEqual(status,200)
  self.assertNotIn('data-tab="home"',admin_html);self.assertNotIn('id="trend"',admin_html);self.assertIn('/admin/assets/admin.js',admin_html)
  guest.session();self.assertEqual(guest.authenticate('login',username='siteadmin',password='admin-password-123')[0],200)
  self.assertEqual(guest.call('/api/config')[0],403);self.assertEqual(guest.call('/api/strategy-control',{'id':'legacy','enabled':True})[0],403)
  # An admin browser's path-limited cookie does not log it into the front member area.
  raw_admin=Client(self.url);raw_admin.jar=self.admin.jar;raw_admin.http=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(raw_admin.jar))
  self.assertEqual(raw_admin.call('/api/algorithms')[0],401)
  self.assertEqual(self.admin.call('/api/config')[0],200)
  self.admin.call('/api/auth/logout',{});self.assertEqual(guest.call('/api/algorithms')[0],200)

 def test_front_auth_modal_and_simulation_only_in_admin(self):
  self.setup();guest=Client(self.url);status,html=guest.call('/')
  self.assertEqual(status,200);self.assertIn('id="member-auth-dialog"',html);self.assertIn('data-open-auth="login"',html);self.assertNotIn('data-tab="simulation"',html);self.assertNotIn('id="simulation"',html);self.assertNotIn('src="/trend-strategy.js"',html)
  for route in ('/login','/register'):
   status,html=guest.call(route);self.assertEqual(status,200);self.assertIn('id="member-auth-dialog"',html)
  member,_=self.member(self.invite()['code'])
  for route in ('/api/simulation-summary','/api/strategy-orders','/api/equity'):
   self.assertEqual(member.call(route)[0],403)
  self.assertEqual(member.call('/api/status')[1]['account'],{})
  status,admin_html=self.admin.call('/admin');self.assertEqual(status,200);self.assertIn('data-tab="simulation"',admin_html);self.assertIn('id="simulation"',admin_html);self.assertNotIn('id="member-auth-dialog"',admin_html)
  for route in ('/api/simulation-summary','/api/strategy-orders','/api/equity'):self.assertEqual(self.admin.call(route)[0],200)

 def test_previous_strategy_and_profit_endpoint_admin_only(self):
  self.setup();status,settings=self.admin.call('/api/strategy-defaults?kind=previous');self.assertEqual(status,200);self.assertEqual(settings['strategy_type'],'previous')
  settings['direction']='大'
  status,r=self.admin.call('/api/strategy-save',{'name':'新上期跟随','settings':settings});self.assertEqual(status,200,r)
  status,curve=self.admin.call('/api/strategy-profit-curve?id='+r['id']);self.assertEqual(status,200);self.assertEqual(curve['orders'],0);self.assertEqual(curve['net'],0)
  member,_=self.member(self.invite()['code']);self.assertEqual(member.call('/api/strategy-profit-curve?id='+r['id'])[0],403)
  status,html=self.admin.call('/admin');self.assertNotIn('<summary>管理后台</summary>',html);self.assertIn('admin-nav-group',html);self.assertIn('strategy-add-previous',html)
