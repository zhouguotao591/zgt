"""Read-only, causal two-hour direction study. Never imports Service."""
import sys,os,json,sqlite3,hashlib,csv
from pathlib import Path
from datetime import datetime,timedelta,timezone
import numpy as np
import pandas as pd
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from pc28.history_backtest import replay_time_repairs
from pc28.maintenance import block_reason
from pc28.model import settlement
OUT=ROOT/'data/audits/two-hour-direction-20261123'
NAMES={'big':'固定买大','small':'固定买小','body':'上一段涨买大、跌买小','inverse':'上一段涨买小、跌买大','count':'上一段大小次数差顺势','confirm':'连续两段同向才跟随','ema':'2小时EMA3/8顺势','strong':'上一段净胜至少2单才跟随','wave50':'顺势＋波段回吐50%/7.2单止损','oracle':'事后知道本段涨跌（禁止执行的对照）'}
NAMES.update(smallchart='固定买小K线上涨买小、下跌买大',profit50='固定买大K线顺势＋最高盈利回吐50%',profit50_small='固定买小K线顺势＋最高盈利回吐50%')
NAMES.update(profit50_2h='顺势＋仅两小时收盘检查盈利回吐50%')

def ro(p):
 c=sqlite3.connect(p.resolve().as_uri()+'?mode=ro',uri=True,timeout=10);c.row_factory=sqlite3.Row;c.execute('PRAGMA query_only=ON');return c

def load():
 cfg=json.loads((ROOT/'config.json').read_text(encoding='utf-8-sig'));c=ro(ROOT/cfg['database'])
 c.execute('BEGIN');raw=[dict(x) for x in c.execute("SELECT id,draw_utc,source_time,source,total,size,parity FROM draws WHERE verification='full_keno' AND julianday(draw_utc)<=julianday('now') AND NOT EXISTS(SELECT 1 FROM conflicts x WHERE x.draw_id=draws.id AND x.resolved=0) ORDER BY id")];c.close()
 for r in raw:r['_time']=datetime.fromisoformat(r['draw_utc'])
 repairs=replay_time_repairs(raw)
 p=ro(ROOT/'data/pc28-algorithm-paper.sqlite3');odds={x['id']:json.loads(x['value']) for x in p.execute('SELECT id,value FROM odds')};by_id={x['id']:x['odds_id'] for x in p.execute('SELECT id,odds_id FROM sources')};p.close()
 end=int(raw[-1]['_time'].timestamp()//7200)*7200;rows=[];skips={};fallback=0
 for a,b in zip(raw,raw[1:]):
  t=a['_time']+timedelta(seconds=15)
  reason='缺期' if a['id']+1!=b['id'] else '时间异常' if not a['_time']<t<b['_time'] else block_reason(a,t,cfg['collector'].get('maintenance_guard_enabled',True),b['_time'])
  if reason:skips[reason]=skips.get(reason,0)+1;continue
  if b['_time'].timestamp()+15>=end:continue
  assert b['size']==('大' if b['total']>=14 else '小')
  o=odds[by_id[b['id']]] if b['id'] in by_id else cfg['odds'];fallback+=b['id'] not in by_id
  actual=[settlement(10000,d,b['total'],d==b['size'],o)['pnl']/100 for d in ('大','小')]
  stress={**o,'normal':198,'special_size':198,'full_payout_totals':[]}
  lower=[settlement(10000,d,b['total'],d==b['size'],stress)['pnl']/100 for d in ('大','小')]
  rows.append((b['id'],t.timestamp(),b['_time'].timestamp()+15,1 if b['size']=='大' else -1,*actual,*lower))
 f=pd.DataFrame(rows,columns=['draw','decision','settled','actual','big','small','big198','small198'])
 assert f.draw.is_unique and f.decision.is_monotonic_increasing
 quality={'source_rows':len(raw),'eligible_rows':len(f),'first_draw':int(f.draw.iloc[0]),'last_draw':int(f.draw.iloc[-1]),'cutoff_closed_utc':datetime.fromtimestamp(end,timezone.utc).isoformat(),'time_repairs':repairs,'skips':skips,'odds_fallback_rows':fallback,'initial_capital':100000,'stake':100,'schema':'fixed size profit with per-draw saved odds; no real member orders','live_db_written':False,'missing_ids':sum(max(0,b['id']-a['id']-1) for a,b in zip(raw,raw[1:]))}
 return f,quality

def candles(f):
 x=f[['settled','big','actual']].copy();x['equity']=x.big.cumsum();x['opening']=x.equity-x.big
 x['high']=x[['equity','opening']].max(axis=1);x['low']=x[['equity','opening']].min(axis=1)
 g=x.set_index(pd.to_datetime(x.settled,unit='s',utc=True)).resample('2h',origin='epoch')
 b=pd.DataFrame({'open':g.opening.first(),'high':g.high.max(),'low':g.low.min(),'close':g.equity.last(),'count':g.big.count(),'delta':g.big.sum(),'size_delta':g.actual.sum()})
 b['end']=b.index.as_unit('ns').astype('int64')/1e9+7200;b['close']=b.close.ffill().fillna(0)
 assert b.end.iloc[0]>1_700_000_000 and np.all(np.diff(b.end)==7200)
 return b

def directions(f,b,delay=0):
 valid=b['count'].to_numpy()>=20;body=np.sign(b.delta.to_numpy());count=np.sign(b.size_delta.to_numpy())
 confirm=np.where(body==np.r_[0,body[:-1]],body,0)
 fast=b.close.ewm(span=3,adjust=False).mean();slow=b.close.ewm(span=8,adjust=False).mean();ema=np.sign((fast-slow).to_numpy())
 strong=np.where(abs(b.delta.to_numpy())>=200,body,0)
 # A candle ending at this instant is not yet known, and allow 15s collection.
 idx=np.searchsorted(b.end.to_numpy()+15+delay,f.decision.to_numpy(),side='left')-1
 known=(idx>=8)&(idx<len(b));known[known]&=valid[idx[known]]
 masks={}
 for key,values in [('big',np.ones(len(b))),('small',-np.ones(len(b))),('body',body),('inverse',-body),('count',count),('confirm',confirm),('ema',ema),('strong',strong)]:
  d=np.zeros(len(f),dtype=np.int8);d[known]=values[idx[known]];masks[key]=d
 # Deliberately invalid look-ahead reference, never a candidate strategy.
 own=np.searchsorted(b.end.to_numpy(),f.settled.to_numpy(),side='right');d=np.zeros(len(f),dtype=np.int8);ok=known&(own<len(b));d[ok]=body[own[ok]];masks['oracle']=d
 return masks,known,idx

def waves(f,signal,stop=-720,checkpoints=None):
 out=np.zeros(len(f),dtype=np.int8);records=[];direction=0;equity=peak=0.;active=False;last=0;start=0
 def finish(i,why):
  if direction and i>start and np.count_nonzero(out[start:i]):
   records.append({'start_draw':int(f.draw.iloc[start]),'end_draw':int(f.draw.iloc[i-1]),'direction':'大' if direction>0 else '小','start_time':float(f.decision.iloc[start]),'end_time':float(f.settled.iloc[i-1]),'bets':int(np.count_nonzero(out[start:i])),'net':float(equity),'peak':float(peak),'reason':why,'closed':True})
 for i,d in enumerate(signal):
  if d!=direction:
   finish(i,'两小时趋势切换');direction=int(d);equity=peak=0.;active=bool(d);start=i
  if checkpoints is not None and active and i>start and checkpoints[i]!=checkpoints[i-1]:
   peak=max(peak,equity)
   if peak>0 and equity<=peak*.5:
    finish(i,'两小时收盘盈利回吐50%');active=False;start=i;equity=peak=0.
  if active:
   out[i]=direction;equity+=f.big.iloc[i] if direction>0 else f.small.iloc[i]
   peak=max(peak,equity)
   if equity<=stop or checkpoints is None and peak>0 and equity<=peak*.5:
    finish(i+1,'固定止损' if equity<=stop else '回吐本波盈利50%');active=False;direction=int(d);last=i+1
    # The wave is ended; do not double-report it at a later direction change.
    start=i+1;equity=peak=0.
 if active:
  finish(len(f),'截止尚未退出');records[-1]['closed']=False
 return out,records

def values(f,d,stress=False):
 return np.where(d>0,f['big198' if stress else 'big'].to_numpy(),np.where(d<0,f['small198' if stress else 'small'].to_numpy(),0.))

def metrics(f,d,mask,stress=False):
 d=np.asarray(d);mask=np.asarray(mask);p=values(f,d,stress)[mask];dd=d[mask];active=dd!=0;num=int(active.sum());net=float(p.sum());curve=np.cumsum(p);drawdown=np.maximum.accumulate(np.r_[0,curve])[1:]-curve
 actual=f.actual.to_numpy()[mask]
 return {'bets':num,'big_bets':int((dd>0).sum()),'small_bets':int((dd<0).sum()),'net':net,'roi':net/(num*100) if num else None,'capital_roi':net/100000,'hit':float((dd[active]==actual[active]).mean()) if num else None,'refunds':int(((p==0)&active).sum()),'wins':int((p>0).sum()),'losses':int((p<0).sum()),'max_dd':float(drawdown.max()) if len(drawdown) else 0.,'minimum_balance':float(100000+min(0,curve.min())) if len(curve) else 100000}

def bootstrap(f,d,mask,seed=721):
 ff=f.loc[mask];dd=d[mask];days=np.floor(ff.decision.to_numpy()/86400).astype(int);days-=days.min();n=int(days.max())+1
 daily=np.bincount(days,weights=values(ff,dd),minlength=n);bets=np.bincount(days,weights=dd!=0,minlength=n)
 rng=np.random.default_rng(seed);starts=rng.integers(0,n,(2000,int(np.ceil(n/7))));idx=((starts[:,:,None]+np.arange(7))%n).reshape(2000,-1)[:,:n]
 totals=daily[idx].sum(axis=1);counts=bets[idx].sum(axis=1);roi=totals[counts>0]/(100*counts[counts>0])
 return {'days':n,'block_days':7,'draws':2000,'net_ci95':[float(x) for x in np.quantile(totals,[.025,.975])],'roi_ci95':[float(x) for x in np.quantile(roi,[.025,.975])] if len(roi) else None}

def main():
 if os.name=='nt':
  import ctypes
  ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(),0x4000)
 OUT.mkdir(exist_ok=True,parents=True)
 if (OUT/'quality.json').exists():
  f=pd.read_csv(OUT/'frozen-input.csv');quality=json.loads((OUT/'quality.json').read_text(encoding='utf-8'))
 else:
  f,quality=load();f.to_csv(OUT/'frozen-input.csv',index=False);quality['input_sha256']=hashlib.sha256((OUT/'frozen-input.csv').read_bytes()).hexdigest();(OUT/'quality.json').write_text(json.dumps(quality,ensure_ascii=False,indent=2),encoding='utf-8')
 b=candles(f);models,common,idx=directions(f,b)
 small_source=f.copy();small_source['big']=f.small;small_models,_,_=directions(f,candles(small_source));models['smallchart']=-small_models['body']
 models['wave50'],wave_hard=waves(f,models['body'])
 models['profit50'],wave=waves(f,models['body'],stop=-float('inf'))
 models['profit50_small'],small_wave=waves(f,models['smallchart'],stop=-float('inf'))
 models['profit50_2h'],closed_wave=waves(f,models['body'],stop=-float('inf'),checkpoints=idx)
 n=len(f);pos=np.arange(n)
 phases={'first60':common&(pos<int(n*.6)),'middle20':common&(pos>=int(n*.6))&(pos<int(n*.8)),'last20':common&(pos>=int(n*.8)),'all':common,'recent7':common&(f.decision.to_numpy()>=f.decision.iloc[-1]-7*86400)}
 result={'quality':quality,'names':NAMES,'phase_bounds':{k:{'start':float(f.decision[m].iloc[0]),'end':float(f.settled[m].iloc[-1]),'eligible':int(m.sum())} for k,m in phases.items()},'models':{},'causality':{},'wave_records':wave,'assumptions':{'profit50':'Exit when current wave profit <= half its previous highest positive profit; hold flat until signal direction changes. Not a 50% price-range retracement.','wave50':'Additional exploratory hard stop -7.2 stakes, not the screenshot original configuration; no confirmed stake/exit-turn-positive formula supplied.','chart_source':'Fixed-side 100U cumulative simulated P&L, big source and small source both checked.','fees':'Historical site odds snapshots; stress assumes normal wins 1.98 with other refund rules retained.'}}
 for key,d in models.items():
  item={'periods':{p:metrics(f,d,m) for p,m in phases.items()},'stress_198':metrics(f,d,phases['last20'],True),'bootstrap':bootstrap(f,d,phases['last20'])}
  if key in ('wave50','profit50','profit50_small','profit50_2h'):
   ff=f.copy();ff['big']=f.big198;ff['small']=f.small198
   signal=models['smallchart'] if key=='profit50_small' else models['body']
   stressed,_=waves(ff,signal,stop=-720 if key=='wave50' else -float('inf'),checkpoints=idx if key=='profit50_2h' else None)
   item['stress_198']=metrics(f,stressed,phases['last20'],True)
  # One-month subperiods show whether a full-history profit is concentrated.
  month=pd.to_datetime(f.decision,unit='s',utc=True).dt.strftime('%Y-%m').to_numpy();item['months']={m:metrics(f,d,common&(month==m)) for m in np.unique(month)}
  days=np.floor(f.settled.to_numpy()/86400).astype(int);a=int(days[common].min());length=int(days.max())-a+1
  daily=np.bincount(days[common]-a,weights=values(f,d)[common],minlength=length);item['daily_curve']=[[int((a+j)*86400),round(float(v),2)] for j,v in enumerate(np.cumsum(daily))]
  result['models'][key]=item
  print(json.dumps({'model':key,'last20':item['periods']['last20'],'roi_ci95':item['bootstrap']['roi_ci95']},ensure_ascii=True),flush=True)
 # Mutating later outcomes must never change earlier model directions.
 cut=int(n*.8);alter=f.copy();alter.loc[cut:,['big','small']]=[[-999.,999.]]
 changed,_,_=directions(alter,candles(alter));
 for key in ('body','inverse','count','confirm','ema','strong'):
  assert np.array_equal(models[key][:cut],changed[key][:cut]),key
  result['causality'][key]=True
 changed_small=alter.copy();changed_small['big']=alter.small
 alt_small,_,_=directions(alter,candles(changed_small))
 assert np.array_equal(models['smallchart'][:cut],-alt_small['body'][:cut])
 changed_wave,_=waves(alter,changed['body'],stop=-float('inf'))
 assert np.array_equal(models['profit50'][:cut],changed_wave[:cut])
 result['causality'].update(smallchart=True,profit50=True)
 changed_closed,_=waves(alter,changed['body'],stop=-float('inf'),checkpoints=idx)
 assert np.array_equal(models['profit50_2h'][:cut],changed_closed[:cut]);result['causality']['profit50_2h']=True
 delayed,_,_=directions(f,b,210)
 result['one_period_lag']=metrics(f,delayed['body'],phases['last20'])
 # Predicting the NEXT wave direction, rather than classifying the same wave.
 previous=np.sign(b.delta.shift(1).to_numpy());actual=np.sign(b.delta.to_numpy());ok=(b['count'].to_numpy()>=20)&(b['count'].shift(1).fillna(0).to_numpy()>=20)&np.isfinite(previous)&(previous!=0)&(actual!=0)
 result['next_wave_direction']={'waves':int(ok.sum()),'same_direction_share':float((previous[ok]==actual[ok]).mean())}
 validwave=[r for r in wave if r['closed'] and r['bets']];result['wave_summary']={'closed':len(validwave),'profitable':sum(r['net']>0 for r in validwave),'loss':sum(r['net']<0 for r in validwave),'flat':sum(r['net']==0 for r in validwave),'stop_loss':sum(r['reason']=='固定止损' for r in validwave)}
 with (OUT/'wave-records.csv').open('w',encoding='utf-8-sig',newline='') as h:
  w=csv.DictWriter(h,fieldnames=list(wave[0]));w.writeheader();w.writerows(wave)
 (OUT/'study.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')

if __name__=='__main__':main()
