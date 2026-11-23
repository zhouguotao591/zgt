"""Read-only reversal-pattern research on the six frozen basic strategies."""
import os,sys,json,math
from pathlib import Path
from datetime import datetime,timedelta,timezone
import numpy as np
import pandas as pd
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.analyze_strategy_regimes import ro,bars_for,metrics,uncertainty,daily_values
from scripts.analyze_six_strategy_entries import SIX,select_capital_candidate,select_candidate,roi_uncertainty
from scripts.reversal_pattern_rules import detect,policy,FAMILIES
from pc28.history_backtest import replay_time_repairs
from pc28.maintenance import block_reason
from pc28.model import settlement
OUT=ROOT/'data/audits/candle-reversals-20261121';OUT.mkdir(exist_ok=True,parents=True)

def frames():
 prior=json.loads((ROOT/'data/audits/six-strategy-entries-20261121/study.json').read_text(encoding='utf8'));cutoff=prior['cutoff'];cfg=json.loads((ROOT/'config.json').read_text(encoding='utf8'))
 saved=OUT/'frames.npz'
 if saved.exists():
  data=np.load(saved);assert int(data['cutoff'])==cutoff
  return prior,[(identity,pd.DataFrame({'draw':data['draw'],'decision':data['decision'],'settled':data['settled'],'pnl':data['pnl'][i],'hit':data['hit'][i],'stake':np.full(len(data['draw']),100.)})) for i,identity in enumerate([{'id':r['id'],'name':r['name'],'initial_capital':r['initial_capital']} for r in prior['results']])]
 c=ro(ROOT/'data/pc28.sqlite3');draws=[dict(r) for r in c.execute("SELECT id,draw_utc,source_time,source,total,size,parity FROM draws WHERE id<=? AND verification='full_keno' AND NOT EXISTS(SELECT 1 FROM conflicts x WHERE x.draw_id=draws.id AND x.resolved=0) ORDER BY id",(cutoff,))];c.close()
 for r in draws:r['_time']=datetime.fromisoformat(r['draw_utc'])
 repairs=replay_time_repairs(draws);assert len(repairs)==10
 p=ro(ROOT/'data/pc28-algorithm-paper.sqlite3');odds={r['id']:json.loads(r['value']) for r in p.execute('SELECT * FROM odds')};by_draw={r['id']:odds[r['odds_id']] for r in p.execute('SELECT id,odds_id FROM sources WHERE id<=?',(cutoff,))};p.close()
 eligible=[]
 for a,b in zip(draws,draws[1:]):
  decision=a['_time']+timedelta(seconds=15)
  if a['id']+1==b['id'] and not block_reason(a,decision,cfg['collector'].get('maintenance_guard_enabled',True),b['_time']):eligible.append((a,b,decision))
 output=[]
 for key,name,axis,side in SIX:
  old=next(r for r in prior['results'] if r['id']==key);records=[]
  for a,b,t in eligible:
   direction=side or a[axis];hit=direction==b[axis];paid=settlement(10000,direction,b['total'],hit,by_draw.get(b['id'],cfg['odds']))
   records.append({'draw':b['id'],'decision':t.timestamp(),'settled':b['_time'].timestamp()+15,'pnl':paid['pnl']/100,'hit':int(hit),'stake':100.})
  output.append(({'id':key,'name':name,'initial_capital':old['initial_capital']},pd.DataFrame(records)))
 np.savez_compressed(saved,cutoff=cutoff,draw=output[0][1].draw.to_numpy(),decision=output[0][1].decision.to_numpy(),settled=output[0][1].settled.to_numpy(),pnl=np.stack([f.pnl.to_numpy() for _,f in output]),hit=np.stack([f.hit.to_numpy() for _,f in output]))
 return prior,output

def hac_p(effect,values):
 n=len(values);v=values-np.mean(values);variance=float(np.dot(v,v)/n)
 for k in range(1,min(4,n-1)+1):variance+=2*(1-k/5)*float(np.dot(v[k:],v[:-k])/n)
 se=math.sqrt(max(0,variance)/n)
 return .5*math.erfc(effect/(se*math.sqrt(2))) if se else (0. if effect>0 else 1.)

def diagnostics(f,base_mask,keep_mask):
 a=f.loc[base_mask];start,end=int(a.day.min()),int(a.day.max());n=end-start+1;day=f.day.to_numpy()-start
 def sums(mask,values):return np.bincount(day[mask],weights=np.asarray(values)[mask],minlength=n)[:n]
 bp=sums(base_mask,f.pnl);fp=sums(keep_mask,f.pnl);bn=sums(base_mask,np.ones(len(f)));fn=sums(keep_mask,np.ones(len(f)));bh=sums(base_mask,f.hit);fh=sums(keep_mask,f.hit)
 if fn.sum()==0:return {'p_net':1.,'p_roi':1.,'p_hit':1.,'p_joint_capital':1.,'p_joint_efficiency':1.}
 hit_b=bh.sum()/bn.sum();hit_f=fh.sum()/fn.sum();hit_influence=(fh-hit_f*fn)/fn.mean()-(bh-hit_b*bn)/bn.mean()
 roi_b=bp.sum()/(bn.sum()*100);roi_f=fp.sum()/(fn.sum()*100);roi_influence=(fp-roi_f*fn*100)/(fn.mean()*100)-(bp-roi_b*bn*100)/(bn.mean()*100)
 pnet=hac_p(float((fp-bp).mean()),fp-bp);phit=hac_p(float(hit_f-hit_b),hit_influence);proi=hac_p(float(roi_f-roi_b),roi_influence)
 return {'p_net':pnet,'p_roi':proi,'p_hit':phit,'p_joint_capital':max(pnet,phit),'p_joint_efficiency':max(proi,phit)}

def event_outcomes(f,b,features,split_ids):
 decisions=f.decision.to_numpy();pnl=f.pnl.to_numpy();hits=f.hit.to_numpy();cum=np.r_[0,np.cumsum(pnl)];hc=np.r_[0,np.cumsum(hits)];results={}
 for key,flag in features['patterns'].items():
  if key.endswith('union'):continue
  usable=flag&features['known']&(b.end.to_numpy()<=decisions[-1]);idx=np.searchsorted(decisions,b.end.to_numpy()[usable],side='left');record={}
  for phase,low,high in [('train',-1,split_ids[0]),('validation',split_ids[0],split_ids[1]),('replication',split_ids[1],float('inf'))]:
   positions=[int(i) for i in idx if i<len(f) and low<=f.draw.iloc[i]<high];phase_data={}
   for horizon in (5,20):
    selected=[];last=-horizon
    for i in positions:
     if i+horizon>len(f):continue
     if not f.draw.iloc[i+horizon-1]<high:continue
     if i>=last+horizon:selected.append(i);last=i
    if selected:
     j=np.array(selected);pay=cum[j+horizon]-cum[j];wins=(hc[j+horizon]-hc[j])/horizon
     phase_data[str(horizon)]={'events':len(j),'mean_net':float(pay.mean()),'roi':float(pay.sum()/(len(j)*horizon*100)),'hit':float(wins.mean())}
    else:phase_data[str(horizon)]={'events':0}
   record[phase]=phase_data
  record['raw_confirmed_count']=int(usable.sum());results[key]=record
 return results

def study(identity,f,splits):
 f=f.copy();f['day']=np.floor(f.decision/86400).astype(int);positions=np.arange(len(f));periods={'train':(f.draw<splits[0]).to_numpy(),'validation':((f.draw>=splits[0])&(f.draw<splits[1])).to_numpy(),'replication':(f.draw>=splits[1]).to_numpy()};bounds=np.linspace(0,len(f),11,dtype=int)
 for i in range(5):
  j=4+i;periods[f'f{i}_train']=positions<bounds[j];periods[f'f{i}_valid']=(positions>=bounds[j])&(positions<bounds[j+1]);periods[f'f{i}_test']=(positions>=bounds[j+1])&(positions<bounds[j+2])
 masks={'always':np.ones(len(f),dtype=bool)};catalog={};pattern_results={}
 for hours in (1,4):
  b=bars_for(f,hours);features=detect(b);pattern_results[str(hours)+'h']=event_outcomes(f,b,features,splits)
  for family in FAMILIES:
   for mode in ('raw','level','confirm'):
    key=f'{hours}h.{family}.{mode}';mask,_=policy(f,b,features,family,mode);masks[key]=mask;catalog[key]={'hours':hours,'family':family,'mode':mode}
 scores={key:{phase:metrics(f,mask&select) for phase,select in periods.items()} for key,mask in masks.items()}
 records={key:{phase:value[phase] for phase in ('train','validation','replication')} for key,value in scores.items()}
 test=periods['replication'];base=records['always']['replication'];diag={key:diagnostics(f,test,test&mask) for key,mask in masks.items() if key!='always'}
 cap,cap_valid=select_capital_candidate(scores,'train','validation');eff,eff_valid=select_candidate(scores,'train','validation');selected={}
 for purpose,key,valid in [('capital',cap,cap_valid),('efficiency',eff,eff_valid)]:
  kept=test&masks[key];days=f.loc[test].day
  delta=daily_values(f,kept,int(days.min()),int(days.max()))-daily_values(f,test,int(days.min()),int(days.max()))
  info={'key':key,'validation_pass':valid,'metrics':records[key]['replication'],'net_uncertainty':uncertainty(delta),'roi_uncertainty':roi_uncertainty(f,test,kept)}
  if key!='always':
   spec=catalog[key];b=bars_for(f,spec['hours']);_,events=policy(f,b,detect(b),spec['family'],spec['mode'],True);info['events']=events
  else:info['events']=[]
  selected[purpose]=info
 folds=[];walk=np.ones(len(f),dtype=bool);walk_test=np.zeros(len(f),dtype=bool)
 for i in range(5):
  key,valid=select_capital_candidate(scores,f'f{i}_train',f'f{i}_valid');applied=key if valid else 'always';segment=periods[f'f{i}_test'];walk[segment]=masks[applied][segment];walk_test|=segment;folds.append({'fold':i+1,'candidate':key,'validation_pass':valid,'executed':applied,'baseline':scores['always'][f'f{i}_test'],'filtered':scores[applied][f'f{i}_test']})
 return {**identity,'n':len(f),'baseline':base,'models':records,'catalog':catalog,'diagnostics':diag,'selected':selected,'shape_outcomes':pattern_results,'walkforward':{'folds':folds,'baseline':metrics(f,walk_test),'filtered':metrics(f,walk_test&walk)},'confirmed_events':sum(len(z['events']) for z in selected.values())}

def main():
 if os.name=='nt':
  import ctypes
  ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(),0x4000)
 prior,data=frames();results=[]
 for identity,f in data:results.append(study(identity,f,prior['splits']));print(json.dumps({'done':len(results),'id':identity['id']}),flush=True)
 tests=[]
 for r in results:
  for key,value in r['diagnostics'].items():
   for objective,p in [('capital',value['p_joint_capital']),('efficiency',value['p_joint_efficiency'])]:tests.append((p,r,key,objective))
 tests.sort(key=lambda t:t[0]);last=1.;count=len(tests)
 for i in range(count-1,-1,-1):
  p,r,key,objective=tests[i];last=min(last,p*count/(i+1));r['diagnostics'][key]['q_joint_'+objective]=last
 harmonic=sum(1/i for i in range(1,count+1))
 for r in results:
  for value in r['diagnostics'].values():
   for objective in ('capital','efficiency'):value['q_by_joint_'+objective]=min(1.,value['q_joint_'+objective]*harmonic)
 payload={'created':datetime.now(timezone.utc).isoformat(),'cutoff':prior['cutoff'],'forecasts_per_strategy':prior['eligible_forecasts'],'splits':prior['splits'],'models_per_strategy':25,'multiple_comparisons':count,'notes':['Frozen data were previously inspected. Historical replication is not a new prospective trial.','Three-bar fractals are confirmed on the right bar close. Five-bar fractals are diagnostic only.','Stars without a body gap are explicitly gapless adaptations. Strict gap stars are counted separately.','All P&L is independent fixed-100U signal simulation, without real fills or account stop limits.'],'results':results}
 (OUT/'study.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf8');print(json.dumps({'complete':True,'multiple_comparisons':count}),flush=True)

if __name__=='__main__':main()
