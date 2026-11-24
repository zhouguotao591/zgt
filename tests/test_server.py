import unittest,tempfile,copy,threading,json,urllib.request,urllib.error
from pathlib import Path
from pc28.config import DEFAULT
from pc28.service import Service
from pc28.server import LocalServer
class ServerTests(unittest.TestCase):
 def test_ball_follow_save_and_enable_http(self):
  from pc28.follow_strategy import defaults
  for axis in 'abc':
   body={'name':'上期'+axis,'settings':defaults(self.s.cfg,'previous',axis)}
   req=urllib.request.Request(self.url+'/api/strategy-save',data=json.dumps(body).encode(),headers={'Content-Type':'application/json','X-PC28-Token':self.server.token})
   result=json.load(urllib.request.urlopen(req));self.assertIn('id',result)
   req=urllib.request.Request(self.url+'/api/strategy-control',data=json.dumps({'id':result['id'],'enabled':True}).encode(),headers={'Content-Type':'application/json','X-PC28-Token':self.server.token})
   self.assertTrue(json.load(urllib.request.urlopen(req))['ok'])

 def test_public_home_fast_parts_and_lightweight_status(self):
  from unittest.mock import patch
  base='http://127.0.0.1:18028'
  with patch.object(self.s.tools,'snapshot',side_effect=AssertionError('full history snapshot forbidden')):
   records=json.load(urllib.request.urlopen(base+'/api/tools?view=home&part=records'))
   self.assertEqual(records['rows'],[]);self.assertNotIn('summaries',records)
   summary=json.load(urllib.request.urlopen(base+'/api/tools?view=home&part=summary'))
   self.assertIn('loading',summary)
   import time
   while self.s.tools.home_data.running:time.sleep(.01)
  with patch.object(self.s.db,'gaps',side_effect=AssertionError('full gaps forbidden')):
   status=json.load(urllib.request.urlopen(base+'/api/status'))
   self.assertEqual(status['count'],0);self.assertIn('next_draw',status)

 def test_strategy_phase_page_and_admin_route(self):
  import shutil
  from pc28.portal import strip_section
  shutil.copytree(Path(__file__).resolve().parent.parent/'web',self.root/'web')
  page=urllib.request.urlopen(self.url).read().decode('utf8')
  self.assertIn('data-tab="strategy-phases"',page)
  self.assertIn('id="strategy-phases"',page)
  self.assertNotIn('id="strategy-phases"',strip_section(page,'strategy-phases'))
  self.assertIn('strategy-phases.js',page)
  result=json.load(urllib.request.urlopen(self.url+'/api/admin/strategy-phases?interval=1h'))
  self.assertIn('rows',result);self.assertEqual(result['interval'],'1h')
  for asset in ('strategy-phases.js','strategy-phases.css'):
   self.assertEqual(urllib.request.urlopen(self.url+'/assets/'+asset).status,200)
  with self.assertRaises(urllib.error.HTTPError) as error:urllib.request.urlopen(self.url+'/api/admin/strategy-phases?interval=bad')
  self.assertEqual(error.exception.code,400)

 def test_lightweight_health_does_not_create_sessions(self):
  import os
  from pc28.watchdog import root_id
  before=self.s.db.query('SELECT COUNT(*) n FROM web_sessions')[0]['n']
  opener=urllib.request.build_opener(urllib.request.ProxyHandler({}));data=json.load(opener.open('http://127.0.0.1:18028/api/health'))
  self.assertEqual(data,{'ok':True,'service':'pc28','pid':os.getpid(),'parent_pid':os.getppid(),'root_id':root_id(self.root)});self.assertEqual(self.s.db.query('SELECT COUNT(*) n FROM web_sessions')[0]['n'],before)
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent/'data');self.root=Path(self.tmp.name);c=copy.deepcopy(DEFAULT);c['port']=18028;c['collector']['enabled']=False;(self.root/'config.json').write_text(json.dumps(c),encoding='utf8');self.s=Service(self.root);self.server=LocalServer(self.s);self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start();self.url='http://127.0.0.1:18028/admin'
  from pc28.auth import hash_password
  from pc28.trend_strategy import settings
  self.s.cfg['trend_simulation']['strategies']={axis:{**settings(self.s.cfg,axis),'direction':direction} for axis,direction in [('size','大'),('parity','单')]}
  with self.s.db.tx() as c:uid=c.execute("INSERT INTO web_users(username,password_hash,role,created) VALUES(?,?,?,?)",('admin',hash_password('test-password-123'),'admin','2026-01-01')).lastrowid
  with self.s.db.tx() as c:session,cookie=self.s.auth.write_session(c,uid,scope='admin',verified=True)
  self.server.token=session['csrf'];self.previous_opener=urllib.request._opener;opener=urllib.request.build_opener();opener.addheaders=[('Cookie',cookie.split(';')[0])];urllib.request.install_opener(opener)
 def tearDown(self):self.server.shutdown();self.server.server_close();self.tmp.cleanup();urllib.request.install_opener(self.previous_opener)
 def test_get_and_csrf(self):
  self.assertEqual(json.load(urllib.request.urlopen(self.url+'/api/status'))['count'],0)
  req=urllib.request.Request(self.url+'/api/pause',data=b'{"paused":true}',headers={'Content-Type':'application/json'})
  with self.assertRaises(urllib.error.HTTPError) as e:urllib.request.urlopen(req)
  self.assertEqual(e.exception.code,403)
  token=json.load(urllib.request.urlopen(self.url+'/api/session'))['token'];req.add_header('X-PC28-Token',token);self.assertTrue(json.load(urllib.request.urlopen(req))['ok'])
 def test_expired_session_is_identifiable_and_new_session_works(self):
  req=urllib.request.Request(self.url+'/api/pause',data=b'{"paused":false}',headers={'Content-Type':'application/json','X-PC28-Token':'previous-server-token'})
  with self.assertRaises(urllib.error.HTTPError) as e:urllib.request.urlopen(req)
  self.assertEqual(json.load(e.exception)['code'],'invalid_session')
  req.add_header('X-PC28-Token',self.server.token)
  self.assertTrue(json.load(urllib.request.urlopen(req))['ok'])
 def test_wrong_origin_not_treated_as_expired_session(self):
  req=urllib.request.Request(self.url+'/api/pause',data=b'{"paused":false}',headers={'Content-Type':'application/json','X-PC28-Token':self.server.token,'Origin':'https://example.com'})
  with self.assertRaises(urllib.error.HTTPError) as e:urllib.request.urlopen(req)
  self.assertEqual(json.load(e.exception)['code'],'forbidden_origin')
 def test_tools_endpoint_and_invalid_filters(self):
  result=json.load(urllib.request.urlopen(self.url+'/api/tools?view=analysis&number=13'));self.assertEqual(result['coverage']['count'],0)
  with self.assertRaises(urllib.error.HTTPError) as e:urllib.request.urlopen(self.url+'/api/tools?view=analysis&number=99')
  self.assertEqual(e.exception.code,400)
 def test_two_trend_strategy_controls_and_split_status(self):
  result=json.load(urllib.request.urlopen(self.url+'/api/trend-status'));self.assertEqual(set(result['strategies']),{'parity','size'});self.assertFalse(any(s['enabled'] for s in result['strategies'].values()))
  req=urllib.request.Request(self.url+'/api/trend-control',data=b'{"enabled_axes":["parity","size"]}',headers={'Content-Type':'application/json','X-PC28-Token':self.server.token})
  self.assertTrue(json.load(urllib.request.urlopen(req))['ok']);result=json.load(urllib.request.urlopen(self.url+'/api/trend-status'));self.assertTrue(all(s['enabled'] for s in result['strategies'].values()))
 def test_strategy_catalog_controls_share_execution_flags(self):
  def post(body):
   req=urllib.request.Request(self.url+'/api/strategy-control',data=json.dumps(body).encode(),headers={'Content-Type':'application/json','X-PC28-Token':self.server.token})
   return json.load(urllib.request.urlopen(req))
  rows=json.load(urllib.request.urlopen(self.url+'/api/strategies'))['strategies']
  self.assertEqual({r['id'] for r in rows},{'legacy','parity','size'})
  post({'id':'legacy','enabled':True});post({'id':'size','enabled':True});post({'id':'parity','enabled':True})
  self.assertTrue(self.s.cfg['simulation']['enabled']);self.assertEqual(self.s.cfg['trend_simulation']['enabled_axes'],['parity','size'])
  self.s.pause(True);post({'id':'legacy','enabled':False})
  row=json.load(urllib.request.urlopen(self.url+'/api/strategies'))['strategies'][0]
  self.assertFalse(row['enabled']);self.assertTrue(row['paused'])
  self.assertEqual(self.s.cfg['trend_simulation']['enabled_axes'],['parity','size'])
  with self.assertRaises(urllib.error.HTTPError):post({'id':'fake','enabled':True})
  with self.assertRaises(urllib.error.HTTPError):post({'id':'size','enabled':'false'})
 def test_strategy_create_api_and_unified_filter(self):
  settings={k:copy.deepcopy(self.s.cfg['simulation'][k]) for k in ('mode','initial_balance','daily_loss_limit','max_drawdown','max_stake','round_restart','fixed')}
  req=urllib.request.Request(self.url+'/api/strategy-save',data=json.dumps({'name':'test custom','settings':settings}).encode(),headers={'Content-Type':'application/json','X-PC28-Token':self.server.token})
  key=json.load(urllib.request.urlopen(req))['id'];rows=json.load(urllib.request.urlopen(self.url+'/api/strategies'))['strategies'];row=next(r for r in rows if r['id']==key)
  self.assertFalse(row['enabled']);self.assertTrue(row['editable'])
  result=json.load(urllib.request.urlopen(self.url+'/api/strategy-orders?strategy='+key+'&active=0'));self.assertEqual(result['total'],0);self.assertEqual(result['strategy_names'][key],'test custom')
 def test_compact_status_skips_heavy_lists(self):
  from unittest.mock import patch
  with patch.object(self.s.db,'gaps',side_effect=AssertionError('compact response must not scan gaps')):
   result=json.load(urllib.request.urlopen(self.url+'/api/status?compact=1'))
  self.assertIn('fresh',result);self.assertIn('next_draw',result);self.assertNotIn('gaps',result);self.assertNotIn('orders',result)
 def test_strategy_compact_preserves_full_response(self):
  compact=json.load(urllib.request.urlopen(self.url+'/api/strategies?compact=1'))
  full=json.load(urllib.request.urlopen(self.url+'/api/strategies'))
  self.assertEqual([r['id'] for r in compact['strategies']],[r['id'] for r in full['strategies']])
  self.assertTrue(all('curve' not in r['stats'] for r in compact['strategies']))
  self.assertTrue(all('curve' in r['stats'] for r in full['strategies']))
 def test_host_rejected(self):
  req=urllib.request.Request(self.url+'/api/status',headers={'Host':'evil.test'})
  with self.assertRaises(urllib.error.HTTPError) as e:urllib.request.urlopen(req)
  self.assertEqual(e.exception.code,403)
if __name__=='__main__':unittest.main()
