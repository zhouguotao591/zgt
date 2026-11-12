import json,secrets,mimetypes,threading,os
from .auth import AuthError
from .portal import portal_html
from pathlib import Path
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from urllib.parse import urlparse,parse_qs
from .service import Service

class LocalServer(ThreadingHTTPServer):
 # Browsers fetch many admin assets in parallel; Windows refuses connections
 # when the default five-slot accept queue fills during a burst.
 request_queue_size=128
 daemon_threads=True
 def __init__(self,service):
  self.service=service;self.token=secrets.token_urlsafe(32);super().__init__((service.cfg['host'],service.cfg['port']),Handler)
class Handler(BaseHTTPRequestHandler):
 def log_message(self,fmt,*args):pass
 def send(self,code,data,ctype='application/json; charset=utf-8'):
  if isinstance(data,(dict,list)):data=json.dumps(data,ensure_ascii=False).encode()
  elif isinstance(data,str):data=data.encode()
  self.send_response(code)
  if getattr(self,'new_cookie',None):self.send_header('Set-Cookie',self.new_cookie)
  self.send_header('Referrer-Policy','same-origin');self.send_header('X-Frame-Options','DENY');self.send_header('Content-Type',ctype);self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; style-src-attr 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'");self.end_headers();self.wfile.write(data)
 def redirect(self,path):
  self.send_response(303);self.send_header('Location',path);self.send_header('Cache-Control','no-store');self.end_headers()
 def identity(self):return self.server.service.auth.session(self.headers.get('Cookie'),self.scope)
 def parsed_path(self):
  p=urlparse(self.path);self.admin_scope=p.path=='/admin' or p.path.startswith('/admin/')
  self.center_scope=p.path=='/member' or p.path.startswith('/member/api/')
  self.scope='admin' if self.admin_scope else 'member_center' if self.center_scope else 'member'
  path=p.path
  if path.startswith('/admin/api/'):path=path[len('/admin'):]
  elif path.startswith('/member/api/'):path=path[len('/member'):]
  elif path.startswith('/admin/assets/'):path='/'+path[len('/admin/assets/'):]
  elif path.startswith('/admin/exports/'):path=path[len('/admin'):]
  return p._replace(path=path)
 def check_host(self):
  origin=self.server.service.cfg.get('deployment',{}).get('public_origin','')
  allowed={f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}'}
  if origin:allowed.add(urlparse(origin).netloc)
  admin_origin=self.server.service.cfg.get('deployment',{}).get('admin_origin','')
  if admin_origin:allowed.add(urlparse(admin_origin).netloc)
  return self.headers.get('Host') in allowed
 def do_GET(self):
  self.new_cookie=None
  if not self.check_host():self.send(403,{'error':'不允许的Host'});return
  try:
   p=self.parsed_path();s=self.server.service
   if p.path=='/favicon.ico':
    self.send(200,(s.root/'web/favicon.ico').read_bytes(),'image/x-icon');return
   if p.path=='/api/health' and not self.admin_scope:
    from .watchdog import root_id
    self.send(200,{'ok':True,'service':'pc28','pid':os.getpid(),'parent_pid':os.getppid(),'root_id':root_id(s.root)});return
   auth=s.auth;session,user=self.identity()
   if user and p.path in ('/','/admin','/member','/api/session'):
    self.new_cookie=auth.renew_session(self.headers.get('Cookie'),session,self.scope)
   if p.path=='/api/session':
    if not session:session,self.new_cookie=auth.guest_session(self.client_address[0],self.scope)
    self.send(200,{'token':session['csrf'],'user':auth.public_user(user),'needs_setup':auth.setup_needed(),'site_name':auth.setting('site_name'),'registration_enabled':auth.setting('registration_enabled')=='true'});return
   if p.path=='/api/auth/captcha':
    if not session:session,self.new_cookie=auth.guest_session(self.client_address[0],self.scope)
    self.send(200,auth.captcha(session,self.client_address[0]));return
   if p.path=='/api/auth/me':auth.require(user);self.send(200,{'user':auth.public_user(user),'site_name':auth.setting('site_name')});return
   if self.scope!='member' and p.path=='/api/auth/mfa/status':
    auth.require(user,self.admin_scope);self.send(200,auth.mfa.status(self.scope,user));return
   if not self.admin_scope and p.path=='/member':
    from .portal import member_center_html
    self.send(200,member_center_html(s,user),'text/html; charset=utf-8');return
   if not self.admin_scope and p.path in ('/member-center.js','/member-center.css'):
    self.send(200,(s.root/'web'/p.path[1:]).read_bytes(),('application/javascript' if p.path.endswith('.js') else 'text/css')+'; charset=utf-8');return
   if self.center_scope and p.path.startswith('/api/'):
    from .member_center import API,read
    resource=p.path[len('/api/'):]
    if resource in API:
     self.send(200,read(s.db,user,resource,{k:v[0] for k,v in parse_qs(p.query).items()}));return
    raise AuthError('接口不存在',404)
   if p.path.startswith('/api/member/'):raise AuthError('接口已迁移至会员中心',410)
   if p.path=='/setup':self.redirect('/admin/setup');return
   if p.path in ('/login','/register'):
    next_view=parse_qs(p.query).get('next',[''])[0];next_view=next_view if next_view in ('algorithm-hall','strategy-center','home','trend','dragon','missing','stats','analysis','ai-analysis') else ''
    self.redirect('/?auth='+p.path[1:]+('&next='+next_view if next_view else ''));return
   if p.path in ('/admin/login','/admin/setup'):
    if p.path=='/admin/setup' and not auth.setup_needed():self.redirect('/admin/login');return
    if p.path=='/admin/login' and auth.setup_needed():self.redirect('/admin/setup');return
    page=(s.root/'web/auth.html').read_text(encoding='utf-8')
    page=page.replace('<script src="/auth.js"', '<script src="/mfa-login.js" defer></script><script src="/auth.js"',1)
    self.send(200,page,'text/html; charset=utf-8');return
   if p.path in ('/auth.js','/auth.css','/mfa-login.js'):
    self.send(200,(s.root/'web'/p.path[1:]).read_bytes(),('application/javascript' if p.path.endswith('.js') else 'text/css')+'; charset=utf-8');return
   public_apis={'/api/status','/api/tools','/api/draws','/api/ai-analysis'}
   member_apis=public_apis|{'/api/algorithms','/api/algorithm-detail','/api/strategy-center'}
   public_assets={'/app.js','/style.css','/tools.js','/tools.css','/trend-strategy.js','/account.js','/portal.css','/algorithm-hall.js','/income-chart.js','/algorithm-hall.css','/strategy-center.js','/strategy-center.css','/member-auth.js','/member-auth.css','/ai-analysis.js','/ai-analysis.css'}
   if p.path=='/admin':
    if not user:self.redirect('/admin/setup' if auth.setup_needed() else '/admin/login');return
    auth.require(user,True);self.send(200,portal_html(s,True,user),'text/html; charset=utf-8');return
   if self.admin_scope:
    auth.require(user,True)
    from .permissions import authorize
    authorize(s,user,'GET',p.path,query={k:v[0] for k,v in parse_qs(p.query).items()})
   elif p.path.startswith('/api/'):
    if p.path not in member_apis:raise AuthError('管理功能请使用独立后台入口',403,'admin_portal_required')
    if p.path not in public_apis:auth.require(user)
   elif p.path.startswith('/exports/') or p.path in ('/strategies.js','/history-backtest.js','/admin.js','/member-admin.js','/admin-bootstrap.js','/access-admin.js','/access-management.js','/profit-curve.js','/robot.js','/robot-management.js','/ai-admin.js'):raise AuthError('管理功能请使用独立后台入口',403,'admin_portal_required')
   elif p.path!='/' and p.path not in public_assets:auth.require(user)
   if p.path=='/':self.send(200,portal_html(s,False,user),'text/html; charset=utf-8');return
   if p.path=='/api/admin/access':
    from .permissions import grants,owner,catalog,PAGES
    self.send(200,{'grants':sorted(grants(auth,user)),'owner':owner(auth,user),'catalog':catalog(),'pages':PAGES});return
   if p.path=='/api/admin/robot-notifications':
    self.send(200,s.notifications.listing());return
   if p.path=='/api/admin/administrators':
    from .permissions import listing
    self.send(200,listing(s,{k:v[0] for k,v in parse_qs(p.query).items()}));return
   if p.path=='/api/admin/permissions':
    from .permissions import grants,protect
    uid=parse_qs(p.query).get('id',['0'])[0];rows=s.db.query("SELECT * FROM web_users WHERE id=? AND role='admin' AND id NOT IN (SELECT user_id FROM web_admin_removed)",(uid,))
    if not rows:raise ValueError('管理员不存在')
    protect(auth,rows[0]);self.send(200,{'grants':sorted(grants(auth,rows[0]))});return
   if p.path.startswith('/api/admin/'):
    from .admin import get
    with s.lock:result=get(s,p.path.split('/')[-1],{k:v[0] for k,v in parse_qs(p.query).items()},user)
    self.send(200,result);return
   if p.path=='/api/simulation-summary':
    from .strategies import catalog
    with s.lock:rows=catalog(s.db,s.cfg)['strategies']
    metrics={'bets','wins','refunds','win_rate','profitable_win_rate','net','investment','roi','max_drawdown','pending_stake','initial_balance','balance','curve'}
    self.send(200,{'strategies':[{**{k:r[k] for k in ('id','name','enabled')},'stats':{k:v for k,v in r['stats'].items() if k in metrics}} for r in rows if r['enabled']]});return
   if p.path=='/api/status':
    result=s.status()
    from .permissions import owner
    if not self.admin_scope or not owner(auth,user):
     for key in ('orders','decisions','tasks','jobs','selections','switches','rounds'):result[key]=[]
     result['account']={}
     if result.get('collection_health'):result['collection_health']={'status':result['collection_health']['status'],'details':{'attempts':[{'error':'上游暂未返回有效数据，程序将自动重试'}]}}
    self.send(200,result);return
   if p.path=='/api/ai-analysis':
    window=parse_qs(p.query).get('window',['20'])[0];self.send(200,s.ai.public(int(window) if window in ('20','50','100') else 20));return
   if p.path=='/api/strategy-center':
    from .strategy_center import listing
    self.send(200,listing(s));return
   if p.path=='/api/algorithms':self.send(200,s.algorithm_hall.listing(user['id']));return
   if p.path=='/api/algorithm-detail':
    q=parse_qs(p.query);key=q.get('id',[''])[0]
    if 'backtest' in q:result=s.algorithm_hall.backtest(key,q['backtest'][0],s.cfg['odds'],q.get('interval',['1h'])[0],cutoff=q.get('cutoff',[None])[0])
    elif 'interval' in q:result=s.algorithm_hall.income(key,q['interval'][0])
    else:result=s.algorithm_hall.detail(key,q.get('page',['1'])[0],q.get('page_size',['20'])[0],window=q.get('window',[None])[0],source=q.get('source',['history'])[0])
    self.send(200,result);return
   if p.path=='/api/tools':self.send(200,s.tools.run({k:v[0] for k,v in parse_qs(p.query).items()}));return
   if p.path=='/api/strategy-profit-curve':
    from .profit_curve import result
    q=parse_qs(p.query);include=q.get('include_backfill',['1'])[0]
    if include not in ('0','1'):raise ValueError('历史补单筛选无效')
    with s.lock:data=result(s.db,q.get('id',[''])[0],include=='1')
    self.send(200,data);return
   if p.path=='/api/strategy-defaults':
    from .follow_strategy import defaults
    q=parse_qs(p.query);kind=q.get('kind',['hourly'])[0];axis=q.get('axis',['size'])[0]
    if kind not in ('hourly','majority','previous','single','algorithm','algorithm_rank') or axis not in ('size','parity'):raise ValueError('策略类型或维度无效')
    self.send(200,defaults(s.cfg,kind,axis));return
   if p.path=='/api/history-backtests':
    from .history_backtest import metadata
    with s.lock:result=metadata(s)
    self.send(200,result);return
   if p.path=='/api/history-backtest-result':
    from .history_backtest import result_page
    q=parse_qs(p.query);self.send(200,result_page(s.db,q.get('id',[''])[0],q.get('page',['1'])[0],q.get('page_size',['20'])[0],q.get('interval',['1h'])[0]));return
   if p.path=='/api/history-backtest-task':
    q=parse_qs(p.query);task=s.tasks.get(q.get('id',[''])[0])
    if not task or task['kind']!='history_backtest':raise ValueError('回测任务不存在，请从已保存结果中查看')
    self.send(200,dict(task));return
   if p.path=='/api/strategies':
    from .strategies import catalog
    with s.lock:result=catalog(s.db,s.cfg,dict(s.tasks))
    self.send(200,result);return
   if p.path=='/api/strategy-orders':
    from .strategies import order_page
    q=parse_qs(p.query)
    with s.lock:result=order_page(s.db,s.cfg,q.get('strategy',['all'])[0],q.get('page',['1'])[0],q.get('page_size',['20'])[0],q.get('active',['1'])==['1'])
    from .permissions import allows
    if not self.admin_scope or not allows(auth,user,'page.strategies'):
     for order in result['orders']:
      order.pop('strategy_json',None);order.pop('reason',None)
    self.send(200,result);return
   if p.path=='/api/trend-orders':
    from .trend_strategy import order_page
    q=parse_qs(p.query);self.send(200,order_page(s.db,q.get('strategy',['all'])[0],q.get('page',['1'])[0],q.get('page_size',['20'])[0],s.cfg['trend_simulation']['enabled_axes'] if q.get('active')==['1'] else None));return
   if p.path=='/api/trend-status':
    from .trend_strategy import status
    self.send(200,status(s.db,s.cfg));return
   if p.path=='/api/config':self.send(200,s.cfg);return
   if p.path=='/api/draws':
    q=parse_qs(p.query);limit=max(1,min(1000,int(q.get('limit',['100'])[0])));before=int(q.get('before',['9223372036854775807'])[0]);self.send(200,[s.display_draw(r) for r in s.db.query('SELECT * FROM draws WHERE id<? ORDER BY id DESC LIMIT ?',(before,limit))]);return
   if p.path=='/api/conflicts':self.send(200,s.db.query('SELECT * FROM conflicts WHERE resolved=0 ORDER BY id DESC LIMIT 100'));return
   if p.path=='/api/equity':self.send(200,s.db.query("SELECT target_draw,pnl,equity FROM (SELECT id,target_draw,pnl,SUM(pnl) OVER(ORDER BY id)/100.0 equity FROM orders WHERE status='settled') ORDER BY id DESC LIMIT 500"));return
   if p.path.startswith('/exports/'):
    name=p.path[len('/exports/'):]
    if Path(name).name!=name or not name or '..' in name:raise ValueError('文件路径错误')
    f=s.root/'data/exports'/name
   else:
    names={'/':'index.html','/app.js':'app.js','/style.css':'style.css','/tools.js':'tools.js','/tools.css':'tools.css','/trend-strategy.js':'trend-strategy.js','/strategies.js':'strategies.js','/history-backtest.js':'history-backtest.js','/admin.js':'admin.js','/member-admin.js':'member-admin.js','/admin-bootstrap.js':'admin-bootstrap.js','/access-admin.js':'access-admin.js','/access-management.js':'access-management.js','/account.js':'account.js','/portal.css':'portal.css','/algorithm-hall.js':'algorithm-hall.js','/income-chart.js':'income-chart.js','/algorithm-hall.css':'algorithm-hall.css','/member-auth.js':'member-auth.js','/member-auth.css':'member-auth.css','/profit-curve.js':'profit-curve.js','/robot.js':'robot.js','/robot-management.js':'robot-management.js','/robot-accounts.css':'robot-accounts.css','/ai-analysis.js':'ai-analysis.js','/ai-analysis.css':'ai-analysis.css','/ai-admin.js':'ai-admin.js','/strategy-center.js':'strategy-center.js','/strategy-center.css':'strategy-center.css'}
    if p.path not in names:self.send(404,{'error':'不存在'});return
    f=s.root/'web'/names[p.path]
   if not f.is_file():self.send(404,{'error':'文件不存在'});return
   self.send(200,f.read_bytes(),(mimetypes.guess_type(f.name)[0] or 'application/octet-stream')+'; charset=utf-8')
  except AuthError as e:self.send(e.status,{'error':str(e),'code':e.code})
  except (ValueError,KeyError) as e:self.send(400,{'error':str(e)})
  except Exception as e:self.send(500,{'error':str(e)})
 def do_POST(self):
  self.new_cookie=None
  port=self.server.server_port;origins={f'http://127.0.0.1:{port}',f'http://localhost:{port}'}
  configured=self.server.service.cfg.get('deployment',{}).get('public_origin','')
  if configured:origins.add(configured)
  admin_origin=self.server.service.cfg.get('deployment',{}).get('admin_origin','')
  if admin_origin:origins.add(admin_origin)
  if not self.check_host() or self.headers.get('Origin') not in origins|{None}:
   self.send(403,{'error':'请求来源校验失败，请使用已配置的站点地址','code':'forbidden_origin'});return
  if self.headers.get('Content-Type','').split(';')[0]!='application/json':self.send(415,{'error':'仅接受application/json'});return
  try:
   length=int(self.headers.get('Content-Length','0'))
   if length<0 or length>1024*1024:raise ValueError('请求过大')
   body=json.loads(self.rfile.read(length) or b'{}');s=self.server.service;p=self.parsed_path().path
   if not isinstance(body,dict):raise ValueError('请求必须为对象')
   if p=='/api/receiver/order-status' and not self.admin_scope and not self.center_scope:
    from .order_confirmation import receive
    self.send(200,receive(s,self.headers.get('Authorization'),body));return
   auth=s.auth;session,user=self.identity();ip=self.client_address[0]
   local_control=p=='/api/shutdown' and not self.center_scope and auth.control_allowed(self.headers.get('X-PC28-Control'))
   if p.startswith('/api/member/'):raise AuthError('接口已迁移至会员中心',410)
   if not local_control:
    auth.csrf(session,self.headers.get('X-PC28-Token'))
    if self.scope!='member' and p in ('/api/auth/mfa/rotate','/api/auth/mfa/regenerate'):
     auth.require(user,self.admin_scope)
     result,self.new_cookie=auth.mfa.self_service(session,user,p.rsplit('/',1)[-1],body,ip)
     self.send(200,result);return
    if self.scope!='member' and p.startswith('/api/auth/mfa/'):
     result,self.new_cookie=auth.mfa.challenge(session,p[len('/api/auth/mfa/'):],body,ip)
     self.send(200,result);return
    if self.center_scope and p not in ('/api/auth/login','/api/auth/logout','/api/auth/password'):
     raise AuthError('接口不存在',404)
    if p not in ('/api/auth/login','/api/auth/register','/api/auth/setup'):
     if self.admin_scope:auth.require(user,True)
     else:
      auth.require(user)
      if p not in ('/api/auth/logout','/api/auth/password','/api/algorithm-favorite'):raise AuthError('管理操作必须在独立后台执行',403,'admin_portal_required')
   if p in ('/api/auth/login','/api/auth/register','/api/auth/setup'):
    result,self.new_cookie=auth.authenticate(p.split('/')[-1],session,body,ip)
    if p=='/api/auth/setup':
     from .permissions import migrate
     migrate(auth)
    self.send(200,result);return
   if p=='/api/algorithm-favorite':self.send(200,s.algorithm_hall.favorite(user['id'],body));return
   if p=='/api/auth/logout':self.new_cookie=auth.logout(session,user,ip);self.send(200,{'ok':True});return
   if p=='/api/auth/password':self.send(200,auth.change_password(user,body,ip));return
   if self.admin_scope and not local_control:
    from .permissions import authorize
    authorize(s,user,'POST',p,body)
   if self.admin_scope and p=='/api/admin/robot-notifications':
    self.send(200,s.notifications.save(body,user,ip));return
   if self.admin_scope and p=='/api/admin/administrator-mfa-unbind':
    self.send(200,auth.mfa.unbind_administrator(user,session,body,ip));return
   if self.admin_scope and p in ('/api/admin/member-mfa-policy','/api/admin/member-mfa-unbind'):
    self.send(200,auth.mfa.manage_member(p.rsplit('/',1)[-1],user,session,body,ip));return
   if p in ('/api/admin/administrator-create','/api/admin/administrator-control','/api/admin/administrator-password','/api/admin/administrator-delete','/api/admin/permissions-save'):
    from .permissions import manage
    self.send(200,manage(s,p.split('/')[-1],body,user,ip));return
   if p.startswith('/api/admin/'):
    from .admin import post
    with s.lock:result=post(s,p.split('/')[-1],body,user,ip)
    self.send(200,result);return
   if user:
    with s.db.tx() as c:auth.audit(c,user['id'],'admin_request',p,ip,{'fields':list(body)})
   if p=='/api/shutdown':
    if not (local_control and self.headers.get('X-PC28-Restart')=='1'):
     (s.root/'data/watchdog.stop').write_text('intentional stop',encoding='utf-8')
    s.stop.set();self.send(200,{'ok':True});threading.Thread(target=self.server.shutdown,daemon=True).start();return
   if p=='/api/resolve-conflict':
    with s.lock:result=s.db.resolve_conflict(int(body['id']),body['choice'],body['reason'])
    self.send(200,result);return
   if p=='/api/strategy-save':
    from .custom_strategy import save
    from .strategies import require_fixed_direction
    require_fixed_direction(body.get('settings'))
    self.send(200,save(s,body));return
   if p=='/api/strategy-delete':
    from .strategy_delete import delete
    self.send(200,delete(s,body));return
   if p=='/api/strategy-control':
    from .strategies import control,require_selected_direction
    if body.get('enabled') is True:require_selected_direction(s,body.get('id'))
    self.send(200,control(s,body));return
   if p=='/api/trend-control':
    import copy
    from .strategies import require_selected_direction
    for key in body['enabled_axes']:require_selected_direction(s,key)
    with s.lock:
     config=copy.deepcopy(s.cfg);config['trend_simulation']['enabled_axes']=body['enabled_axes'];s.update_config(config)
    self.send(200,{'ok':True});return
   if p=='/api/config':s.update_config(body);self.send(200,{'ok':True});return
   if p=='/api/pause':s.pause(bool(body['paused']));self.send(200,{'ok':True});return
   if p=='/api/task':self.send(202,{'task_id':s.submit(body['kind'],body.get('args',{}))});return
   self.send(404,{'error':'不存在'})
  except AuthError as e:self.send(e.status,{'error':str(e),'code':e.code})
  except (ValueError,KeyError,TypeError) as e:self.send(400,{'error':str(e)})
  except Exception as e:self.send(500,{'error':str(e)})

def serve(root):
 s=Service(root);server=LocalServer(s);s.start();print(f'PC28本地模拟平台 http://127.0.0.1:{server.server_port}',flush=True)
 try:server.serve_forever(poll_interval=.5)
 except KeyboardInterrupt:pass
 finally:s.stop.set();server.server_close()
