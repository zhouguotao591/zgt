"""Chronological subscription admission simulation, independent of strategy sizing."""
import copy
from datetime import datetime,timezone
from .trend_risk import analyze,transition,normalize

def apply(rows,orders,snapshot,rule):
 rule=normalize(rule)
 from .history_backtest import replay,income_candles
 from .strategy_signals import neutral
 from .trend_strategy import summary
 from .model import money
 if rule['source']=='signal':
  shadow=copy.deepcopy(snapshot);shadow['settings']=neutral(shadow['settings'])
  _,source=replay(rows,shadow)
 else:source=orders
 bars=income_candles(source,rule['interval'],start=rows[0]['_time'].isoformat(),end=rows[-1]['_time'].isoformat())
 duration=3600 if rule['interval']=='1h' else 14400
 state={};events=[];accepted=[];blocked_draws=set();old_allow=None
 slot=int(rows[0]['_time'].timestamp()//duration)*duration
 by_draw={o['target_draw']:o for o in orders}
 allowed=False;assessment={'status':'unknown','reason':'等待已收盘样本'}
 for row in rows:
  # The decision occurs immediately before this draw; a bar ending at this
  # exact instant is not yet available (including the target draw's result).
  decision=row['_time'].timestamp()-.000001
  while slot<=decision:
   assessment=analyze(bars,rule,slot)
   state={**transition(state,assessment,rule,slot),'closed_at':slot}
   allowed=not state['blocked'] and (assessment['status']!='unknown' or rule['unknown_policy']=='allow')
   if allowed!=old_allow:
    events.append({'time':datetime.fromtimestamp(slot,timezone.utc).isoformat(),'draw':row['id'],'action':'resume' if allowed else 'pause','reason':assessment['reason'],'status':assessment['status'],'environment':assessment.get('environment'),'patterns':assessment.get('patterns'),'gate':copy.deepcopy(assessment.get('gate'))})
   old_allow=allowed;slot+=duration
  order=by_draw.get(row['id'])
  if order:
   if allowed:accepted.append(dict(order))
   else:blocked_draws.add(row['id'])
 initial=money(snapshot['settings']['initial_balance']);equity=0;curve=[]
 for order in accepted:
  equity+=order['pnl'];order['equity']=equity/100
  curve.append({'draw':order['target_draw'],'net':equity/100,'time':order['beijing_time']})
 result=summary(accepted,initial)
 result.update(initial_balance=initial/100,balance=(initial+equity)/100,curve=curve if len(curve)<=400 else curve[::max(1,len(curve)//399)])
 return result,accepted,{'settings':rule,'events':events,'blocked_orders':len(blocked_draws),'source_orders':len(source),'method':'订阅推送拦截模拟：策略独立运行，保留其原金额轨迹；只按已收盘K线判断，不改变真实订阅。按开奖时刻假设结算可用，未模拟网络及延迟。'}
