import unittest
from test_calibrated_risk import CalibratedRiskTests as Fixture
from pc28 import robot_risk


class MergedRobotRiskTests(unittest.TestCase):
    setUp=Fixture.setUp
    tearDown=Fixture.tearDown
    member=Fixture.member
    body=Fixture.body
    create=Fixture.create
    context=Fixture.context
    order=Fixture.order
    policy=Fixture.policy

    def add(self,c,uid,bid,oid,draw,pnl):
        self.order(c,uid,bid,oid,100,pnl)
        c.execute('UPDATE member_orders SET target_draw=?,outcome=? WHERE id=?',(draw,None if pnl is None else 'win' if pnl>0 else 'loss' if pnl<0 else 'refund',oid))

    def test_all_lost_by_draw_and_independent_pause(self):
        uid,bid=self.context()
        with self.s.db.tx() as c:
            robot_risk.save(c,bid,{**self.policy,'merged_loss_limit':2,'merged_pause_draws':7,'pause_draws':20})
            self.add(c,uid,bid,1,100,-100)
            self.add(c,uid,bid,2,100,150)
            self.assertEqual(robot_risk.state(c,bid)['merged_loss_streak'],0)
            self.add(c,uid,bid,3,101,-100)
            self.add(c,uid,bid,4,101,-100)
            self.assertEqual(robot_risk.state(c,bid)['merged_loss_streak'],1)
            self.assertFalse(robot_risk.check(c,bid)['paused'])
            self.add(c,uid,bid,5,102,-100)
            risk=robot_risk.check(c,bid)
            self.assertEqual(risk['pause_until_draw'],109)
            self.assertIn('合并订单',risk['paused'])
            # Repeated checks do not extend the pause.
            self.assertEqual(robot_risk.check(c,bid)['pause_until_draw'],109)
            c.execute("INSERT INTO draws(id,source_time,numbers,first_seen,source,raw_hash) VALUES(108,'t','[]','t','test','test')")
            self.assertTrue(robot_risk.check(c,bid)['paused'])
            c.execute("INSERT INTO draws(id,source_time,numbers,first_seen,source,raw_hash) VALUES(109,'t','[]','t','test','test')")
            restored=robot_risk.check(c,bid)
            self.assertFalse(restored['paused'])
            self.assertEqual(restored['merged_loss_streak'],0)
            self.add(c,uid,bid,6,110,-100)
            self.assertEqual(robot_risk.check(c,bid)['merged_loss_streak'],1)
            self.assertFalse(robot_risk.check(c,bid)['paused'])

    def test_partial_settlement_blocks_later_draws_and_flat_resets(self):
        uid,bid=self.context()
        with self.s.db.tx() as c:
            robot_risk.save(c,bid,{**self.policy,'merged_loss_limit':2})
            self.add(c,uid,bid,1,100,-100)
            self.add(c,uid,bid,2,100,None)
            self.add(c,uid,bid,3,101,-100)
            self.assertEqual(robot_risk.state(c,bid)['merged_loss_streak'],0)
            c.execute("UPDATE member_orders SET status='settled',pnl=100,outcome='win' WHERE id=2")
            self.assertEqual(robot_risk.state(c,bid)['merged_loss_streak'],1)
            self.add(c,uid,bid,4,102,0)
            self.assertEqual(robot_risk.state(c,bid)['merged_loss_streak'],0)

    def test_defaults_validation_and_manual_resume(self):
        uid,bid=self.context()
        with self.s.db.tx() as c:
            risk=robot_risk.save(c,bid,self.policy)
            self.assertEqual(risk['merged_loss_limit'],0)
            with self.assertRaises(ValueError):
                robot_risk.save(c,bid,{**self.policy,'merged_pause_draws':0})
            robot_risk.save(c,bid,{**self.policy,'merged_loss_limit':1})
            self.add(c,uid,bid,1,100,-100)
            self.assertTrue(robot_risk.check(c,bid)['paused'])
            resumed=robot_risk.resume(c,bid,False)
            self.assertEqual(resumed['merged_loss_streak'],0)
            self.assertFalse(robot_risk.check(c,bid)['paused'])

    def test_winning_leg_breaks_streak_even_when_combined_money_loses(self):
        uid,bid=self.context()
        with self.s.db.tx() as c:
            robot_risk.save(c,bid,{**self.policy,'merged_loss_limit':2})
            self.add(c,uid,bid,1,100,-100)
            self.assertEqual(robot_risk.state(c,bid)['merged_loss_streak'],1)
            self.add(c,uid,bid,2,101,-200)
            self.add(c,uid,bid,3,101,10)
            self.assertEqual(robot_risk.state(c,bid)['merged_loss_streak'],0)
            self.assertFalse(robot_risk.check(c,bid)['paused'])
            self.add(c,uid,bid,4,102,-100)
            self.add(c,uid,bid,5,102,0)
            self.assertEqual(robot_risk.state(c,bid)['merged_loss_streak'],0)
            self.add(c,uid,bid,6,103,-100)
            self.add(c,uid,bid,7,103,-100)
            self.assertEqual(robot_risk.state(c,bid)['merged_loss_streak'],1)
