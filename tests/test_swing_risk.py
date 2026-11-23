import copy,json,unittest
from datetime import datetime,timezone
from pc28.trend_risk import NEW_DEFAULT,validate,analyze,transition
from pc28.swing_risk import features
from pc28.trend_patterns import detect
import test_trend_risk as fixture

RULE={**NEW_DEFAULT,'mode':'auto','swing_stop_atr':0}
NOW=fixture.NOW

def result(bottom=False,top=False,close=10,environment='down',atr=2):
 return {'status':'neutral','reason':'fixture','environment':environment,'last_bar':{'close':close},
         'patterns':{'prior_middle':9},'swing':{'bottom':bottom,'top':top,'high':11,'low':5,'atr':atr,
         'bottom_patterns':['bottom3'] if bottom else [],'top_patterns':['top3'] if top else []}}

def step(old,r,t,rule=RULE):
 s=transition(old,r,rule,t)
 return {**s,'closed_at':t,'analysis':json.dumps(r)},r

class SwingTests(unittest.TestCase):
 def test_bottom_during_downtrend_then_later_confirmation(self):
  s,r=step({},result(bottom=True),NOW)
  self.assertTrue(s['blocked']);self.assertEqual(r['gate']['phase'],'candidate')
  s,r=step(s,result(close=12),NOW+3600)
  self.assertEqual(s['action'],'resume');self.assertFalse(s['blocked'])
 def test_single_top_shape_is_latched_until_later_breakdown(self):
  old={'blocked':0,'gate':{'phase':'admitted'},'closed_at':NOW-3600}
  s,r=step(old,result(top=True,close=9,environment='up'),NOW)
  self.assertFalse(s['blocked']);self.assertEqual(r['gate']['phase'],'exit_candidate')
  s,r=step(s,result(close=4,environment='range'),NOW+3600)
  self.assertEqual(s['action'],'pause');self.assertIn('顶部反转',r['reason'])
 def test_down_slope_or_minor_pullback_alone_does_not_exit(self):
  s,r=step({'blocked':0,'closed_at':NOW-3600,'gate':{'phase':'admitted'}},result(close=8),NOW)
  self.assertFalse(s['blocked']);self.assertNotIn('exit_candidate',s['gate'])
 def test_exit_confirmation_counts_closes_not_repeated_shapes(self):
  rule={**RULE,'pause_confirm':2}
  s,r=step({'blocked':0,'closed_at':NOW-3600,'gate':{'phase':'admitted'}},result(top=True),NOW,rule)
  s,r=step(s,result(close=4),NOW+3600,rule);self.assertFalse(s['blocked']);self.assertEqual(s['risk_count'],1)
  s,r=step(s,result(close=4),NOW+7200,rule);self.assertEqual(s['action'],'pause')
 def test_candidate_expiry_invalidation_and_false_break_buffer(self):
  for close,delta in ((10,5*3600),(4,3600),(11.1,3600)):
   with self.subTest(close=close):
    s,_=step({},result(bottom=True),NOW);s,r=step(s,result(close=close),NOW+delta)
    self.assertTrue(s['blocked'])
  s,_=step({'blocked':0,'closed_at':NOW-3600,'gate':{'phase':'admitted'}},result(top=True),NOW)
  s,r=step(s,result(close=12),NOW+3600);self.assertFalse(s['blocked']);self.assertNotIn('exit_candidate',s['gate'])
 def test_floor_and_trailing_stop_veto_even_without_top_shape(self):
  s,_=step({},result(bottom=True),NOW);s,_=step(s,result(close=12),NOW+3600)
  s,r=step(s,result(close=4),NOW+7200);self.assertEqual(s['action'],'pause')
  rule={**RULE,'swing_stop_atr':2}
  s,r=step({'blocked':0,'closed_at':NOW-3600,'gate':{'phase':'admitted'}},result(close=12),NOW,rule)
  self.assertEqual(s['gate']['trailing_floor'],8)
  s,r=step(s,result(close=7),NOW+3600,rule);self.assertEqual(s['action'],'pause');self.assertIn('ATR',r['reason'])
 def test_persistence_origin_translation_and_no_inplace_state_mutation(self):
  s,_=step({'blocked':0,'closed_at':NOW-3600,'gate':{'phase':'admitted'}},result(top=True),NOW)
  before=copy.deepcopy(s);r=result(close=104);r.update(previous_close=110,previous_closed_at=NOW)
  without_gate={k:v for k,v in s.items() if k!='gate'}
  a=step(s,copy.deepcopy(r),NOW+3600);b=step(without_gate,copy.deepcopy(r),NOW+3600)
  self.assertEqual(a,b);self.assertEqual(s,before);self.assertEqual(a[0]['action'],'pause')
 def test_feature_wave_requires_prior_amplitude_efficiency_and_extreme(self):
  bars=fixture.TrendMathTests().bars(True)
  bars[-3].update(open=-27,close=-30,high=-26.9,low=-30.1)
  bars[-2].update(open=-30,close=-31,high=-29.9,low=-32)
  bars[-1].update(open=-31,close=-29,high=-28.9,low=-31.1)
  p=detect(bars,20,'all');f=features(bars,RULE,p)
  self.assertTrue(f['bottom']);self.assertEqual(f['direction'],'down')
  inverted=[{**b,'open':-b['open'],'close':-b['close'],'high':-b['low'],'low':-b['high']} for b in bars]
  top=features(inverted,RULE,detect(inverted,20,'all'))
  self.assertTrue(top['top']);self.assertEqual(top['direction'],'up')
  scaled=[{**b,**{k:b[k]*7+100 for k in ('open','high','low','close')}} for b in bars]
  scaled_f=features(scaled,RULE,detect(scaled,20,'all'))
  self.assertTrue(scaled_f['bottom']);self.assertAlmostEqual(f['amplitude_atr'],scaled_f['amplitude_atr']);self.assertAlmostEqual(f['efficiency'],scaled_f['efficiency'])
  weak=features(bars,{**RULE,'swing_atr':10},p);self.assertFalse(weak['bottom'])
  # A local shape in a flat/choppy context cannot claim a prior wave.
  flat=[{**b,'open':0,'close':0,'high':1,'low':-1} for b in bars]
  self.assertFalse(features(flat,RULE,{**p,'bull':['bottom3']})['bottom'])
 def test_validation_and_future_bar_immunity(self):
  for k,v in [('swing_bars',3),('swing_atr',0),('swing_efficiency',float('nan')),('reversal_buffer_atr',-1),('swing_stop_atr',11)]:
   with self.assertRaises(ValueError):validate({**RULE,k:v})
  b=fixture.TrendMathTests().bars();a=analyze(b,RULE,NOW)
  future={**b[-1],'time':datetime.fromtimestamp(NOW,timezone.utc).isoformat(),'close':-99999}
  self.assertEqual(a,analyze(b+[future],RULE,NOW))

class SwingServiceTests(unittest.TestCase):
 setUp=fixture.TrendServiceTests.setUp
 tearDown=fixture.TrendServiceTests.tearDown
 save=fixture.TrendServiceTests.save
 gate=fixture.TrendServiceTests.gate
 def test_saved_swing_runs_same_transition_and_retains_manual_veto(self):
  from unittest.mock import patch
  self.save(mode='auto',**{k:v for k,v in RULE.items() if k!='mode'})
  with patch.object(self.s.trend_risk,'source',return_value=result(bottom=True)):self.s.trend_risk.tick(NOW)
  self.assertIsNotNone(self.gate())
  self.s.trend_risk.save(self.owner,{'id':1,'version':1,'action':'evaluate'},'')
  with patch.object(self.s.trend_risk,'source',return_value=result(bottom=True)):self.s.trend_risk.tick(NOW)
  self.assertEqual(self.s.db.query('SELECT safe_count FROM trend_risk_state')[0]['safe_count'],0)
  from pc28.trend_risk import TrendRisk
  self.s.trend_risk=TrendRisk(self.s)
  with patch.object(self.s.trend_risk,'source',return_value=result(close=12)):self.s.trend_risk.tick(NOW+3600)
  self.assertIsNone(self.gate(now=NOW+3600))
  self.s.trend_risk.save(self.owner,{'id':1,'version':1,'action':'pause'},'')
  self.assertIn('人工',self.gate(now=NOW+3600))
  self.s.trend_risk.save(self.owner,{'id':1,'version':1,'action':'resume'},'')
  self.assertIsNotNone(self.gate(now=NOW+3600))

if __name__=='__main__':unittest.main()
