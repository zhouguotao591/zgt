"""Wave-context reversal policy inside the existing trend admission machine."""
import statistics
from .trend_patterns import restore_gate


def features(data,rule,patterns):
 n=rule['swing_bars'];pre=data[-n-3:-3];last=data[-3:];eps=1e-6
 # Context stops BEFORE the pattern: the reversal cannot erase its own trend.
 changes=[b['close']-a['close'] for a,b in zip(pre,pre[1:])]
 movement=pre[-1]['close']-pre[0]['close'];travel=sum(abs(v) for v in changes)
 tr=[];history=data[:-3];start=max(0,len(history)-20)
 previous=history[start-1]['close'] if start else history[0]['open']
 for b in history[start:]:
  tr.append(max(b['high']-b['low'],abs(b['high']-previous),abs(b['low']-previous)));previous=b['close']
 atr=statistics.mean(tr) if tr else 0
 efficiency=abs(movement)/travel if travel else 0
 amplitude=abs(movement)/atr if atr>eps else 0
 strong=amplitude>=rule['swing_atr'] and efficiency>=rule['swing_efficiency']
 high=max(b['high'] for b in last);low=min(b['low'] for b in last)
 wave=data[-n-3:];peak=max(b['high'] for b in wave);trough=min(b['low'] for b in wave)
 top=strong and movement>eps and high>=peak-.5*atr-eps
 bottom=strong and movement<-eps and low<=trough+.5*atr+eps
 return {'direction':'up' if strong and movement>eps else 'down' if strong and movement<-eps else 'range',
         'atr':atr,'amplitude_atr':amplitude,'efficiency':efficiency,'movement':movement,
         'top':bool(top and patterns['bear']),'bottom':bool(bottom and patterns['bull']),
         'top_patterns':patterns['bear'] if top else [],'bottom_patterns':patterns['bull'] if bottom else [],
         'high':high,'low':low,'wave_high':peak,'wave_low':trough,'context_bars':n}


def decide(old,result,rule,closed_at):
 duration=3600 if rule['interval']=='1h' else 14400
 gate,stored,adjacent=restore_gate(old,result,closed_at,duration)
 blocked=int(old.get('blocked',1));paused=old.get('paused_at',0);action='keep';risk=0;safe=0
 close=result.get('last_bar',{}).get('close');swing=result.get('swing',{});p=result.get('patterns',{})
 status=result['status'];reason=result['reason'];eps=1e-6
 gap=bool(old.get('closed_at') and not adjacent)
 unanchored=gate.get('floor') is not None and stored and not stored.get('last_bar') and close is not None
 if gap or unanchored:
  if not blocked:action='pause';paused=closed_at
  blocked=1;gate={}
 if status=='unknown':
  gate.pop('candidate',None);gate.pop('exit_candidate',None)
 else:
  atr=swing['atr'];buffer=atr*rule['reversal_buffer_atr']
  floor=gate.get('floor');trailing=gate.get('trailing_floor')
  stopped=not blocked and ((floor is not None and close<floor-buffer-eps) or (trailing is not None and close<trailing-eps))
  if stopped:
   blocked=1;paused=closed_at;action='pause';gate={};status='risk';reason='跌破入场形态低点' if floor is not None and close<floor-buffer-eps else '波段回撤触及ATR保护线'
  elif blocked:
   status='neutral';reason='等待下跌波段低位反转形态'
   candidate=gate.get('candidate')
   if candidate:
    age=(closed_at-candidate['at'])/duration
    if age>rule['candidate_bars'] or close<candidate['low']-candidate['buffer']-eps:
     gate.pop('candidate',None);candidate=None;reason='底部候选失效或过期，重新等待'
    elif closed_at>candidate['at']:
     boundary=candidate['high'] if rule['entry_confirm']=='breakout' else max(candidate['middle'],p['prior_middle'])
     if close>boundary+candidate['buffer']+eps:
      safe=(old.get('safe_count',0) if adjacent else 0)+1;status='safe';reason='下跌波段底部反转，后续收盘确认进场'
      if safe>=rule['resume_confirm'] and (not paused or closed_at-paused>=duration*rule['min_pause_bars']):
       blocked=0;paused=0;action='resume';gate={'floor':candidate['low'],'peak':close,'entry_at':closed_at,'entry_patterns':candidate['patterns']}
   # A bottom setup is retained even while the lagging long-window slope is down.
   if blocked and not gate.get('candidate') and swing['bottom']:
    gate['candidate']={'at':closed_at,'high':swing['high'],'low':swing['low'],'middle':p['prior_middle'],'buffer':buffer,'patterns':swing['bottom_patterns']}
    reason='下跌波段低位反转形成候选，等待后续收盘确认'
  else:
   status='neutral';reason='波段持有，未出现已确认顶部反转'
   candidate=gate.get('exit_candidate')
   if candidate:
    age=(closed_at-candidate['at'])/duration
    if age>rule['candidate_bars'] or close>candidate['high']+candidate['buffer']+eps:
     gate.pop('exit_candidate',None);candidate=None;reason='顶部候选失效或过期，继续持有'
    elif closed_at>candidate['at']:
     boundary=candidate['low'] if rule['entry_confirm']=='breakout' else min(candidate['middle'],p['prior_middle'])
     if close<boundary-candidate['buffer']-eps:
      risk=(old.get('risk_count',0) if adjacent else 0)+1;status='risk';reason='上涨波段顶部反转，后续收盘确认出场'
      if risk>=rule['pause_confirm']:blocked=1;paused=closed_at;action='pause';gate={}
   # The shape need only occur once; later closes confirm the stored setup.
   if not blocked and not gate.get('exit_candidate') and swing['top']:
    gate['exit_candidate']={'at':closed_at,'high':swing['high'],'low':swing['low'],'middle':p['prior_middle'],'buffer':buffer,'patterns':swing['top_patterns']}
    reason='上涨波段高位反转形成候选，等待后续收盘确认'
  if not blocked:
   gate['peak']=max(gate.get('peak',close),close)
   if rule['swing_stop_atr'] and atr>eps:
    line=gate['peak']-rule['swing_stop_atr']*atr
    gate['trailing_floor']=max(gate.get('trailing_floor',line),line)
 gate['phase']='exit_candidate' if not blocked and gate.get('exit_candidate') else 'admitted' if not blocked else 'candidate' if gate.get('candidate') else 'paused' if paused else 'waiting'
 result.update(status=status,reason=reason,gate=gate)
 return {'blocked':blocked,'risk_count':risk,'safe_count':safe,'paused_at':paused,'action':action,'gate':gate}
