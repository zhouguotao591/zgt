import copy,json,tempfile,threading,time,unittest,io,urllib.error
from pathlib import Path
from datetime import datetime,timezone,timedelta
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from unittest.mock import patch
from pc28.config import DEFAULT
from pc28.service import Service
from pc28.robot_signals import RobotSignals,validate_rule,receiver_error,member_stake
from pc28.bot_secret import SecretStore

class MemorySecret:
 def exists(self):return True
 def read(self):return 'test-token-not-real-123'
 def write(self,value):pass

class RobotTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent/'data');self.root=Path(self.tmp.name);cfg=copy.deepcopy(DEFAULT);cfg['collector']['enabled']=False;cfg['trend_simulation']['enabled_axes']=['size','parity'];(self.root/'config.json').write_text(json.dumps(cfg));self.s=Service(self.root);self.r=RobotSignals(self.s,MemorySecret());self.r.save_rule(self.r.defaults());self.r.save_config({**self.r.config(),'enabled':True,'merge_wait_seconds':0})
 def tearDown(self):self.tmp.cleanup()
 def permit(self,axis):
  if not self.r.bindings():
   uid=self.member('fixture_default');cfg=self.r.config();self.r.save_binding({'user_id':uid,'account_id':cfg['account_id'] or 1,'bot_key':cfg['bot_key'] or 3,'signal_type':self.r.rules()[0]['signal_type'],'enabled':True,'strategy_ids':['size','parity'],'default_cents':1000,'strategy_amounts':{}})
  self.r.push_control({'id':axis,'enabled':True})
 def order(self,axis='size',direction='大',stake=1000,target=999,age=5):
  with self.s.db.tx() as c:return c.execute("INSERT INTO trend_orders(strategy,target_draw,cutoff_draw,created,direction,stake,status,cycle,step) VALUES(?,?,?,?,?,?,'pending',1,1)",(axis,target,target-1,(datetime.now(timezone.utc)-timedelta(seconds=age)).isoformat(),direction,stake)).lastrowid
 def fake_send(self,statuses):
  owner=self;owner.received=[]
  class Receiver(BaseHTTPRequestHandler):
   def log_message(self,*a):pass
   def do_POST(self):
    owner.received.append((dict(self.headers),json.loads(self.rfile.read(int(self.headers['Content-Length'])))));self.send_response(statuses[min(len(owner.received)-1,len(statuses)-1)]);self.end_headers()
  server=ThreadingHTTPServer(('127.0.0.1',0),Receiver);threading.Thread(target=server.serve_forever,daemon=True).start();self.addCleanup(server.server_close);self.addCleanup(server.shutdown);return patch('pc28.robot_signals.ENDPOINT','http://127.0.0.1:'+str(server.server_port)+'/api/v1/signals')
 def test_default_permissions_off_and_source_gate(self):
  self.order();self.assertEqual(self.r.capture(),0);self.permit('size');self.s.cfg['trend_simulation']['enabled_axes']=[];self.order(target=1000);self.assertEqual(self.r.capture(),0)
 def test_platforms_isolate_credentials_batches_failures_and_bindings(self):
  self.permit('size');received=[]
  class Receiver(BaseHTTPRequestHandler):
   def log_message(self,*a):pass
   def do_POST(self):
    received.append((self.headers['Authorization'],json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
    self.send_response(202);self.end_headers()
  server=ThreadingHTTPServer(('127.0.0.1',0),Receiver);threading.Thread(target=server.serve_forever,daemon=True).start();self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
  platform=self.r.save_platform({'name':'远程工具','endpoint':f'http://127.0.0.1:{server.server_port}/api/v1/signals','enabled':True,'token':'separate-platform-token-123'})['id']
  binding=self.r.bindings()[0];body={k:binding[k] for k in ('user_id','account_id','bot_key','signal_type','enabled','strategy_ids','default_cents','strategy_amounts')};body['platform_id']=platform;body['default_cents']=2500
  self.r.save_binding(body);self.assertEqual(len(self.r.bindings()),2)
  restarted=RobotSignals(self.s,MemorySecret());self.assertEqual(len(restarted.bindings()),2)
  self.order();self.order('parity','单');self.assertEqual(self.r.capture(),4)
  with self.fake_send([401]):
   self.r.deliver_one();self.r.deliver_one()
  self.assertEqual(len(received),1);self.assertEqual(received[0][0],'Bearer separate-platform-token-123');self.assertEqual(received[0][1]['payload']['text'],'大25 单25')
  self.assertEqual({r['status'] for r in self.s.db.query('SELECT status FROM robot_outbox WHERE platform_id=?',(platform,))},{'sent'})
  self.assertEqual({r['status'] for r in self.s.db.query('SELECT status FROM robot_outbox WHERE platform_id=1')},{'failed'})
  self.assertNotIn('separate-platform-token',json.dumps(self.r.platforms()))
  self.r.save_platform({'id':platform,'name':'远程工具','endpoint':f'http://127.0.0.1:{server.server_port}/api/v1/signals','enabled':False})
  self.order(target=1000);self.assertEqual(self.r.capture(),1)
 def test_platform_validation_and_migration_are_idempotent(self):
  for url in ('ftp://example/api/v1/signals','https://user:pass@example/api/v1/signals','https://example/api/v1/signals?token=x'):
   with self.assertRaises(ValueError):self.r.save_platform({'name':'invalid','endpoint':url,'enabled':False})
  self.permit('size');self.r.init_platforms();self.assertEqual(self.r.bindings()[0]['platform_id'],1);self.assertEqual(len(self.r.platforms()),1)
 def test_same_draw_combined_exactly_once(self):
  self.permit('size');self.permit('parity');self.order();self.order('parity','单',1000);self.assertEqual(self.r.capture(),2);self.assertEqual(self.r.capture(),0)
  with self.fake_send([200]):self.assertTrue(self.r.deliver_one());self.assertFalse(self.r.deliver_one())
  self.assertEqual(len(self.received),1);headers,wire=self.received[0];self.assertEqual(wire['payload']['text'],'大10 单10');self.assertEqual(headers['Authorization'],'Bearer test-token-not-real-123');self.assertEqual(wire['signal_type'],'BUY_SIGNAL');self.assertAlmostEqual(wire['timestamp'],time.time(),delta=3);self.assertEqual({r['status'] for r in self.s.db.query('SELECT status FROM robot_outbox')},{'sent'})
 def test_different_draws_not_combined(self):
  self.permit('size');self.order(target=999);self.order(target=1000);self.r.capture()
  with self.fake_send([200]):self.r.deliver_one();self.r.deliver_one()
  self.assertEqual(len(self.received),2)
 def test_retry_keeps_batch_event_and_membership(self):
  self.permit('size');self.permit('parity');self.order();self.order('parity','双',1250);self.r.capture()
  with self.fake_send([500,200]):
   self.r.deliver_one()
   with self.s.db.tx() as c:c.execute('UPDATE robot_outbox SET next_attempt=0')
   self.r.deliver_one()
  self.assertEqual(len(self.received),2);self.assertEqual(self.received[0][1]['event_id'],self.received[1][1]['event_id']);self.assertEqual(self.received[1][1]['payload']['text'],'大10 双10')
 def test_legacy_push_switch_does_not_override_member_subscription(self):
  self.permit('size');self.permit('parity');self.order();self.order('parity','单');self.r.capture();self.r.push_control({'id':'parity','enabled':False})
  with self.fake_send([200]):self.r.deliver_one()
  self.assertEqual(self.received[0][1]['payload']['text'],'大10 单10')
 def test_stop_member_after_failed_batch_cancels_whole_retry(self):
  self.permit('size');self.permit('parity');self.order();self.order('parity','单');self.r.capture()
  with self.fake_send([500]):
   self.r.deliver_one();self.r.binding_control({'id':self.r.bindings()[0]['id'],'enabled':False})
   with self.s.db.tx() as c:c.execute('UPDATE robot_outbox SET next_attempt=0')
   self.r.deliver_one()
  self.assertEqual(len(self.received),1);self.assertEqual({r['status'] for r in self.s.db.query('SELECT status FROM robot_outbox')},{'cancelled'})
 def test_no_replay_on_enable_and_expiry(self):
  self.permit('size');self.order(age=601);self.assertEqual(self.r.capture(),0);self.r.save_config({**self.r.config(),'enabled':False});self.order(target=1000);self.r.save_config({**self.r.config(),'enabled':True});self.assertEqual(self.r.capture(),0)
 def test_multiple_rules_one_order_one_push(self):
  self.r.save_rule(self.r.defaults());self.permit('size');self.order();self.assertEqual(self.r.capture(),1)
 def test_auth_failure_no_auto_retry(self):
  self.permit('size');self.order();self.r.capture()
  with self.fake_send([401]):self.r.deliver_one()
  self.assertEqual(self.s.db.query('SELECT status FROM robot_outbox')[0]['status'],'failed')
 def test_template_validation_and_preview(self):
  d=self.r.defaults();self.assertEqual(self.r.preview(d)['message'],'大10');d['message_template']='{direction.__class__}'
  with self.assertRaises(ValueError):validate_rule(d)
 def test_multi_account_route(self):
  self.r.save_config({**self.r.config(),'account_id':1,'bot_key':3});rule=self.r.defaults();rule['id']=self.r.rules()[0]['id'];rule['signal_type']='BUY_SYSTEM';self.r.save_rule(rule);self.permit('size');self.permit('parity');self.order();self.order('parity','双',5100);self.r.capture()
  with self.fake_send([202]):self.r.deliver_one()
  wire=self.received[0][1];self.assertEqual(wire['account_id'],1);self.assertEqual(wire['bot_key'],3);self.assertEqual(wire['signal_type'],'BUY_SYSTEM');self.assertEqual(wire['payload']['text'],'大10 双10')
 def test_receiver_error_allowlist(self):
  def response(value):return urllib.error.HTTPError('http://127.0.0.1',400,'bad',{},io.BytesIO(json.dumps({'error':value}).encode()))
  self.assertIn('多账号信号必须指定 account_id',receiver_error(response('多账号信号必须指定 account_id')));self.assertEqual(receiver_error(response('arbitrary-secret-should-not-be-stored')),'接收端返回 HTTP 400')
 def test_account_validation_and_legacy_config_client(self):
  cfg={**self.r.config(),'account_id':1,'bot_key':3};self.r.save_config(cfg);self.r.save_config({k:v for k,v in cfg.items() if k not in ('account_id','bot_key')});self.assertEqual(self.r.config()['account_id'],1)
  with self.assertRaises(ValueError):self.r.save_config({**cfg,'account_id':-1})
 def member(self,name):
  with self.s.db.tx() as c:return c.execute("INSERT INTO web_users(username,password_hash,role,created) VALUES(?,'fixture-hash','member',?)",(name,datetime.now(timezone.utc).isoformat())).lastrowid
 def binding(self,uid,ids,account=1,bot=3,enabled=True,base=1000,amounts=None):return self.r.save_binding({'user_id':uid,'account_id':account,'bot_key':bot,'signal_type':'BUY_SYSTEM','enabled':enabled,'strategy_ids':ids,'default_cents':base,'strategy_amounts':amounts or {}})['id']
 def test_member_subscription_isolation_and_batch_per_recipient(self):
  a=self.member('member_a');b=self.member('member_b');self.binding(a,['size','parity'],amounts={'parity':2000});self.binding(b,['parity'],2,4,base=3000);self.permit('size');self.permit('parity');self.order();self.order('parity','双',5100);self.assertEqual(self.r.capture(),3);self.assertEqual(self.r.capture(),0)
  RobotSignals(self.s,MemorySecret()) # restart must preserve recipient-scoped uniqueness
  with self.fake_send([202]):self.r.deliver_one();self.r.deliver_one();self.assertFalse(self.r.deliver_one())
  wires={v['account_id']:v for _,v in self.received};self.assertEqual(wires[1]['payload']['text'],'大10 双20');self.assertEqual(wires[2]['payload']['text'],'双30');self.assertEqual(wires[2]['bot_key'],4);self.assertNotEqual(wires[1]['event_id'],wires[2]['event_id']);self.assertEqual(self.r.records({})['total'],2)
 def test_disabled_binding_does_not_fall_back_to_global(self):
  self.binding(self.member('member_off'),['size'],enabled=False);self.permit('size');self.order();self.assertEqual(self.r.capture(),0)
 def test_inactive_member_blocks_delivery(self):
  uid=self.member('member_disabled');self.binding(uid,['size']);self.permit('size');self.order();self.r.capture()
  with self.s.db.tx() as c:c.execute('UPDATE web_users SET active=0 WHERE id=?',(uid,))
  with self.fake_send([202]):self.r.deliver_one()
  self.assertEqual(self.received,[]);self.assertEqual(self.s.db.query('SELECT status FROM robot_outbox')[0]['status'],'cancelled')
 def test_binding_enable_does_not_replay_old_order(self):
  key=self.binding(self.member('member_later'),['size'],enabled=False);self.permit('size');self.order();self.r.binding_control({'id':key,'enabled':True});self.assertEqual(self.r.capture(),0);self.order(target=1000);self.assertEqual(self.r.capture(),1)
 def test_binding_duplicate_target_and_member_rejected(self):
  a=self.member('member_first');b=self.member('member_other');self.binding(a,['size'])
  with self.assertRaises(ValueError):self.binding(b,['size'])
  with self.assertRaises(ValueError):self.binding(a,['size'],2,4)
 def test_unbound_never_uses_simulation_amount(self):
  self.r.push_control({'id':'size','enabled':True});self.order(stake=987600);self.assertEqual(self.r.capture(),0)
 def test_late_order_does_not_send_second_message_for_same_member_draw(self):
  self.permit('size');self.permit('parity');self.order();self.r.capture()
  with self.fake_send([202]):
   self.r.deliver_one();self.order('parity','双',9900);self.r.capture();self.r.deliver_one()
  self.assertEqual(len(self.received),1);self.assertEqual(self.s.db.query('SELECT status FROM robot_outbox ORDER BY id DESC LIMIT 1')[0]['status'],'cancelled')
 def test_collection_wait_and_one_record_per_merged_message(self):
  self.r.save_config({**self.r.config(),'merge_wait_seconds':3});self.permit('size');self.permit('parity');self.order(stake=900000);self.order('parity','双',800000);self.r.capture();self.assertEqual(self.r.records({})['total'],1);self.assertFalse(self.r.deliver_one())
  with self.s.db.tx() as c:c.execute('UPDATE robot_outbox SET created=?',((datetime.now(timezone.utc)-timedelta(seconds=5)).isoformat(),))
  with self.fake_send([202]):self.r.deliver_one()
  d=self.r.records({});self.assertEqual(d['total'],1);self.assertEqual(d['rows'][0]['order_count'],2);self.assertEqual(d['rows'][0]['message'],'大10 双10');self.assertEqual(self.received[0][1]['payload']['text'],'大10 双10')
 def test_ratio_uses_each_order_and_ceils_whole_u(self):
  uid=self.member('ratio_member');self.binding(uid,['size'],amounts={'size':{'mode':'ratio','ratio_units':5000}})
  for i,cents in enumerate([50000,75000,100000,75500]):self.order(stake=cents,target=1000+i)
  self.r.capture()
  with self.fake_send([202]):
   for _ in range(4):self.r.deliver_one()
  self.assertEqual([w['payload']['text'] for _,w in self.received],['大250','大375','大500','大378'])
  self.assertEqual(member_stake({'mode':'ratio','ratio_units':10000},0,75000),75000);self.assertEqual(member_stake({'mode':'ratio','ratio_units':1},0,100),100)
 def test_fixed_format_ignores_saved_template_and_rules_disabled(self):
  uid=self.member('fixed_format');self.binding(uid,['size']);rule=self.r.defaults();rule['message_template']='WRONG {stake}';rule['payload_template']={'other':'wrong'};rule['id']=self.r.rules()[0]['id'];rule['enabled']=False;self.r.save_rule(rule);self.order();self.r.capture()
  with self.fake_send([202]):self.r.deliver_one()
  self.assertEqual(self.received[0][1]['payload'],{'symbol':'PC28','text':'大10'})
 def test_ratio_above_market_limit_is_recorded_not_sent(self):
  self.binding(self.member('over_limit'),['size'],amounts={'size':{'mode':'ratio','ratio_units':1000000}});self.order(stake=2000000);self.r.capture()
  with self.fake_send([202]):self.assertFalse(self.r.deliver_one())
  self.assertEqual(self.received,[]);self.assertIn('超出',self.r.records({})['rows'][0]['last_error'])
 def test_delete_merged_record_preserves_orders_batches_and_dedup(self):
  self.permit('size');self.permit('parity');self.order();self.order('parity','双');self.r.capture()
  with self.fake_send([202]):self.r.deliver_one()
  key=self.r.records({})['rows'][0]['id'];self.r.delete_records({'ids':[key]});self.assertEqual(self.r.records({})['total'],0);self.assertEqual(len(self.s.db.query('SELECT * FROM robot_outbox')),2);self.assertEqual(len(self.s.db.query('SELECT * FROM trend_orders')),2);self.assertEqual(len(self.s.db.query('SELECT * FROM robot_delivery_slots')),1);self.assertEqual(self.r.capture(),0)
 def test_batch_delete_atomic_when_one_message_is_active(self):
  self.permit('size');self.order(target=1000);self.order(target=1001);self.r.capture()
  with self.s.db.tx() as c:c.execute("UPDATE robot_outbox SET status='failed' WHERE target_draw=1000")
  keys=[r['id'] for r in self.r.records({})['rows']]
  with self.assertRaises(ValueError):self.r.delete_records({'ids':keys})
  self.assertEqual(self.s.db.query('SELECT COUNT(*) n FROM robot_hidden_records')[0]['n'],0)
 def test_bulk_delete_and_page_clamp(self):
  self.permit('size')
  for i in range(12):self.order(target=1000+i)
  self.r.capture()
  with self.s.db.tx() as c:c.execute("UPDATE robot_outbox SET status='failed'")
  keys=[r['id'] for r in self.r.records({'page_size':20})['rows']];self.r.delete_records({'ids':keys});d=self.r.records({'page':2,'page_size':10});self.assertEqual(d['page'],1);self.assertEqual(d['total'],0)
 def test_dpapi_roundtrip_no_plaintext(self):
  s=SecretStore(self.root);token='dummy-encrypted-token-123';s.write(token);self.assertEqual(s.read(),token);self.assertNotIn(token.encode(),s.path.read_bytes())

if __name__=='__main__':unittest.main()
