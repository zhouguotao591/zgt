import copy,json,tempfile,unittest
from pathlib import Path
from datetime import datetime,timedelta,timezone
from unittest.mock import patch
from pc28.db import DB
from pc28.config import DEFAULT
from pc28.timing import infer_break,publication_state,DrawTimer
from pc28.collector import collect_once
from pc28.model import convert
class PublicationTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent/'data');self.root=Path(self.tmp.name);self.db=DB(self.root/'db.sqlite3');self.last=datetime(2026,11,2,10,56,30,tzinfo=timezone.utc);self.now=datetime(2026,11,2,11,20,tzinfo=timezone.utc)
  rows=[]
  for i in range(4):
   at=self.last-timedelta(days=4-i);rows.extend([{'id':100+2*i,'time':at.isoformat(),'numbers':list(range(1,21))},{'id':101+2*i,'time':(at+timedelta(minutes=36,seconds=30)).isoformat(),'numbers':list(range(1,21))}])
  rows.append({'id':108,'time':self.last.isoformat(),'numbers':list(range(1,21))});self.db.import_records(rows,'test')
 def tearDown(self):self.tmp.cleanup()
 def test_repeated_contiguous_id_gap_forecast(self):
  plan=infer_break(self.db,self.now);self.assertTrue(plan['inferred']);self.assertTrue(plan['resting']);self.assertEqual(plan['evidence_days'],4);self.assertEqual(plan['next_draw_id'],109);self.assertEqual(plan['next_draw_at'],'2026-11-02T11:33:00+00:00');p=publication_state(self.db.tail(1)[0],plan,self.now);self.assertEqual(p['phase'],'rest');self.assertIn('19:30:00',p['message'])
 def test_forecast_is_not_a_guarantee_and_expires(self):
  plan=infer_break(self.db,self.now);latest=self.db.tail(1)[0];self.assertEqual(publication_state(latest,plan,datetime(2026,11,2,11,33,20,tzinfo=timezone.utc))['phase'],'awaiting');self.assertEqual(publication_state(latest,plan,datetime(2026,11,2,11,35,tzinfo=timezone.utc))['phase'],'delayed');self.assertIsNone(infer_break(self.db,datetime(2026,11,2,11,49,tzinfo=timezone.utc)))
 def test_insufficient_or_unmatched_history_not_inferred(self):
  with self.db.tx() as c:c.execute('DELETE FROM draws WHERE id<104')
  self.assertIsNone(infer_break(self.db,self.now))
 def test_schedule_change_for_same_id_resets_event_attempts(self):
  timer=DrawTimer();first={'next_draw_id':109,'next_draw_at':'2026-11-02T11:00:00+00:00'};timer.update(first,108,self.last.timestamp());timer.mark();self.assertEqual(timer.state['attempts'],1);plan=infer_break(self.db,self.now);timer.update(plan,108,self.now.timestamp());self.assertEqual(timer.state['attempts'],0)
 def test_old_work_response_marked_stale_not_success(self):
  cfg=copy.deepcopy(DEFAULT['collector']);cfg['fallback_enabled']=False;balls=list(range(1,21));v=convert(balls);body=json.dumps({'data':{'drawNbr':108,'drawnAt':'2026-10-01T00:00:00Z','kenoNbrs':balls,'n1':v['a'],'n2':v['b'],'n3':v['c'],'drawNum':v['total'],'size':v['size'],'parity':v['parity']}}).encode()
  with patch('pc28.collector.fetch',side_effect=[ValueError('403'),body]):r=collect_once(self.db,self.root,cfg)
  self.assertEqual(r['publication_status'],'stale');self.assertIsNone(r['draw_schedule']);self.assertEqual(self.db.query("SELECT status FROM jobs WHERE kind='latest' ORDER BY id DESC LIMIT 1")[0]['status'],'stale')
if __name__=='__main__':unittest.main()
