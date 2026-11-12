import unittest
from test_robot_accounts import AccountTests
from pc28 import robot_risk
from pc28.member_orders import entry


class CalibratedRiskTests(unittest.TestCase):
    setUp=AccountTests.setUp
    tearDown=AccountTests.tearDown
    member=AccountTests.member
    body=AccountTests.body
    create=AccountTests.create
    policy={'capital':100000,'loss_mode':'percent','loss_limit':5000,
            'profit_target':50000,'trailing_bps':5000}

    def context(self):
        account=self.r.account_detail(self.create())
        return account['user_id'],account['bots'][0]['id']

    def order(self,c,uid,bid,oid,stake,pnl=None):
        c.execute("""INSERT INTO member_orders(id,user_id,outbox_id,target_draw,direction,stake,
            placed_at,accepted_at,odds_json,status,pnl,bot_id)
            VALUES(?,?,?,999,'大',?,'t','t','{}',?,?,?)""",
                  (oid,uid,oid,stake,'pending' if pnl is None else 'settled',pnl,bid))
        entry(c,uid,'stake',-stake,order_id=oid)
        if pnl is not None:entry(c,uid,'settle',stake+pnl,order_id=oid)

    def test_available_calibration_and_pending_settlement_are_not_double_counted(self):
        uid,bid=self.context()
        with self.s.db.tx() as c:
            robot_risk.save(c,bid,self.policy)
            self.order(c,uid,bid,1,10000,-10000)
            self.order(c,uid,bid,2,20000)
            risk=robot_risk.save(c,bid,{**self.policy,'calibration_amount':70000,'fixed_stop_balance':20000})
            self.assertEqual((risk['risk_capital'],risk['net'],risk['available']),(70000,0,70000))
            c.execute("UPDATE member_orders SET status='settled',pnl=20000 WHERE id=2")
            entry(c,uid,'settle',40000,order_id=2)
            risk=robot_risk.check(c,bid)
            self.assertEqual((risk['funds'],risk['available']),(110000,110000))
            self.order(c,uid,bid,3,10000)
            self.assertEqual(robot_risk.state(c,bid)['available'],100000)
            c.execute("UPDATE member_orders SET status='settled',pnl=-10000 WHERE id=3")
            entry(c,uid,'settle',0,order_id=3)
            self.assertEqual(robot_risk.check(c,bid)['available'],100000)
            before=robot_risk.state(c,bid)
            after=robot_risk.save(c,bid,self.policy)
            self.assertEqual(after['available'],before['available'])
            self.assertEqual(after['calibration_baseline'],before['calibration_baseline'])
            self.assertEqual(after['fixed_stop_balance'],20000)
            robot_risk.migrate(c);robot_risk.migrate(c)
            self.assertEqual(robot_risk.state(c,bid)['available'],100000)

    def test_absolute_floor_is_strict_and_cannot_be_bypassed_by_reset(self):
        uid,bid=self.context()
        with self.s.db.tx() as c:
            risk=robot_risk.save(c,bid,{**self.policy,'calibration_amount':50000,'fixed_stop_balance':50000})
            self.assertFalse(risk['paused'])
            self.order(c,uid,bid,1,1,-1)
            risk=robot_risk.check(c,bid)
            self.assertIn('499.99',risk['paused']);self.assertIn('500.00',risk['paused'])
            with self.assertRaises(ValueError):robot_risk.resume(c,bid,True)
            risk=robot_risk.save(c,bid,{**self.policy,'calibration_amount':60000,'fixed_stop_balance':50000})
            self.assertTrue(risk['paused'])
            risk=robot_risk.resume(c,bid,True)
            self.assertFalse(risk['paused']);self.assertEqual(risk['available'],60000)
            self.assertEqual(risk['risk_capital'],60000)

    def test_batch_reservation_checks_absolute_floor_and_retry_is_idempotent(self):
        uid,bid=self.context()
        with self.s.db.tx() as c:
            robot_risk.save(c,bid,{**self.policy,'calibration_amount':60000,'fixed_stop_balance':50000})
            self.assertIn('固定余额止损线',robot_risk.admit(c,bid,[{'id':901,'push_cents':15000}]))
            self.assertEqual(robot_risk.state(c,bid)['held'],0)
            self.assertEqual(robot_risk.admit(c,bid,[{'id':902,'push_cents':10000}]),'')
            self.assertEqual(robot_risk.admit(c,bid,[{'id':902,'push_cents':10000}]),'')
            self.assertEqual(robot_risk.state(c,bid)['available'],50000)
            self.assertFalse(robot_risk.check(c,bid)['paused'])
            self.assertIn('固定余额止损线',robot_risk.admit(c,bid,[{'id':903,'push_cents':1}]))

    def test_new_amounts_validate_before_writing(self):
        uid,bid=self.context()
        with self.s.db.tx() as c:
            for field,value in [('calibration_amount',-1),('calibration_amount',True),('fixed_stop_balance',-1),('fixed_stop_balance',1.5)]:
                with self.assertRaises(ValueError):robot_risk.save(c,bid,{**self.policy,field:value})
            self.assertIsNone(robot_risk.state(c,bid))

    def test_calibration_covers_existing_holds_and_late_settlement(self):
        uid,bid=self.context()
        with self.s.db.tx() as c:
            c.execute("""INSERT INTO robot_outbox(id,event_id,name,event_time,created,request,message,status,attempts,bot_id,push_cents,sent_at)
                VALUES(901,'calibrated-hold','bot',1,'t','{}','大24490','sent',1,?,2449000,'2026-11-11T12:00:00+00:00')""",(bid,))
            c.execute('INSERT INTO robot_risk_holds VALUES(901,?,2449000)',(bid,))
            c.execute("INSERT INTO robot_execution(outbox_id,status,updated,deadline) VALUES(901,'submitted','t',9999999999)")
            risk=robot_risk.save(c,bid,{**self.policy,'capital':50000000,'calibration_amount':483000,'fixed_stop_balance':450000})
            self.assertEqual((risk['available'],risk['risk_capital'],risk['held']),(483000,483000,2449000))
            self.assertFalse(risk['paused'])
            from pc28.member_orders import reserved
            c.execute('UPDATE robot_outbox SET member_user_id=? WHERE id=901',(uid,))
            self.assertEqual(reserved(c,uid),0)
            self.assertIn('固定余额止损线',robot_risk.admit(c,bid,[{'id':902,'push_cents':33001}]))
            self.order(c,uid,bid,901,2449000)
            c.execute('DELETE FROM robot_risk_holds WHERE outbox_id=901')
            self.assertEqual(robot_risk.state(c,bid)['available'],483000)
            c.execute("UPDATE member_orders SET status='settled',pnl=-2449000 WHERE id=901")
            entry(c,uid,'settle',0,order_id=901)
            self.assertEqual(robot_risk.check(c,bid)['available'],483000)
            self.assertFalse(robot_risk.state(c,bid)['paused'])
            c.execute('UPDATE robot_risk SET calibration_revision=0 WHERE bot_id=?',(bid,))
            robot_risk.migrate(c);robot_risk.migrate(c)
            self.assertEqual(robot_risk.state(c,bid)['available'],483000)

    def test_member_gate_counts_other_bots_holds_and_retries(self):
        from pc28.member_orders import admit
        uid,bid=self.context()
        with self.s.db.tx() as c:
            c.execute('UPDATE member_accounts SET balance=10000 WHERE user_id=?',(uid,))
            for oid,amount in [(901,6000),(902,5000)]:
                c.execute("""INSERT INTO robot_outbox(id,event_id,name,event_time,created,request,message,status,attempts,bot_id,push_cents,member_user_id)
                    VALUES(?,?,'bot',1,'t','{}','大10','pending',0,?,?,?)""",(oid,str(oid),bid,amount,uid))
            rows=c.execute('SELECT * FROM robot_outbox ORDER BY id').fetchall()
            self.assertIn('余额不足',admit(c,rows))
            self.assertEqual(admit(c,rows[:1]),'')
            c.execute('INSERT INTO robot_risk_holds VALUES(901,?,6000)',(bid,))
            self.assertEqual(admit(c,rows[:1]),'')
            self.assertIn('余额不足',admit(c,rows[1:]))
            c.execute('UPDATE member_accounts SET balance=-1 WHERE user_id=?',(uid,))
            self.assertIn('余额不足',admit(c,rows[:1]))
            c.execute('UPDATE member_accounts SET balance=0 WHERE user_id=?',(uid,))
            self.assertIn('余额不足',admit(c,rows[:1]))
