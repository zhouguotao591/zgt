import copy,json,unittest
from datetime import timedelta
import test_previous_strategy as fixture
from pc28.follow_strategy import defaults,validate
from pc28.custom_strategy import save
from pc28.strategies import control,catalog
from pc28.combo_reverse import signal,settled,OPPOSITE


class ComboReverseTests(unittest.TestCase):
    setUp=fixture.PreviousStrategyTests.setUp
    tearDown=fixture.PreviousStrategyTests.tearDown
    draw=fixture.PreviousStrategyTests.draw
    step=fixture.PreviousStrategyTests.step

    def test_mapping_and_missing_draw(self):
        s=defaults(self.cfg,'combo_reverse');s['run_length']=2
        for combo,opposite in OPPOSITE.items():
            rows=[{'id':i,'size':combo[0],'parity':combo[1],'verification':'full_keno'} for i in (1,2)]
            state={};self.assertEqual(signal(rows,s,state)[0],opposite)
            rows[-1]['id']=4;self.assertIsNone(signal(rows,s,{})[0])

    def test_lock_loss_refund_win_and_fresh_observation(self):
        s=defaults(self.cfg,'combo_reverse');s.update(run_length=2,follow_draws=5,loss_increment_cents=1000)
        rows=[{'id':i,'size':'大','parity':'双','verification':'full_keno'} for i in (1,2)]
        state={};self.assertEqual(signal(rows,s,state)[0],'小单')
        settled(state,s,'loss',10000,1,3);self.assertEqual(state['next_stake'],11000)
        rows.append({'id':3,'size':'小','parity':'双','verification':'full_keno'})
        self.assertEqual(signal(rows,s,state)[0],'小单')
        settled(state,s,'refund',11000,2,4);self.assertEqual(state['next_stake'],11000)
        settled(state,s,'win',11000,3,5);self.assertFalse(state['combo_active'])
        self.assertIsNone(signal(rows,s,state)[0])

    def test_percentage_ceiling_and_max_draws(self):
        s=defaults(self.cfg,'combo_reverse');s.update(stake_mode='multiply',multiplier_hundredths=115,follow_draws=3)
        state={'combo_active':True}
        settled(state,s,'loss',11500,2,4);self.assertEqual(state['next_stake'],13300)
        settled(state,s,'refund',13300,3,5);self.assertFalse(state['combo_active'])

    def test_fixed_increment_and_refund_limit(self):
        s=defaults(self.cfg,'combo_reverse');s.update(loss_increment_cents=2500,follow_draws=2)
        state={'combo_active':True,'combo_direction':'大双'}
        settled(state,s,'loss',10000,1,3);self.assertEqual(state['next_stake'],12500)
        settled(state,s,'refund',12500,2,4)
        self.assertFalse(state['combo_active']);self.assertEqual(state['next_stake'],10000)

    def test_unverified_run_never_triggers(self):
        s=defaults(self.cfg,'combo_reverse');s['run_length']=1
        self.assertIsNone(signal([{'id':1,'size':'大','parity':'单','verification':'unverified'}],s,{})[0])

    def test_save_enable_tick_and_catalog(self):
        s=defaults(self.cfg,'combo_reverse');s.update(run_length=2,follow_draws=3,loss_increment_cents=1000,loss_stages=[{'start':1,'end':1,'mode':'additive','value':1000},{'start':2,'end':3,'mode':'multiply','value':15}],daily_loss_limit=100000,max_drawdown=100000)
        key=save(self.s,{'name':'组合反转策略','settings':s})['id'];control(self.s,{'id':key,'enabled':True})
        for i,(size,parity,total) in enumerate([('大','双',16),('大','双',16),('大','单',15),('小','单',13),('小','单',11)]):
            t=self.draw(i,'大')
            with self.s.db.tx() as c:c.execute('UPDATE draws SET size=?,parity=?,total=? WHERE id=?',(size,parity,total,100+i))
            self.step(t+timedelta(seconds=1))
        rows=self.s.db.query('SELECT * FROM custom_orders WHERE strategy=? ORDER BY target_draw',(key,))
        self.assertEqual([r['direction'] for r in rows],['小单']*3)
        self.assertEqual([r['stake'] for r in rows],[10000,11000,11000])
        self.assertEqual([r['outcome'] for r in rows],['loss','refund','win'])
        row=next(r for r in catalog(self.s.db,self.cfg)['strategies'] if r['id']==key)
        self.assertEqual(row['editor_type'],'combo_reverse')
        from pc28.history_backtest import prepare,replay,source_window
        prepared=prepare(self.s,{'strategy_id':key})
        history,_=source_window(self.s.db.rows(),set(),prepared['as_of'])
        _,orders=replay(history,prepared['snapshot'])
        self.assertEqual([(r['target_draw'],r['stake'],r['outcome']) for r in rows],[(r['target_draw'],r['stake'],r['outcome']) for r in orders])

    def test_validation(self):
        s=defaults(self.cfg,'combo_reverse');self.assertEqual(validate(self.cfg,s)['axis'],'combo')
        for field in ('run_length','follow_draws'):
            with self.assertRaises(ValueError):validate(self.cfg,{**s,field:0})

    def history(self,combos):
        return [{'id':i+1,'size':x[0],'parity':x[1],'verification':'full_keno'} for i,x in enumerate(combos)]

    def test_confirmation_wait_one_axis_and_cancel_both(self):
        s=defaults(self.cfg,'combo_reverse');s.update(run_length=2,confirmation_filter=True)
        state={};rows=self.history(['大双','大双'])
        self.assertIsNone(signal(rows,s,state)[0]);self.assertFalse(state.get('combo_active',False))
        rows=self.history(['大双','大双','大双'])
        self.assertIsNone(signal(rows,s,state)[0])
        self.assertEqual(signal(self.history(['大双','大双','大双','大单']),s,state)[0],'小单')
        state={};signal(self.history(['大双','大双']),s,state)
        self.assertIsNone(signal(self.history(['大双','大双','小单']),s,state)[0])
        self.assertNotIn('combo_pending',state)

    def test_trend_only_both_axes_threshold(self):
        s=defaults(self.cfg,'combo_reverse');s.update(run_length=2,trend_filter=True,trend_lookback=4,trend_threshold_percent=75)
        self.assertIsNone(signal(self.history(['大双']*4),s,{})[0])
        self.assertEqual(signal(self.history(['小单','大单','大双','大双']),s,{})[0],'小单')
        self.assertIsNone(signal(self.history(['大双']*2),s,{})[0])

    def test_combined_filters_and_active_round_bypass(self):
        s=defaults(self.cfg,'combo_reverse');s.update(run_length=2,confirmation_filter=True,trend_filter=True,trend_lookback=4,trend_threshold_percent=75)
        rows=self.history(['大双']*4);state={};signal(rows,s,state)
        self.assertIsNone(signal(self.history(['大双']*4+['大单']),s,state)[0])
        self.assertFalse(state.get('combo_active',False))
        s['trend_threshold_percent']=100;state={};signal(rows,s,state)
        self.assertEqual(signal(self.history(['大双']*4+['大单']),s,state)[0],'小单')
        self.assertEqual(signal(self.history(['大双']*8),s,state)[0],'小单')

    def test_old_settings_compatibility_and_filter_validation(self):
        s=defaults(self.cfg,'combo_reverse')
        from pc28.combo_reverse import FILTER_DEFAULTS
        old={k:v for k,v in s.items() if k not in FILTER_DEFAULTS}
        self.assertFalse(validate(self.cfg,old)['trend_filter'])
        for key,value in [('trend_filter',1),('confirmation_filter','yes'),('trend_lookback',1),('trend_threshold_percent',50)]:
            with self.assertRaises(ValueError):validate(self.cfg,{**s,key:value})

    def test_filtered_live_execution_matches_backtest(self):
        s=defaults(self.cfg,'combo_reverse');s.update(run_length=2,follow_draws=3,confirmation_filter=True,trend_filter=True,trend_lookback=3,trend_threshold_percent=100,daily_loss_limit=100000,max_drawdown=100000)
        key=save(self.s,{'name':'组合反转双过滤','settings':s})['id'];control(self.s,{'id':key,'enabled':True})
        for i,(size,parity,total) in enumerate([('大','双',16),('大','双',16),('大','单',15),('小','单',13),('小','单',11)]):
            t=self.draw(i,'大')
            with self.s.db.tx() as c:c.execute('UPDATE draws SET size=?,parity=?,total=? WHERE id=?',(size,parity,total,100+i))
            self.step(t+timedelta(seconds=1))
        rows=self.s.db.query('SELECT * FROM custom_orders WHERE strategy=? ORDER BY target_draw',(key,))
        self.assertEqual([r['target_draw'] for r in rows],[103,104])
        self.assertEqual([r['outcome'] for r in rows],['refund','win'])
        from pc28.history_backtest import prepare,replay,source_window
        p=prepare(self.s,{'strategy_id':key});history,_=source_window(self.s.db.rows(),set(),p['as_of']);_,orders=replay(history,p['snapshot'])
        self.assertEqual([(r['target_draw'],r['direction'],r['stake'],r['outcome']) for r in rows],[(r['target_draw'],r['direction'],r['stake'],r['outcome']) for r in orders])

    def test_multiple_loss_stages_and_refund_does_not_advance(self):
        s=defaults(self.cfg,'combo_reverse');s.update(follow_draws=20,loss_stages=[{'start':1,'end':2,'mode':'additive','value':1000},{'start':3,'end':4,'mode':'multiply','value':15}])
        state={'combo_active':True};stake=10000
        for outcome,step,expected,count in [('loss',1,11000,1),('refund',2,11000,1),('loss',3,12000,2),('loss',4,13800,3),('loss',5,15900,4),('loss',6,15900,5)]:
            settled(state,s,outcome,stake,step,100+step);stake=state['next_stake']
            self.assertEqual((stake,state['combo_loss_count']),(expected,count))
        settled(state,s,'win',stake,7,107);self.assertEqual(state['combo_loss_count'],0)

    def test_stage_gaps_zero_and_validation(self):
        from pc28.combo_reverse import loss_amount,validate_stages
        s=defaults(self.cfg,'combo_reverse');s['loss_stages']=[{'start':2,'end':3,'mode':'multiply','value':0}]
        self.assertEqual(loss_amount(s,10001,1),10001)
        self.assertEqual(loss_amount(s,10001,2),10001)
        validate(self.cfg,s)
        s['loss_stages']=[];self.assertEqual(loss_amount(s,10000,1),10000)
        for stages in [[{'start':3,'end':2,'mode':'additive','value':100}], [{'start':1,'end':2,'mode':'additive','value':100},{'start':2,'end':3,'mode':'multiply','value':10}], [{'start':1,'end':2,'mode':'multiply','value':401}]]:
            with self.assertRaises(ValueError):validate_stages(stages)

    def test_entry_combos_single_and_multiple(self):
        s=defaults(self.cfg,'combo_reverse');s.update(run_length=2,entry_combos=['大双'])
        for source,target in OPPOSITE.items():
            self.assertEqual(signal(self.history([source]*2),s,{})[0],target if source=='大双' else None)
        s['entry_combos']=['大单','小单']
        for source,target in OPPOSITE.items():
            self.assertEqual(signal(self.history([source]*2),s,{})[0],target if source in s['entry_combos'] else None)

    def test_entry_combos_validation_and_compatibility(self):
        s=defaults(self.cfg,'combo_reverse')
        old={k:v for k,v in s.items() if k!='entry_combos'}
        self.assertEqual(len(validate(self.cfg,old)['entry_combos']),4)
        for selection in ([],['大双','大双'],['大'],None,'大双',[{}]):
            with self.assertRaises(ValueError):validate(self.cfg,{**s,'entry_combos':selection})

    def test_entry_selection_with_confirmation_and_active_round(self):
        s=defaults(self.cfg,'combo_reverse');s.update(run_length=2,entry_combos=['大双'],confirmation_filter=True)
        state={};self.assertIsNone(signal(self.history(['大双']*2),s,state)[0])
        self.assertEqual(signal(self.history(['大双','大双','大单']),s,state)[0],'小单')
        self.assertEqual(signal(self.history(['小双']*4),s,state)[0],'小单')

    def test_round_stop_threshold_boundary_and_disabled(self):
        from pc28.combo_reverse import end_on_amount
        s=defaults(self.cfg,'combo_reverse');s['round_stop_cents']=600
        state={'combo_active':True,'combo_direction':'小双','combo_loss_count':2,'next_stake':600,'attempt':2}
        self.assertFalse(end_on_amount(state,s,599,100));self.assertTrue(state['combo_active'])
        self.assertTrue(end_on_amount(state,s,600,100));self.assertFalse(state['combo_active'])
        self.assertEqual((state['next_stake'],state['combo_loss_count'],state['combo_observe_after']),(s['base_cents'],0,100))
        state={'combo_active':True};s['round_stop_cents']=0
        self.assertFalse(end_on_amount(state,s,1000000,100));self.assertTrue(state['combo_active'])
        s['round_stop_cents']=600;self.assertTrue(end_on_amount(state,s,601,101))

    def test_round_stop_validation_and_legacy_default(self):
        s=defaults(self.cfg,'combo_reverse')
        old={k:v for k,v in s.items() if k!='round_stop_cents'}
        self.assertEqual(validate(self.cfg,old)['round_stop_cents'],0)
        for amount in (-1,True,1.5,'200'):
            with self.assertRaises(ValueError):validate(self.cfg,{**s,'round_stop_cents':amount})

    def test_round_stop_live_and_backtest_match_no_blocked_order_debit(self):
        s=defaults(self.cfg,'combo_reverse');s.update(run_length=1,follow_draws=20,base_cents=200,round_stop_cents=600,max_stake=6,loss_stages=[{'start':1,'end':1000,'mode':'multiply','value':200}],daily_loss_limit=100000,max_drawdown=100000)
        key=save(self.s,{'name':'单笔达额结束本轮测试','settings':s})['id'];control(self.s,{'id':key,'enabled':True})
        for i in range(6):
            t=self.draw(i,'大')
            with self.s.db.tx() as c:c.execute("UPDATE draws SET size='大',parity='单',total=15 WHERE id=?",(100+i,))
            self.step(t+timedelta(seconds=1))
        rows=self.s.db.query('SELECT * FROM custom_orders WHERE strategy=? ORDER BY target_draw',(key,))
        self.assertEqual([r['target_draw'] for r in rows],[101,103,105])
        self.assertEqual([r['stake'] for r in rows],[200,200,200])
        self.assertEqual(sum(r['amount'] for r in self.s.db.query("SELECT amount FROM custom_ledger WHERE strategy=? AND kind='stake'",(key,))),-600)
        state=json.loads(self.s.db.query('SELECT state FROM custom_strategies WHERE id=?',(key,))[0]['state'])
        self.assertFalse(state['combo_active']);self.assertEqual(state['next_stake'],200)
        from pc28.history_backtest import prepare,replay,source_window
        p=prepare(self.s,{'strategy_id':key});history,_=source_window(self.s.db.rows(),set(),p['as_of']);result,orders=replay(history,p['snapshot'])
        self.assertEqual([(r['target_draw'],r['stake'],r['pnl']) for r in rows],[(r['target_draw'],r['stake'],r['pnl']) for r in orders])
        # Replay ends at draw105; live also evaluates the not-yet-drawn target106.
        self.assertEqual(result['skips']['单笔达到结束本轮金额，重新观察'],2)
        decisions=self.s.db.query("SELECT reason FROM custom_decisions WHERE strategy=? AND reason LIKE '%结束阈值%'",(key,))
        self.assertEqual(len(decisions),3)

    def test_prior_opposite_four_mappings_and_disabled(self):
        s=defaults(self.cfg,'combo_reverse');s.update(run_length=2,prior_opposite_draws=1)
        for source,opposite in OPPOSITE.items():
            self.assertIsNone(signal(self.history([opposite,source,source]),s,{})[0])
            other=next(x for x in OPPOSITE if x not in (source,opposite))
            self.assertEqual(signal(self.history([other,source,source]),s,{})[0],opposite)
        s['prior_opposite_draws']=0
        self.assertEqual(signal(self.history(['小双','大单','大单']),s,{})[0],'小双')

    def test_prior_window_anchors_entire_run_and_exact_boundary(self):
        s=defaults(self.cfg,'combo_reverse');s.update(run_length=2,prior_opposite_draws=1)
        self.assertIsNone(signal(self.history(['小双']+['大单']*10),s,{})[0])
        s['prior_opposite_draws']=2
        rows=self.history(['小双','大双','小单','大单','大单'])
        self.assertEqual(signal(rows,s,{})[0],'小双')
        s['prior_opposite_draws']=3
        self.assertIsNone(signal(rows,s,{})[0])

    def test_prior_window_missing_unverified_and_confirmation(self):
        s=defaults(self.cfg,'combo_reverse');s.update(run_length=2,prior_opposite_draws=1,confirmation_filter=True)
        self.assertIsNone(signal(self.history(['大单','大单']),s,{})[0])
        rows=self.history(['大双','大单','大单']);rows[0]['verification']='unverified'
        self.assertIsNone(signal(rows,s,{})[0])
        rows=self.history(['大双','大单','大单']);rows[0]['id']=0
        self.assertIsNone(signal(rows,s,{})[0])
        state={};signal(self.history(['大双','大单','大单']),s,state)
        self.assertIn('combo_pending',state)
        self.assertEqual(signal(self.history(['大双','大单','大单','小单']),s,state)[0],'小双')

    def test_prior_window_validation_and_legacy(self):
        s=defaults(self.cfg,'combo_reverse');old={k:v for k,v in s.items() if k!='prior_opposite_draws'}
        self.assertEqual(validate(self.cfg,old)['prior_opposite_draws'],0)
        for value in (-1,1001,True,1.2,'2'):
            with self.assertRaises(ValueError):validate(self.cfg,{**s,'prior_opposite_draws':value})

    def test_prior_filter_live_and_backtest_match(self):
        s=defaults(self.cfg,'combo_reverse');s.update(run_length=2,prior_opposite_draws=1,base_cents=200,daily_loss_limit=100000,max_drawdown=100000)
        key=save(self.s,{'name':'连续组合之前相反组合过滤测试','settings':s})['id'];control(self.s,{'id':key,'enabled':True})
        sequence=[('小','双',12),('大','单',15),('大','单',15),('大','单',15),('大','双',16),('大','单',15),('大','单',15),('小','双',12)]
        for i,(size,parity,total) in enumerate(sequence):
            t=self.draw(i,'大')
            with self.s.db.tx() as c:c.execute('UPDATE draws SET size=?,parity=?,total=? WHERE id=?',(size,parity,total,100+i))
            self.step(t+timedelta(seconds=1))
        rows=self.s.db.query('SELECT * FROM custom_orders WHERE strategy=? ORDER BY target_draw',(key,))
        self.assertEqual([r['target_draw'] for r in rows],[107])
        self.assertEqual(rows[0]['direction'],'小双')
        from pc28.history_backtest import prepare,replay,source_window
        p=prepare(self.s,{'strategy_id':key});history,_=source_window(self.s.db.rows(),set(),p['as_of']);_,orders=replay(history,p['snapshot'])
        self.assertEqual([(r['target_draw'],r['stake'],r['pnl']) for r in rows],[(r['target_draw'],r['stake'],r['pnl']) for r in orders])
