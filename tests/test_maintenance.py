import copy,json,tempfile,unittest
from pathlib import Path
from datetime import datetime,timedelta,timezone
from pc28.maintenance import window,block_reason,bc_zone,covered_gap
from pc28.timing import publication_state
from pc28.config import DEFAULT
from pc28.service import Service
from pc28.router import tick as legacy_tick
from pc28.trend_strategy import live_tick
from pc28.custom_strategy import tick,save
from pc28.follow_strategy import defaults
from pc28.strategies import control
class MaintenanceTests(unittest.TestCase):
 def test_bc_calendar_after_permanent_dst_and_historic_winter(self):
  nov=datetime(2026,11,2,11,10,tzinfo=timezone.utc);a,b=window(nov);self.assertEqual((a.hour,b.hour,b.minute),(11,11,30));self.assertEqual(bc_zone(nov).utcoffset(nov),timedelta(hours=-7))
  jan=datetime(2026,1,2,12,10,tzinfo=timezone.utc);a,b=window(jan);self.assertEqual((a.hour,b.hour,b.minute),(12,12,30))
 def test_pause_last_slot_and_last_record_before_resume(self):
  latest={'id':10,'draw_utc':'2026-11-02T10:53:00+00:00'};self.assertIn('最后一期不下单',block_reason(latest,datetime(2026,11,2,10,53,1,tzinfo=timezone.utc)))
  self.assertIn('暂停休息中',block_reason(latest,datetime(2026,11,2,11,10,tzinfo=timezone.utc)))
  last={'id':11,'draw_utc':'2026-11-02T10:56:30+00:00'};self.assertTrue(block_reason(last,datetime(2026,11,2,11,31,tzinfo=timezone.utc),planned=datetime(2026,11,2,11,33,tzinfo=timezone.utc)))
  resumed={'id':12,'draw_utc':'2026-11-02T11:33:00+00:00'};self.assertEqual(block_reason(resumed,datetime(2026,11,2,11,33,1,tzinfo=timezone.utc)),'')
  self.assertEqual(publication_state(last,{},datetime(2026,11,2,11,10,tzinfo=timezone.utc))['phase'],'rest')
 def test_scheduled_gap_is_covered_but_random_gap_is_not(self):
  a=datetime(2026,11,2,10,56,30,tzinfo=timezone.utc);b=datetime(2026,11,2,11,33,tzinfo=timezone.utc);self.assertTrue(covered_gap(a,b));self.assertFalse(covered_gap(a-timedelta(hours=1),b-timedelta(hours=1)))
 def test_all_live_types_skip_final_period_settle_existing_and_resume(self):
  with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent/'data') as folder:
   root=Path(folder);cfg=copy.deepcopy(DEFAULT);cfg['collector'].update(enabled=False,maintenance_guard_enabled=True);cfg['simulation'].update(enabled=True,max_stake=20000,daily_loss_limit=1000000,max_drawdown=1000000);cfg['simulation']['fixed'].update(direction='小',threshold=6,max_bets=8);cfg['trend_simulation']['enabled_axes']=['size','parity'];(root/'config.json').write_text(json.dumps(cfg));s=Service(root)
   settings={k:copy.deepcopy(cfg['simulation'][k]) for k in ('mode','initial_balance','daily_loss_limit','max_drawdown','max_stake','round_restart','fixed','win_limit','pause_draws')}
   reverse=save(s,{'name':'reverse','settings':settings})['id'];major=save(s,{'name':'major','settings':defaults(cfg,'majority')})['id'];hour=save(s,{'name':'hour','settings':defaults(cfg,'hourly')})['id']
   for key in (reverse,major,hour):control(s,{'id':key,'enabled':True})
   at=datetime(2026,11,2,10,49,30,tzinfo=timezone.utc);s.db.import_records([{'id':100+i,'time':(at-timedelta(seconds=210*(69-i))).isoformat(),'numbers':list(range(1,21))} for i in range(70)],'test')
   def run(now,next_id,next_at):
    with s.db.tx() as c:
     c.execute('UPDATE draws SET first_seen=?',((now-timedelta(seconds=1)).isoformat(),));c.execute("INSERT OR REPLACE INTO runtime_state VALUES('draw_timer',?,?)",(json.dumps({'next_draw_id':next_id,'next_draw_at':next_at.isoformat()}),now.isoformat()))
    legacy_tick(s.db,s.cfg,now);live_tick(s.db,s.cfg,now);tick(s.db,s.cfg,now)
   run(at+timedelta(seconds=1),170,at+timedelta(seconds=210))
   self.assertEqual(sum(s.db.query('SELECT COUNT(*) n FROM '+t)[0]['n'] for t in ('orders','trend_orders','custom_orders')),6)
   before_last=at+timedelta(seconds=210);s.db.import_records([{'id':170,'time':before_last.isoformat(),'numbers':list(range(1,21))}],'test');run(before_last+timedelta(seconds=1),171,before_last+timedelta(seconds=210))
   for t in ('orders','trend_orders','custom_orders'):self.assertFalse(s.db.query("SELECT 1 FROM "+t+" WHERE status='pending'"));self.assertFalse(s.db.query('SELECT 1 FROM '+t+' WHERE target_draw=171'))
   s.db.import_records([{'id':171,'time':'2026-11-02T10:56:30+00:00','numbers':list(range(1,21))}],'test');run(datetime(2026,11,2,11,10,tzinfo=timezone.utc),172,datetime(2026,11,2,11,33,tzinfo=timezone.utc))
   for t in ('orders','trend_orders','custom_orders'):self.assertFalse(s.db.query('SELECT 1 FROM '+t+' WHERE target_draw=172'))
   s.db.import_records([{'id':172,'time':'2026-11-02T11:33:00+00:00','numbers':list(range(1,21))}],'test');run(datetime(2026,11,2,11,33,1,tzinfo=timezone.utc),173,datetime(2026,11,2,11,36,30,tzinfo=timezone.utc))
   self.assertEqual(sum(s.db.query('SELECT COUNT(*) n FROM '+t+' WHERE target_draw=173')[0]['n'] for t in ('orders','trend_orders','custom_orders')),6)
if __name__=='__main__':unittest.main()
