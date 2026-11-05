import copy,json
from datetime import timedelta
import test_history_backtest as fixtures
from pc28.history_backtest import execute,source_window,replay
from pc28.strategies import catalog
from pc28.service import Service

class StrategyBacktestSummaryTests(fixtures.HistoryBacktestTests):
 def test_independent_windows_and_persistent_catalog(self):
  self.s.db.import_records([{'id':10+i,'time':(self.now-timedelta(days=60,seconds=210*(39-i))).isoformat(),'numbers':list(range(1,21))} for i in range(40)],'older')
  prepared,_,_=self.selected();result=execute(self.s,prepared)
  month_rows,coverage=source_window(self.s.db.rows(),set(),prepared['as_of'],months=1)
  expected,_=replay(month_rows,prepared['snapshot'])
  self.assertEqual(result['window_returns']['one_month']['net'],expected['net'])
  self.assertEqual(result['window_returns']['one_month']['coverage'],coverage)
  self.assertGreater(result['window_returns']['three_months']['coverage']['draws'],coverage['draws'])
  row=next(r for r in catalog(self.s.db,self.s.cfg)['strategies'] if r['id']=='size')
  self.assertEqual(row['backtest']['run_id'],result['run_id']);self.assertFalse(row['backtest']['parameters_changed'])
  restarted=Service(self.root);row=next(r for r in catalog(restarted.db,restarted.cfg)['strategies'] if r['id']=='size')
  self.assertEqual(row['backtest']['one_month']['net'],expected['net'])
  self.s.cfg['trend_simulation']['strategies']={'size':{**row['settings'],'base_cents':10000}}
  row=next(r for r in catalog(self.s.db,self.s.cfg)['strategies'] if r['id']=='size');self.assertTrue(row['backtest']['parameters_changed'])
 def test_deduplicated_task_and_progress(self):
  first=self.s.submit('history_backtest',{'strategy_id':'size'});second=self.s.submit('history_backtest',{'strategy_id':'size'})
  self.assertEqual(first,second)
  row=next(r for r in catalog(self.s.db,self.s.cfg,self.s.tasks)['strategies'] if r['id']=='size');self.assertEqual(row['backtest_task']['status'],'queued')
  key,kind,prepared=self.s.q.get_nowait();self.s.execute(kind,prepared,key)
  self.assertEqual(self.s.tasks[key]['percent'],100)
