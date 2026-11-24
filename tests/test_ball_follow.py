import unittest,json
from datetime import datetime,timezone,timedelta
import test_previous_strategy as previous
import test_money_plans as money
from pc28.model import previous_direction,direction_hits,settlement
from pc28.follow_strategy import defaults
from pc28.custom_strategy import save
from pc28.strategies import control,require_fixed_direction
from pc28.history_backtest import prepare,source_window,replay

class BallModelTests(unittest.TestCase):
 def test_boundaries_and_fixed_odds(self):
  for ball in 'abc':
   for value in range(10):
    row={ball:value};direction=ball+('大' if value>=5 else '小')
    self.assertEqual(previous_direction(row,ball),direction);self.assertTrue(direction_hits(direction,'大','单',row));self.assertFalse(direction_hits(ball+('小' if value>=5 else '大'),'大','单',row))
   for total in (0,13,14,27):
    result=settlement(500,ball+'大',total,True,{'refund_winning_totals':[0,27],'full_payout_totals':[13,14],'normal':200,'high_stake_threshold_cents':100,'high_stake_factor':198})
    self.assertEqual((result['credit'],result['pnl'],result['outcome']),(995,495,'win'))
    self.assertEqual(settlement(500,ball+'大',total,False,{})['pnl'],-500)

class BallFollowTests(unittest.TestCase):
 setUp=previous.PreviousStrategyTests.setUp
 tearDown=previous.PreviousStrategyTests.tearDown
 create=previous.PreviousStrategyTests.create
 draw=previous.PreviousStrategyTests.draw
 step=previous.PreviousStrategyTests.step
 def test_three_balls_live_replay_and_order_directions(self):
  keys={axis:self.create(axis)[0] for axis in 'abc'};values=[(4,5,0),(5,4,9),(6,3,8),(0,9,4),(1,8,3)]
  for i,abc in enumerate(values):
   at=self.draw(i,'大');total=sum(abc)
   with self.s.db.tx() as c:c.execute('UPDATE draws SET a=?,b=?,c=?,total=?,size=?,parity=? WHERE id=?',(*abc,total,'大' if total>=14 else '小','单' if total%2 else '双',100+i))
   self.step(at+timedelta(seconds=1))
  for j,axis in enumerate('abc'):
   key=keys[axis];control(self.s,{'id':key,'enabled':False});orders=self.s.db.query('SELECT * FROM custom_orders WHERE strategy=? ORDER BY target_draw',(key,))
   self.assertEqual([o['direction'] for o in orders],[axis+('大' if v[j]>=5 else '小') for v in values])
   for i,o in enumerate(orders[:-1]):self.assertEqual(o['pnl'],round(o['stake']*.99) if (values[i][j]>=5)==(values[i+1][j]>=5) else -o['stake'])
   self.assertEqual(require_fixed_direction(json.loads(orders[0]['strategy_json'])['settings']),axis)
   p=prepare(self.s,{'strategy_id':key});rows,_=source_window(self.s.db.rows(),set(),p['as_of']);_,expected=replay(rows,p['snapshot'])
   self.assertEqual([(o['target_draw'],o['direction'],o['pnl']) for o in orders[:-1]],[(o['target_draw'],o['direction'],o['pnl']) for o in expected])

class BallRobotTests(unittest.TestCase):
 setUp=money.MoneyPlanTests.setUp
 tearDown=money.MoneyPlanTests.tearDown
 member=money.MoneyPlanTests.member
 body=money.MoneyPlanTests.body
 create=money.MoneyPlanTests.create
 plan=money.MoneyPlanTests.plan
 def test_mixed_payload_snapshot_and_member_settlement(self):
  from pc28.member_orders import import_signal,settle
  s=self.s;s.cfg['collector']['maintenance_guard_enabled']=False;keys=[]
  for axis in ('size','parity','a','b','c'):
   key=save(s,{'name':'上期'+axis,'settings':defaults(s.cfg,'previous',axis)})['id'];control(s,{'id':key,'enabled':True});keys.append(key)
  aid=self.create();regular=self.plan(base_cents=1000);ball=self.plan(base_cents=500);a=self.r.account_detail(aid)
  self.r.account_action('subscriptions',{'id':aid,'version':a['version'],'bot_id':a['bots'][0]['id'],'subscriptions':[{'strategy_id':key,'amount_mode':'plan','money_plan_id':regular if i<2 else ball} for i,key in enumerate(keys)]},None,'')
  now=datetime.now(timezone.utc).replace(microsecond=0);s.db.import_records([{'id':998,'time':(now-timedelta(seconds=10)).isoformat(),'numbers':list(range(1,21))}],'test',received=now.isoformat());s.signals.tick(now)
  self.assertEqual(self.r.capture(),5);out=s.db.query('SELECT * FROM robot_outbox ORDER BY id')
  expected='大10 单10 a大5 b小5 c大5';self.assertEqual(' '.join(o['message'] for o in out),expected)
  self.r.deliver_one();wire=json.loads(s.db.query('SELECT request FROM robot_batches')[0]['request']);self.assertEqual(wire['payload']['text'],expected)
  draw_at=(now+timedelta(seconds=200)).isoformat();s.db.import_records([{'id':999,'time':draw_at,'numbers':list(range(1,21))}],'test',received=draw_at);s.signals.settle(now+timedelta(seconds=201))
  with s.db.tx() as c:
   for o in out:import_signal(c,s,o,None,o['created'],o['created'])
   settle(c,until=draw_at)
  orders=s.db.query("SELECT direction,stake,pnl,outcome,result FROM member_orders WHERE direction IN ('a大','b小','c大') ORDER BY id")
  self.assertEqual(len(orders),3)
  for o in orders:self.assertEqual((o['stake'],o['pnl'],o['outcome']),(500,495,'win'));self.assertIn(o['direction'],o['result'])
  self.assertEqual([o['pnl'] for o in s.db.query("SELECT pnl FROM strategy_signals WHERE strategy IN (?,?,?) ORDER BY id",keys[2:])],[9900,9900,9900])
if __name__=='__main__':unittest.main()
