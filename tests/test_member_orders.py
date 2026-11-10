import json,unittest,time
from datetime import datetime,timezone,timedelta
from unittest.mock import patch
import test_robot_signals as fixture
import test_auth as auth_fixture
from pc28 import member_orders as finance
from pc28.model import utcnow


class MemberOrderTests(unittest.TestCase):
 setUp=fixture.RobotTests.setUp
 tearDown=fixture.RobotTests.tearDown
 permit=fixture.RobotTests.permit
 member=fixture.RobotTests.member
 binding=fixture.RobotTests.binding
 order=fixture.RobotTests.order
 fake_send=fixture.RobotTests.fake_send
 ready=fixture.RobotTests.ready

 def user_id(self):return self.r.bindings()[0]['user_id']
 def fund(self,amount='1000',rid='test-capital-unique-123',mode='add'):
  return finance.adjust(self.s,{'id':self.user_id(),'amount':amount,'mode':mode,'note':'test adjustment','request_id':rid},{'id':99,'role':'admin','username':'admin'},'127.0.0.1')

 def test_random_delay_is_cached_no_early_send_and_does_not_repeat(self):
  self.permit('size');self.order(target=999);self.r.capture()
  with self.fake_send([202],advance=False):
   self.assertFalse(self.r.deliver_one());self.assertEqual(self.received,[])
   row=self.s.db.query('SELECT * FROM robot_outbox')[0]
   due=datetime.fromisoformat(row['scheduled_at']).timestamp();created=datetime.fromisoformat(row['created']).timestamp()
   self.assertTrue(30<=round(due-created,3)<=60);self.assertEqual(row['attempts'],0);self.assertIsNone(row['sent_at'])
   batch=self.r.records({})['rows'][0]['batch_id'];self.assertTrue(batch)
   self.assertFalse(self.r.deliver_one());self.assertEqual(self.r.records({})['rows'][0]['batch_id'],batch)
   self.assertEqual(self.s.db.query('SELECT scheduled_at FROM robot_outbox')[0]['scheduled_at'],row['scheduled_at'])
   with patch('pc28.robot_signals.time.time',return_value=due+1),patch('pc28.robot_signals.utcnow',return_value=datetime.fromtimestamp(due+1,timezone.utc).isoformat()):self.assertTrue(self.r.deliver_one())
   self.assertEqual(len(self.received),1)
   result=self.r.records({})['rows'][0];self.assertEqual(result['status'],'sent');self.assertIsNotNone(result['accepted_at']);self.assertEqual(result['batch_id'],batch)
  first_delay=round(due-created);self.order(target=1000);self.r.capture();self.assertFalse(self.r.deliver_one())
  second=self.s.db.query('SELECT created,scheduled_at FROM robot_outbox ORDER BY id DESC LIMIT 1')[0]
  self.assertNotEqual(round((datetime.fromisoformat(second['scheduled_at'])-datetime.fromisoformat(second['created'])).total_seconds()),first_delay)

 def test_confirmed_pushes_use_member_amounts_and_settle_exactly_once(self):
  self.permit('size');self.permit('parity');self.fund()
  self.order('size','大',stake=900000);self.order('parity','双',stake=800000);self.r.capture()
  with self.fake_send([202]):self.r.deliver_one()
  orders=self.s.db.query('SELECT * FROM member_orders ORDER BY id')
  self.assertEqual([o['stake'] for o in orders],[1000,1000]);self.assertEqual(len({o['batch_id'] for o in orders}),1)
  self.assertEqual(self.s.db.query('SELECT balance FROM member_accounts WHERE user_id=?',(self.user_id(),))[0]['balance'],98000)
  self.s.cfg['odds']['normal']=100
  drawtime=datetime.now(timezone.utc)+timedelta(seconds=210)
  self.s.db.import_records([{'id':999,'time':drawtime.isoformat(),'numbers':list(range(1,21))}],'test')
  with self.s.db.tx() as c:finance.settle(c,(drawtime+timedelta(seconds=1)).isoformat());finance.settle(c,(drawtime+timedelta(seconds=1)).isoformat())
  orders=self.s.db.query('SELECT * FROM member_orders ORDER BY id');self.assertEqual([o['pnl'] for o in orders],[1000,-1000])
  account=self.s.db.query('SELECT * FROM member_accounts WHERE user_id=?',(self.user_id(),))[0];self.assertEqual(account['balance'],100000)
  self.assertEqual(len(self.s.db.query("SELECT * FROM member_cash_ledger WHERE kind='settle'")),2)
  listing=finance.listing(self.s,{'username':'fixture','draw':999,'direction':'大','page_size':10});self.assertEqual(listing['total'],1)
  self.assertEqual(listing['rows'][0]['before_balance'],100000);self.assertEqual(listing['rows'][0]['after_stake_balance'],99000)
  detail=listing['rows'][0]
  self.assertEqual(detail['target_draw'],999)
  self.assertEqual(detail['draw_a']+detail['draw_b']+detail['draw_c'],detail['total'])
  self.assertEqual(detail['before_settle_balance']+detail['settlement_credit'],detail['after_settle_balance'])
  self.assertEqual(detail['settlement_credit']-detail['stake'],detail['pnl'])
  stats=next(r for r in finance.users(self.s,{})['rows'] if r['id']==self.user_id());self.assertEqual((stats['profit'],stats['follow_count'],stats['roi']),(0,2,0))
  with self.s.db.tx() as c:
   row=c.execute('SELECT * FROM robot_outbox LIMIT 1').fetchone();finance.import_signal(c,self.s,row,orders[0]['batch_id'],orders[0]['placed_at'],orders[0]['accepted_at'])
  self.assertEqual(len(self.s.db.query('SELECT * FROM member_orders')),2)

 def test_failure_has_no_member_order_and_adjustments_are_audited(self):
  self.permit('size');self.fund();self.fund()
  self.assertEqual(self.s.db.query('SELECT balance FROM member_accounts WHERE user_id=?',(self.user_id(),))[0]['balance'],100000)
  self.order();self.r.capture()
  with self.fake_send([401]):self.r.deliver_one()
  self.assertEqual(self.s.db.query('SELECT * FROM member_orders'),[])
  self.fund('100','subtract-test-123','subtract')
  with self.assertRaises(ValueError):self.fund('1000','excess-subtract-test','subtract')
  logs=finance.listing(self.s,{'user_id':self.user_id()},True)['rows'];self.assertEqual(len(logs),2)
  self.assertEqual(logs[0]['before_balance'],100000);self.assertEqual(logs[0]['after_balance'],90000)
  self.assertEqual(logs[0]['principal_after'],90000);self.assertEqual(logs[0]['actor'],99)

 def test_expired_draw_not_sent_and_no_orders_recorded(self):
  self.permit('size');self.order();self.r.capture()
  self.s.db.import_records([{'id':999,'time':utcnow(),'numbers':list(range(1,21))}],'test')
  with self.fake_send([202]):self.assertTrue(self.r.deliver_one());self.assertEqual(self.received,[])
  self.assertEqual(self.s.db.query('SELECT status FROM robot_outbox')[0]['status'],'expired');self.assertEqual(self.s.db.query('SELECT * FROM member_orders'),[])

 def test_expected_draw_deadline_prevents_late_send_before_result_arrives(self):
  self.permit('size');self.order();self.r.capture()
  with self.s.db.tx() as c:c.execute("INSERT OR REPLACE INTO runtime_state VALUES('draw_timer',?,?)",(json.dumps({'next_draw_id':999,'next_draw_at':(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat()}),utcnow()))
  with self.fake_send([202]):self.assertTrue(self.r.deliver_one());self.assertEqual(self.received,[])
  self.assertEqual(self.s.db.query('SELECT status FROM robot_outbox')[0]['status'],'expired')

 def test_migration_seeds_existing_push_members_once_new_members_zero(self):
  self.permit('size');self.order();self.r.capture()
  with self.fake_send([202]):self.r.deliver_one()
  new=self.member('new_unfunded')
  with self.s.db.tx() as c:
   for table in ('member_orders','member_cash_ledger','member_accounts','member_finance_migrations'):c.execute('DROP TABLE '+table)
  finance.initialize(self.s);finance.initialize(self.s)
  self.assertEqual(self.s.db.query('SELECT principal FROM member_accounts WHERE user_id=?',(self.user_id(),))[0]['principal'],100000)
  self.assertEqual(self.s.db.query('SELECT principal FROM member_accounts WHERE user_id=?',(new,))[0]['principal'],0)
  self.assertEqual(len(self.s.db.query("SELECT * FROM member_cash_ledger WHERE kind='opening'")),1)
  self.assertEqual(len(self.s.db.query('SELECT * FROM member_orders')),1)
  member=finance.create(self.s,{'username':'created_member','password':'new-password-123'},{'id':99},'test')['id']
  self.assertEqual(self.s.db.query('SELECT balance FROM member_accounts WHERE user_id=?',(member,))[0]['balance'],0)
  with self.assertRaises(ValueError):finance.create(self.s,{'username':'created_member','password':'new-password-123'},{'id':99},'test')


class MemberFinanceAccessTests(unittest.TestCase):
 setup_username='admin'
 setUp=auth_fixture.AuthTests.setUp
 tearDown=auth_fixture.AuthTests.tearDown
 setup=auth_fixture.AuthTests.setup
 invite=auth_fixture.AuthTests.invite
 member=auth_fixture.AuthTests.member

 def test_only_administrators_can_read_orders_or_change_capital(self):
  self.setup();invite=self.invite();member,_=self.member(invite['code'])
  for path in ('member-orders','member-ledger'):
   self.assertEqual(member.call('/api/admin/'+path)[0],403)
  body={'id':2,'amount':'100','mode':'add','note':'not allowed','request_id':'access-test-request'}
  self.assertEqual(member.call('/api/admin/member-capital',body)[0],403)
  self.assertEqual(member.call('/api/admin/member-create',{'username':'not_allowed','password':'new-password-123'})[0],403)
  status,d=self.admin.call('/api/admin/member-create',{'username':'admin_created_member','password':'new-password-123'})
  self.assertEqual(status,200);uid=d['id']
  status,d=self.admin.call('/api/admin/member-capital',{**body,'id':uid})
  self.assertEqual(status,200)
  status,d=self.admin.call('/api/admin/users',None);row=next(r for r in d['rows'] if r['id']==uid)
  self.assertEqual((row['principal'],row['balance']),(10000,10000))
  self.assertEqual(self.admin.call('/api/admin/member-orders?direction=invalid')[0],400)
  self.assertEqual(self.admin.call('/api/admin/member-orders?start=2026-02-02T00:00&end=2026-02-01T00:00')[0],400)


if __name__=='__main__':unittest.main()
