"""Causal reversal setups on closed equity candles, with no runtime dependencies."""
import statistics

LABELS={'bottom3':'底分型','top3':'顶分型','bull_engulf':'阳包阴','bear_engulf':'阴包阳','morning':'黎明之星（连续行情）','evening':'黄昏之星（连续行情）'}


def restore_gate(old,result,closed_at,duration):
 """Restore one subscription state and translate its rolling equity origin."""
 import json,copy
 stored=old.get('analysis',{})
 if isinstance(stored,str):
  try:stored=json.loads(stored)
  except (ValueError,TypeError):stored={}
 gate=copy.deepcopy(old.get('gate') or stored.get('gate') or {})
 adjacent=old.get('closed_at')==closed_at-duration
 if adjacent and result.get('previous_closed_at')==old.get('closed_at') and stored.get('last_bar'):
  shift=result['previous_close']-stored['last_bar']['close']
  for k in ('floor','peak','trailing_floor'):
   if gate.get(k) is not None:gate[k]+=shift
  for name in ('candidate','exit_candidate'):
   if gate.get(name):
    for k in ('high','low','middle'):gate[name][k]+=shift
  result['coordinate_shift']=shift
 if not adjacent:
  for key in ('candidate','exit_candidate','confirmed'):gate.pop(key,None)
 return gate,stored,adjacent


def detect(data,lookback,family):
 """The final candle is the confirmation candle, never the visual pivot."""
 a,b,c=data[-3:];eps=1e-6
 bull=[];bear=[]
 if family in ('all','fractal'):
  if b['high']<min(a['high'],c['high'])-eps and b['low']<min(a['low'],c['low'])-eps:bull.append('bottom3')
  if b['high']>max(a['high'],c['high'])+eps and b['low']>max(a['low'],c['low'])+eps:bear.append('top3')
 if family in ('all','engulf') and len(data)>=5:
  down=b['close']<data[-5]['close']-eps;up=b['close']>data[-5]['close']+eps
  if down and b['close']<b['open']-eps and c['close']>c['open']+eps and c['open']<=b['close']+eps and c['close']>b['open']+eps:bull.append('bull_engulf')
  if up and b['close']>b['open']+eps and c['close']<c['open']-eps and c['open']>=b['close']-eps and c['close']<b['open']-eps:bear.append('bear_engulf')
 if family in ('all','star') and len(data)>=23:
  reference=statistics.median(abs(x['close']-x['open']) for x in data[-23:-3]);first=abs(a['close']-a['open']);middle=abs(b['close']-b['open']);last=abs(c['close']-c['open'])
  shape=first>eps and first>=reference and middle<=first*.5 and middle<=reference*.5 and last>=first*.5
  if shape and data[-4]['close']<data[-7]['close']-eps and a['close']<a['open']-eps and c['close']>c['open']+eps and c['close']>=(a['open']+a['close'])/2:bull.append('morning')
  if shape and data[-4]['close']>data[-7]['close']+eps and a['close']>a['open']+eps and c['close']<c['open']-eps and c['close']<=(a['open']+a['close'])/2:bear.append('evening')
 prior=data[-lookback-1:-1];values=[x['close'] for x in prior];mean=statistics.mean(values);std=statistics.pstdev(values)
 support=min(x['low'] for x in prior);resistance=max(x['high'] for x in prior)
 lows=[min(x['low'] for x in data[-(2 if k=='bull_engulf' else 3):]) for k in bull]
 highs=[max(x['high'] for x in data[-(2 if k=='bear_engulf' else 3):]) for k in bear]
 bull_near=[k for k,low in zip(bull,lows) if low<=max(support,mean-2*std)+.25*std+eps]
 bear_near=[k for k,high in zip(bear,highs) if high>=min(resistance,mean+2*std)-.25*std-eps]
 return {'bull':bull,'bear':bear,'bull_near':bull_near,'bear_near':bear_near,'labels':[LABELS[k] for k in bull+bear],
         'setup_high':max(x['high'] for x in data[-3:]),'setup_low':min(x['low'] for x in data[-3:]),
         'prior_middle':mean,'prior_support':support,'prior_resistance':resistance}


def decide(old,result,rule,closed_at):
 """One admission state: waiting -> candidate -> admitted -> paused.

 State lives in the existing decision analysis JSON, so restarts and replay
 use exactly the same transition without creating another permission gate.
 """
 duration=3600 if rule['interval']=='1h' else 14400
 gate,stored,adjacent=restore_gate(old,result,closed_at,duration)
 blocked=int(old.get('blocked',1));paused=old.get('paused_at',0);risk=0;safe=0;action='keep'
 if gate.get('floor') is not None and stored and not stored.get('last_bar') and result.get('last_bar'):
  blocked=1;paused=closed_at;action='pause';gate.pop('floor',None)
 if old.get('closed_at') and not adjacent:
  if not blocked:blocked=1;paused=closed_at;action='pause'
  gate.pop('floor',None)
 if not blocked and not gate:gate={'phase':'admitted'}
 p=result.get('patterns',{});bar=result.get('last_bar',{});close=bar.get('close');candidate=gate.get('candidate')
 reason=result['reason'];status=result['status']
 if status=='unknown':
  gate.pop('candidate',None);gate.pop('confirmed',None)
  gate['phase']='paused' if blocked else 'admitted'
 else:
  failed=close is not None and gate.get('floor') is not None and close<gate['floor']-1e-6
  hard_exit=failed or result.get('support_break',False)
  danger=hard_exit or bool(p.get('bear_near')) or result.get('environment')=='down'
  if danger:
   risk=(old.get('risk_count',0) if adjacent else 0)+1
   gate.pop('candidate',None);gate.pop('confirmed',None)
   status='risk';reason='跌破入场形态低点' if failed else '跌破前窗口支撑' if result.get('support_break') else '顶部形态接近压力或BOLL上轨' if p.get('bear_near') else '下降趋势，暂停等待环境改善'
   if not blocked and (hard_exit or risk>=rule['pause_confirm']):blocked=1;paused=closed_at;action='pause';gate.pop('floor',None)
  elif not blocked:
   status='neutral';reason='已放行，尚未出现退出条件'
  else:
   status='neutral';reason='等待支撑附近底部形态及收盘确认'
   if candidate:
    age=(closed_at-candidate['at'])/duration
    if age>rule['candidate_bars'] or close<candidate['low']-1e-6:
     gate.pop('candidate',None);gate.pop('confirmed',None);candidate=None;reason='候选形态过期或跌破低点，重新等待'
    elif closed_at>candidate['at'] and close>(candidate['high'] if rule['entry_confirm']=='breakout' else max(candidate['middle'],p['prior_middle']))+1e-6:
     gate['confirmed']=True
   if gate.get('confirmed') and candidate:
    threshold=candidate['high'] if rule['entry_confirm']=='breakout' else max(candidate['middle'],p['prior_middle'])
    if close>threshold+1e-6 and result.get('environment')!='down':
     safe=(old.get('safe_count',0) if adjacent else 0)+1;status='safe';reason='底部形态后收盘突破形态高点' if rule['entry_confirm']=='breakout' else '底部形态后收盘站上BOLL中轨'
     if safe>=rule['resume_confirm'] and (not paused or closed_at-paused>=duration*rule['min_pause_bars']):
      blocked=0;paused=0;action='resume';gate['floor']=candidate['low'];gate.pop('candidate',None);gate.pop('confirmed',None)
    else:gate.pop('confirmed',None)
   if blocked and not gate.get('candidate') and p.get('bull_near'):
    gate['candidate']={'at':closed_at,'high':p['setup_high'],'low':p['setup_low'],'middle':p['prior_middle'],'patterns':p['bull_near']}
    reason='支撑附近底部形态已形成，等待后续收盘确认'
  gate['phase']='admitted' if not blocked else 'candidate' if gate.get('candidate') else 'paused' if paused else 'waiting'
 result.update(status=status,reason=reason,gate=gate)
 return {'blocked':blocked,'risk_count':risk,'safe_count':safe,'paused_at':paused,'action':action,'gate':gate}
