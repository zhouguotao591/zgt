import copy,json,unittest
from unittest.mock import patch
from pc28.trend_risk import DEFAULT,NEW_DEFAULT,LEGACY_KEYS,validate,transition,analyze,reason_for
from pc28.trend_patterns import detect
import test_trend_risk as fixture
from pc28.trend_risk_backtest import apply
import test_trend_risk_backtest as replay_fixture

NOW=fixture.NOW
RULE={**NEW_DEFAULT,'decision_model':'hybrid','mode':'auto','resume_confirm':2,'pause_confirm':2}

def result(bull=False,bear=False,close=10,environment='range',support_break=False):
 return {'status':'neutral','reason':'fixture','environment':environment,'support_break':support_break,'last_bar':{'close':close},
         'patterns':{'bull_near':['bottom3'] if bull else [],'bear_near':['top3'] if bear else [],'setup_high':11,'setup_low':5,'prior_middle':9}}

def step(old,r,t,rule=RULE):
 state=transition(old,r,rule,t)
 return {**state,'closed_at':t,'analysis':json.dumps(r)},r

class PatternGateTests(unittest.TestCase):
 def test_legacy_validation_and_strict_new_parameters(self):
  legacy={k:v for k,v in DEFAULT.items() if k in LEGACY_KEYS}
  self.assertEqual(validate(legacy)['decision_model'],'legacy')
  for key,value in [('decision_model','bad'),('pattern_family','bad'),('entry_confirm','bad'),('candidate_bars',0),('candidate_bars',True)]:
   with self.assertRaises(ValueError):validate({**RULE,key:value})
 def test_unknown_allow_does_not_bypass_new_model_initialization(self):
  self.assertIsNotNone(reason_for({**RULE,'unknown_policy':'allow'},{},1,NOW))
 def test_wait_candidate_later_confirmation_and_floor_exit(self):
  s,r=step({},result(close=12),NOW);self.assertTrue(s['blocked']);self.assertEqual(r['gate']['phase'],'waiting')
  s,r=step(s,result(bull=True),NOW+3600);self.assertEqual(r['gate']['phase'],'candidate');self.assertEqual(s['safe_count'],0)
  s,r=step(s,result(close=12),NOW+7200);self.assertTrue(s['blocked']);self.assertEqual(s['safe_count'],1)
  s,r=step(s,result(close=13),NOW+10800);self.assertEqual(s['action'],'resume');self.assertEqual(r['gate']['floor'],5)
  s,r=step(s,result(close=4),NOW+14400);self.assertEqual(s['action'],'pause');self.assertIn('形态低点',r['reason'])
 def test_downtrend_veto_candidate_and_risk_confirmation(self):
  s,r=step({},result(bull=True,environment='down'),NOW);self.assertNotIn('candidate',r['gate'])
  old={'blocked':0,'closed_at':NOW,'gate':{'phase':'admitted'}}
  s,r=step(old,result(bear=True),NOW+3600);self.assertFalse(s['blocked'])
  s,r=step(s,result(bear=True),NOW+7200);self.assertEqual(s['action'],'pause')
 def test_expired_invalid_and_nonadjacent_candidates(self):
  for mode in ('expire','invalid','gap','unknown'):
   with self.subTest(mode=mode):
    s,_=step({},result(bull=True),NOW)
    r={'status':'unknown','reason':'missing'} if mode=='unknown' else result(close=4 if mode=='invalid' else 10)
    t=NOW+(14400 if mode=='expire' else 7200 if mode=='gap' else 3600)
    s,r=step(s,r,t);self.assertTrue(s['blocked']);self.assertNotIn('candidate',r['gate'])
 def test_middle_confirmation_and_minimum_pause(self):
  rule={**RULE,'entry_confirm':'middle','resume_confirm':1,'min_pause_bars':3}
  s,r=step({'blocked':1,'paused_at':NOW},result(bull=True,close=8),NOW,rule)
  s,r=step(s,result(close=10),NOW+3600,rule);self.assertTrue(s['blocked'])
  s,r=step(s,result(close=10),NOW+7200,rule);self.assertTrue(s['blocked'])
  s,r=step(s,result(close=10),NOW+10800,rule);self.assertEqual(s['action'],'resume')
 def test_persisted_json_has_same_transition_after_restart(self):
  old,r=step({},result(bull=True),NOW)
  persisted={k:v for k,v in old.items() if k!='gate'}
  self.assertEqual(step(old,result(close=12),NOW+3600),step(persisted,result(close=12),NOW+3600))
 def test_rolling_equity_origin_translates_candidate_and_floor(self):
  s,_=step({},result(bull=True),NOW)
  shifted=result(close=112);shifted.update(previous_close=110,previous_closed_at=NOW)
  s,r=step(s,shifted,NOW+3600,{**RULE,'resume_confirm':1})
  self.assertEqual(s['action'],'resume');self.assertEqual(r['gate']['floor'],105)
  shifted=result(close=213);shifted.update(previous_close=212,previous_closed_at=NOW+3600)
  s,r=step(s,shifted,NOW+7200);self.assertFalse(s['blocked']);self.assertEqual(r['gate']['floor'],205)
  s,r=step(s,result(close=204),NOW+10800);self.assertEqual(s['action'],'pause')
 def test_detection_is_causal_and_matches_research_definitions(self):
  import random,pandas as pd
  from scripts.reversal_pattern_rules import detect as research
  rng=random.Random(42);bars=[];close=0
  for i in range(100):
   opening=close;close+=rng.choice([-3,-2,-1,0,1,2,3]);bars.append(dict(open=opening,close=close,high=max(opening,close)+rng.random(),low=min(opening,close)-rng.random(),orders=2))
  expected=research(pd.DataFrame(bars))['patterns']
  mapping={'morning':'morning_nogap','evening':'evening_nogap'}
  for i in range(23,len(bars)+1):
   actual=detect(bars[:i],20,'all')
   for key in ('bottom3','top3','bull_engulf','bear_engulf','morning','evening'):
    self.assertEqual(key in actual['bull']+actual['bear'],bool(expected[mapping.get(key,key)][i-1]),(i,key))
 def test_unclosed_future_bar_cannot_change_patterns(self):
  b=fixture.TrendMathTests().bars();r=analyze(b,RULE,NOW)
  future={**b[-1],'time':__import__('datetime').datetime.fromtimestamp(NOW,__import__('datetime').timezone.utc).isoformat(),'close':-99999}
  self.assertEqual(r,analyze(b+[future],RULE,NOW))
 def test_hybrid_replay_has_no_future_leakage_and_conserves_pnl(self):
  rows,orders,snapshot,rule=replay_fixture.TrendReplayTests().fixture();rule.update(decision_model='hybrid',pattern_family='all',entry_confirm='breakout',candidate_bars=3)
  _,past,events=apply(rows[:150],orders[:150],snapshot,rule)
  altered=[{**o,'pnl':-999999 if i>=150 else o['pnl']} for i,o in enumerate(orders)]
  summary,full,info=apply(rows,altered,snapshot,rule)
  self.assertEqual(past,[o for o in full if o['target_draw']<=150]);self.assertEqual(summary['net'],sum(o['pnl'] for o in full)/100)
  self.assertEqual(events['events'],[e for e in info['events'] if e['draw']<=150])

class PatternGateServiceTests(unittest.TestCase):
 setUp=fixture.TrendServiceTests.setUp
 tearDown=fixture.TrendServiceTests.tearDown
 save=fixture.TrendServiceTests.save
 gate=fixture.TrendServiceTests.gate
 def test_tick_persistence_refresh_and_config_reset(self):
  self.save(mode='auto',decision_model='hybrid',resume_confirm=1)
  with patch.object(self.s.trend_risk,'source',return_value=result(bull=True)):
   self.s.trend_risk.tick(NOW)
   self.s.trend_risk.save(self.owner,{'id':1,'version':1,'action':'evaluate'},'');self.s.trend_risk.tick(NOW)
  state=self.s.db.query('SELECT * FROM trend_risk_state')[0]
  self.assertTrue(state['blocked']);self.assertEqual(json.loads(state['analysis'])['gate']['phase'],'candidate')
  from pc28.trend_risk import TrendRisk
  self.s.trend_risk=TrendRisk(self.s)
  with patch.object(self.s.trend_risk,'source',return_value=result(close=12)):
   self.s.trend_risk.tick(NOW+3600)
  self.assertIsNone(self.gate(now=NOW+3600))
  self.s.trend_risk.save(self.owner,{'id':1,'version':1,'settings':{**RULE,'resume_confirm':1}},'')
  with patch.object(self.s.trend_risk,'source',return_value=result(close=12)):self.s.trend_risk.tick(NOW+3600)
  self.assertIsNotNone(self.gate(now=NOW+3600));self.assertEqual(json.loads(self.s.db.query('SELECT analysis FROM trend_risk_state')[0]['analysis'])['gate']['phase'],'waiting')
 def test_manual_resume_cannot_bypass_shape_confirmation(self):
  self.save(mode='auto',decision_model='hybrid')
  with patch.object(self.s.trend_risk,'source',return_value=result(bull=True)):self.s.trend_risk.tick(NOW)
  self.s.trend_risk.save(self.owner,{'id':1,'version':1,'action':'pause'},'')
  self.assertIn('人工',self.gate())
  self.s.trend_risk.save(self.owner,{'id':1,'version':1,'action':'resume'},'')
  self.assertIsNotNone(self.gate());self.assertEqual(self.s.db.query('SELECT manual FROM trend_risk_state')[0]['manual'],0)

class PatternCapitalVetoTests(unittest.TestCase):
 setUp=fixture.TrendCaptureTests.setUp
 tearDown=fixture.TrendCaptureTests.tearDown
 def test_capital_still_vetoes_when_shape_gate_admits(self):
  from datetime import datetime,timezone,timedelta
  self.fixture.order();self.assertGreater(self.r.capture(),0);self.fixture.ready()
  fixture.TrendCaptureTests.rule(self,'auto')
  now=datetime.now(timezone.utc);previous=now-timedelta(seconds=50);due=previous+timedelta(seconds=210)
  self.s.db.import_records([{'id':998,'time':previous.isoformat(),'numbers':list(range(1,21))}],'test')
  with self.s.db.tx() as c:
   c.execute('UPDATE trend_risk_rules SET settings=?',(json.dumps({**RULE,'mode':'auto'}),))
   c.execute("UPDATE trend_risk_state SET blocked=0,status='neutral',reason='本模块放行',analysis='{}'")
   c.execute("INSERT OR REPLACE INTO runtime_state VALUES('draw_timer',?,?)",(json.dumps({'next_draw_id':999,'next_draw_at':due.isoformat()}),now.isoformat()))
  with patch('pc28.maintenance.live_guard',return_value=''),patch('pc28.member_orders.admit',return_value='资金风控否决') as capital,patch('pc28.robot_signals.urllib.request.build_opener') as network:
   self.assertTrue(self.r.deliver_one());capital.assert_called_once();network.assert_not_called()
  row=self.s.db.query('SELECT status,last_error FROM robot_outbox')[0];self.assertEqual(row['status'],'cancelled');self.assertIn('资金风控',row['last_error'])

if __name__=='__main__':unittest.main()
