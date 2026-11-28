import unittest,json,copy
from datetime import timedelta
import test_history_backtest as fixture
from pc28.strategy_hourly import aggregate,page
from pc28.history_backtest import prepare,execute


class HourlyTests(unittest.TestCase):
 def test_timezone_refunds_empty_hours_and_type_filter(self):
  coverage={'actual_start':'2026-11-01T23:00:00+08:00','actual_end':'2026-11-02T02:59:00+08:00'}
  orders=[dict(beijing_time='2026-11-01T15:20:00+00:00',direction='大',previous_type='size',stake=200,pnl=198,outcome='win',hit=1),dict(beijing_time='2026-11-02 00:20:00',direction='小单',previous_type='小单',stake=300,pnl=0,outcome='refund',hit=1),dict(beijing_time='2026-11-02 00:30:00',direction='单',previous_type='parity',stake=500,pnl=-500,outcome='loss',hit=0)]
  r=aggregate(orders,coverage)
  self.assertEqual(len(r['hours']),4);self.assertEqual(len(r['clock']),24)
  b=r['clock'][0];self.assertEqual((b['bets'],b['wins'],b['refunds'],b['losses']),(2,0,1,1));self.assertEqual(b['win_rate'],0);self.assertEqual(b['hit_rate'],.5)
  self.assertEqual(r['clock'][23]['pnl'],1.98)
  self.assertIsNone(r['clock'][1]['win_rate']);self.assertEqual(r['clock'][1]['bets'],0)
  filtered=aggregate(orders,coverage,'小单');self.assertEqual(sum(b['bets'] for b in filtered['hours']),1)
  self.assertEqual(sum(b['pnl'] for b in r['hours']),sum(o['pnl'] for o in orders)/100)


class HourlyReplayTests(unittest.TestCase):
 def setUp(self):fixture.HistoryBacktestTests.setUp(self)
 def tearDown(self):fixture.HistoryBacktestTests.tearDown(self)
 def prepared(self):return prepare(self.s,{'strategy_id':'size','range_mode':'count','periods':90,'hourly':True})
 def test_continuous_replay_cache_and_live_isolation(self):
  before={t:self.s.db.query('SELECT * FROM '+t) for t in ('accounts','orders','ledger','trend_accounts','trend_orders','trend_ledger','custom_orders')}
  p=self.prepared();r=execute(self.s,p);r2=execute(self.s,self.prepared());self.assertEqual(r['run_id'],r2['run_id'])
  ordinary=execute(self.s,{**p,'hourly':False})
  a=self.s.db.query('SELECT record FROM history_backtest_orders WHERE run_id=? ORDER BY seq',(r['run_id'],));b=self.s.db.query('SELECT record FROM history_backtest_orders WHERE run_id=? ORDER BY seq',(ordinary['run_id'],));self.assertEqual(a,b)
  d=page(self.s.db,r['run_id']);self.assertEqual(sum(x['bets'] for x in d['hours']),r['bets']);self.assertEqual(sum(x['bets'] for x in d['clock']),r['bets']);self.assertAlmostEqual(sum(x['pnl'] for x in d['hours']),r['net'])
  for t,rows in before.items():self.assertEqual(rows,self.s.db.query('SELECT * FROM '+t))
  self.assertFalse(self.s.db.query('SELECT * FROM strategy_backtest_summary'))
  changed=copy.deepcopy(p);changed['snapshot']['settings']['base_cents']+=100
  self.assertNotEqual(execute(self.s,changed)['run_id'],r['run_id'])
  changed=copy.deepcopy(p);changed['range']['periods']=80
  self.assertNotEqual(execute(self.s,changed)['run_id'],r['run_id'])
 def test_drilldown_and_validation(self):
  r=execute(self.s,self.prepared());d=page(self.s.db,r['run_id']);hour=next(x for x in d['hours'] if x['bets'])
  detail=page(self.s.db,r['run_id'],slot=hour['key']);self.assertEqual(detail['total'],hour['bets']);self.assertLessEqual(len(detail['orders']),50)
  clock=str(__import__('datetime').datetime.fromisoformat(hour['key']).hour)
  d=page(self.s.db,r['run_id'],mode='clock',slot=clock);self.assertEqual(d['total'],d['clock'][int(clock)]['bets'])
  with self.assertRaises(ValueError):page(self.s.db,r['run_id'],mode='bad')
  with self.assertRaises(ValueError):page(self.s.db,r['run_id'],mode='clock',slot='24')
  with self.assertRaises(ValueError):page(self.s.db,'missing')
 def test_task_dedup_uses_full_request(self):
  args={'strategy_id':'size','range_mode':'count','periods':80,'hourly':True}
  a=self.s.submit('history_backtest',args);b=self.s.submit('history_backtest',args);c=self.s.submit('history_backtest',{**args,'periods':90})
  self.assertEqual(a,b);self.assertNotEqual(a,c)
