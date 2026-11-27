import json,unittest
from datetime import datetime,timezone,timedelta
import test_robot_accounts as fixture
from pc28 import member_orders,member_center
from pc28.auth import AuthError


class MemberRobotOrderTests(unittest.TestCase):
 def setUp(self):
  fixture.AccountTests.setUp(self)
  self.uid=fixture.AccountTests.member(self,'merged_member')
  body=fixture.AccountTests.body(self,self.uid)
  self.aid=self.r.account_save(body,None,'')['id']
  self.bots=[x['id'] for x in self.r.account_detail(self.aid)['bots']]
  self.now=datetime.now(timezone.utc).isoformat()
 tearDown=fixture.AccountTests.tearDown

 def add(self,bot=None,batch='batch-one',draw=999,direction='大',stake=1000,pnl=1000,status='settled',uid=None,outcome=None,original=None):
  uid=self.uid if uid is None else uid;bot=self.bots[0] if bot is None else bot
  with self.s.db.tx() as c:
   oid=9000+c.execute('SELECT COALESCE(MAX(id),0) FROM member_orders').fetchone()[0]
   result=outcome or ('win' if pnl and pnl>0 else 'loss' if pnl and pnl<0 else 'refund' if pnl is not None else None)
   cur=c.execute('''INSERT INTO member_orders(user_id,outbox_id,batch_id,platform_id,strategy_name,target_draw,direction,stake,placed_at,accepted_at,odds_json,status,draw_time,result,total,pnl,outcome,settled_at,follow_account_id,bot_id,original_stake)
    VALUES(?,?,?,1,?,?,?,?,?,?,?, ?,?,'大双',16,?,?,?,?,?,?)''',(uid,oid,batch,'策略'+str(oid),draw,direction,stake,self.now,self.now,json.dumps(self.s.cfg['odds']),status,self.now if status=='settled' else None,pnl,result,self.now if status!='pending' else None,self.aid,bot,original))
   key=cur.lastrowid
   if stake:member_orders.entry(c,uid,'stake',-stake,order_id=key)
   if status=='settled':member_orders.entry(c,uid,'settle',stake+pnl,order_id=key)
   elif status=='void' and stake:member_orders.entry(c,uid,'execution_reversal',stake,order_id=key)
   return key

 def listing(self,**q):return member_orders.listing(self.s,q)
 def center(self,resource='orders',**q):return member_center.read(self.s.db,{'id':self.uid,'role':'member'},resource,q)

 def test_same_robot_batch_is_one_order_and_does_not_rewrite_cash(self):
  self.add();self.add(direction='单',stake=500,pnl=-500)
  before=self.s.db.query('SELECT * FROM member_cash_ledger')
  result=self.listing();self.assertEqual(result['total'],1)
  row=result['rows'][0]
  self.assertEqual((row['stake'],row['pnl'],row['detail_count'],row['outcome']),(1500,500,2,'win'))
  self.assertEqual((row['debit'],row['credit']),(1500,2000))
  self.assertEqual(len(row['items']),2)
  self.assertEqual(before,self.s.db.query('SELECT * FROM member_cash_ledger'))
  self.assertEqual(self.center()['summary']['total'],1)
  self.assertEqual(member_orders.users(self.s,{})['rows'][0]['follow_count'],1)

 def test_multiple_robots_members_draws_and_batches_stay_separate(self):
  self.add();self.add(direction='双');self.add(bot=self.bots[1])
  self.add(batch='different-batch');self.add(draw=1000)
  other=fixture.AccountTests.member(self,'other_member');foreign=self.add(uid=other)
  self.assertEqual(self.listing()['total'],5)
  self.assertEqual(self.center()['summary']['total'],4)
  with self.assertRaises(AuthError):self.center('order',id=str(foreign))

 def test_direction_filter_returns_whole_group_and_status_is_group_status(self):
  self.add();self.add(direction='单',stake=500,pnl=-500)
  row=self.listing(direction='单')['rows'][0]
  self.assertEqual(row['stake'],1500);self.assertEqual(len(row['items']),2)
  self.assertEqual(self.listing(direction='小')['total'],0)
  self.assertEqual(self.listing(status='settled')['total'],1)

 def test_partial_settlement_pending_totals_then_final_net(self):
  self.add();pending=self.add(direction='单',stake=500,pnl=None,status='pending')
  row=self.listing()['rows'][0]
  self.assertEqual(row['status'],'pending');self.assertIsNone(row['pnl'])
  total=self.center('dashboard')['total']
  self.assertEqual((total['total'],total['settled'],total['pending'],total['net'],total['pending_amount']),(1,0,1,1000,500))
  with self.s.db.tx() as c:
   c.execute("UPDATE member_orders SET status='settled',pnl=-500,outcome='loss',draw_time=?,settled_at=? WHERE id=?",(self.now,self.now,pending))
   member_orders.entry(c,self.uid,'settle',0,order_id=pending)
  row=self.listing()['rows'][0]
  self.assertEqual((row['status'],row['pnl']),('settled',500))

 def test_flat_outcome_not_falsely_labeled_refund(self):
  self.add();self.add(direction='单',pnl=-1000)
  self.assertEqual(self.listing()['rows'][0]['outcome'],'flat')

 def test_full_offset_is_one_zero_cash_record(self):
  self.add(stake=0,pnl=0,status='void',outcome='offset',original=1000)
  self.add(direction='小',stake=0,pnl=0,status='void',outcome='offset',original=1000)
  row=self.listing()['rows'][0]
  self.assertEqual((row['stake'],row['original_stake'],row['outcome'],row['debit']),(0,2000,'offset',0))

 def test_pagination_counts_groups_not_components(self):
  for i in range(12):
   self.add(draw=1000+i);self.add(draw=1000+i,direction='单',pnl=-500)
  result=self.listing(page=2,page_size=10)
  self.assertEqual((result['total'],result['pages'],len(result['rows'])),(12,2,2))

 def test_member_filters_and_ledger_link_to_main_order(self):
  first=self.add();second=self.add(direction='单',pnl=-500);self.add(bot=self.bots[1])
  result=self.center(bots=str(self.bots[0]));self.assertEqual(result['summary']['total'],1)
  detail=self.center('order',id=str(second))
  self.assertEqual(detail['id'],first);self.assertEqual(len(detail['items']),2)
  ledger=member_orders.listing(self.s,{'user_id':self.uid},ledger=True)['rows']
  self.assertTrue(all(r['merged_order_id']==first for r in ledger if r['order_id'] in (first,second)))

 def test_void_reversal_return_is_included(self):
  self.add(status='void',pnl=0,outcome='execution_rejected')
  row=self.listing()['rows'][0]
  self.assertEqual((row['debit'],row['credit'],row['pnl']),(1000,1000,0))
