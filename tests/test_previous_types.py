import json,sqlite3,unittest
from datetime import timedelta
import test_previous_strategy as fixture
from pc28.follow_strategy import defaults
from pc28.custom_strategy import save
from pc28.strategies import control


class PreviousTypeTests(unittest.TestCase):
 setUp=fixture.PreviousStrategyTests.setUp
 tearDown=fixture.PreviousStrategyTests.tearDown
 draw=fixture.PreviousStrategyTests.draw
 step=fixture.PreviousStrategyTests.step

 def create(self,types,**extra):
  settings=defaults(self.cfg,'previous');settings.update(base_cents=10000,loss_increment_cents=3000,win_decrement_cents=2000,daily_loss_limit=100000,max_drawdown=100000)
  settings.update(extra.pop('settings',{}))
  return save(self.s,{'name':'同一策略多类型上期跟随','settings':settings,'previous_types':types,**extra})['id']

 def play(self,key,sequence):
  control(self.s,{'id':key,'enabled':True})
  for i,(size,parity,total) in enumerate(sequence):
   t=self.draw(i,'大')
   with self.s.db.tx() as c:c.execute('UPDATE draws SET size=?,parity=?,total=? WHERE id=?',(size,parity,total,100+i))
   self.step(t+timedelta(seconds=1))
  return self.s.db.query('SELECT * FROM custom_orders WHERE strategy=? ORDER BY target_draw,id',(key,))

 def check_replay(self,key,rows):
  from pc28.history_backtest import prepare,replay,source_window
  p=prepare(self.s,{'strategy_id':key});history,_=source_window(self.s.db.rows(),set(),p['as_of']);_,orders=replay(history,p['snapshot'])
  actual=[(r['target_draw'],r['direction'],r['stake'],r['pnl']) for r in rows if r['status']=='settled']
  self.assertEqual(actual,[(r['target_draw'],r['direction'],r['stake'],r['pnl']) for r in orders])

 def test_all_types_save_one_strategy(self):
  types=['size','parity','小单','大双','大单','小双','a','b','c'];key=self.create(types)
  rows=self.s.db.query('SELECT * FROM custom_strategies')
  self.assertEqual(len(rows),1);self.assertEqual(rows[0]['id'],key)
  self.assertEqual(json.loads(rows[0]['settings'])['previous_types'],types)

 def test_same_draw_multiple_orders_independent_amounts_and_idempotency(self):
  key=self.create(['size','parity'])
  rows=self.play(key,[('大','单',15),('大','双',16),('小','单',13)])
  bytype={}
  for row in rows:bytype.setdefault(json.loads(row['strategy_json'])['previous_type'],[]).append(row)
  self.assertEqual([r['stake'] for r in bytype['size']],[10000,8000,11000])
  self.assertEqual([r['stake'] for r in bytype['parity']],[10000,13000,16000])
  self.assertEqual(len({r['strategy'] for r in rows}),1)
  self.check_replay(key,rows)
  latest=self.s.db.rows()[-1]
  from datetime import datetime
  self.step(datetime.fromisoformat(latest['draw_utc'])+timedelta(seconds=2))
  self.assertEqual(len(self.s.db.query('SELECT * FROM custom_orders WHERE strategy=?',(key,))),6)
  before=json.loads(self.s.db.query('SELECT state FROM custom_strategies WHERE id=?',(key,))[0]['state'])
  with self.s.db.tx() as c:c.execute('DELETE FROM custom_decisions WHERE strategy=? AND target_draw=?',(key,103))
  self.step(datetime.fromisoformat(latest['draw_utc'])+timedelta(seconds=3))
  self.assertEqual(before,json.loads(self.s.db.query('SELECT state FROM custom_strategies WHERE id=?',(key,))[0]['state']))

 def test_combo_matching_and_refund_are_independent(self):
  key=self.create(['小单','大双'])
  rows=self.play(key,[('小','单',13),('小','单',13),('大','双',14),('大','双',14)])
  small=[r for r in rows if json.loads(r['strategy_json'])['previous_type']=='小单']
  large=[r for r in rows if json.loads(r['strategy_json'])['previous_type']=='大双']
  self.assertEqual([r['target_draw'] for r in small],[101,102])
  self.assertEqual(small[0]['outcome'],'refund');self.assertEqual(small[1]['stake'],10000)
  self.assertEqual([r['target_draw'] for r in large],[103,104])
  self.assertEqual(large[0]['outcome'],'refund');self.assertEqual(large[1]['stake'],10000)
  self.check_replay(key,rows)

 def test_shared_balance_prevents_using_future_wins_for_other_type(self):
  key=self.create(['size','parity'],settings={'initial_balance':150,'daily_loss_limit':0,'max_drawdown':0})
  rows=self.play(key,[('大','单',15),('大','双',16)])
  settled=[r for r in rows if r['status']=='settled']
  self.assertEqual(len(settled),1);self.assertEqual(settled[0]['direction'],'大')
  self.check_replay(key,rows)

 def test_all_nine_types_execute_only_matching_combo_and_all_axes(self):
  key=self.create(['size','parity','小单','大双','大单','小双','a','b','c'])
  control(self.s,{'id':key,'enabled':True})
  for i,(size,parity,total,a,b,cball) in enumerate([('大','双',14,9,0,5),('小','单',13,1,9,0)]):
   t=self.draw(i,'大')
   with self.s.db.tx() as c:c.execute('UPDATE draws SET size=?,parity=?,total=?,a=?,b=?,c=? WHERE id=?',(size,parity,total,a,b,cball,100+i))
   self.step(t+timedelta(seconds=1))
  rows=self.s.db.query('SELECT * FROM custom_orders WHERE strategy=? ORDER BY target_draw,id',(key,))
  self.assertEqual(len(rows),12)
  self.assertEqual({r['direction'] for r in rows if r['target_draw']==101},{'大','双','大双','a大','b小','c大'})
  self.assertEqual(next(r['stake'] for r in rows if r['target_draw']==102 and r['direction']=='小单'),10000)
  self.check_replay(key,rows)

 def test_daily_budget_reserves_other_type_pending_stakes(self):
  key=self.create(['size','parity'],settings={'daily_loss_limit':150,'max_drawdown':0})
  rows=self.play(key,[('大','单',15),('大','双',16)])
  self.assertEqual(len([r for r in rows if r['status']=='settled']),1)
  self.check_replay(key,rows)

 def test_independent_win_pause(self):
  key=self.create(['size','parity'],settings={'win_limit':1})
  rows=self.play(key,[('大','单',15),('大','双',16),('小','单',13)])
  self.assertEqual(len([r for r in rows if json.loads(r['strategy_json'])['previous_type']=='size']),1)
  self.assertEqual(len([r for r in rows if json.loads(r['strategy_json'])['previous_type']=='parity']),3)
  self.check_replay(key,rows)

 def test_multi_type_replay_matches_across_midnight_and_shared_risk(self):
  import random
  from datetime import datetime,timezone
  self.start=(datetime.now(timezone.utc)-timedelta(days=2)).replace(hour=23,minute=52,second=0,microsecond=0)
  key=self.create(['size','parity','小单','大双','大单','小双','a','b','c'],settings={'initial_balance':1000,'daily_loss_limit':500,'max_drawdown':1000,'win_limit':2,'pause_draws':3,'max_increase_steps':2,'max_decrease_steps':2})
  control(self.s,{'id':key,'enabled':True});randomizer=random.Random(17)
  for i in range(40):
   balls=[randomizer.randrange(10) for _ in range(3)];total=sum(balls);t=self.draw(i,'大')
   with self.s.db.tx() as c:c.execute('UPDATE draws SET a=?,b=?,c=?,total=?,size=?,parity=? WHERE id=?',(*balls,total,'大' if total>=14 else '小','单' if total%2 else '双',100+i))
   self.step(t+timedelta(seconds=1))
  rows=self.s.db.query('SELECT * FROM custom_orders WHERE strategy=? ORDER BY target_draw,id',(key,))
  self.check_replay(key,rows)

 def test_invalid_selection_and_legacy(self):
  for selection in ([],['size','size'],['combo'],['invalid'],[{}],'size',None):
   with self.assertRaises(ValueError):self.create(selection)
  self.assertEqual(self.s.db.query('SELECT * FROM custom_strategies'),[])
  settings=defaults(self.cfg,'previous');settings.pop('previous_types')
  key=save(self.s,{'name':'旧配置','settings':settings})['id']
  self.assertIsNone(json.loads(self.s.db.query('SELECT settings FROM custom_strategies WHERE id=?',(key,))[0]['settings'])['previous_types'])

 def test_edit_retains_one_id_and_history(self):
  key=self.create(['size'])
  self.play(key,[('大','单',15)])
  control(self.s,{'id':key,'enabled':False})
  with self.assertRaises(ValueError):self.create(['size','parity'],id=key)
  t=self.draw(1,'大');self.step(t+timedelta(seconds=1))
  before=self.s.db.query('SELECT * FROM custom_orders WHERE strategy=?',(key,))
  self.assertEqual(self.create(['size','parity'],id=key),key)
  self.assertEqual(before,self.s.db.query('SELECT * FROM custom_orders WHERE strategy=?',(key,)))
  self.assertEqual(len(self.s.db.query('SELECT * FROM custom_strategies')),1)


class MigrationTests(unittest.TestCase):
 def test_upgrade_keeps_ids_indexes_and_is_idempotent(self):
  from pc28.order_direction_migration import migrate
  for table in ('custom_orders','strategy_signals'):
   c=sqlite3.connect(':memory:')
   c.execute('CREATE TABLE '+table+'(id INTEGER PRIMARY KEY,strategy TEXT,target_draw INTEGER,direction TEXT,extra TEXT,UNIQUE(strategy,target_draw))')
   c.execute('CREATE INDEX keep_index ON '+table+'(direction)')
   c.execute('INSERT INTO '+table+" VALUES(37,'same',999,'大','original')")
   c.commit();c.execute('BEGIN');migrate(c,table);c.commit()
   self.assertEqual(c.execute('SELECT * FROM '+table).fetchone(),(37,'same',999,'大','original'))
   c.execute('INSERT INTO '+table+" VALUES(38,'same',999,'单','new')")
   with self.assertRaises(sqlite3.IntegrityError):c.execute('INSERT INTO '+table+" VALUES(39,'same',999,'大','duplicate')")
   self.assertTrue(c.execute("SELECT 1 FROM sqlite_master WHERE name='keep_index'").fetchone())
   migrate(c,table);self.assertEqual(c.execute('SELECT COUNT(*) FROM '+table).fetchone()[0],2)
   c.close()

 def test_upgrade_rolls_back_with_transaction(self):
  from pc28.order_direction_migration import migrate
  c=sqlite3.connect(':memory:');c.execute('CREATE TABLE custom_orders(id INTEGER PRIMARY KEY,strategy TEXT,target_draw INTEGER,direction TEXT,UNIQUE(strategy,target_draw))')
  c.execute("INSERT INTO custom_orders VALUES(7,'same',1,'大')");c.commit()
  c.execute('BEGIN');migrate(c,'custom_orders');c.rollback()
  with self.assertRaises(sqlite3.IntegrityError):c.execute("INSERT INTO custom_orders VALUES(8,'same',1,'单')")
  self.assertEqual(c.execute('SELECT id FROM custom_orders').fetchall(),[(7,)])
