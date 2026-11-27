"""Several independently sized previous-result types in one strategy account."""
import copy,json
TYPES={'size':'大小','parity':'单双','小单':'小单','大双':'大双','大单':'大单','小双':'小双','a':'A球大小','b':'B球大小','c':'C球大小'}


def validate_types(selected):
 if not isinstance(selected,list) or not 1<=len(selected)<=len(TYPES) or any(not isinstance(t,str) or t not in TYPES for t in selected) or len(set(selected))!=len(selected):raise ValueError('请至少选择一个下单类型，不能重复或包含未知类型')


def settings_for(settings,kind):
 value=copy.deepcopy(settings)
 value.update(previous_types=None,axis=kind if kind in ('size','parity','a','b','c') else 'combo',direction=None if kind in ('size','parity','a','b','c') else kind)
 return value


def tick_selected(c,a,history,cfg,now):
 from .follow_strategy import tick_one,utcnow
 settings=json.loads(a['settings']);selected=settings['previous_types'];root=json.loads(a['state']);key=a['id'];target=history[-1]['id']+1
 if c.execute('SELECT 1 FROM custom_decisions WHERE strategy=? AND target_draw=?',(key,target)).fetchone():return
 root.setdefault('type_states',{})
 reasons=[]
 for kind in selected:
  existing=c.execute('SELECT action,reason FROM custom_decisions WHERE strategy=? AND target_draw=?',(key+'::'+kind,target)).fetchone()
  if existing:
   reasons.append(TYPES[kind]+'：'+existing['reason'])
   continue
  current=dict(c.execute('SELECT * FROM custom_strategies WHERE id=?',(key,)).fetchone())
  current['settings']=json.dumps(settings_for(settings,kind),ensure_ascii=False)
  current['state']=json.dumps(root['type_states'].get(kind,{}),ensure_ascii=False)
  tick_one(c,current,history,cfg,now,selection_type=kind)
  root['type_states'][kind]=json.loads(c.execute('SELECT state FROM custom_strategies WHERE id=?',(key,)).fetchone()[0])
  c.execute('UPDATE custom_strategies SET state=? WHERE id=?',(json.dumps(root,ensure_ascii=False),key))
  decision=c.execute('SELECT action,reason FROM custom_decisions WHERE strategy=? AND target_draw=?',(key+'::'+kind,target)).fetchone()
  if decision:reasons.append(TYPES[kind]+'：'+decision['reason'])
 c.execute('INSERT OR IGNORE INTO custom_decisions VALUES(?,?,?,?,?)',(key,target,utcnow(),'order' if c.execute('SELECT 1 FROM custom_orders WHERE strategy=? AND target_draw=?',(key,target)).fetchone() else 'skip','；'.join(reasons)))


def signal_type(order):
 raw=order.get('evidence') or order.get('strategy_json')
 if not raw:return None
 try:return json.loads(raw).get('previous_type')
 except (ValueError,TypeError,AttributeError):return None


def replay_selected(rows,snapshot,on_progress=None):
 from collections import Counter
 from datetime import timezone,timedelta
 from zoneinfo import ZoneInfo
 from . import maintenance,win_pause
 from .model import money,previous_direction,direction_hits,settlement
 from .follow_strategy import previous_amount
 from .trend_strategy import risk_reason,summary
 settings=snapshot['settings'];selected=settings['previous_types'];odds=snapshot['odds'];initial=money(settings['initial_balance'])
 balance=peak=initial;daily=0;day=None;states={kind:{} for kind in selected};orders=[];skips=Counter();equity=0;curve=[];settings_by_type={kind:settings_for(settings,kind) for kind in selected}
 for i,row in enumerate(rows):
  if on_progress and i%1000==0:on_progress(i,len(rows))
  if not i:skips['观察窗口预热']+=1;continue
  prev=rows[i-1];target=row['id'];decision=prev['_time']+timedelta(seconds=1)
  decision_day=decision.astimezone(timezone.utc).date()
  if day!=decision_day:day=decision_day;daily=0
  reason=maintenance.block_reason(prev,row['_time']-timedelta(microseconds=1),snapshot.get('maintenance_guard',True),row['_time'])
  if reason:skips[reason]+=1;continue
  if prev['id']+1!=target:skips['开奖缺期边界']+=1;continue
  proposed=[];reserved=0
  for kind in selected:
   state=states[kind];s=settings_by_type[kind];reason=win_pause.paused(state,target)
   direction=previous_direction(prev,s['axis'])
   if s['direction'] and direction!=s['direction']:reason=reason or '入场方向不符合所选类型'
   stake=state.get('next_stake',s['base_cents'])
   if s.get('loss_increment_cents',0)==0 and s.get('win_decrement_cents',0)==0:stake=s['base_cents']
   reason=reason or risk_reason(balance-reserved,peak,stake,daily-reserved,s,odds)
   if reason:skips[TYPES[kind]+'：'+reason]+=1;continue
   proposed.append((kind,s,direction,stake,state.get('attempt',0)+1));reserved+=stake
  balance-=reserved
  settle_day=row['_time'].astimezone(timezone.utc).date()
  if day!=settle_day:day=settle_day;daily=0
  for kind,s,direction,stake,step in proposed:
   state=states[kind];cycle=state.setdefault('cycle',1);hit=direction_hits(direction,row['size'],row['parity'],row)
   paid=settlement(stake,direction,row['total'],hit,odds);balance+=paid['credit'];peak=max(peak,balance);daily+=paid['pnl'];equity+=paid['pnl']
   state['next_stake']=previous_amount(stake,paid['outcome'],s,state)
   state.update(losses=0 if paid['outcome']=='win' else state.get('losses',0)+int(paid['outcome']=='loss'),last_settled_draw=target,last_direction=direction,attempt=step)
   win_pause.settled(state,paid['outcome'],target,s.get('win_limit',0),s.get('pause_draws',20),s.get('win_loss_difference_limit',0))
   orders.append({'target_draw':target,'cutoff_draw':prev['id'],'beijing_time':row['_time'].astimezone(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d %H:%M:%S'),'previous_type':kind,'direction':direction,'ratio':None,'stake':stake,'credit':paid['credit'],'status':'settled','pnl':paid['pnl'],'hit':int(hit),'outcome':paid['outcome'],'total':row['total'],'cycle':cycle,'step':step,'max_bets':None,'round_end':False,'pause_until':state.get('pause_until',0),'equity':equity/100})
   curve.append({'draw':target,'net':equity/100,'time':row['_time'].isoformat()})
 active_draws=len({order['target_draw'] for order in orders})
 result=summary(orders,initial);result.update(curve=curve if len(curve)<=400 else [curve[int(i*(len(curve)-1)/399)] for i in range(400)],skips=dict(skips),periods_skipped=len(rows)-active_draws,active_draws=active_draws,skip_counting='type-period',initial_balance=initial/100,balance=balance/100)
 if on_progress:on_progress(len(rows),len(rows))
 return result,orders
