import copy,json,unittest
from pc28.follow_strategy import defaults,validate
from pc28.custom_strategy import save,tick
from pc28.strategies import control,catalog,require_selected_direction
from pc28.model import DIRECTION_AXES,direction_hits,settlement
from pc28.history_backtest import prepare,source_window,replay
import test_follow_strategy as follow_fixture
import test_history_backtest as history_fixture


class SingleLiveTests(unittest.TestCase):
 setUp=follow_fixture.FollowTests.setUp
 tearDown=follow_fixture.FollowTests.tearDown
 received=follow_fixture.FollowTests.received
 plan=follow_fixture.FollowTests.plan

 def create(self,direction,**updates):
  rule=defaults(self.s.cfg,'single');rule.update(direction=direction,axis=DIRECTION_AXES[direction],base_cents=10000,lookback=1000,stake_mode='fixed',win_limit=0)
  rule.update(updates);return save(self.s,{'name':'固定'+direction,'settings':rule})['id'],rule

 def draw(self,did,nums=follow_fixture.BIG):
  self.s.db.import_records([{'id':did,'time':self.now.isoformat(),'numbers':nums}],'test');self.received(self.now)

 def test_all_directions_always_place_once_per_period(self):
  keys={direction:self.create(direction)[0] for direction in DIRECTION_AXES}
  for direction,key in keys.items():
   self.assertEqual(require_selected_direction(self.s,key),direction)
   control(self.s,{'id':key,'enabled':True})
  tick(self.s.db,self.s.cfg,self.now);tick(self.s.db,self.s.cfg,self.now)
  self.assertEqual(len(self.s.db.query('SELECT * FROM custom_orders')),8)
  self.draw(136);tick(self.s.db,self.s.cfg,self.now)
  self.draw(137,follow_fixture.ZERO);tick(self.s.db,self.s.cfg,self.now)
  for direction,key in keys.items():
   rows=self.s.db.query('SELECT * FROM custom_orders WHERE strategy=? ORDER BY target_draw',(key,))
   self.assertEqual([r['target_draw'] for r in rows],[136,137,138])
   self.assertEqual({r['direction'] for r in rows},{direction})
   self.assertEqual({r['stake'] for r in rows},{10000})
   self.assertEqual(json.loads(rows[0]['strategy_json'])['settings']['direction'],direction)
   if direction in ('大单','小单','大双','小双'):
    expected={'大单':('win',33150),'小单':('loss',-10000),'大双':('loss',-10000),'小双':('loss',-10000)}
    self.assertEqual((rows[0]['outcome'],rows[0]['pnl']),expected[direction])
    self.assertEqual(rows[1]['outcome'],'refund' if direction=='小双' else 'loss')
  listing=catalog(self.s.db,self.s.cfg)['strategies']
  for key in keys.values():self.assertEqual(next(r for r in listing if r['id']==key)['editor_type'],'single')

 def test_combo_odds_require_both_components_and_round_to_cents(self):
  odds=self.s.cfg['odds']
  for direction,total,credit in [('大单',15,436),('小单',11,480),('大双',16,480),('小双',12,436)]:
   size='大' if total>=14 else '小';parity='单' if total%2 else '双'
   with self.subTest(direction=direction):
    self.assertTrue(direction_hits(direction,size,parity))
    self.assertFalse(direction_hits(direction,size,'双' if parity=='单' else '单'))
    self.assertFalse(direction_hits(direction,'小' if size=='大' else '大',parity))
    self.assertEqual(settlement(101,direction,total,True,odds)['credit'],credit)

 def test_difference_pause_and_manual_restart(self):
  key,_=self.create('大',win_loss_difference_limit=2);control(self.s,{'id':key,'enabled':True});tick(self.s.db,self.s.cfg,self.now)
  for did in (136,137):self.draw(did);tick(self.s.db,self.s.cfg,self.now)
  state=json.loads(self.s.db.query('SELECT state FROM custom_strategies WHERE id=?',(key,))[0]['state'])
  self.assertTrue(state['difference_paused']);self.assertEqual(state['win_loss_difference'],2)
  self.assertEqual(len(self.s.db.query('SELECT * FROM custom_orders')),2)
  control(self.s,{'id':key,'enabled':True});self.draw(138);tick(self.s.db,self.s.cfg,self.now)
  self.assertEqual(self.s.db.query('SELECT target_draw FROM custom_orders ORDER BY id DESC LIMIT 1')[0]['target_draw'],139)

 def test_direction_validation_and_disabled_only_editing(self):
  for direction in ('大小','单双','',None):
   rule=defaults(self.s.cfg,'single');rule['direction']=direction
   with self.assertRaises(ValueError):validate(self.s.cfg,rule)
  rule=defaults(self.s.cfg,'single');rule.update(direction='大',axis='parity')
  with self.assertRaises(ValueError):validate(self.s.cfg,rule)
  key,rule=self.create('小');control(self.s,{'id':key,'enabled':True})
  with self.assertRaises(ValueError):save(self.s,{'id':key,'name':'edit','settings':rule})

 def test_adjustment_counts_survive_alternating_results_and_reset_next_stake(self):
  key,_=self.create('大',stake_mode='adjust',base_cents=50000,win_decrement_cents=10000,loss_increment_cents=10000,max_increase_steps=2,max_decrease_steps=2,max_losses=1)
  control(self.s,{'id':key,'enabled':True});tick(self.s.db,self.s.cfg,self.now)
  for did,nums in [(136,follow_fixture.ZERO),(137,follow_fixture.BIG),(138,follow_fixture.ZERO),(139,follow_fixture.BIG),(140,follow_fixture.BIG)]:
   self.draw(did,nums);tick(self.s.db,self.s.cfg,self.now)
  rows=self.s.db.query('SELECT * FROM custom_orders WHERE strategy=? ORDER BY target_draw',(key,))
  self.assertEqual([r['stake'] for r in rows],[50000,60000,50000,50000,40000,50000])
  state=json.loads(self.s.db.query('SELECT state FROM custom_strategies WHERE id=?',(key,))[0]['state'])
  self.assertEqual((state['increase_steps'],state['decrease_steps']),(0,0))

 def test_adjustment_refund_does_not_change_stake_or_counts(self):
  key,_=self.create('小',stake_mode='adjust',base_cents=50000,win_decrement_cents=10000,loss_increment_cents=10000)
  control(self.s,{'id':key,'enabled':True});tick(self.s.db,self.s.cfg,self.now);self.draw(136,follow_fixture.ZERO);tick(self.s.db,self.s.cfg,self.now)
  rows=self.s.db.query('SELECT * FROM custom_orders ORDER BY id');self.assertEqual(rows[0]['outcome'],'refund');self.assertEqual(rows[1]['stake'],50000)
  state=json.loads(self.s.db.query('SELECT state FROM custom_strategies WHERE id=?',(key,))[0]['state']);self.assertEqual((state['increase_steps'],state['decrease_steps']),(0,0))


class SingleReplayTests(unittest.TestCase):
 setUp=history_fixture.HistoryBacktestTests.setUp
 tearDown=history_fixture.HistoryBacktestTests.tearDown

 def run_direction(self,direction,**updates):
  rule=defaults(self.s.cfg,'single');rule.update(direction=direction,axis=DIRECTION_AXES[direction],base_cents=10000,lookback=1000,stake_mode='fixed',win_limit=0,max_losses=0);rule.update(updates)
  key=save(self.s,{'name':'single '+direction,'settings':rule})['id'];prepared=prepare(self.s,{'strategy_id':key})
  rows,_=source_window(self.s.db.rows(),set(),prepared['as_of']);return replay(rows,prepared['snapshot'])

 def test_replay_fixed_direction_every_draw_without_observation_window(self):
  for direction in DIRECTION_AXES:
   with self.subTest(direction=direction):
    result,orders=self.run_direction(direction)
    self.assertEqual(len(orders),89);self.assertEqual({o['direction'] for o in orders},{direction});self.assertEqual({o['stake'] for o in orders},{10000})

 def test_combo_replay_refunds_special_totals_without_adjusting_stake(self):
  for direction,total in [('小单',13),('大双',14)]:
   with self.subTest(direction=direction):
    rule=defaults(self.s.cfg,'single')
    rule.update(direction=direction,axis='combo',stake_mode='adjust',base_cents=10000,win_decrement_cents=1000,loss_increment_cents=1000)
    key=save(self.s,{'name':'退本'+direction,'settings':rule})['id']
    prepared=prepare(self.s,{'strategy_id':key})
    rows,_=source_window(self.s.db.rows(),set(),prepared['as_of'])
    size,parity=direction
    ordinary=11 if total==13 else 16
    rows=[rows[0],{**rows[1],'total':total,'size':size,'parity':parity},{**rows[2],'total':ordinary,'size':size,'parity':parity}]
    result,orders=replay(rows,prepared['snapshot'])
    self.assertEqual([(o['outcome'],o['stake'],o['credit'],o['pnl']) for o in orders],
                     [('refund',10000,10000,0),('win',10000,47500,37500)])
    self.assertEqual(result['refunds'],1)
    wrong='大单' if total==13 else '小双'
    self.assertEqual(settlement(10000,wrong,total,False,self.s.cfg['odds'])['pnl'],-10000)
    self.assertEqual(settlement(10000,size,total,True,self.s.cfg['odds'])['outcome'],'win')

 def test_additive_progression_reset_and_pause_match_live_rules(self):
  result,orders=self.run_direction('小',stake_mode='additive',loss_increment_cents=5000,max_losses=3)
  self.assertEqual([o['stake'] for o in orders[:4]],[10000,15000,20000,10000])
  result,orders=self.run_direction('大',win_loss_difference_limit=2)
  self.assertEqual(len(orders),2);self.assertGreater(result['skips']['胜负差值暂停'],0)

 def test_adjustment_replay_uses_each_actual_previous_stake(self):
  _,wins=self.run_direction('大',stake_mode='adjust',win_decrement_cents=2000,max_decrease_steps=2)
  self.assertEqual([o['stake'] for o in wins[:4]],[10000,8000,10000,8000])
  _,losses=self.run_direction('小',stake_mode='adjust',loss_increment_cents=3000,max_increase_steps=2)
  self.assertEqual([o['stake'] for o in losses[:4]],[10000,13000,10000,13000])


if __name__=='__main__':unittest.main()
