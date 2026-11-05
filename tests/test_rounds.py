import copy,json,tempfile,unittest
from pathlib import Path
from datetime import datetime,timezone,timedelta
from pc28.config import DEFAULT
from pc28.db import DB
from pc28.router import tick,settle
from pc28.engine import Prepared,replay
from pc28.model import Strategy
class RoundTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent/'data');self.db=DB(Path(self.tmp.name)/'db.sqlite3');self.c=copy.deepcopy(DEFAULT);self.c['collector']['maintenance_guard_enabled']=False;self.c['simulation'].update(enabled=True,round_restart='condition_reset',max_stake=2000);self.c['simulation']['fixed'].update(direction='小',threshold=8,max_bets=2);self.now=datetime.now(timezone.utc).replace(microsecond=0)
  for i in range(30):self.add(100+i,True,-210*(29-i))
 def tearDown(self):self.tmp.cleanup()
 def add(self,id,big,seconds=0):self.db.import_records([{'id':id,'time':(self.now+timedelta(seconds=seconds)).isoformat(),'numbers':list(range(1,21)) if big else list(range(4,24))}],'test')
 def test_unified_strategy_orders_filters_and_preserves_history(self):
  from pc28.strategies import order_page
  from pc28.trend_strategy import initialize,live_tick
  initialize(self.db,self.c);tick(self.db,self.c,self.now)
  self.c['trend_simulation']['enabled_axes']=['parity','size'];live_tick(self.db,self.c,self.now)
  result=order_page(self.db,self.c)
  self.assertEqual(result['total'],3);self.assertEqual({o['strategy'] for o in result['orders']},{'legacy','parity','size'})
  legacy=order_page(self.db,self.c,'legacy');self.assertEqual(legacy['total'],1)
  self.assertEqual(legacy['orders'][0]['direction'],'小');self.assertEqual(legacy['orders'][0]['max_bets'],2)
  self.assertEqual(set(legacy['enabled_strategies']),{'legacy','parity','size'})
  self.c['simulation']['enabled']=False
  self.assertEqual(order_page(self.db,self.c)['total'],2)
  self.assertEqual(order_page(self.db,self.c,'legacy',active_only=False)['total'],1)
  self.assertEqual(len(self.db.query('SELECT * FROM orders')),1)
 def test_round_stops_and_requires_condition_reset(self):
  self.c['simulation']['fixed']['max_losses']=2
  tick(self.db,self.c,self.now);self.add(130,True);tick(self.db,self.c,self.now);self.add(131,True);r=tick(self.db,self.c,self.now)
  self.assertEqual(r['action'],'skip');self.assertIn('条件先退出',r['reason']);self.assertEqual(len(self.db.query('SELECT * FROM orders')),2);self.assertEqual(self.db.query('SELECT orders_count,status FROM rounds')[0],{'orders_count':2,'status':'ended'})
  # Repeated checks and restart cannot open another round under the same condition.
  tick(DB(self.db.path),self.c,self.now);self.assertEqual(len(self.db.query('SELECT * FROM orders')),2)
  for i in range(132,144):self.add(i,False);tick(self.db,self.c,self.now)
  self.assertFalse(json.loads(self.db.query('SELECT state FROM accounts')[0]['state'])['waiting_rearm'])
  for i in range(144,163):self.add(i,True);tick(self.db,self.c,self.now)
  o=self.db.query('SELECT round_id,round_attempt FROM orders ORDER BY id DESC LIMIT 1')[0];self.assertEqual(o,{'round_id':2,'round_attempt':1})
 def test_count_limit_rechecks_condition_and_resets_stake(self):
  tick(self.db,self.c,self.now);self.add(130,True);tick(self.db,self.c,self.now);self.add(131,True)
  r=tick(self.db,self.c,self.now);self.assertEqual(r['action'],'order')
  orders=self.db.query('SELECT * FROM orders ORDER BY target_draw')
  self.assertEqual([o['stake'] for o in orders],[10000,20000,10000])
  self.assertEqual([(o['round_id'],o['round_attempt']) for o in orders],[(1,1),(1,2),(2,1)])
  tick(DB(self.db.path),self.c,self.now);self.assertEqual(len(self.db.query('SELECT * FROM orders')),3)
 def test_count_limit_waits_only_until_window_qualifies(self):
  self.c['simulation']['fixed'].update(lookback=4,threshold=4,run_length=4)
  tick(self.db,self.c,self.now);self.add(130,True);tick(self.db,self.c,self.now);self.add(131,False)
  self.assertEqual(tick(self.db,self.c,self.now)['action'],'skip');self.assertEqual(len(self.db.query('SELECT * FROM orders')),2)
  for i in range(132,136):self.add(i,True);tick(self.db,self.c,self.now)
  latest=self.db.query('SELECT * FROM orders ORDER BY id DESC LIMIT 1')[0]
  self.assertEqual((latest['target_draw'],latest['round_attempt'],latest['stake']),(136,1,10000))
 def test_previous_count_stop_adopts_new_rule(self):
  tick(self.db,self.c,self.now);self.add(130,True);tick(self.db,self.c,self.now);self.add(131,True);settle(self.db,self.c)
  with self.db.tx() as c:
   st=json.loads(c.execute('SELECT state FROM accounts').fetchone()[0]);st['waiting_rearm']=True;c.execute('UPDATE accounts SET state=?',(json.dumps(st),))
  self.assertEqual(tick(self.db,self.c,self.now)['action'],'order')
 def test_count_limit_backtest_uses_same_reentry_rule(self):
  for i in range(130,136):self.add(i,True)
  p=Prepared(self.db.rows());st=Strategy(direction='小',threshold=8,max_bets=2,max_losses=3)
  a=replay(p,st,self.c['odds'],restart_policy='condition_reset',trace=True);b=replay(p,st,self.c['odds'],restart_policy='immediate',trace=True)
  self.assertEqual(a['bets'],6);self.assertEqual(a['orders'],b['orders'])
 def test_reversal_win_pause_restarts_with_reassessment(self):
  self.c['simulation'].update(win_limit=2,pause_draws=3);self.c['simulation']['fixed']['max_bets']=8
  tick(self.db,self.c,self.now);self.add(130,False);tick(self.db,self.c,self.now);self.add(131,False);tick(self.db,self.c,self.now)
  state=json.loads(self.db.query('SELECT state FROM accounts')[0]['state']);self.assertEqual(state['pause_until'],134);self.assertFalse(state['active']);self.assertFalse(state['waiting_rearm'])
  for did in (132,133):self.add(did,True);tick(self.db,self.c,self.now)
  self.assertEqual(len(self.db.query('SELECT * FROM orders')),2)
  self.add(134,True);tick(DB(self.db.path),self.c,self.now);rows=self.db.query('SELECT * FROM orders ORDER BY target_draw');self.assertEqual([r['target_draw'] for r in rows],[130,131,135]);self.assertEqual(rows[-1]['round_attempt'],1)
 def test_every_round_order_limit_is_total_not_losses(self):
  self.c['simulation']['fixed'].update(direction='单',threshold=8,max_bets=8);self.c['simulation']['round_restart']='immediate'
  # Seed must favor even to trigger buying odd; use direct history axis adjustment only in isolated fixture.
  with self.db.tx() as c:c.execute("UPDATE draws SET parity='双'")
  for i in range(130,140):tick(self.db,self.c,self.now);self.add(i,True);settle(self.db,self.c)
  rounds=self.db.query('SELECT orders_count FROM rounds ORDER BY id');self.assertEqual(rounds[0]['orders_count'],8);self.assertTrue(all(r['orders_count']<=8 for r in rounds))
 def test_backtest_condition_reset_differs_from_immediate(self):
  for i in range(130,136):self.add(i,True)
  p=Prepared(self.db.rows());st=Strategy(direction='小',threshold=8,max_bets=2,max_losses=2)
  a=replay(p,st,self.c['odds'],restart_policy='condition_reset');b=replay(p,st,self.c['odds'],restart_policy='immediate');self.assertEqual(a['bets'],2);self.assertEqual(b['bets'],6)
if __name__=='__main__':unittest.main()
