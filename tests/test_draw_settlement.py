import json,time,unittest
from datetime import datetime,timedelta,timezone
from unittest.mock import patch
from test_robot_signals import RobotTests
from pc28 import robot_risk
from pc28.member_orders import sync
from pc28.order_confirmation import receive,UI_SUBMISSION_ATTEMPTED

class DrawSettlementTests(unittest.TestCase):
    setUp=RobotTests.setUp
    tearDown=RobotTests.tearDown
    permit=RobotTests.permit
    member=RobotTests.member
    order=RobotTests.order
    ready=RobotTests.ready

    def prepare(self,submitted=True):
        self.s.robot=self.r
        self.s.cfg['deployment']={'public_origin':'https://example.test'}
        self.permit('size');self.permit('parity')
        self.order('size');self.order('parity','单')
        self.r.capture();self.ready()
        self.now=datetime.now(timezone.utc)
        previous=self.now-timedelta(seconds=50)
        self.due=previous+timedelta(seconds=210)
        self.s.db.import_records([{'id':998,'time':previous.isoformat(),'numbers':list(range(1,21))}],'test')
        with self.s.db.tx() as c:
            self.bid=c.execute('SELECT bot_id FROM robot_outbox LIMIT 1').fetchone()[0]
            robot_risk.save(c,self.bid,{'capital':100000,'loss_mode':'percent','loss_limit':5000,
                'profit_target':50000,'trailing_bps':5000})
            c.execute("UPDATE robot_platforms SET execution_required=1 WHERE id=1")
            c.execute("INSERT OR REPLACE INTO runtime_state VALUES('draw_timer',?,?)",
                      (json.dumps({'next_draw_id':999,'next_draw_at':self.due.isoformat()}),self.now.isoformat()))
        with patch('pc28.maintenance.live_guard',return_value=''),patch('pc28.robot_signals.urllib.request.build_opener') as opener:
            opener.return_value.open.return_value.__enter__.return_value.status=202
            self.assertTrue(self.r.deliver_one())
            self.wire=json.loads(opener.return_value.open.call_args.args[0].data)
        self.body={'platform_id':1,'event_id':self.wire['event_id'],'batch_id':self.wire['event_id']}
        if submitted:
            receive(self.s,'Bearer test-token-not-real-123',{**self.body,'status':'unknown','detail':UI_SUBMISSION_ATTEMPTED})
        self.s.db.import_records([{'id':999,'time':self.due.isoformat(),'numbers':list(range(1,21))}],'test')

    def sync_at(self,at):
        with patch('pc28.member_orders.utcnow',return_value=at.isoformat()):
            sync(self.s)

    def test_draw_settles_releases_once_and_late_confirmation_does_not_duplicate(self):
        self.prepare()
        self.sync_at(self.due-timedelta(seconds=1))
        self.assertEqual(self.s.db.query('SELECT * FROM member_orders'),[])
        self.sync_at(self.due+timedelta(seconds=1))
        orders=self.s.db.query('SELECT status,pnl FROM member_orders')
        self.assertEqual(len(orders),2)
        self.assertEqual({r['status'] for r in orders},{'settled'})
        self.assertEqual(self.s.db.query('SELECT * FROM robot_risk_holds'),[])
        with self.s.db.tx() as c:
            self.assertEqual(robot_risk.state(c,self.bid)['available'],100000+sum(r['pnl'] for r in orders))
        ledger=len(self.s.db.query('SELECT * FROM member_cash_ledger'))
        self.sync_at(self.due+timedelta(seconds=2))
        receive(self.s,'Bearer test-token-not-real-123',{**self.body,'status':'executed','executed_at':int(time.time())})
        self.assertEqual(len(self.s.db.query('SELECT * FROM member_cash_ledger')),ledger)

    def test_late_failure_reverses_profit_and_pauses_bot_once(self):
        self.prepare();self.sync_at(self.due+timedelta(seconds=1))
        body={**self.body,'status':'failed','detail':'下单失败'}
        result=receive(self.s,'Bearer test-token-not-real-123',body)
        self.assertFalse(result['valid'])
        self.assertEqual({r['outcome'] for r in self.s.db.query('SELECT outcome FROM member_orders')},{'execution_rejected'})
        self.assertEqual(len(self.s.db.query("SELECT * FROM member_cash_ledger WHERE kind='execution_reversal'")),2)
        self.assertTrue(receive(self.s,'Bearer test-token-not-real-123',body)['duplicate'])
        with self.s.db.tx() as c:
            risk=robot_risk.state(c,self.bid)
            self.assertEqual(risk['available'],100000)
            self.assertTrue(risk['paused'])

    def test_calibration_after_draw_releases_without_rebooking_old_results(self):
        self.prepare()
        with patch('pc28.model.utcnow',return_value=(self.due+timedelta(seconds=10)).isoformat()),self.s.db.tx() as c:
            robot_risk.calibrate(c,self.bid,50000)
        self.sync_at(self.due+timedelta(seconds=20))
        self.assertEqual(self.s.db.query('SELECT * FROM member_orders'),[])
        self.assertEqual(self.s.db.query('SELECT * FROM robot_risk_holds'),[])
        self.assertEqual({r['status'] for r in self.s.db.query('SELECT status FROM robot_execution')},{'calibrated'})
        with self.s.db.tx() as c:self.assertEqual(robot_risk.state(c,self.bid)['available'],50000)

    def test_queued_only_and_conflicting_results_do_not_settle(self):
        self.prepare(submitted=False)
        self.sync_at(self.due+timedelta(seconds=1))
        self.assertEqual(self.s.db.query('SELECT * FROM member_orders'),[])
        receive(self.s,'Bearer test-token-not-real-123',{**self.body,'status':'unknown','detail':UI_SUBMISSION_ATTEMPTED})
        with self.s.db.tx() as c:c.execute('INSERT INTO conflicts(draw_id,resolved) VALUES(999,0)')
        self.sync_at(self.due+timedelta(seconds=2))
        self.assertEqual(self.s.db.query('SELECT * FROM member_orders'),[])
        self.assertEqual(len(self.s.db.query('SELECT * FROM robot_risk_holds')),2)
        with self.s.db.tx() as c:c.execute('UPDATE conflicts SET resolved=1')
        self.sync_at(self.due+timedelta(seconds=3))
        self.assertEqual(len(self.s.db.query('SELECT * FROM member_orders')),2)
