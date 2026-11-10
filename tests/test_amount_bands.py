import unittest,copy,json
from pc28.config import DEFAULT
from pc28.follow_strategy import defaults,validate,previous_amount
from pc28.amount_bands import ZERO
import test_previous_strategy as fixture
from pc28.custom_strategy import initialize

class BandTests(unittest.TestCase):
 def settings(self,**kw):
  s=defaults(DEFAULT,'single');s.update(base_cents=3000,loss_increment_cents=100,win_decrement_cents=100,low_enter_cents=500,low_exit_cents=1000,low_loss_increment_cents=200,low_win_decrement_cents=100,high_enter_cents=5500,high_exit_cents=5000,high_loss_increment_cents=100,high_win_decrement_cents=200,**kw);return validate(DEFAULT,s)
 def test_low_hysteresis_and_exact_boundaries(self):
  s=self.settings();state={};stake=500;values=[]
  for outcome in ['win','loss','loss','loss','loss','loss']:
   stake=previous_amount(stake,outcome,s,state);values.append(stake)
   state=json.loads(json.dumps(state))
  self.assertEqual(values,[400,600,800,1000,1200,1300]);self.assertNotIn('amount_band',state)
 def test_high_hysteresis_and_exact_boundaries(self):
  s=self.settings();state={};stake=5500;values=[]
  for outcome in ['loss','win','win','win','win','win']:
   stake=previous_amount(stake,outcome,s,state);values.append(stake)
  self.assertEqual(values,[5600,5400,5200,5000,4800,4700]);self.assertNotIn('amount_band',state)
 def test_high_entry_includes_threshold(self):
  s=self.settings();self.assertEqual(previous_amount(5500,'win',s,{}),5300);self.assertEqual(previous_amount(5499,'win',s,{}),5399)
 def test_disabled_legacy_refund_floor_and_cycle_reset(self):
  s=self.settings();legacy={k:v for k,v in s.items() if k not in ZERO};legacy=validate(DEFAULT,legacy);self.assertTrue(all(legacy[k]==0 for k in ZERO));self.assertEqual(previous_amount(400,'loss',legacy,{'amount_band':'low'}),500)
  state={'amount_band':'low','adjustment_count_version':2,'increase_steps':3,'decrease_steps':2};before=copy.deepcopy(state);self.assertEqual(previous_amount(600,'refund',s,state),600);self.assertEqual(state,before)
  self.assertEqual(previous_amount(100,'win',s,{}),100)
  s['max_increase_steps']=2;state={};self.assertEqual(previous_amount(400,'loss',s,state),600);self.assertEqual(previous_amount(600,'loss',s,state),3000);self.assertNotIn('amount_band',state);self.assertEqual(previous_amount(3000,'win',s,state),2900)
 def test_zero_adjustments_are_not_counted(self):
  s=self.settings();s['low_loss_increment_cents']=0;state={};self.assertEqual(previous_amount(400,'loss',s,state),400);self.assertEqual(state['increase_steps'],0)
 def test_invalid_thresholds_and_money(self):
  for update in [dict(low_enter_cents=1000),dict(high_exit_cents=5500),dict(low_enter_cents=0),dict(low_exit_cents=5100),dict(low_loss_increment_cents=-1),dict(high_win_decrement_cents=True)]:
   s=self.settings();s.update(update)
   with self.assertRaises(ValueError):validate(DEFAULT,s)

class MigrationTests(unittest.TestCase):
 setUp=fixture.PreviousStrategyTests.setUp
 tearDown=fixture.PreviousStrategyTests.tearDown
 def test_old_settings_only_gain_zero_fields_without_changing_account_state(self):
  from pc28.custom_strategy import save
  s=defaults(self.cfg,'previous');s.update(direction='大',axis='size');uid=save(self.s,{'name':'legacy','settings':s})['id'];old={k:v for k,v in s.items() if k not in ZERO}
  with self.s.db.tx() as c:c.execute('UPDATE custom_strategies SET settings=?,state=? WHERE id=?',(json.dumps(old),json.dumps({'next_stake':1234,'cycle':9}),uid))
  before=self.s.db.query('SELECT * FROM custom_strategies WHERE id=?',(uid,))[0];initialize(self.s.db);after=self.s.db.query('SELECT * FROM custom_strategies WHERE id=?',(uid,))[0]
  result=json.loads(after.pop('settings'));before.pop('settings');self.assertEqual(after,before);self.assertEqual(result,{**ZERO,**old});initialize(self.s.db);self.assertEqual(json.loads(self.s.db.query('SELECT settings FROM custom_strategies WHERE id=?',(uid,))[0]['settings']),result)

class LiveReplayTests(unittest.TestCase):
 setUp=fixture.PreviousStrategyTests.setUp
 tearDown=fixture.PreviousStrategyTests.tearDown
 create=fixture.PreviousStrategyTests.create
 draw=fixture.PreviousStrategyTests.draw
 step=fixture.PreviousStrategyTests.step
 def test_both_bands_live_settlement_matches_replay(self):
  from datetime import timedelta
  from pc28.history_backtest import prepare,source_window,replay
  key,_=self.create(base_cents=600,win_decrement_cents=100,loss_increment_cents=100,low_enter_cents=500,low_exit_cents=600,low_loss_increment_cents=200,low_win_decrement_cents=100,high_enter_cents=900,high_exit_cents=700,high_loss_increment_cents=100,high_win_decrement_cents=200)
  sequence=['大']*5+['小','大']*6+['大']*8
  for i,side in enumerate(sequence):self.step(self.draw(i,side)+timedelta(seconds=1))
  p=prepare(self.s,{'strategy_id':key});rows,_=source_window(self.s.db.rows(),set(),p['as_of']);_,expected=replay(rows,p['snapshot']);actual=self.s.db.query("SELECT target_draw,stake,pnl FROM custom_orders WHERE strategy=? AND status='settled' ORDER BY target_draw",(key,))
  self.assertEqual([(r['target_draw'],r['stake'],r['pnl']) for r in actual],[(r['target_draw'],r['stake'],r['pnl']) for r in expected]);self.assertTrue(any(r['stake']<500 for r in actual));self.assertTrue(any(r['stake']>900 for r in actual))

if __name__=='__main__':unittest.main()
