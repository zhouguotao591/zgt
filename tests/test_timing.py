import copy,json,tempfile,unittest
from pathlib import Path
from pc28.timing import DrawTimer,extract_schedule
from pc28.config import DEFAULT
from pc28.service import Service
class TimingTests(unittest.TestCase):
 def plan(self):return {'next_draw_id':11,'next_draw_at':'2026-11-01T04:00:00+00:00'}
 def test_trigger_and_bounded_retries(self):
  from datetime import datetime
  at=datetime.fromisoformat(self.plan()['next_draw_at']).timestamp();t=DrawTimer();t.update(self.plan(),10,at-30)
  self.assertFalse(t.due(at-1));self.assertTrue(t.due(at));t.mark();self.assertFalse(t.due(at+2));self.assertTrue(t.due(at+3));t.mark();self.assertTrue(t.due(at+8));t.mark();self.assertTrue(t.due(at+15));t.mark();self.assertFalse(t.due(at+100))
 def test_same_target_keeps_retry_count_and_new_target_resets(self):
  from datetime import datetime
  at=datetime.fromisoformat(self.plan()['next_draw_at']).timestamp();t=DrawTimer();t.update(self.plan(),10,at);t.mark();t.update(self.plan(),10,at);self.assertEqual(t.state['attempts'],1)
  t.update({'next_draw_id':12,'next_draw_at':'2026-11-01T04:03:30+00:00'},11,at);self.assertEqual(t.state['attempts'],0)
 def test_metadata_epoch_milliseconds(self):
  b=json.dumps({'data':{'next_nbr':11,'countdown':{'next_draw_at':1793505600000}}}).encode();r=extract_schedule(b,10,1793505500);self.assertEqual(r['next_draw_at'],'2026-11-01T04:00:00+00:00')
 def test_invalid_or_old_metadata_ignored(self):
  self.assertIsNone(extract_schedule(b'{}',10));self.assertIsNone(extract_schedule(json.dumps({'data':{'next_nbr':9,'countdown':{'next_draw_at':1793505600000}}}).encode(),10,1793505500))
 def test_event_triggers_do_not_replace_thirty_second_polls(self):
  from unittest.mock import patch
  from datetime import datetime,timezone
  with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent/'data') as root:
   cfg=copy.deepcopy(DEFAULT);cfg['monthly_auto_select']=False;Path(root,'config.json').write_text(json.dumps(cfg),encoding='utf8');service=Service(root);clock={'now':0};base=1793505500;calls=[]
   service.draw_timer.update({'next_draw_id':11,'next_draw_at':datetime.fromtimestamp(base+10,timezone.utc).isoformat()},10,base)
   class Stop:
    def wait(self,seconds):clock['now']+=1;return clock['now']>35
   service.stop=Stop();service.submit=lambda kind,args=None:calls.append((kind,clock['now'],args))
   with patch('pc28.service.time.monotonic',side_effect=lambda:clock['now']),patch('pc28.service.time.time',side_effect=lambda:base+clock['now']),patch('pc28.service.tick',return_value={}):service._schedule()
   self.assertEqual([t for kind,t,a in calls if a['trigger']=='periodic'],[2,32]);self.assertEqual([t for kind,t,a in calls if a['trigger']=='draw_time'],[10,13,18,25])
 def test_beijing_conversion_handles_winter_summer_and_known_utc(self):
  with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent/'data') as root:
   cfg=copy.deepcopy(DEFAULT);cfg['collector']['source_timezone']='America/Vancouver';Path(root,'config.json').write_text(json.dumps(cfg),encoding='utf8');s=Service(root)
   for raw,expected in [('2026-01-01T00:02:00','2026-01-01 16:02:00'),('2026-07-01T00:02:00','2026-07-01 15:02:00')]:self.assertEqual(s.display_draw({'source_time':raw,'source':'official'})['beijing_time'],expected)
   self.assertEqual(s.display_draw({'draw_utc':'2026-11-01T04:00:00+00:00','source_time':'ignored'})['beijing_time'],'2026-11-01 12:00:00')
if __name__=='__main__':unittest.main()
