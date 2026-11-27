import json,unittest
from datetime import datetime,timezone,timedelta
from unittest.mock import patch
import test_money_plans as fixture
from pc28 import money_plans as mp
from pc28.custom_strategy import save
from pc28.follow_strategy import defaults
from pc28.strategies import control


class TypeMoneyPlanTests(unittest.TestCase):
 setUp=fixture.MoneyPlanTests.setUp
 tearDown=fixture.MoneyPlanTests.tearDown
 member=fixture.MoneyPlanTests.member
 body=fixture.MoneyPlanTests.body
 create=fixture.MoneyPlanTests.create
 order=fixture.MoneyPlanTests.order
 plan=fixture.MoneyPlanTests.plan

 def prepare(self):
  self.s.cfg['collector']['maintenance_guard_enabled']=False
  settings=defaults(self.s.cfg,'previous')
  key=save(self.s,{'name':'一条多类型策略','settings':settings,'previous_types':['size','parity']})['id']
  control(self.s,{'id':key,'enabled':True})
  aid=self.create();pid=self.plan(mode='adjust',base_cents=3000,loss_increment_cents=100,win_decrement_cents=100)
  account=self.r.account_detail(aid);bot=account['bots'][0]
  self.r.account_action('subscriptions',{'id':aid,'version':account['version'],'bot_id':bot['id'],'subscriptions':[{'strategy_id':key,'amount_mode':'plan','money_plan_id':pid}]},None,'')
  sub=self.s.db.query('SELECT * FROM robot_subscriptions WHERE bot_id=? AND strategy_id=?',(bot['id'],key))[0]
  now=datetime.now(timezone.utc).replace(microsecond=0)
  self.s.db.import_records([{'id':998,'time':(now-timedelta(seconds=10)).isoformat(),'numbers':list(range(1,21))}],'test',received=now.isoformat())
  self.s.signals.tick(now)
  self.assertEqual(len(self.s.db.query('SELECT * FROM strategy_signals WHERE strategy=?',(key,))),2)
  self.assertEqual(self.r.capture(),2);self.assertEqual(self.r.capture(),0)
  out=self.s.db.query('SELECT * FROM robot_outbox WHERE strategy_id=? ORDER BY id',(key,))
  self.assertEqual(len(out),2)
  self.assertEqual({json.loads(row['money_plan_snapshot'])['previous_type'] for row in out},{'size','parity'})
  return key,aid,sub,out,now

 def test_subscription_progress_is_independent_and_same_type_waits(self):
  key,aid,sub,orders,now=self.prepare()
  with self.s.db.tx() as c:
   with self.assertRaises(mp.AwaitingSettlement):mp.quote(c,sub,previous_type='size')
   uid=self.r.account_detail(aid)['user_id']
   for row in orders:
    kind=json.loads(row['money_plan_snapshot'])['previous_type'];win=kind=='size'
    c.execute("UPDATE robot_outbox SET status='sent' WHERE id=?",(row['id'],))
    c.execute("INSERT INTO member_orders(user_id,outbox_id,target_draw,direction,stake,placed_at,accepted_at,odds_json,status,pnl,outcome) VALUES(?,?,?,?,3000,'t','t','{}','settled',?,?)",(uid,row['id'],999,'大' if win else '单',3000 if win else -3000,'win' if win else 'loss'))
   self.assertEqual(mp.quote(c,sub,previous_type='size')[0],2900)
   self.assertEqual(mp.quote(c,sub,previous_type='parity')[0],3100)
   self.assertEqual(mp.quote(c,sub,previous_type='size')[0],2900)

 def test_one_subscription_merges_both_types_in_one_message(self):
  key,aid,sub,orders,now=self.prepare()
  old=(now-timedelta(seconds=70)).isoformat()
  with self.s.db.tx() as c:c.execute('UPDATE robot_outbox SET created=?,next_attempt=0 WHERE strategy_id=?',(old,key))
  with patch('pc28.maintenance.live_guard',return_value=''),patch('pc28.robot_signals.draw_deadline',return_value={'draw_at':int(now.timestamp())+200,'expires_at':int(now.timestamp())+170}),patch('pc28.robot_signals.urllib.request.build_opener') as opener:
   opener.return_value.open.return_value.__enter__.return_value.status=202
   self.assertTrue(self.r.deliver_one())
   wire=json.loads(opener.return_value.open.call_args.args[0].data)
   self.assertEqual(set(wire['payload']['text'].split()),{row['message'] for row in orders})
  records=self.r.records({})['rows']
  self.assertEqual(len(records),1);self.assertEqual(records[0]['order_count'],2)
