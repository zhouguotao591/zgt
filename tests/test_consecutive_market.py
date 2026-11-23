import unittest, io, json
from unittest.mock import patch
from test_robot_signals import MemorySecret
from datetime import timedelta
import test_special_market as fixture
from pc28.notifications import consecutive_market, Notifications


class ConsecutiveMarketTests(unittest.TestCase):
 setUp=fixture.SpecialMarketTests.setUp
 tearDown=fixture.SpecialMarketTests.tearDown
 data=fixture.SpecialMarketTests.data
 messages=fixture.SpecialMarketTests.messages
 def subscribers(self,c):
  fixture.SpecialMarketTests.subscribers(self,c)
  c.execute("UPDATE notice_subscriptions SET events='[\"streak\"]'")
 def test_ninth_only_combines_axes_and_persists_dedup(self):
  with self.s.db.tx() as c:
   self.subscribers(c);now=self.data(c,['大']*8,['单']*8)
   consecutive_market(c,now);self.assertEqual(self.messages(c),[])
   now+=timedelta(seconds=210);self.data(c,['大'],['单'],first=9,now=now)
   consecutive_market(c,now);consecutive_market(c,now)
   self.assertEqual(len(self.messages(c)),2)
   self.assertIn('大：连续出现 9 期',self.messages(c)[0]);self.assertIn('单：连续出现 9 期',self.messages(c)[0])
  Notifications(self.s)
  with self.s.db.tx() as c:
   consecutive_market(c,now)
   for i in (10,11):
    now+=timedelta(seconds=210);self.data(c,['大'],['单'],first=i,now=now);consecutive_market(c,now)
   self.assertEqual(len(self.messages(c)),2)
   now+=timedelta(seconds=9*210);self.data(c,['小']*9,['双']*9,first=12,now=now);consecutive_market(c,now)
   self.assertEqual(len(self.messages(c)),4)
   self.assertIn('小：连续出现 9 期',self.messages(c)[-1]);self.assertIn('双：连续出现 9 期',self.messages(c)[-1])
 def test_all_four_directions(self):
  for direction in ('大','小','单','双'):
   with self.subTest(direction=direction),self.s.db.tx() as c:
    c.execute('DELETE FROM draws');c.execute('DELETE FROM notice_events');c.execute('DELETE FROM notice_queue')
    if not c.execute('SELECT 1 FROM notice_subscriptions').fetchone():self.subscribers(c)
    sizes=[direction]*9 if direction in ('大','小') else ['大','小']*4+['大']
    parities=[direction]*9 if direction in ('单','双') else ['单','双']*4+['单']
    now=self.data(c,sizes,parities);consecutive_market(c,now)
    self.assertEqual(len(self.messages(c)),2);self.assertIn(direction+'：连续出现 9 期',self.messages(c)[0])
 def test_group_delivery_uses_notice_transport(self):
  with self.s.db.tx() as c:
   self.subscribers(c);c.execute("UPDATE notice_subscriptions SET chat_id='-1001234567890' WHERE id=101")
   now=self.data(c,['大']*9);consecutive_market(c,now)
  notices=Notifications(self.s)
  with patch.object(notices,'secret',return_value=MemorySecret()),patch('pc28.notifications.urllib.request.urlopen',return_value=io.BytesIO(b'{"ok":true}')) as send:
   notices.deliver()
   body=json.loads(send.call_args.args[0].data)
   self.assertEqual(body['chat_id'],'-1001234567890');self.assertIn('连续9期提醒',body['text'])
  self.assertEqual(self.s.db.query('SELECT status FROM notice_queue ORDER BY id')[0]['status'],'sent')
 def test_invalid_data_and_non_subscribers_do_not_alert(self):
  for mode in ('gap','conflict','unverified','stale','future','disabled','other_event','already_ten'):
   with self.subTest(mode=mode),self.s.db.tx() as c:
    for table in ('notice_queue','notice_bot_subscribers','notice_subscriptions','notice_events','draws','conflicts'):c.execute('DELETE FROM '+table)
    self.subscribers(c);now=self.data(c,['大']*(10 if mode=='already_ten' else 9))
    if mode=='gap':c.execute('DELETE FROM draws WHERE id=5')
    if mode=='conflict':c.execute('INSERT INTO conflicts(draw_id,resolved) VALUES(5,0)')
    if mode=='unverified':c.execute("UPDATE draws SET verification='unverified' WHERE id=5")
    if mode=='stale':now+=timedelta(seconds=700)
    if mode=='future':now-=timedelta(seconds=60)
    if mode=='disabled':c.execute('UPDATE notice_subscriptions SET enabled=0')
    if mode=='other_event':c.execute("UPDATE notice_subscriptions SET events='[\"market\"]'")
    consecutive_market(c,now);self.assertEqual(self.messages(c),[])

if __name__=='__main__':unittest.main()
