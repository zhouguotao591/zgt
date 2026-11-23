import unittest
from datetime import datetime,timedelta,timezone
from pc28.trend_risk import DEFAULT
from pc28.trend_risk_backtest import apply

class TrendReplayTests(unittest.TestCase):
 def fixture(self):
  start=datetime(2025,1,1,tzinfo=timezone.utc);rows=[];orders=[]
  for i in range(240):
   t=start+timedelta(minutes=i*30+10);pnl=1000 if i<60 or i>=140 else -1000
   rows.append({'id':i+1,'_time':t})
   orders.append({'target_draw':i+1,'beijing_time':t.isoformat(),'pnl':pnl,'stake':1000,'status':'settled','hit':int(pnl>0),'outcome':'win' if pnl>0 else 'loss'})
  return rows,orders,{'settings':{'initial_balance':10000}},{**DEFAULT,'mode':'auto','source':'strategy'}
 def test_pause_resume_and_profit_conservation(self):
  rows,orders,snapshot,rule=self.fixture();result,accepted,info=apply(rows,orders,snapshot,rule)
  self.assertLess(len(accepted),len(orders));self.assertGreater(len(accepted),0)
  self.assertEqual(result['net'],sum(o['pnl'] for o in accepted)/100)
  self.assertEqual(result['balance'],10000+result['net'])
  self.assertEqual(info['blocked_orders']+len(accepted),len(orders))
  actions=[e['action'] for e in info['events']]
  self.assertEqual(actions[:4],['pause','resume','pause','resume'])
  self.assertEqual(orders[-1]['pnl'],1000)
 def test_future_changes_never_change_prior_admission(self):
  rows,orders,snapshot,rule=self.fixture();cut=150
  _,past,events=apply(rows[:cut],orders[:cut],snapshot,rule)
  altered=[{**o,'pnl':-999999 if i>=cut else o['pnl']} for i,o in enumerate(orders)]
  _,full,info=apply(rows,altered,snapshot,rule)
  self.assertEqual(past,[o for o in full if o['target_draw']<=cut])
  self.assertEqual(events['events'],[e for e in info['events'] if e['draw']<=cut])
 def test_unknown_policy_and_exact_closed_boundary(self):
  rows,orders,snapshot,rule=self.fixture()
  r,accepted,_=apply(rows[:10],orders[:10],snapshot,rule);self.assertEqual(r['bets'],0)
  r,accepted,_=apply(rows[:10],orders[:10],snapshot,{**rule,'unknown_policy':'allow'})
  self.assertEqual(r['bets'],10)

if __name__=='__main__':unittest.main()
