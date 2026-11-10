import copy,json,unittest
from datetime import timedelta
from pc28 import win_pause,trend_strategy,router
from pc28.config import DEFAULT,validate
from pc28.custom_strategy import save,tick
from pc28.follow_strategy import defaults,validate as validate_follow
from pc28.strategies import control,catalog
from pc28.service import Service
from pc28.history_backtest import prepare,source_window,replay
import test_custom_strategy as custom_fixture
import test_follow_strategy as follow_fixture
import test_history_backtest as history_fixture


class DifferenceTests(unittest.TestCase):
 def test_both_signs_refund_and_permanent_pause(self):
  for outcomes,expected in [(['win','loss','refund','win','win'],2),(['loss','win','refund','loss','loss'],-2)]:
   state={}
   for target,outcome in enumerate(outcomes,100):win_pause.settled(state,outcome,target,0,2,2)
   self.assertEqual(state['win_loss_difference'],expected)
   self.assertTrue(state['difference_paused'])
   self.assertIn('需手动',win_pause.paused(state,1000000))
   state=json.loads(json.dumps(state))
   self.assertIn('胜负差值暂停',win_pause.paused(state,2000000))
   win_pause.restart(state)
   self.assertEqual(state['win_loss_difference'],0)
   self.assertFalse(win_pause.paused(state,2000000))

 def test_zero_disabled_and_timed_pause_does_not_reset_difference(self):
  state={}
  win_pause.settled(state,'win',100,1,2,0)
  self.assertTrue(win_pause.paused(state,102))
  self.assertFalse(win_pause.paused(state,103))
  self.assertEqual(state['win_loss_difference'],1)
  self.assertFalse(state.get('difference_paused'))

 def test_restart_clears_simultaneous_timed_pause(self):
  state={};win_pause.settled(state,'win',100,1,20,1)
  self.assertTrue(state['difference_paused']);self.assertEqual(state['pause_until'],120)
  win_pause.restart(state)
  self.assertFalse(win_pause.paused(state,101))
  self.assertEqual(state['win_streak'],0)

 def test_parameter_validation_all_families(self):
  for invalid in (-1,1.5,True,100001,'5',None):
   with self.assertRaises(ValueError):win_pause.validate(0,20,invalid)
  cfg=copy.deepcopy(DEFAULT);cfg['simulation'].update(win_loss_difference_limit=5,max_stake=20000);validate(cfg)
  for kind in ('hourly','majority','previous'):
   rule=defaults(cfg,kind);rule['win_loss_difference_limit']=5
   self.assertEqual(validate_follow(cfg,rule)['win_loss_difference_limit'],5)
  rule=trend_strategy.settings(cfg,'size');rule['win_loss_difference_limit']=5
  self.assertEqual(trend_strategy.validate_settings(rule,cfg['odds'])['win_loss_difference_limit'],5)


class CustomDifferenceTests(unittest.TestCase):
 setUp=custom_fixture.CustomStrategyTests.setUp
 tearDown=custom_fixture.CustomStrategyTests.tearDown
 add=custom_fixture.CustomStrategyTests.add
 create=custom_fixture.CustomStrategyTests.create
 enable=custom_fixture.CustomStrategyTests.enable

 def test_custom_loss_pause_restart_and_saved_snapshot(self):
  self.settings.update(win_loss_difference_limit=1,win_limit=0)
  key=self.create();self.enable(key);tick(self.s.db,self.s.cfg,self.now)
  order=self.s.db.query('SELECT * FROM custom_orders')[0]
  self.assertEqual(json.loads(order['pause_json'])['win_loss_difference_limit'],1)
  self.add(130);tick(self.s.db,self.s.cfg,self.now)
  row=next(r for r in catalog(self.s.db,self.s.cfg)['strategies'] if r['id']==key)
  self.assertTrue(row['difference_paused']);self.assertEqual(row['win_loss_difference'],-1)
  self.assertIn('手动',row['status'])
  self.add(131);tick(self.s.db,self.s.cfg,self.now)
  self.assertEqual(len(self.s.db.query('SELECT * FROM custom_orders')),1)
  restarted=Service(self.root)
  state=json.loads(restarted.db.query('SELECT state FROM custom_strategies WHERE id=?',(key,))[0]['state'])
  self.assertTrue(state['difference_paused'])
  control(self.s,{'id':key,'enabled':True})
  state=json.loads(self.s.db.query('SELECT state FROM custom_strategies WHERE id=?',(key,))[0]['state'])
  self.assertFalse(state['difference_paused']);self.assertEqual(state['win_loss_difference'],0)
  self.add(132);tick(self.s.db,self.s.cfg,self.now)
  self.assertEqual(len(self.s.db.query('SELECT * FROM custom_orders')),2)

 def test_legacy_and_trend_positive_pause_independent_and_restart(self):
  self.s.cfg['simulation'].update(enabled=True,win_limit=0,win_loss_difference_limit=1)
  self.s.cfg['simulation']['fixed'].update(self.settings['fixed'])
  self.s.cfg['trend_simulation']['enabled_axes']=['parity','size']
  self.s.cfg['trend_simulation']['strategies']={a:{**trend_strategy.settings(self.s.cfg,a),'win_limit':0,'win_loss_difference_limit':1 if a=='size' else 0} for a in ('size','parity')}
  router.tick(self.s.db,self.s.cfg,self.now);trend_strategy.live_tick(self.s.db,self.s.cfg,self.now)
  self.add(130);router.tick(self.s.db,self.s.cfg,self.now);trend_strategy.live_tick(self.s.db,self.s.cfg,self.now)
  rows={r['id']:r for r in catalog(self.s.db,self.s.cfg)['strategies']}
  self.assertEqual(rows['legacy']['win_loss_difference'],-1);self.assertTrue(rows['legacy']['difference_paused'])
  self.assertEqual(rows['size']['win_loss_difference'],1);self.assertTrue(rows['size']['difference_paused'])
  self.assertFalse(rows['parity']['difference_paused'])
  self.add(131);router.tick(self.s.db,self.s.cfg,self.now);trend_strategy.live_tick(self.s.db,self.s.cfg,self.now)
  self.assertEqual(len(self.s.db.query('SELECT * FROM orders')),1)
  self.assertEqual(len(self.s.db.query("SELECT * FROM trend_orders WHERE strategy='size'")),1)
  for key in ('legacy','size'):control(self.s,{'id':key,'enabled':True})
  rows={r['id']:r for r in catalog(self.s.db,self.s.cfg)['strategies']}
  for key in ('legacy','size'):
   self.assertEqual(rows[key]['win_loss_difference'],0);self.assertFalse(rows[key]['difference_paused'])


class FollowDifferenceTests(unittest.TestCase):
 setUp=follow_fixture.FollowTests.setUp
 tearDown=follow_fixture.FollowTests.tearDown
 received=follow_fixture.FollowTests.received
 plan=follow_fixture.FollowTests.plan
 create=follow_fixture.FollowTests.create
 enable=follow_fixture.FollowTests.enable

 def test_all_follow_kinds_pause_independently_and_replay(self):
  keys={kind:self.create(kind,win_limit=0,win_loss_difference_limit=1)[0] for kind in ('hourly','majority','previous')}
  for key in keys.values():self.enable(key)
  tick(self.s.db,self.s.cfg,self.now)
  self.s.db.import_records([{'id':136,'time':self.now.isoformat(),'numbers':follow_fixture.BIG}],'test')
  tick(self.s.db,self.s.cfg,self.now);tick(self.s.db,self.s.cfg,self.now)
  self.assertEqual(len(self.s.db.query('SELECT * FROM custom_orders')),3)
  for kind,key in keys.items():
   row=next(r for r in catalog(self.s.db,self.s.cfg)['strategies'] if r['id']==key)
   self.assertTrue(row['difference_paused']);self.assertEqual(row['win_loss_difference'],-1 if kind=='hourly' else 1)
   control(self.s,{'id':key,'enabled':True})
   state=json.loads(self.s.db.query('SELECT state FROM custom_strategies WHERE id=?',(key,))[0]['state'])
   self.assertEqual(state['win_loss_difference'],0);self.assertFalse(state['difference_paused'])


class HistoryDifferenceTests(unittest.TestCase):
 setUp=history_fixture.HistoryBacktestTests.setUp
 tearDown=history_fixture.HistoryBacktestTests.tearDown

 def test_all_replays_stop_permanently_at_either_signed_threshold(self):
  self.s.cfg['simulation'].update(win_limit=0,win_loss_difference_limit=2)
  self.s.cfg['trend_simulation']['strategies']={a:{**trend_strategy.settings(self.s.cfg,a),'win_limit':0,'win_loss_difference_limit':2} for a in ('size','parity')}
  keys=['legacy','size','parity']
  for kind in ('hourly','majority','previous'):
   rule=defaults(self.s.cfg,kind);rule.update(win_limit=0,win_loss_difference_limit=2)
   keys.append(save(self.s,{'name':kind,'settings':rule})['id'])
  for key in keys:
   with self.subTest(key=key):
    snapshot=prepare(self.s,{'strategy_id':key})
    rows,_=source_window(self.s.db.rows(),set(),snapshot['as_of'])
    result,orders=replay(rows,snapshot['snapshot'])
    self.assertEqual(len(orders),2)
    self.assertEqual(abs(sum(1 if o['outcome']=='win' else -1 if o['outcome']=='loss' else 0 for o in orders)),2)
    self.assertGreater(result['skips'].get('胜负差值暂停',0),0)


if __name__=='__main__':unittest.main()
