from dataclasses import asdict
from datetime import datetime,timezone
from .model import Strategy,payout,rounded_ratio,money,utcnow,settlement,stake_limit_reason
import json
from . import win_pause,maintenance

def choose(candidates,history,mode,odds=None,base_cents=None,restart_policy=None,direction=None):
 if odds is not None:candidates=[c for c in candidates if c.get('validation',{}).get('odds')==odds]
 if base_cents is not None:candidates=[c for c in candidates if c['strategy'].get('base_cents',10000)==base_cents]
 if restart_policy is not None:candidates=[c for c in candidates if c.get('validation',{}).get('restart_policy','immediate')==restart_policy]
 if direction in ('大','小','单','双'):candidates=[c for c in candidates if c['strategy']['direction']==direction]
 if not candidates:return None
 pool=candidates[:1] if mode=='monthly' else candidates
 for c in pool:
  s=Strategy(**c['strategy'])
  if s.triggered(history):return c
 return None

def replay_portfolio(rows,cfg,schedule,start,end):
 from .engine import Prepared
 refunds=profit_wins=0;p=Prepared(rows);settings=cfg['simulation'];initial=money(settings['initial_balance']);net=peak=dd=invest=bets=wins=0;active=None;stake=attempt=loss=0;day='';daily=0;orders=[];halt='';last_switch=-100000;oldkey=None;waiting_strategy=None;pause_state={}
 for i,r in enumerate(rows):
  if r['source_time']<start or r['source_time']>=end:continue
  if win_pause.paused(pause_state,r['id']):continue
  if i<30 or rows[i-1]['id']+1!=r['id']:active=None;continue
  if p.breaks[i]-p.breaks[max(0,i-30)+1]:active=None;continue
  if r['source_time'][:10]!=day:day=r['source_time'][:10];daily=0
  if active is None:
   if waiting_strategy is not None:
    if not waiting_strategy.triggered(rows[max(0,i-1000):i]):waiting_strategy=None
    continue
   candidates=schedule.get(r['source_time'][:7],[]);selected=choose(candidates,rows[max(0,i-1000):i],settings['mode'] if settings['mode']!='fixed' else 'adaptive',cfg['odds'],settings['fixed']['base_cents'],settings.get('round_restart','immediate'),direction=settings['fixed'].get('direction'))
   if selected is None:continue
   if selected['key']!=oldkey:
    if r['id']-last_switch<settings['switch_cooldown_draws']:continue
    oldkey=selected['key'];last_switch=r['id']
   active=Strategy(**selected['strategy']);stake=active.base_cents;attempt=loss=0
  limit=stake_limit_reason(stake,cfg['odds'])
  if limit:halt=limit;break
  if initial+net<stake:halt='insufficient_balance';break
  if stake>money(settings['max_stake']):halt='stake_limit';break
  if money(settings['max_drawdown'])>0 and peak-net+stake>money(settings['max_drawdown']):halt='drawdown_limit';break
  if money(settings['daily_loss_limit'])>0 and daily-stake< -money(settings['daily_loss_limit']):active=None;continue
  hit=r[active.axis]==active.direction;paid=settlement(stake,active.direction,r['total'],hit,cfg['odds']);pnl=paid['pnl'];refunds+=paid['outcome']=='refund';profit_wins+=paid['outcome']=='win';bets+=1;wins+=hit;invest+=stake;net+=pnl;daily+=pnl;peak=max(peak,net);dd=max(dd,peak-net);attempt+=1
  orders.append({'draw':r['id'],'time':r['source_time'],'key':active.key,'direction':active.direction,'stake':stake/100,'outcome':paid['outcome'],'return':paid['credit']/100,'pnl':pnl/100,'equity':net/100})
  if hit:stake=active.base_cents;loss=0
  else:loss+=1;stake=rounded_ratio(stake,active.multiplier_hundredths)
  win_pause.settled(pause_state,paid['outcome'],r['id'],settings.get('win_limit',0),settings.get('pause_draws',20), settings.get('win_loss_difference_limit',0))
  if (pause_state.get('difference_paused',False) or pause_state.get('pause_until',0)>r['id']):active=None;waiting_strategy=None
  elif (active.max_losses>0 and loss>=active.max_losses) or attempt>=active.max_bets:
   waiting_strategy=active if (active.max_losses>0 and loss>=active.max_losses) and settings.get('round_restart','immediate')=='condition_reset' else None;active=None
 return {'odds':cfg['odds'].copy(),'refunds':refunds,'profitable_wins':profit_wins,'profit_win_rate':profit_wins/bets if bets else None,'routing_mode':settings['mode'] if settings['mode']!='fixed' else 'adaptive','bets':bets,'wins':wins,'win_rate':wins/bets if bets else None,'net':net/100,'investment':invest/100,'roi':net/invest if invest else None,'max_drawdown':dd/100,'balance':(initial+net)/100,'halt':halt,'orders':orders,'last_order_draw':orders[-1]['draw'] if orders else None,'last_order_time':orders[-1]['time'] if orders else None}

def ensure_account(db,cfg):
 with db.tx() as c:
  initial=money(cfg['simulation']['initial_balance']);c.execute('INSERT OR IGNORE INTO accounts(id,balance,initial_balance,peak,state) VALUES(1,?,?,?,?)',(initial,initial,initial,'{}'))

def settle(db,cfg):
 # Atomic debit/credit + state; repeated calls or restarts cannot settle twice.
 with db.tx() as c:
  pending=c.execute("SELECT * FROM orders WHERE status='pending' ORDER BY target_draw").fetchall()
  for o in pending:
   if c.execute('SELECT 1 FROM conflicts WHERE draw_id=? AND resolved=0 LIMIT 1',(o['target_draw'],)).fetchone():continue
   r=c.execute('SELECT * FROM draws WHERE id=?',(o['target_draw'],)).fetchone()
   if r is None:continue
   account=dict(c.execute('SELECT * FROM accounts WHERE id=1').fetchone());state=json.loads(account['state']);s=Strategy(**json.loads(o['strategy_json']));hit=r[s.axis]==s.direction
   details=json.loads(o['reason']);odds=details['odds'];paid=settlement(o['stake'],s.direction,r['total'],hit,odds);pnl=paid['pnl'];credit=paid['credit'];balance=account['balance']+credit
   details['settlement']=paid
   c.execute('UPDATE orders SET reason=? WHERE id=?',(json.dumps(details,ensure_ascii=False),o['id']))
   c.execute("UPDATE orders SET status='settled',pnl=?,total=?,settled=? WHERE id=? AND status='pending'",(pnl,r['total'],utcnow(),o['id']))
   c.execute('INSERT INTO ledger(created,order_id,kind,amount,balance) VALUES(?,?,?,?,?)',(utcnow(),o['id'],'settle',credit,balance))
   state['attempt']=o['round_attempt'] or state.get('attempt',0)+1;state['losses']=0 if hit else state.get('losses',0)+1;state['stake']=s.base_cents if hit else rounded_ratio(o['stake'],s.multiplier_hundredths)
   state['last_settled_draw']=o['target_draw']
   pause_cfg=details.get('win_pause',{});win_pause.settled(state,paid['outcome'],o['target_draw'],pause_cfg.get('win_limit',0),pause_cfg.get('pause_draws',20), pause_cfg.get('win_loss_difference_limit',0))
   win_stop=(state.get('difference_paused',False) or state.get('pause_until',0)>o['target_draw'])
   state['active']=(s.max_losses==0 or state['losses']<s.max_losses) and state['attempt']<s.max_bets and not win_stop
   if o['round_id']:
    ended=not state['active'];why=('胜负差值触发暂停' if state.get('difference_paused') else '连续盈利触发暂停') if win_stop else '连续亏损达到上限' if (s.max_losses>0 and state['losses']>=s.max_losses) else '每轮下单数达到上限' if ended else None
    c.execute('UPDATE rounds SET settled_count=settled_count+1,status=?,ended=?,end_reason=? WHERE id=?',('ended' if ended else 'active',utcnow() if ended else None,why,o['round_id']))
   if not state['active']:
    state['waiting_rearm']=not win_stop and (s.max_losses>0 and state['losses']>=s.max_losses) and cfg['simulation'].get('round_restart','immediate')=='condition_reset';state['rearm_strategy']=state.get('strategy',json.loads(o['strategy_json']));state['round_end_reason']=why if o['round_id'] else '本轮结束'
   c.execute('UPDATE accounts SET balance=?,peak=MAX(peak,?),state=? WHERE id=1',(balance,balance,json.dumps(state,ensure_ascii=False)))

def tick(db,cfg,now=None):
 ensure_account(db,cfg);settle(db,cfg)
 if not cfg['simulation']['enabled']:return {'action':'disabled'}
 now=now or datetime.now(timezone.utc);history=db.tail(1001)
 if not history:return {'action':'skip','reason':'没有开奖数据'}
 latest=history[-1];target=latest['id']+1;reason='';candidate=None;settings=cfg['simulation']
 if not latest['draw_utc']:reason='开奖时区未确认：请配置source_timezone并重新导入/采集'
 else:
  age=(now-datetime.fromisoformat(latest['draw_utc'])).total_seconds()
  received=(now-datetime.fromisoformat(latest['first_seen'])).total_seconds()
  if age< -30 or age>cfg['collector']['max_age_seconds']:reason='开奖数据过期或时间在未来'
  elif age>settings.get('max_decision_age_seconds',120):reason='已超出本期模拟决策时窗，跳过'
  elif received>settings['max_received_delay_seconds']:reason='本次决策距离首次收到数据过久，跳过'
 if len(history)<30 or any(b['id']!=a['id']+1 for a,b in zip(history[-30:],history[-29:])):reason='最近30期不足或存在缺期'
 month=now.astimezone(timezone.utc).strftime('%Y-%m')
 with db.tx() as c:
  account=dict(c.execute('SELECT * FROM accounts WHERE id=1').fetchone());state=json.loads(account['state'])
  if c.execute('SELECT 1 FROM orders WHERE target_draw=?',(target,)).fetchone():return {'action':'existing','target':target}
  if c.execute("SELECT 1 FROM orders WHERE status='pending'").fetchone():return {'action':'skip','reason':'存在待补开奖的未结算模拟订单'}
  pause_reason=win_pause.paused(state,target)
  c.execute('UPDATE accounts SET state=? WHERE id=1',(json.dumps(state,ensure_ascii=False),))
  if pause_reason:
   c.execute('INSERT OR IGNORE INTO decisions(target_draw,created,cutoff_draw,action,reason) VALUES(?,?,?,?,?)',(target,utcnow(),latest['id'],'skip',pause_reason));return {'action':'skip','reason':pause_reason,'target':target}
  rest_reason=maintenance.live_guard(c,latest,now,cfg)
  if rest_reason:
   if state.get('active') and state.get('round_id'):c.execute("UPDATE rounds SET status='ended',ended=?,end_reason=? WHERE id=?",(utcnow(),'暂停休息，结束本轮',state['round_id']))
   state.update(active=False,waiting_rearm=False);c.execute('UPDATE accounts SET state=? WHERE id=1',(json.dumps(state,ensure_ascii=False),))
   c.execute('INSERT OR IGNORE INTO decisions(target_draw,created,cutoff_draw,action,reason) VALUES(?,?,?,?,?)',(target,utcnow(),latest['id'],'skip',rest_reason));return {'action':'skip','reason':rest_reason,'target':target}
  if c.execute('SELECT 1 FROM conflicts WHERE resolved=0 LIMIT 1').fetchone():reason='存在未解决的数据冲突'
  if account['paused']:reason='账户已暂停：'+account['pause_reason']
  if state.get('active') and state.get('last_settled_draw',latest['id'])!=latest['id']:
   state['active']=False;state['waiting_rearm']=settings.get('round_restart','immediate')=='condition_reset';state['rearm_strategy']=state.get('strategy');state['round_end_reason']='中间期次未参与，结束旧轮'
   if state.get('round_id'):c.execute("UPDATE rounds SET status='ended',ended=?,end_reason=? WHERE id=?",(utcnow(),state['round_end_reason'],state['round_id']))
   c.execute('UPDATE accounts SET state=? WHERE id=1',(json.dumps(state,ensure_ascii=False),))
   c.execute('INSERT INTO switches(created,target_draw,old_key,new_key,reason) VALUES(?,?,?,?,?)',(utcnow(),target,state.get('last_key'),None,'恢复时已跨过未参与期次，结束旧轮'))
  if state.get('active') and state.get('round_id'):
   r=c.execute('SELECT orders_count,max_bets FROM rounds WHERE id=?',(state['round_id'],)).fetchone()
   if r and r['orders_count']>=r['max_bets']:
    state['active']=False;state['waiting_rearm']=False;state['rearm_strategy']=state.get('strategy');state['round_end_reason']='每轮下单数达到上限'
    c.execute('UPDATE accounts SET state=? WHERE id=1',(json.dumps(state,ensure_ascii=False),))
  # Existing rounds that stopped at the order count adopt the new re-entry rule.
  if not state.get('active') and state.get('waiting_rearm') and state.get('strategy'):
   previous=Strategy(**state['strategy'])
   if state.get('attempt',0)>=previous.max_bets and (previous.max_losses==0 or state.get('losses',0)<previous.max_losses):
    state['waiting_rearm']=False;c.execute('UPDATE accounts SET state=? WHERE id=1',(json.dumps(state,ensure_ascii=False),))
  rearming=False
  if not state.get('active') and state.get('waiting_rearm') and settings.get('round_restart','immediate')=='condition_reset':
   previous=Strategy(**state['rearm_strategy'])
   if not reason and len(history)>=previous.lookback and not previous.triggered(history):
    state['waiting_rearm']=False;c.execute('UPDATE accounts SET state=? WHERE id=1',(json.dumps(state,ensure_ascii=False),));reason='入场条件已退出，等待再次满足后开新轮'
   else:reason=reason or '本轮已结束，等待入场条件先退出后再次满足'
   rearming=True
  if rearming:candidate=None
  elif state.get('active'):
   s=Strategy(**state['strategy']);candidate={'strategy':asdict(s),'key':s.key}
  elif settings['mode']=='fixed':
   s=Strategy(**settings['fixed']);s=s.resolve(history) if s.triggered(history) else None;candidate={'strategy':asdict(s),'key':s.key} if s else None
  else:
   selection=c.execute('SELECT * FROM selections WHERE month=?',(month,)).fetchone()
   candidate=choose(json.loads(selection['candidates']) if selection else [],history,settings['mode'],cfg['odds'],settings['fixed']['base_cents'],settings.get('round_restart','immediate'),direction=settings['fixed'].get('direction'))
  if candidate is None:reason=reason or '没有合格且触发的策略'
  if candidate:
   s=Strategy(**candidate['strategy']);stake=state.get('stake',s.base_cents) if state.get('active') else s.base_cents
   if len(history)<s.lookback or any(b['id']!=a['id']+1 for a,b in zip(history[-s.lookback:],history[-s.lookback+1:])):reason='策略窗口存在缺期或样本不足'
   lastkey=state.get('last_key');switching=lastkey!=s.key
   if switching and target-state.get('last_switch',-100000)<settings['switch_cooldown_draws']:reason='策略切换冷却中'
   market_limit=stake_limit_reason(stake,cfg['odds'])
   if market_limit:reason='超出玩法单注限额（大小单双1—20000U）'
   if stake>money(settings['max_stake']):reason='单笔金额超过上限'
   if account['balance']<stake:reason='余额不足'
   if money(settings['max_drawdown'])>0 and account['peak']-account['balance']+stake>money(settings['max_drawdown']):reason='账户最大回撤限制'
   # Daily limits use UTC settlement date, independent of machine timezone.
   day=now.astimezone(timezone.utc).date().isoformat();daily=c.execute("SELECT COALESCE(SUM(pnl),0) FROM orders WHERE status='settled' AND settled LIKE ?",(day+'%',)).fetchone()[0]
   if money(settings['daily_loss_limit'])>0 and daily-stake< -money(settings['daily_loss_limit']):reason='UTC当日亏损限制'
  if reason:
   c.execute('INSERT OR IGNORE INTO decisions(target_draw,created,cutoff_draw,action,reason,strategy_key) VALUES(?,?,?,?,?,?)',(target,utcnow(),latest['id'],'skip',reason,candidate['key'] if candidate else None))
   return {'action':'skip','reason':reason,'target':target}
  if c.execute('SELECT 1 FROM decisions WHERE target_draw=?',(target,)).fetchone():return {'action':'skip','reason':'该期已有决策，避免事后重写'}
  if not state.get('active'):
   round_id=c.execute("INSERT INTO rounds(created,status,strategy_json,max_bets,first_draw) VALUES(?,'active',?,?,?)",(utcnow(),json.dumps(asdict(s),ensure_ascii=False),s.max_bets,target)).lastrowid
   state.update(active=True,strategy=asdict(s),stake=stake,attempt=0,losses=0,round_id=round_id,waiting_rearm=False)
  current_round=c.execute('SELECT orders_count,max_bets FROM rounds WHERE id=?',(state['round_id'],)).fetchone()
  slot=current_round['orders_count']+1
  if slot>current_round['max_bets']:raise ValueError('本轮下单数已达到上限，禁止追加订单')
  if switching:
   c.execute('INSERT INTO switches(created,target_draw,old_key,new_key,reason) VALUES(?,?,?,?,?)',(utcnow(),target,lastkey,s.key,'轮结束后的策略选择'));state['last_key']=s.key;state['last_switch']=target
  cur=c.execute("INSERT INTO orders(target_draw,created,cutoff_draw,strategy_key,strategy_json,selection_month,stake,status,reason,round_id,round_attempt) VALUES(?,?,?,?,?,?,?,'pending',?,?,?)",(target,utcnow(),latest['id'],s.key,json.dumps(asdict(s),ensure_ascii=False),month,stake,json.dumps({'odds':cfg['odds'],'mode':settings['mode'],'win_pause':{'win_limit':settings.get('win_limit',0),'pause_draws':settings.get('pause_draws',20),'win_loss_difference_limit':settings.get('win_loss_difference_limit',0)}},ensure_ascii=False),state['round_id'],slot))
  c.execute('UPDATE rounds SET orders_count=?,last_draw=? WHERE id=?',(slot,target,state['round_id']))
  balance=account['balance']-stake;c.execute('INSERT INTO ledger(created,order_id,kind,amount,balance) VALUES(?,?,?,?,?)',(utcnow(),cur.lastrowid,'stake',-stake,balance));c.execute('UPDATE accounts SET balance=?,state=? WHERE id=1',(balance,json.dumps(state,ensure_ascii=False)))
  c.execute('INSERT INTO decisions VALUES(?,?,?,?,?,?)',(target,utcnow(),latest['id'],'order','场景符合且通过资金检查',s.key))
  return {'action':'order','target':target,'direction':s.direction,'stake':stake/100,'round_id':state['round_id'],'round_attempt':slot}
