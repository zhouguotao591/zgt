"""Causal shape/structure wave classification; fixed rules, not live trading."""
import sys,os,json
from pathlib import Path
from datetime import datetime,timezone
import numpy as np
import pandas as pd
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from pc28.trend_patterns import detect
from pc28.swing_risk import features
from pc28.trend_risk import DEFAULT
from scripts.research_two_hour_direction import candles,waves,metrics,bootstrap,values,directions
SOURCE=ROOT/'data/audits/two-hour-direction-20261123'
OUT=ROOT/'data/audits/two-hour-shapes-20261123'
LABELS={'turn':'顶底形态右侧收盘确认','confirm':'顶底形态＋后续突破确认','structure':'高低点递进＋结构破位确认'}


def classify(b,kind):
 data=[];state=0;pending=None;highs=[];lows=[];signature=None;flags=[];events=[];setups=[]
 for j,(at,row) in enumerate(b.iterrows()):
  data.append({k:float(row[k]) if k!='count' else int(row[k]) for k in ('open','high','low','close','count')})
  old=state;reason='保持波段';labels=[]
  if j<23 or row['count']<20:
   state=0;pending=None;reason='样本不足'
  else:
   recent=data[max(0,j-60):j+1];p=detect(recent,20,'all');w=features(recent,{**DEFAULT,'swing_bars':8},p);atr=w['atr'];close=row['close'];buffer=.1*atr
   high=max(x['high'] for x in recent[-3:]);low=min(x['low'] for x in recent[-3:]);window=recent[-6:]
   # The preceding movement stops at the LEFT bar of the three-bar pattern.
   movement=recent[-3]['close']-recent[-6]['close']
   top=bool(p['bear']) and movement>=.5*atr and high>=max(x['high'] for x in window)-.5*atr
   bottom=bool(p['bull']) and movement<=-.5*atr and low<=min(x['low'] for x in window)+.5*atr
   if 'top3' in p['bear']:highs.append((j-1,recent[-2]['high']))
   if 'bottom3' in p['bull']:lows.append((j-1,recent[-2]['low']))
   if kind=='structure':
    # Both pivots are confirmed by their right candle, never backdated.
    sig=tuple(x[0] for x in highs[-2:]+lows[-2:])
    fresh=len(highs)>=2 and len(lows)>=2 and min(highs[-2][0],lows[-2][0])>=j-20
    if old==1 and lows and close<lows[-1][1]-buffer and close<recent[-2]['close']:
     state=-1;reason='上涨波段收盘跌破最近已确认波谷';signature=sig
    elif old==-1 and highs and close>highs[-1][1]+buffer and close>recent[-2]['close']:
     state=1;reason='下跌波段收盘突破最近已确认波峰';signature=sig
    elif fresh and sig!=signature:
     if highs[-1][1]>highs[-2][1] and lows[-1][1]>lows[-2][1]:state=1;reason='已确认波峰和波谷同时抬高'
     elif highs[-1][1]<highs[-2][1] and lows[-1][1]<lows[-2][1]:state=-1;reason='已确认波峰和波谷同时降低'
     signature=sig
   elif kind=='turn':
    if top and not bottom:state=-1;reason='上涨后高位看跌形态，右侧K线已收盘';labels=p['bear']
    elif bottom and not top:state=1;reason='下跌后低位看涨形态，右侧K线已收盘';labels=p['bull']
   else:
    if pending:
     invalid=j-pending['born']>3 or pending['side']==1 and close<pending['low']-pending['buffer'] or pending['side']==-1 and close>pending['high']+pending['buffer']
     if invalid:pending=None
     elif j>pending['born'] and (close>pending['high']+pending['buffer'] if pending['side']==1 else close<pending['low']-pending['buffer']):
      state=pending['side'];reason='低位反转后突破形态高点' if state==1 else '高位反转后跌破形态低点';labels=pending['labels'];pending=None
    if pending is None:
     side=1 if bottom and not top and state!=1 else -1 if top and not bottom and state!=-1 else 0
     if side:
      pending={'born':j,'side':side,'high':high,'low':low,'buffer':buffer,'labels':p['bull'] if side==1 else p['bear']}
      setups.append({'bar':j,'time':float(row['end']),'side':side,'high':high,'low':low})
  flags.append(state)
  if state!=old:
   events.append({'bar':j,'time':float(row['end']),'from':old,'to':state,'reason':reason,'patterns':labels,'close':float(row['close'])})
 return np.array(flags,dtype=np.int8),events,setups


def signals(f,b,flags):
 idx=np.searchsorted(b.end.to_numpy()+15,f.decision.to_numpy(),side='left')-1
 d=np.zeros(len(f),dtype=np.int8);ok=(idx>=0)&(idx<len(b));d[ok]=flags[idx[ok]]
 return d,idx


def main():
 if os.name=='nt':
  import ctypes
  ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(),0x4000)
 OUT.mkdir(exist_ok=True,parents=True);f=pd.read_csv(SOURCE/'frozen-input.csv');prior=json.loads((SOURCE/'study.json').read_text(encoding='utf-8'))
 b=candles(f);_,common,_=directions(f,b);n=len(f);pos=np.arange(n);cut=int(n*.8)
 phases={'first60':common&(pos<int(n*.6)),'middle20':common&(pos>=int(n*.6))&(pos<cut),'last20':common&(pos>=cut),'all':common,'recent7':common&(f.decision.to_numpy()>=f.decision.iloc[-1]-7*86400)}
 output={'quality':prior['quality'],'phase_bounds':prior['phase_bounds'],'source_input':'two-hour-direction-20261123/frozen-input.csv','baseline':{k:prior['models'][k] for k in ('big','small','body')},'models':{},'rules':{'chart':'固定买大100U累计净收益，两小时K线；固定买小口径反向映射复核','patterns':'三根分型、实体吞没、连续行情星形；均在最后确认K线收盘后才可用','location':'最近6根K线极值附近0.5ATR，形态前方向位移至少0.5ATR','breakout':'确认候选后最多3根内后续收盘突破形态边界，缓冲0.1ATR；未确认维持此前波段','structure':'最近两个已确认波峰和波谷同时抬高/降低；或收盘突破最近确认结构的反向边界','profit50':'波段最高盈利回吐50%，退出后等待新的形态方向切换；逐期开奖检查与两小时收盘检查分开计算','selection':'三套预先定义规则均展示，不按最后区间结果挑选最优参数；这是历史研究，不是实盘验证'}}
 altered=f.copy();altered.loc[cut:,['big','small']]=[[-999.,999.]]
 for kind in LABELS:
  flags,events,setups=classify(b,kind);d,idx=signals(f,b,flags)
  future_flags,_,_=classify(candles(altered),kind);future,_=signals(altered,candles(altered),future_flags)
  assert np.array_equal(d[:cut],future[:cut]),kind
  small=f.copy();small['big']=f.small;sb=candles(small);sf,_,_=classify(sb,kind);sd,_=signals(f,sb,sf);sd=-sd
  for suffix,mask in [('base',d),('profit50',waves(f,d,stop=-float('inf'))[0]),('closed50',waves(f,d,stop=-float('inf'),checkpoints=idx)[0]),('small_chart',sd)]:
   key=kind+'_'+suffix;stats={p:metrics(f,mask,m) for p,m in phases.items()}
   item={'name':LABELS[kind]+' · '+{'base':'持有至反向波段','profit50':'逐期开奖盈利回吐50%','closed50':'两小时收盘盈利回吐50%','small_chart':'固定买小口径持有'}[suffix],'periods':stats,'bootstrap':bootstrap(f,mask,phases['last20']),'causal_prefix_check':True,'signals_changed':len(events),'candidate_setups':len(setups)}
   ff=f.copy();ff['big']=f.big198;ff['small']=f.small198
   stress=waves(ff,d,stop=-float('inf'),checkpoints=idx if suffix=='closed50' else None)[0] if suffix in ('profit50','closed50') else mask
   item['stress_198']=metrics(f,stress,phases['last20'],True)
   days=np.floor((f.settled.to_numpy()+28800)/86400).astype(int);a=int(days[common].min());length=int(days.max())-a+1
   for phase in ('all','last20'):
    use=phases[phase];start=int(days[use].min());daily=np.bincount(days[use]-start,weights=values(f,mask)[use],minlength=int(days[use].max())-start+1)
    item['curve_'+phase]=[[datetime.fromtimestamp((start+i)*86400,timezone.utc).strftime('%Y-%m-%d'),float(v)] for i,v in enumerate(np.cumsum(daily))]
   if suffix=='base':item['events']=events;item['candidate_records']=setups
   output['models'][key]=item
   print(json.dumps({'model':key,'last20':stats['last20'],'all_net':stats['all']['net'],'middle_net':stats['middle20']['net'],'stress198':item['stress_198']['net']},ensure_ascii=True),flush=True)
  (OUT/'study.json').write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding='utf-8')

if __name__=='__main__':main()
