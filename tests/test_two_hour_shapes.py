import unittest
import pandas as pd,numpy as np
from scripts.research_two_hour_shapes import classify,signals
from scripts.research_two_hour_direction import candles
import test_trend_risk as fixture

class ShapeWaveTests(unittest.TestCase):
 def bars(self):
  data=fixture.TrendMathTests().bars()
  data[-3].update(open=27,close=30,high=30.1,low=26.9)
  data[-2].update(open=30,close=31,high=33,low=29.9)
  data[-1].update(open=31,close=29,high=32,low=28.9)
  b=pd.DataFrame(data);b.index=pd.date_range('2026-01-01',periods=len(b),freq='2h',tz='UTC');b['count']=34;b['end']=b.index.as_unit('ns').astype('int64')/1e9+7200
  return b
 def test_right_candle_confirmation_never_backdates_pivot(self):
  b=self.bars();flags,events,_=classify(b,'turn')
  self.assertEqual(flags[-1],-1);self.assertEqual(flags[-2],0)
  self.assertEqual(events[-1]['bar'],len(b)-1)
  self.assertEqual(events[-1]['time'],b.end.iloc[-1])
 def test_extra_breakout_waits_for_later_candle(self):
  b=self.bars();flags,_,setup=classify(b,'confirm')
  self.assertEqual(flags[-1],0);self.assertEqual(setup[-1]['side'],-1)
  row={**b.iloc[-1].to_dict(),'open':29,'close':26,'high':29.1,'low':25.9,'end':b.end.iloc[-1]+7200}
  b.loc[b.index[-1]+pd.Timedelta(hours=2)]=row
  flags,events,_=classify(b,'confirm');self.assertEqual(flags[-1],-1);self.assertEqual(flags[-2],0)
 def test_future_data_and_equity_origin_do_not_change_old_classification(self):
  b=self.bars()
  for kind in ('turn','confirm','structure'):
   prefix=classify(b.iloc[:-1],kind)[0];full=classify(b,kind)[0];self.assertTrue(np.array_equal(prefix,full[:-1]))
   shifted=b.copy()
   for k in ('open','high','low','close'):shifted[k]=shifted[k]*7+100
   self.assertTrue(np.array_equal(full,classify(shifted,kind)[0]))
 def test_state_becomes_usable_only_after_closed_bar_collection(self):
  b=self.bars();flags,_,_=classify(b,'turn');q=pd.DataFrame({'decision':[b.end.iloc[-1]+15,b.end.iloc[-1]+16]})
  d,_=signals(q,b,flags);self.assertEqual(list(d),[0,-1])

if __name__=='__main__':unittest.main()
