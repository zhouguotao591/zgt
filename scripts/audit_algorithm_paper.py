import json,sys,time,bisect,sqlite3
from pathlib import Path
root=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root))
from pc28.db import DB
from pc28.algorithm_hall import AlgorithmHall,DEFINITIONS,predict
from pc28.model import settlement
cfg=json.loads((root/'config.json').read_text(encoding='utf-8'));db=DB.__new__(DB);db.path=root/cfg['database'];h=AlgorithmHall(db,cfg['odds']);report=h.paper.sync();source=h.paper.query('SELECT * FROM sources ORDER BY id');ids=[r['id'] for r in source];by={r['id']:r for r in source};checked=0
c=h.paper.connect();c.execute('BEGIN');odds={r['id']:json.loads(r['value']) for r in c.execute('SELECT * FROM odds')}
for d in DEFINITIONS:
 rows=c.execute('SELECT * FROM paper_orders WHERE algorithm_id=? AND (target_draw%5000=0 OR target_draw>?) ORDER BY target_draw',(d['id'],ids[-1]-10)).fetchall()
 for r in rows:
  i=bisect.bisect_left(ids,r['target_draw']);history=source[max(0,i-d['lookback']):i];direction=predict(history,d)[0]
  if not history or history[-1]['id']!=r['target_draw']-1:direction=None
  assert direction==r['direction'],(d['id'],r['target_draw'],'prediction')
  prior=c.execute('SELECT balance_after,next_stake,investment FROM paper_orders WHERE algorithm_id=? AND target_draw<? ORDER BY target_draw DESC LIMIT 1',(d['id'],r['target_draw'])).fetchone()
  balance=prior['balance_after'] if prior else 1000000;stake=prior['next_stake'] if prior else 10000
  assert r['balance_before']==balance
  if direction:
   actual=by[r['target_draw']];hit=direction==actual[d['axis']];paid=settlement(stake,direction,actual['total'],hit,odds[actual['odds_id']]);assert (r['stake'],r['hit'],r['pnl'],r['outcome'],r['balance_after'])==(stake,int(hit),paid['pnl'],paid['outcome'],balance+paid['pnl'])
   next_stake=10000 if paid['outcome']=='win' or (paid['outcome']=='loss' and stake>=20000) else stake+2500 if paid['outcome']=='loss' else stake;assert r['next_stake']==next_stake
  else:assert r['stake']==0 and r['pnl']==0 and r['balance_after']==balance
  checked+=1
counts=dict(c.execute('SELECT COUNT(*) records,SUM(stake>0) settled_orders,SUM(stake=0) warmup_skips FROM paper_orders').fetchone());c.close();h.snapshot()
results={'algorithms':len(DEFINITIONS),'source_draws':len(source),'sampled_records':checked,'all_sampled_predictions_stakes_payouts_balances_match':True,'counts':counts,'latest_cutoff':ids[-1],'sync_report':report}
(root/'data/audits/algorithm-paper-production-audit-20261121.json').write_text(json.dumps(results,indent=2),encoding='utf-8');print(json.dumps(results))
