import unittest,json,copy
from datetime import datetime,timezone,timedelta
from unittest.mock import patch
import test_robot_accounts as fixture
from pc28 import money_plans as mp
from pc28.strategy_signals import Signals,stats
from pc28.follow_strategy import defaults
from pc28 import custom_strategy

class MoneyPlanTests(unittest.TestCase):
 setUp=fixture.AccountTests.setUp
 tearDown=fixture.AccountTests.tearDown
 member=fixture.AccountTests.member
 body=fixture.AccountTests.body
 create=fixture.AccountTests.create
 order=fixture.AccountTests.order
 def plan(self,**settings):
  return mp.save(self.s,{'name':'独立金额方案','enabled':True,'settings':{**mp.DEFAULTS,**settings}},None,'')['id']
 def subscribe(self,aid,pid):
  a=self.r.account_detail(aid);bot=a['bots'][0]
  self.r.account_action('subscriptions',{'id':aid,'version':a['version'],'bot_id':bot['id'],'subscriptions':[{'strategy_id':'size','amount_mode':'plan','money_plan_id':pid}]},None,'')
  return self.s.db.query('SELECT * FROM robot_subscriptions WHERE bot_id=? AND strategy_id=?',(bot['id'],'size'))[0]
 def signal(self,target=999):
  with self.s.db.tx() as c:return c.execute("INSERT INTO strategy_signals(strategy,version,target_draw,cutoff_draw,created,direction,cycle,step,odds_json,evidence,source_engine,source_id) VALUES('size','v1',?,?,?,'大',1,1,?,'{}','trend',?)",(target,target-1,datetime.now(timezone.utc).isoformat(),json.dumps(self.s.cfg['odds']),target)).lastrowid
 def test_combo_direction_reaches_receiver_payload_and_member_settlement(self):
  from pc28.member_orders import import_signal,settle
  from pc28.strategies import control
  s=self.s;s.cfg['collector']['maintenance_guard_enabled']=False
  settings=defaults(s.cfg,'single');settings.update(direction='大单',axis='combo')
  key=custom_strategy.save(s,{'name':'组合单边','settings':settings})['id']
  control(s,{'id':key,'enabled':True})
  aid=self.create();pid=self.plan(base_cents=3000);a=self.r.account_detail(aid)
  self.r.account_action('subscriptions',{'id':aid,'version':a['version'],'bot_id':a['bots'][0]['id'],'subscriptions':[{'strategy_id':key,'amount_mode':'plan','money_plan_id':pid}]},None,'')
  now=datetime.now(timezone.utc).replace(microsecond=0)
  s.db.import_records([{'id':998,'time':(now-timedelta(seconds=10)).isoformat(),'numbers':list(range(1,21))}],'test',received=now.isoformat())
  s.signals.tick(now)
  self.assertEqual(self.r.capture(),1)
  out=s.db.query('SELECT * FROM robot_outbox')[0]
  self.assertEqual((out['message'],json.loads(out['request'])['payload']['text']),('大单30','大单30'))
  placed=out['created'];draw_at=(now+timedelta(seconds=200)).isoformat()
  s.db.import_records([{'id':999,'time':draw_at,'numbers':list(range(1,21))}],'test',received=draw_at)
  s.signals.settle(now+timedelta(seconds=201))
  with s.db.tx() as c:
   import_signal(c,s,out,None,placed,placed)
   settle(c,until=draw_at)
  member=s.db.query('SELECT direction,status,pnl FROM member_orders')[0]
  self.assertEqual(member,{'direction':'大单','status':'settled','pnl':9945})
  self.assertEqual(s.db.query('SELECT status,pnl FROM strategy_signals WHERE strategy=?',(key,))[0],{'status':'settled','pnl':33150})
 def test_independent_plan_capture_and_legacy_no_duplicate(self):
  aid=self.create();pid=self.plan(base_cents=3000);self.subscribe(aid,pid)
  self.order();self.signal();self.assertEqual(self.r.capture(),1)
  rows=self.s.db.query('SELECT * FROM robot_outbox');self.assertEqual(rows[0]['engine'],'signal');self.assertEqual(rows[0]['push_cents'],3000);self.assertEqual(rows[0]['subscription_id'],self.s.db.query("SELECT id FROM robot_subscriptions WHERE amount_mode='plan'")[0]['id'])
  self.assertEqual(json.loads(rows[0]['money_plan_snapshot'])['settings']['base_cents'],3000);self.assertEqual(self.r.capture(),0)
 def test_progress_settled_only_version_reset_and_independent(self):
  aid=self.create();pid=self.plan(mode='adjust',base_cents=3000,win_decrement_cents=100,loss_increment_cents=100,max_increase_steps=2,max_decrease_steps=2);sub=self.subscribe(aid,pid)
  self.signal();self.r.capture();out=self.s.db.query('SELECT * FROM robot_outbox')[0]
  with self.s.db.tx() as c:
   with self.assertRaisesRegex(ValueError,'尚未确认'):mp.quote(c,sub)
   c.execute("UPDATE robot_outbox SET status='failed' WHERE id=?",(out['id'],));self.assertEqual(mp.quote(c,sub)[0],3000)
   c.execute("INSERT INTO member_orders(user_id,outbox_id,target_draw,direction,stake,placed_at,accepted_at,odds_json,status,pnl,outcome) VALUES(?,?,999,'大',3000,'t','t','{}','settled',-3000,'loss')",(sub['account_id'],out['id']))
   self.assertEqual(mp.quote(c,sub)[0],3100);self.assertEqual(mp.quote(c,sub)[0],3100)
  plan=mp.listing(self.s)['rows'][0];mp.save(self.s,{'id':pid,'version':plan['version'],'name':'修订','enabled':True,'settings':{**plan['settings'],'base_cents':5000}},None,'')
  with self.s.db.tx() as c:self.assertEqual(mp.quote(c,sub)[0],5000)
  frozen=json.loads(self.s.db.query('SELECT money_plan_snapshot FROM robot_outbox')[0]['money_plan_snapshot']);self.assertEqual(frozen['version'],1)
 def test_cumulative_counters_floor_refund_and_caps(self):
  s=mp.validate({**mp.DEFAULTS,'mode':'adjust','base_cents':3000,'loss_increment_cents':100,'win_decrement_cents':100,'max_decrease_steps':2,'max_increase_steps':2});st={'next_cents':3000}
  for outcome,expected in [('win',2900),('loss',3000),('refund',3000),('win',3000)]:
   st=mp.advance(st,s,{'stake':st['next_cents'],'outcome':outcome});self.assertEqual(st['next_cents'],expected)
  self.assertEqual(st['increases'],0);self.assertEqual(st['decreases'],0)
  self.assertEqual(mp.advance({'next_cents':100},s,{'stake':100,'outcome':'win'})['next_cents'],100)
  with self.assertRaises(ValueError):mp.validate({'base_cents':1})
  with self.assertRaises(ValueError):mp.validate({'mode':'bad'})
 def test_net_amount_reset_includes_boundary_order_and_offsets_results(self):
  s=mp.validate({**mp.DEFAULTS,'mode':'adjust','reset_basis':'amount','base_cents':1000,
                 'loss_increment_cents':100,'win_decrement_cents':100,'max_increase_steps':9,'max_decrease_steps':9})
  for outcome,boundary in [('loss',1900),('win',100)]:
   state={'next_cents':1000,'increases':8,'decreases':5}
   for _ in range(9):state=mp.advance(state,s,{'stake':state['next_cents'],'outcome':outcome})
   self.assertEqual(state['next_cents'],boundary)
   refund=mp.advance(state,s,{'stake':boundary,'outcome':'refund'})
   self.assertEqual(refund,state)
   state=mp.advance(state,s,{'stake':boundary,'outcome':outcome})
   self.assertEqual(state,{'next_cents':1000,'increases':0,'decreases':0})
  state={'next_cents':1000}
  for _ in range(20):
   state=mp.advance(state,s,{'stake':state['next_cents'],'outcome':'loss'})
   state=mp.advance(state,s,{'stake':state['next_cents'],'outcome':'win'})
  self.assertEqual(state['next_cents'],1000)
  self.assertEqual((state['increases'],state['decreases']),(0,0))
 def test_delete_in_use_and_disabled_plan_block(self):
  aid=self.create();pid=self.plan();sub=self.subscribe(aid,pid);row=mp.listing(self.s)['rows'][0]
  with self.assertRaisesRegex(ValueError,'有效订阅'):mp.save(self.s,{'id':pid,'version':row['version'],'action':'archive'},None,'')
  mp.save(self.s,{'id':pid,'version':row['version'],'name':row['name'],'settings':row['settings'],'enabled':False},None,'')
  with self.s.db.tx() as c:
   with self.assertRaisesRegex(ValueError,'启用'):mp.quote(c,sub)
 def test_shadow_signal_ignores_simulation_risk_and_restart_dedup(self):
  s=self.s;s.cfg['collector']['maintenance_guard_enabled']=False;s.cfg['trend_simulation']['enabled_axes']=[]
  settings=defaults(s.cfg,'previous');settings.update(base_cents=3000,daily_loss_limit=1,max_drawdown=1,win_limit=1)
  key=custom_strategy.save(s,{'name':'持续信号','settings':settings})['id']
  with s.db.tx() as c:c.execute('UPDATE custom_strategies SET enabled=1,balance=0,peak=1000000 WHERE id=?',(key,))
  now=datetime.now(timezone.utc)-timedelta(seconds=5)
  s.db.import_records([{'id':1000+i,'time':(now-timedelta(seconds=(60-i)*210)).isoformat(),'numbers':list(range(1,21))} for i in range(61)],'test',received=now.isoformat())
  engine=Signals(s);engine.tick(now+timedelta(seconds=1));self.assertEqual(s.db.query('SELECT COUNT(*) n FROM strategy_signals WHERE strategy=?',(key,))[0]['n'],1)
  self.assertEqual(s.db.query('SELECT COUNT(*) n FROM custom_orders WHERE strategy=?',(key,))[0]['n'],0)
  engine=Signals(s);engine.tick(now+timedelta(seconds=2));self.assertEqual(s.db.query('SELECT COUNT(*) n FROM strategy_signals WHERE strategy=?',(key,))[0]['n'],1)
  nxt=now+timedelta(seconds=210);s.db.import_records([{'id':1061,'time':nxt.isoformat(),'numbers':list(range(1,21))}],'test',received=nxt.isoformat())
  engine.tick(nxt+timedelta(seconds=1));self.assertEqual(stats(s.db)[key]['bets'],1);self.assertEqual(stats(s.db)[key]['pending'],1)
  self.assertEqual(s.db.query('SELECT COUNT(*) n FROM strategy_signals WHERE strategy=?',(key,))[0]['n'],2)
 def test_missing_draw_keeps_subscription_running_and_settles_once(self):
  from pc28.member_orders import entry,settle
  from pc28.collector import missing_order_draws
  s=self.s;s.cfg['collector']['maintenance_guard_enabled']=False
  aid=self.create();pid=self.plan(base_cents=3000);sub=self.subscribe(aid,pid)
  strategy=custom_strategy.save(s,{'name':'缺期续单','settings':defaults(s.cfg,'previous')})['id']
  now=datetime.now(timezone.utc)-timedelta(seconds=425)
  def draw(target,at,numbers):
   s.db.import_records([{'id':target,'time':at.isoformat(),'numbers':numbers}],'test',received=at.isoformat())
  with s.db.tx() as c:c.execute('UPDATE custom_strategies SET enabled=1 WHERE id=?',(strategy,))
  draw(998,now,list(range(1,21)))
  with patch('pc28.follow_strategy.utcnow',return_value=(now+timedelta(seconds=1)).isoformat()):
   s.signals.tick(now+timedelta(seconds=1))
  self.signal(999);self.assertEqual(self.r.capture(),1)
  out=s.db.query('SELECT * FROM robot_outbox')[0];uid=self.r.account_detail(aid)['user_id']
  placed=(now+timedelta(seconds=1)).isoformat()
  with s.db.tx() as c:
   oid=c.execute("INSERT INTO member_orders(user_id,outbox_id,target_draw,direction,stake,placed_at,accepted_at,odds_json,bot_id) VALUES(?,?,999,'大',3000,?,?,?,?)",(uid,out['id'],placed,placed,json.dumps(s.cfg['odds']),sub['bot_id'])).lastrowid
   entry(c,uid,'stake',-3000,order_id=oid)
   with self.assertRaisesRegex(ValueError,'尚未确认'):mp.quote(c,sub)
  latest=now+timedelta(seconds=420)
  draw(1000,latest,list(range(4,24)))
  s.signals.tick(latest+timedelta(seconds=1))
  with s.db.tx() as c:
   settle(c)
   self.assertEqual(c.execute('SELECT status FROM member_orders WHERE id=?',(oid,)).fetchone()[0],'awaiting_result')
  self.assertEqual(missing_order_draws(s.db),{999})
  self.signal(1001);self.assertEqual(self.r.capture(),1)
  self.assertEqual(s.db.query('SELECT push_cents FROM robot_outbox WHERE target_draw=1001')[0]['push_cents'],3000)
  rows=s.db.query('SELECT target_draw,status,direction FROM strategy_signals WHERE strategy=? ORDER BY target_draw',(strategy,))
  self.assertEqual([tuple(row.values()) for row in rows],[(999,'awaiting_result','大'),(1001,'pending','小')])
  # Move the missing draw outside the runtime's 200-draw sync window.
  for target in range(1001,1202):
   draw(target,latest+timedelta(seconds=(target-1000)*210),list(range(1,21)))
  after=latest+timedelta(seconds=201*210+1)
  s.signals.tick(after)
  draw(999,now+timedelta(seconds=210),list(range(1,21)))
  s.signals.tick(after+timedelta(seconds=1))
  runtime=s.signals.runtime
  self.assertEqual(runtime.query('SELECT status FROM custom_orders WHERE strategy=? AND target_draw=999',(strategy,))[0]['status'],'settled')
  with s.db.tx() as c:
   settle(c);settle(c)
   self.assertEqual(c.execute('SELECT status FROM member_orders WHERE id=?',(oid,)).fetchone()[0],'settled')
   self.assertEqual(c.execute("SELECT COUNT(*) FROM member_cash_ledger WHERE order_id=? AND kind='settle'",(oid,)).fetchone()[0],1)
  self.assertEqual(s.db.query('SELECT status FROM strategy_signals WHERE strategy=? AND target_draw=999',(strategy,))[0]['status'],'settled')
  self.assertEqual(missing_order_draws(s.db),set())
 def test_trend_runtime_export_and_no_backfill(self):
  s=self.s;s.cfg['collector']['maintenance_guard_enabled']=False
  s.cfg['trend_simulation']['strategies']={a:{'direction':'大' if a=='size' else '双','lookback':20} for a in ('size','parity')}
  now=datetime.now(timezone.utc)-timedelta(seconds=5)
  s.db.import_records([{'id':1000+i,'time':(now-timedelta(seconds=(40-i)*210)).isoformat(),'numbers':list(range(1,21))} for i in range(41)],'test',received=now.isoformat())
  engine=Signals(s);engine.tick(now+timedelta(seconds=1))
  signals=s.db.query('SELECT * FROM strategy_signals');self.assertTrue(signals);self.assertTrue(all(r['target_draw']==1041 for r in signals))
  engine.tick(now+timedelta(seconds=2));self.assertEqual(len(s.db.query('SELECT * FROM strategy_signals')),len(signals))
 def test_legacy_reversal_signal_ignores_money_stop(self):
  s=self.s;s.cfg['collector']['maintenance_guard_enabled']=False;s.cfg['simulation'].update(enabled=True,daily_loss_limit=1,max_drawdown=1)
  s.cfg['simulation']['fixed'].update(direction='大小',kind='imbalance',lookback=30,threshold=6,run_length=0,max_bets=8,max_losses=1)
  with s.db.tx() as c:c.execute("UPDATE accounts SET balance=0,paused=1,pause_reason='资金止损' WHERE id=1")
  now=datetime.now(timezone.utc)-timedelta(seconds=5)
  s.db.import_records([{'id':1000+i,'time':(now-timedelta(seconds=(40-i)*210)).isoformat(),'numbers':list(range(1,21))} for i in range(41)],'test',received=now.isoformat())
  engine=Signals(s);engine.tick(now+timedelta(seconds=1));rows=s.db.query("SELECT * FROM strategy_signals WHERE strategy='legacy'")
  self.assertEqual(len(rows),1);self.assertEqual(rows[0]['direction'],'小');self.assertEqual(rows[0]['stake'],10000)
 def check_settlement_retry(self,remaining):
  from pc28.robot_signals import RobotSignals
  aid=self.create();pid=self.plan(mode='adjust',base_cents=3000,loss_increment_cents=100)
  a=self.r.account_detail(aid);bot=a['bots'][0]
  self.r.account_action('subscriptions',{'id':aid,'version':a['version'],'bot_id':bot['id'],'subscriptions':[{'strategy_id':key,'amount_mode':'plan','money_plan_id':pid} for key in ('size','parity')]},None,'')
  self.s.cfg['collector']['maintenance_guard_enabled']=False
  self.signal(998);self.assertEqual(self.r.capture(),1)
  old=self.s.db.query('SELECT * FROM robot_outbox')[0]
  uid=self.r.account_detail(aid)['user_id']
  now=datetime.now(timezone.utc).replace(microsecond=0)
  draw_time=now+timedelta(seconds=remaining)
  self.s.db.import_records([{'id':998,'time':(now-timedelta(seconds=210-remaining)).isoformat(),'numbers':list(range(1,21))}],'test')
  with self.s.db.tx() as c:
   c.execute("INSERT OR REPLACE INTO runtime_state VALUES('draw_timer',?,?)",(json.dumps({'next_draw_id':999,'next_draw_at':draw_time.isoformat()}),now.isoformat()))
   c.execute("INSERT INTO member_orders(user_id,outbox_id,target_draw,direction,stake,placed_at,accepted_at,odds_json) VALUES(?,?,998,'大',3000,'t','t','{}')",(uid,old['id']))
  first=self.signal(999)
  with self.s.db.tx() as c:
   c.execute("INSERT INTO strategy_signals(strategy,version,target_draw,cutoff_draw,created,direction,cycle,step,odds_json,evidence,source_engine,source_id) SELECT 'parity',version,target_draw,cutoff_draw,created,'单',cycle,step,odds_json,evidence,'retry-test',source_id FROM strategy_signals WHERE id=?",(first,))
   c.execute("UPDATE robot_outbox SET status='sent' WHERE id=?",(old['id'],))
  self.assertEqual(self.r.capture(),1)
  self.assertFalse(self.r.deliver_one())
  self.assertEqual(self.s.db.query('SELECT * FROM robot_delivery_slots'),[])
  self.assertEqual(len(self.s.db.query('SELECT * FROM robot_settlement_retries')),1)
  with self.s.db.tx() as c:
   c.execute("UPDATE member_orders SET status='settled',pnl=-3000,outcome='loss' WHERE outbox_id=?",(old['id'],))
  restarted=RobotSignals(self.s,self.r.secret)
  with patch('pc28.robot_signals.time.time',return_value=now.timestamp()):
   self.assertEqual(restarted.capture(),int(remaining>60))
   self.assertEqual(restarted.capture(),0)
  self.assertEqual(self.s.db.query('SELECT * FROM robot_settlement_retries'),[])
  expected=[{'push_cents':3100,'settlement_retry':1}] if remaining>60 else []
  self.assertEqual(self.s.db.query("SELECT push_cents,settlement_retry FROM robot_outbox WHERE target_draw=999 AND strategy_id='size'"),expected)
  if remaining>60:
   with patch('pc28.robot_signals.time.time',return_value=now.timestamp()),patch('pc28.robot_signals.secrets.randbelow',side_effect=lambda n:n-1):
    self.assertFalse(restarted.deliver_one())
   self.assertEqual(len(self.s.db.query('SELECT * FROM robot_batch_items')),2)
   scheduled=self.s.db.query('SELECT scheduled_at FROM robot_outbox WHERE target_draw=999')[0]['scheduled_at']
   self.assertLess(datetime.fromisoformat(scheduled),draw_time-timedelta(seconds=10))

 def test_retry_after_settlement_with_more_than_one_minute(self):
  self.check_settlement_retry(61)

 def test_no_settlement_retry_with_exactly_one_minute(self):
  self.check_settlement_retry(60)

 def test_old_unconfirmed_failure_blocks_new_amount(self):
  aid=self.create();pid=self.plan();sub=self.subscribe(aid,pid);self.signal();self.r.capture();out=self.s.db.query('SELECT * FROM robot_outbox')[0]
  with self.s.db.tx() as c:
   c.execute("UPDATE robot_outbox SET status='failed' WHERE id=?",(out['id'],))
   c.execute("INSERT INTO robot_execution VALUES(?,'unconfirmed','人工核对',?,0)",(out['id'],datetime.now(timezone.utc).isoformat()))
   with self.assertRaisesRegex(ValueError,'尚未确认'):mp.quote(c,sub)
 def test_runtime_copies_hour_schedule_and_excludes_future_data(self):
  s=self.s;s.cfg['collector']['maintenance_guard_enabled']=False
  now=datetime.now(timezone.utc)-timedelta(seconds=5);at=now.isoformat()
  s.db.import_records([{'id':2000,'time':at,'numbers':list(range(1,21))}],'test',received=at)
  timer=json.dumps({'next_draw_id':2001,'next_draw_at':(now+timedelta(seconds=210)).isoformat()})
  with s.db.tx() as c:c.execute("INSERT INTO runtime_state VALUES('draw_timer',?,?)",(timer,at))
  engine=Signals(s);engine.tick(now+timedelta(seconds=1))
  self.assertEqual(engine.runtime.query("SELECT value FROM runtime_state WHERE key='draw_timer'")[0]['value'],timer)
  s.db.import_records([{'id':2001,'time':(now+timedelta(seconds=210)).isoformat(),'numbers':list(range(1,21))}],'test',received=at)
  engine.tick(now+timedelta(seconds=2));self.assertFalse(engine.runtime.query('SELECT id FROM draws WHERE id=2001'));self.assertFalse(s.db.query('SELECT id FROM strategy_signals WHERE target_draw=2002'))
 def test_old_subscription_schema_migrates_without_losing_bindings(self):
  from pc28.robot_accounts import SCHEMA
  aid=self.create();before=self.s.db.query('SELECT * FROM robot_subscriptions')
  with self.s.db.tx() as c:
   c.execute('DROP TABLE robot_subscriptions')
   sql=next(x for x in SCHEMA.split(';') if 'CREATE TABLE IF NOT EXISTS robot_subscriptions(' in x).replace('UNIQUE(account_id,bot_id,strategy_id)','UNIQUE(account_id,strategy_id)');c.execute(sql)
   fields=[r[1] for r in c.execute('PRAGMA table_info(robot_subscriptions)')]
   c.executemany('INSERT INTO robot_subscriptions('+','.join(fields)+') VALUES('+','.join('?' for _ in fields)+')',([r[k] for k in fields] for r in before))
  self.r.init_accounts();after=self.s.db.query('SELECT * FROM robot_subscriptions');self.assertEqual(len(before),len(after));self.assertTrue(all('money_plan_id' in r for r in after));self.assertEqual(self.r.account_detail(aid)['name'],'主账号')

 def test_two_subscriptions_do_not_share_progress(self):
  aid=self.create();pid=self.plan(mode='adjust',base_cents=3000,loss_increment_cents=100,win_decrement_cents=100);a=self.subscribe(aid,pid)
  body=self.body(self.member('second_money_member'),external=202);body['subscriptions']=[{'strategy_id':'size','bot_index':0,'amount_mode':'plan','money_plan_id':pid}];second=self.r.account_save(body,None,'')['id'];b=self.s.db.query("SELECT * FROM robot_subscriptions WHERE account_id=? AND strategy_id='size'",(second,))[0]
  self.signal();self.r.capture();outs=self.s.db.query("SELECT * FROM robot_outbox WHERE subscription_id IS NOT NULL")
  with self.s.db.tx() as c:
   for out in outs:
    c.execute("UPDATE robot_outbox SET status='failed' WHERE id=?",(out['id'],))
   out=next(r for r in outs if r['subscription_id']==a['id'])
   c.execute("INSERT INTO member_orders(user_id,outbox_id,target_draw,direction,stake,placed_at,accepted_at,odds_json,status,pnl,outcome) VALUES(?,?,999,'大',3000,'t','t','{}','settled',-3000,'loss')",(self.s.db.query('SELECT user_id FROM robot_accounts WHERE id=?',(aid,))[0]['user_id'],out['id']))
   self.assertEqual(mp.quote(c,a)[0],3100);self.assertEqual(mp.quote(c,b)[0],3000)
 def test_robot_limits_are_isolated_and_auto_resume_by_draws(self):
  from pc28 import robot_risk
  from pc28.member_orders import entry
  from pc28.model import utcnow
  aid=self.create();account=self.r.account_detail(aid);bid=account['bots'][0]['id'];uid=account['user_id']
  with self.s.db.tx() as c:
   robot_risk.save(c,bid,{'capital':100000,'loss_mode':'amount','loss_limit':40000,'profit_target':30000,'trailing_bps':5000,'max_stake':3000,'daily_loss_limit':10000,'max_drawdown':15000,'win_limit':2,'pause_draws':3})
   self.assertIn('单笔',robot_risk.admit(c,bid,[{'id':991,'push_cents':3100}]))
   for i in (1,2):
    c.execute("INSERT INTO member_orders(user_id,outbox_id,target_draw,direction,stake,placed_at,accepted_at,odds_json,status,pnl,outcome,settled_at,bot_id) VALUES(?,?,?,'大',1000,'t','t','{}','settled',1000,'win',?,?)",(uid,900+i,100+i,utcnow(),bid));oid=c.execute('SELECT last_insert_rowid()').fetchone()[0];entry(c,uid,'settle',2000,order_id=oid)
   risk=robot_risk.check(c,bid);self.assertEqual(risk['pause_until_draw'],105);self.assertTrue(risk['paused'])
  s=self.s;s.db.import_records([{'id':105,'time':datetime.now(timezone.utc).isoformat(),'numbers':list(range(1,21))}],'test')
  with s.db.tx() as c:self.assertEqual(robot_risk.check(c,bid)['paused'],'')

class MoneyPermissionTests(unittest.TestCase):
 import test_admin_permissions as permissions_fixture
 setUp=permissions_fixture.PermissionTests.setUp
 tearDown=permissions_fixture.PermissionTests.tearDown
 setup=permissions_fixture.PermissionTests.setup
 create=permissions_fixture.PermissionTests.create
 login=permissions_fixture.PermissionTests.login
 grant=permissions_fixture.PermissionTests.grant
 def test_plan_manager_requires_separate_write_permission(self):
  self.setup();uid=self.create();self.grant(uid,['page.money-plans']);client=self.login()
  self.assertEqual(client.call('/api/admin/money-plans')[0],200)
  body={'name':'权限金额方案','enabled':True,'settings':mp.DEFAULTS}
  self.assertEqual(client.call('/api/admin/money-plan-save',body)[0],403)
  self.grant(uid,['page.money-plans','money.manage']);client=self.login();self.assertEqual(client.call('/api/admin/money-plan-save',body)[0],200)

if __name__=='__main__':unittest.main()
