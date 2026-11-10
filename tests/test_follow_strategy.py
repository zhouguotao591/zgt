import copy,json,tempfile,unittest
from pathlib import Path
from datetime import datetime,timedelta,timezone
from pc28.config import DEFAULT
from pc28.service import Service
from pc28.custom_strategy import tick,save
from pc28.strategies import control,order_page,catalog
from pc28.follow_strategy import defaults,hourly_signal,next_stake,BJ
BIG=list(range(1,21));ZERO=[1,2,11,14,17,24,26,30,32,33,36,47,48,50,59,61,65,67,68,80]
class FollowTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent/'data');self.root=Path(self.tmp.name);cfg=copy.deepcopy(DEFAULT);cfg['collector']['enabled']=False;cfg['collector']['maintenance_guard_enabled']=False;cfg['simulation'].update(max_stake=20000,daily_loss_limit=1000000,max_drawdown=1000000);(self.root/'config.json').write_text(json.dumps(cfg));self.s=Service(self.root);self.hour=datetime.now(timezone.utc).replace(minute=0,second=0,microsecond=0);self.now=self.hour+timedelta(seconds=20)
  times=[self.hour-timedelta(seconds=60+210*i) for i in range(36)][::-1]
  self.s.db.import_records([{'id':100+i,'time':t.isoformat(),'numbers':BIG} for i,t in enumerate(times)],'test');self.received(self.now)
  self.plan(136,self.hour+timedelta(seconds=150))
 def tearDown(self):self.tmp.cleanup()
 def received(self,now):
  with self.s.db.tx() as c:c.execute('UPDATE draws SET first_seen=?',((now-timedelta(seconds=1)).isoformat(),))
 def plan(self,id,t):
  with self.s.db.tx() as c:c.execute("INSERT OR REPLACE INTO runtime_state VALUES('draw_timer',?,?)",(json.dumps({'next_draw_id':id,'next_draw_at':t.isoformat()}),self.now.isoformat()))
 def create(self,kind='hourly',axis='size',**updates):
  value=defaults(self.s.cfg,kind,axis);value.update(updates);key=save(self.s,{'name':kind+axis,'settings':value})['id'];return key,value
 def enable(self,key):control(self.s,{'id':key,'enabled':True})
 def test_both_hourly_axes_and_disabled_then_idempotent(self):
  a,_=self.create();b,_=self.create(axis='parity');tick(self.s.db,self.s.cfg,self.now);self.assertEqual(self.s.db.query('SELECT * FROM custom_orders'),[])
  self.enable(a);self.enable(b);tick(self.s.db,self.s.cfg,self.now);tick(self.s.db,self.s.cfg,self.now)
  orders=self.s.db.query('SELECT * FROM custom_orders');self.assertEqual(len(orders),2);self.assertEqual({o['direction'] for o in orders},{'小','双'})
  for o in orders:
   snapshot=json.loads(o['strategy_json']);self.assertEqual(len(snapshot['evidence']),2);self.assertEqual(datetime.fromisoformat(snapshot['follow_end'])-datetime.fromisoformat(snapshot['follow_start']),timedelta(hours=2))
 def test_hours_must_each_qualify_and_have_same_minority(self):
  _,rule=self.create();history=self.s.db.rows();choice,evidence,_=hourly_signal(history,self.hour,rule,self.now);self.assertEqual(choice,'小')
  mixed=copy.deepcopy(history)
  for r in mixed:
   if datetime.fromisoformat(r['draw_utc'])>=self.hour-timedelta(hours=1):r['size']='小'
  self.assertIsNone(hourly_signal(mixed,self.hour,rule,self.now)[0])
  balanced=copy.deepcopy(history)
  for i,r in enumerate(balanced):r['size']='大' if i%2 else '小'
  self.assertIsNone(hourly_signal(balanced,self.hour,rule,self.now)[0])
 def test_exact_five_threshold_and_beijing_midnight(self):
  _,rule=self.create();h=self.s.db.rows();hour=self.hour
  for i in range(2):
   bucket=[r for r in h if hour-timedelta(hours=2-i)<=datetime.fromisoformat(r['draw_utc'])<hour-timedelta(hours=1-i)]
   for j,r in enumerate(bucket):r['size']='大' if j<(len(bucket)+6)//2 else '小'
  choice,evidence,_=hourly_signal(h,hour,rule,self.now);self.assertEqual(choice,'小');self.assertTrue(all(e['difference']>=5 for e in evidence))
  self.assertIsNone(hourly_signal(h,hour,{**rule,'hour_difference':7},self.now)[0])
  midnight=datetime(2026,11,3,tzinfo=BJ);shift=midnight-hour
  for r in h:
   r['draw_utc']=(datetime.fromisoformat(r['draw_utc'])+shift).isoformat();r['first_seen']=(midnight+timedelta(seconds=1)).isoformat()
  self.assertEqual(hourly_signal(h,midnight,rule,midnight+timedelta(seconds=20))[0],'小')
 def test_no_partial_or_gapped_or_future_hours(self):
  _,rule=self.create();h=self.s.db.rows();self.assertIsNone(hourly_signal(h[2:],self.hour,rule,self.now)[0]);gap=h[:10]+h[11:];self.assertIsNone(hourly_signal(gap,self.hour,rule,self.now)[0])
  future=copy.deepcopy(h)
  for r in future:r['first_seen']=(self.now+timedelta(seconds=1)).isoformat()
  self.assertIsNone(hourly_signal(future,self.hour,rule,self.now)[0])
  extra={**h[-1],'id':999,'size':'小','draw_utc':(self.hour+timedelta(minutes=1)).isoformat()};self.assertEqual(hourly_signal([*h,extra],self.hour,rule,self.now)[0],'小')
 def test_hourly_fixed_direction_through_two_hours_and_reevaluates(self):
  key,rule=self.create();self.enable(key);tick(self.s.db,self.s.cfg,self.now)
  t=self.hour+timedelta(seconds=150)
  for did in range(136,172):
   self.s.db.import_records([{'id':did,'time':t.isoformat(),'numbers':ZERO}],'test');now=t+timedelta(seconds=1);self.received(now);self.plan(did+1,t+timedelta(seconds=210));tick(self.s.db,self.s.cfg,now);t+=timedelta(seconds=210)
  orders=self.s.db.query('SELECT * FROM custom_orders ORDER BY target_draw');self.assertTrue(all(o['direction']==('小' if o['cycle']==1 else '大') for o in orders));self.assertTrue(any(o['cycle']==2 for o in orders))
  for o in orders:
   snapshot=json.loads(o['strategy_json']);actual=self.s.db.query('SELECT draw_utc FROM draws WHERE id=?',(o['target_draw'],))
   if actual:self.assertLess(datetime.fromisoformat(actual[0]['draw_utc']),datetime.fromisoformat(snapshot['follow_end']))
 def test_majority_additive_progress_and_standalone_axis(self):
  key,_=self.create('majority',lookback=4,base_cents=25000,loss_increment_cents=5000,max_losses=2);self.enable(key)
  now=self.now
  for i in range(3):
   tick(self.s.db,self.s.cfg,now);self.s.db.import_records([{'id':136+i,'time':(now+timedelta(seconds=1)).isoformat(),'numbers':ZERO}],'test');now+=timedelta(seconds=2);self.received(now)
  control(self.s,{'id':key,'enabled':False});tick(self.s.db,self.s.cfg,now)
  orders=self.s.db.query('SELECT * FROM custom_orders ORDER BY id');self.assertEqual([o['stake'] for o in orders],[25000,30000,25000]);self.assertTrue(all(o['status']=='settled' for o in orders))
  self.assertEqual(order_page(self.s.db,self.s.cfg,key,active_only=False)['total'],3);self.assertEqual(len(self.s.db.query("SELECT * FROM custom_ledger WHERE kind='settle'")),3)
 def test_edit_guards_persistence_and_money_modes(self):
  key,value=self.create();self.enable(key)
  with self.assertRaises(ValueError):save(self.s,{'id':key,'name':'edit','settings':value})
  tick(self.s.db,self.s.cfg,self.now);control(self.s,{'id':key,'enabled':False})
  with self.assertRaises(ValueError):save(self.s,{'id':key,'name':'edit','settings':value})
  for mode,expected in [('fixed',50000),('additive',70000),('multiply',200000)]:self.assertEqual(next_stake({**value,'stake_mode':mode},2),expected)
  bad={**value,'observe_hours':0}
  with self.assertRaises(ValueError):save(self.s,{'name':'bad','settings':bad})
 def test_missing_schedule_and_future_hour_do_not_create_order(self):
  key,_=self.create();self.enable(key)
  self.plan(999,self.now);tick(self.s.db,self.s.cfg,self.now);self.assertEqual(self.s.db.query('SELECT * FROM custom_orders'),[])
  self.plan(136,self.hour+timedelta(hours=1,minutes=1));tick(self.s.db,self.s.cfg,self.now);self.assertEqual(self.s.db.query('SELECT * FROM custom_orders'),[])
 def test_majority_profit_streak_pause_and_resume(self):
  from pc28.db import DB
  key,_=self.create('majority',lookback=4,win_limit=2,pause_draws=3);self.enable(key);now=self.now;tick(self.s.db,self.s.cfg,now)
  for did in range(136,141):
   self.s.db.import_records([{'id':did,'time':(now+timedelta(seconds=1)).isoformat(),'numbers':BIG}],'test');now+=timedelta(seconds=2);self.received(now);tick(DB(self.s.db.path),self.s.cfg,now)
   if did==137:
    state=json.loads(self.s.db.query('SELECT state FROM custom_strategies WHERE id=?',(key,))[0]['state']);self.assertEqual(state['pause_until'],140)
  orders=self.s.db.query('SELECT * FROM custom_orders ORDER BY target_draw');self.assertEqual([o['target_draw'] for o in orders],[136,137,141])
 def test_win_pause_refund_loss_reset_and_disable(self):
  from pc28.win_pause import settled,paused,validate
  state={}
  settled(state,'win',100,2,20);settled(state,'refund',101,2,20);self.assertEqual(state['win_streak'],0)
  settled(state,'win',102,2,20);settled(state,'loss',103,2,20);self.assertEqual(state['win_streak'],0)
  settled(state,'win',104,2,20);settled(state,'win',105,2,20);self.assertEqual(state['pause_until'],125);self.assertTrue(paused(state,125));self.assertFalse(paused(state,126));self.assertEqual(state['win_streak'],0)
  for i in range(10):settled(state,'win',200+i,0,20)
  self.assertEqual(state['pause_until'],0)
  for limit,draws in [(-1,20),(5,0),(1.5,20)]:
   with self.assertRaises(ValueError):validate(limit,draws)
 def test_hourly_win_pause_ends_follow_and_reassesses(self):
  key,_=self.create('hourly',win_limit=1,pause_draws=2);self.enable(key);tick(self.s.db,self.s.cfg,self.now)
  t=self.hour+timedelta(seconds=150)
  for did in (136,137,138):
   balls=list(range(4,24)) if did==136 else BIG
   self.s.db.import_records([{'id':did,'time':t.isoformat(),'numbers':balls}],'test');now=t+timedelta(seconds=1);self.received(now);self.plan(did+1,t+timedelta(seconds=210));tick(self.s.db,self.s.cfg,now)
   if did==136:
    state=json.loads(self.s.db.query('SELECT state FROM custom_strategies')[0]['state']);self.assertEqual(state['pause_until'],138);self.assertIsNone(state['follow_end']);self.assertFalse(state['active'])
   t+=timedelta(seconds=210)
  orders=self.s.db.query('SELECT * FROM custom_orders ORDER BY target_draw');self.assertEqual([o['target_draw'] for o in orders],[136,139]);self.assertEqual(orders[-1]['cycle'],2)
 def test_edited_follow_preserves_balance_and_catalog_type(self):
  key,rule=self.create('majority');changed={**rule,'lookback':50};save(self.s,{'id':key,'name':'edited','settings':changed});row=next(r for r in catalog(self.s.db,self.s.cfg)['strategies'] if r['id']==key)
  self.assertEqual(row['editor_type'],'majority');self.assertEqual(row['settings']['lookback'],50);self.assertEqual(row['stats']['balance'],100000)
if __name__=='__main__':unittest.main()
