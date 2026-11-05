"""Expanded retrospective size search; historical fit is not independent validation."""
import copy,json,sqlite3,random
from pathlib import Path
from types import SimpleNamespace
from threading import RLock
from pc28.db import DB
from pc28.model import utcnow
from pc28 import history_backtest as hb
from pc28.follow_strategy import defaults,validate,save

def main():
 root=Path(__file__).resolve().parent
 service=SimpleNamespace(root=root,cfg=json.loads((root/'config.json').read_text('utf8')),db=DB(root/'data/pc28.sqlite3'),lock=RLock())
 previous=json.loads((root/'data/exports/strategy-tuning-latest.json').read_text('utf8'))
 target=next(s['list_id'] for s in previous['strategies'] if s['source']=='size')
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
 # Deterministic joint parameter search; no increase to base stake or limits.
 rng=random.Random(2026110250)
 plans=[]
 for lookback in [40,60,70,75,80,85,90,100,120,150,200,300]:
  for limit,pause in [(0,20),(2,10),(3,20),(4,30),(5,10),(5,40),(6,20),(8,20)]:
   plans.append((lookback,limit,pause,10000,3))
 for _ in range(224):
  plans.append((rng.randint(2,300),rng.choice([0,2,3,4,5,6,8]),rng.choice([5,10,20,30,40,60,100]),rng.choice([0,5000,10000]),rng.choice([1,2,3])))
 for index,(lookback,limit,pause,increment,losses) in enumerate(dict.fromkeys(plans)):
  snap=copy.deepcopy(original);snap['settings'].update(lookback=lookback,win_limit=limit,pause_draws=pause,loss_increment_cents=increment,max_losses=losses)
  validate(service.cfg,{**defaults(service.cfg,'majority','size'),**snap['settings']})
  result=evaluate(rows,snap);candidates.append((result,snap))
  if index%20==0:print(json.dumps({'tested':index+1,'best_net':max(r['net'] for r,s in candidates)},ensure_ascii=False),flush=True)
 eligible=[(r,s) for r,s in candidates if r['bets']>=300 and r['net']>=50000 and r['net']>baseline['net'] and (r['roi'] or 0)>(baseline['roi'] or 0) and r['max_drawdown']<=original['settings']['max_drawdown']]
 result,chosen=max(eligible or candidates,key=lambda p:(p[0]['net'],p[0]['roi'] or 0))
 before_test=evaluate(test,original);after_test=evaluate(test,chosen)
 report={'coverage':coverage,'target_id':target,'candidate_count':len(candidates),'method':'Retrospective selection on full three months. Final third was previously inspected and is not an untouched holdout. Requires future validation.','baseline':baseline,'chosen':result,'settings':chosen['settings'],'first_two_thirds':evaluate(train,chosen),'last_third_baseline':before_test,'last_third_chosen':after_test,'goal_met':bool(eligible),'search':[{'settings':s['settings'],'result':r} for r,s in candidates]}
 folder=root/'data/exports'
 if eligible:
  stamp=utcnow().replace(':','').replace('+','_')
  with service.db.connect() as c,sqlite3.connect(folder/f'before-size-tuning-{stamp}.sqlite3') as b:c.backup(b)
  settings=defaults(service.cfg,'majority','size');settings.update(chosen['settings']);settings.pop('mode',None);settings=validate(service.cfg,settings)
  save(service,{'id':target,'name':'大小策略 · 历史净收益五万达标（待前向验证）','settings':settings})
  final=hb.execute(service,hb.prepare(service,{'strategy_id':target}));report['backtest_run']=final['run_id'];report['updated']=True
 else:report['updated']=False
 (folder/'size-tuning-50000-latest.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
 print(json.dumps({k:v for k,v in report.items() if k not in ('search','coverage')},ensure_ascii=False),flush=True)

if __name__=='__main__':main()
