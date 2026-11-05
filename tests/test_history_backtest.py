import copy,json,tempfile,unittest
from pathlib import Path
from datetime import datetime,timedelta,timezone
from pc28.config import DEFAULT
from pc28.service import Service
from pc28.history_backtest import prepare,execute,replay,source_window,result_page,three_months_before,metadata,income_candles
from pc28 import trend_strategy
from pc28.engine import Prepared,replay as replay_fixed
from pc28.follow_strategy import defaults
from pc28.custom_strategy import save
class HistoryBacktestTests(unittest.TestCase):
 def test_candles_use_every_order_and_carry_empty_periods(self):
  orders=[{'beijing_time':'2026-01-01 00:10:00','pnl':10000},{'beijing_time':'2026-01-01 00:20:00','pnl':-20000},{'beijing_time':'2026-01-01 00:30:00','pnl':5000},{'beijing_time':'2026-01-01 02:00:00','pnl':2500}]
  bars=income_candles(orders,'1h','2026-01-01T00:00:00+08:00','2026-01-01T03:00:00+08:00')
  self.assertEqual([bars[0][k] for k in ('open','high','low','close')],[0,100,-100,-50]);self.assertEqual(bars[1]['open'],-50);self.assertEqual(bars[1]['close'],-50);self.assertEqual(bars[1]['orders'],0);self.assertEqual(bars[-1]['close'],-25);self.assertEqual(sum(b['orders'] for b in bars),4)
  four=income_candles(orders,'4h');self.assertEqual(len(four),1);self.assertEqual([four[0][k] for k in ('open','high','low','close')],[0,100,-100,-25])
 def test_candle_day_boundary_is_beijing(self):
  orders=[{'beijing_time':'2026-01-01T15:59:00+00:00','pnl':100},{'beijing_time':'2026-01-01T16:00:00+00:00','pnl':-300}]
  bars=income_candles(orders,'1d');self.assertEqual(len(bars),2);self.assertEqual(bars[1]['open'],1);self.assertEqual(bars[1]['close'],-2);self.assertTrue(bars[1]['time'].startswith('2026-01-02T00:00'))
  with self.assertRaises(ValueError):income_candles(orders,'5m')
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent/'data');self.root=Path(self.tmp.name);cfg=copy.deepcopy(DEFAULT);cfg['collector']['enabled']=False;cfg['collector']['maintenance_guard_enabled']=False;cfg['simulation'].update(max_stake=20000,max_drawdown=1000000,daily_loss_limit=1000000);cfg['simulation']['fixed'].update(direction='小',max_bets=2,max_losses=3);cfg['simulation']['round_restart']='immediate';(self.root/'config.json').write_text(json.dumps(cfg));self.s=Service(self.root);self.now=datetime.now(timezone.utc).replace(microsecond=0);self.end=self.now-timedelta(seconds=60)
  self.s.db.import_records([{'id':100+i,'time':(self.end-timedelta(seconds=210*(89-i))).isoformat(),'numbers':list(range(1,21))} for i in range(90)],'test')
  self.s.db.import_records([{'id':99,'time':(self.now-timedelta(days=150)).isoformat(),'numbers':list(range(1,21))}],'test')
 def tearDown(self):self.tmp.cleanup()
 def selected(self,key='size'):
  p=prepare(self.s,{'strategy_id':key});rows,coverage=source_window(self.s.db.rows(),set(),p['as_of']);return p,rows,coverage
 def test_range_is_calendar_three_months_and_warmup_inside(self):
  _,rows,coverage=self.selected();self.assertEqual(rows[0]['id'],100);self.assertEqual(coverage['draws'],90);self.assertEqual(coverage['last_id'],189)
  self.assertEqual(three_months_before(datetime(2026,5,31,tzinfo=timezone.utc)).date().isoformat(),'2026-02-28')
  self.assertEqual(three_months_before(datetime(2024,5,31,tzinfo=timezone.utc)).date().isoformat(),'2024-02-29')
  with self.assertRaises(ValueError):prepare(self.s,{'strategy_id':'size','start':'2000-01-01'})
 def test_custom_count_window_and_fixed_summary_not_overwritten(self):
  original=execute(self.s,prepare(self.s,{'strategy_id':'size'}));saved=self.s.db.query('SELECT * FROM strategy_backtest_summary')
  p=prepare(self.s,{'strategy_id':'size','range_mode':'count','periods':20});r=execute(self.s,p)
  self.assertEqual(r['coverage']['draws'],20);self.assertEqual(r['coverage']['first_id'],170);self.assertEqual(r['coverage']['last_id'],189);self.assertEqual(saved,self.s.db.query('SELECT * FROM strategy_backtest_summary'));self.assertIn('selected',r['window_returns'])
  for n in (19,100001,20.5,True):
   with self.assertRaises(ValueError):prepare(self.s,{'strategy_id':'size','range_mode':'count','periods':n})
  with self.assertRaises(ValueError):execute(self.s,prepare(self.s,{'strategy_id':'size','range_mode':'count','periods':100000}))
 def test_custom_dates_use_beijing_and_enforce_limits(self):
  raw=self.s.db.rows();start=datetime.fromisoformat(raw[-30]['draw_utc']).astimezone(__import__('zoneinfo').ZoneInfo('Asia/Shanghai')).replace(tzinfo=None).isoformat();end=datetime.fromisoformat(raw[-1]['draw_utc']).astimezone(__import__('zoneinfo').ZoneInfo('Asia/Shanghai')).replace(tzinfo=None).isoformat()
  r=execute(self.s,prepare(self.s,{'strategy_id':'size','range_mode':'dates','start':start,'end':end}));self.assertEqual(r['coverage']['draws'],30);self.assertEqual(r['coverage']['first_id'],160)
  with self.assertRaises(ValueError):prepare(self.s,{'strategy_id':'size','range_mode':'dates','start':'2023-12-31T23:59','end':end})
  with self.assertRaises(ValueError):prepare(self.s,{'strategy_id':'size','range_mode':'dates','start':end,'end':start})
  with self.assertRaises(ValueError):execute(self.s,prepare(self.s,{'strategy_id':'size','range_mode':'dates','start':end,'end':self.now.isoformat()}))
 def test_repeated_hour_repair_requires_both_boundaries_and_preserves_raw(self):
  raw=[];base=datetime(2026,11,1,8,57,30,tzinfo=timezone.utc)
  for i in range(13):
   actual=base+timedelta(seconds=210*i);stored=actual-timedelta(hours=1) if 1<=i<=10 else actual
   raw.append({'id':100+i,'draw_utc':stored.isoformat(),'source_time':actual.astimezone(__import__('zoneinfo').ZoneInfo('America/Los_Angeles')).replace(tzinfo=None).isoformat(),'source':'https://www.playnow.com/keno.zip','a':1,'b':2,'c':3,'total':6,'size':'小','parity':'双'})
  original=copy.deepcopy(raw);rows,c=source_window(raw,set(),'2026-11-02T00:00:00+00:00');self.assertEqual(len(rows),13);self.assertEqual(len(c['time_repairs']),10);self.assertEqual(c['missing_periods'],0);self.assertEqual(raw,original)
  raw[11]['draw_utc']=(base+timedelta(seconds=210*11+50)).isoformat();rows,c=source_window(raw,set(),'2026-11-02T00:00:00+00:00');self.assertEqual(c['time_repairs'],[]);self.assertEqual(c['excluded']['窗口内时间逆序'],10)
 def test_all_draws_accounted_for_when_drawdown_blocks_orders(self):
  p,rows,_=self.selected('legacy');p['snapshot']['settings'].update(max_drawdown=1)
  result,orders=replay(rows,p['snapshot']);self.assertEqual(len(rows),result['bets']+result['periods_skipped']);self.assertIn('达到最大回撤限制',result['skips'])
 def test_builtin_majority_matches_existing_replay(self):
  self.s.cfg['trend_simulation']['strategies']={'size':{**trend_strategy.settings(self.s.cfg,'size'),'win_limit':2,'pause_draws':3}}
  p,rows,_=self.selected();a,orders=replay(rows,p['snapshot']);old=trend_strategy.backtest(rows,self.s.cfg,with_risk=True)
  expected=[r for r in old['orders'] if r['strategy']=='size'];self.assertEqual([(r['target_draw'],r['stake'],r['pnl']) for r in orders],[(r['target_draw'],r['stake'],r['pnl']) for r in expected]);self.assertEqual(a['net'],old['strategies']['size']['net'])
 def test_reversal_matches_fixed_replay(self):
  p,rows,_=self.selected('legacy');a,orders=replay(rows,p['snapshot']);cfg=self.s.cfg['simulation'];expected=replay_fixed(Prepared(rows),__import__('pc28.model',fromlist=['Strategy']).Strategy(**cfg['fixed']),self.s.cfg['odds'],trace=True,restart_policy='immediate')
  self.assertEqual([(r['target_draw'],r['stake']/100,r['pnl']/100) for r in orders],[(r['draw'],r['stake'],r['pnl']) for r in expected['orders']]);self.assertEqual(a['net'],expected['net'])
 def test_hourly_and_custom_majority_saved_types(self):
  for kind in ('hourly','majority'):
   value=defaults(self.s.cfg,kind);key=save(self.s,{'name':kind,'settings':value})['id'];p,rows,_=self.selected(key);result,orders=replay(rows,p['snapshot']);self.assertGreater(result['bets'],0)
   self.assertTrue(all(r['direction']==('小' if kind=='hourly' else '大') for r in orders))
 def test_result_persistence_pagination_exports_and_live_isolation(self):
  before={t:self.s.db.query('SELECT * FROM '+t) for t in ('accounts','orders','ledger','trend_accounts','trend_orders','trend_ledger','custom_strategies','custom_orders','custom_ledger')};config=(self.root/'config.json').read_bytes();p,_,_=self.selected();r=execute(self.s,p)
  self.assertEqual(config,(self.root/'config.json').read_bytes())
  for t,values in before.items():self.assertEqual(self.s.db.query('SELECT * FROM '+t),values)
  self.assertTrue((self.root/'data'/r['file'].lstrip('/')).exists());self.assertTrue((self.root/'data'/r['summary_file'].lstrip('/')).exists())
  for size in (10,20,50,100):
   first=result_page(self.s.db,r['run_id'],1,size);last=result_page(self.s.db,r['run_id'],999,size);self.assertEqual(first['total'],r['bets']);self.assertLessEqual(len(first['orders']),size);self.assertEqual(last['page'],last['pages'])
  self.assertEqual(metadata(self.s)['runs'][0]['id'],r['run_id'])
 def test_submit_snapshot_and_cutoff_are_immutable(self):
  p,_,_=self.selected();self.s.cfg['trend_simulation']['strategies']={'size':{**trend_strategy.settings(self.s.cfg,'size'),'base_cents':25000}}
  self.s.db.import_records([{'id':190,'time':self.now.isoformat(),'numbers':list(range(1,21))}],'later');r=execute(self.s,p);self.assertEqual(r['coverage']['last_id'],189);self.assertEqual(r['strategy']['settings']['base_cents'],50000)
 def test_conflicts_and_future_time_excluded_and_gaps_not_bridged(self):
  p,_,_=self.selected();raw=self.s.db.rows();rows,coverage=source_window(raw,{150},p['as_of']);r,orders=replay(rows,p['snapshot']);self.assertEqual(coverage['missing_periods'],1);self.assertFalse(any(150<=o['target_draw']<=170 for o in orders))
  future={**raw[-1],'id':999,'draw_utc':(self.now+timedelta(days=1)).isoformat()};rows,c=source_window([*raw,future],set(),p['as_of']);self.assertEqual(rows[-1]['id'],189);self.assertEqual(c['excluded']['未来时间'],1)
 def test_no_target_result_leak_and_no_future_monthly_candidates(self):
  p,rows,_=self.selected();_,a=replay(rows,p['snapshot']);changed=copy.deepcopy(rows);changed[21]['size']='小';_,b=replay(changed,p['snapshot']);self.assertEqual([o for o in a if o['target_draw']<121],[o for o in b if o['target_draw']<121]);self.assertEqual(next(o for o in a if o['target_draw']==121)['direction'],next(o for o in b if o['target_draw']==121)['direction'])
  p,rows,_=self.selected('legacy');p['snapshot']['settings']['mode']='monthly';r,orders=replay(rows,p['snapshot']);self.assertFalse(orders);self.assertIn('当时没有可用的月度候选快照',r['skips'])
if __name__=='__main__':unittest.main()
