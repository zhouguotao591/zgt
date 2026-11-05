"""Sequential, auditable search for remaining research strategies; fixed risk budgets."""
import copy,json,random,sqlite3
from pathlib import Path
from types import SimpleNamespace
from threading import RLock
from pc28.db import DB
from pc28.model import utcnow
from pc28 import history_backtest as hb
from pc28 import custom_strategy,follow_strategy

def main():
 root=Path(__file__).resolve().parent;folder=root/'data/exports'
 service=SimpleNamespace(root=root,cfg=json.loads((root/'config.json').read_text('utf8')),db=DB(root/'data/pc28.sqlite3'),lock=RLock())
 previous=json.loads((folder/'strategy-tuning-latest.json').read_text('utf8'))
 sources=['parity','custom_2f829b7ffed54043aa1919957075151a','custom_911375fccdc646c1bab0cd6c09a525f2','custom_8793040133384c3e98a2f829120c435a','custom_8793e46b6c3644b0b51da3233b8355f2']
 rows,coverage=hb.source_window(service.db.query('SELECT * FROM draws ORDER BY id'),{r['draw_id'] for r in service.db.query('SELECT draw_id FROM conflicts WHERE resolved=0')},utcnow())
 boundary=rows[0]['_time']+(rows[-1]['_time']-rows[0]['_time'])*2/3
 test=[r for r in rows if r['_time']>=boundary]
 # Exact memoization of repeated immutable historical inputs, shared between candidates.
 original_guard=hb.maintenance.block_reason;guard_cache={}
 def cached_guard(prev,decision,enabled,target_time):
  key=(prev['id'],decision,enabled,target_time)
  if key not in guard_cache:guard_cache[key]=original_guard(prev,decision,enabled,target_time)
  return guard_cache[key]
 hb.maintenance.block_reason=cached_guard
 original_hourly=hb.hourly_signal;hour_cache={}
 def cached_hourly(history,hour,settings,known_at):
  key=(history[-1]['id'],hour,settings['axis'],settings['observe_hours'],settings['hour_difference'],known_at)
  if key not in hour_cache:hour_cache[key]=original_hourly(history,hour,settings,known_at)
  return hour_cache[key]
 hb.hourly_signal=cached_hourly
 stamp=utcnow().replace(':','').replace('+','_')
 with service.db.connect() as c,sqlite3.connect(folder/f'before-remaining-tuning-{stamp}.sqlite3') as backup:c.backup(backup)
 report={'coverage':coverage,'method':'Retrospective historical search, not untouched holdout; last third separately restarts bankroll and warmup. Fixed base stake, initial bankroll and risk limits.','strategies':[]}
 def evaluate(part,snapshot):
  result,_=hb.replay(part,snapshot)
  return {k:v for k,v in result.items() if k!='curve'}
 for number,source in enumerate(sources):
  entry=next(s for s in previous['strategies'] if s['source']==source);target=entry['list_id'];snap=hb.selection(service,target);saved=copy.deepcopy(snap['settings']);kind=snap['kind']
  snap['maintenance_guard']=service.cfg['collector'].get('maintenance_guard_enabled',True)
  if kind=='reversal':snap['settings']['fixed']['direction']='小' if '买小' in entry['name'] else '大'
  baseline=evaluate(rows,snap);rng=random.Random(2026110200+number);plans=[]
  if kind=='reversal':
   for threshold in [2,4,6,8,10,12,14,16]:
    for bets in [1,2,3,4,5,8,15,21]:
     value=copy.deepcopy(snap['settings']);value['fixed'].update(threshold=threshold,max_bets=bets);plans.append(value)
   for _ in range(128):
    value=copy.deepcopy(snap['settings']);value['fixed'].update(threshold=rng.choice([2,4,6,8,10,12,14]),max_bets=rng.choice([1,2,3,4,5,8,15]),max_losses=rng.choice([1,2,3]),multiplier_hundredths=rng.choice([100,125,150,175,200]));value.update(win_limit=rng.choice([0,3,4,5,6,8]),pause_draws=rng.choice([5,10,20,40,80,120]));plans.append(value)
  elif kind=='majority':
   for lookback in [2,4,6,10,15,20,30,40,60,80,100,150,200,300]:
    for limit,pause in [(0,20),(3,20),(5,20),(6,80)]:
     value=copy.deepcopy(snap['settings']);value.update(lookback=lookback,win_limit=limit,pause_draws=pause);plans.append(value)
   for _ in range(136):
    value=copy.deepcopy(snap['settings']);value.update(lookback=rng.randint(2,300),win_limit=rng.choice([0,2,3,4,5,6,8]),pause_draws=rng.choice([5,10,20,40,80,120]),loss_increment_cents=rng.choice([0,5000,10000]),max_losses=rng.choice([1,2,3]));plans.append(value)
  else:
   for observe in [1,2,3,4]:
    for difference in [1,3,5,7]:
     for follow in [1,2,3,4]:
      value=copy.deepcopy(snap['settings']);value.update(observe_hours=observe,hour_difference=difference,follow_hours=follow);plans.append(value)
   for _ in range(64):
    value=copy.deepcopy(snap['settings']);value.update(observe_hours=rng.choice([1,2,3,4,6]),hour_difference=rng.choice([1,2,3,4,5,6,7,8]),follow_hours=rng.choice([1,2,3,4,6]),win_limit=rng.choice([0,3,5,6,8]),pause_draws=rng.choice([10,20,40,80]));plans.append(value)
  seen=set();results=[]
  for index,value in enumerate(plans):
   encoded=json.dumps(value,sort_keys=True)
   if encoded in seen:continue
   seen.add(encoded);candidate=copy.deepcopy(snap);candidate['settings']=value
   result=evaluate(rows,candidate);results.append((result,candidate))
   if index%16==0:print(json.dumps({'strategy':entry['name'],'tested':len(results),'best_net':max(r['net'] for r,s in results)},ensure_ascii=False),flush=True)
  improving=[(r,s) for r,s in results if r['bets']>=100 and r['net']>baseline['net'] and (r['roi'] or 0)>(baseline['roi'] or 0) and r['max_drawdown']<=snap['settings']['max_drawdown']]
  achieved=[p for p in improving if p[0]['net']>=50000]
  best,chosen=max(achieved or improving or [(baseline,snap)],key=lambda p:(p[0]['net'],p[0]['roi'] or 0))
  segment=evaluate(test,chosen);segment_before=evaluate(test,snap)
  name=entry['name']+(' · 历史净收益五万达标（待前向验证）' if best['net']>=50000 else ' · 参数研究未达五万')
  value=chosen['settings']
  if kind=='reversal':
   allowed=('mode','initial_balance','daily_loss_limit','max_drawdown','max_stake','round_restart','fixed','win_limit','pause_draws')
   value=custom_strategy.validate_settings(service.cfg,{k:v for k,v in value.items() if k in allowed});custom_strategy.save(service,{'id':target,'name':name,'settings':value})
  else:
   value={**follow_strategy.defaults(service.cfg,kind,snap['settings']['axis']),**value};follow_strategy.save(service,{'id':target,'name':name,'settings':value})
  final=hb.execute(service,hb.prepare(service,{'strategy_id':target}))
  record={'name':entry['name'],'target_id':target,'kind':kind,'saved_before':saved,'corrected_baseline_settings':snap['settings'],'baseline':baseline,'chosen':best,'saved_settings':value,'last_third':segment,'last_third_baseline':segment_before,'goal_met_in_search':best['net']>=50000,'final_net':final['net'],'final_roi':final['roi'],'backtest_run':final['run_id'],'candidate_count':len(results),'search':[{'result':r,'settings':s['settings']} for r,s in results]}
  report['strategies'].append(record);(folder/'remaining-tuning-50000-latest.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
  print(json.dumps({'completed':entry['name'],'net':final['net'],'roi':final['roi'],'last_third_net':segment['net']},ensure_ascii=False),flush=True)

if __name__=='__main__':main()
