import unittest
from unittest.mock import patch
import numpy as np,pandas as pd
from scripts.research_four_hour_support import simulate,setups

BASE=1767225600

class SupportTests(unittest.TestCase):
 def fixture(self):
  b=pd.DataFrame([{'open':0,'close':100,'low':-20,'high':110,'count':69,'end':BASE},
                  {'open':-100,'close':-50,'low':-110,'high':-40,'count':69,'end':BASE+14400},
                  {'open':-50,'close':100,'low':-60,'high':110,'count':69,'end':BASE+28800}])
  f=pd.DataFrame({'draw':[1,2,3],'decision':[BASE+16,BASE+14416,BASE+28816],'settled':[BASE+210,BASE+14610,BASE+29010],'big':[100.,100.,100.],'small':[-100.,-100.,-100.]})
  plan=[{'ready':True,'support':-30},{'ready':False,'support':None},{'ready':True,'support':-70}]
  return f,b,plan
 def test_exit_before_updating_new_bullish_low_and_reverse(self):
  f,b,plan=self.fixture()
  with patch('scripts.research_four_hour_support.setups',return_value=plan):d,events,_=simulate(f,b)
  self.assertEqual(list(d),[1,-1,1]);self.assertEqual(events[1]['anchor'],-20)
  self.assertEqual(events[1]['effective_draw'],2);self.assertEqual(events[2]['effective_draw'],3)
 def test_period_break_uses_only_previous_result(self):
  f,b,plan=self.fixture();f.decision=[BASE+16,BASE+226,BASE+436];f.big=[-30.,100.,100.]
  with patch('scripts.research_four_hour_support.setups',return_value=plan):d,events,_=simulate(f,b,exit_mode='period')
  self.assertEqual(list(d),[1,-1,-1]);self.assertEqual(events[1]['effective_draw'],2)
 def test_wick_below_stop_does_not_trigger_close_exit(self):
  f,b,plan=self.fixture();b.loc[1,['open','close','low']]=[100,90,-50]
  with patch('scripts.research_four_hour_support.setups',return_value=plan):d,events,_=simulate(f,b)
  self.assertEqual(d[1],1);self.assertFalse(any(e['kind']=='break' for e in events))
 def test_waits_for_support_breakout_and_ignores_future(self):
  rows=[]
  for i in range(24):rows.append(dict(open=1000-i*10,close=995-i*10,low=945-i*10,high=1050-i*10,count=69,end=BASE+i*14400))
  rows.extend([dict(open=700,close=690,low=680,high=710,count=69,end=BASE+24*14400),dict(open=690,close=695,low=682,high=705,count=69,end=BASE+25*14400),dict(open=695,close=700,low=683,high=708,count=69,end=BASE+26*14400),dict(open=700,close=730,low=695,high=740,count=69,end=BASE+27*14400)])
  b=pd.DataFrame(rows);self.assertTrue(setups(b)[-1]['ready'])
  self.assertEqual(setups(b.iloc[:-1]),setups(b)[:-1])
  b.loc[27,'close']=705;self.assertFalse(setups(b)[-1]['ready'])
 def test_collection_boundary_does_not_backdate_entry(self):
  f,b,plan=self.fixture();f.decision=[BASE+15,BASE+16,BASE+226]
  with patch('scripts.research_four_hour_support.setups',return_value=plan):d,_,_=simulate(f,b)
  self.assertEqual(list(d),[0,1,1])

if __name__=='__main__':unittest.main()
