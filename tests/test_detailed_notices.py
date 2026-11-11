import json,unittest
from datetime import datetime,timedelta,timezone
from test_robot_signals import RobotTests
from pc28.notifications import enqueue,hourly_ratios,strategy_alerts
from pc28.order_confirmation import alert
from pc28.follow_strategy import defaults
from pc28.custom_strategy import save

class DetailedNoticeTests(unittest.TestCase):
 setUp=RobotTests.setUp
 tearDown=RobotTests.tearDown
 member=RobotTests.member
 def subscribe(self,c):
  c.execute("INSERT INTO notice_subscriptions(id,name,chat_id,events,enabled,mode) VALUES(101,'fixed','1','[\"order\",\"strategy\",\"ratio\"]',1,'fixed')")
  c.execute("INSERT INTO notice_subscriptions(id,name,chat_id,events,enabled,mode,bot_id) VALUES(102,'private','','[\"order\",\"strategy\",\"ratio\"]',1,'bot','fixture')")
  c.execute("INSERT INTO notice_bot_subscribers VALUES(102,'1234',1)")
  c.execute("INSERT INTO notice_bot_subscribers VALUES(102,'9999',0)")
 def test_private_and_fixed_order_details_match_and_deduplicate(self):
  uid=self.member('notice_member')
  with self.s.db.tx() as c:
   self.subscribe(c)
   aid=c.execute("INSERT INTO robot_accounts(user_id,platform_id,external_account_id,name,created,updated) VALUES(?,1,7,'账号甲','t','t')",(uid,)).lastrowid
   bid=c.execute("INSERT INTO robot_bots(account_id,external_bot_key,name,signal_type,created,updated) VALUES(?,8,'机器人乙','BUY_SYSTEM','t','t')",(aid,)).lastrowid
   order=dict(id=321,bot_id=bid,recipient_name='notice_member',name='旧名',target_draw=3500000,message='大10',push_cents=1000,strategy_name='算法排名',strategy_id='custom_fixture')
   alert(c,order,'rejected','接收端返回 HTTP 422：缺少 account_id');alert(c,order,'rejected','重复')
   queued=c.execute('SELECT message,subscriber_chat FROM notice_queue ORDER BY id').fetchall()
   self.assertEqual(len(queued),2);self.assertEqual(queued[0]['message'],queued[1]['message'])
   for text in ('notice_member','账号甲','工具账号#7','机器人乙','机器人#8','PC28-321','3500000','算法排名','大10','422','account_id'):
    self.assertIn(text,queued[1]['message'])
   self.assertEqual(queued[1]['subscriber_chat'],'1234')
 def test_hourly_only_qualified_direction_and_beijing_time(self):
  start=datetime(2026,11,10,1,tzinfo=timezone.utc);end=start+timedelta(hours=1)
  with self.s.db.tx() as c:
   self.subscribe(c)
   c.executemany("INSERT INTO draws(id,source_time,draw_utc,numbers,size,parity,first_seen,source,raw_hash,verification) VALUES(?,'t',?,'[]',?,?,'t','test','test','full_keno')",[(i+1,(start+timedelta(seconds=i)).isoformat(),'大' if i<26 else '小','单' if i<25 else '双') for i in range(50)])
   hourly_ratios(c,start,end);self.assertEqual(c.execute('SELECT COUNT(*) FROM notice_queue').fetchone()[0],0)
   c.execute("UPDATE draws SET size='大' WHERE id=27")
   hourly_ratios(c,start,end);hourly_ratios(c,start,end)
   messages=[r[0] for r in c.execute('SELECT message FROM notice_queue')]
   self.assertEqual(len(messages),2);self.assertIn('09:00–10:00',messages[0]);self.assertIn('大小达标方向：大',messages[0]);self.assertIn('8.00 个百分点',messages[0]);self.assertIn('总开奖期数：50 期',messages[0]);self.assertIn('大：27 期（54.00%）',messages[0]);self.assertIn('小：23 期（46.00%）',messages[0]);self.assertIn('单：25 期（50.00%）',messages[0]);self.assertIn('双：25 期（50.00%）',messages[0]);self.assertNotIn('单双达标',messages[0]);self.assertNotIn('达标方向：小',messages[0])
 def test_strategy_details_include_losses_and_drawdown_with_thresholds(self):
  settings=defaults(self.s.cfg,'previous');settings.update(daily_loss_limit=500,max_drawdown=800)
  key=save(self.s,{'name':'风险策略甲','settings':settings})['id'];now=datetime(2026,11,10,12,tzinfo=timezone.utc)
  self.s.cfg['simulation']['enabled']=False;self.s.cfg['trend_simulation']['enabled_axes']=[]
  with self.s.db.tx() as c:
   self.subscribe(c);c.execute('UPDATE custom_strategies SET enabled=1 WHERE id=?',(key,))
   for i,pnl in enumerate([10000,-20000,-20000,-20000,-20000,-20000]):
    c.execute("INSERT INTO custom_orders(strategy,target_draw,cutoff_draw,created,direction,stake,status,pnl,settled) VALUES(?,?,?,?,?,?,'settled',?,?)",(key,1000+i,999+i,now.isoformat(),'大',20000,pnl,(now-timedelta(minutes=6-i)).isoformat()))
   strategy_alerts(c,self.s.cfg,now);strategy_alerts(c,self.s.cfg,now)
   messages=[r[0] for r in c.execute('SELECT message FROM notice_queue')]
   self.assertEqual(len(messages),2);self.assertEqual(messages[0],messages[1])
   for text in ('风险策略甲','连续亏损 5 笔','净亏损 900.00 U','亏损限额 500 U','当前回撤 1000.00 U','回撤限额 800 U','1005','不代表会员实际账户收益'):
    self.assertIn(text,messages[0])
