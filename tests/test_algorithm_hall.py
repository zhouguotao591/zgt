import unittest,tempfile
from pathlib import Path
from datetime import datetime,timezone,timedelta
from pc28.db import DB
from pc28.algorithm_hall import AlgorithmHall,DEFINITIONS,BY_ID,predict,evaluate,metrics

class AlgorithmHallTests(unittest.TestCase):
 def rows(self,count=160):
  t=datetime(2026,1,1,tzinfo=timezone.utc)
  return [{'id':100+i,'draw_utc':(t+timedelta(seconds=i*210)).isoformat(),'a':1,'b':2,'c':3,'total':6,'size':'大' if i%3 else '小','parity':'单' if i%2 else '双'} for i in range(count)]
 def test_only_two_axes_and_known_methods(self):
  self.assertEqual(len(DEFINITIONS),28);self.assertEqual({d['axis'] for d in DEFINITIONS},{'size','parity'})
  history=[{**r,'size':'大'} for r in self.rows(51)]
  self.assertEqual(predict(history,BY_ID['size.minority.20'])[0],'小');self.assertEqual(predict(history,BY_ID['size.majority.20'])[0],'大')
 def test_no_target_or_future_leakage(self):
  rows=self.rows();definition=BY_ID['size.majority.20'];a=evaluate(rows,definition)
  altered=[{**r,'size':'小' if r['size']=='大' else '大'} if r['id']>=220 else r for r in rows];b=evaluate(altered,definition)
  pa={r['target_draw']:r['prediction'] for r in a['records']};pb={r['target_draw']:r['prediction'] for r in b['records']}
  self.assertTrue(all(pa[k]==pb[k] for k in pa if k<=220))
  self.assertTrue(all(r['cutoff_draw']<r['target_draw'] for r in a['records']))
 def test_missing_target_excluded_and_streak_breaks(self):
  rows=[r for r in self.rows() if r['id']!=250];out=evaluate(rows,BY_ID['size.follow.1']);missing=next(r for r in out['records'] if r['target_draw']==250)
  self.assertIsNone(missing['hit']);self.assertEqual(len(out['records']),100);self.assertLess(out['metrics']['20']['count'],20)
  m=metrics([{'target_draw':1,'hit':1},{'target_draw':2,'hit':1},{'target_draw':3,'hit':None},{'target_draw':4,'hit':1}],20)
  self.assertEqual(m['max_hit'],2);self.assertEqual(m['current_hit'],1);self.assertEqual(m['count'],3)
 def test_gap_inside_lookback_and_tie_rule(self):
  rows=self.rows(21);rows[10]['id']+=1000;self.assertIsNone(predict(rows,BY_ID['size.majority.20'])[0])
  rows=self.rows(20)
  for i,r in enumerate(rows):r['size']='大' if i%2 else '小'
  self.assertEqual(predict(rows,BY_ID['size.minority.20'])[0],rows[-1]['size'])
 def test_empty_and_favorite_isolation_idempotency(self):
  with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent/'data') as tmp:
   db=DB(Path(tmp)/'test.sqlite3');hall=AlgorithmHall(db);listing=hall.listing(1);self.assertEqual(len(listing['algorithms']),28);self.assertTrue(all(r['metrics']['20']['rate'] is None for r in listing['algorithms']))
   body={'id':'size.follow.1','favorite':True};hall.favorite(1,body);hall.favorite(1,body)
   row=next(r for r in hall.listing(1)['algorithms'] if r['id']==body['id']);other=next(r for r in hall.listing(2)['algorithms'] if r['id']==body['id'])
   self.assertTrue(row['favorite']);self.assertFalse(other['favorite']);self.assertEqual(row['favorites'],1)
   with self.assertRaises(ValueError):hall.favorite(1,{'id':'bad','favorite':True})
   with self.assertRaises(ValueError):hall.detail('size.follow.1',page_size=7)
   self.assertEqual(hall.detail('size.follow.1')['total'],0)
