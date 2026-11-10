import copy,json,tempfile,unittest
from pathlib import Path
from datetime import datetime,timezone,timedelta
from pc28.config import DEFAULT
from pc28.service import Service
from pc28.custom_strategy import save,tick,catalog
from pc28.strategies import control,order_page
class CustomStrategyTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent/'data');self.root=Path(self.tmp.name);cfg=copy.deepcopy(DEFAULT);cfg['collector']['enabled']=False;cfg['collector']['maintenance_guard_enabled']=False;cfg['simulation'].update(max_stake=2000,daily_loss_limit=100000,max_drawdown=100000);(self.root/'config.json').write_text(json.dumps(cfg),encoding='utf8');self.s=Service(self.root);self.now=datetime.now(timezone.utc).replace(microsecond=0)
  self.settings={k:copy.deepcopy(cfg['simulation'][k]) for k in ('mode','initial_balance','daily_loss_limit','max_drawdown','max_stake','round_restart','fixed')};self.settings['fixed'].update(direction='小',threshold=6,base_cents=10000,max_bets=2)
  for i in range(30):self.add(100+i,-210*(29-i))
 def tearDown(self):self.tmp.cleanup()
 def add(self,id,seconds=0):self.s.db.import_records([{'id':id,'time':(self.now+timedelta(seconds=seconds)).isoformat(),'numbers':list(range(1,21))}],'test')
 def create(self,name='test',settings=None):return save(self.s,{'name':name,'settings':settings or self.settings})['id']
 def enable(self,key):control(self.s,{'id':key,'enabled':True})
 def test_disabled_new_and_two_independent_orders(self):
  a=self.create('a');b=self.create('b');tick(self.s.db,self.s.cfg,self.now);self.assertEqual(self.s.db.query('SELECT * FROM custom_orders'),[])
  self.enable(a);self.enable(b);tick(self.s.db,self.s.cfg,self.now);tick(self.s.db,self.s.cfg,self.now)
  self.assertEqual(len(self.s.db.query('SELECT * FROM custom_orders')),2)
  self.assertEqual({r['balance'] for r in self.s.db.query('SELECT * FROM custom_strategies')},{9990000})
  self.assertEqual(order_page(self.s.db,self.s.cfg,a)['total'],1)
 def test_two_order_round_and_condition_reset(self):
  self.settings['fixed']['max_losses']=2
  key=self.create();self.enable(key);tick(self.s.db,self.s.cfg,self.now);self.add(130);tick(self.s.db,self.s.cfg,self.now);self.add(131);tick(self.s.db,self.s.cfg,self.now)
  orders=self.s.db.query('SELECT * FROM custom_orders ORDER BY id');self.assertEqual([o['stake'] for o in orders],[10000,20000]);self.assertTrue(all(o['status']=='settled' for o in orders))
  tick(self.s.db,self.s.cfg,self.now);self.assertEqual(len(self.s.db.query('SELECT * FROM custom_orders')),2)
  self.assertTrue(json.loads(self.s.db.query('SELECT state FROM custom_strategies')[0]['state'])['waiting_rearm'])
 def test_custom_count_limit_restarts_and_migrates_old_wait(self):
  key=self.create();self.enable(key);tick(self.s.db,self.s.cfg,self.now);self.add(130);tick(self.s.db,self.s.cfg,self.now);self.add(131)
  control(self.s,{'id':key,'enabled':False});tick(self.s.db,self.s.cfg,self.now)
  with self.s.db.tx() as c:
   state=json.loads(c.execute('SELECT state FROM custom_strategies WHERE id=?',(key,)).fetchone()[0]);state['waiting_rearm']=True;c.execute('UPDATE custom_strategies SET state=? WHERE id=?',(json.dumps(state),key))
  self.enable(key);tick(self.s.db,self.s.cfg,self.now);tick(self.s.db,self.s.cfg,self.now)
  orders=self.s.db.query('SELECT * FROM custom_orders ORDER BY target_draw');self.assertEqual([o['stake'] for o in orders],[10000,20000,10000]);self.assertEqual([(o['cycle'],o['step']) for o in orders],[(1,1),(1,2),(2,1)])
 def test_custom_count_stop_rechecks_false_then_true(self):
  self.settings['fixed'].update(lookback=4,threshold=4,run_length=4);key=self.create();self.enable(key);tick(self.s.db,self.s.cfg,self.now);self.add(130);tick(self.s.db,self.s.cfg,self.now)
  self.s.db.import_records([{'id':131,'time':self.now.isoformat(),'numbers':list(range(4,24))}],'test');tick(self.s.db,self.s.cfg,self.now)
  self.assertEqual(len(self.s.db.query('SELECT * FROM custom_orders')),2)
  for i in range(132,136):self.add(i);tick(self.s.db,self.s.cfg,self.now)
  orders=self.s.db.query('SELECT * FROM custom_orders ORDER BY target_draw');self.assertEqual(orders[-1]['target_draw'],136);self.assertEqual(orders[-1]['stake'],10000);self.assertEqual(orders[-1]['step'],1)
 def test_custom_reversal_win_pause_exact_skip(self):
  self.settings.update(win_limit=2,pause_draws=3);self.settings['fixed']['max_bets']=8;key=self.create();self.enable(key);tick(self.s.db,self.s.cfg,self.now)
  for did in (130,131):
   self.s.db.import_records([{'id':did,'time':self.now.isoformat(),'numbers':list(range(4,24))}],'test');tick(self.s.db,self.s.cfg,self.now)
  state=json.loads(self.s.db.query('SELECT state FROM custom_strategies')[0]['state']);self.assertEqual(state['pause_until'],134);self.assertFalse(state['active'])
  for did in (132,133,134):self.add(did);tick(self.s.db,self.s.cfg,self.now)
  self.assertEqual([o['target_draw'] for o in self.s.db.query('SELECT * FROM custom_orders ORDER BY target_draw')],[130,131,135])
 def test_disabled_orders_settle_once_with_saved_odds(self):
  key=self.create();self.enable(key);tick(self.s.db,self.s.cfg,self.now);control(self.s,{'id':key,'enabled':False});self.add(130);tick(self.s.db,self.s.cfg,self.now);tick(self.s.db,self.s.cfg,self.now)
  self.assertEqual(len(self.s.db.query("SELECT * FROM custom_ledger WHERE kind='settle'")),1);self.assertEqual(self.s.db.query('SELECT balance FROM custom_strategies')[0]['balance'],9990000)
 def test_edit_rebases_balance_and_pending_prevents_edit(self):
  key=self.create();self.enable(key)
  with self.assertRaises(ValueError):save(self.s,{'id':key,'name':'edit','settings':self.settings})
  tick(self.s.db,self.s.cfg,self.now);control(self.s,{'id':key,'enabled':False})
  with self.assertRaises(ValueError):save(self.s,{'id':key,'name':'edit','settings':self.settings})
  self.add(130);tick(self.s.db,self.s.cfg,self.now);settings=copy.deepcopy(self.settings);settings['initial_balance']=200000;save(self.s,{'id':key,'name':'edit','settings':settings});a=self.s.db.query('SELECT * FROM custom_strategies')[0]
  self.assertEqual(a['name'],'edit');self.assertEqual((a['initial_balance'],a['balance']),(20000000,19990000))
 def test_missing_draw_and_conflict_do_not_settle(self):
  key=self.create();self.enable(key);tick(self.s.db,self.s.cfg,self.now);self.add(131);tick(self.s.db,self.s.cfg,self.now);self.assertEqual(self.s.db.query('SELECT status FROM custom_orders')[0]['status'],'pending');self.add(130)
  with self.s.db.tx() as c:c.execute('INSERT INTO conflicts(draw_id,resolved) VALUES(130,0)')
  tick(self.s.db,self.s.cfg,self.now);self.assertEqual(self.s.db.query('SELECT status FROM custom_orders')[0]['status'],'pending')
 def test_validation_and_catalog(self):
  bad=copy.deepcopy(self.settings);bad['fixed']['base_cents']=-1
  for body in ({'name':'','settings':self.settings},{'name':'x','settings':bad},{'name':'x','settings':{'enabled':True}}):
   with self.assertRaises(ValueError):save(self.s,body)
  key=self.create('alpha');row=catalog(self.s.db)[0];self.assertEqual(row['id'],key);self.assertFalse(row['enabled']);self.assertEqual(row['stats']['bets'],0)
 def test_max_three_losses_stop_and_freshness_limits(self):
  st=copy.deepcopy(self.settings);st['fixed']['max_bets']=8;key=self.create(settings=st);self.enable(key)
  for i in range(3):tick(self.s.db,self.s.cfg,self.now);self.add(130+i)
  tick(self.s.db,self.s.cfg,self.now);self.assertEqual([o['stake'] for o in self.s.db.query('SELECT * FROM custom_orders ORDER BY id')],[10000,20000,40000])
 def test_win_uses_saved_odds_and_refund_counts_separately(self):
  st=copy.deepcopy(self.settings);st['fixed']['direction']='大'
  with self.s.db.tx() as c:c.execute("UPDATE draws SET size='小'")
  key=self.create(settings=st);self.enable(key);tick(self.s.db,self.s.cfg,self.now)
  self.s.cfg['odds']['normal']=100;self.add(130);tick(self.s.db,self.s.cfg,self.now)
  first=self.s.db.query('SELECT * FROM custom_orders ORDER BY id LIMIT 1')[0];self.assertEqual(first['pnl'],10000);self.assertEqual(first['outcome'],'win')
 def test_edit_trend_axis_parameters_live_and_backtest(self):
  from pc28 import trend_strategy
  rule=trend_strategy.settings(self.s.cfg,'size');rule.update(lookback=4,base_cents=25000,loss_increment_cents=5000,max_losses=2,initial_balance=12345,win_limit=0)
  save(self.s,{'id':'size','settings':rule});self.assertEqual(trend_strategy.settings(self.s.cfg,'parity')['base_cents'],50000)
  self.assertEqual(self.s.db.query("SELECT initial_balance FROM trend_accounts WHERE strategy='size'")[0]['initial_balance'],1234500)
  self.enable('size')
  zero=[1,2,11,14,17,24,26,30,32,33,36,47,48,50,59,61,65,67,68,80]
  for i in range(3):
   trend_strategy.live_tick(self.s.db,self.s.cfg,self.now)
   self.s.db.import_records([{'id':130+i,'time':self.now.isoformat(),'numbers':zero}],'test')
  self.s.cfg['trend_simulation']['enabled_axes']=[];trend_strategy.live_tick(self.s.db,self.s.cfg,self.now)
  orders=self.s.db.query("SELECT * FROM trend_orders WHERE strategy='size' ORDER BY target_draw")
  self.assertEqual([o['stake'] for o in orders],[25000,30000,25000]);self.assertTrue(all(json.loads(o['rules_json'])['lookback']==4 for o in orders))
  result=trend_strategy.backtest(self.s.db.rows(),self.s.cfg,with_risk=False)
  self.assertEqual(result['strategies']['size']['initial_balance'],12345)
  self.assertEqual([o['stake'] for o in result['orders'] if o['strategy']=='size' and o['target_draw']>=130],[25000,30000,25000])
 def test_trend_edit_guards_and_snapshot(self):
  from pc28 import trend_strategy
  rule=trend_strategy.settings(self.s.cfg,'parity');rule['max_losses']=2
  save(self.s,{'id':'parity','settings':rule});self.enable('parity')
  with self.assertRaises(ValueError):save(self.s,{'id':'parity','settings':rule})
  trend_strategy.live_tick(self.s.db,self.s.cfg,self.now);control(self.s,{'id':'parity','enabled':False})
  with self.assertRaises(ValueError):save(self.s,{'id':'parity','settings':rule})
  self.s.cfg['trend_simulation']['strategies']['parity']['max_losses']=5
  with self.s.db.tx() as c:c.execute("UPDATE trend_accounts SET losses=1 WHERE strategy='parity'")
  self.s.db.import_records([{'id':130,'time':self.now.isoformat(),'numbers':[1,2,11,14,17,24,26,30,32,33,36,47,48,50,59,61,65,67,68,80]}],'test')
  trend_strategy.live_tick(self.s.db,self.s.cfg,self.now)
  self.assertEqual(self.s.db.query("SELECT losses FROM trend_accounts WHERE strategy='parity'")[0]['losses'],0)
  bad=copy.deepcopy(rule);bad['loss_increment_cents']=-1
  with self.assertRaises(ValueError):save(self.s,{'id':'parity','settings':bad})
 def test_stale_data_cannot_create_order(self):
  key=self.create();self.enable(key);tick(self.s.db,self.s.cfg,self.now+timedelta(seconds=300));self.assertEqual(self.s.db.query('SELECT * FROM custom_orders'),[])
if __name__=='__main__':unittest.main()
