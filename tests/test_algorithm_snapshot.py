import json,tempfile,threading,time,unittest
from pathlib import Path
from unittest.mock import patch
from pc28.db import DB
from pc28.custom_strategy import initialize
from pc28.algorithm_hall import AlgorithmHall,BY_ID

class SnapshotTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'data');self.db=DB(Path(self.tmp.name)/'snapshot.sqlite');initialize(self.db)
  with self.db.tx() as c:
   c.executemany("INSERT INTO draws(id,source_time,draw_utc,numbers,a,b,c,total,size,parity,first_seen,source,raw_hash) VALUES(?,?,?,'[]',1,2,3,6,?,?,'fixture','fixture','fixture')",[(100+i,f'2026-01-01T00:{i:02}:00+00:00',f'2026-01-01T00:{i:02}:00+00:00','大' if i%3 else '小','单' if i%2 else '双') for i in range(60)])
  self.h=AlgorithmHall(self.db)
 def tearDown(self):self.tmp.cleanup()
 def ready(self):self.h.snapshot()
 def test_cold_page_never_computes_and_warm_page_is_paged(self):
  with patch.object(self.h,'_calculate_snapshot',side_effect=AssertionError('HTTP must not compute')):
   self.assertFalse(self.h.page(1,{'page':'1'})['ready'])
  self.ready();full=self.h.listing(1,admin=True)
  with patch.object(self.h,'_calculate_snapshot',side_effect=AssertionError('cache must not compute')):
   first=self.h.page(1,{'page':'1','page_size':'10'},admin=True)
  expected=sorted(full['algorithms'],key=lambda r:(r['metrics']['20']['simulation']['return_rate'] is None,-(r['metrics']['20']['simulation']['return_rate'] or 0),r['id']))
  self.assertEqual(first['total'],82);self.assertEqual(len(first['algorithms']),10)
  self.assertEqual([r['id'] for r in first['algorithms']],[r['id'] for r in expected[:10]])
  self.assertEqual(first['algorithms'][0]['metrics']['20'],{k:v for k,v in expected[0]['metrics']['20'].items() if k!='hits'})
  self.assertEqual(list(first['algorithms'][0]['metrics']),['20'])
  second=self.h.page(1,{'page':'2','page_size':'10'},admin=True)
  self.assertTrue(set(r['id'] for r in first['algorithms']).isdisjoint(r['id'] for r in second['algorithms']))
 def test_version_favorites_visibility_and_user_isolation(self):
  self.ready();q={'page':'1','favorites':'1'};empty=self.h.page(1,q)
  self.assertTrue(self.h.page(1,{**q,'known':empty['version']})['unchanged'])
  self.h.favorite(1,{'id':'size.follow.1','favorite':True})
  one=self.h.page(1,{**q,'known':empty['version']});self.assertEqual(one['total'],1)
  self.assertEqual(self.h.page(2,q)['total'],0)
  with self.db.tx() as c:c.execute("INSERT INTO public_visibility VALUES('algorithm','size.follow.1',0)")
  self.assertEqual(self.h.page(1,q)['total'],0);self.assertEqual(self.h.page(1,q,admin=True)['total'],1)
 def test_source_correction_updates_and_stale_page_does_not_wait(self):
  self.ready();version=self.h.snapshots.bundle['version']
  with self.db.tx() as c:c.execute("UPDATE draws SET size='小' WHERE id=159")
  with self.h.snapshots.lock,patch.object(self.h,'_calculate_snapshot',side_effect=AssertionError('page must not rebuild')):
   stale=self.h.page(1,{'page':'1'});self.assertTrue(stale['ready']);self.assertTrue(stale['updating'])
  self.ready();self.assertNotEqual(version,self.h.snapshots.bundle['version']);self.assertFalse(self.h.page(1,{'page':'1'})['updating'])
 def test_durable_restore_and_history_detail(self):
  self.ready();old=self.h.detail('size.follow.1');restored=AlgorithmHall(self.db)
  with patch.object(restored,'_calculate_snapshot',side_effect=AssertionError('restart must restore')):
   self.assertTrue(restored.page(1,{'page':'1'})['ready']);new=restored.detail('size.follow.1')
  self.assertEqual(old,new)
 def test_future_eligibility_and_unrelated_writes(self):
  from datetime import datetime,timezone,timedelta
  future=datetime.now(timezone.utc)+timedelta(minutes=1)
  with self.db.tx() as c:c.execute("UPDATE draws SET draw_utc=? WHERE id=159",(future.isoformat(),))
  self.ready();self.assertEqual(self.h.page(1,{'page':'1'})['latest_id'],158)
  revision=self.h.snapshots.revision()['source_revision']
  with self.db.tx() as c:c.execute("INSERT INTO jobs(kind) VALUES('unrelated')")
  self.assertEqual(self.h.snapshots.revision()['source_revision'],revision)
  with patch('pc28.algorithm_snapshot.time.time',return_value=future.timestamp()+1):
   self.assertTrue(self.h.page(1,{'page':'1'})['updating'])
 def test_real_settlement_does_not_invalidate_independent_paper_income(self):
  self.ready();key='size.follow.1';before=self.h.detail(key)
  with self.db.tx() as c:c.execute("INSERT INTO custom_orders(strategy,target_draw,cutoff_draw,created,direction,stake,status,pnl,hit,outcome,settled,strategy_json) VALUES('real',159,158,'2026-01-01','大',999999,'settled',99999999,1,'win','2026-01-01','{}')")
  self.assertFalse(self.h.page(1,{'page':'1'})['updating']);self.assertEqual(before,self.h.detail(key))
 def test_refresh_does_not_publish_a_mixed_revision(self):
  self.ready();original=self.h._calculate_snapshot;calls=0
  with self.db.tx() as c:c.execute("UPDATE draws SET size='大' WHERE id=159")
  def calculate():
   nonlocal calls
   calls+=1;result=original()
   if calls==1:
    with self.db.tx() as c:c.execute("UPDATE draws SET size='小' WHERE id=159")
   return result
  with patch.object(self.h,'_calculate_snapshot',side_effect=calculate):self.ready()
  self.assertEqual(calls,2);self.assertEqual(self.h.snapshots.bundle['draws'][-1]['size'],'小')
 def test_page_during_background_rebuild_is_nonblocking(self):
  self.ready();entered=threading.Event();release=threading.Event();stop=threading.Event();original=self.h._calculate_snapshot
  def slow():
   entered.set();release.wait(5);return original()
  with self.db.tx() as c:c.execute("UPDATE draws SET size='小' WHERE id=159")
  with patch.object(self.h,'_calculate_snapshot',side_effect=slow):
   self.h.snapshots.start(stop)
   try:
    self.assertTrue(entered.wait(2));started=time.perf_counter();r=self.h.page(1,{'page':'1'})
    self.assertLess(time.perf_counter()-started,0.2);self.assertTrue(r['ready']);self.assertTrue(r['updating'])
   finally:
    release.set();stop.set();self.h.snapshots.wake.set();self.h.snapshots.thread.join(5)
 def test_long_lookback_has_full_5000_period_warmup(self):
  from datetime import datetime,timedelta,timezone
  start=datetime(2025,1,1,tzinfo=timezone.utc)
  with self.db.tx() as c:
   c.execute('DELETE FROM draws')
   c.executemany("INSERT INTO draws(id,source_time,draw_utc,numbers,a,b,c,total,size,parity,first_seen,source,raw_hash) VALUES(?,?,?,'[]',1,2,3,6,'小','双','fixture','fixture','fixture')",[(10000+i,(start+timedelta(seconds=i*210)).isoformat(),(start+timedelta(seconds=i*210)).isoformat()) for i in range(5205)])
  self.ready();detail=self.h.detail('size.majority.120')
  self.assertEqual(detail['metrics']['5000']['count'],5000)
 def test_revenue_leaders_are_filtered_and_independent_of_pagination(self):
  self.ready();allrows=self.h.listing(1,admin=True)['algorithms']
  def ranked(rows):return sorted([r for r in rows if r['metrics']['20']['simulation']['return_rate'] is not None],key=lambda r:(-r['metrics']['20']['simulation']['return_rate'],r['id']))[:3]
  first=self.h.page(1,{'page':'1'});second=self.h.page(1,{'page':'2'})
  self.assertEqual([r['id'] for r in first['leaders']],[r['id'] for r in ranked(allrows)])
  self.assertEqual(first['leaders'],second['leaders'])
  sized=self.h.page(1,{'page':'1','axis':'size','sort':'name'})
  self.assertEqual([r['id'] for r in sized['leaders']],[r['id'] for r in ranked([r for r in allrows if r['axis']=='size'])])
  searched=self.h.page(1,{'page':'1','search':'上期跟随'})
  self.assertEqual([r['id'] for r in searched['leaders']],[r['id'] for r in ranked([r for r in allrows if '上期跟随' in r['name']])])
 def test_validation(self):
  for q in ({'window':'99'},{'axis':'bad'},{'sort':'bad'},{'page_size':'1000'}):
   with self.assertRaises(ValueError):self.h.page(1,{'page':'1',**q})
if __name__=='__main__':unittest.main()
