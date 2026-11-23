import unittest
import numpy as np
import pandas as pd
from scripts.research_two_hour_direction import candles,directions,waves,metrics,values

class TwoHourStudyTests(unittest.TestCase):
 def frame(self,n=1000):
  t=1767225600+np.arange(n)*210+100;actual=np.where(((t-1767225600)//7200)%2, -1,1)
  return pd.DataFrame(dict(draw=np.arange(n)+1,decision=t-15,settled=t,actual=actual,big=actual*100.,small=-actual*100.,big198=np.where(actual>0,98.,-100.),small198=np.where(actual<0,98.,-100.)))
 def test_candle_unit_and_exact_availability_boundary(self):
  f=self.frame();b=candles(f);self.assertEqual(b.end.iloc[0],1767225600+7200)
  q=f.iloc[:2].copy();q.decision=[b.end.iloc[8]+15,b.end.iloc[8]+16]
  _,known,idx=directions(q,b)
  self.assertEqual(list(idx),[7,8]);self.assertEqual(list(known),[False,True])
 def test_past_signal_unchanged_by_future_outcomes(self):
  f=self.frame();original,_,_=directions(f,candles(f));changed=f.copy();changed.loc[800:,'big']=-999
  future,_,_=directions(changed,candles(changed))
  for k in ('body','inverse','confirm','ema','strong','count'):
   self.assertTrue(np.array_equal(original[k][:800],future[k][:800]),k)
  self.assertTrue((original['body']==1).any());self.assertTrue((original['body']==-1).any())
 def test_profit_and_hit_are_distinct_from_wave_success(self):
  f=self.frame(4);d=np.array([1,-1,1,0]);m=metrics(f,d,np.ones(4,dtype=bool))
  self.assertEqual(m['bets'],3);self.assertEqual(m['net'],100);self.assertAlmostEqual(m['hit'],2/3)
  self.assertEqual(m['net'],values(f,d).sum())
 def test_wave_exit_no_duplicate_or_future_trades(self):
  f=self.frame(6);f.big=[100,-100,100,100,-100,100];f.small=-f.big
  d=np.ones(6,dtype=int);out,records=waves(f,d)
  self.assertEqual(list(out),[1,1,0,0,0,0]);self.assertEqual(len(records),1)
  self.assertEqual(records[0]['bets'],2);self.assertEqual(records[0]['net'],0)
  self.assertTrue(records[0]['closed'])
 def test_two_hour_exit_uses_prior_profit_not_next_draw_result(self):
  f=self.frame(8);f.big=[100,100,-100,-100,100,100,100,100];f.small=-f.big
  out,records=waves(f,np.ones(8,dtype=int),stop=-float('inf'),checkpoints=np.arange(8)//2)
  self.assertEqual(list(out),[1,1,1,1,0,0,0,0]);self.assertEqual(records[0]['net'],0)
  self.assertEqual(records[0]['peak'],200)

if __name__=='__main__':unittest.main()
