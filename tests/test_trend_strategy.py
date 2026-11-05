import copy,tempfile,unittest
from pathlib import Path
from datetime import datetime,timezone,timedelta
from pc28.config import DEFAULT
from pc28.db import DB
from pc28.trend_strategy import settings,initialize,live_tick,signal,progress,backtest,status
BIG=list(range(1,21));ZERO=[1,2,11,14,17,24,26,30,32,33,36,47,48,50,59,61,65,67,68,80]
class DualTrendTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent/'data');self.db=DB(Path(self.tmp.name)/'test.sqlite3');self.c=copy.deepcopy(DEFAULT);self.c['collector']['maintenance_guard_enabled']=False;self.c['trend_simulation']={'enabled_axes':['parity','size'],'initial_balance_per_strategy':100000};self.c['simulation']['max_stake']=2000;self.c['trend_simulation']['strategies']={a:{**settings(self.c,a),'win_limit':0} for a in ('parity','size')};self.now=datetime.now(timezone.utc).replace(microsecond=0);initialize(self.db,self.c)
 def tearDown(self):self.tmp.cleanup()
 def add(self,id,nums=BIG,seconds=0):self.db.import_records([{'id':id,'time':(self.now+timedelta(seconds=seconds)).isoformat(),'numbers':nums}],'test')
 def seed(self):
  for i in range(20):self.add(100+i,seconds=-210*(19-i))
 def test_two_orders_per_period_and_idempotence(self):
  self.seed();live_tick(self.db,self.c,self.now);live_tick(self.db,self.c,self.now);orders=self.db.query('SELECT * FROM trend_orders');self.assertEqual(len(orders),2);self.assertEqual({o['target_draw'] for o in orders},{120});self.assertEqual({o['direction'] for o in orders},{'单','大'});self.assertEqual(len(self.db.query('SELECT * FROM trend_ledger')),2)
 def test_additive_500_600_700_then_500(self):
  self.seed()
  for i in range(4):live_tick(self.db,self.c,self.now);self.add(120+i,ZERO)
  live_tick(self.db,{**self.c,'trend_simulation':{'enabled_axes':[]}},self.now)
  for axis in ('parity','size'):
   stakes=[o['stake']/100 for o in self.db.query('SELECT stake FROM trend_orders WHERE strategy=? ORDER BY id',(axis,))];self.assertEqual(stakes,[500,600,700,500])
  self.assertEqual(len(self.db.query("SELECT * FROM trend_orders WHERE status='settled'")),8)
 def test_axes_progress_independently(self):
  self.seed();live_tick(self.db,self.c,self.now);self.add(120,ZERO);live_tick(self.db,self.c,self.now);self.add(121,list(range(4,24)));live_tick(self.db,self.c,self.now)
  stakes={o['strategy']:o['stake']/100 for o in self.db.query('SELECT strategy,stake FROM trend_orders WHERE target_draw=122')};self.assertEqual(stakes,{'parity':500,'size':700})
 def test_tie_skips_both_and_preserves_progress(self):
  for i in range(20):self.add(100+i,BIG if i<10 else ZERO)
  with self.db.tx() as c:c.execute('UPDATE trend_accounts SET losses=1')
  live_tick(self.db,self.c,self.now);self.assertEqual(len(self.db.query('SELECT * FROM trend_orders')),0);self.assertTrue(all(a['losses']==1 for a in self.db.query('SELECT losses FROM trend_accounts')))
 def test_tie_continues_previous_order_after_restart(self):
  self.seed();live_tick(self.db,self.c,self.now)
  for i in range(10):self.add(120+i,ZERO)
  live_tick(DB(self.db.path),self.c,self.now)
  orders=self.db.query('SELECT * FROM trend_orders WHERE target_draw=130')
  self.assertEqual(len(orders),2)
  self.assertEqual({o['direction'] for o in orders},{'单','大'})
  self.assertTrue(all(o['ratio']==0.5 and o['stake']==60000 for o in orders))
 def test_tie_backtest_continues_last_executed_direction(self):
  self.seed()
  for i in range(11):self.add(120+i,ZERO)
  r=backtest(self.db.rows(),self.c,with_risk=False)
  orders=[o for o in r['orders'] if o['target_draw']==130]
  self.assertEqual(len(orders),2);self.assertEqual({o['direction'] for o in orders},{'单','大'})
  self.assertTrue(all(o['ratio']==0.5 for o in orders))
 def test_order_pagination_full_history_and_filters(self):
  from pc28.trend_strategy import order_page
  self.seed()
  for i in range(56):live_tick(self.db,self.c,self.now);self.add(120+i)
  for size in (10,20,50,100):
   first=order_page(self.db,page_size=size);last=order_page(self.db,page=999,page_size=size)
   self.assertEqual(first['total'],112);self.assertEqual(len(first['orders']),size)
   self.assertEqual(last['page'],last['pages'])
   self.assertEqual(last['orders'][-1]['target_draw'],120)
  filtered=order_page(self.db,'size',page_size=100)
  self.assertEqual(filtered['total'],56);self.assertTrue(all(o['strategy']=='size' for o in filtered['orders']))
  self.assertEqual(order_page(self.db,page=-1)['page'],1)
  with self.assertRaises(ValueError):order_page(self.db,page_size=30)
  with self.assertRaises(ValueError):order_page(self.db,'unknown')
 def test_disabled_axes_do_not_order_but_pending_still_settles(self):
  self.seed();self.c['trend_simulation']['enabled_axes']=['size'];live_tick(self.db,self.c,self.now)
  self.assertEqual({r['strategy'] for r in self.db.query('SELECT strategy FROM trend_orders')},{'size'})
  self.c['trend_simulation']['enabled_axes']=[];self.add(120);live_tick(self.db,self.c,self.now)
  self.assertEqual(len(self.db.query('SELECT * FROM trend_orders')),1)
  self.assertEqual(self.db.query('SELECT status FROM trend_orders')[0]['status'],'settled')
  from pc28.trend_strategy import order_page
  self.assertEqual(order_page(self.db,enabled_axes=[])['total'],0)
  self.assertEqual(order_page(self.db,enabled_axes=['size'])['total'],1)
 def test_order_pagination_empty(self):
  from pc28.trend_strategy import order_page
  r=order_page(self.db,page=999);self.assertEqual((r['page'],r['pages'],r['total'],r['orders']),(1,1,0,[]))
 def test_repair_pending_validates_raw_and_settles_once(self):
  import json
  from unittest.mock import patch
  from pc28.collector import repair_pending_once
  from pc28.model import convert
  self.seed();live_tick(self.db,self.c,self.now);self.add(121)
  self.c['collector']['work_fallback_enabled']=True
  result=convert(BIG)
  item={'drawNbr':120,'drawnAt':self.now.isoformat(),'kenoNbrs':BIG,'n1':result['a'],'n2':result['b'],'n3':result['c'],'drawNum':result['total'],'size':result['size'],'parity':result['parity']}
  with patch('pc28.collector.fetch',return_value=json.dumps({'items':[item]}).encode()):
   repaired=repair_pending_once(self.db,Path(self.tmp.name),self.c['collector'])
  self.assertEqual(repaired['inserted'],1);self.assertEqual(repaired['missing'],[])
  disabled={**self.c,'trend_simulation':{'enabled_axes':[]}}
  live_tick(self.db,disabled,self.now);live_tick(self.db,disabled,self.now)
  self.assertEqual(len(self.db.query("SELECT * FROM trend_ledger WHERE kind='settle'")),2)
  self.assertTrue(all(o['status']=='settled' for o in self.db.query('SELECT * FROM trend_orders')))
 def test_win_pause_exact_periods_and_restart(self):
  self.c['trend_simulation']['enabled_axes']=['size'];self.c['trend_simulation']['strategies']['size'].update(win_limit=2,pause_draws=3)
  self.seed();live_tick(self.db,self.c,self.now);self.add(120);live_tick(self.db,self.c,self.now);self.add(121);live_tick(self.db,self.c,self.now)
  a=self.db.query("SELECT * FROM trend_accounts WHERE strategy='size'")[0];self.assertEqual(a['pause_until'],124);self.assertEqual(a['win_streak'],2)
  for did in (122,123):self.add(did);live_tick(DB(self.db.path),self.c,self.now)
  self.assertEqual(len(self.db.query('SELECT * FROM trend_orders')),2)
  self.add(124);live_tick(DB(self.db.path),self.c,self.now)
  orders=self.db.query('SELECT * FROM trend_orders ORDER BY target_draw');self.assertEqual([o['target_draw'] for o in orders],[120,121,125]);self.assertEqual(orders[-1]['stake'],50000)
 def test_backtest_win_pause_matches_live_periods(self):
  self.c['trend_simulation']['strategies']['size'].update(win_limit=2,pause_draws=3)
  self.seed()
  for did in range(120,136):self.add(did)
  r=backtest(self.db.rows(),self.c,with_risk=False);ids=[o['target_draw'] for o in r['orders'] if o['strategy']=='size'];self.assertEqual(ids[:6],[120,121,125,126,130,131])
 def test_pending_conflict_does_not_settle(self):
  self.seed();live_tick(self.db,self.c,self.now);self.add(120)
  with self.db.tx() as c:c.execute("INSERT INTO conflicts(draw_id,resolved) VALUES(120,0)")
  live_tick(self.db,self.c,self.now);self.assertTrue(all(o['status']=='pending' for o in self.db.query('SELECT status FROM trend_orders')))
 def test_refund_separate_from_profit(self):
  for i in range(20):self.add(100+i,ZERO)
  live_tick(self.db,self.c,self.now);self.add(120,ZERO);disabled=copy.deepcopy(self.c);disabled['trend_simulation']['enabled_axes']=[];live_tick(self.db,disabled,self.now);s=status(self.db,disabled)
  self.assertEqual(s['combined']['net'],0);self.assertEqual(s['combined']['refunds'],2);self.assertEqual(s['combined']['profitable_win_rate'],0)
 def test_backtest_excludes_target_and_warms_up(self):
  for i in range(20):self.add(100+i,BIG if i<10 else ZERO)
  self.add(120,ZERO);self.add(121,BIG);r=backtest(self.db.rows(),self.c,with_risk=False);self.assertEqual(len(r['orders']),2);self.assertEqual({o['target_draw'] for o in r['orders']},{121});self.assertEqual({o['direction'] for o in r['orders']},{'小','双'})
 def test_gaps_reject_window(self):
  self.seed();h=self.db.rows();h[-1]['id']+=1;self.assertIsNone(signal(h,'size'))
 def test_backtest_both_reports_sum_to_combined(self):
  self.seed()
  for i in range(20):self.add(120+i,BIG if i%3 else ZERO)
  r=backtest(self.db.rows(),self.c,with_risk=False);self.assertEqual(sum(s['net'] for s in r['strategies'].values()),r['combined']['net']);self.assertEqual(sum(s['bets'] for s in r['strategies'].values()),r['combined']['bets'])
if __name__=='__main__':unittest.main()
