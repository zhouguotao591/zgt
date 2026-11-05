import unittest,tempfile,copy,json
from pathlib import Path
from unittest.mock import patch
from pc28.db import DB
from pc28.config import DEFAULT
from pc28.sync import sync_history
class SyncTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent/'data');self.root=Path(self.tmp.name);self.db=DB(self.root/'db.sqlite3');self.cfg=copy.deepcopy(DEFAULT['collector']);self.db.import_records([{'id':i,'time':'2026-11-01T00:00:00+08:00','numbers':list(range(1,21))} for i in (1,2,5)],'test')
 def tearDown(self):self.tmp.cleanup()
 def payload(self):return {'ok':True,'data':{'items':[{'nbr':i,'time':'2026-11-01 00:00:00','keno':list(range(1,21)),'n1':7,'n2':3,'n3':9,'sum':19,'big':True,'odd':True} for i in range(5,0,-1)]}}
 def test_fills_missing_and_compares_overlap(self):
  with patch('pc28.sync.fetch',return_value=json.dumps(self.payload()).encode()):r=sync_history(self.db,self.root,self.cfg)
  self.assertEqual(r['inserted'],2);self.assertEqual(r['full_overlap_matched'],3);self.assertEqual(r['gaps'],[]);self.assertTrue(Path(r['backup']).exists())
 def test_null_raw_not_fabricated(self):
  p=self.payload();p['data']['items'][2]['keno']=None
  with patch('pc28.sync.fetch',return_value=json.dumps(p).encode()):r=sync_history(self.db,self.root,self.cfg)
  self.assertEqual(r['missing_through_cutoff'],[3]);self.assertEqual(r['inserted'],1)
 def test_invalid_abc_does_not_import_page(self):
  p=self.payload();p['data']['items'][2]['n1']=0
  with patch('pc28.sync.fetch',return_value=json.dumps(p).encode()):
   with self.assertRaises(ValueError):sync_history(self.db,self.root,self.cfg)
  self.assertEqual(len(self.db.rows()),3)
if __name__=='__main__':unittest.main()
