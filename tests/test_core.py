import unittest,tempfile,json,copy,io,csv
from pathlib import Path
from datetime import datetime,timezone,timedelta
from pc28.model import convert,Strategy,payout,utcnow
from pc28.db import DB
from pc28.ingest import csv_records,json_records,import_bytes
from pc28.config import DEFAULT
from pc28.engine import Prepared,replay
from pc28.research import select_month,store_selection
from pc28.router import tick,settle

class CoreTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent/'data');self.root=Path(self.tmp.name);self.db=DB(self.root/'test.sqlite3');self.cfg=copy.deepcopy(DEFAULT);self.cfg['collector']['enabled']=False;self.cfg['collector']['maintenance_guard_enabled']=False;self.cfg['simulation']['enabled']=True;self.cfg['simulation']['round_restart']='immediate';self.cfg['simulation']['fixed'].update(direction='小',threshold=8,max_bets=3);self.now=datetime.now(timezone.utc).replace(microsecond=0)
 def tearDown(self):self.tmp.cleanup()
 def add(self,id,offset=0,nums=None):
  return self.db.import_records([{'id':id,'time':(self.now+timedelta(seconds=offset)).isoformat(),'numbers':nums or list(range(1,21))}],'test')
 def seed(self):
  for i in range(30):self.add(100+i,-(29-i)*210)
 def test_conversion_example(self):
  r=convert([1,7,13,18,27,28,32,33,34,39,42,44,45,48,53,58,61,62,63,80]);self.assertEqual([r['a'],r['b'],r['c'],r['total']],[8,4,5,17])
 def test_conflict_resolution_preserves_audit(self):
  self.add(1);self.add(1,nums=list(range(4,24)));self.db.resolve_conflict(1,'replace','已与官方逐列核验');self.assertEqual(self.db.tail()[0]['total'],13);self.assertEqual(len(self.db.query('SELECT * FROM revisions')),1);self.assertEqual(self.db.query('SELECT resolved FROM conflicts')[0]['resolved'],1)
 def test_same_conflict_not_repeated(self):
  self.add(1);self.add(1,nums=list(range(4,24)));self.add(1,nums=list(range(4,24)));self.assertEqual(len(self.db.query('SELECT * FROM conflicts')),1)
 def test_incomplete_month_not_frozen(self):
  r={'month':'2026-11','created':utcnow(),'candidates':[],'reason':'partial','data_complete':False};store_selection(self.db,r,self.cfg);self.assertEqual(len(self.db.query('SELECT * FROM selections')),0)
 def test_invalid_numbers(self):
  for nums in ([1]*20,list(range(20)),list(range(1,20))):
   with self.assertRaises(ValueError):convert(nums)
 def test_duplicate_and_conflict(self):
  self.add(1);self.assertEqual(self.add(1)['duplicates'],1);self.assertEqual(self.add(1,nums=list(range(4,24)))['conflicts'],1);self.assertEqual(self.db.tail()[0]['total'],19)
 def test_atomic_invalid_import(self):
  def gen():
   yield {'id':1,'time':self.now.isoformat(),'numbers':list(range(1,21))}
   yield {'id':2,'time':self.now.isoformat(),'numbers':[1]*20}
  with self.assertRaises(ValueError):self.db.import_records(gen(),'test')
  self.assertEqual(len(self.db.rows()),0)
 def test_json_mapping(self):
  c=copy.deepcopy(self.cfg['collector']);c['field_map']={'records':'data.records','id':'period','time':'when','numbers':'balls'}
  b=json.dumps({'data':{'records':[{'period':1,'when':self.now.isoformat(),'balls':list(range(1,21))}]}}).encode();self.assertEqual(list(json_records(b,c))[0]['id'],1)
 def test_json_order_guard(self):
  b=json.dumps([{'drawNumber':1,'drawDate':self.now.isoformat(),'numbers':list(range(20,0,-1))}]).encode()
  with self.assertRaises(ValueError):list(json_records(b,self.cfg['collector']))
 def test_gaps(self):
  self.add(1);self.add(4);self.assertEqual(self.db.gaps()[0]['count'],2)
 def test_live_three_losses_restart_and_idempotence(self):
  self.seed()
  for j,stake in enumerate((10000,20000,40000,10000)):
   result=tick(self.db,self.cfg,self.now);self.assertEqual(result['stake'],stake/100);tick(self.db,self.cfg,self.now)
   o=self.db.query('SELECT * FROM orders ORDER BY id DESC')[0];self.assertEqual(o['stake'],stake);self.add(130+j)
   settle(self.db,self.cfg);settle(self.db,self.cfg)
  self.assertEqual(len(self.db.query('SELECT * FROM orders')),4);self.assertEqual(len(self.db.query('SELECT * FROM ledger')),8);self.assertEqual(self.db.query('SELECT balance FROM accounts')[0]['balance'],10000000-80000)
 def test_win_resets_amount_continues_round(self):
  self.seed();tick(self.db,self.cfg,self.now);self.add(130);settle(self.db,self.cfg);tick(self.db,self.cfg,self.now);self.add(131,nums=list(range(4,24)));settle(self.db,self.cfg);state=json.loads(self.db.query('SELECT state FROM accounts')[0]['state']);self.assertTrue(state['active']);self.assertEqual(state['attempt'],2);self.assertEqual(state['stake'],10000);self.assertEqual(state['losses'],0)
 def test_live_refund_credits_only_principal(self):
  self.seed();tick(self.db,self.cfg,self.now)
  self.add(130,nums=[1,2,11,14,17,24,26,30,32,33,36,47,48,50,59,61,65,67,68,80]);settle(self.db,self.cfg);settle(self.db,self.cfg)
  order=self.db.query('SELECT * FROM orders')[0];account=self.db.query('SELECT * FROM accounts')[0]
  self.assertEqual(order['pnl'],0);self.assertEqual(account['balance'],10000000);self.assertEqual(json.loads(order['reason'])['settlement']['outcome'],'refund');self.assertEqual(json.loads(account['state'])['losses'],0);self.assertEqual(len(self.db.query('SELECT * FROM ledger')),2)
 def test_crosschecked_result_settles_once_before_raw_upgrade(self):
  self.seed();tick(self.db,self.cfg,self.now)
  partial={'id':130,'time':self.now.isoformat(),'numbers':None,'a':5,'b':1,'c':7,'total':13,'verification':'crosschecked_abc','result_sources':['https://pc28.world/api/v1/draws/latest','https://www.pc28.work/bff/market/latest']}
  self.db.import_records([partial],'https://www.pc28.work/bff/market/latest');settle(self.db,self.cfg);balance=self.db.query('SELECT balance FROM accounts')[0]['balance'];self.assertEqual(balance,10010000)
  self.add(130,nums=list(range(4,24)));settle(self.db,self.cfg);self.assertEqual(self.db.query('SELECT balance FROM accounts')[0]['balance'],balance);self.assertEqual(len(self.db.query('SELECT * FROM ledger')),2)
 def test_stale_prevents_order(self):
  self.seed();r=tick(self.db,self.cfg,self.now+timedelta(hours=1));self.assertEqual(r['action'],'skip');self.assertFalse(self.db.query('SELECT * FROM orders'))
 def test_naive_timezone_prevents_order(self):
  self.db.import_records([{'id':100+i,'time':self.now.replace(tzinfo=None).isoformat(),'numbers':list(range(1,21))} for i in range(30)],'file');r=tick(self.db,self.cfg,self.now);self.assertIn('时区',r['reason'])
 def test_gap_prevents_order(self):
  self.seed();self.add(132);r=tick(self.db,self.cfg,self.now);self.assertEqual(r['action'],'skip')
 def test_pending_gap_blocks_next_order(self):
  self.seed();tick(self.db,self.cfg,self.now);self.add(131);r=tick(self.db,self.cfg,self.now);self.assertIn('未结算',r['reason']);self.assertEqual(len(self.db.query('SELECT * FROM orders')),1)
 def test_balance_limit(self):
  self.seed();self.cfg['simulation']['initial_balance']=50;r=tick(self.db,self.cfg,self.now);self.assertIn('余额',r['reason'])
 def test_special_odds(self):
  self.assertEqual(payout(20000,'小',13,True,self.cfg['odds']),20000);self.assertEqual(payout(10000,'单',13,True,self.cfg['odds']),10000)
 def test_replay_stakes_and_no_lookahead(self):
  self.seed();self.add(130);self.add(131);self.add(132);s=Strategy(direction='小',threshold=8,max_bets=3);r=replay(Prepared(self.db.rows()),s,self.cfg['odds'],trace=True);self.assertEqual([x['stake'] for x in r['orders']],[100,200,400]);self.assertEqual(r['net'],-700);self.assertEqual(r['loss_stops'],1)
 def test_custom_loss_multiplier_and_stop_count(self):
  self.seed();self.add(130);self.add(131);self.add(132);p=Prepared(self.db.rows())
  r=replay(p,Strategy(direction='小',threshold=8,max_bets=8,multiplier_hundredths=300,max_losses=2),self.cfg['odds'],trace=True)
  self.assertEqual([x['stake'] for x in r['orders']],[100,300,100]);self.assertEqual(r['net'],-500);self.assertEqual(r['loss_stops'],1)
  r=replay(p,Strategy(direction='小',threshold=8,max_bets=8,multiplier_hundredths=150,max_losses=3),self.cfg['odds'],trace=True)
  self.assertEqual([x['stake'] for x in r['orders']],[100,150,225]);self.assertEqual(r['net'],-475)
 def test_custom_base_stake_drives_losses_and_win_reset(self):
  self.seed();self.add(130);self.add(131,nums=list(range(4,24)));self.add(132)
  r=replay(Prepared(self.db.rows()),Strategy(direction='小',threshold=8,max_bets=8,base_cents=25025),self.cfg['odds'],trace=True)
  self.assertEqual([x['stake'] for x in r['orders']],[250.25,500.5,250.25]);self.assertEqual(r['net'],0)
 def test_live_custom_base_amount(self):
  self.seed();self.cfg['simulation']['fixed']['base_cents']=25025;result=tick(self.db,self.cfg,self.now)
  self.assertEqual(result['stake'],250.25);self.assertEqual(self.db.query('SELECT stake FROM orders')[0]['stake'],25025)
 def test_pause_allows_settlement(self):
  self.seed();tick(self.db,self.cfg,self.now)
  with self.db.tx() as c:c.execute("UPDATE accounts SET paused=1,pause_reason='test'")
  self.add(130);tick(self.db,self.cfg,self.now);self.assertEqual(self.db.query('SELECT status FROM orders')[0]['status'],'settled');self.assertEqual(len(self.db.query('SELECT * FROM orders')),1)
 def test_selection_uses_only_past(self):
  rows=[];i=1
  for month in range(1,6):
   for day in (1,28):
    for j in range(35):rows.append({'id':i,'source_time':f'2026-{month:02}-{day:02}T00:{j:02}:00','size':'大','parity':'单','total':19});i+=1
  c=copy.deepcopy(self.cfg);c['research'].update(thresholds=[2],max_bets=[3],include_reversal=False,min_train_bets=1,min_validation_bets=1)
  a=select_month(rows,c,'2026-05');future=copy.deepcopy(rows)
  for r in future:
   if r['source_time']>='2026-05':r.update(size='小',parity='双',total=8)
  b=select_month(future,c,'2026-05');self.assertEqual(a['train_results'],b['train_results']);self.assertEqual(a['validation'],b['validation'])
 def test_month_selection_immutable(self):
  r={'month':'2026-11','created':utcnow(),'candidates':[],'reason':'first'};store_selection(self.db,r,self.cfg);r['reason']='second';store_selection(self.db,r,self.cfg);self.assertEqual(self.db.query('SELECT reason FROM selections')[0]['reason'],'first')
if __name__=='__main__':unittest.main()
