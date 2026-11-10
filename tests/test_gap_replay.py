import copy,json
from datetime import timedelta
import test_history_backtest as fixtures
from pc28.follow_strategy import defaults
from pc28.custom_strategy import save
from pc28.strategies import control
from pc28.gap_replay import preview,apply,rebase_preserving_live_orders
class GapReplayTests(fixtures.HistoryBacktestTests):
 def strategy(self,pause=0):
  s=defaults(self.s.cfg,'majority');s.update(win_limit=pause,pause_draws=20,max_drawdown=100000,daily_loss_limit=100000)
  key=save(self.s,{'name':'gap','settings':s})['id'];control(self.s,{'id':key,'enabled':True})
  with self.s.db.tx() as c:
   c.execute('UPDATE custom_strategies SET created=? WHERE id=?',((self.now-timedelta(days=2)).isoformat(),key));c.execute('UPDATE strategy_edits SET created=?',((self.now-timedelta(days=2)).isoformat(),))
  return key
 def test_preview_isolated_and_apply_idempotent(self):
  key=self.strategy();before=self.s.db.query('SELECT * FROM custom_strategies');plan=preview(self.s.db,self.s.cfg,170,180,self.root)
  self.assertEqual(before,self.s.db.query('SELECT * FROM custom_strategies'));self.assertEqual(len(plan['orders']),11)
  self.assertTrue(all(o['target_draw']==o['cutoff_draw']+1 for o in plan['orders']))
  apply(self.s.db,plan);self.assertEqual(len(self.s.db.query('SELECT * FROM gap_order_tags')),11)
  self.assertEqual(len(self.s.db.query('SELECT * FROM custom_ledger')),22)
  actual=self.s.db.query('SELECT balance FROM custom_strategies WHERE id=?',(key,))[0]['balance'];self.assertEqual(actual,before[0]['balance']+sum(o['pnl'] for o in plan['orders']))
  with self.assertRaises(ValueError):apply(self.s.db,plan)
  self.assertEqual(len(self.s.db.query('SELECT * FROM custom_ledger')),22)
 def test_pause_not_forced_and_concurrency_guard(self):
  key=self.strategy(pause=2);plan=preview(self.s.db,self.s.cfg,170,180,self.root);self.assertLess(len(plan['orders']),11)
  self.assertTrue(any('暂停' in k for k in plan['summary'][0]['skips']))
  with self.s.db.tx() as c:c.execute('UPDATE custom_strategies SET balance=balance+1 WHERE id=?',(key,))
  with self.assertRaisesRegex(ValueError,'已变化'):apply(self.s.db,plan)
  self.assertFalse(self.s.db.query('SELECT * FROM gap_order_tags'))

 def test_later_pending_order_is_preserved(self):
  key=self.strategy();plan=preview(self.s.db,self.s.cfg,170,180,self.root)
  original={**plan['orders'][0],'target_draw':181,'cutoff_draw':180,'status':'pending','pnl':None,'hit':None,'outcome':None,'settled':None};original.pop('id')
  with self.s.db.tx() as c:
   columns=list(original);cur=c.execute('INSERT INTO custom_orders('+','.join(columns)+') VALUES('+','.join('?' for _ in columns)+')',tuple(original[k] for k in columns));oid=cur.lastrowid
   c.execute('UPDATE custom_strategies SET balance=balance-?,state=? WHERE id=?',(original['stake'],json.dumps({'cycle':99,'losses':1}),key))
  live_order=self.s.db.query('SELECT * FROM custom_orders WHERE id=?',(oid,))[0]
  adjusted=rebase_preserving_live_orders(self.s.db,plan);apply(self.s.db,adjusted)
  self.assertEqual(self.s.db.query('SELECT * FROM custom_orders WHERE id=?',(oid,))[0],live_order)
  state=json.loads(self.s.db.query('SELECT state FROM custom_strategies WHERE id=?',(key,))[0]['state']);self.assertEqual(state['cycle'],99)
