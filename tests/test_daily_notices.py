import unittest
from datetime import datetime,timedelta,timezone
from zoneinfo import ZoneInfo
from test_robot_signals import RobotTests
from pc28.notifications import daily_reports,schedule_daily,Notifications

class DailyNoticeTests(unittest.TestCase):
    setUp=RobotTests.setUp
    tearDown=RobotTests.tearDown

    def subscriptions(self,c):
        c.execute("""INSERT INTO notice_subscriptions(id,name,chat_id,events,enabled,mode)
            VALUES(101,'fixed','1','["daily_ratio","daily_profit"]',1,'fixed')""")
        c.execute("""INSERT INTO notice_subscriptions(id,name,chat_id,events,enabled,mode,bot_id)
            VALUES(102,'private','','["daily_ratio","daily_profit"]',1,'bot','fixture')""")
        c.execute("INSERT INTO notice_bot_subscribers VALUES(102,'1234',1)")
        c.execute("INSERT INTO notice_bot_subscribers VALUES(102,'9999',0)")

    def bounds(self):
        start=datetime(2026,11,10,tzinfo=ZoneInfo('Asia/Shanghai'))
        return start,start+timedelta(days=1)

    def test_profit_same_draw_netting_invalid_orders_and_midnight_boundaries(self):
        start,end=self.bounds()
        with self.s.db.tx() as c:
            self.subscriptions(c)
            data=[(1,100,start,10000,10000,'settled'),
                  (2,100,start,10000,-5000,'settled'),
                  (3,101,start+timedelta(hours=1),20000,-12000,'settled'),
                  (4,102,start+timedelta(hours=2),10000,9000,'settled'),
                  (5,103,end,10000,999999,'settled'),
                  (6,104,start-timedelta(seconds=1),10000,999999,'settled'),
                  (7,105,start,10000,None,'pending'),
                  (8,106,start,10000,999999,'settled'),
                  (9,107,start,10000,999999,'void')]
            for oid,draw,at,stake,pnl,status in data:
                c.execute("""INSERT INTO member_orders(id,user_id,outbox_id,target_draw,direction,stake,
                    placed_at,accepted_at,odds_json,status,pnl,draw_time)
                    VALUES(?,1,?,?,'大',?,'t','t','{}',?,?,?)""",
                    (oid,oid,draw,stake,status,pnl,at.astimezone(timezone.utc).isoformat()))
            c.execute("INSERT INTO robot_execution VALUES(8,'rejected','','t',1)")
            c.execute("INSERT INTO robot_execution VALUES(2,'draw_settled','','t',1)")
            daily_reports(c,start,end);daily_reports(c,start,end)
            messages=c.execute("SELECT message FROM notice_queue WHERE message LIKE '全体会员%'").fetchall()
            self.assertEqual(len(messages),2)
            self.assertEqual(messages[0][0],messages[1][0])
            for text in ('2026-11-10','订单数：4','下单金额：500.00 U','盈利金额：+20.00 U',
                         '盈利比：4.00%','胜率：50.00%','最大盈利金额：50.00 U','最大利润回撤：120.00 U'):
                self.assertIn(text,messages[0][0])

    def test_daily_ratios_have_no_four_point_threshold_and_exclude_conflicts(self):
        start,end=self.bounds()
        with self.s.db.tx() as c:
            self.subscriptions(c)
            for i,at,verification in [(1,start,'full_keno'),(2,start+timedelta(hours=1),'full_keno'),
                                      (3,end-timedelta(seconds=1),'crosschecked_abc'),
                                      (4,end,'full_keno'),(5,start,'unverified'),(6,start,'full_keno')]:
                c.execute("""INSERT INTO draws(id,source_time,draw_utc,numbers,size,parity,first_seen,source,raw_hash,verification)
                    VALUES(?,'t',?,'[]',?,?,'t','test','test',?)""",
                    (i,at.isoformat(),'大' if i!=3 else '小','单' if i==1 else '双',verification))
            c.execute("INSERT INTO conflicts(draw_id,resolved) VALUES(6,0)")
            daily_reports(c,start,end)
            message=c.execute("SELECT message FROM notice_queue WHERE message LIKE '每日开奖%'").fetchone()[0]
            for text in ('已核验开奖：3 期','大：2 期 · 66.67%','小：1 期 · 33.33%','单：1 期 · 33.33%','双：2 期 · 66.67%'):
                self.assertIn(text,message)

    def test_midnight_restart_deduplication_and_empty_day(self):
        start,end=self.bounds()
        with self.s.db.tx() as c:
            self.subscriptions(c)
            c.execute("UPDATE notice_daily_state SET day='2026-11-10'")
            schedule_daily(c,end-timedelta(seconds=1))
            self.assertEqual(c.execute('SELECT COUNT(*) FROM notice_queue').fetchone()[0],0)
            schedule_daily(c,end)
        Notifications(self.s)
        with self.s.db.tx() as c:
            schedule_daily(c,end+timedelta(hours=1))
            messages=[r[0] for r in c.execute('SELECT message FROM notice_queue')]
            self.assertEqual(len(messages),4)
            self.assertTrue(any('订单数：0' in m and '胜率：0.00%' in m for m in messages))
            self.assertEqual(c.execute('SELECT COUNT(*) FROM notice_events').fetchone()[0],2)
