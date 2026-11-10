import copy,json,tempfile,unittest,urllib.error
from pathlib import Path
from unittest.mock import patch
from pc28.config import DEFAULT
from pc28.db import DB
from pc28.collector import collect_once
from pc28.ingest import world_records
from pc28.service import Service

SAMPLE={'ok':True,'data':{'draw':{'nbr':3501087,'time':'2026-11-01 11:25:00','n1':0,'n2':3,'n3':2,'sum':5,'big':False,'odd':True,'keno':[55,77,52,1,34,66,39,16,59,22,37,67,60,64,24,26,15,49,48,32]}}}
FALLBACK=json.dumps(SAMPLE).encode()
class FallbackTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent/'data');self.root=Path(self.tmp.name);self.db=DB(self.root/'test.sqlite3');self.config=copy.deepcopy(DEFAULT['collector']);self.config['work_fallback_enabled']=False
 def tearDown(self):self.tmp.cleanup()
 def test_official_success_no_fallback(self):
  body=json.dumps([{'drawNumber':1,'drawDate':'2026-11-01T00:00:00Z','numbers':list(range(1,21))}]).encode()
  with patch('pc28.collector.fetch',return_value=body) as f:r=collect_once(self.db,self.root,self.config)
  self.assertEqual(f.call_count,1);self.assertEqual(r['selected_source'],'official')
 def test_http_error_falls_back(self):
  with patch('pc28.collector.fetch',side_effect=[urllib.error.HTTPError(self.config['latest_url'],403,'Forbidden',{},None),FALLBACK]) as f:r=collect_once(self.db,self.root,self.config)
  self.assertEqual(f.call_count,2);self.assertEqual(r['selected_source'],'fallback');self.assertEqual(r['inserted'],1);row=self.db.tail()[0];self.assertEqual([row['a'],row['b'],row['c']],[0,3,2]);self.assertEqual(row['draw_utc'],'2026-11-01T03:25:00+00:00');self.assertEqual(row['source'],self.config['fallback_url'])
  self.assertEqual(json.loads(self.db.query("SELECT details FROM jobs WHERE kind='latest'")[0]['details'])['attempts'][0]['status'],'error')
 def test_bad_primary_response_falls_back(self):
  with patch('pc28.collector.fetch',side_effect=[b'{"bad":true}',FALLBACK]):r=collect_once(self.db,self.root,self.config)
  self.assertEqual(r['selected_source'],'fallback')
 def test_both_fail_recorded(self):
  with patch('pc28.collector.fetch',side_effect=[TimeoutError('primary'),TimeoutError('fallback')]):
   with self.assertRaises(RuntimeError):collect_once(self.db,self.root,self.config)
  self.assertEqual(self.db.query("SELECT status FROM jobs WHERE kind='latest'")[0]['status'],'error');self.assertFalse(self.db.rows())
 def test_disabled_fallback_not_requested(self):
  self.config['fallback_enabled']=False
  with patch('pc28.collector.fetch',side_effect=TimeoutError('primary')) as f:
   with self.assertRaises(RuntimeError):collect_once(self.db,self.root,self.config)
  self.assertEqual(f.call_count,1)
 def test_recomputed_numbers_must_match(self):
  sample=copy.deepcopy(SAMPLE);sample['data']['draw']['n1']=7
  with self.assertRaises(ValueError):list(world_records(json.dumps(sample).encode(),self.config))
 def test_cross_source_time_preserved_without_false_conflict(self):
  r=list(world_records(FALLBACK,self.config))[0];official={'id':r['id'],'time':'2026-10-31T20:25:30','numbers':r['numbers']};self.db.import_records([official],self.config['archive_url']);result=self.db.import_records([r],self.config['fallback_url'])
  self.assertEqual(result['conflicts'],0);self.assertEqual(self.db.tail()[0]['source_time'],official['time']);self.assertEqual(len(self.db.query('SELECT * FROM source_observations')),2)
 def test_different_numbers_still_conflict(self):
  r=list(world_records(FALLBACK,self.config))[0];self.db.import_records([{'id':r['id'],'time':'2026-10-31T20:25:30','numbers':list(range(1,21))}],self.config['archive_url']);result=self.db.import_records([r],self.config['fallback_url']);self.assertEqual(result['conflicts'],1)
 def test_fixed_thirty_second_schedule(self):
  cfg=copy.deepcopy(DEFAULT);cfg['monthly_auto_select']=False;(self.root/'config.json').write_text(json.dumps(cfg),encoding='utf8');service=Service(self.root);clock={'now':0};calls=[]
  class Stop:
   def wait(self,seconds):clock['now']+=1;return clock['now']>65
  service.stop=Stop();service.submit=lambda kind,args=None:calls.append((kind,clock['now']))
  with patch('pc28.service.time.monotonic',side_effect=lambda:clock['now']),patch('pc28.service.tick',return_value={}):service._schedule()
  self.assertEqual([t for kind,t in calls if kind=='collect'],[2,32,62])
if __name__=='__main__':unittest.main()
