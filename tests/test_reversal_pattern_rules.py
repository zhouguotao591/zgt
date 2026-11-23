import unittest
import numpy as np
import pandas as pd
from scripts.reversal_pattern_rules import detect,policy,FAMILIES
from scripts.analyze_strategy_regimes import bars_for
from tests.test_regime_study import RegimeStudyTests

def candles(rows):
 b=pd.DataFrame(rows,columns=['open','high','low','close']);b['orders']=10;b['end']=1735689600+(np.arange(len(b))+1)*3600
 b.index=pd.date_range('2025-01-01',periods=len(b),freq='h',tz='UTC');return b

class ReversalPatternTests(unittest.TestCase):
 def test_three_bar_fractal_is_not_backdated(self):
  b=candles([[8,10,7,9],[10,12,9,11],[10,11,8,9]])
  p=detect(b)['patterns'];self.assertFalse(p['top3'][1]);self.assertTrue(p['top3'][2])
  altered=b.copy();altered.loc[altered.index[2],'high']=14
  self.assertFalse(detect(altered)['patterns']['top3'][2])
 def test_five_bar_fractal_waits_for_two_right_bars(self):
  b=candles([[7,9,5,7],[8,10,6,8],[10,15,8,12],[9,11,7,9],[8,10,6,8]])
  flags=detect(b)['patterns']['top5'];self.assertFalse(flags[:4].any());self.assertTrue(flags[4])
 def test_gapless_morning_evening_and_engulfing(self):
  prefix=[[100-i,101-i,98-i,99-i] for i in range(25)]
  b=candles(prefix+[[75,76,64,65],[65,66,63.5,64.5],[64.5,72,63.5,71]])
  p=detect(b)['patterns'];self.assertTrue(p['morning_nogap'][-1]);self.assertFalse(p['morning_gap'].any())
  mirror=b.copy();mirror['open']=-b.open;mirror['close']=-b.close;mirror['high']=-b.low;mirror['low']=-b.high
  self.assertTrue(detect(mirror)['patterns']['evening_nogap'][-1])
  pair=candles(prefix+[[75,76,72,73],[73,77,72,76]])
  self.assertTrue(detect(pair)['patterns']['bull_engulf'][-1])
 def test_patterns_and_policies_do_not_use_future_outcomes(self):
  f=RegimeStudyTests().fixture();later=f.copy();later.loc[4000:,'pnl']=-999999
  for hours in (1,4):
   b=bars_for(f,hours);x=detect(b);other=bars_for(later,hours);y=detect(other)
   for family in FAMILIES:
    for mode in ('raw','level','confirm'):
     with self.subTest(hours=hours,family=family,mode=mode):self.assertTrue(np.array_equal(policy(f,b,x,family,mode)[0][:4000],policy(later,other,y,family,mode)[0][:4000]))
 def test_breakout_confirmation_is_later_and_keeps_original_pivot(self):
  b=candles([[1,2,0,1],[2,3,1,2],[12,13,1,12],[8,9,1,8],[11,12,1,11],[7,8,-2,-1]])
  n=len(b);x={'known':np.ones(n,dtype=bool),'patterns':{'bottom3':np.array([False,False,True,False,False,False]),'top3':np.zeros(n,dtype=bool)},'families':{'fractal3':{'low':np.zeros(n),'high':np.full(n,10.),'bull_near':np.ones(n,dtype=bool)}},'support':np.zeros(n),'resistance':np.full(n,20.),'lower':np.zeros(n),'middle':np.full(n,10.),'upper':np.full(n,20.)}
  f=pd.DataFrame({'decision':b.end.to_numpy()+1});mask,events=policy(f,b,x,'fractal3','confirm',True)
  self.assertFalse(mask[2]);self.assertFalse(mask[3]);self.assertTrue(mask[4]);self.assertEqual(events[0]['confirmation_index'],4);self.assertEqual(events[0]['pattern_center_index'],1)
  self.assertFalse(mask[5]);self.assertEqual(events[-1]['setup_low'],0.)
 def test_bearish_signal_takes_priority(self):
  f=RegimeStudyTests().fixture();b=bars_for(f,1);x=detect(b)
  for family,(bull,bear) in FAMILIES.items():
   x['patterns'][bull]=np.ones(len(b),dtype=bool);x['patterns'][bear]=np.ones(len(b),dtype=bool)
   for mode in ('raw','level','confirm'):self.assertFalse(policy(f,b,x,family,mode)[0].any())

if __name__=='__main__':unittest.main()
