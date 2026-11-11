import unittest,tempfile,json
from pathlib import Path
from datetime import datetime,timezone,timedelta
from pc28.db import DB
from pc28.algorithm_hall import AlgorithmHall,DEFINITIONS,BY_ID,predict,evaluate,metrics

class AlgorithmHallTests(unittest.TestCase):
 def rows(self,count=160):
  t=datetime(2026,1,1,tzinfo=timezone.utc)
  return [{'id':100+i,'draw_utc':(t+timedelta(seconds=i*210)).isoformat(),'a':1,'b':2,'c':3,'total':6,'size':'大' if i%3 else '小','parity':'单' if i%2 else '双'} for i in range(count)]
 def test_only_two_axes_and_known_methods(self):
  self.assertEqual(len(DEFINITIONS),310);self.assertEqual({d['axis'] for d in DEFINITIONS},{'size','parity'})
  history=[{**r,'size':'大'} for r in self.rows(51)]
  self.assertEqual(predict(history,BY_ID['size.minority.20'])[0],'小');self.assertEqual(predict(history,BY_ID['size.majority.20'])[0],'大')
 def test_no_target_or_future_leakage(self):
  rows=self.rows();definition=BY_ID['size.majority.20'];a=evaluate(rows,definition)
  altered=[{**r,'size':'小' if r['size']=='大' else '大'} if r['id']>=220 else r for r in rows];b=evaluate(altered,definition)
  pa={r['target_draw']:r['prediction'] for r in a['records']};pb={r['target_draw']:r['prediction'] for r in b['records']}
  self.assertTrue(all(pa[k]==pb[k] for k in pa if k<=220))
  self.assertTrue(all(r['cutoff_draw']<r['target_draw'] for r in a['records']))
 def test_missing_target_excluded_and_streak_breaks(self):
  rows=[r for r in self.rows() if r['id']!=250];out=evaluate(rows,BY_ID['size.follow.1']);missing=next(r for r in out['records'] if r['target_draw']==250)
  self.assertIsNone(missing['hit']);self.assertEqual(len(out['records']),100);self.assertLess(out['metrics']['20']['count'],20)
  m=metrics([{'target_draw':1,'hit':1},{'target_draw':2,'hit':1},{'target_draw':3,'hit':None},{'target_draw':4,'hit':1}],20)
  self.assertEqual(m['max_hit'],2);self.assertEqual(m['current_hit'],1);self.assertEqual(m['count'],3)
 def test_gap_inside_lookback_and_tie_rule(self):
  rows=self.rows(21);rows[10]['id']+=1000;self.assertIsNone(predict(rows,BY_ID['size.majority.20'])[0])
  rows=self.rows(20)
  for i,r in enumerate(rows):r['size']='大' if i%2 else '小'
  self.assertEqual(predict(rows,BY_ID['size.minority.20'])[0],rows[-1]['size'])
 def test_backtest_warmup_directions_risk_and_chart_totals(self):
  from pc28.algorithm_hall import backtest_algorithm
  from pc28.config import DEFAULT
  rows=[{**r,'size':side} for r,side in zip(self.rows(6),('大','大','小','小','大','大'))]
  for interval in ('1h','4h','1d','1o','20o'):
   result=backtest_algorithm(rows,BY_ID['size.follow.1'],5,DEFAULT['odds'],interval)
   self.assertEqual((result['first_draw'],result['last_draw'],result['observed']),(101,105,5))
   self.assertEqual((result['orders'],result['wins'],result['investment']),(5,3,55000))
   self.assertEqual((result['net'],result['max_drawdown']),(15000,10000))
   self.assertEqual(result['win_rate'],0.6)
   self.assertEqual(result['directions']['大'],{'actual':3,'predicted':3,'orders':3})
   self.assertEqual(result['candles'][-1]['close'],150)
  gap=backtest_algorithm([r for r in rows if r['id']!=102],BY_ID['size.follow.1'],5,DEFAULT['odds'],'1h')
  self.assertEqual(gap['orders'],3)
 def test_history_rates_and_attributed_order_income_are_separate(self):
  from pc28.custom_strategy import initialize
  rows=self.rows(240)
  key='size.follow.1'
  with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent/'data') as tmp:
   db=DB(Path(tmp)/'test.sqlite3');initialize(db)
   with db.tx() as c:
    c.executemany("""INSERT INTO draws(id,draw_utc,source_time,a,b,c,total,size,parity,verification,numbers,first_seen,source,raw_hash)
      VALUES(?,?,?,?,?,?,?,?,?,'full_keno','[]','test','test','test')""",
      ((r['id'],r['draw_utc'],r['draw_utc'],r['a'],r['b'],r['c'],r['total'],r['size'],r['parity']) for r in rows))
    def order(strategy,target,hit,pnl,direction='大',late=False,backfill=False,status='settled'):
     time=rows[target-100]['draw_utc'];created=time if late else rows[target-101]['draw_utc']
     evidence=[{'cutoff_draw':target-1,'predictions':[{'id':key,'prediction':'大'},{'id':'size.reverse.1','prediction':'小'}]}]
     result=c.execute("""INSERT INTO custom_orders(strategy,target_draw,cutoff_draw,created,direction,stake,status,pnl,hit,outcome,settled,strategy_json)
      VALUES(?,?,?,?,?,1000,?,?,?,?,?,?)""",(strategy,target,target-1,created,direction,status,pnl,hit,'win' if hit else 'loss',time,json.dumps({'strategy_type':'algorithm','evidence':evidence})))
     if backfill:c.execute("INSERT INTO gap_order_tags VALUES('custom',?,'fixture')",(result.lastrowid,))
    order('s1',200,1,900);order('s2',200,1,900);order('s1',332,0,-1000)
    order('s1',333,1,900,direction='小');order('s1',334,1,900,late=True)
    order('s1',335,1,900,backfill=True);order('s1',336,1,900,status='pending')
   hall=AlgorithmHall(db)
   from pc28.config import DEFAULT
   self.assertEqual(hall.backtest(key,20,DEFAULT['odds'],cutoff=330)['last_draw'],330)
   with self.assertRaises(ValueError):hall.backtest(key,50001,DEFAULT['odds'])
   historical=hall.detail(key)
   self.assertEqual(historical['source'],'history');self.assertEqual(historical['total'],240)
   self.assertEqual(historical['metrics']['20']['count'],20)
   detail=hall.detail(key,source='orders')
   self.assertEqual(detail['total'],3)
   self.assertEqual(detail['actual']['orders'],3)
   self.assertEqual(detail['actual']['net'],800)
   self.assertEqual(detail['metrics']['100']['count'],100)
   for window in (20,100,500,1000,2000,5000):
    scoped=hall.detail(key,window=window,source='orders')
    totals=scoped['metrics'][str(window)]['actual']
    self.assertEqual((scoped['total'],totals['orders'],totals['net']),
                     (1,1,-1000) if window<=100 else (3,3,800))
   with self.assertRaises(ValueError):hall.detail(key,window=200)
   for interval in ('1h','4h','1d'):
    chart=hall.income(key,interval)
    self.assertEqual(sum(b['orders'] for b in chart['candles']),3)
    self.assertEqual(chart['net'],8)
   for interval in ('1o','20o','50o','100o'):
    bars=hall.income(key,interval)['candles']
    self.assertEqual(sum(b['orders'] for b in bars),3)
    self.assertEqual(len(bars),3 if interval=='1o' else 1)
    self.assertEqual(bars[-1]['close'],8)
    self.assertEqual(bars[-1]['last_order'],3)
   from pc28.algorithm_hall import order_candles
   trades=[{'beijing_time':rows[0]['draw_utc'],'pnl':pnl} for pnl in ([100,-200,300]*35)]
   for size in (1,20,50,100):
    bars=order_candles(trades,size)
    self.assertEqual(bars[-1]['last_order'],105)
    self.assertEqual(bars[-1]['orders'],105%size or size)
    self.assertEqual(bars[-1]['close'],70)
   self.assertEqual((order_candles(trades,20)[0]['high'],order_candles(trades,20)[0]['low']),(13,-1))
   with db.tx() as c:c.execute("INSERT INTO public_visibility(kind,item_id,published) VALUES('algorithm',?,0)",(key,))
   with self.assertRaises(ValueError):hall.income(key)
   with self.assertRaises(ValueError):hall.backtest(key,20,DEFAULT['odds'])
   self.assertEqual(hall.income(key,admin=True)['orders'],3)
 def test_empty_and_favorite_isolation_idempotency(self):
  with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent/'data') as tmp:
   from pc28.custom_strategy import initialize
   db=DB(Path(tmp)/'test.sqlite3');initialize(db);hall=AlgorithmHall(db);listing=hall.listing(1);self.assertEqual(len(listing['algorithms']),310);self.assertTrue(all(r['metrics']['20']['rate'] is None for r in listing['algorithms']))
   body={'id':'size.follow.1','favorite':True};hall.favorite(1,body);hall.favorite(1,body)
   row=next(r for r in hall.listing(1)['algorithms'] if r['id']==body['id']);other=next(r for r in hall.listing(2)['algorithms'] if r['id']==body['id'])
   self.assertTrue(row['favorite']);self.assertFalse(other['favorite']);self.assertEqual(row['favorites'],1)
   with self.assertRaises(ValueError):hall.favorite(1,{'id':'bad','favorite':True})
   with self.assertRaises(ValueError):hall.detail('size.follow.1',page_size=7)
   self.assertEqual(hall.detail('size.follow.1')['total'],0)

 def test_history_available_without_orders_and_survives_restart(self):
  from pc28.custom_strategy import initialize
  from pc28.follow_strategy import rank_signal
  with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent/'data') as tmp:
   db=DB(Path(tmp)/'test.sqlite3');initialize(db)
   rows=self.rows(160)
   with db.tx() as c:
    c.executemany("INSERT INTO draws(id,draw_utc,source_time,a,b,c,total,size,parity,verification,numbers,first_seen,source,raw_hash) VALUES(?,?,?,?,?,?,?,?,?,'full_keno','[]','test','test','test')",((r['id'],r['draw_utc'],r['draw_utc'],r['a'],r['b'],r['c'],r['total'],r['size'],r['parity']) for r in rows))
   first=AlgorithmHall(db).listing(1)
   again=AlgorithmHall(DB(Path(tmp)/'test.sqlite3')).listing(1)
   self.assertEqual(first,again)
   self.assertTrue(all(a['metrics']['20']['count']==20 for a in first['algorithms']))
   self.assertTrue(all(a['actual']['orders']==0 and a['actual']['net']==0 for a in first['algorithms']))
   ranking=sorted([a for a in first['algorithms'] if a['axis']=='size'],key=lambda a:(-a['metrics']['20']['rate'],a['id']))
   verified=[dict(r,verification='full_keno') for r in rows]
   _,e,_=rank_signal(verified,{'rank_window':20,'rank_position':1,'axis':'size'},{},260,set())
   self.assertEqual(e[0]['algorithm_ids'],[ranking[0]['id']])
   with self.assertRaises(ValueError):AlgorithmHall(db).detail('size.follow.1',source='bad')
   with db.tx() as c:c.execute("UPDATE draws SET verification='unverified' WHERE id=250")
   hall=AlgorithmHall(db);history=hall.detail('size.follow.1',window=20)
   self.assertLess(history['metrics']['20']['count'],20)
   self.assertEqual(hall.detail('size.follow.1',source='orders')['total'],0)
