"""Expanded retrospective size search; historical fit is not independent validation."""
import copy,json,sqlite3,random
from pathlib import Path
from types import SimpleNamespace
from threading import RLock
from pc28.db import DB
from pc28.model import utcnow
from pc28 import history_backtest as hb
from pc28.custom_strategy import validate_settings,save

def main():
 root=Path(__file__).resolve().parent
 service=SimpleNamespace(root=root,cfg=json.loads((root/'config.json').read_text('utf8')),db=DB(root/'data/pc28.sqlite3'),lock=RLock())
 previous=json.loads((root/'data/exports/strategy-tuning-latest.json').read_text('utf8'))
 target=next(s['list_id'] for s in previous['strategies'] if s['source']=='legacy')
 original=hb.selection(service,target)
 rows,coverage=hb.source_window(service.db.query('SELECT * FROM draws ORDER BY id'),{r['draw_id'] for r in service.db.query('SELECT draw_id FROM conflicts WHERE resolved=0')},utcnow())
 boundary=rows[0]['_time']+(rows[-1]['_time']-rows[0]['_time'])*2/3
 train=[r for r in rows if r['_time']<boundary];test=[r for r in rows if r['_time']>=boundary]
 def evaluate(part,snap):
  result,_=hb.replay(part,snap)
  return {k:v for k,v in result.items() if k!='curve'}
 original['maintenance_guard']=service.cfg['collector'].get('maintenance_guard_enabled',True)
 baseline=evaluate(rows,original)
 candidates=[]
 # Preserve 30-draw direction and bankroll; search entry, round, staking and pause.
 rng=random.Random(2026110230)
 plans=[]
 for threshold in [2,4,6,8,10,12,14,16]:
  for bets in [1,2,3,4,5,6,8,10,15,21]:
   plans.append((threshold,bets,200,3,6,20))
 for _ in range(160):
  plans.append((rng.choice([2,4,6,8,10,12,14,16]),rng.choice([1,2,3,4,5,6,8,10,15,21]),rng.choice([100,125,150,175,200]),rng.choice([1,2,3]),rng.choice([0,3,4,5,6,8]),rng.choice([5,10,20,40,80])))
 for index,(threshold,bets,multiplier,losses,limit,pause) in enumerate(dict.fromkeys(plans)):
  snap=copy.deepcopy(original);snap['settings']['fixed'].update(threshold=threshold,max_bets=bets,multiplier_hundredths=multiplier,max_losses=losses)
  snap['settings'].update(win_limit=limit,pause_draws=pause)
  result=evaluate(rows,snap);candidates.append((result,snap))
  if index%20==0:print(json.dumps({'tested':index+1,'best_net':max(r['net'] for r,s in candidates)},ensure_ascii=False),flush=True)
 eligible=[(r,s) for r,s in candidates if r['bets']>=300 and r['net']>=50000 and r['net']>baseline['net'] and (r['roi'] or 0)>(baseline['roi'] or 0) and r['max_drawdown']<=original['settings']['max_drawdown']]
 result,chosen=max(eligible or candidates,key=lambda p:(p[0]['net'],p[0]['roi'] or 0))
 before_test=evaluate(test,original);after_test=evaluate(test,chosen)
 report={'coverage':coverage,'target_id':target,'candidate_count':len(candidates),'method':'Retrospective selection on full three months. Final third was previously inspected and is not an untouched holdout. Requires future validation.','baseline':baseline,'chosen':result,'settings':chosen['settings'],'first_two_thirds':evaluate(train,chosen),'last_third_baseline':before_test,'last_third_chosen':after_test,'goal_met':bool(eligible),'search':[{'settings':s['settings'],'result':r} for r,s in candidates]}
 folder=root/'data/exports'
 if eligible:
  stamp=utcnow().replace(':','').replace('+','_')
  with service.db.connect() as c,sqlite3.connect(folder/f'before-parity-reversal-tuning-{stamp}.sqlite3') as b:c.backup(b)
  allowed=('mode','initial_balance','daily_loss_limit','max_drawdown','max_stake','round_restart','fixed','win_limit','pause_draws')
  settings=validate_settings(service.cfg,{k:v for k,v in chosen['settings'].items() if k in allowed})
  save(service,{'id':target,'name':'三十期买单反转策略 · 历史净收益五万达标（待前向验证）','settings':settings})
  final=hb.execute(service,hb.prepare(service,{'strategy_id':target}));report['backtest_run']=final['run_id'];report['updated']=True
 else:report['updated']=False
 (folder/'parity-reversal-tuning-50000-latest.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
 print(json.dumps({k:v for k,v in report.items() if k not in ('search','coverage')},ensure_ascii=False),flush=True)

if __name__=='__main__':main()
