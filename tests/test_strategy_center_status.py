import copy,json,tempfile,unittest
from pathlib import Path
from pc28.config import DEFAULT
from pc28.service import Service
from pc28.strategy_center import listing,execution_states
from pc28.model import utcnow

class StrategyCenterStatusTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent/'data');root=Path(self.tmp.name);cfg=copy.deepcopy(DEFAULT);cfg['collector']['enabled']=False;(root/'config.json').write_text(json.dumps(cfg));self.s=Service(root)
 def tearDown(self):self.tmp.cleanup()
 def pending(self,target):
  with self.s.db.tx() as c:c.execute("INSERT INTO trend_orders(strategy,target_draw,cutoff_draw,created,direction,stake,status,cycle,step) VALUES('size',?,?,?,'大',1000,'pending',1,1)",(target,target-1,utcnow()))
 def test_no_order_waits_for_entry_even_when_disabled(self):
  self.assertEqual({r['state'] for r in listing(self.s)['strategies']},{'等待入场'})
 def test_pending_undrawn_waits_for_draw(self):
  self.pending(100);self.assertEqual(execution_states(self.s.db)['size'],'等待开奖')
 def test_draw_received_pending_settlement_then_entry(self):
  self.pending(100);self.s.db.import_records([{'id':100,'time':utcnow(),'numbers':list(range(1,21))}],'test');self.assertEqual(execution_states(self.s.db)['size'],'等待结算')
  with self.s.db.tx() as c:c.execute("UPDATE trend_orders SET status='settled',pnl=1000,hit=1,outcome='win' WHERE target_draw=100")
  self.assertNotIn('size',execution_states(self.s.db));self.assertEqual(next(r for r in listing(self.s)['strategies'] if r['id']=='size')['state'],'等待入场')
 def test_overdue_settlement_takes_priority(self):
  self.pending(100);self.pending(101);self.s.db.import_records([{'id':100,'time':utcnow(),'numbers':list(range(1,21))}],'test');self.assertEqual(execution_states(self.s.db)['size'],'等待结算')

if __name__=='__main__':unittest.main()
