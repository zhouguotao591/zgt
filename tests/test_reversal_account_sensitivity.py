import unittest
import numpy as np
import pandas as pd
from scripts.candle_reversal_risk_sensitivity import account

class ReversalAccountTests(unittest.TestCase):
 def frame(self,days):
  return pd.DataFrame({'settled':np.array(days)*86400.+1735689600,'pnl':np.full(len(days),-100.),'hit':np.zeros(len(days))})
 def test_drawdown_stops_without_automatic_reset(self):
  f=self.frame([0]*200);mask=np.ones(len(f),dtype=bool)
  r=account(f,mask,mask,{'initial_balance':100000,'max_stake':400,'max_drawdown':15000,'daily_loss_limit':0})
  self.assertEqual(r['bets'],150);self.assertEqual(r['net'],-15000);self.assertEqual(r['max_dd'],15000);self.assertEqual(r['skips']['回撤限制'],50)
 def test_daily_limit_resets_on_next_day(self):
  f=self.frame([0]*20+[1]*20);mask=np.ones(len(f),dtype=bool)
  r=account(f,mask,mask,{'initial_balance':100000,'max_stake':400,'max_drawdown':0,'daily_loss_limit':1000})
  self.assertEqual(r['bets'],20);self.assertEqual(r['net'],-2000);self.assertEqual(r['skips']['当日亏损限制'],20)
 def test_gate_does_not_spend_on_blocked_orders(self):
  f=self.frame([0]*10);mask=np.ones(len(f),dtype=bool)
  r=account(f,mask,np.zeros(len(f),dtype=bool),{'initial_balance':100000,'max_stake':400,'max_drawdown':1000,'daily_loss_limit':1000})
  self.assertEqual(r['bets'],0);self.assertEqual(r['net'],0);self.assertEqual(r['capital_return'],0)

if __name__=='__main__':unittest.main()
