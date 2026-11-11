import random,unittest
from datetime import datetime,timedelta,timezone
from pc28.algorithm_hall import DEFINITIONS,BY_ID,predict,evaluate,HALL_WINDOWS
from pc28.algorithm_library import FormulaHistory,full_backtest
from pc28.config import DEFAULT
from pc28.model import settlement

class ExpandedLibraryTests(unittest.TestCase):
 def rows(self):
  rng=random.Random(20261111);rows=[];t=datetime(2026,1,1,tzinfo=timezone.utc)
  for i in range(220):
   if i in (33,98):continue
   total=rng.randrange(28)
   rows.append(dict(id=100+i,size='大' if total>=14 else '小',parity='单' if total%2 else '双',total=total,a=0,b=0,c=0,draw_utc=(t+timedelta(seconds=210*i)).isoformat()))
  return rows
 def test_all_requested_periods_unique_and_legacy_ids_preserved(self):
  self.assertEqual(len(DEFINITIONS),310);self.assertEqual(len(BY_ID),310)
  for axis in ('size','parity'):
   for kind in ('majority','minority','transition'):
    self.assertEqual({d['lookback']-(kind=='transition') for d in DEFINITIONS if d['axis']==axis and d['kind']==kind},set(range(5,56)))
   for old in ('follow.1','reverse.1','majority.20','minority.50','transition.50'):self.assertIn(axis+'.'+old,BY_ID)
 def test_fast_predictions_match_reference_for_all_algorithms_and_gaps(self):
  rows=self.rows();prepared=FormulaHistory(rows)
  for d in DEFINITIONS:
   for i,choice in prepared.predictions(d):
    expected,_=predict(rows[:i],d)
    if not i or rows[i]['id']!=rows[i-1]['id']+1:expected=None
    labels=('小','大') if d['axis']=='size' else ('双','单')
    self.assertEqual(None if choice is None else labels[choice],expected,d['id'])
   windows=prepared.windows(d,(20,100,5000));records=evaluate(rows,d,5000)['records']
   for window in (20,100,5000):
    from pc28.algorithm_hall import metrics
    self.assertEqual(windows[str(window)],metrics(records,window),d['id'])
 def test_fixed_stake_batch_uses_same_odds_and_never_places_orders(self):
  rows=self.rows();prepared=FormulaHistory(rows)
  for key in ('size.majority.55','size.minority.5','parity.transition.55','parity.follow.1'):
   definition=BY_ID[key];result=full_backtest(prepared,rows,definition,DEFAULT['odds'])
   records=evaluate(rows,definition,5000)['records'];by_id={r['id']:r for r in rows};pnl=[]
   for r in records:
    if r['prediction'] is not None:pnl.append(settlement(10000,r['prediction'],by_id[r['target_draw']]['total'],r['hit'],DEFAULT['odds'])['pnl'])
   equity=peak=drawdown=0
   for value in pnl:equity+=value;peak=max(peak,equity);drawdown=max(drawdown,peak-equity)
   self.assertEqual(result['net_cents'],sum(pnl));self.assertEqual(result['count'],len(pnl));self.assertEqual(result['max_drawdown_cents'],drawdown)
