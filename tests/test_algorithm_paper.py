import copy,json,random,tempfile,unittest
from contextlib import closing
from datetime import datetime,timezone,timedelta
from pathlib import Path
from pc28.db import DB
from pc28.algorithm_hall import AlgorithmHall,DEFINITIONS,DIRECTION_LOOKBACKS,predict
from pc28.algorithm_paper import BASE,INITIAL,RULE_VERSION
from pc28.config import DEFAULT
from pc28.model import settlement

class PaperTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'data');self.db=DB(Path(self.tmp.name)/'main.sqlite');self.odds=copy.deepcopy(DEFAULT['odds']);self.h=AlgorithmHall(self.db,self.odds)
 def tearDown(self):self.tmp.cleanup()
 def add(self,totals,start=100):
  stamp=datetime(2020,1,1,tzinfo=timezone.utc)
  with self.db.tx() as c:
   for i,total in enumerate(totals):
    a=min(9,total);b=min(9,total-a);third=total-a-b;t=(stamp+timedelta(seconds=(start+i)*210)).isoformat()
    c.execute("INSERT INTO draws(id,source_time,draw_utc,numbers,a,b,c,total,size,parity,first_seen,source,raw_hash) VALUES(?,?,?,'[]',?,?,?,?,?,?,'fixture','fixture','fixture')",(start+i,t,t,a,b,third,total,'大' if total>=14 else '小','单' if total%2 else '双'))
 def test_catalog_exact_periods_and_reversal(self):
  self.assertEqual(len(DEFINITIONS),82)
  for axis in ('size','parity'):
   for kind in ('majority','minority','transition'):
    self.assertEqual({d['lookback']-(kind=='transition') for d in DEFINITIONS if d['axis']==axis and d['kind']==kind},set(DIRECTION_LOOKBACKS))
 def test_amount_loss_cap_win_refund_and_balances(self):
  self.add([6,18]*7);r=self.h.paper.sync();self.assertEqual(r['inserted'],14*82)
  rows=self.h.paper.query("SELECT * FROM paper_orders WHERE algorithm_id='size.follow.1' AND stake>0 ORDER BY target_draw")
  self.assertEqual([v['stake'] for v in rows[:6]],[10000,12500,15000,17500,20000,10000])
  for row in rows:self.assertEqual(row['balance_after'],row['balance_before']+row['pnl'])
  with self.db.tx() as c:c.execute('DELETE FROM draws')
  self.add([6,18,6,0,6]);self.h.paper.sync()
  rows=self.h.paper.query("SELECT * FROM paper_orders WHERE algorithm_id='size.follow.1' AND stake>0 ORDER BY target_draw")
  self.assertEqual([r['stake'] for r in rows],[10000,12500,15000,15000])
  self.assertEqual(rows[2]['outcome'],'refund');self.assertEqual(rows[2]['next_stake'],15000);self.assertEqual(rows[3]['next_stake'],10000)
 def test_all_saved_orders_match_independent_reference(self):
  rng=random.Random(8);self.add([rng.randrange(28) for _ in range(180)]);self.h.paper.sync()
  source=self.db.query('SELECT * FROM draws ORDER BY id')
  for d in DEFINITIONS:
   saved=self.h.paper.query('SELECT * FROM paper_orders WHERE algorithm_id=? ORDER BY target_draw',(d['id'],));stake=BASE;balance=INITIAL
   for i,r in enumerate(saved):
    direction=predict(source[:i],d)[0]
    self.assertEqual(r['direction'],direction,(d['id'],r['target_draw']))
    if direction is None:self.assertEqual(r['stake'],0);continue
    hit=direction==source[i][d['axis']];paid=settlement(stake,direction,source[i]['total'],hit,self.odds)
    self.assertEqual((r['stake'],r['pnl'],r['outcome']),(stake,paid['pnl'],paid['outcome']))
    self.assertEqual(r['balance_before'],balance);balance+=paid['pnl'];self.assertEqual(r['balance_after'],balance)
    stake=BASE if paid['outcome']=='win' or (paid['outcome']=='loss' and stake>=20000) else stake+2500 if paid['outcome']=='loss' else stake
 def test_window_is_slice_and_patterns_are_window_specific(self):
  self.add([6,18]*85);self.h.snapshot();cutoff=269;allrows=self.h.paper.query("SELECT * FROM paper_orders WHERE algorithm_id='size.follow.1' ORDER BY target_draw")
  m=self.h.paper.metrics('size.follow.1',cutoff,(20,100))
  for window in (20,100):
   expected=[r for r in allrows if r['target_draw']>cutoff-window];sim=m[str(window)]['simulation']
   self.assertEqual(sim['net'],sum(r['pnl'] for r in expected));self.assertEqual(sim['stake'],sum(r['stake'] for r in expected));self.assertEqual(sim['start_balance'],expected[0]['balance_before']);self.assertEqual(len(m[str(window)]['hits']),min(100,window))
  self.assertNotEqual(m['20']['simulation']['start_balance'],INITIAL)
  page=self.h.page(1,{'page':'1','window':'100'},admin=True);self.assertEqual(page['total'],82)
  for r in page['algorithms']:self.assertNotIn('actual',r['metrics']['100']);self.assertNotIn('hits',r['metrics']['100'])
  self.assertEqual(len(page['leaders'][0]['metrics']['100']['hits']),100)
 def test_missing_and_corrupted_orders_are_repaired_idempotently(self):
  self.add([6,18]*40);self.h.paper.sync();before=self.h.paper.query("SELECT * FROM paper_orders WHERE algorithm_id='size.follow.1' ORDER BY target_draw")
  with closing(self.h.paper.connect()) as c:c.execute("DELETE FROM paper_orders WHERE algorithm_id='size.follow.1' AND target_draw=140");c.commit()
  report=self.h.paper.sync();self.assertEqual(report['inserted'],1);self.assertEqual(before,self.h.paper.query("SELECT * FROM paper_orders WHERE algorithm_id='size.follow.1' ORDER BY target_draw"))
  with closing(self.h.paper.connect()) as c:c.execute("UPDATE paper_orders SET stake=123 WHERE algorithm_id='size.follow.1' AND target_draw=140");c.commit()
  report=self.h.paper.sync(force_verify=True);self.assertGreater(report['repaired'],0)
  self.assertEqual(before,self.h.paper.query("SELECT * FROM paper_orders WHERE algorithm_id='size.follow.1' ORDER BY target_draw"));report=self.h.paper.sync();self.assertEqual(report['inserted']+report['repaired'],0)
 def test_backfill_and_correction_match_clean_replay(self):
  self.add([6,18]*40)
  with self.db.tx() as c:c.execute('DELETE FROM draws WHERE id=135')
  self.h.paper.sync();self.add([18],135);self.h.paper.sync()
  before=self.h.paper.query("SELECT * FROM paper_orders WHERE algorithm_id='size.transition.20' ORDER BY target_draw")
  self.h.paper.sync(force_verify=True);self.assertEqual(before,self.h.paper.query("SELECT * FROM paper_orders WHERE algorithm_id='size.transition.20' ORDER BY target_draw"))
  with self.db.tx() as c:c.execute("UPDATE draws SET total=6,size='小',parity='双',a=6,b=0,c=0 WHERE id=135")
  report=self.h.paper.sync();self.assertGreater(report['repaired'],0)
  before=self.h.paper.query("SELECT * FROM paper_orders WHERE algorithm_id='size.follow.1' ORDER BY target_draw")
  self.h.paper.sync(force_verify=True);self.assertEqual(before,self.h.paper.query("SELECT * FROM paper_orders WHERE algorithm_id='size.follow.1' ORDER BY target_draw"))
 def test_invalidated_draw_removes_orphan_and_replays_amounts(self):
  self.add([6,18]*40);self.h.paper.sync()
  with self.db.tx() as c:c.execute("UPDATE draws SET verification='unverified' WHERE id=140")
  self.h.paper.sync();self.assertEqual(self.h.paper.query('SELECT COUNT(*) n FROM paper_orders WHERE target_draw=140')[0]['n'],0)
  before=self.h.paper.query("SELECT * FROM paper_orders WHERE algorithm_id='size.follow.1' ORDER BY target_draw")
  self.h.paper.sync(force_verify=True);self.assertEqual(before,self.h.paper.query("SELECT * FROM paper_orders WHERE algorithm_id='size.follow.1' ORDER BY target_draw"))
 def test_next_period_pending_and_saved_odds(self):
  self.add([6]*5);self.h.paper.sync();pending=self.h.paper.query("SELECT * FROM pending WHERE algorithm_id='size.follow.1'")[0];self.assertEqual(pending['target_draw'],105)
  self.odds['normal']=300;self.h.paper.sync();self.add([6],105);self.h.paper.sync()
  r=self.h.paper.query("SELECT * FROM paper_orders WHERE algorithm_id='size.follow.1' AND target_draw=105")[0]
  self.assertEqual(r['direction'],pending['direction']);self.assertEqual(r['stake'],pending['stake']);self.assertEqual(r['pnl'],10000)
 def test_real_strategy_orders_do_not_affect_simulation(self):
  from pc28.custom_strategy import initialize
  initialize(self.db);self.add([6,18]*40);self.h.snapshot();before=self.h.page(1,{'page':'1'},admin=True)
  with self.db.tx() as c:
   c.execute("INSERT INTO custom_orders(strategy,target_draw,cutoff_draw,created,direction,stake,status,pnl,hit,outcome,settled,strategy_json) VALUES('unrelated',179,178,'2020-01-01','大',999999,'settled',88888888,1,'win','2020-01-01','{}')")
  after=self.h.page(1,{'page':'1'},admin=True)
  self.assertEqual(before['algorithms'],after['algorithms']);self.assertEqual(before['leaders'],after['leaders']);self.assertFalse(after['updating'])
 def test_unverified_result_does_not_discard_pending(self):
  self.add([6]*5);self.h.paper.sync();pending=self.h.paper.query("SELECT * FROM pending WHERE algorithm_id='size.follow.1'")[0]
  self.add([6],105)
  with self.db.tx() as c:c.execute("UPDATE draws SET verification='unverified' WHERE id=105")
  self.h.paper.sync();self.assertEqual(pending,self.h.paper.query("SELECT * FROM pending WHERE algorithm_id='size.follow.1'")[0])
 def test_ledger_detail_income_and_paged_patterns(self):
  self.add([6,18]*110);self.h.snapshot();d=self.h.detail('size.follow.1',window=100,source='orders');self.assertTrue(d['simulation_only']);self.assertEqual(d['total'],100)
  a=self.h.detail('size.follow.1',1,100,window=500,source='pattern',cutoff=300);b=self.h.detail('size.follow.1',2,100,window=500,source='pattern',cutoff=300)
  self.assertEqual(len(a['records']),100);self.assertEqual(len(b['records']),100);self.assertLess(b['records'][0]['target_draw'],a['records'][-1]['target_draw']);self.assertLessEqual(a['records'][0]['target_draw'],300)
  result=self.h.backtest('size.follow.1',100,self.odds,'1o');self.assertEqual(result['net'],sum(r['pnl'] for r in self.h.paper.records('size.follow.1',319,100,1,100)['records']))
  candles=self.h.income('size.follow.1','1o');self.assertEqual(candles['net'],sum(r['pnl'] for r in self.h.paper.query("SELECT pnl FROM paper_orders WHERE algorithm_id='size.follow.1'"))/100)
if __name__=='__main__':unittest.main()
