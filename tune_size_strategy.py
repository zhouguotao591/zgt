"""Expanded retrospective size search; historical fit is not independent validation."""
import copy,json,sqlite3
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
 # Predeclared sweep: fixed original bankroll, base stake, limits and progression.
 for lookback in [2,3,4,5,6,8,10,12,15,20,25,30,40,50,60,80,100]:
  for win_limit,pause in [(5,20),(0,20),(3,10),(6,10)]:
   snap=copy.deepcopy(original);snap['settings'].update(lookback=lookback,win_limit=win_limit,pause_draws=pause)
   result=evaluate(rows,snap);candidates.append((result,snap))
  print(json.dumps({'lookback':lookback,'best_net':max(r['net'] for r,s in candidates)},ensure_ascii=False),flush=True)
 eligible=[(r,s) for r,s in candidates if r['bets']>=300 and r['net']>=10000 and r['net']>baseline['net'] and (r['roi'] or 0)>(baseline['roi'] or 0) and r['max_drawdown']<=baseline['max_drawdown']]
 result,chosen=max(eligible or candidates,key=lambda p:(p[0]['net'],p[0]['roi'] or 0))
 before_test=evaluate(test,original);after_test=evaluate(test,chosen)
 report={'coverage':coverage,'target_id':target,'candidate_count':len(candidates),'method':'Retrospective selection on full three months. Final third was previously inspected and is not an untouched holdout. Requires future validation.','baseline':baseline,'chosen':result,'settings':chosen['settings'],'first_two_thirds':evaluate(train,chosen),'last_third_baseline':before_test,'last_third_chosen':after_test,'goal_met':bool(eligible),'search':[{'settings':s['settings'],'result':r} for r,s in candidates]}
 folder=root/'data/exports'
 if eligible:
  stamp=utcnow().replace(':','').replace('+','_')
  with service.db.connect() as c,sqlite3.connect(folder/f'before-size-tuning-{stamp}.sqlite3') as b:c.backup(b)
  settings=defaults(service.cfg,'majority','size');settings.update(chosen['settings']);settings.pop('mode',None);settings=validate(service.cfg,settings)
  save(service,{'id':target,'name':'大小策略 · 三个月调优达标（待前向验证）','settings':settings})
  final=hb.execute(service,hb.prepare(service,{'strategy_id':target}));report['backtest_run']=final['run_id'];report['updated']=True
 else:report['updated']=False
 (folder/'size-tuning-latest.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
 print(json.dumps({k:v for k,v in report.items() if k not in ('search','coverage')},ensure_ascii=False),flush=True)

if __name__=='__main__':main()
