import copy,unittest
from pc28.follow_strategy import defaults,validate
from pc28.custom_strategy import save,tick
from pc28.strategies import control
from pc28 import trend_strategy
import test_follow_strategy as fixture
import test_history_backtest as history_fixture
from pc28.history_backtest import prepare,source_window,replay
import test_auth as auth_fixture


class DirectionGateTests(unittest.TestCase):
 setUp=fixture.FollowTests.setUp
 tearDown=fixture.FollowTests.tearDown
 received=fixture.FollowTests.received
 plan=fixture.FollowTests.plan

 def create(self,kind,direction):
  rule=defaults(self.s.cfg,kind);rule.update(direction=direction,axis='size' if direction in ('大','小') else 'parity',base_cents=10000,win_limit=0,stake_mode='fixed')
  key=save(self.s,{'name':kind+direction,'settings':rule})['id'];control(self.s,{'id':key,'enabled':True});return key

 def test_all_follow_kinds_keep_entry_rules_and_only_selected_direction(self):
  keys={(kind,d):self.create(kind,d) for kind in ('hourly','majority','previous','single') for d in ('大','小','单','双')}
  tick(self.s.db,self.s.cfg,self.now)
  for (kind,direction),key in keys.items():
   rows=self.s.db.query('SELECT direction FROM custom_orders WHERE strategy=?',(key,))
   expected=direction in (('小','双') if kind=='hourly' else ('大','单')) if kind!='single' else True
   self.assertEqual(bool(rows),expected,(kind,direction))
   if rows:self.assertEqual({r['direction'] for r in rows},{direction})

 def test_reversal_still_waits_for_its_original_imbalance_condition(self):
  for d in ('大','小','单','双'):
   s={k:copy.deepcopy(v) for k,v in self.s.cfg['simulation'].items() if k in ('mode','initial_balance','daily_loss_limit','max_drawdown','max_stake','round_restart','fixed','win_limit','pause_draws','win_loss_difference_limit')}
   s['fixed'].update(direction=d,lookback=30,threshold=6,run_length=0)
   key=save(self.s,{'name':'reversal'+d,'settings':s})['id'];control(self.s,{'id':key,'enabled':True})
  tick(self.s.db,self.s.cfg,self.now)
  self.assertEqual({r['direction'] for r in self.s.db.query('SELECT direction FROM custom_orders')},{'小','双'})

 def test_builtin_trend_can_choose_any_direction_and_settles_on_correct_axis(self):
  self.s.cfg['trend_simulation']['enabled_axes']=['size','parity']
  self.s.cfg['trend_simulation']['strategies']={a:{**trend_strategy.settings(self.s.cfg,a),'direction':'单' if a=='size' else '小','win_limit':0,'base_cents':10000,'lookback':2} for a in ('size','parity')}
  trend_strategy.live_tick(self.s.db,self.s.cfg,self.now)
  rows=self.s.db.query('SELECT * FROM trend_orders');self.assertEqual(len(rows),1);self.assertEqual(rows[0]['direction'],'单')
  self.s.db.import_records([{'id':136,'time':self.now.isoformat(),'numbers':fixture.BIG}],'test');self.received(self.now)
  trend_strategy.live_tick(self.s.db,self.s.cfg,self.now)
  self.assertEqual(self.s.db.query('SELECT pnl FROM trend_orders WHERE target_draw=136')[0]['pnl'],10000)

 def test_grouped_direction_values_are_not_accepted_as_fixed_directions(self):
  for direction in ('大小','单双','invalid'):
   s=defaults(self.s.cfg,'majority');s['direction']=direction;s['base_cents']=10000
   with self.assertRaises(ValueError):validate(self.s.cfg,s)


class DirectionReplayTests(unittest.TestCase):
 setUp=history_fixture.HistoryBacktestTests.setUp
 tearDown=history_fixture.HistoryBacktestTests.tearDown

 def test_replay_keeps_original_conditions_for_every_selected_direction(self):
  for kind in ('hourly','majority','previous'):
   for direction in ('大','小','单','双'):
    with self.subTest(kind=kind,direction=direction):
     settings=defaults(self.s.cfg,kind,'size' if direction in ('大','小') else 'parity');settings.update(direction=direction,base_cents=10000,win_limit=0,stake_mode='fixed')
     key=save(self.s,{'name':kind+direction,'settings':settings})['id'];p=prepare(self.s,{'strategy_id':key});rows,_=source_window(self.s.db.rows(),set(),p['as_of']);_,orders=replay(rows,p['snapshot'])
     expected=direction in (('小','双') if kind=='hourly' else ('大','单'))
     self.assertEqual(bool(orders),expected)
     if orders:self.assertEqual({o['direction'] for o in orders},{direction})


class DirectionApiTests(unittest.TestCase):
 setUp=auth_fixture.AuthTests.setUp
 tearDown=auth_fixture.AuthTests.tearDown
 setup=auth_fixture.AuthTests.setup

 def test_api_requires_an_explicit_direction_before_save_or_enable(self):
  self.setup()
  self.assertEqual(self.admin.call('/api/strategy-control',{'id':'size','enabled':True})[0],400)
  status,rule=self.admin.call('/api/strategy-defaults?kind=previous');self.assertEqual(status,200)
  self.assertEqual(self.admin.call('/api/strategy-save',{'name':'未选方向','settings':rule})[0],400)
  rule.update(direction='单',axis='parity')
  status,result=self.admin.call('/api/strategy-save',{'name':'只跟单','settings':rule});self.assertEqual(status,200,result)
  self.assertEqual(self.admin.call('/api/strategy-control',{'id':result['id'],'enabled':True})[0],200)
  rule['direction']='单双'
  self.assertEqual(self.admin.call('/api/strategy-save',{'name':'合并方向无效','settings':rule})[0],400)


if __name__=='__main__':unittest.main()
