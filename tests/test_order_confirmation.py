import json
import hmac
import time
import unittest
import urllib.error
from io import BytesIO
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from pc28.auth import AuthError
from pc28.notifications import hourly_ratios
from pc28.order_confirmation import UI_SUBMISSION_ATTEMPTED, expire, receive
from test_robot_signals import RobotTests


class ConfirmationTests(unittest.TestCase):
    setUp = RobotTests.setUp
    tearDown = RobotTests.tearDown
    permit = RobotTests.permit
    member = RobotTests.member
    order = RobotTests.order
    ready = RobotTests.ready

    def test_batch_confirmation_applies_once_to_all_member_orders(self):
        self.s.robot=self.r
        self.s.cfg['deployment']={'public_origin':'https://example.test'}
        self.permit('size')
        self.permit('parity')
        self.order('size')
        self.order('parity','单')
        self.assertEqual(self.r.capture(),2)
        self.ready()
        now=datetime.now(timezone.utc)
        previous=now-timedelta(seconds=50)
        self.s.db.import_records([{'id':998,'time':previous.isoformat(),'numbers':list(range(1,21))}],'test')
        with self.s.db.tx() as c:
            c.execute("UPDATE robot_platforms SET execution_required=1 WHERE id=1")
            plan={'next_draw_id':999,'next_draw_at':(previous+timedelta(seconds=210)).isoformat()}
            c.execute("INSERT OR REPLACE INTO runtime_state VALUES('draw_timer',?,?)",(json.dumps(plan),now.isoformat()))
            c.execute("INSERT INTO notice_subscriptions(name,chat_id,events,enabled) VALUES('test','1',?,1)",(json.dumps(['order']),))
        with patch('pc28.maintenance.live_guard',return_value=''),patch('pc28.robot_signals.urllib.request.build_opener') as opener:
            opener.return_value.open.return_value.__enter__.return_value.status=202
            self.assertTrue(self.r.deliver_one())
            wire=json.loads(opener.return_value.open.call_args.args[0].data)
        self.assertEqual(len(wire['orders']),2)
        self.assertEqual(wire['execution_callback']['version'],2)
        self.assertEqual(self.s.db.query('SELECT * FROM member_orders'),[])
        self.assertEqual({r['status'] for r in self.s.db.query('SELECT status FROM robot_execution')},{'awaiting'})
        auth='Bearer test-token-not-real-123'
        body={'platform_id':1,'event_id':wire['event_id'],'batch_id':wire['event_id'],
              'status':'executed','executed_at':int(time.time())}
        with self.assertRaises(AuthError):receive(self.s,'Bearer invalid',body)
        scoped=hmac.digest(b'test-token-not-real-123',
                           '\n'.join(('1',wire['event_id'],wire['event_id'])).encode(),'sha256').hex()
        submitted={**body,'status':'unknown','detail':UI_SUBMISSION_ATTEMPTED}
        self.assertEqual(receive(self.s,'PC28-Order '+scoped,submitted)['orders'],2)
        self.assertTrue(receive(self.s,'PC28-Order '+scoped,submitted)['duplicate'])
        self.assertEqual({r['status'] for r in self.s.db.query('SELECT status FROM robot_execution')},{'submitted'})
        self.assertEqual(self.s.db.query('SELECT * FROM member_orders'),[])
        self.assertEqual(receive(self.s,'PC28-Order '+scoped,body)['orders'],2)
        self.assertTrue(receive(self.s,auth,body)['duplicate'])
        with self.assertRaises(AuthError):receive(self.s,auth,{**body,'status':'failed'})
        self.assertEqual(len(self.s.db.query('SELECT * FROM member_orders')),2)
        self.assertEqual(self.s.db.query('SELECT * FROM notice_queue'),[])

    def test_opaque_http_400_keeps_batch_unconfirmed_and_held(self):
        self.s.robot=self.r
        self.s.cfg['deployment']={'public_origin':'https://example.test'}
        self.permit('size');self.permit('parity')
        self.order('size');self.order('parity','单')
        self.r.capture();self.ready()
        now=datetime.now(timezone.utc);previous=now-timedelta(seconds=50)
        self.s.db.import_records([{'id':998,'time':previous.isoformat(),'numbers':list(range(1,21))}],'test')
        with self.s.db.tx() as c:
            c.execute("UPDATE robot_platforms SET execution_required=1 WHERE id=1")
            plan={'next_draw_id':999,'next_draw_at':(previous+timedelta(seconds=210)).isoformat()}
            c.execute("INSERT OR REPLACE INTO runtime_state VALUES('draw_timer',?,?)",(json.dumps(plan),now.isoformat()))
            c.execute("INSERT INTO notice_subscriptions(name,chat_id,events,enabled) VALUES('test','1','[\"order\"]',1)")
            c.execute("INSERT INTO robot_risk_holds SELECT id,987,push_cents FROM robot_outbox")
        error=urllib.error.HTTPError('https://receiver.test',400,'Bad Request',{},BytesIO(b'{"error":"internal enqueue error"}'))
        with patch('pc28.maintenance.live_guard',return_value=''),patch('pc28.robot_signals.urllib.request.build_opener') as opener:
            opener.return_value.open.side_effect=error
            self.assertTrue(self.r.deliver_one())
            wire=json.loads(opener.return_value.open.call_args.args[0].data)
        self.assertEqual({r['status'] for r in self.s.db.query('SELECT status FROM robot_execution')},{'unconfirmed'})
        self.assertEqual(len(self.s.db.query('SELECT * FROM notice_queue')),1)
        self.assertEqual(len(self.s.db.query('SELECT * FROM robot_risk_holds')),2)
        self.assertEqual(self.s.db.query('SELECT * FROM member_orders'),[])
        result=receive(self.s,'Bearer test-token-not-real-123',{'platform_id':1,'event_id':wire['event_id'],
                       'batch_id':wire['event_id'],'status':'executed','executed_at':int(time.time())})
        self.assertTrue(result['valid'])
        self.assertEqual(self.s.db.query('SELECT * FROM robot_risk_holds'),[])
        self.assertEqual(len(self.s.db.query('SELECT * FROM member_orders')),2)

    def test_missing_callback_alerts_once_and_never_books_order(self):
        with self.s.db.tx() as c:
            c.execute("INSERT INTO robot_outbox(event_id,name,event_time,created,request,message,status) VALUES('test','bot',1,'t','{}','大10','sent')")
            c.execute("INSERT INTO robot_execution(outbox_id,updated,deadline) VALUES(1,'t',10)")
            expire(c,11)
            expire(c,12)
        self.assertEqual(self.s.db.query('SELECT status FROM robot_execution')[0]['status'],'unconfirmed')
        self.assertEqual(len(self.s.db.query('SELECT * FROM notice_events')),1)
        self.assertEqual(self.s.db.query('SELECT * FROM member_orders'),[])

    def test_batch_timeout_marks_all_members_without_booking(self):
        self.s.robot=self.r
        event='pc28-batch-timeout-test'
        with self.s.db.tx() as c:
            c.execute("INSERT INTO notice_subscriptions(name,chat_id,events,enabled) VALUES('test','1','[\"order\"]',1)")
            c.execute('INSERT INTO robot_batches(id,request,created) VALUES(?,?,?)',
                      (event,json.dumps({'event_id':event,'execution_callback':{'version':2},
                                        'orders':[{'order_no':'PC28-1'},{'order_no':'PC28-2'}]}),'t'))
            for order_id in (1,2):
                c.execute("""INSERT INTO robot_outbox(id,event_id,name,event_time,created,request,message,status,attempts)
                    VALUES(?,?,'bot',1,'t','{}','大10','sent',1)""",(order_id,f'order-{order_id}'))
                c.execute('INSERT INTO robot_batch_items VALUES(?,?)',(order_id,event))
                c.execute("INSERT INTO robot_execution(outbox_id,updated,deadline) VALUES(?,'t',10)",(order_id,))
        body={'platform_id':1,'event_id':event,'batch_id':event,'status':'timeout'}
        self.assertEqual(receive(self.s,'Bearer test-token-not-real-123',body)['orders'],2)
        self.assertTrue(receive(self.s,'Bearer test-token-not-real-123',body)['duplicate'])
        self.assertEqual([r['status'] for r in self.s.db.query('SELECT status FROM robot_execution')],['timeout','timeout'])
        notices=self.s.db.query('SELECT message FROM notice_queue')
        self.assertEqual(len(notices),1)
        self.assertIn('PC28-1',notices[0]['message'])
        self.assertIn('PC28-2',notices[0]['message'])
        self.assertIn(event,notices[0]['message'])
        self.assertEqual(self.s.db.query('SELECT * FROM member_orders'),[])

    def test_hourly_ratio_strictly_above_four_points_fans_out_once(self):
        start=datetime(2026,11,10,1,tzinfo=timezone.utc)
        end=start+timedelta(hours=1)
        with self.s.db.tx() as c:
            c.executemany("""INSERT INTO notice_subscriptions(name,chat_id,events,enabled) VALUES(?,?,?,1)""",
                          [('first','1','["ratio"]'),('second','2','["ratio"]'),('orders','3','["order"]')])
            c.executemany("""INSERT INTO draws(id,source_time,draw_utc,numbers,size,parity,first_seen,source,raw_hash,verification)
                VALUES(?,'t',?,'[]',?,?,'t','test','test','full_keno')""",
                [(i+1,(start+timedelta(seconds=i)).isoformat(),'大' if i<26 else '小','单' if i<25 else '双') for i in range(50)])
            hourly_ratios(c,start,end)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM notice_queue').fetchone()[0],0)
            c.execute("UPDATE draws SET size='大' WHERE id=27")
            hourly_ratios(c,start,end)
            hourly_ratios(c,start,end)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM notice_queue').fetchone()[0],2)
