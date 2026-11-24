import unittest
from pc28.money_plans import validate,advance

class MoneyBandTests(unittest.TestCase):
 def settings(self,**kw):return validate(dict(mode='adjust',base_cents=1000,loss_increment_cents=100,win_decrement_cents=100,low_enter_cents=500,low_exit_cents=1000,low_loss_increment_cents=200,low_win_decrement_cents=100,high_enter_cents=5500,high_exit_cents=5000,high_loss_increment_cents=100,high_win_decrement_cents=200,**kw))
 def step(self,state,s,stake,outcome):return advance(state,s,dict(stake=stake,outcome=outcome))
 def test_low_strict_boundaries_and_hysteresis(self):
  s=self.settings();st=self.step({},s,500,'loss');self.assertNotIn('amount_band',st);self.assertEqual(st['next_cents'],600)
  st=self.step({},s,400,'loss');self.assertEqual((st['amount_band'],st['next_cents']),('low',600))
  st=self.step(st,s,1000,'loss');self.assertEqual((st['amount_band'],st['next_cents']),('low',1200))
  st=self.step(st,s,1200,'loss');self.assertNotIn('amount_band',st);self.assertEqual(st['next_cents'],1300)
 def test_high_inclusive_entry_strict_exit(self):
  s=self.settings();st=self.step({},s,5500,'win');self.assertEqual((st['amount_band'],st['next_cents']),('high',5300))
  st=self.step(st,s,5000,'win');self.assertEqual(st['next_cents'],4800);self.assertEqual(st['amount_band'],'high')
  st=self.step(st,s,4800,'win');self.assertNotIn('amount_band',st);self.assertEqual(st['next_cents'],4700)
 def test_refund_and_subscription_independence(self):
  s=self.settings();st=self.step({},s,400,'loss');copy=dict(st)
  self.assertEqual(self.step(st,s,600,'refund'),copy)
  other=self.step({},s,1100,'win');self.assertNotIn('amount_band',other);self.assertEqual(st,copy)
 def test_cap_clears_band_and_clamps(self):
  s=self.settings(max_increase_steps=1);st=self.step({},s,400,'loss');self.assertEqual(st['next_cents'],1000);self.assertNotIn('amount_band',st)
  s=self.settings(min_cents=300);self.assertEqual(self.step({},s,300,'win')['next_cents'],300)
 def test_zero_band_adjustment_does_not_count(self):
  s=self.settings();s['low_loss_increment_cents']=0
  st=self.step({},s,400,'loss');self.assertEqual(st['next_cents'],400);self.assertEqual(st.get('increases',0),0)
 def test_follow_strategy_cannot_own_band_rules(self):
  from pc28.follow_strategy import defaults,validate as validate_follow
  from pc28.config import DEFAULT
  for kind in ('previous','algorithm','algorithm_rank'):
   settings=defaults(DEFAULT,kind);settings.update(low_enter_cents=500,low_exit_cents=1000,low_loss_increment_cents=200)
   with self.assertRaisesRegex(ValueError,'金额配置管理'):validate_follow(DEFAULT,settings)
 def test_validation_and_legacy_plan(self):
  self.assertEqual(validate({'mode':'fixed'})['low_enter_cents'],0)
  s=self.settings();s['mode']='fixed'
  with self.assertRaises(ValueError):validate(s)
  s=self.settings();s['low_exit_cents']=100
  with self.assertRaises(ValueError):validate(s)
  s=self.settings();s['low_enter_cents']=401
  with self.assertRaises(ValueError):validate(s)
if __name__=='__main__':unittest.main()
