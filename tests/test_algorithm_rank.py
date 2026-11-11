import copy,json,random,unittest
from datetime import datetime,timedelta,timezone
from test_follow_strategy import FollowTests,BIG
from pc28.algorithm_hall import RollingRanking,DEFINITIONS,evaluate,predict
from pc28.follow_strategy import defaults,validate,rank_signal
from pc28.custom_strategy import tick
from pc28.history_backtest import period_candles,replay,selection

class RankingTests(unittest.TestCase):
 def rows(self,n=220):
  rng=random.Random(71)
  return [dict(id=i+100,size=rng.choice(['大','小']),parity=rng.choice(['单','双']),verification='full_keno',a=1,b=2,c=3,total=6) for i in range(n)]
 def test_incremental_rank_equals_full_prior_evaluation(self):
  rows=self.rows();hidden={'size.follow.1'}
  for window in (10,20,87):
   cache=RollingRanking('size',window,hidden)
   for end in range(window,221):
    h=rows[:end];ranked=cache.update(h)
    if end%13:continue
    expected=[]
    for d in DEFINITIONS:
     if d['axis']!='size' or d['id'] in hidden:continue
     r=evaluate(h,d,window);hits=[v['hit'] for v in r['records'] if v['hit'] is not None]
     if hits:expected.append((d['id'],sum(hits)/len(hits),len(hits)))
    self.assertEqual(ranked,sorted(expected,key=lambda r:(-r[1],r[0])))
 def test_streamed_long_window_matches_full_history(self):
  rows=self.rows(350)
  for window in (10,87,200):
   streaming=RollingRanking('parity',window)
   for i in range(1,len(rows)+1):
    result=streaming.update(rows[max(0,i-52):i])
    if i>=window and i%29==0:
     self.assertEqual(result,RollingRanking('parity',window).update(rows[:i]))
 def test_missing_unverified_future_and_ties(self):
  h=self.rows();s={'rank_window':20,'rank_position':2,'axis':'size'}
  choice,evidence,_=rank_signal(h,s,{},320,set())
  self.assertIn(choice,('大','小'));self.assertEqual(evidence[0]['cutoff_draw'],319)
  self.assertEqual(rank_signal(h+[dict(h[-1],id=320,size='大')],s,{},320,set()),rank_signal(h,s,{},320,set()))
  self.assertIsNone(rank_signal(h[:-1],s,{},320,set())[0])
  self.assertIsNone(rank_signal(h,s,{},320,{d['id'] for d in DEFINITIONS})[0])
  h[-1]['verification']='unverified';self.assertIsNone(rank_signal(h,s,{},320,set())[0])
 def test_period_bars_include_unbet_periods_and_ohlc(self):
  records=[{'target_draw':101,'pnl':10000,'beijing_time':'2026-01-01 00:00:00'},{'target_draw':102,'pnl':-15000,'beijing_time':'2026-01-01 00:03:30'},{'target_draw':112,'pnl':2500,'beijing_time':'2026-01-01 00:38:30'}]
  bars=period_candles(records,'5p',100,113)
  self.assertEqual(len(bars),3);self.assertEqual([bars[0][k] for k in ('open','high','low','close')],[0,100,-50,-50]);self.assertEqual(bars[1]['close'],-50);self.assertEqual(bars[1]['orders'],0);self.assertEqual(bars[-1]['last_draw'],113);self.assertEqual(bars[-1]['close'],-25)
  self.assertEqual(len(period_candles(records,'1p',100,113)),14)
  self.assertEqual(period_candles([], '10p',100,119)[-1]['close'],0)

class RankLiveTests(unittest.TestCase):
 setUp=FollowTests.setUp
 tearDown=FollowTests.tearDown
 received=FollowTests.received
 plan=FollowTests.plan
 create=FollowTests.create
 enable=FollowTests.enable
 def test_create_enable_tick_settle_and_replay(self):
  key,s=self.create('algorithm_rank',rank_window=10,rank_position=2)
  self.assertEqual(validate(self.s.cfg,s),s)
  self.enable(key);tick(self.s.db,self.s.cfg,self.now)
  orders=self.s.db.query('SELECT * FROM custom_orders WHERE strategy=?',(key,));self.assertEqual(len(orders),1)
  order=orders[0];e=json.loads(order['strategy_json'])['evidence'][0]
  expected=rank_signal(self.s.db.tail(1001),s,{},136,set())
  self.assertEqual(order['direction'],expected[0]);self.assertEqual(e['algorithm_ids'],expected[1][0]['algorithm_ids'])
  t=self.hour+timedelta(seconds=150)
  self.s.db.import_records([{'id':136,'time':t.isoformat(),'numbers':BIG}],'test',received=t.isoformat());self.plan(137,t+timedelta(seconds=210));tick(self.s.db,self.s.cfg,t+timedelta(seconds=1))
  self.assertEqual(self.s.db.query('SELECT status FROM custom_orders WHERE id=?',(order['id'],))[0]['status'],'settled')
  rows=self.s.db.rows()
  for r in rows:r['_time']=datetime.fromisoformat(r['draw_utc'])
  snap=selection(self.s,key);snap['maintenance_guard']=False
  result,backorders=replay(rows,snap)
  matching=next(o for o in backorders if o['target_draw']==136)
  self.assertEqual(matching['direction'],order['direction']);self.assertEqual(matching['algorithm_ids'],e['algorithm_ids'])
  for field,value in [('rank_window',0),('rank_window',10001),('rank_position',15),('rank_position',True)]:
   with self.assertRaises(ValueError):validate(self.s.cfg,{**s,field:value})
