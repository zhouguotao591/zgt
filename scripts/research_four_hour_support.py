"""Four-hour support -> long -> last bullish low break -> inverse research."""
import sys,os,json
from pathlib import Path
from datetime import datetime,timezone
import numpy as np
import pandas as pd
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.research_two_hour_direction import metrics,bootstrap,values
SOURCE=ROOT/'data/audits/two-hour-direction-20261123'
OUT=ROOT/'data/audits/four-hour-support-20261123'


def candles(f,side='big'):
 x=f[['settled',side]].copy();x['close']=x[side].cumsum();x['open']=x.close-x[side]
 x['high']=x[['open','close']].max(axis=1);x['low']=x[['open','close']].min(axis=1)
 g=x.set_index(pd.to_datetime(x.settled,unit='s',utc=True)).resample('4h',origin='epoch')
 b=pd.DataFrame({k:getattr(g[k],method)() for k,method in [('open','first'),('high','max'),('low','min'),('close','last')]})
 b['count']=g[side].count();b['close']=b.close.ffill().fillna(0)
 for k in ('open','high','low'):b[k]=b[k].fillna(b.close)
 b['end']=b.index.as_unit('ns').astype('int64')/1e9+14400
 assert b.end.iloc[0]>1_700_000_000
 return b


def setups(b,entry='zone'):
 """Three CLOSED support candles, then a later bullish breakout candle."""
 items=[];records=b.to_dict('records')
 for j,c in enumerate(records):
  result={'ready':False,'support':None,'zone_high':None,'atr':None}
  if j>=24 and c['count']>=40:
   history=records[j-23:j];tr=[];previous=history[0]['open']
   for q in history:
    tr.append(max(q['high']-q['low'],abs(q['high']-previous),abs(q['low']-previous)));previous=q['close']
   atr=float(np.mean(tr[-20:]));base=records[j-3:j];low=min(q['low'] for q in base);high=max(q['high'] for q in base)
   stable=max(q['low'] for q in base)-low<=.5*atr and min(q['low'] for q in base[1:])>=base[0]['low']-.1*atr
   compressed=high-low<=2*atr
   pullback=max(q['high'] for q in records[j-11:j-3])-low>=atr
   boundary=high if entry=='zone' else base[-1]['high']
   if entry=='higher_low':
    stable=base[-1]['low']>=base[-2]['low']-.1*atr and base[-2]['low']>=base[0]['low']-.1*atr
    compressed=high-low<=3*atr
    pullback=max(q['high'] for q in records[j-11:j-3])-low>=.5*atr
   ready=atr>0 and stable and compressed and pullback and c['close']>c['open'] and c['close']>boundary+.1*atr and all(q['count']>=40 for q in base)
   result.update(ready=bool(ready),support=low,zone_high=high,atr=atr)
  items.append(result)
 return items


def simulate(f,b,entry='zone',exit_mode='close',reverse=True,side='big',ratchet=False,staged=False):
 plan=setups(b,entry);records=b.to_dict('records');baridx=np.searchsorted(b.end.to_numpy()+15,f.decision.to_numpy(),side='left')-1
 price=f[side].cumsum().to_numpy()-f[side].to_numpy()
 d=np.zeros(len(f),dtype=np.int8);phase=0;anchor=None;anchor_bar=None;processed=-1;events=[];base_side=1 if side=='big' else -1
 support_floor=None;entry_price=None;entry_atr=None;armed=not staged
 for i,j in enumerate(baridx):
  while processed<j:
   processed+=1;c=records[processed];exited=False
   if c['count']<40:
    phase=0;anchor=None;anchor_bar=None
   else:
    # Exit compares against the PREVIOUS known bullish candle before updating.
    if staged and phase==1:armed=armed or c['high']>=entry_price+entry_atr
    stop_line=anchor if armed else support_floor
    if phase==1 and exit_mode=='close' and stop_line is not None and c['close']<stop_line:
     phase=-1 if reverse else 0;exited=True
     events.append({'kind':'break','bar':processed,'time':float(c['end']),'available_at':float(c['end']+15),'effective_draw':int(f.draw.iloc[i]),'direction':int(phase*base_side),'anchor':stop_line,'anchor_bar':anchor_bar,'price':c['close'],'reason':'四小时收盘跌破此前最后上涨K线低点' if armed else '拉升前支撑失效，切换反投'})
     anchor=None;anchor_bar=None
    if phase==1 and c['close']>c['open']:
     if not ratchet or anchor is None or c['low']>=anchor:anchor=c['low'];anchor_bar=processed
    if phase!=1 and not exited and plan[processed]['ready']:
     # A setup already invalidated before its first possible order is not used.
     if price[i]>=c['low']:
      phase=1;anchor=c['low'];anchor_bar=processed;support_floor=plan[processed]['support'];entry_price=float(price[i]);entry_atr=plan[processed].get('atr',0);armed=not staged
      events.append({'kind':'support','bar':processed,'time':float(c['end']),'available_at':float(c['end']+15),'effective_draw':int(f.draw.iloc[i]),'direction':int(base_side),'anchor':anchor,'anchor_bar':processed,'support':plan[processed]['support'],'price':c['close'],'reason':'三根回落K线形成支撑，后续上涨K收盘突破支撑区'})
   plan[processed].update(anchor_after=anchor if armed else support_floor,phase_after=phase,anchor_bar=anchor_bar,armed=armed)
  if staged and phase==1:armed=armed or price[i]>=entry_price+entry_atr
  stop_line=anchor if armed else support_floor
  if exit_mode=='period' and phase==1 and stop_line is not None and price[i]<stop_line:
   phase=-1 if reverse else 0
   events.append({'kind':'break','bar':int(j),'time':float(f.decision.iloc[i]),'available_at':float(f.decision.iloc[i]),'effective_draw':int(f.draw.iloc[i]),'direction':int(phase*base_side),'anchor':stop_line,'anchor_bar':anchor_bar,'price':float(price[i]),'reason':'逐期开奖已知收益跌破最近已收盘上涨K线低点' if armed else '拉升前支撑失效，切换反投'})
   anchor=None;anchor_bar=None
  d[i]=phase*base_side
 return d,events,plan


def main():
 if os.name=='nt':
  import ctypes
  ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(),0x4000)
 OUT.mkdir(exist_ok=True,parents=True)
 f=pd.read_csv(SOURCE/'frozen-input.csv');end=int(f.settled.iloc[-1]//14400)*14400;f=f[f.settled<end].reset_index(drop=True)
 prior=json.loads((SOURCE/'quality.json').read_text(encoding='utf-8'));n=len(f);pos=np.arange(n);cut=int(n*.8);b=candles(f)
 common=f.decision.to_numpy()>b.end.iloc[23]+15
 phases={'first60':common&(pos<int(n*.6)),'middle20':common&(pos>=int(n*.6))&(pos<cut),'last20':common&(pos>=cut),'all':common}
 output={'quality':prior,'scope':{'eligible':n,'closed_4h_cutoff':datetime.fromtimestamp(end,timezone.utc).isoformat(),'phase_bounds':{k:{'start':float(f.decision[m].iloc[0]),'end':float(f.settled[m].iloc[-1])} for k,m in phases.items()}},'rules':{'entry':'形态前已有至少1ATR回落，三根已收盘K线低点聚拢在0.5ATR内、不继续明显创新低，区间宽度不超过2ATR；后续上涨K收盘突破区间高点0.1ATR。','exit':'跌破最近已收盘上涨K线最低点；先检查旧线是否跌破，再更新最新上涨K低点。','after_exit':'主方案立即反投另一方向，直到下一次支撑确认并突破，再转回正投。','source':'固定买大或固定买小100U累计模拟净收益，基准预先固定，不按盈利结果切换。','timing':'仅使用收盘后15秒已知数据；逐期退出用决策前已知收益，四小时退出用已收盘K收盘价。','research':'同一冻结历史，未修改生产。三根支撑与ATR阈值是可执行近似，不是已知截图原算法。'},'models':{},'baseline':{}}
 for side,sign in [('big',1),('small',-1)]:
  d=np.full(n,sign,dtype=np.int8);output['baseline'][side]={k:metrics(f,d,m) for k,m in phases.items()}
 for side in ('big','small'):
  bside=candles(f,side)
  for entry in ('zone','recovery','higher_low'):
   for exit_mode in ('close','period'):
    for reverse in (True,False):
     d,events,plan=simulate(f,bside,entry,exit_mode,reverse,side)
     key='_'.join((side,entry,exit_mode,'reverse' if reverse else 'flat'))
     altered=f.copy();altered.loc[cut:,side]=-999.
     future,_,_=simulate(altered,candles(altered,side),entry,exit_mode,reverse,side)
     assert np.array_equal(d[:cut],future[:cut]),key
     stats={k:metrics(f,d,m) for k,m in phases.items()}
     item={'periods':stats,'stress_198':metrics(f,d,phases['last20'],True),'bootstrap':bootstrap(f,d,phases['last20']),'events':events,'support_setups':sum(x['ready'] for x in plan),'causal_prefix_check':True}
     # Track the total requested trading wave, including inverse holding.
     item['positive_phases']=sum(e['kind']=='support' for e in events);item['breaks']=sum(e['kind']=='break' for e in events)
     for phase in ('all','last20'):
      use=phases[phase];days=np.floor((f.settled.to_numpy()+28800)/86400).astype(int);a=int(days[use].min());daily=np.bincount(days[use]-a,weights=values(f,d)[use],minlength=int(days[use].max())-a+1)
      item['curve_'+phase]=[[datetime.fromtimestamp((a+i)*86400,timezone.utc).strftime('%Y-%m-%d'),float(v)] for i,v in enumerate(np.cumsum(daily))]
     output['models'][key]=item
     print(json.dumps({'model':key,'setups':item['positive_phases'],'breaks':item['breaks'],'all':stats['all'],'last20':stats['last20']},ensure_ascii=True),flush=True)
  (OUT/'study.json').write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding='utf-8')

if __name__=='__main__':main()
