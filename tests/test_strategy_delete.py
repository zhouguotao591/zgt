import copy,json,tempfile,unittest
from pathlib import Path
from pc28.config import DEFAULT
from pc28.service import Service
from pc28.custom_strategy import save
from pc28.strategies import catalog,control,order_page
from pc28.strategy_delete import delete
from pc28.history_backtest import metadata,selection

class DeleteTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent/"data");self.root=Path(self.tmp.name)
  cfg=copy.deepcopy(DEFAULT);cfg['collector']['enabled']=False
  (self.root/'config.json').write_text(json.dumps(cfg),encoding='utf8');self.s=Service(self.root)
 def tearDown(self):self.tmp.cleanup()
 def test_archive_custom_and_protect_history(self):
  settings=copy.deepcopy(catalog(self.s.db,self.s.cfg)['strategies'][0]['settings'])
  key=save(self.s,{'name':'删除测试','settings':settings})['id']
  control(self.s,{'id':key,'enabled':True})
  with self.assertRaisesRegex(ValueError,'先停用'):delete(self.s,{'id':key})
  control(self.s,{'id':key,'enabled':False})
  with self.s.db.tx() as c:c.execute("INSERT INTO custom_orders(strategy,target_draw,status,pnl,stake) VALUES(?,100,'pending',NULL,100)",(key,))
  with self.assertRaisesRegex(ValueError,'结算'):delete(self.s,{'id':key})
  with self.s.db.tx() as c:c.execute("UPDATE custom_orders SET status='settled',pnl=100,hit=1,outcome='win' WHERE strategy=?",(key,))
  delete(self.s,{'id':key})
  self.assertNotIn(key,[r['id'] for r in catalog(self.s.db,self.s.cfg)['strategies']])
  self.assertNotIn(key,[r['id'] for r in metadata(self.s)['strategies']])
  self.assertNotIn(key,order_page(self.s.db,self.s.cfg)['strategy_names'])
  self.assertEqual(order_page(self.s.db,self.s.cfg,key,active_only=False)['total'],1)
  self.assertEqual(self.s.db.query('SELECT pnl FROM custom_orders WHERE strategy=?',(key,))[0]['pnl'],100)
  for action in (lambda:control(self.s,{'id':key,'enabled':True}),lambda:save(self.s,{'id':key,'name':'x','settings':settings}),lambda:selection(self.s,key)):
   with self.assertRaisesRegex(ValueError,'已删除'):action()
 def test_builtin_and_restart(self):
  for key in ('legacy','parity','size'):delete(self.s,{'id':key})
  self.assertEqual(catalog(self.s.db,self.s.cfg)['strategies'],[])
  self.assertEqual(catalog(Service(self.root).db,self.s.cfg)['strategies'],[])
  cfg=copy.deepcopy(self.s.cfg);cfg['trend_simulation']['enabled_axes']=['size']
  with self.assertRaisesRegex(ValueError,'已删除'):self.s.update_config(cfg)
  self.assertEqual(len(self.s.db.query('SELECT * FROM trend_accounts')),2)
if __name__=='__main__':unittest.main()


