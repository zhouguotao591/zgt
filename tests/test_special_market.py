import json,unittest
from datetime import datetime,timedelta,timezone
from test_detailed_notices import DetailedNoticeTests as Fixture
from pc28.notifications import special_market,Notifications

class SpecialMarketTests(unittest.TestCase):
 setUp=Fixture.setUp
 tearDown=Fixture.tearDown
 def subscribers(self,c):
  Fixture.subscribe(self,c)
  c.execute("UPDATE notice_subscriptions SET events='[\"market\"]'")
 def data(self,c,size,parity=None,first=1,now=None):
  now=now or datetime.now(timezone.utc);parity=parity or ['单' if i%2 else '双' for i in range(len(size))]
  for i,(s,p) in enumerate(zip(size,parity)):
   at=(now-timedelta(seconds=(len(size)-1-i)*210+1)).isoformat()
   c.execute("INSERT INTO draws(id,source_time,draw_utc,numbers,size,parity,first_seen,source,raw_hash,verification) VALUES(?,?,?,'[]',?, ?,?,'test','test','full_keno')",(first+i,at,at,s,p,at))
  return now
 def messages(self,c):return [r[0] for r in c.execute('SELECT message FROM notice_queue ORDER BY id')]
 def test_strict_20_boundary_then_merged_axes_and_restart_dedup(self):
  with self.s.db.tx() as c:
   self.subscribers(c);now=self.data(c,['小']*4+['大']*16,['双']*4+['单']*16)
   special_market(c,now);self.assertEqual(self.messages(c),[])
   now+=timedelta(seconds=210);self.data(c,['大'],['单'],first=21,now=now)
   special_market(c,now);special_market(c,now);messages=self.messages(c)
   self.assertEqual(len(messages),2);self.assertEqual(messages[0],messages[1])
   for value in ('最近20期','第2–21期','大小次数差：14','单双次数差：14','大：17 期（85.00%）','小：3 期（15.00%）'):self.assertIn(value,messages[0])
  Notifications(self.s)
  with self.s.db.tx() as c:
   now+=timedelta(seconds=210);self.data(c,['大'],['单'],first=22,now=now);special_market(c,now)
   self.assertEqual(len(self.messages(c)),2)
 def test_30_boundary_and_only_matching_dimension(self):
  with self.s.db.tx() as c:
   self.subscribers(c);now=self.data(c,['小']*4+['大']*6+['小']*4+['大']*16)
   special_market(c,now);self.assertEqual(self.messages(c),[])
   now+=timedelta(seconds=210);self.data(c,['大'],['双'],first=31,now=now);special_market(c,now)
   messages=self.messages(c);self.assertEqual(len(messages),2)
   self.assertIn('最近30期',messages[0]);self.assertIn('大小次数差：16',messages[0]);self.assertNotIn('单双次数差',messages[0])
 def test_same_draw_windows_merge_and_disabled_subscriptions(self):
  with self.s.db.tx() as c:
   self.subscribers(c);c.execute('UPDATE notice_subscriptions SET enabled=0');now=self.data(c,['大']*30,['单']*30)
   special_market(c,now);self.assertEqual(self.messages(c),[])
   c.execute('UPDATE notice_subscriptions SET enabled=1');special_market(c,now)
   messages=self.messages(c);self.assertEqual(len(messages),2)
   self.assertIn('最近20期',messages[0]);self.assertIn('最近30期',messages[0]);self.assertIn('单：30 期（100.00%）',messages[0])
 def test_invalid_gaps_conflicts_stale_and_future_do_not_alert(self):
  for mode in ('gap','conflict','unverified','stale','future'):
   with self.subTest(mode=mode),self.s.db.tx() as c:
    c.execute('DELETE FROM notice_queue');c.execute('DELETE FROM notice_subscriptions');c.execute('DELETE FROM notice_bot_subscribers');c.execute('DELETE FROM notice_events');c.execute('DELETE FROM notice_market_state');c.execute('DELETE FROM draws');c.execute('DELETE FROM conflicts')
    self.subscribers(c);now=self.data(c,['大']*30)
    if mode=='gap':c.execute('DELETE FROM draws WHERE id=25')
    if mode=='conflict':c.execute('INSERT INTO conflicts(draw_id,resolved) VALUES(25,0)')
    if mode=='unverified':c.execute("UPDATE draws SET verification='unverified' WHERE id=25")
    if mode=='stale':now+=timedelta(seconds=700)
    if mode=='future':now-=timedelta(seconds=60)
    special_market(c,now);self.assertEqual(self.messages(c),[])
 def test_rearm_after_condition_exits(self):
  with self.s.db.tx() as c:
   self.subscribers(c);now=self.data(c,['大']*30);special_market(c,now)
   c.execute('DELETE FROM draws');now+=timedelta(seconds=210);self.data(c,['大','小']*15,first=2,now=now);special_market(c,now)
   self.assertTrue(all(not v for v in json.loads(c.execute('SELECT active FROM notice_market_state').fetchone()[0]).values()))
   c.execute('DELETE FROM draws');now+=timedelta(seconds=210);self.data(c,['大']*30,first=3,now=now);special_market(c,now)
   self.assertEqual(len(self.messages(c)),4)

if __name__=='__main__':unittest.main()
