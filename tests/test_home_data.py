import unittest,tempfile,copy
from pathlib import Path
from types import SimpleNamespace
from pc28.db import DB
from pc28.config import DEFAULT
from pc28.tools import Tools,enrich,BJ,daily_summary,period_summaries
from pc28.home_data import HomeData,COLUMNS
from zoneinfo import ZoneInfo
class HomeDataTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(dir='data');self.db=DB(Path(self.tmp.name)/'test.sqlite3');self.s=SimpleNamespace(db=self.db,cfg=copy.deepcopy(DEFAULT));self.tools=Tools(self.s)
  for i,stamp in enumerate(['2026-11-08T15:59:59+00:00','2026-11-08T16:00:00+00:00','2026-11-08T19:59:59+00:00','2026-11-08T20:00:00+00:00','2026-11-09T16:00:00+00:00'],100):self.db.import_records([{'id':i,'time':stamp,'numbers':list(range(1,21))}],'test')
 def tearDown(self):
  if hasattr(self.tools,'home_data'):
   import time
   while self.tools.home_data.running:time.sleep(.01)
  self.tmp.cleanup()
 def baseline(self):
  rows=[enrich(r,ZoneInfo('America/Vancouver')) for r in self.db.query('SELECT '+COLUMNS+' FROM draws ORDER BY id')];t=Tools(None);t.daily=daily_summary(rows);t.snapshot=lambda:rows;return t
 def test_records_match_legacy_window_search_and_date(self):
  for q in ({},{'limit':'2'},{'limit':'0'},{'q':'15'},{'q':'100'},{'date':'2026-11-09'},{'page':'999'}):
   query={'view':'home',**q};old=self.baseline().run(query);new=self.tools.run({**query,'part':'records'})
   for key in ('rows','coverage','total','page','pages','last_updated_draw'):self.assertEqual(new[key],old[key],key)
  self.assertFalse(hasattr(self.tools.home_data,'rows'))
 def test_summary_matches_legacy_and_persists(self):
  old=self.baseline().run({'view':'home'});new=self.tools.run({'view':'home'})
  self.assertEqual(new['daily'],old['daily'])
  for kind in ('hour','four_hour','week'):self.assertEqual(new['summaries'][kind]['rows'],old['summaries'][kind]['rows'])
  second=HomeData(self.s);second.initialize();self.assertEqual(self.db.query('SELECT COUNT(*) n FROM home_cache_dirty')[0]['n'],0)
 def test_insert_update_delete_backfill_repair(self):
  home=HomeData(self.s);home.initialize();home.refresh()
  self.db.import_records([{'id':50,'time':'2026-11-01T00:00:00+00:00','numbers':list(range(1,21))}],'backfill')
  with self.db.tx() as c:
   c.execute("UPDATE draws SET size='小',parity='双',draw_utc='2026-11-02T00:00:00+00:00' WHERE id=100")
   c.execute('DELETE FROM draws WHERE id=104')
  self.assertEqual(self.db.query('SELECT COUNT(*) n FROM home_cache_dirty')[0]['n'],3);home.refresh()
  new=home.summaries({},wait=True);old=self.baseline().run({'view':'home'})
  self.assertEqual(new['daily'],old['daily'])
  for kind in ('hour','four_hour','week'):self.assertEqual(new['summaries'][kind]['rows'],old['summaries'][kind]['rows'])
 def test_date_lookup_includes_pending_time_repairs(self):
  self.tools.run({'view':'home'})
  with self.db.tx() as c:c.execute("UPDATE draws SET draw_utc='2026-11-01T00:00:00+00:00' WHERE id=100")
  query={'view':'home','date':'2026-11-01','part':'records'}
  new=self.tools.run(query);old=self.baseline().run(query)
  self.assertEqual(new['rows'],old['rows']);self.assertEqual(new['total'],1)
 def test_only_page_is_enriched(self):
  from unittest.mock import patch
  with patch('pc28.home_data.enrich',wraps=enrich) as fn:
   self.tools.run({'view':'home','part':'records','limit':'1'});self.assertEqual(fn.call_count,1)
if __name__=='__main__':unittest.main()
