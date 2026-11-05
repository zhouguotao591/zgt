import unittest,copy
from pc28.model import SCREENSHOT_ODDS,payout,settlement,stake_limit_reason,Strategy
from pc28.engine import Prepared,replay
class OddsTests(unittest.TestCase):
 def test_13_14_regular_odds(self):
  for direction,total in [('小',13),('单',13),('大',14),('双',14)]:self.assertEqual(payout(10000,direction,total,True,SCREENSHOT_ODDS),10000)
 def test_high_stake_boundary_includes_3001(self):
  self.assertEqual(payout(300000,'大',15,True,SCREENSHOT_ODDS),300000)
  self.assertEqual(payout(300099,'大',15,True,SCREENSHOT_ODDS),300099)
  self.assertEqual(payout(300100,'大',15,True,SCREENSHOT_ODDS),294098)
 def test_13_14_override_high_stake_discount(self):
  self.assertEqual(payout(300100,'大',14,True,SCREENSHOT_ODDS),300100)
  self.assertEqual(payout(300100,'单',13,True,SCREENSHOT_ODDS),300100)
 def test_extremes_only_winning_bets_refunded(self):
  for direction,total in [('小',0),('双',0),('大',27),('单',27)]:
   self.assertEqual(settlement(10000,direction,total,True,SCREENSHOT_ODDS)['outcome'],'refund');self.assertEqual(payout(10000,direction,total,True,SCREENSHOT_ODDS),0)
  self.assertEqual(payout(10000,'大',0,False,SCREENSHOT_ODDS),-10000)
  self.assertEqual(payout(10000,'小',27,False,SCREENSHOT_ODDS),-10000)
 def test_research_grid_uses_configured_base(self):
  from pc28.config import DEFAULT
  from pc28.research import grid
  cfg=copy.deepcopy(DEFAULT);cfg['simulation']['fixed']['base_cents']=25025
  self.assertTrue(all(s.base_cents==25025 for s in grid(cfg)))
 def test_legacy_snapshots_preserved(self):
  old={'normal':200,'special_size':199,'special_parity':198}
  self.assertEqual(payout(10000,'单',13,True,old),9800);self.assertEqual(payout(10000,'大',27,True,old),10000)
 def test_market_limits_always_apply(self):
  self.assertEqual(stake_limit_reason(99,SCREENSHOT_ODDS),'market_min_stake');self.assertEqual(stake_limit_reason(2000000,SCREENSHOT_ODDS),'');self.assertEqual(stake_limit_reason(2000001,SCREENSHOT_ODDS),'market_max_stake')
 def test_refund_resets_stake_but_not_profit_count(self):
  rows=[{'id':i,'source_time':f'2026-01-01T00:{i:02}:00','total':19,'size':'大','parity':'单'} for i in range(33)];rows[31].update(total=0,size='小',parity='双')
  r=replay(Prepared(rows),Strategy(direction='小',threshold=8,max_bets=8),SCREENSHOT_ODDS,trace=True)
  self.assertEqual([x['stake'] for x in r['orders']],[100,200,100]);self.assertEqual(r['net'],-200);self.assertEqual(r['refunds'],1);self.assertEqual(r['profitable_wins'],0);self.assertEqual(r['wins'],1)
if __name__=='__main__':unittest.main()
