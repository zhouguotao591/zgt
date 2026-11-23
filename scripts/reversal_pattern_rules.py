"""Causal research definitions for continuous cumulative-P&L candlesticks.

Signals are indexed by the LAST CLOSED confirmation candle, never the visual
pivot candle. These are explicit research conventions, not universal standards.
"""
import numpy as np
import pandas as pd

FAMILIES={'fractal3':('bottom3','top3'),'star':('morning_nogap','evening_nogap'),'engulf':('bull_engulf','bear_engulf'),'union':('bull_union','bear_union')}

def detect(b):
 o,h,l,c=(b[k] for k in ('open','high','low','close'));eps=1e-6;body=(c-o).abs();middle_high=pd.concat([o.shift(1),c.shift(1)],axis=1).max(axis=1);middle_low=pd.concat([o.shift(1),c.shift(1)],axis=1).min(axis=1)
 # Three-bar Chinese-style fenxing: center has BOTH its high and low above/
 # below its neighbors. The right neighbor must already have closed.
 top3=(h.shift(1)>h.shift(2)+eps)&(h.shift(1)>h+eps)&(l.shift(1)>l.shift(2)+eps)&(l.shift(1)>l+eps)
 bottom3=(h.shift(1)<h.shift(2)-eps)&(h.shift(1)<h-eps)&(l.shift(1)<l.shift(2)-eps)&(l.shift(1)<l-eps)
 # Five-bar Williams variant is a separate diagnostic, confirmed two bars later.
 top5=pd.concat([h.shift(2)>h.shift(k)+eps for k in (0,1,3,4)],axis=1).all(axis=1)
 bottom5=pd.concat([l.shift(2)<l.shift(k)-eps for k in (0,1,3,4)],axis=1).all(axis=1)
 up_before_pair=c.shift(1)>c.shift(4)+eps;down_before_pair=c.shift(1)<c.shift(4)-eps
 bullish=(c>o+eps)&(c.shift(1)<o.shift(1)-eps)&(o<=c.shift(1)+eps)&(c>o.shift(1)+eps)&down_before_pair
 bearish=(c<o-eps)&(c.shift(1)>o.shift(1)+eps)&(o>=c.shift(1)-eps)&(c<o.shift(1)-eps)&up_before_pair
 # Long first body is judged against 20 bodies BEFORE the three-bar pattern.
 reference=body.rolling(20).median().shift(3);first=body.shift(2)
 long=(first>=reference)&(first>eps);small=(body.shift(1)<=first*.5)&(body.shift(1)<=reference*.5)
 down_before_triple=c.shift(3)<c.shift(6)-eps;up_before_triple=c.shift(3)>c.shift(6)+eps
 morning=long&small&(c.shift(2)<o.shift(2)-eps)&(c>o+eps)&(body>=first*.5)&(c>=(o.shift(2)+c.shift(2))/2)&down_before_triple
 evening=long&small&(c.shift(2)>o.shift(2)+eps)&(c<o-eps)&(body>=first*.5)&(c<=(o.shift(2)+c.shift(2))/2)&up_before_triple
 patterns={'top3':top3,'bottom3':bottom3,'top5':top5,'bottom5':bottom5,'bull_engulf':bullish,'bear_engulf':bearish,'morning_nogap':morning,'evening_nogap':evening,'morning_gap':morning&(middle_high<c.shift(2)-eps),'evening_gap':evening&(middle_low>c.shift(2)+eps)}
 patterns['bull_union']=bottom3|morning|bullish;patterns['bear_union']=top3|evening|bearish
 arrays={key:value.fillna(False).to_numpy(dtype=bool) for key,value in patterns.items()}
 mean=c.shift(1).rolling(20).mean();std=c.shift(1).rolling(20).std(ddof=0);support=l.shift(1).rolling(20).min();resistance=h.shift(1).rolling(20).max()
 previous_traded=(b.orders>0).rolling(20).sum();indices=pd.Series(np.arange(len(b)),index=b.index);last_active=indices.where(b.orders>0).ffill()
 known=((b.orders.rolling(20).sum()>=20)&(previous_traded>=5)&mean.notna()&((indices-last_active)<=3)).to_numpy()
 low3=pd.concat([l,l.shift(1),l.shift(2)],axis=1).min(axis=1).to_numpy();high3=pd.concat([h,h.shift(1),h.shift(2)],axis=1).max(axis=1).to_numpy()
 low2=pd.concat([l,l.shift(1)],axis=1).min(axis=1).to_numpy();high2=pd.concat([h,h.shift(1)],axis=1).max(axis=1).to_numpy()
 lower=np.maximum(support.to_numpy(),(mean-2*std).to_numpy())+.25*std.to_numpy();upper=np.minimum(resistance.to_numpy(),(mean+2*std).to_numpy())-.25*std.to_numpy()
 levels={}
 for family,(bull,bear) in FAMILIES.items():
  low,high=(low2,high2) if family=='engulf' else (low3,high3)
  levels[family]={'low':low,'high':high,'bull_near':low<=lower,'bear_near':high>=upper}
 return {'patterns':arrays,'known':known,'families':levels,'support':support.to_numpy(),'resistance':resistance.to_numpy(),'middle':mean.to_numpy(),'upper':(mean+2*std).to_numpy(),'lower':(mean-2*std).to_numpy()}

def policy(frame,b,features,family,mode,record_events=False):
 """raw: shapes only; level: level+failure; confirm: extra close breakout.

 All policies start flat. Mode confirm waits for a LATER closed candle to close
 above the setup high within three candles. Bearish/failed setups take priority.
 There is no implicit position liquidation or reversal of the betting direction.
 """
 bull_key,bear_key=FAMILIES[family];p=features['patterns'];level=features['families'][family];active=False;pending=None;floor=None;origin=None;flags=[];events=[]
 for j,end in enumerate(b.end.to_numpy()):
  before=active;reason='';event_origin=origin;event_floor=floor;failed=floor is not None and b.close.iloc[j]<floor-1e-6
  if not features['known'][j]:active=False;pending=None;floor=None;reason='样本不足'
  elif p[bear_key][j] or (mode!='raw' and failed):
   if p[bear_key][j]:event_origin=j
   active=False;pending=None;floor=None;reason='已确认看跌形态' if p[bear_key][j] else '跌破入场形态低点'
  elif active:pass
  elif mode=='confirm':
   if pending is not None:
    born,high,low=pending
    if b.close.iloc[j]<low-1e-6 or j-born>3:pending=None
    elif j>born and b.close.iloc[j]>high+1e-6:active=True;floor=low;origin=event_origin=born;pending=None;reason='形态后收盘突破形态高点'
   if not active and pending is None and p[bull_key][j] and level['bull_near'][j]:pending=(j,level['high'][j],level['low'][j])
  elif p[bull_key][j] and (mode=='raw' or level['bull_near'][j]):active=True;floor=level['low'][j];origin=event_origin=j;reason='已确认看涨形态'+('且接近支撑或下轨' if mode=='level' else '')
  flags.append(active)
  if record_events and before!=active:
   anchor=event_origin if event_origin is not None else j
   original_floor=floor if active else event_floor
   events.append({'time':pd.Timestamp(float(end),unit='s',tz='UTC').isoformat(),'action':'resume' if active else 'pause','reason':reason,'close':float(b.close.iloc[j]),'setup_low':float(original_floor) if original_floor is not None else None,'confirmation_index':j,'pattern_center_index':anchor-1 if family=='fractal3' else anchor,'support':float(features['support'][j]),'resistance':float(features['resistance'][j]),'boll_lower':float(features['lower'][j]),'boll_middle':float(features['middle'][j]),'boll_upper':float(features['upper'][j])})
 idx=np.searchsorted(b.end.to_numpy(),frame.decision.to_numpy(),side='right')-1
 mask=np.array([flags[i] if i>=0 else False for i in idx],dtype=bool)
 last=float(frame.decision.max());events=[e for e in events if pd.Timestamp(e['time']).timestamp()<=last]
 return mask,events
