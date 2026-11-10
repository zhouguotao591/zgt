import unittest,tempfile,copy,threading,json,urllib.request,urllib.error
from pathlib import Path
from pc28.config import DEFAULT
from pc28.service import Service
from pc28.server import LocalServer
class ServerTests(unittest.TestCase):
 def test_lightweight_health_does_not_create_sessions(self):
  import os
  from pc28.watchdog import root_id
  before=self.s.db.query('SELECT COUNT(*) n FROM web_sessions')[0]['n']
  opener=urllib.request.build_opener(urllib.request.ProxyHandler({}));data=json.load(opener.open('http://127.0.0.1:18028/api/health'))
  self.assertEqual(data,{'ok':True,'service':'pc28','pid':os.getpid(),'root_id':root_id(self.root)});self.assertEqual(self.s.db.query('SELECT COUNT(*) n FROM web_sessions')[0]['n'],before)
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent/'data');self.root=Path(self.tmp.name);c=copy.deepcopy(DEFAULT);c['port']=18028;c['collector']['enabled']=False;(self.root/'config.json').write_text(json.dumps(c),encoding='utf8');self.s=Service(self.root);self.server=LocalServer(self.s);self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start();self.url='http://127.0.0.1:18028/admin'
  from pc28.auth import hash_password
  with self.s.db.tx() as c:uid=c.execute("INSERT INTO web_users(username,password_hash,role,created) VALUES(?,?,?,?)",('testadmin',hash_password('test-password-123'),'admin','2026-01-01')).lastrowid
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
 def test_host_rejected(self):
  req=urllib.request.Request(self.url+'/api/status',headers={'Host':'evil.test'})
  with self.assertRaises(urllib.error.HTTPError) as e:urllib.request.urlopen(req)
  self.assertEqual(e.exception.code,403)
if __name__=='__main__':unittest.main()
