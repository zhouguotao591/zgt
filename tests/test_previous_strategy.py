import unittest,tempfile,copy,json
from pathlib import Path
from datetime import datetime,timezone,timedelta
from unittest.mock import patch
from pc28.service import Service
from pc28.config import DEFAULT
from pc28.follow_strategy import defaults,previous_amount,validate
from pc28.custom_strategy import save,tick
from pc28.strategies import control,catalog
from pc28.strategy_delete import delete
from pc28.history_backtest import prepare,source_window,replay,execute
from pc28.profit_curve import result

class PreviousStrategyTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent/'data');self.root=Path(self.tmp.name);self.cfg=copy.deepcopy(DEFAULT);self.cfg['collector']['enabled']=False;self.cfg['collector']['maintenance_guard_enabled']=False;self.cfg['simulation']['max_received_delay_seconds']=60;(self.root/'config.json').write_text(json.dumps(self.cfg));self.s=Service(self.root);self.start=datetime.now(timezone.utc).replace(microsecond=0)-timedelta(hours=2)
 def tearDown(self):self.tmp.cleanup()
 def create(self,axis='size',**options):
  settings=defaults(self.cfg,'previous',axis);settings.update({**dict(base_cents=10000,win_decrement_cents=2000,loss_increment_cents=3000,daily_loss_limit=100000,max_drawdown=100000),**options});key=save(self.s,{'name':'上期跟随测试','settings':settings})['id'];control(self.s,{'id':key,'enabled':True});return key,settings
 def draw(self,i,side):
  t=self.start+timedelta(seconds=210*i);numbers=list(range(1,21)) if side=='大' else list(range(4,24));self.s.db.import_records([{'id':100+i,'time':t.isoformat(),'numbers':numbers}],'test',received=t.isoformat());return t
 def step(self,t):
  with patch('pc28.custom_strategy.utcnow',return_value=t.isoformat()),patch('pc28.follow_strategy.utcnow',return_value=t.isoformat()):tick(self.s.db,self.cfg,t)
 def test_amount_floor_loss_and_refund(self):
  settings=defaults(self.cfg,'previous');self.assertEqual(settings['stake_floor'],'market');settings['stake_floor']='base';self.assertEqual(previous_amount(50000,'win',settings),50000);self.assertEqual(previous_amount(50000,'loss',settings),60000);self.assertEqual(previous_amount(60000,'refund',settings),60000);settings['stake_floor']='market';settings['win_decrement_cents']=100000;self.assertEqual(previous_amount(50000,'win',settings),100)
  settings['win_decrement_cents']=-1
  with self.assertRaises(ValueError):validate(self.cfg,settings)
 def test_live_matches_history_and_profit_is_actual_only(self):
  key,settings=self.create();sequence=['大','小','小','小','大','大','小','大','小','小','大','大']*2;self.step(self.draw(0,sequence[0])+timedelta(seconds=1))
  for i,side in enumerate(sequence[1:],1):self.step(self.draw(i,side)+timedelta(seconds=1))
  control(self.s,{'id':key,'enabled':False})
  orders=self.s.db.query("SELECT * FROM custom_orders WHERE strategy=? AND status='settled' ORDER BY target_draw",(key,));self.assertEqual([o['stake'] for o in orders[:4]],[10000,13000,11000,9000]);self.assertEqual([o['direction'] for o in orders[:4]],['大','小','小','小'])
  p=prepare(self.s,{'strategy_id':key});rows,_=source_window(self.s.db.rows(),set(),p['as_of']);summary,expected=replay(rows,p['snapshot']);self.assertEqual([(o['target_draw'],o['stake'],o['pnl']) for o in orders],[(o['target_draw'],o['stake'],o['pnl']) for o in expected])
  curve=result(self.s.db,key);self.assertEqual(curve['net'],summary['net']);self.assertEqual(curve['orders'],len(orders));self.assertEqual(curve['pending'],1)
  before=result(self.s.db,key);execute(self.s,p);after=result(self.s.db,key);self.assertEqual(before['points'],after['points']);self.assertEqual(before['net'],after['net'])
  with self.s.db.tx() as c:c.execute("INSERT INTO gap_order_tags VALUES('custom',?,'test-run')",(orders[0]['id'],))
  filtered=result(self.s.db,key,False);self.assertEqual(filtered['orders'],len(orders)-1);self.assertEqual(filtered['net'],curve['net']-orders[0]['pnl']/100)
 def test_controls_edit_delete_and_axis(self):
  key,settings=self.create('parity');t=self.draw(0,'大');self.step(t+timedelta(seconds=1));o=self.s.db.query('SELECT * FROM custom_orders')[0];self.assertEqual(o['direction'],'单');self.assertEqual(next(r for r in catalog(self.s.db,self.cfg)['strategies'] if r['id']==key)['editor_type'],'previous')
  with self.assertRaises(ValueError):save(self.s,{'id':key,'name':'edited','settings':settings})
  control(self.s,{'id':key,'enabled':False});self.step(self.draw(1,'小')+timedelta(seconds=1));save(self.s,{'id':key,'name':'edited','settings':settings});delete(self.s,{'id':key});self.assertEqual(result(self.s.db,key)['orders'],1)
 def test_risk_limit_prevents_forced_orders(self):
  key,_=self.create(max_stake=100,daily_loss_limit=100);self.step(self.draw(0,'大')+timedelta(seconds=1));self.step(self.draw(1,'小')+timedelta(seconds=1));self.assertEqual(len(self.s.db.query('SELECT * FROM custom_orders')),1);self.assertTrue(self.s.db.query("SELECT 1 FROM custom_decisions WHERE action='skip'"))

 def test_consecutive_adjustment_limits_reset_to_base(self):
  settings=defaults(self.cfg,'previous');settings.update(base_cents=10000,loss_increment_cents=2000,win_decrement_cents=1000,max_increase_steps=3,max_decrease_steps=2)
  state={};amount=10000;amounts=[amount]
  for _ in range(5):amount=previous_amount(amount,'loss',settings,state);amounts.append(amount)
  self.assertEqual(amounts,[10000,12000,14000,10000,12000,14000])
  state={};amount=10000;amounts=[amount]
  for _ in range(4):amount=previous_amount(amount,'win',settings,state);amounts.append(amount)
  self.assertEqual(amounts,[10000,9000,10000,9000,10000])
  amount=previous_amount(amount,'refund',settings,state);self.assertEqual(amount,10000);self.assertEqual(state['decrease_steps'],0)
  settings['max_increase_steps']=-1
  with self.assertRaises(ValueError):validate(self.cfg,settings)
 def test_mixed_results_count_cumulatively_and_reset_at_48(self):
  settings=defaults(self.cfg,'previous');settings.update(base_cents=5000,loss_increment_cents=100,win_decrement_cents=100,max_increase_steps=0,max_decrease_steps=48)
  state={};amount=5000
  for i in range(47):
   amount=previous_amount(amount,'loss',settings,state);amount=previous_amount(amount,'win',settings,state)
  self.assertEqual(state['decrease_steps'],47);self.assertEqual(state['increase_steps'],47)
  amount=previous_amount(amount,'win',settings,state);self.assertEqual(amount,5000);self.assertEqual(state['decrease_steps'],0);self.assertEqual(state['increase_steps'],0)
  settings.update(max_increase_steps=3,max_decrease_steps=0);state={};amount=5000
  for outcome in ('loss','win','refund','loss','win'):amount=previous_amount(amount,outcome,settings,state)
  self.assertEqual(state['increase_steps'],2);self.assertEqual(state['decrease_steps'],2)
  self.assertEqual(previous_amount(amount,'loss',settings,state),5000)
 def test_floor_wins_do_not_erase_cumulative_count(self):
  settings=defaults(self.cfg,'previous');settings.update(base_cents=5000,win_decrement_cents=10000,max_decrease_steps=3);state={}
  a=previous_amount(5000,'win',settings,state);self.assertEqual(a,100)
  a=previous_amount(a,'win',settings,state);self.assertEqual(state['decrease_steps'],2);self.assertEqual(a,100)
  self.assertEqual(previous_amount(a,'win',settings,state),5000)
 def test_limited_adjustments_live_and_backtest_equal(self):
  key,settings=self.create(max_increase_steps=2,max_decrease_steps=2);sequence=['大','小','大','小','大','大','大','大','小','小'];self.step(self.draw(0,sequence[0])+timedelta(seconds=1))
  for i,side in enumerate(sequence[1:],1):self.step(self.draw(i,side)+timedelta(seconds=1))
  p=prepare(self.s,{'strategy_id':key});rows,_=source_window(self.s.db.rows(),set(),p['as_of']);_,expected=replay(rows,p['snapshot']);actual=self.s.db.query("SELECT target_draw,stake,pnl FROM custom_orders WHERE strategy=? AND status='settled' ORDER BY target_draw",(key,))
  self.assertEqual([(o['target_draw'],o['stake'],o['pnl']) for o in actual],[(o['target_draw'],o['stake'],o['pnl']) for o in expected]);self.assertEqual([o['stake'] for o in actual[:4]],[10000,13000,10000,13000])
