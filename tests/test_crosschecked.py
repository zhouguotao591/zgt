import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from pc28.config import DEFAULT
from pc28.db import DB
from pc28.collector import collect_once,repair_raw_once
from pc28.ingest import reported_result,crosscheck_results
from pc28.router import ensure_account
WORLD={'ok':True,'data':{'draw':{'nbr':10,'time':'2026-11-01 12:31:30','keno':None,'n1':7,'n2':3,'n3':9,'sum':19}}}
WORK={'data':{'drawNbr':10,'drawnAt':'2026-11-01T04:31:30Z','kenoNbrs':None,'n1':7,'n2':3,'n3':9,'drawNum':19,'size':'大','parity':'单'}}
class CrosscheckedTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent/'data');self.root=Path(self.tmp.name);self.db=DB(self.root/'db.sqlite3');self.cfg=copy.deepcopy(DEFAULT);self.c=self.cfg['collector']
 def tearDown(self):self.tmp.cleanup()
 def row(self):return crosscheck_results(reported_result(json.dumps(WORLD).encode(),'pc28_world',self.c),reported_result(json.dumps(WORK).encode(),'pc28_work',self.c),[self.c['fallback_url'],self.c['work_fallback_url']])
 def test_null_raw_uses_matching_crosschecked_results(self):
  with patch('pc28.collector.fetch',side_effect=[ValueError('403'),json.dumps(WORLD).encode(),json.dumps(WORK).encode()]):r=collect_once(self.db,self.root,self.c)
  self.assertEqual(r['verification'],'crosschecked_abc');row=self.db.tail()[0];self.assertEqual(row['numbers'],'null');self.assertEqual(row['total'],19);self.assertEqual(row['verification'],'crosschecked_abc')
 def test_mismatch_rejected(self):
  right=reported_result(json.dumps(WORK).encode(),'pc28_work',self.c);right.update(a=6,total=18)
  with self.assertRaises(ValueError):crosscheck_results(reported_result(json.dumps(WORLD).encode(),'pc28_world',self.c),right,[self.c['fallback_url'],self.c['work_fallback_url']])
  self.assertEqual(self.db.rows(),[])
 def test_unverified_partial_cannot_enter_db(self):
  row=self.row();row['result_sources']=[self.c['work_fallback_url']]
  with self.assertRaises(ValueError):self.db.import_records([row],self.c['work_fallback_url'])
 def test_matching_raw_upgrades_preserving_first_seen(self):
  self.db.import_records([self.row()],self.c['work_fallback_url'],received='2026-11-01T04:32:00+00:00');r=self.db.import_records([{'id':10,'time':'2026-11-01T12:31:30+08:00','numbers':list(range(1,21))}],self.c['fallback_url']);self.assertEqual(r['upgraded'],1);row=self.db.tail()[0];self.assertEqual(row['verification'],'full_keno');self.assertEqual(row['first_seen'],'2026-11-01T04:32:00+00:00');self.assertEqual(len(json.loads(row['numbers'])),20);self.assertEqual(r['conflicts'],0)
 def test_partial_does_not_downgrade_existing_raw(self):
  self.db.import_records([{'id':10,'time':'2026-11-01T12:31:30+08:00','numbers':list(range(1,21))}],self.c['fallback_url']);r=self.db.import_records([self.row()],self.c['work_fallback_url']);self.assertEqual(r['conflicts'],0);self.assertEqual(self.db.tail()[0]['verification'],'full_keno')
 def test_conflicting_later_raw_pauses_account(self):
  ensure_account(self.db,self.cfg);self.db.import_records([self.row()],self.c['work_fallback_url']);r=self.db.import_records([{'id':10,'time':'2026-11-01T12:31:30+08:00','numbers':list(range(4,24))}],self.c['fallback_url']);self.assertEqual(r['conflicts'],1);self.assertEqual(self.db.query('SELECT paused FROM accounts')[0]['paused'],1);self.assertEqual(self.db.tail()[0]['total'],19)
 def test_raw_repair_job_upgrades(self):
  self.db.import_records([self.row()],self.c['work_fallback_url']);d=copy.deepcopy(WORLD['data']['draw']);d['keno']=list(range(1,21))
  with patch('pc28.collector.fetch',return_value=json.dumps({'ok':True,'data':{'items':[d]}}).encode()):r=repair_raw_once(self.db,self.root,self.c)
  self.assertEqual(r['upgraded'],1);self.assertEqual(r['pending'],0)
if __name__=='__main__':unittest.main()
