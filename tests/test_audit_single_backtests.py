import unittest
from datetime import datetime,timedelta,timezone
from scripts.audit_single_backtests import reference


class IndependentAuditTests(unittest.TestCase):
 def snapshot(self,base=3000,mode='adjust'):
  return {'maintenance_guard':False,'settings':{'direction':'单','stake_mode':mode,'base_cents':base,'initial_balance':100000,'max_stake':20000,'max_drawdown':0,'daily_loss_limit':0,'win_decrement_cents':100,'loss_increment_cents':100,'max_increase_steps':0,'max_decrease_steps':0,'stake_floor':'market'},'odds':{'normal':200,'special_size':200,'special_parity':200,'refund_winning_totals':[0,27],'full_payout_totals':[13,14],'min_stake_cents':100,'max_stake_cents':2000000,'high_stake_threshold_cents':300100,'high_stake_factor':198,'max_return_per_draw_cents':10000000}}
 def rows(self,totals):
  start=datetime(2026,1,1,tzinfo=timezone.utc)
  return [{'id':100+i,'total':n,'_time':start+timedelta(seconds=210*i),'draw_utc':(start+timedelta(seconds=210*i)).isoformat()} for i,n in enumerate([10,*totals])]
 def test_independent_next_stake_and_refund_hand_calculation(self):
  orders,result=reference(self.rows([1,2,27,1]),self.snapshot())
  self.assertEqual([o['stake'] for o in orders],[3000,2900,3000,3000])
  self.assertEqual([o['pnl'] for o in orders],[3000,-2900,0,3000])
  self.assertEqual(result['net'],31)
 def test_fixed_hit_count_includes_nonprofitable_refund(self):
  _,result=reference(self.rows([27,2,1]),self.snapshot(10000,'fixed'))
  self.assertEqual((result['wins'],result['profitable_orders'],result['loss_orders'],result['refunds'],result['net']),(2,1,1,1,0))
 def test_high_stake_rule_does_not_override_full_payout_13(self):
  orders,result=reference(self.rows([13,15]),self.snapshot(300100,'fixed'))
  self.assertEqual([o['pnl'] for o in orders],[300100,294098])
  self.assertEqual(result['net'],5941.98)


if __name__=='__main__':unittest.main()
