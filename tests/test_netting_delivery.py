import json
import time
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch
import test_robot_signals as fixture
from pc28 import member_orders


class NettingDeliveryTests(unittest.TestCase):
    setUp = fixture.RobotTests.setUp
    tearDown = fixture.RobotTests.tearDown
    member = fixture.RobotTests.member
    permit = fixture.RobotTests.permit
    order = fixture.RobotTests.order
    ready = fixture.RobotTests.ready

    def prepare(self, small):
        self.permit('size'); self.permit('parity')
        self.order('size', '大'); self.order('parity', '小')
        self.r.capture()
        with self.s.db.tx() as c:
            c.execute("UPDATE robot_outbox SET message=?,push_cents=? WHERE message LIKE '小%'", ('小'+str(small/100), small))
        self.ready()

    def deliver(self, status=202):
        deadline = {'draw_at': int(time.time())+60, 'expires_at': int(time.time())+50}
        with patch('pc28.maintenance.live_guard', return_value=''), patch('pc28.robot_signals.draw_deadline', return_value=deadline), patch('pc28.robot_signals.urllib.request.build_opener') as build:
            build.return_value.open.return_value.__enter__.return_value.status = status
            if status>=400:
                import urllib.error
                build.return_value.open.side_effect=urllib.error.HTTPError('http://receiver.test',status,'test',{},None)
            self.r.deliver_one()
            request = build.return_value.open.call_args
            return json.loads(request.args[0].data) if request else None

    def test_full_offset_no_http_no_cash(self):
        self.prepare(1000)
        self.assertIsNone(self.deliver())
        orders=self.s.db.query('SELECT * FROM member_orders')
        self.assertEqual(len(orders),2)
        self.assertTrue(all(o['stake']==0 and o['outcome']=='offset' for o in orders))
        self.assertEqual(self.s.db.query("SELECT COUNT(*) n FROM member_cash_ledger WHERE kind='stake'")[0]['n'],0)

    def test_partial_sends_net_and_preserves_original(self):
        self.prepare(500)
        wire=self.deliver()
        self.assertEqual(wire['payload']['text'],'大5')
        self.assertNotIn('orders',wire)
        self.assertNotIn('execution_callback',wire)
        orders=self.s.db.query('SELECT * FROM member_orders ORDER BY id')
        self.assertEqual([o['stake'] for o in orders],[500,0])
        self.assertEqual([o['original_stake'] for o in orders],[1000,500])
        self.assertEqual(len(self.s.db.query('SELECT * FROM robot_risk_holds')),0)
        self.assertIsNone(self.deliver())

    def test_rejection_does_not_debit(self):
        self.prepare(500)
        self.deliver(401)
        self.assertEqual(self.s.db.query('SELECT * FROM member_orders'),[])

    def test_retry_reuses_frozen_net_and_event(self):
        self.prepare(500)
        first=self.deliver(503)
        self.assertEqual(self.s.db.query('SELECT * FROM member_orders'),[])
        with self.s.db.tx() as c:
            c.execute("UPDATE robot_outbox SET next_attempt=0 WHERE status='retry'")
        second=self.deliver()
        self.assertEqual(first['event_id'],second['event_id'])
        self.assertEqual(first['payload'],second['payload'])
        self.assertEqual(len(self.s.db.query('SELECT * FROM member_orders')),2)

    def test_records_show_actual_and_original(self):
        self.prepare(500);self.deliver()
        row=self.r.records({})['rows'][0]
        self.assertEqual(row['message'],'大5')
        self.assertIn('小',row['original_message'])
        detail=self.r.batch_detail(row['id'])
        self.assertEqual(sum(i['execution_cents'] for i in detail['items']),500)

    def test_zero_and_27_net_settlement(self):
        from pc28.model import settlement
        odds=self.s.cfg['odds']
        for total,hit in ((0,False),(27,True)):
            original=sum(settlement(stake,d,total,h,odds)['pnl'] for stake,d,h in ((1000,'大',hit),(500,'小',not hit)))
            net=settlement(500,'大',total,hit,odds)['pnl']
            self.assertEqual(net-original,500)

    def test_zero_draw_updates_cash_once(self):
        self.prepare(500);self.deliver()
        before=self.s.db.query('SELECT balance FROM member_accounts')[0]['balance']
        at=datetime.now(timezone.utc)+timedelta(seconds=1)
        self.s.db.import_records([{'id':999,'time':at.isoformat(),'numbers':list(range(1,21))}],'test')
        with self.s.db.tx() as c:
            c.execute("UPDATE draws SET a=0,b=0,c=0,total=0,size='小',parity='双' WHERE id=999")
            member_orders.settle(c,(at+timedelta(seconds=1)).isoformat())
            member_orders.settle(c,(at+timedelta(seconds=1)).isoformat())
        self.assertEqual(self.s.db.query('SELECT balance FROM member_accounts')[0]['balance'],before)
        self.assertEqual(sum(o['pnl'] for o in self.s.db.query('SELECT * FROM member_orders')),-500)
        self.assertEqual(len(self.s.db.query("SELECT * FROM member_cash_ledger WHERE kind='settle'")),1)
