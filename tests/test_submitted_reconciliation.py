import unittest,time,json
import test_robot_accounts as fixture
from pc28 import robot_risk
from pc28.order_confirmation import expire,reconcile_submission_receipts

class SubmittedReconciliationTests(unittest.TestCase):
 setUp=fixture.AccountTests.setUp
 tearDown=fixture.AccountTests.tearDown
 member=fixture.AccountTests.member
 body=fixture.AccountTests.body
 create=fixture.AccountTests.create
 def seed(self,status='failed',execution='submitted',due=-10):
  account=self.r.account_detail(self.create());bot=account['bots'][0];bid=bot['id']
  with self.s.db.tx() as c:
   c.execute("INSERT INTO robot_risk(bot_id,capital,loss_mode,loss_limit,profit_target,trailing_bps,baseline) VALUES(?,100000,'percent',5000,50000,5000,0)",(bid,))
   oid=c.execute("INSERT INTO robot_outbox(event_id,name,strategy_id,target_draw,event_time,created,request,message,status,attempts,is_test,follow_account_id,bot_id,push_cents,recipient_name) VALUES('reconcile-fixture','fixture','size',999,?,'t','{}','小7',?,1,0,?,?,700,'fixture')",(int(time.time()),status,account['id'],bid)).lastrowid
   c.execute('INSERT INTO robot_execution(outbox_id,status,updated,deadline) VALUES(?,?,?,?)',(oid,execution,'t',int(time.time())+due))
   c.execute('INSERT INTO robot_risk_holds VALUES(?,?,700)',(oid,bid))
  return bid,oid
 def test_failed_submitted_visible_without_releasing_balance(self):
  bid,oid=self.seed(due=300)
  with self.s.db.tx() as c:
   r=robot_risk.state(c,bid);self.assertEqual(r['unresolved'],[oid]);self.assertEqual(r['held'],700);self.assertEqual(r['uncertain_orders'][0]['message'],'小7')
  record=self.r.records({})['rows'][0];self.assertEqual(record['needs_reconciliation'],1)
  with self.assertRaisesRegex(ValueError,'执行状态待核对'):self.r.retry(oid)
 def test_active_submission_not_reconcilable_before_deadline(self):
  bid,oid=self.seed(status='sent',due=300)
  with self.s.db.tx() as c:
   self.assertEqual(robot_risk.state(c,bid)['uncertain'],0)
   with self.assertRaises(ValueError):robot_risk.reconcile_rejected(c,bid,oid)
   self.assertEqual(c.execute('SELECT count(*) FROM robot_risk_holds').fetchone()[0],1)
 def test_submitted_timeout_marks_review_never_assumes_failure(self):
  bid,oid=self.seed(status='sent')
  with self.s.db.tx() as c:
   expire(c);expire(c)
   row=c.execute('SELECT status,detail FROM robot_execution WHERE outbox_id=?',(oid,)).fetchone()
   self.assertEqual(row['status'],'unconfirmed');self.assertIn('禁止重复发送',row['detail'])
   self.assertEqual(robot_risk.state(c,bid)['uncertain'],1);self.assertEqual(robot_risk.state(c,bid)['held'],700)
   self.assertEqual(c.execute('SELECT count(*) FROM member_orders').fetchone()[0],0)
 def test_explicit_reconciliation_releases_only_verified_record(self):
  bid,oid=self.seed()
  with self.s.db.tx() as c:
   robot_risk.reconcile_rejected(c,bid,oid)
   self.assertEqual(c.execute('SELECT status FROM robot_execution WHERE outbox_id=?',(oid,)).fetchone()[0],'rejected')
   self.assertEqual(robot_risk.state(c,bid)['held'],0)
 def test_timely_authenticated_submission_repairs_transport_only(self):
  from datetime import datetime,timezone
  bid,oid=self.seed();now=int(time.time());receipt=datetime.fromtimestamp(now-10,timezone.utc).isoformat();sent=datetime.fromtimestamp(now-20,timezone.utc).isoformat()
  with self.s.db.tx() as c:
   c.execute("UPDATE robot_outbox SET sent_at=?,last_status=400 WHERE id=?",(sent,oid))
   c.execute('UPDATE robot_execution SET updated=? WHERE outbox_id=?',(receipt,oid))
   c.execute("INSERT INTO robot_batches(id,request,created) VALUES('receipt-fixture',?,'t')",(json.dumps({'expires_at':now+30}),))
   c.execute("INSERT INTO robot_batch_items(batch_id,outbox_id) VALUES('receipt-fixture',?)",(oid,))
   self.assertEqual(reconcile_submission_receipts(c),1);self.assertEqual(reconcile_submission_receipts(c),0)
   o=c.execute('SELECT status,last_status,accepted_at FROM robot_outbox WHERE id=?',(oid,)).fetchone()
   self.assertEqual(tuple(o),('sent',400,receipt));self.assertEqual(c.execute('SELECT count(*) FROM member_orders').fetchone()[0],0)
   self.assertEqual(c.execute('SELECT status FROM robot_execution WHERE outbox_id=?',(oid,)).fetchone()[0],'submitted')
 def test_late_submission_or_unconfirmed_does_not_prove_acceptance(self):
  from datetime import datetime,timezone
  bid,oid=self.seed();now=int(time.time());at=datetime.fromtimestamp(now,timezone.utc).isoformat()
  with self.s.db.tx() as c:
   c.execute('UPDATE robot_outbox SET sent_at=?,last_status=400 WHERE id=?',(at,oid))
   c.execute('UPDATE robot_execution SET updated=? WHERE outbox_id=?',(at,oid))
   c.execute("INSERT INTO robot_batches(id,request,created) VALUES('late-fixture',?,'t')",(json.dumps({'expires_at':now-1}),))
   c.execute("INSERT INTO robot_batch_items(batch_id,outbox_id) VALUES('late-fixture',?)",(oid,))
   self.assertEqual(reconcile_submission_receipts(c),0)
 def test_repaired_receipt_settles_once_without_resending_or_resetting_plan(self):
  from datetime import datetime,timezone
  from pc28.member_orders import settle_sent,settle
  bid,oid=self.seed();now=int(time.time())
  iso=lambda seconds:datetime.fromtimestamp(seconds,timezone.utc).isoformat()
  with self.s.db.tx() as c:
   uid=c.execute('SELECT user_id FROM robot_accounts WHERE id=(SELECT follow_account_id FROM robot_outbox WHERE id=?)',(oid,)).fetchone()[0]
   c.execute('UPDATE robot_outbox SET sent_at=?,last_status=400,member_user_id=? WHERE id=?',(iso(now-60),uid,oid))
   c.execute('UPDATE robot_execution SET updated=? WHERE outbox_id=?',(iso(now-50),oid))
   c.execute("INSERT INTO robot_batches(id,request,created) VALUES('settle-fixture',?,'t')",(json.dumps({'expires_at':now-40}),))
   c.execute("INSERT INTO robot_batch_items(batch_id,outbox_id) VALUES('settle-fixture',?)",(oid,))
   c.execute("INSERT INTO draws(id,source_time,draw_utc,numbers,a,b,c,total,size,parity,first_seen,source,raw_hash,verification) VALUES(999,?,?,'[]',1,2,3,6,'小','双','t','test','t','full_keno')",(iso(now-30),iso(now-30)))
   reconcile_submission_receipts(c);expire(c);settle_sent(c,self.s);settle(c);settle_sent(c,self.s);settle(c)
   self.assertEqual(c.execute('SELECT status FROM robot_execution WHERE outbox_id=?',(oid,)).fetchone()[0],'draw_settled')
   self.assertEqual(c.execute('SELECT count(*) FROM robot_risk_holds WHERE outbox_id=?',(oid,)).fetchone()[0],0)
   self.assertEqual(c.execute('SELECT count(*) FROM member_orders WHERE outbox_id=?',(oid,)).fetchone()[0],1)
   self.assertEqual(c.execute('SELECT count(*) FROM member_cash_ledger WHERE order_id IN (SELECT id FROM member_orders WHERE outbox_id=?)',(oid,)).fetchone()[0],2)
   c.execute("UPDATE robot_execution SET status='unconfirmed' WHERE outbox_id=?",(oid,))
   self.assertEqual(reconcile_submission_receipts(c),0)

if __name__=='__main__':unittest.main()
