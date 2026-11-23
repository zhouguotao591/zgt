"""Independent-account sensitivity, NOT a claim about existing subscription P&L."""
import json,sys,math
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.analyze_candle_reversals import frames,OUT
from scripts.analyze_strategy_regimes import bars_for
from scripts.reversal_pattern_rules import detect,policy
from scripts.analyze_six_strategy_entries import roi_uncertainty

def account(f,eligible,admitted,settings):
 initial=float(settings['initial_balance']);balance=peak=initial;daily=0.;day=None;net=gains=losses=0.;bets=hits=refunds=0;maxdd=0.;skips={};stake=100.
 days=np.floor(f.settled.to_numpy()/86400).astype(int);pnl=f.pnl.to_numpy();hit=f.hit.to_numpy();indices=np.flatnonzero(eligible&admitted)
 for position,i in enumerate(indices):
  if days[i]!=day:day=days[i];daily=0.
  if balance<stake:skips['余额不足']=len(indices)-position;break
  if stake>float(settings['max_stake']):skips['超过单额']=len(indices)-position;break
  if settings['max_drawdown'] and peak-balance+stake>settings['max_drawdown']:skips['回撤限制']=len(indices)-position;break
  if settings['daily_loss_limit'] and daily-stake < -settings['daily_loss_limit']:skips['当日亏损限制']=skips.get('当日亏损限制',0)+1;continue
  paid=float(pnl[i]);balance+=paid;daily+=paid;peak=max(peak,balance);maxdd=max(maxdd,peak-balance);net+=paid;gains+=max(0,paid);losses+=max(0,-paid);bets+=1;hits+=int(hit[i]);refunds+=int(paid==0)
 return {'bets':bets,'net':net,'initial_capital':initial,'capital_return':net/initial,'stake':bets*stake,'roi':net/(bets*stake) if bets else None,'prediction_hit':hits/bets if bets else None,'refunds':refunds,'max_dd':maxdd,'profit_factor':gains/losses if losses else None,'skips':skips}

def main():
 prior,data=frames();identity,f=next((i,f) for i,f in data if i['id']=='fixed_even');b=bars_for(f,4);mask,events=policy(f,b,detect(b),'union','confirm',True);test=(f.draw>=prior['splits'][1]).to_numpy();f['day']=np.floor(f.decision/86400).astype(int)
 definitions=json.loads((ROOT/'data/audits/strategy-regimes-20261121/study.json').read_text(encoding='utf8'))['audit']['roster'];definition=next(r for r in definitions if r['settings'].get('strategy_type')=='single' and r['settings'].get('direction')=='双');settings=definition['settings']
 kept=['initial_balance','daily_loss_limit','max_drawdown','max_stake'];rules={k:settings[k] for k in kept}
 sensitivity={}
 for scope,eligible in [('replication_fresh_account',test),('full_history_continuous_account',np.ones(len(f),dtype=bool))]:
  sensitivity[scope]={'baseline':account(f,eligible,np.ones(len(f),dtype=bool),rules),'pattern_gate':account(f,eligible,mask,rules)}
 # Paired calendar-day block bootstrap for the conditional hit rate, not a
 # forecast probability for the next draw.
 days=f.loc[test].day;first,last=int(days.min()),int(days.max());n=last-first+1;day=f.day.to_numpy()-first
 def sums(m,values):return np.bincount(day[m],weights=np.asarray(values)[m],minlength=n)[:n]
 bn=sums(test,np.ones(len(f)));fn=sums(test&mask,np.ones(len(f)));bh=sums(test,f.hit);fh=sums(test&mask,f.hit)
 rng=np.random.default_rng(117);starts=rng.integers(0,n,(2000,math.ceil(n/4)));idx=((starts[:,:,None]+np.arange(4)[None,None,:])%n).reshape(2000,-1)[:,:n];den=fn[idx].sum(axis=1);valid=den>0
 delta=fh[idx].sum(axis=1)[valid]/den[valid]-bh[idx].sum(axis=1)[valid]/bn[idx].sum(axis=1)[valid]
 payload={'candidate':'fixed_even.4h.union.confirm','selection':'Post-hoc research lead, not the training-selected winner; frozen history was previously inspected.','rules':rules,'assumption':'Gate precedes sizing and capital updates in a new isolated account. The unfiltered signal book keeps running. This is not identical to the current subscription-only gate if its source simulation account has stopped.','sensitivity':sensitivity,'roi_uncertainty':roi_uncertainty(f,test,test&mask),'conditional_hit_delta_ci95':[float(x) for x in np.quantile(delta,[.025,.975])],'events':events}
 cases=[]
 for sid,hours,family,mode in [('fixed_big',1,'union','level'),('fixed_small',4,'union','level'),('fixed_even',4,'star','level'),('fixed_even',4,'union','confirm')]:
  who,ff=next((i,ff) for i,ff in data if i['id']==sid);bb=bars_for(ff,hours);mm,_=policy(ff,bb,detect(bb),family,mode);direction={'fixed_big':'大','fixed_small':'小','fixed_even':'双'}[sid]
  cfg=next(r['settings'] for r in definitions if r['settings'].get('strategy_type')=='single' and r['settings'].get('direction')==direction);settings={k:cfg[k] for k in kept};scopes={}
  for scope,select in [('replication_fresh_account',(ff.draw>=prior['splits'][1]).to_numpy()),('full_history_continuous_account',np.ones(len(ff),dtype=bool))]:scopes[scope]={'baseline':account(ff,select,np.ones(len(ff),dtype=bool),settings),'pattern_gate':account(ff,select,mm,settings)}
  cases.append({'strategy':who['name'],'id':sid,'model':f'{hours}h.{family}.{mode}','rules':settings,'scopes':scopes})
 payload['other_account_cases']=cases
 (OUT/'risk-sensitivity.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf8');print(json.dumps({k:v for k,v in payload.items() if k!='events'},ensure_ascii=False))

if __name__=='__main__':main()
