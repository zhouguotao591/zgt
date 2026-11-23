import unittest
import numpy as np
import pandas as pd
from scripts.analyze_six_strategy_entries import extra_gate,EXTRA,timing_states,select_candidate,select_capital_candidate,roi_uncertainty
from tests.test_regime_study import RegimeStudyTests

class SixStrategyStudyTests(unittest.TestCase):
 def test_all_extra_rules_are_causal(self):
  f=RegimeStudyTests().fixture();changed=f.copy();changed.loc[4000:,'pnl']=-999999.;changed.loc[4000:,'hit']=0
  for key in EXTRA:
   with self.subTest(rule=key):
    original=extra_gate(f,key,{})[0];later=extra_gate(changed,key,{})[0]
    self.assertTrue(np.array_equal(original[:4000],later[:4000]))
 def test_stop_wins_over_simultaneous_entry(self):
  ends=np.arange(10)*3600+1735689600;f=pd.DataFrame({'decision':ends+1})
  mask,_=timing_states(f,np.ones(10,dtype=bool),np.ones(10,dtype=bool),np.ones(10,dtype=bool),ends,resume=1)
  self.assertFalse(mask.any())
 def test_accuracy_gate_cannot_use_its_own_outcome(self):
  f=RegimeStudyTests().fixture();other=f.copy();other.loc[200,'hit']=1-f.loc[200,'hit']
  self.assertTrue(np.array_equal(extra_gate(f,'accuracy_40_20',{})[0][:201],extra_gate(other,'accuracy_40_20',{})[0][:201]))
 def test_validation_targets_roi_not_larger_total_stake(self):
  baseline={'bets':10000,'net':10000,'roi':.01,'prediction_hit':.51,'max_dd':5000}
  filtered={'bets':2500,'net':5000,'roi':.02,'prediction_hit':.52,'max_dd':2000}
  key,valid=select_candidate({'always':{'train':baseline,'valid':baseline},'candidate':{'train':filtered,'valid':filtered}},'train','valid')
  self.assertEqual(key,'candidate');self.assertTrue(valid)
  capital_key,capital_valid=select_capital_candidate({'always':{'train':baseline,'valid':baseline},'candidate':{'train':filtered,'valid':filtered}},'train','valid')
  self.assertEqual(capital_key,'always');self.assertFalse(capital_valid)
 def test_identical_masks_have_zero_roi_difference(self):
  f=RegimeStudyTests().fixture();f['day']=np.floor(f.decision/86400).astype(int);mask=np.ones(len(f),dtype=bool)
  r=roi_uncertainty(f,mask,mask);self.assertEqual(r['delta_roi'],0);self.assertEqual(r['delta_roi_ci95'],[0.,0.])

if __name__=='__main__':unittest.main()
