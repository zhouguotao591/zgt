import unittest
import numpy as np
import pandas as pd
from scripts.analyze_strategy_regimes import bars_for,flags_for,gate
from pc28.trend_risk import DEFAULT,analyze

class RegimeStudyTests(unittest.TestCase):
 def fixture(self):
  rng=np.random.default_rng(123);n=6000;seconds=1735689600+np.arange(n)*210
  pnl=rng.choice([-100.,100.],n)
  return pd.DataFrame({'draw':np.arange(n)+1,'settled':seconds+5,'decision':seconds-100,'pnl':pnl,'stake':100.,'hit':(pnl>0).astype(int)})
 def test_calendar_and_production_statuses(self):
  f=self.fixture();bars=bars_for(f,1)
  self.assertEqual(bars.index[0].year,2025);self.assertGreater(bars.end.iloc[0],1700000000)
  statuses,_=flags_for(bars,DEFAULT)
  self.assertGreater(sum(s!='unknown' for s in statuses),100)
  for i in range(25,len(bars),13):
   sample=[{'time':idx.isoformat(),**{k:int(row[k]) if k=='orders' else float(row[k]) for k in ('open','high','low','close','orders')}} for idx,row in bars.iloc[max(0,i-130):i+1].iterrows()]
   self.assertEqual(statuses[i],analyze(sample,DEFAULT,int(bars.end.iloc[i]))['status'])
 def test_future_outcomes_do_not_change_past_gate(self):
  f=self.fixture();first=gate(f,DEFAULT,{})[0]
  changed=f.copy();changed.loc[4000:,'pnl']=-999999.
  second=gate(changed,DEFAULT,{})[0]
  self.assertTrue(np.array_equal(first[:4000],second[:4000]))
  self.assertGreater(int(first.sum()),500);self.assertLess(int(first.sum()),len(f))

if __name__=='__main__':unittest.main()
