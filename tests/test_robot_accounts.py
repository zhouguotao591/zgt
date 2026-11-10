import unittest,json,copy,threading,time
from unittest.mock import patch
import test_robot_signals as fixture
from pc28.auth import AuthError

class AccountTests(unittest.TestCase):
 setUp=fixture.RobotTests.setUp
 tearDown=fixture.RobotTests.tearDown
 member=fixture.RobotTests.member
 order=fixture.RobotTests.order
 fake_send=fixture.RobotTests.fake_send
 ready=fixture.RobotTests.ready
 def body(self,uid,external=101):return {'user_id':uid,'platform_id':1,'external_account_id':external,'name':'主账号','enabled':True,'bots':[{'name':'大小机器人','external_bot_key':3,'signal_type':'BUY_SYSTEM','enabled':True},{'name':'单双机器人','external_bot_key':4,'signal_type':'BUY_SYSTEM','enabled':True}],'subscriptions':[{'strategy_id':'size','bot_index':0,'amount_mode':'ratio','ratio_units':5000},{'strategy_id':'parity','bot_index':1,'amount_mode':'fixed','fixed_cents':10000}]}
 def create(self):return self.r.account_save(self.body(self.member('multi_member')),None,'')['id']
 def test_bot_risk_blocks_oversize_batch_before_sending(self):
  from datetime import datetime,timezone,timedelta
  aid=self.create();bot=self.r.account_detail(aid)['bots'][0]
  self.r.risk_action({'id':aid,'bot_id':bot['id'],'bot_version':bot['version'],'action':'save',
                      'capital':1000,'loss_mode':'percent','loss_limit':5000,
                      'profit_target':500,'trailing_bps':5000},None,'')
  self.order(stake=2000);self.assertEqual(self.r.capture(),1);self.ready()
  now=datetime.now(timezone.utc);previous=now-timedelta(seconds=50);due=previous+timedelta(seconds=210)
  self.s.db.import_records([{'id':998,'time':previous.isoformat(),'numbers':list(range(1,21))}],'test')
  with self.s.db.tx() as c:
   c.execute("INSERT OR REPLACE INTO runtime_state VALUES('draw_timer',?,?)",
             (json.dumps({'next_draw_id':999,'next_draw_at':due.isoformat()}),now.isoformat()))
  with patch('pc28.maintenance.live_guard',return_value=''),patch('pc28.robot_signals.urllib.request.build_opener') as opener:
   self.assertTrue(self.r.deliver_one())
   opener.assert_not_called()
  row=self.s.db.query('SELECT status,last_error,attempts FROM robot_outbox')[0]
  self.assertEqual((row['status'],row['attempts']),('cancelled',0))
  self.assertIn('止损线',row['last_error'])
 def test_bot_risk_trailing_pause_persists_and_reset_keeps_pending(self):
  from pc28 import robot_risk
  from pc28.member_orders import entry
  aid=self.create();account=self.r.account_detail(aid);bot=account['bots'][0];bid=bot['id'];uid=account['user_id']
  body={'capital':100000,'loss_mode':'amount','loss_limit':20000,'profit_target':10000,'trailing_bps':4000}
  with self.s.db.tx() as c:
   robot_risk.save(c,bid,body)
   def settle(oid,pnl):
    c.execute("""INSERT INTO member_orders(id,user_id,outbox_id,target_draw,direction,stake,placed_at,accepted_at,
     odds_json,status,pnl,bot_id) VALUES(?,?,?,999,'大',1000,'t','t','{}','settled',?,?)""",(oid,uid,oid,pnl,bid))
    entry(c,uid,'settle',1000+pnl,order_id=oid)
    return robot_risk.check(c,bid)
   self.assertEqual(settle(1,10000)['floor'],6000)
   self.assertEqual(settle(2,10000)['floor'],12000)
   self.assertEqual(settle(3,-8000)['paused'],'触及移动止损线')
   self.assertEqual(settle(4,1000)['paused'],'触及移动止损线')
   c.execute("""INSERT INTO member_orders(user_id,outbox_id,target_draw,direction,stake,placed_at,accepted_at,odds_json,bot_id)
      VALUES(?,5,999,'大',1000,'t','t','{}',?)""",(uid,bid))
   with self.assertRaises(ValueError):robot_risk.resume(c,bid,False)
   reset=robot_risk.resume(c,bid,True)
   self.assertEqual((reset['net'],reset['peak'],reset['pending'],reset['paused']),(0,0,1000,''))
   self.assertEqual(settle(6,-51000)['paused'],'触及配资50%强制止损线')
   with self.assertRaises(ValueError):robot_risk.save(c,bid,{**body,'trailing_bps':5001})
   with self.assertRaises(ValueError):robot_risk.save(c,bid,{**body,'loss_mode':'percent','loss_limit':5001})
   self.assertIsNone(robot_risk.state(c,account['bots'][1]['id']))
 def test_bot_risk_reserves_retries_and_requires_confirmed_release(self):
  from pc28 import robot_risk
  aid=self.create();bot=self.r.account_detail(aid)['bots'][0];bid=bot['id']
  self.order(stake=6000);self.r.capture()
  with self.s.db.tx() as c:
   order=c.execute('SELECT id,push_cents FROM robot_outbox WHERE bot_id=?',(bid,)).fetchone()
   robot_risk.save(c,bid,{'capital':10000,'loss_mode':'amount','loss_limit':5000,
                        'profit_target':1000,'trailing_bps':5000})
   self.assertEqual(robot_risk.admit(c,bid,[order]),'')
   self.assertEqual(robot_risk.admit(c,bid,[order]),'')
   self.assertEqual(robot_risk.state(c,bid)['held'],3000)
   self.assertIn('止损线',robot_risk.admit(c,bid,[{'id':999,'push_cents':2100}]))
   with self.assertRaises(ValueError):robot_risk.reconcile_rejected(c,bid,order['id'])
   c.execute("UPDATE robot_outbox SET status='failed' WHERE id=?",(order['id'],))
   self.assertEqual(robot_risk.state(c,bid)['uncertain'],1)
   robot_risk.reconcile_rejected(c,bid,order['id'])
   self.assertEqual(robot_risk.state(c,bid)['held'],0)
 def test_shared_catalog_robot_routes_separately_by_account(self):
  uid=self.member('shared_robot_member')
  shared=self.r.catalog_save({'platform_id':1,'name':'共享机器人'},None,'')['id']
  first=self.body(uid,101)
  second=self.body(uid,102)
  for account,key in ((first,31),(second,32)):
   account['bots'][0].update(catalog_id=shared,external_bot_key=key)
  second['subscriptions'][0]['ratio_units']=10000
  a=self.r.account_save(first,None,'')['id']
  b=self.r.account_save(second,None,'')['id']
  self.assertEqual(self.r.account_detail(a)['bots'][0]['catalog_id'],shared)
  self.assertEqual(self.r.account_detail(b)['bots'][0]['catalog_id'],shared)
  self.assertEqual(next(x for x in self.r.catalog_list() if x['id']==shared)['subscriber_count'],2)
  self.order('size','大',stake=75100)
  self.assertEqual(self.r.capture(),2)
  outputs=self.s.db.query('SELECT request,push_cents FROM robot_outbox ORDER BY id')
  self.assertEqual(sorted((json.loads(x['request'])['account_id'],json.loads(x['request'])['bot_key'],x['push_cents']) for x in outputs),[(101,31,37600),(102,32,75100)])
  current=self.r.account_detail(a)
  self.r.account_action('subscriptions',{'id':a,'version':current['version'],'bot_id':current['bots'][0]['id'],'subscriptions':[]},None,'')
  self.assertTrue(next(s for s in self.r.account_detail(b)['subscriptions'] if s['strategy_id']=='size')['enabled'])
 def test_bot_subscription_save_does_not_touch_other_bot(self):
  aid=self.create()
  account=self.r.account_detail(aid)
  first,second=account['bots']
  other=next(s for s in account['subscriptions'] if s['bot_id']==second['id'])
  request={'id':aid,'version':account['version'],'bot_id':first['id'],'subscriptions':[]}
  self.r.account_action('subscriptions',request,None,'')
  updated=self.r.account_detail(aid)
  self.assertEqual(next(s for s in updated['subscriptions'] if s['bot_id']==second['id']),other)
  self.assertFalse(next(s for s in updated['subscriptions'] if s['bot_id']==first['id'])['enabled'])
  self.r.account_action('subscriptions',{**request,'version':updated['version'],'subscriptions':[dict(other,fixed_cents=20000,enabled=True)]},None,'')
  current=self.r.account_detail(aid)
  self.assertEqual(next(s for s in current['subscriptions'] if s['bot_id']==second['id']),other)
  self.order('parity','单')
  self.assertEqual(self.r.capture(),2)
  self.assertEqual(self.r.capture(),0)
  outputs=self.s.db.query('SELECT request,push_cents FROM robot_outbox ORDER BY id')
  self.assertEqual(sorted((json.loads(r['request'])['bot_key'],r['push_cents']) for r in outputs),[(3,20000),(4,10000)])
 def test_readding_archived_bot_reuses_delivery_target(self):
  aid=self.create()
  account=self.r.account_detail(aid)
  bot=account['bots'][1]
  self.r.account_action('subscriptions',{'id':aid,'version':account['version'],'subscriptions':[]},None,'')
  account=self.r.account_detail(aid)
  self.r.account_action('bot-control',{'id':aid,'version':account['version'],'bot_id':bot['id'],'bot_version':bot['version'],'deleted':True},None,'')
  account=self.r.account_detail(aid)
  request={'id':aid,'version':account['version'],'bot':{'name':'恢复机器人','external_bot_key':bot['external_bot_key'],'signal_type':bot['signal_type'],'enabled':True}}
  self.r.account_action('bot-save',request,None,'')
  restored=self.r.account_detail(aid)
  self.assertEqual(restored['bots'][1]['id'],bot['id'])
  self.assertEqual(restored['bots'][1]['binding_id'],bot['binding_id'])
  self.assertEqual(self.s.db.query('SELECT deleted,enabled FROM robot_bindings WHERE id=?',(bot['binding_id'],)),[{'deleted':0,'enabled':0}])
  self.assertFalse(any(s['enabled'] for s in restored['subscriptions']))
  with self.assertRaisesRegex(ValueError,'机器人ID已存在'):
   self.r.account_action('bot-save',{**request,'version':restored['version']},None,'')
 def test_multi_accounts_bots_routing_and_no_double_capture(self):
  uid=self.member('multi_member');aid=self.r.account_save(self.body(uid),None,'')['id'];self.r.account_save(self.body(uid,102),None,'');detail=self.r.account_detail(aid);self.assertEqual(len(detail['bots']),2);self.assertEqual(self.r.account_list({})['total'],2)
  self.order('size','大',stake=75100);self.order('parity','单');self.assertEqual(self.r.capture(),4);self.assertEqual(self.r.capture(),0)
  rows=self.s.db.query('SELECT * FROM robot_outbox ORDER BY id');self.assertEqual(len(rows),4);self.assertEqual(sorted(r['push_cents'] for r in rows),[10000,10000,37600,37600]);self.assertEqual({json.loads(r['request'])['bot_key'] for r in rows},{3,4})
  with self.fake_send([202]):
   for _ in range(4):self.r.deliver_one()
   self.assertEqual(len(self.received),4)
  self.assertEqual(self.s.db.query('SELECT COUNT(*) n FROM member_orders')[0]['n'],4)
 def test_unique_ownership_subscription_and_atomic_validation(self):
  uid=self.member('multi_member');body=self.body(uid);self.r.account_save(body,None,'')
  with self.assertRaises(ValueError):self.r.account_save(body,None,'')
  body=self.body(uid,103);body['subscriptions'].append(copy.deepcopy(body['subscriptions'][0]))
  with self.assertRaises(ValueError):self.r.account_save(body,None,'')
  self.assertEqual(self.r.account_list({})['total'],1)
  body=self.body(uid,104);body['subscriptions'][0]={'strategy_id':'size','bot_index':0,'amount_mode':'fixed','fixed_cents':1050}
  with self.assertRaises(ValueError):self.r.account_save(body,None,'')
 def test_optimistic_version_stop_cancel_and_default_does_not_reroute(self):
  aid=self.create();a=self.r.account_detail(aid);self.order();self.r.capture();self.assertEqual(len(self.s.db.query("SELECT * FROM robot_outbox WHERE status='pending'")),1)
  self.r.account_save({'id':aid,'version':a['version'],'name':a['name'],'enabled':True,'default_bot_id':a['bots'][1]['id']},None,'')
  self.assertEqual(self.r.account_detail(aid)['subscriptions'][0]['bot_id'],a['bots'][0]['id']);self.assertEqual(self.s.db.query('SELECT status FROM robot_outbox')[0]['status'],'pending')
  with self.assertRaises(AuthError):self.r.account_save({'id':aid,'version':a['version'],'name':'stale','enabled':True},None,'')
  a=self.r.account_detail(aid);self.r.account_action('account-control',{'id':aid,'version':a['version'],'enabled':False},None,'');self.assertEqual(self.s.db.query('SELECT status FROM robot_outbox')[0]['status'],'cancelled')
 def test_amount_overflow_blocks_whole_target(self):
  uid=self.member('multi_member');body=self.body(uid);body['subscriptions'][1]['bot_index']=0;body['subscriptions'][0]['ratio_units']=1000000;self.r.account_save(body,None,'')
  self.order('size','大',stake=100000);self.order('parity','单',stake=1000);self.r.capture();self.assertTrue(all(r['status']=='failed' for r in self.s.db.query('SELECT status FROM robot_outbox')))
 def test_migration_reentry_preserves_ids_slots_and_legacy_amounts(self):
  uid=self.member('legacy_member');self.r.save_binding({'user_id':uid,'account_id':1,'bot_key':3,'signal_type':'BUY_SYSTEM','enabled':True,'strategy_ids':['size'],'default_cents':1050,'strategy_amounts':{}});self.order();self.r.capture();before=self.s.db.query('SELECT id,event_id,recipient_id,status FROM robot_outbox');self.r.init_accounts();self.r.init_accounts();self.assertEqual(self.s.db.query('SELECT id,event_id,recipient_id,status FROM robot_outbox'),before);self.assertEqual(self.r.account_list({})['total'],1);self.assertEqual(self.r.account_detail(1)['subscriptions'][0]['fixed_cents'],1050)
 def test_timeout_retry_uses_frozen_event_and_amount(self):
  self.create();self.order();self.r.capture();self.ready();requests=[];accepted=set()
  class Response:
   status=202
   def __enter__(self):return self
   def __exit__(self,*args):pass
  class Opener:
   def open(inner,req,timeout):
    wire=json.loads(req.data);requests.append(wire);accepted.add(wire['event_id'])
    if len(requests)==1:raise TimeoutError('receiver accepted but response lost')
    return Response()
  with patch('pc28.robot_signals.urllib.request.build_opener',return_value=Opener()):
   self.r.deliver_one();self.assertEqual(self.s.db.query('SELECT status FROM robot_outbox')[0]['status'],'retry')
   with self.s.db.tx() as c:c.execute('UPDATE robot_outbox SET next_attempt=0')
   self.r.deliver_one()
  self.assertEqual(len(requests),2);self.assertEqual(len(accepted),1);self.assertEqual(requests[0]['payload'],requests[1]['payload']);self.assertEqual(self.s.db.query('SELECT COUNT(*) n FROM member_orders')[0]['n'],1)
 def test_platform_isolation_and_live_claim_blocks_same_target(self):
  aid=self.create();uid=self.member('other_member')
  with patch.object(self.r,'platform_secret',return_value=fixture.MemorySecret()):pid=self.r.save_platform({'name':'工具B','endpoint':'http://127.0.0.1:9/api/v1/signals','enabled':True})['id']
  body=self.body(uid,202);body['platform_id']=pid;self.r.account_save(body,None,'');self.order(target=999);self.order(target=1000);self.r.capture();self.ready();entered=threading.Event();release=threading.Event();requests=[];failures=[]
  class Response:
   status=202
   def __enter__(self):return self
   def __exit__(self,*args):pass
  class Opener:
   def open(inner,req,timeout):
    requests.append(json.loads(req.data))
    if req.full_url.endswith('/api/v1/signals') and json.loads(req.data)['account_id']==101:entered.set();release.wait(5)
    return Response()
  def send():
   try:self.r.deliver_one(1)
   except BaseException as e:failures.append(str(e))
  with patch.object(self.r,'platform_secret',return_value=fixture.MemorySecret()),patch('pc28.robot_signals.urllib.request.build_opener',return_value=Opener()):
   t=threading.Thread(target=send);t.start()
   try:
    self.assertTrue(entered.wait(3));self.assertFalse(self.r.deliver_one(1));self.assertTrue(self.r.deliver_one(pid));self.assertEqual(len(requests),2)
   finally:release.set();t.join(5)
  self.assertFalse(failures);self.assertFalse(t.is_alive())
 def test_rest_cancels_cached_batch_before_request(self):
  self.create();self.order();self.r.capture();self.ready()
  with patch('pc28.maintenance.live_guard',return_value='暂停休息中'),patch('pc28.robot_signals.urllib.request.build_opener') as transport:
   self.assertTrue(self.r.deliver_one());transport.assert_not_called()
  self.assertEqual(self.s.db.query('SELECT status FROM robot_outbox')[0]['status'],'cancelled')
 def test_restart_recovers_expired_claim_without_new_batch(self):
  self.create();self.order();self.r.capture()
  with self.fake_send([500,202]):
   self.r.deliver_one();event=self.received[0][1]['event_id']
   with self.s.db.tx() as c:c.execute("UPDATE robot_outbox SET status='sending',lease_until=?",(int(time.time())+60,))
   restarted=fixture.RobotSignals(self.s,fixture.MemorySecret());self.assertFalse(restarted.deliver_one())
   with self.s.db.tx() as c:c.execute('UPDATE robot_outbox SET lease_until=0')
   self.assertTrue(restarted.deliver_one());self.assertEqual(self.received[1][1]['event_id'],event)
  self.assertEqual(self.s.db.query('SELECT COUNT(*) n FROM robot_batches')[0]['n'],1)

if __name__=='__main__':unittest.main()
