import copy,json,tempfile,threading,unittest,time
from datetime import datetime,timezone,timedelta
from pathlib import Path
from unittest.mock import patch
from pc28.config import DEFAULT as CONFIG
from pc28.service import Service
from pc28.trend_risk import DEFAULT,analyze,transition,admit,validate
from pc28.auth import AuthError
NOW=int(datetime(2025,2,1,tzinfo=timezone.utc).timestamp())

class TrendMathTests(unittest.TestCase):
 def bars(self,down=False):
  rows=[];previous=0
  for i in range(30):
   close=-(i+1) if down else i+1;rows.append({'time':datetime.fromtimestamp(NOW-(30-i)*3600,timezone.utc).isoformat(),'open':previous,'close':close,'high':max(previous,close)+.1,'low':min(previous,close)-.1,'orders':2});previous=close
  return rows
 def test_trend_closed_bars_and_no_future_leakage(self):
  bars=self.bars();safe=analyze(bars,DEFAULT,NOW);self.assertEqual(safe['status'],'safe')
  bars.append({'time':datetime.fromtimestamp(NOW,timezone.utc).isoformat(),'open':0,'close':-100000,'high':0,'low':-100000,'orders':99})
  self.assertEqual(safe,analyze(bars,DEFAULT,NOW));self.assertEqual(analyze(self.bars(True),DEFAULT,NOW)['status'],'risk')
 def test_top_bottom_reversal_and_unknown(self):
  bars=self.bars();bars[-1].update(open=29,close=25,high=32,low=24.9);a=analyze(bars,DEFAULT,NOW);self.assertTrue(a['top_reversal']);self.assertEqual(a['status'],'risk')
  bars=self.bars(True);bars[-1].update(open=-29,close=-25,high=-24.9,low=-32);a=analyze(bars,DEFAULT,NOW);self.assertTrue(a['bottom_reversal']);self.assertEqual(a['status'],'safe')
  self.assertEqual(analyze([],DEFAULT,NOW)['status'],'unknown')
  bars=self.bars()
  for b in bars[-20:]:b['orders']=0
  self.assertEqual(analyze(bars,DEFAULT,NOW)['status'],'unknown')
 def test_confirmation_recovery_and_nonconsecutive_reset(self):
  first=transition({}, {'status':'risk'},DEFAULT,NOW);self.assertEqual(first['blocked'],0)
  second=transition({**first,'closed_at':NOW},{'status':'risk'},DEFAULT,NOW+3600);self.assertEqual(second['action'],'pause')
  same=transition({**first,'closed_at':NOW},{'status':'risk'},DEFAULT,NOW+7200);self.assertEqual(same['blocked'],0)
  recover=transition({**second,'closed_at':NOW+3600},{'status':'safe'},DEFAULT,NOW+7200);self.assertEqual(recover['blocked'],1)
  done=transition({**recover,'closed_at':NOW+7200},{'status':'safe'},DEFAULT,NOW+10800);self.assertEqual(done['action'],'resume')
 def test_validation(self):
  for key,value in [('mode','bad'),('interval','1d'),('lookback',19),('pause_confirm',0),('min_pause_bars',0),('slope_threshold',float('nan'))]:
   with self.assertRaises(ValueError):validate({**DEFAULT,key:value})

class TrendServiceTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'data');root=Path(self.tmp.name);cfg=copy.deepcopy(CONFIG);cfg['collector']['enabled']=False;(root/'config.json').write_text(json.dumps(cfg));self.s=Service(root);self.owner={'id':1,'role':'admin','username':'admin'}
  with self.s.db.tx() as c:
   for uid,name,role in [(1,'admin','admin'),(2,'operator','admin'),(3,'member01','member'),(4,'member02','member')]:c.execute('INSERT INTO web_users(id,username,password_hash,role,created) VALUES(?,?,?,?,?)',(uid,name,'fixture',role,'2025-01-01'))
   c.execute('INSERT INTO web_member_access VALUES(2,3)')
   for aid,uid in [(1,3),(2,4)]:
    c.execute("INSERT INTO robot_accounts(id,user_id,platform_id,external_account_id,name,enabled,created,updated) VALUES(?,?,1,?,'account',1,'t','t')",(aid,uid,aid))
    c.execute("INSERT INTO robot_bots(id,account_id,external_bot_key,name,signal_type,created,updated) VALUES(?,?,?,'bot','TEST','t','t')",(aid,aid,aid))
    c.execute("INSERT INTO robot_subscriptions(id,account_id,strategy_id,bot_id,amount_mode,starts) VALUES(?,?,'size',?,'fixed','{}')",(aid,aid,aid))
 def tearDown(self):self.s.stop.set();self.tmp.cleanup()
 def save(self,sid=1,mode='observe',**values):return self.s.trend_risk.save(self.owner,{'id':sid,'version':0,'settings':{**DEFAULT,'mode':mode,**values}},'127.0.0.1')
 def result(self,status):return {'status':status,'reason':status,'bars':30,'cutoff_draw':100,'candles':[]}
 def gate(self,bot=1,now=NOW):
  with self.s.db.tx() as c:return admit(c,bot,'size',999,'send',now)
 def test_observe_never_blocks_auto_does_and_off_releases(self):
  self.save()
  with patch.object(self.s.trend_risk,'source',return_value=self.result('risk')):
   self.s.trend_risk.tick(NOW);self.s.trend_risk.tick(NOW+3600)
  self.assertIsNone(self.gate());self.assertEqual(self.s.db.query('SELECT blocked FROM trend_risk_state')[0]['blocked'],1)
  self.s.trend_risk.save(self.owner,{'id':1,'version':1,'settings':{**DEFAULT,'mode':'auto','pause_confirm':1}},'')
  with patch.object(self.s.trend_risk,'source',return_value=self.result('risk')):self.s.trend_risk.tick(NOW)
  self.assertIn('risk',self.gate());self.assertIsNone(self.gate(2));self.assertEqual(self.s.db.query('SELECT COUNT(*) n FROM trend_risk_blocks')[0]['n'],1)
  self.s.trend_risk.save(self.owner,{'id':1,'version':2,'settings':{**DEFAULT,'mode':'off'}},'');self.assertIsNone(self.gate())
 def test_unknown_stale_manual_restore_and_restart(self):
  self.save(mode='auto');self.assertIsNotNone(self.gate())
  with patch.object(self.s.trend_risk,'source',return_value=self.result('safe')):self.s.trend_risk.tick(NOW)
  self.assertIsNone(self.gate());self.assertIsNotNone(self.gate(now=NOW+3600+121))
  self.s.trend_risk.save(self.owner,{'id':1,'version':1,'action':'pause'},'');self.assertIn('人工',self.gate())
  from pc28.trend_risk import TrendRisk
  restarted=TrendRisk(self.s);self.assertIn('人工',self.gate())
  restarted.save(self.owner,{'id':1,'version':1,'action':'resume'},'');self.assertIsNone(self.gate())
 def test_four_hour_rule_does_not_count_each_hour_as_a_new_bar(self):
  self.save(mode='auto',interval='4h')
  with patch.object(self.s.trend_risk,'source',return_value=self.result('risk')):
   self.s.trend_risk.tick(NOW);self.s.trend_risk.tick(NOW+3600)
   self.assertEqual(self.s.db.query('SELECT risk_count FROM trend_risk_state')[0]['risk_count'],1)
   self.assertIsNone(self.gate(now=NOW+3600));self.s.trend_risk.tick(NOW+14400)
  self.assertIn('risk',self.gate(now=NOW+14400))
 def test_manual_pause_survives_config_edits(self):
  self.save(mode='auto')
  with patch.object(self.s.trend_risk,'source',return_value=self.result('safe')):self.s.trend_risk.tick(NOW)
  self.s.trend_risk.save(self.owner,{'id':1,'version':1,'action':'pause'},'')
  self.s.trend_risk.save(self.owner,{'id':1,'version':1,'settings':{**DEFAULT,'mode':'auto','interval':'4h'}},'')
  self.assertIn('人工',self.gate())
  with patch.object(self.s.trend_risk,'source',return_value=self.result('safe')):self.s.trend_risk.tick(NOW)
  self.assertIn('人工',self.gate())
 def test_same_bar_dedup_force_refresh_without_double_count(self):
  self.save(mode='auto')
  with patch.object(self.s.trend_risk,'source',return_value=self.result('risk')):
   self.s.trend_risk.tick(NOW);self.s.trend_risk.tick(NOW)
   self.s.trend_risk.save(self.owner,{'id':1,'version':1,'action':'evaluate'},'');self.s.trend_risk.tick(NOW)
  state=self.s.db.query('SELECT * FROM trend_risk_state')[0];self.assertEqual(state['risk_count'],1);self.assertFalse(state['blocked']);self.assertEqual(self.s.db.query('SELECT COUNT(*) n FROM trend_risk_decisions')[0]['n'],1)
 def test_scope_optimistic_version_and_audit(self):
  operator={'id':2,'role':'admin','username':'operator'}
  self.assertEqual(self.s.trend_risk.listing(operator,{})['total'],1)
  with self.assertRaises(AuthError):self.s.trend_risk.save(operator,{'id':2,'version':0,'settings':DEFAULT},'')
  with self.assertRaises(AuthError):self.s.trend_risk.logs(operator,{'id':2})
  self.save()
  with self.assertRaises(AuthError):self.save()
  self.assertEqual(self.s.db.query('SELECT COUNT(*) n FROM trend_risk_events')[0]['n'],1)
 def test_source_uses_only_verified_closed_signal_orders(self):
  self.save()
  with self.s.db.tx() as c:
   for i in range(30):
    t=datetime.fromtimestamp(NOW-(30-i)*3600+1800,timezone.utc).isoformat()
    c.execute("INSERT INTO draws(id,source_time,draw_utc,numbers,a,b,c,total,size,parity,first_seen,source,raw_hash) VALUES(?,?,?,'[]',1,2,3,6,'小','双','t','t','t')",(100+i,t,t))
    c.execute("INSERT INTO strategy_signals(strategy,version,target_draw,cutoff_draw,created,direction,stake,status,hit,pnl,outcome,settled,odds_json,evidence,source_engine,source_id) VALUES('size','v',?,?,?,'小',10000,'settled',1,10000,'win',?,'{}','{}','fixture',?)",(100+i,99+i,t,t,i))
   t=datetime.fromtimestamp(NOW+1800,timezone.utc).isoformat();c.execute("INSERT INTO draws(id,source_time,draw_utc,numbers,a,b,c,total,size,parity,first_seen,source,raw_hash) VALUES(999,?,?,'[]',1,2,3,6,'小','双','t','t','t')",(t,t))
   c.execute("INSERT INTO strategy_signals(strategy,version,target_draw,cutoff_draw,created,direction,status,pnl,odds_json,evidence,source_engine,source_id) VALUES('size','v',999,998,?,'小','settled',-99999999,'{}','{}','fixture',999)",(t,))
  result=self.s.trend_risk.source('size',DEFAULT,NOW);self.assertEqual(result['status'],'safe');self.assertEqual(result['source_orders'],30);self.assertEqual(result['cutoff_draw'],129);self.assertEqual(result['candles'][-1]['close'],3000)
  with self.s.db.tx() as c:
   for i in range(30):
    t=datetime.fromtimestamp(NOW-(30-i)*3600+1800,timezone.utc).isoformat()
    c.execute("INSERT INTO trend_orders(strategy,target_draw,cutoff_draw,created,direction,stake,status,pnl,hit,outcome,settled,cycle,step) VALUES('size',?,?,?,'大',20000,'settled',-20000,0,'loss',?,1,1)",(100+i,99+i,t,t))
  selected=self.s.trend_risk.source('size',{**DEFAULT,'source':'strategy'},NOW)
  self.assertEqual(selected['status'],'risk');self.assertEqual(selected['candles'][-1]['close'],-6000)


class TrendCaptureTests(unittest.TestCase):
 def setUp(self):
  from test_robot_accounts import AccountTests
  self.fixture=AccountTests();self.fixture.setUp();self.s=self.fixture.s;self.r=self.fixture.r
  self.aid=AccountTests.create(self.fixture);detail=self.r.account_detail(self.aid);self.bot=detail['bots'][0];self.sid=next(r['id'] for r in detail['subscriptions'] if r['bot_id']==self.bot['id'])
 def tearDown(self):self.fixture.tearDown()
 def rule(self,mode):
  with self.s.db.tx() as c:
   c.execute('INSERT INTO trend_risk_rules VALUES(?,?,1,?)',(self.sid,json.dumps({**DEFAULT,'mode':mode}),datetime.now(timezone.utc).isoformat()))
   c.execute("INSERT INTO trend_risk_state VALUES(?,1,?,1,0,2,0,?,'risk','fixture risk','{}',?)",(self.sid,int(time.time()//3600)*3600,int(time.time()//3600)*3600,datetime.now(timezone.utc).isoformat()))
 def test_capture_blocks_only_automatic_mode_and_keeps_strategy_settings(self):
  before=copy.deepcopy(self.s.cfg);self.rule('auto');self.fixture.order()
  self.assertEqual(self.r.capture(),0);self.assertIn('趋势风控',self.s.db.query('SELECT reason FROM robot_capture_skips')[0]['reason']);self.assertEqual(before,self.s.cfg)
  with self.s.db.tx() as c:c.execute('UPDATE trend_risk_rules SET settings=?',(json.dumps(DEFAULT),))
  self.fixture.order(target=1000);self.assertGreater(self.r.capture(),0)
 def test_delayed_send_rechecks_trend_before_transport(self):
  self.fixture.order();self.assertGreater(self.r.capture(),0);self.fixture.ready();self.rule('auto')
  now=datetime.now(timezone.utc);previous=now-timedelta(seconds=50);due=previous+timedelta(seconds=210)
  self.s.db.import_records([{'id':998,'time':previous.isoformat(),'numbers':list(range(1,21))}],'test')
  with self.s.db.tx() as c:c.execute("INSERT OR REPLACE INTO runtime_state VALUES('draw_timer',?,?)",(json.dumps({'next_draw_id':999,'next_draw_at':due.isoformat()}),now.isoformat()))
  with patch('pc28.maintenance.live_guard',return_value=''),patch('pc28.robot_signals.urllib.request.build_opener') as opener:
   self.assertTrue(self.r.deliver_one());opener.assert_not_called()
  row=self.s.db.query('SELECT status,last_error FROM robot_outbox')[0];self.assertEqual(row['status'],'cancelled');self.assertIn('趋势风控',row['last_error'])
  self.assertEqual(self.s.db.query('SELECT COUNT(*) n FROM robot_risk_holds')[0]['n'],0)

if __name__=='__main__':unittest.main()
