import unittest,tempfile
from pathlib import Path
from pc28.db import DB
SOURCE='https://www.playnow.com/resources/documents/downloadable-numbers/KenoCurrentYear.zip'
class SourceTimeTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent/'data');self.db=DB(Path(self.tmp.name)/'test.sqlite3')
 def tearDown(self):self.tmp.cleanup()
 def record(self,time,numbers=None):return {'id':1,'time':time,'numbers':numbers or list(range(1,21))}
 def test_official_november_permanent_zone(self):
  self.db.import_records([self.record('2026-11-01T03:56:30')],SOURCE,source_timezone='America/Vancouver')
  self.assertEqual(self.db.tail()[0]['draw_utc'],'2026-11-01T10:56:30+00:00')
 def test_same_numbers_same_utc_normalize_and_audit(self):
  self.db.import_records([self.record('2026-11-01T02:56:30-08:00')],SOURCE)
  result=self.db.import_records([self.record('2026-11-01T03:56:30')],SOURCE,source_timezone='America/Vancouver')
  self.assertEqual(result['conflicts'],0);self.assertEqual(self.db.tail()[0]['source_time'],'2026-11-01T03:56:30')
  self.assertEqual(len(self.db.query('SELECT * FROM revisions')),1)
 def test_repair_preserves_real_conflicts(self):
  self.db.import_records([self.record('2026-11-01T02:56:30-08:00')],SOURCE)
  self.db.import_records([self.record('2026-11-01T03:56:30')],SOURCE)
  self.db.import_records([self.record('2026-11-01T03:56:30',list(range(2,22)))],SOURCE)
  self.assertEqual(self.db.reconcile_time_conflicts(),[1]);self.assertEqual(self.db.reconcile_time_conflicts(),[])
  self.assertEqual(self.db.query('SELECT COUNT(*) n FROM conflicts WHERE resolved=0')[0]['n'],1)
 def test_different_utc_still_conflicts(self):
  self.db.import_records([self.record('2026-11-01T03:56:30')],SOURCE,source_timezone='America/Vancouver')
  result=self.db.import_records([self.record('2026-11-01T04:56:30')],SOURCE,source_timezone='America/Vancouver')
  self.assertEqual(result['conflicts'],1)
