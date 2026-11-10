import unittest
from datetime import timedelta
import test_auth as auth_fixture
import test_previous_strategy as fixture
from pc28.follow_strategy import defaults
from pc28.history_backtest import prepare,source_window,replay

class AxisAPITests(unittest.TestCase):
 setUp=auth_fixture.AuthTests.setUp
 tearDown=auth_fixture.AuthTests.tearDown
 setup=auth_fixture.AuthTests.setup
 def test_save_enable_both_axis_types_without_fixed_direction(self):
  self.setup()
  for axis in ('size','parity'):
   settings=defaults(self.s.cfg,'previous',axis);settings['direction']=None
   code,data=self.admin.call('/api/strategy-save',{'name':'previous '+axis,'settings':settings});self.assertEqual(code,200,data)
   code,data=self.admin.call('/api/strategy-control',{'id':data['id'],'enabled':True});self.assertEqual(code,200,data)
  settings['axis']='invalid';self.assertEqual(self.admin.call('/api/strategy-save',{'name':'invalid','settings':settings})[0],400)

class AxisLiveTests(unittest.TestCase):
 setUp=fixture.PreviousStrategyTests.setUp
 tearDown=fixture.PreviousStrategyTests.tearDown
 create=fixture.PreviousStrategyTests.create
 draw=fixture.PreviousStrategyTests.draw
 step=fixture.PreviousStrategyTests.step
 def test_each_axis_follows_the_previous_draw_one_order_per_period(self):
  keys={axis:self.create(axis)[0] for axis in ('size','parity')}
  for i,side in enumerate(['大','小','大','小','小','大']):self.step(self.draw(i,side)+timedelta(seconds=1))
  for axis,key in keys.items():
   actual=self.s.db.query('SELECT * FROM custom_orders WHERE strategy=? ORDER BY target_draw',(key,));self.assertEqual(len(actual),6);self.assertEqual(len({r['target_draw'] for r in actual}),6)
   for order in actual:
    previous=self.s.db.query('SELECT * FROM draws WHERE id=?',(order['cutoff_draw'],))[0];self.assertEqual(order['direction'],previous[axis])
   prepared=prepare(self.s,{'strategy_id':key});rows,_=source_window(self.s.db.rows(),set(),prepared['as_of']);_,expected=replay(rows,prepared['snapshot']);settled=[r for r in actual if r['status']=='settled'];self.assertEqual([(r['target_draw'],r['direction'],r['stake'],r['pnl']) for r in settled],[(r['target_draw'],r['direction'],r['stake'],r['pnl']) for r in expected])

if __name__=='__main__':unittest.main()
