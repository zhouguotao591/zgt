import copy,json,math,random,tempfile,unittest
from pathlib import Path
from datetime import datetime,timedelta,timezone
from unittest.mock import patch
from pc28.config import DEFAULT
from pc28.service import Service
from pc28 import ai_model
from pc28.ai_simulation import next_stake,summary

class PaperTests(unittest.TestCase):
 def test_progression_and_drawdown_do_not_reset_equity(self):
  records=[]
  for expected in (50000,60000,75000,50000):
   self.assertEqual(next_stake(records),expected);records.append({'outcome':'loss','stake':expected,'pnl':-expected,'hit':0})
  self.assertEqual(summary(records)['max_drawdown'],235000)
  records.append({'outcome':'refund','stake':60000,'pnl':0,'hit':1});self.assertEqual(next_stake(records),50000)

def rows(count,start=None):
 start=start or datetime.now(timezone.utc)-timedelta(seconds=count*210+200);rng=random.Random(29);result=[]
 for i in range(count):
  a,b,c=[rng.randrange(10) for _ in range(3)];total=a+b+c;result.append({'id':1000+i,'draw_utc':(start+timedelta(seconds=i*210)).isoformat(),'a':a,'b':b,'c':c,'total':total,'size':'大' if total>=14 else '小','parity':'单' if total%2 else '双'})
 return result

class AIModelTests(unittest.TestCase):
 def test_features_do_not_read_target_or_future_labels(self):
  raw=rows(240);first,_,_=ai_model.dataset(raw);changed=copy.deepcopy(raw);r=changed[200];r.update(a=0,b=0,c=0,total=0,size='小',parity='双');second,_,_=ai_model.dataset(changed);self.assertEqual(first[0]['size']['x'],second[0]['size']['x']);self.assertEqual(first[0]['parity']['x'],second[0]['parity']['x'])
 def test_gaps_conflicts_and_future_records_reset_warmup(self):
  raw=rows(450);samples,_,_=ai_model.dataset(raw,{raw[210]['id']});ids={r['id'] for r in samples};self.assertNotIn(raw[210]['id'],ids);self.assertNotIn(raw[300]['id'],ids);self.assertIn(raw[411]['id'],ids)
  raw[-1]['draw_utc']=(datetime.now(timezone.utc)+timedelta(days=1)).isoformat();samples,history,quality=ai_model.dataset(raw);self.assertNotIn(raw[-1]['id'],{r['id'] for r in samples});self.assertEqual(history,[]);self.assertEqual(quality['excluded']['invalid_or_conflict'],1)
 def test_forward_validation_and_probability_bounds(self):
  samples,_,_=ai_model.dataset(rows(6500));artifact,validation=ai_model.train(samples,train_days=4,cal_days=2,test_days=2,folds=3,min_train=100,min_cal=50,min_test=50)
  self.assertTrue(validation)
  for axis in ('size','parity'):
   obj=artifact[axis];self.assertAlmostEqual(sum(obj['model']['weights']),1);self.assertTrue(all(w>=0 for w in obj['model']['weights']))
   for f in obj['folds']:self.assertLess(datetime.fromisoformat(f['training_end']),datetime.fromisoformat(f['start']));self.assertEqual(f['calibration_end'],f['start'])
  self.assertTrue(all(.001<=r['p']<=.999 for r in validation));self.assertFalse(artifact['size']['evaluation']['qualified'])
 def test_calibration_bins_and_empty_metrics(self):
  records=[{'p':.8,'base':.5,'y':int(i%5!=0),'time':'2026-01-01T12:00:00+00:00'} for i in range(100)];m=ai_model.metrics(records);self.assertAlmostEqual(m['brier'],.16);self.assertAlmostEqual(m['bins'][0]['observed'],.8);self.assertIsNone(ai_model.metrics([])['brier'])
 def test_partial_training_window_is_not_labeled_as_full_history(self):
  samples,_,_=ai_model.dataset(rows(6500))
  with self.assertRaises(ValueError):ai_model.train(samples,min_train=100,min_cal=50,min_test=50)

class AIServiceTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent/'data');root=Path(self.tmp.name);cfg=copy.deepcopy(DEFAULT);cfg['collector']['enabled']=False;(root/'config.json').write_text(json.dumps(cfg));self.s=Service(root)
 def tearDown(self):self.tmp.cleanup()
 def seed(self,age=200):
  raw=rows(400,datetime.now(timezone.utc)-timedelta(seconds=399*210+age))
  with self.s.db.tx() as c:
   c.executemany("INSERT INTO draws(id,source_time,draw_utc,numbers,a,b,c,total,size,parity,conversion_version,first_seen,source,raw_hash) VALUES(?,?,?,'[]',?,?,?,?,?,?,'test',?,'test','test')",[(r['id'],r['draw_utc'],r['draw_utc'],r['a'],r['b'],r['c'],r['total'],r['size'],r['parity'],r['draw_utc']) for r in raw])
  return raw
 def model(self,key='fixture',p=.6):
  artifact={axis:{'model':{'baseline':.5,'conditions':{},'logistic':{'mean':[0]*24,'scale':[1]*24,'coef':[ai_model.logit(p)]+[0]*24},'calibration':[1,0],'weights':[0,0,1]}} for axis in ('size','parity')};report={'axes':{axis:{'evaluation':ai_model.metrics([])} for axis in ('size','parity')}}
  with self.s.db.tx() as c:c.execute('INSERT INTO ai_models VALUES(?,?,?,?,?,?)',(key,datetime.now(timezone.utc).isoformat(),1399,ai_model.VERSION,json.dumps(artifact),json.dumps(report)))
 def test_prediction_immutable_and_no_financial_side_effects(self):
  self.seed();self.model();before=self.s.db.query('SELECT * FROM accounts')
  original_created=self.s.db.query("SELECT created FROM ai_models WHERE id='fixture'")[0]['created']
  with patch('pc28.ai_analysis.maintenance.block_reason',return_value=''):
   self.s.ai.tick();self.model('replacement',.9);self.s.ai.tick();self.assertEqual(self.s.ai.public()['cards'][0]['model_created'],original_created)
  pred=self.s.db.query('SELECT * FROM ai_predictions');self.assertEqual(len(pred),2);self.assertEqual({r['model_id'] for r in pred},{'fixture'});self.assertEqual(before,self.s.db.query('SELECT * FROM accounts'));self.assertEqual(self.s.db.query('SELECT COUNT(*) n FROM orders')[0]['n'],0)
  paper=self.s.db.query('SELECT * FROM ai_paper_orders');self.assertEqual(len(paper),2);self.assertEqual({r['stake'] for r in paper},{50000})
 def test_stale_data_and_no_model_are_honest_states(self):
  self.seed(age=600);self.model()
  with patch('pc28.ai_analysis.maintenance.block_reason',return_value=''):self.s.ai.tick();d=self.s.ai.public()
  self.assertIsNone(d['cards'][0]['probability']);self.assertEqual(self.s.db.query('SELECT COUNT(*) n FROM ai_predictions')[0]['n'],0);self.assertNotIn('coef',json.dumps(d));self.assertNotIn('artifact',d)
 def test_only_predictions_before_actual_draw_are_scored(self):
  self.seed();self.model();now=datetime.now(timezone.utc)
  with patch('pc28.ai_analysis.maintenance.block_reason',return_value=''),patch('pc28.ai_analysis.utcnow',return_value=(now-timedelta(seconds=20)).isoformat()),patch('pc28.ai_simulation.utcnow',return_value=(now-timedelta(seconds=20)).isoformat()):self.s.ai.tick()
  with self.s.db.tx() as c:c.execute("INSERT INTO draws(id,source_time,draw_utc,numbers,a,b,c,total,size,parity,conversion_version,first_seen,source,raw_hash) VALUES(1400,?,?,'[]',1,2,3,6,'小','双','test',?,'test','test')",((now-timedelta(seconds=10)).isoformat(),)*3)
  self.s.ai.settle();pred=self.s.db.query('SELECT * FROM ai_predictions');self.assertTrue(all(r['eligible']==1 and r['brier'] is not None for r in pred))
  self.s.ai.paper.tick();self.assertEqual({r['pnl'] for r in self.s.db.query('SELECT * FROM ai_paper_orders')},{-50000})
  with self.s.db.tx() as c:c.execute('UPDATE ai_predictions SET actual=NULL,created=?',(now.isoformat(),))
  self.s.ai.settle();self.assertTrue(all(r['eligible']==0 and r['brier'] is None for r in self.s.db.query('SELECT * FROM ai_predictions')))

