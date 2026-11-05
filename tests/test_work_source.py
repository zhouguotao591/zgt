import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from pc28.config import DEFAULT
from pc28.db import DB
from pc28.collector import collect_once
from pc28.ingest import work_records,world_records
from pc28.timing import extract_schedule
WORK={'data':{'drawNbr':10,'drawnAt':'2026-11-01T04:31:30.000Z','nextDrawNbr':11,'nextDrawAt':'2026-11-01T04:35:00.000Z','kenoNbrs':list(range(1,21)),'n1':7,'n2':3,'n3':9,'drawNum':19,'size':'大','parity':'单'}}
WORLD_NULL={'ok':True,'data':{'draw':{'nbr':10,'keno':None}}}
class WorkSourceTests(unittest.TestCase):
 def test_null_keno_is_clear_validation_error(self):
  with self.assertRaisesRegex(ValueError,'尚未补齐'):list(world_records(json.dumps(WORLD_NULL).encode(),DEFAULT['collector']))
 def test_utc_and_abc_verified(self):
  r=list(work_records(json.dumps(WORK).encode(),DEFAULT['collector']))[0];self.assertEqual(r['time'],'2026-11-01T12:31:30+08:00');self.assertEqual(r['numbers'],list(range(1,21)))
 def test_schedule_is_read_from_work(self):
  plan=extract_schedule(json.dumps(WORK).encode(),10,1793507600);self.assertEqual(plan['next_draw_id'],11);self.assertEqual(plan['next_draw_at'],'2026-11-01T04:35:00+00:00')
 def test_world_null_uses_work_and_preserves_official_priority(self):
  with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent/'data') as root:
   db=DB(Path(root)/'db.sqlite3')
   with patch('pc28.collector.fetch',side_effect=[ValueError('official403'),json.dumps(WORLD_NULL).encode(),json.dumps(WORK).encode()]):r=collect_once(db,root,copy.deepcopy(DEFAULT['collector']))
   self.assertEqual(r['selected_source'],'pc28_work');self.assertEqual(r['inserted'],1)
   old={'id':10,'time':'2026-10-31T21:31:30','numbers':list(range(1,21))};q=db.import_records([old],'https://www.playnow.com/archive');self.assertEqual(q['conflicts'],0);self.assertEqual(db.tail()[0]['source_time'],old['time'])
if __name__=='__main__':unittest.main()
