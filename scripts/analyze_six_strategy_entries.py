"""Focused, read-only six-direction study. Never changes production strategies.

The historical data have already been inspected; the last segment is therefore
a chronological replication segment, NOT a newly unseen prospective experiment.
"""
import os,sys,json,math,csv
from pathlib import Path
from datetime import datetime,timezone,timedelta
import numpy as np
import pandas as pd
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.analyze_strategy_regimes import ro,bars_for,flags_for,gate,metrics,daily_values,uncertainty,CANDIDATES,event_study
from pc28.history_backtest import replay_time_repairs
from pc28.maintenance import block_reason
from pc28.model import settlement
from pc28.trend_risk import DEFAULT,analyze
OUT=ROOT/'data/audits/six-strategy-entries-20261121';OUT.mkdir(exist_ok=True,parents=True)
SIX=[('follow_size','上期大小跟随','size',None),('follow_parity','上期单双跟随','parity',None),('fixed_big','固定买大','size','大'),('fixed_small','固定买小','size','小'),('fixed_odd','固定买单','parity','单'),('fixed_even','固定买双','parity','双')]
EXTRA=['momentum_1h','momentum_4h','reentry_lower_1h','breakout_1h','dual_4h_1h','accuracy_40_20']

def timing_states(frame,entry,stop,known,ends,resume=2,pause=1):
 active=False;safe=risk=0;last_pause=-1e30;states=[];events=[]
 for j,end in enumerate(ends):
  if not known[j]:safe=risk=0;allow=False
  else:
   safe=safe+1 if entry[j] else 0;risk=risk+1 if stop[j] else 0
   if active and risk>=pause:active=False;last_pause=end
   elif not active and not stop[j] and safe>=resume and end-last_pause>=3600:active=True
   allow=active
  if not states or states[-1]!=allow:events.append({'time':datetime.fromtimestamp(float(end),timezone.utc).isoformat(),'action':'resume' if allow else 'pause'})
  states.append(allow)
 indices=np.searchsorted(ends,frame.decision.to_numpy(),side='right')-1
 return np.array([states[i] if i>=0 else False for i in indices]),events

def extra_gate(frame,key,cache):
 if key=='accuracy_40_20':
  # Outcomes are accessed by settlement time, never by the target prediction.
  settled=frame.settled.to_numpy();hits=frame.hit.to_numpy();cum=np.r_[0,np.cumsum(hits)]
  ready=np.searchsorted(settled,frame.decision.to_numpy(),side='right');mask=[];active=False;events=[]
  for j,n in enumerate(ready):
   before=active
   if n<40:active=False
   elif active and (cum[n]-cum[n-20])<=9:active=False
   elif not active and (cum[n]-cum[n-40])>=22:active=True
   mask.append(active)
   if active!=before:events.append({'time':datetime.fromtimestamp(float(frame.decision.iloc[j]),timezone.utc).isoformat(),'action':'resume' if active else 'pause'})
  return np.array(mask,dtype=bool),events
 hours=4 if key=='momentum_4h' else 1
 if hours not in cache:cache[hours]=bars_for(frame,hours)
 b=cache[hours];rule={**DEFAULT,'interval':'4h' if hours==4 else '1h'};status,p=flags_for(b,rule);known=status!='unknown'
 entry=p['up'];stop=p['top']|p['support_break']|p['down']
 if key=='reentry_lower_1h':
  mean=b.close.rolling(20).mean();std=b.close.rolling(20).std(ddof=0);lower=mean-2*std
  entry=p['bottom']|((b.close>lower)&(b.close.shift(1)<lower.shift(1))&(b.close>b.open)).to_numpy()
  stop=(b.close>=mean).to_numpy()|p['support_break']
 elif key=='breakout_1h':entry=p['resistance_break']&p['up'];stop=(b.close<b.close.rolling(20).mean()).to_numpy()|p['top']
 elif key=='dual_4h_1h':
  if 4 not in cache:cache[4]=bars_for(frame,4)
  big=cache[4];big_status,big_p=flags_for(big,{**DEFAULT,'interval':'4h'})
  idx=np.searchsorted(big.end.to_numpy(),b.end.to_numpy(),side='right')-1
  big_safe=np.array([i>=0 and big_status[i]!='unknown' and big_p['up'][i] for i in idx]);big_risk=np.array([i<0 or big_status[i] in ('risk','unknown') for i in idx])
  entry=big_safe&(p['up']|p['bottom']);stop=big_risk|p['top']|p['support_break']|p['down']
 return timing_states(frame,np.asarray(entry),np.asarray(stop),known,b.end.to_numpy(),resume=1 if key in ('reentry_lower_1h','breakout_1h') else 2)

def select_candidate(records,train,validation):
 baseline=records['always'];eligible=[k for k,v in records.items() if k!='always' and v[train]['bets']>=500 and v[train]['bets']>=.2*baseline[train]['bets']]
 if not eligible:return 'always',False
 best=max(eligible,key=lambda k:records[k][train]['roi']);b=baseline[validation];v=records[best][validation]
 passed=v['bets']>=200 and v['bets']>=.2*b['bets'] and v['net']>0 and v['roi']>b['roi'] and v['prediction_hit']>b['prediction_hit'] and v['max_dd']<=b['max_dd']
 return best,bool(passed)

def select_capital_candidate(records,train,validation):
 baseline=records['always'];eligible=[k for k,v in records.items() if v[train]['bets']>=500 and v[train]['bets']>=.2*baseline[train]['bets']]
 best=max(eligible,key=lambda k:records[k][train]['net']) if eligible else 'always';b=baseline[validation];v=records[best][validation]
 passed=best!='always' and v['bets']>=200 and v['net']>0 and v['net']>b['net'] and v['prediction_hit']>b['prediction_hit'] and v['max_dd']<=b['max_dd']
 return best,bool(passed)

def roi_uncertainty(frame,baseline_mask,filtered_mask):
 days=frame.loc[baseline_mask].day;start,end=int(days.min()),int(days.max())
 bp=daily_values(frame,baseline_mask,start,end);fp=daily_values(frame,filtered_mask,start,end)
 stakes=frame.copy();stakes['pnl']=stakes.stake
 bs=daily_values(stakes,baseline_mask,start,end);fs=daily_values(stakes,filtered_mask,start,end)
 n=len(bp)
 if not fs.sum():return {'delta_roi':None,'delta_roi_ci95':None,'p_roi_hac':None}
 rb=bp.sum()/bs.sum();rf=fp.sum()/fs.sum();effect=rf-rb
 influence=(fp-rf*fs)/fs.mean()-(bp-rb*bs)/bs.mean()
 variance=float(np.dot(influence,influence)/n)
 for lag in range(1,5):variance+=2*(1-lag/5)*float(np.dot(influence[lag:],influence[:-lag])/n)
 se=math.sqrt(max(variance,0)/n);p=.5*math.erfc(float(effect)/(se*math.sqrt(2))) if se else (0. if effect>0 else 1.)
 rng=np.random.default_rng(915);starts=rng.integers(0,n,(2000,math.ceil(n/4)));idx=((starts[:,:,None]+np.arange(4)[None,None,:])%n).reshape(2000,-1)[:,:n]
 fsum=fs[idx].sum(axis=1);valid=fsum>0
 values=fp[idx].sum(axis=1)[valid]/fsum[valid]-bp[idx].sum(axis=1)[valid]/bs[idx].sum(axis=1)[valid]
 return {'delta_roi':float(effect),'delta_roi_ci95':[float(x) for x in np.quantile(values,[.025,.975])],'p_roi_hac':p}

def study(frame,identity,split_ids):
 f=frame.sort_values('draw').reset_index(drop=True);f['day']=np.floor(f.decision/86400).astype(int);cache={};masks={};events={};scores={}
 period={'train':(f.draw<split_ids[0]).to_numpy(),'validation':((f.draw>=split_ids[0])&(f.draw<split_ids[1])).to_numpy(),'replication':(f.draw>=split_ids[1]).to_numpy()}
 fold_bounds=np.linspace(0,len(f),11,dtype=int)
 for fold in range(5):
  t=4+fold;period[f'fold{fold}_train']=np.arange(len(f))<fold_bounds[t];period[f'fold{fold}_valid']=(np.arange(len(f))>=fold_bounds[t])&(np.arange(len(f))<fold_bounds[t+1]);period[f'fold{fold}_test']=(np.arange(len(f))>=fold_bounds[t+1])&(np.arange(len(f))<fold_bounds[t+2])
 for key,rule in CANDIDATES:
  if rule is None:mask=np.ones(len(f),dtype=bool);ev=[]
  else:mask,ev,*_=gate(f,rule,cache)
  masks[key]=mask;events[key]=ev
 for key in EXTRA:masks[key],events[key]=extra_gate(f,key,cache)
 # The resampler also contains the final unfinished candle. It can never
 # influence an order mask, and must not appear as a future chart marker.
 for key in events:events[key]=[e for e in events[key] if datetime.fromisoformat(e['time']).timestamp()<=float(f.decision.max())]
 for key,mask in masks.items():scores[key]={p:metrics(f,mask&selected) for p,selected in period.items()}
 winner,passed=select_candidate(scores,'train','validation');base=scores['always']['replication'];candidate=scores[winner]['replication'];test=period['replication'];days=f.loc[test].day
 capital_key,capital_valid=select_capital_candidate(scores,'train','validation')
 capital_initial=identity['initial_capital']
 capital_comparison={'candidate':capital_key,'validation_pass':capital_valid,'initial_capital':capital_initial,'baseline':base,'filtered':scores[capital_key]['replication'],'baseline_capital_return':base['net']/capital_initial,'filtered_capital_return':scores[capital_key]['replication']['net']/capital_initial}
 delta=daily_values(f,test&masks[winner],int(days.min()),int(days.max()))-daily_values(f,test,int(days.min()),int(days.max()));u=uncertainty(delta,seed=312)
 folds=[];walk_mask=np.ones(len(f),dtype=bool);walk_test=np.zeros(len(f),dtype=bool)
 for fold in range(5):
  key,valid=select_candidate(scores,f'fold{fold}_train',f'fold{fold}_valid');chosen=key if valid else 'always';segment=period[f'fold{fold}_test'];walk_mask[segment]=masks[chosen][segment];walk_test|=segment
  folds.append({'fold':fold+1,'training_candidate':key,'validation_pass':valid,'executed_policy':chosen,'baseline':scores['always'][f'fold{fold}_test'],'filtered':scores[chosen][f'fold{fold}_test']})
 selected_daily=daily_values(f,test&masks[winner],int(days.min()),int(days.max()));base_daily=daily_values(f,test,int(days.min()),int(days.max()))
 b=cache[1];_,p=flags_for(b,DEFAULT)
 event_details=[]
 for event in events[winner]:
  detail=dict(event);stamp=datetime.fromisoformat(event['time']).timestamp()
  if winner=='accuracy_40_20':
   known=f.loc[f.settled<=stamp]
   detail.update(previous40_hit=float(known.hit.iloc[-40:].mean()) if len(known)>=40 else None,previous20_hit=float(known.hit.iloc[-20:].mean()) if len(known)>=20 else None)
  else:
   rule=dict(CANDIDATES).get(winner) or {**DEFAULT,'interval':'4h' if winner=='momentum_4h' else '1h'};hours=4 if rule['interval']=='4h' else 1;bb=cache[hours]
   index=int(np.searchsorted(bb.end.to_numpy(),stamp,side='right')-1)
   if index>=0:
    sample=[{'time':idx.isoformat(),**{k:int(row[k]) if k=='orders' else float(row[k]) for k in ('open','high','low','close','orders')}} for idx,row in bb.iloc[max(0,index-rule['lookback']):index+1].iterrows()]
    info=analyze(sample,rule,int(stamp));detail.update(reason=info['reason'],analysis=info,signal_equity=float(bb.close.iloc[index]))
  event_details.append(detail)
 sensitivity=[]
 for delay in (5,15,45):
  shifted=f.copy();shifted['decision']+=delay-15;shifted['settled']+=delay-15
  if winner=='always':mask=np.ones(len(f),dtype=bool)
  elif winner in EXTRA:mask=extra_gate(shifted,winner,{})[0]
  else:mask=gate(shifted,dict(CANDIDATES)[winner],{})[0]
  sensitivity.append({'assumed_delay_seconds':delay,'metrics':metrics(shifted,test&mask)})
 # Keep a bounded series for local visual review, preserving all daily totals.
 curve=[{'day':datetime.fromtimestamp(int(day)*86400,timezone.utc).date().isoformat(),'baseline':float(left),'filtered':float(right)} for day,left,right in zip(range(int(days.min()),int(days.max())+1),np.cumsum(base_daily),np.cumsum(selected_daily))]
 return {**identity,'n':len(f),'start':datetime.fromtimestamp(float(f.decision.iloc[0]),timezone.utc).isoformat(),'end':datetime.fromtimestamp(float(f.settled.iloc[-1]),timezone.utc).isoformat(),'whole_baseline':metrics(f,np.ones(len(f),dtype=bool)),'candidate':winner,'validation_pass':passed,'capital_objective':capital_comparison,'all_candidates':{k:{p:v[p] for p in ('train','validation','replication')} for k,v in scores.items()},'baseline':base,'filtered':candidate,'coverage':candidate['bets']/base['bets'],'uncertainty':u,'roi_uncertainty':roi_uncertainty(f,test,test&masks[winner]),'walkforward':{'folds':folds,'baseline':metrics(f,walk_test),'filtered':metrics(f,walk_test&walk_mask)},'events':event_details,'event_patterns':event_study(f,b,p,int(np.flatnonzero(test)[0])),'latency_sensitivity':sensitivity,'daily_curve':curve}

def main():
 if os.name=='nt':
  import ctypes
  ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(),0x4000)
 cfg=json.loads((ROOT/'config.json').read_text(encoding='utf8'));prior=json.loads((ROOT/'data/audits/strategy-regimes-20261121/study.json').read_text(encoding='utf8'));cutoff=prior['audit']['last']['id'];splits=prior['audit']['splits']
 c=ro(ROOT/'data/pc28.sqlite3');draws=[dict(r) for r in c.execute("SELECT id,draw_utc,source_time,source,total,size,parity FROM draws WHERE id<=? AND verification='full_keno' AND NOT EXISTS(SELECT 1 FROM conflicts x WHERE x.draw_id=draws.id AND x.resolved=0) ORDER BY id",(cutoff,))];c.close()
 for r in draws:r['_time']=datetime.fromisoformat(r['draw_utc'])
 repairs=replay_time_repairs(draws);assert all(b['_time']>=a['_time'] for a,b in zip(draws,draws[1:]))
 p=ro(ROOT/'data/pc28-algorithm-paper.sqlite3');odds={r['id']:json.loads(r['value']) for r in p.execute('SELECT * FROM odds')};odds_by_draw={r['id']:odds[r['odds_id']] for r in p.execute('SELECT id,odds_id FROM sources WHERE id<=?',(cutoff,))};p.close()
 guard=cfg['collector'].get('maintenance_guard_enabled',True);eligible=[];excluded={}
 for previous,target in zip(draws,draws[1:]):
  decision=previous['_time']+timedelta(seconds=15)
  reason='期号不连续' if previous['id']+1!=target['id'] else block_reason(previous,decision,guard,target['_time'])
  if reason:excluded[reason]=excluded.get(reason,0)+1;continue
  eligible.append((previous,target,decision))
 results=[]
 for key,name,axis,direction in SIX:
  matching=[r for r in prior['audit']['roster'] if r['settings'].get('strategy_type')==('single' if direction else 'previous') and r['settings'].get('axis')==axis and r['settings'].get('direction')==direction]
  assert len(matching)==1,'Expected a single current strategy definition for this direction'
  initial_capital=float(matching[0]['settings']['initial_balance'])
  records=[]
  for prev,target,decision in eligible:
   side=direction or prev[axis];hit=side==target[axis];paid=settlement(10000,side,target['total'],hit,odds_by_draw.get(target['id'],cfg['odds']))
   records.append({'draw':target['id'],'decision':decision.timestamp(),'settled':target['_time'].timestamp()+15,'stake':100.,'pnl':paid['pnl']/100,'hit':int(hit),'outcome':paid['outcome'],'direction':side})
  f=pd.DataFrame(records);results.append(study(f,{'id':key,'name':name,'axis':axis,'fixed_direction':direction,'initial_capital':initial_capital},splits));print(json.dumps({'done':len(results),'total':6,'id':key}),flush=True)
 ranked=sorted(results,key=lambda r:r['uncertainty'].get('p_hac') if r['uncertainty'].get('p_hac') is not None else 1);q=1.
 for i in range(len(ranked)-1,-1,-1):q=min(q,(ranked[i]['uncertainty'].get('p_hac') or 0)*len(ranked)/(i+1));ranked[i]['uncertainty']['q_bh_six']=q
 ranked_roi=sorted(results,key=lambda r:r['roi_uncertainty'].get('p_roi_hac') if r['roi_uncertainty'].get('p_roi_hac') is not None else 1);q=1.
 for i in range(len(ranked_roi)-1,-1,-1):
  value=ranked_roi[i]['roi_uncertainty'].get('p_roi_hac');q=min(q,(value if value is not None else 1)*len(ranked_roi)/(i+1));ranked_roi[i]['roi_uncertainty']['q_roi_bh_six']=q
 payload={'created':datetime.now(timezone.utc).isoformat(),'cutoff':cutoff,'splits':splits,'raw_draws':len(draws),'eligible_forecasts':len(eligible),'time_repairs':repairs,'maintenance_guard':guard,'excluded':excluded,'decision_assumption':'上一期开奖后15秒生成预测，目标开奖后15秒假设结算可用；不是实际发送/接收回执。','historical_reuse_warning':'该数据已在前轮分析中查看，本轮末20%是时间顺序复核段，不是真正未见过的新数据。','stake_assumption':'统一100U独立预测、不加码、不叠加账户资金止损；用于分离方向与择时效果，非真实会员收益。','candidate_count':len(CANDIDATES)+len(EXTRA),'results':results}
 (OUT/'study.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf8')
 print(json.dumps({'complete':True,'file':str(OUT/'study.json')}),flush=True)

if __name__=='__main__':main()
