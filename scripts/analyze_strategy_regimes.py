"""Read-only, chronological regime study. No Service initialization or production writes.

Run with the production venv, preferably with BELOW_NORMAL_PRIORITY_CLASS on Windows.
Inputs are frozen to a source cutoff; the untouched last 20% is evaluation only.
"""
import os,sys,json,sqlite3,math,time,hashlib,csv
from pathlib import Path
from datetime import datetime,timezone
import numpy as np
import pandas as pd
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from pc28.trend_risk import DEFAULT,analyze,transition
from pc28.algorithm_hall import BY_ID
OUT=ROOT/'data/audits/strategy-regimes-20261121';OUT.mkdir(exist_ok=True,parents=True)
CANDIDATES=[('always',None),('1h20_p2r2',{**DEFAULT,'mode':'auto'}),('4h20_p2r2',{**DEFAULT,'mode':'auto','interval':'4h'}),('1h20_p1r2',{**DEFAULT,'mode':'auto','pause_confirm':1}),('1h40_p2r3',{**DEFAULT,'mode':'auto','lookback':40,'resume_confirm':3}),('4h40_p2r3',{**DEFAULT,'mode':'auto','interval':'4h','lookback':40,'resume_confirm':3})]
PATTERNS=['top','bottom','up','down','support_break','resistance_break','boll_upper_break','boll_lower_break','bullish_engulf','bearish_engulf','hammer','shooting_star']

def ro(path):
 c=sqlite3.connect('file:'+path.as_posix()+'?mode=ro',uri=True,timeout=10);c.row_factory=sqlite3.Row;c.execute('PRAGMA query_only=ON');return c

def bars_for(frame,hours):
 # All price values are cumulative fixed-100U signal P&L, not raw draw sums.
 f=frame.sort_values(['settled','draw']).copy();f['equity']=f.pnl.cumsum();f['open']=f.equity-f.pnl
 f['high']=f[['open','equity']].max(axis=1);f['low']=f[['open','equity']].min(axis=1)
 g=f.set_index(pd.to_datetime(f.settled,unit='s',utc=True)).resample(str(hours)+'h',origin='epoch')
 b=pd.DataFrame({'open':g.open.first(),'high':g.high.max(),'low':g.low.min(),'close':g.equity.last(),'orders':g.draw.count()})
 b['close']=b.close.ffill().fillna(0)
 for key in ('open','high','low'):b[key]=b[key].fillna(b.close)
 b['end']=b.index.astype('datetime64[ns, UTC]').astype('int64')/1e9+hours*3600
 return b

def flags_for(b,rule):
 n=rule['lookback'];close=b.close;mean=close.rolling(n).mean();std=close.rolling(n).std(ddof=0)
 index=pd.Series(np.arange(len(b),dtype=float),index=b.index)
 slope=((index*close).rolling(n).sum()-(index-(n-1)/2)*close.rolling(n).sum())/(n*(n*n-1)/12)
 normalized=(slope/std.replace(0,np.nan)).fillna(0)
 prior=b.shift(1);pm=mean.shift(1);ps=std.shift(1);support=b.low.shift(1).rolling(n).min();resistance=b.high.shift(1).rolling(n).max()
 body=(close-b.open).abs();upper=b.high-pd.concat([b.open,close],axis=1).max(axis=1);lower=pd.concat([b.open,close],axis=1).min(axis=1)-b.low;eps=(std*.05).clip(lower=1e-8)
 bearish=(close<b.open)&(prior.close>prior.open)&(close<=prior.open)&(b.open>=prior.close)
 bullish=(close>b.open)&(prior.close<prior.open)&(close>=prior.open)&(b.open<=prior.close)
 hammer=lower>=2*np.maximum(body,eps);star=upper>=2*np.maximum(body,eps)
 top=(bearish|star)&(np.maximum(b.high,prior.high)>=np.minimum(resistance,pm+2*ps)-eps)&(close<prior.close)
 bottom=(bullish|hammer)&(np.minimum(b.low,prior.low)<=np.maximum(support,pm-2*ps)+eps)&(close>prior.close)
 up=(normalized>=rule['slope_threshold'])&(close>mean);down=(normalized<=-rule['slope_threshold'])&(close<mean)
 broken=close<support-eps;breakout=close>resistance+eps
 last_active=index.where(b.orders>0).ffill()
 known=(index>=n)&(b.orders.rolling(n).sum()>=n)&((b.orders>0).rolling(n).sum()>=5)&((index-last_active)<=rule['max_stale_bars'])
 risk=top|broken|(down&~bottom);safe=~risk&(bottom|up)
 status=np.where(~known,'unknown',np.where(risk,'risk',np.where(safe,'safe','neutral')))
 patterns={'top':top,'bottom':bottom,'up':up,'down':down,'support_break':broken,'resistance_break':breakout,'boll_upper_break':close>mean+2*std,'boll_lower_break':close<mean-2*std,'bullish_engulf':bullish,'bearish_engulf':bearish,'hammer':hammer,'shooting_star':star}
 return status,{k:(v&known).to_numpy() for k,v in patterns.items()}

def gate(frame,rule,cache):
 hours=1 if rule['interval']=='1h' else 4
 if hours not in cache:cache[hours]=bars_for(frame,hours)
 b=cache[hours]
 statuses,patterns=flags_for(b,rule);allowed=[];state={};events=[];was=None
 for j,(end,status) in enumerate(zip(b.end,statuses)):
  end=int(end);new=transition(state,{'status':status},rule,end);state={**new,'closed_at':end}
  allow=not new['blocked'] and (status!='unknown' or rule['unknown_policy']=='allow')
  allowed.append(allow)
  if was!=allow:events.append({'time':datetime.fromtimestamp(end,timezone.utc).isoformat(),'action':'resume' if allow else 'pause','status':str(status)})
  was=allow
 indices=np.searchsorted(b.end.to_numpy(),frame.decision.to_numpy(),side='right')-1
 mask=np.array([allowed[i] if i>=0 else rule['unknown_policy']=='allow' for i in indices],dtype=bool)
 return mask,events,b,statuses,patterns

def metrics(f,mask):
 a=f.loc[mask];n=len(a)
 if not n:return {'bets':0,'net':0.,'stake':0.,'roi':None,'prediction_hit':None,'profit_win':None,'refunds':0,'profit_factor':None,'max_dd':0.,'days':0}
 pnl=a.pnl.to_numpy();equity=np.cumsum(pnl);dd=np.maximum.accumulate(np.r_[0,equity])[1:]-equity
 gains=float(pnl[pnl>0].sum());loss=-float(pnl[pnl<0].sum());stake=float(a.stake.sum())
 return {'bets':n,'net':float(pnl.sum()),'stake':stake,'roi':float(pnl.sum()/stake),'prediction_hit':float(a.hit.mean()),'profit_win':float((pnl>0).mean()),'refunds':int((pnl==0).sum()),'profit_factor':gains/loss if loss else None,'max_dd':float(dd.max()),'days':int(a.day.nunique())}

def daily_values(f,mask,start_day,end_day):
 days=np.arange(start_day,end_day+1);out=np.zeros(len(days));a=f.loc[mask].groupby('day').pnl.sum();idx=a.index.to_numpy(dtype=int)-start_day;valid=(idx>=0)&(idx<len(out));out[idx[valid]]=a.to_numpy()[valid];return out

def uncertainty(delta,seed=123):
 n=len(delta)
 if n<30:return {'days':n,'net_delta_ci95':None,'p_hac':None}
 centered=delta-delta.mean();lag=min(4,n//4);v=float(np.dot(centered,centered)/n)
 for k in range(1,lag+1):v+=2*(1-k/(lag+1))*float(np.dot(centered[k:],centered[:-k])/n)
 se=math.sqrt(max(v,0)/n);p=.5*math.erfc(float(delta.mean())/(se*math.sqrt(2))) if se else (0. if delta.mean()>0 else 1.)
 rng=np.random.default_rng(seed);length=4;starts=rng.integers(0,n,(1500,math.ceil(n/length)));idx=(starts[:,:,None]+np.arange(length)[None,None,:])%n
 draws=delta[idx.reshape(1500,-1)[:,:n]].sum(axis=1)
 return {'days':n,'net_delta_ci95':[float(x) for x in np.quantile(draws,[.025,.975])],'p_hac':p}

def event_study(f,b,patterns,test_start):
 result={};ends=b.end.to_numpy();decision=f.decision.to_numpy();pnl=f.pnl.to_numpy();hit=f.hit.to_numpy();cum=np.r_[0,np.cumsum(pnl)];hc=np.r_[0,np.cumsum(hit)]
 for key,flag in patterns.items():
  positions=np.searchsorted(decision,ends[flag],side='left');positions=positions[(positions>=test_start)&(positions+20<=len(f))]
  selected=[];last=-100
  for pos in positions:
   if pos>=last+20:selected.append(int(pos));last=int(pos)
  if not selected:result[key]={'events':0};continue
  a=np.array(selected);net=cum[a+20]-cum[a];hits=(hc[a+20]-hc[a])/20
  result[key]={'events':len(a),'next20_mean_net':float(net.mean()),'next20_hit':float(hits.mean()),'next20_positive_share':float((net>0).mean())}
 return result

def study(identity,f,split_ids,source,actual=None):
 f=f.sort_values('draw').reset_index(drop=True);f['day']=np.floor(f.decision/86400).astype(int)
 assert f.decision.min()>1700000000,'Timestamp precision must be seconds since Unix epoch'
 train=(f.draw<split_ids[0]).to_numpy();valid=((f.draw>=split_ids[0])&(f.draw<split_ids[1])).to_numpy();test=(f.draw>=split_ids[1]).to_numpy()
 # Recent live ledgers use their own chronological thirds; they do not pretend
 # to have existed during the long-history test interval.
 if source=='live_signal':
  a=int(len(f)*.6);b=int(len(f)*.8);train=np.arange(len(f))<a;valid=(np.arange(len(f))>=a)&(np.arange(len(f))<b);test=np.arange(len(f))>=b
 periods={'train':train,'validation':valid,'test':test};cache={};candidates={};masks={};event_map={};pattern_data=None;checks=[]
 for key,rule in CANDIDATES:
  if rule is None:mask=np.ones(len(f),dtype=bool);events=[]
  else:
   mask,events,b,statuses,patterns=gate(f,rule,cache)
   if key=='1h20_p2r2':
    pattern_data=(b,patterns)
    # Cross-check the vectorized calculations against the production rule.
    for j in np.linspace(min(50,len(b)-1),len(b)-1,min(5,len(b)),dtype=int):
     data=[{'time':idx.isoformat(),**{k:float(row[k]) if k!='orders' else int(row[k]) for k in ('open','high','low','close','orders')}} for idx,row in b.iloc[max(0,j-130):j+1].iterrows()]
     observed=analyze(data,rule,int(b.end.iloc[j]))['status'];checks.append({'bar':int(j),'production':observed,'vectorized':str(statuses[j]),'match':observed==statuses[j]})
  masks[key]=mask;event_map[key]=events;candidates[key]={p:metrics(f,mask&select) for p,select in periods.items()}
 base=candidates['always']
 # A single winner is selected on training only; validation is a veto.
 eligible=[key for key in masks if key!='always' and candidates[key]['train']['bets']>=500 and candidates[key]['train']['bets']>=.2*base['train']['bets']]
 winner=max(eligible,key=lambda key:candidates[key]['train']['roi']) if eligible else 'always'
 chosen=candidates[winner];v=chosen['validation'];bv=base['validation']
 validated=winner!='always' and v['bets']>=200 and v['net']>0 and v['net']>bv['net'] and v['prediction_hit']>bv['prediction_hit']
 # Report the training-selected candidate even when validation vetoes execution.
 tm=chosen['test'];bt=base['test'];test_rows=f.loc[test]
 if len(test_rows):
  start_day=int(test_rows.day.min());end_day=int(test_rows.day.max());base_daily=daily_values(f,test,start_day,end_day);cand_daily=daily_values(f,test&masks[winner],start_day,end_day);u=uncertainty(cand_daily-base_daily)
 else:u={'days':0,'net_delta_ci95':None,'p_hac':None}
 delta={k:(tm[k]-bt[k]) if tm[k] is not None and bt[k] is not None else None for k in ('net','roi','prediction_hit','profit_win','max_dd')}
 item={**identity,'source':source,'n':len(f),'start':datetime.fromtimestamp(float(f.decision.min()),timezone.utc).isoformat(),'end':datetime.fromtimestamp(float(f.decision.max()),timezone.utc).isoformat(),'candidate':winner,'settings':dict(CANDIDATES)[winner],'validation_pass':validated,'baseline':base,'filtered':chosen,'test_delta':delta,'uncertainty':u,'all_candidates':candidates,'rule_crosschecks':checks,'events':event_map[winner], 'event_patterns':event_study(f,*pattern_data,int(np.flatnonzero(test)[0])) if pattern_data and test.any() else {}}
 if actual is not None and len(actual):
  amap={int(draw):bool(ok) for draw,ok in zip(f.draw,masks[winner])};actual=actual.copy();actual['day']=np.floor(actual.decision/86400).astype(int)
  comparable=actual.draw.isin(amap).to_numpy();keep=np.array([amap.get(int(d),False) for d in actual.draw])
  item['recorded_strategy_amounts']={'available':len(actual),'matched':int(comparable.sum()),'baseline':metrics(actual,comparable),'filtered':metrics(actual,comparable&keep)}
 return item

def main():
 if os.name=='nt':
  import ctypes
  ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(),0x4000)
 main=ro(ROOT/'data/pc28.sqlite3');paper=ro(ROOT/'data/pc28-algorithm-paper.sqlite3');main.execute('BEGIN');paper.execute('BEGIN')
 cfg=json.loads((ROOT/'config.json').read_text(encoding='utf8'))
 draws=[dict(r) for r in main.execute('SELECT id,draw_utc,source_time,source,total,size,parity,verification FROM draws ORDER BY id')]
 from pc28.history_backtest import replay_time_repairs
 for row in draws:row['_time']=datetime.fromisoformat(row['draw_utc'])
 repairs=replay_time_repairs(draws)
 assert all(b['_time']>=a['_time'] for a,b in zip(draws,draws[1:])),'Unresolved timestamp reversal'
 for row in draws:row.pop('_time')
 drawmap={r['id']:r for r in draws}
 cutoff=min(draws[-1]['id'],paper.execute('SELECT MIN(last_target) FROM states').fetchone()[0])
 prior_live=json.loads((OUT/'live-checkpoint.json').read_text(encoding='utf8')) if (OUT/'live-checkpoint.json').exists() else None
 if prior_live:cutoff=min(cutoff,prior_live['audit']['last']['id'])
 draws=[r for r in draws if r['id']<=cutoff]
 split_ids=[draws[int(len(draws)*p)]['id'] for p in (.6,.8)]
 roster=[dict(r) for r in main.execute('SELECT id,name,settings,enabled FROM custom_strategies WHERE id NOT IN(SELECT id FROM deleted_strategies) ORDER BY created,id')]
 live={};actual={};audit={'draws':len(draws),'first':draws[0],'last':draws[-1],'splits':split_ids,'roster':[],'signal_checks':{},'created':datetime.now(timezone.utc).isoformat(),'odds':cfg['odds'],'definitions':'pc28.trend_risk.analyze/transition; production source ledger, independent fixed-100U gates; 6 predeclared candidates including baseline','sources':{'main':'data/pc28.sqlite3 (readonly snapshot)','paper':'data/pc28-algorithm-paper.sqlite3 (readonly snapshot)'},'missing_periods':sum(max(0,b['id']-a['id']-1) for a,b in zip(draws,draws[1:]))}
 for strategy in roster:
  strategy['settings']=json.loads(strategy['settings']);audit['roster'].append(strategy)
  live[strategy['id']]=[dict(r) for r in main.execute("SELECT target_draw draw,created,settled,pnl,stake,hit,outcome,direction,version,odds_json,total FROM strategy_signals WHERE strategy=? AND status='settled' AND target_draw<=? ORDER BY target_draw",(strategy['id'],cutoff))]
  actual[strategy['id']]=[dict(r) for r in main.execute("SELECT o.target_draw draw,o.created,o.settled,o.pnl,o.stake,o.hit,o.outcome FROM custom_orders o WHERE strategy=? AND status='settled' AND target_draw<=? AND NOT EXISTS(SELECT 1 FROM gap_order_tags g WHERE g.engine='custom' AND g.order_id=o.id) ORDER BY target_draw",(strategy['id'],cutoff))]
 algorithms=[dict(r) for r in paper.execute('SELECT algorithm_id,last_target FROM states ORDER BY algorithm_id')]
 paper_source={r['id']:drawmap[r['id']]['draw_utc'] for r in paper.execute('SELECT id,draw_utc FROM sources WHERE id<=?',(cutoff,))}
 audit['time_repairs']=repairs
 # Read-only snapshots are released before the CPU-heavy studies.
 main.rollback();main.close()
 results=[]
 for i,strategy in enumerate(roster):
  rows=live[strategy['id']];identity={'id':strategy['id'],'name':strategy['name'],'kind':strategy['settings'].get('strategy_type','reversal'),'enabled':bool(strategy['enabled'])}
  if not rows:results.append({**identity,'source':'live_signal','n':0,'status':'no_live_samples'});continue
  cached=next((r for r in prior_live['results'] if r['id']==strategy['id'] and r.get('n')==len(rows) and r.get('baseline',{}).get('train',{}).get('days',0)>1),None) if prior_live else None
  if cached:
   results.append(cached);continue
  f=pd.DataFrame(rows);f['pnl']/=100;f['stake']/=100;f['decision']=pd.to_datetime(f.created,utc=True).astype('datetime64[ns, UTC]').astype('int64')/1e9;f['settled']=pd.to_datetime(f.settled,utc=True).astype('datetime64[ns, UTC]').astype('int64')/1e9
  af=pd.DataFrame(actual[strategy['id']])
  if len(af):af['pnl']/=100;af['stake']/=100;af['decision']=pd.to_datetime(af.created,utc=True).astype('datetime64[ns, UTC]').astype('int64')/1e9
  item=study(identity,f,split_ids,'live_signal',af);item['versions']=int(f.version.nunique());results.append(item)
  print(json.dumps({'phase':'live','done':i+1,'total':len(roster),'n':len(f)}),flush=True)
  (OUT/'live-checkpoint.json').write_text(json.dumps({'audit':audit,'results':results},ensure_ascii=False,allow_nan=False),encoding='utf8')
 for i,a in enumerate(algorithms):
  key=a['algorithm_id'];definition=BY_ID.get(key)
  if not definition:continue
  rows=[tuple(r) for r in paper.execute("SELECT target_draw,pnl,stake,hit,outcome FROM paper_orders WHERE algorithm_id=? AND target_draw<=? AND outcome IN ('win','loss','refund') ORDER BY target_draw",(key,cutoff))]
  if not rows:continue
  f=pd.DataFrame(rows,columns=['draw','amount_pnl','amount_stake','hit','outcome']);f['pnl']=f.amount_pnl/f.amount_stake*100;f['stake']=100.;f['settled']=pd.to_datetime(f.draw.map(paper_source),utc=True).astype('datetime64[ns, UTC]').astype('int64')/1e9
  f['decision']=np.nextafter(f.settled.to_numpy(),-np.inf)
  results.append(study({'id':key,'name':definition['name'],'kind':definition['kind'],'enabled':None},f,split_ids,'algorithm_history'))
  if i%10==0:(OUT/'partial-checkpoint.json').write_text(json.dumps({'audit':audit,'results':results},ensure_ascii=False,allow_nan=False),encoding='utf8')
  print(json.dumps({'phase':'algorithm','done':i+1,'total':len(algorithms),'n':len(f)}),flush=True)
 paper.rollback();paper.close()
 hypotheses=[r for r in results if r.get('uncertainty',{}).get('p_hac') is not None and r.get('candidate')!='always']
 ordered=sorted(hypotheses,key=lambda r:r['uncertainty']['p_hac']);m=len(ordered);last=1.
 for rank in range(m,0,-1):
  r=ordered[rank-1];last=min(last,r['uncertainty']['p_hac']*m/rank);r['uncertainty']['q_bh']=last
 for item in results:
  if not item['n']:continue
  u=item['uncertainty'];test=item['filtered']['test'];delta=item['test_delta'];ci=u['net_delta_ci95']
  item['eligible_for_forward_test']=bool(item['validation_pass'] and test['bets']>=500 and u['days']>=30 and test['net']>0 and delta['net']>0 and delta['prediction_hit']>0 and ci and ci[0]>0 and u.get('q_bh',1)<.05)
  item['recommendation']='forward_observation_only' if item['eligible_for_forward_test'] else 'insufficient_recent_live_sample' if item['source']=='live_signal' else 'no_validated_joint_improvement'
 audit['crosscheck_failures']=[{'id':r['id'],**c} for r in results for c in r.get('rule_crosschecks',[]) if not c['match']]
 audit['counts']={'live_strategies':len(roster),'hall_algorithms':len(algorithms),'with_live_samples':sum(bool(r['n']) for r in results if r['source']=='live_signal'),'eligible_for_forward_test':sum(r.get('eligible_for_forward_test',False) for r in results),'tested_hypotheses':m}
 report={'audit':audit,'results':results};(OUT/'study.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf8')
 with (OUT/'strategy-results.csv').open('w',encoding='utf-8-sig',newline='') as stream:
  fields=['id','name','source','n','candidate','validation_pass','test_bets','baseline_net','filtered_net','net_delta','baseline_hit','filtered_hit','hit_delta','baseline_dd','filtered_dd','delta_ci_low','delta_ci_high','q_bh','recommendation'];writer=csv.DictWriter(stream,fieldnames=fields);writer.writeheader()
  for r in results:
   row={k:r.get(k) for k in fields};b=r.get('baseline',{}).get('test',{});f=r.get('filtered',{}).get('test',{});u=r.get('uncertainty',{});ci=u.get('net_delta_ci95') or [None,None]
   row.update(test_bets=f.get('bets'),baseline_net=b.get('net'),filtered_net=f.get('net'),net_delta=r.get('test_delta',{}).get('net'),baseline_hit=b.get('prediction_hit'),filtered_hit=f.get('prediction_hit'),hit_delta=r.get('test_delta',{}).get('prediction_hit'),baseline_dd=b.get('max_dd'),filtered_dd=f.get('max_dd'),delta_ci_low=ci[0],delta_ci_high=ci[1],q_bh=u.get('q_bh'));writer.writerow(row)
 print(json.dumps({'finished':True,'counts':audit['counts'],'crosscheck_failures':len(audit['crosscheck_failures'])}),flush=True)

if __name__=='__main__':main()
