"""Hourly minority windows and independently configurable majority followers."""
import copy,json,uuid
from . import win_pause,maintenance
from datetime import datetime,timedelta,timezone
from zoneinfo import ZoneInfo
from .model import money,utcnow,settlement,rounded_ratio,stake_limit_reason
from .trend_strategy import risk_reason,signal,progress
KINDS=('hourly','majority','previous');BJ=ZoneInfo('Asia/Shanghai')
def defaults(cfg,kind,axis='size'):
 value={'strategy_type':kind,'axis':axis,'win_limit':5 if kind=='majority' else 0,'pause_draws':20,'observe_hours':2,'hour_difference':5,'follow_hours':2,'lookback':20,'base_cents':50000,'loss_increment_cents':10000,'multiplier_hundredths':200,'stake_mode':'fixed' if kind=='hourly' else 'additive','max_losses':3,'initial_balance':cfg['simulation']['initial_balance'],**{k:cfg['simulation'][k] for k in ('daily_loss_limit','max_drawdown','max_stake')}}
 if kind=='previous':value.update(win_decrement_cents=10000,stake_floor='market',max_stake=20000,win_limit=0,max_increase_steps=0,max_decrease_steps=0)
 return value

def previous_amount(stake,outcome,settings,state=None):
 state=state if state is not None else {}
 base=settings['base_cents'];floor=base if settings['stake_floor']=='base' else 100
 # Adjustment counts belong to one amount cycle, not a consecutive win/loss
 # streak. Start the revised counting rule cleanly for old persisted states.
 if state.get('adjustment_count_version')!=2:
  state.update(increase_steps=0,decrease_steps=0,adjustment_count_version=2)
 if outcome not in ('win','loss'):
  return stake
 increasing=outcome=='loss';key='increase_steps' if increasing else 'decrease_steps'
 amount=stake+settings['loss_increment_cents'] if increasing else max(floor,stake-settings['win_decrement_cents'])
 adjustment=settings['loss_increment_cents'] if increasing else settings['win_decrement_cents']
 if adjustment==0:return amount
 limit=settings.get('max_increase_steps' if increasing else 'max_decrease_steps',0)
 state[key]=state.get(key,0)+1
 if limit and state[key]>=limit:
  state.update(increase_steps=0,decrease_steps=0);return base
 return amount

def validate(cfg,value):
 if not isinstance(value,dict):raise ValueError('策略参数必须是对象')
 value={**{'win_limit':5 if value.get('strategy_type')=='majority' else 0,'pause_draws':20},**value}
 win_pause.validate(value['win_limit'],value['pause_draws'])
 if value.get('strategy_type')=='previous':value={**{'max_increase_steps':0,'max_decrease_steps':0},**value}
 expected=set(defaults(cfg,value.get('strategy_type','hourly')))
 if set(value)!=expected:raise ValueError('跟随策略参数不完整或包含未知字段')
 if value['strategy_type'] not in KINDS or value['axis'] not in ('size','parity'):raise ValueError('策略类型或维度无效')
 for k in ('observe_hours','hour_difference','follow_hours','lookback','base_cents','loss_increment_cents','multiplier_hundredths','max_losses'):
  if type(value[k]) is not int:raise ValueError('期数、小时和金额分必须为整数')
 if not 1<=value['observe_hours']<=24 or not 1<=value['hour_difference']<=30 or not 1<=value['follow_hours']<=24:raise ValueError('观察/跟随小时数须为1—24，小时差值须为1—30')
 if not 2<=value['lookback']<=1000 or not 1<=value['max_losses']<=10:raise ValueError('观察期数或连亏重置次数无效')
 if value['stake_mode'] not in ('fixed','additive','multiply') or value['loss_increment_cents']<0 or not 100<=value['multiplier_hundredths']<=500:raise ValueError('金额递增设置无效')
 from decimal import Decimal
 for k in ('initial_balance','daily_loss_limit','max_drawdown','max_stake'):
  try:
   x=Decimal(str(value[k]))
   if not x.is_finite() or x<=0:raise ValueError('资金参数须为有限正数')
  except (ArithmeticError,TypeError):raise ValueError('资金参数无效')
 if stake_limit_reason(value['base_cents'],cfg['odds']) or value['base_cents']>money(value['max_stake']):raise ValueError('基础金额超过玩法或账户上限')
 if value['strategy_type']=='previous':
  for field in ('max_increase_steps','max_decrease_steps'):
   if type(value[field]) is not int or not 0<=value[field]<=1000:raise ValueError('累计加减额期数须为0—1000，0表示不限')
  if type(value['win_decrement_cents'])!=int or value['win_decrement_cents']<0 or value['stake_floor'] not in ('base','market'):raise ValueError('赢后减额或最低金额设置无效')
 return copy.deepcopy(value)
def save(service,body):
 from .strategy_delete import require_available
 require_available(service.db,body.get("id"))
 name=body.get('name','')
 if not isinstance(name,str) or not 1<=len(name.strip())<=60:raise ValueError('策略名称需要1—60个字符')
 value=validate(service.cfg,body.get('settings'));key=body.get('id')
 with service.lock,service.db.tx() as c:
  require_available(service.db,body.get("id"))
  if key:
   old=c.execute('SELECT * FROM custom_strategies WHERE id=?',(key,)).fetchone()
   if not old or json.loads(old['settings']).get('strategy_type')!=value['strategy_type']:raise ValueError('策略不存在或类型不匹配')
   if old['enabled']:raise ValueError('请先停用策略再编辑参数')
   if c.execute("SELECT 1 FROM custom_orders WHERE strategy=? AND status='pending'",(key,)).fetchone():raise ValueError('请等待已有订单结算后再编辑')
   state={'cycle':json.loads(old['state']).get('cycle',0)+1}
   c.execute('UPDATE custom_strategies SET name=?,settings=?,state=? WHERE id=?',(name.strip(),json.dumps(value,ensure_ascii=False),json.dumps(state),key))
  else:
   if c.execute('SELECT COUNT(*) FROM custom_strategies WHERE id NOT IN (SELECT id FROM deleted_strategies)').fetchone()[0]>=100:raise ValueError('最多添加100条策略')
   key='custom_'+uuid.uuid4().hex;initial=money(value['initial_balance'])
   c.execute('INSERT INTO custom_strategies(id,name,settings,balance,initial_balance,peak,created) VALUES(?,?,?,?,?,?,?)',(key,name.strip(),json.dumps(value,ensure_ascii=False),initial,initial,initial,utcnow()))
  c.execute('INSERT INTO strategy_edits(strategy,created,settings) VALUES(?,?,?)',(key,utcnow(),json.dumps({'name':name.strip(),'settings':value},ensure_ascii=False)))
 return {'ok':True,'id':key}
def next_stake(settings,losses):
 base=settings['base_cents']
 if settings['stake_mode']=='fixed':return base
 if settings['stake_mode']=='additive':return base+settings['loss_increment_cents']*losses
 for _ in range(losses):base=rounded_ratio(base,settings['multiplier_hundredths'])
 return base
def settle_one(c,o,r,a):
 plan=json.loads(o['strategy_json']);settings=plan['settings'];state=json.loads(a['state']);hit=r[settings['axis']]==o['direction'];paid=settlement(o['stake'],o['direction'],r['total'],hit,json.loads(o['odds_json']));balance=a['balance']+paid['credit'];losses,reset=progress(state.get('losses',0),hit,settings['max_losses'])
 if settings['strategy_type']=='previous':
  losses=0 if paid['outcome']=='win' else state.get('losses',0)+int(paid['outcome']=='loss')
  state['next_stake']=previous_amount(o['stake'],paid['outcome'],settings,state)
 state.update(losses=losses,last_settled_draw=o['target_draw'],last_direction=o['direction'],attempt=o['step'])
 if settings['strategy_type']=='majority':
  if reset:state['cycle']=state.get('cycle',1)+1
 win_pause.settled(state,paid['outcome'],o['target_draw'],settings.get('win_limit',5 if settings['strategy_type']=='majority' else 0),settings.get('pause_draws',20))
 if state.get('pause_until',0)>o['target_draw'] and settings['strategy_type']=='hourly':state.update(active=False,follow_end=None)
 c.execute("UPDATE custom_orders SET status='settled',pnl=?,hit=?,outcome=?,total=?,settled=? WHERE id=?",(paid['pnl'],int(hit),paid['outcome'],r['total'],utcnow(),o['id']))
 c.execute('INSERT INTO custom_ledger(strategy,order_id,created,kind,amount,balance) VALUES(?,?,?,?,?,?)',(o['strategy'],o['id'],utcnow(),'settle',paid['credit'],balance))
 c.execute('UPDATE custom_strategies SET balance=?,peak=MAX(peak,?),state=? WHERE id=?',(balance,balance,json.dumps(state,ensure_ascii=False),o['strategy']))
def hourly_signal(history,hour,settings,known_at=None):
 """Use only fully closed Beijing hours, with contiguous raw IDs and boundary coverage."""
 hour=hour.astimezone(BJ).replace(minute=0,second=0,microsecond=0);start=hour-timedelta(hours=settings['observe_hours']);known_at=known_at or hour
 timed=[]
 for r in history:
  if not r.get('draw_utc'):continue
  t=datetime.fromisoformat(r['draw_utc'])
  if t.tzinfo is None:continue
  if r.get('first_seen') and datetime.fromisoformat(r['first_seen'])>known_at:continue
  timed.append((r,t.astimezone(BJ)))
 before=[x for x in timed if x[1]<start];window=[x for x in timed if start<=x[1]<hour]
 if not before or not window:return None,[],'观察小时数据不足'
 chain=[before[-1],*window]
 if any(b[0]['id']!=a[0]['id']+1 or not (0<(b[1]-a[1]).total_seconds()<=240 or maintenance.covered_gap(a[1],b[1])) for a,b in zip(chain,chain[1:])):return None,[],'观察小时存在缺期或时间间隔异常'
 labels=('大','小') if settings['axis']=='size' else ('单','双');evidence=[];directions=[]
 for i in range(settings['observe_hours']):
  begin=start+timedelta(hours=i);end=begin+timedelta(hours=1);bucket=[x for x in window if begin<=x[1]<end]
  if not bucket or not maintenance.hour_start_covered(begin,bucket[0][1]) or (end-bucket[-1][1]).total_seconds()>240:return None,evidence,'观察小时覆盖不完整'
  counts={v:sum(r[settings['axis']]==v for r,_ in bucket) for v in labels};difference=abs(counts[labels[0]]-counts[labels[1]]);minority=min(labels,key=counts.get)
  evidence.append({'hour':begin.isoformat(),'counts':counts,'difference':difference,'minority':minority,'first':bucket[0][0]['id'],'last':bucket[-1][0]['id']})
  if difference<settings['hour_difference']:return None,evidence,'每小时差值未达到阈值'
  directions.append(minority)
 if len(set(directions))!=1:return None,evidence,'连续小时少数方向不一致'
 return directions[0],evidence,''
def target_time(c,target):
 r=c.execute("SELECT value FROM runtime_state WHERE key='draw_timer'").fetchone()
 if not r:return None
 try:
  plan=json.loads(r[0]);t=datetime.fromisoformat(plan['next_draw_at'])
  return t if int(plan['next_draw_id'])==target and t.tzinfo else None
 except (KeyError,ValueError,TypeError):return None
def tick_one(c,a,history,cfg,now):
 settings=json.loads(a['settings']);state=json.loads(a['state']);latest=history[-1];target=latest['id']+1;key=a['id']
 if c.execute('SELECT 1 FROM custom_decisions WHERE strategy=? AND target_draw=?',(key,target)).fetchone():return
 pause_reason=win_pause.paused(state,target)
 c.execute('UPDATE custom_strategies SET state=? WHERE id=?',(json.dumps(state,ensure_ascii=False),key))
 if pause_reason:c.execute('INSERT INTO custom_decisions VALUES(?,?,?,?,?)',(key,target,utcnow(),'skip',pause_reason));return
 rest_reason=maintenance.live_guard(c,latest,now,cfg)
 if rest_reason:c.execute('INSERT INTO custom_decisions VALUES(?,?,?,?,?)',(key,target,utcnow(),'skip',rest_reason));return
 reason='';transient=False;direction=None;ratio=None;evidence=[]
 if not latest['draw_utc']:reason='开奖时区未确认'
 else:
  age=(now-datetime.fromisoformat(latest['draw_utc'])).total_seconds();delay=(now-datetime.fromisoformat(latest['first_seen'])).total_seconds()
  if age< -30 or age>cfg['simulation'].get('max_decision_age_seconds',120) or delay>cfg['simulation']['max_received_delay_seconds']:reason='数据超过下单时窗'
 if c.execute('SELECT 1 FROM conflicts WHERE resolved=0 LIMIT 1').fetchone():reason='存在待核验的数据冲突'
 if c.execute("SELECT 1 FROM custom_orders WHERE strategy=? AND status='pending'",(key,)).fetchone():reason='等待上一单结算'
 if settings['strategy_type']=='previous':direction=latest[settings['axis']];evidence=[{'cutoff_draw':latest['id'],'direction':direction}]
 elif settings['strategy_type']=='majority':
  previous=c.execute('SELECT direction FROM custom_orders WHERE strategy=? ORDER BY target_draw DESC LIMIT 1',(key,)).fetchone();choice=signal(history,settings['axis'],previous[0] if previous else None,settings['lookback'])
  if choice:direction,ratio=choice
  else:reason=reason or '观察窗口不足/缺期，或持平且没有历史方向'
 else:
  hour=now.astimezone(BJ).replace(minute=0,second=0,microsecond=0);planned=target_time(c,target)
  if not planned:reason=reason or '等待下期开奖时间，避免跨小时下单';transient=True
  elif planned.astimezone(BJ).replace(minute=0,second=0,microsecond=0)>hour:reason=reason or '等待整点后完成小时观察';transient=True
  elif planned<now-timedelta(seconds=30):reason=reason or '目标开奖时间已过期'
  elif not reason:
   if state.get('follow_end') and planned<datetime.fromisoformat(state['follow_end']):direction=state['direction'];evidence=state.get('evidence',[])
   else:
    state['active']=False
    direction,evidence,why=hourly_signal(history,hour,settings,now)
    if direction:state.update(active=True,direction=direction,follow_start=hour.isoformat(),follow_end=(hour+timedelta(hours=settings['follow_hours'])).isoformat(),cycle=state.get('cycle',0)+1,losses=0,attempt=0,evidence=evidence)
    else:reason=why
 stake=state.get('next_stake',settings['base_cents']) if settings['strategy_type']=='previous' else next_stake(settings,state.get('losses',0));day=now.astimezone(timezone.utc).date().isoformat();daily=c.execute("SELECT COALESCE(SUM(pnl),0) FROM custom_orders WHERE strategy=? AND status='settled' AND substr(settled,1,10)=?",(key,day)).fetchone()[0]
 reason=reason or risk_reason(a['balance'],a['peak'],stake,daily,settings,cfg['odds'])
 if reason:
  c.execute('UPDATE custom_strategies SET state=? WHERE id=?',(json.dumps(state,ensure_ascii=False),key))
  if not transient:c.execute('INSERT INTO custom_decisions VALUES(?,?,?,?,?)',(key,target,utcnow(),'skip',reason))
  return
 cycle=state.setdefault('cycle',1);step=state.get('attempt',0)+1 if settings['strategy_type'] in ('hourly','previous') else state.get('losses',0)+1
 plan={'strategy_type':settings['strategy_type'],'settings':settings,'evidence':evidence,'follow_start':state.get('follow_start'),'follow_end':state.get('follow_end')}
 cur=c.execute("INSERT INTO custom_orders(strategy,target_draw,cutoff_draw,created,direction,ratio,stake,status,cycle,step,strategy_json,odds_json,restart_policy) VALUES(?,?,?,?,?,?,?,'pending',?,?,?,?,?)",(key,target,latest['id'],utcnow(),direction,ratio,stake,cycle,step,json.dumps(plan,ensure_ascii=False),json.dumps(cfg['odds']), 'follow'))
 balance=a['balance']-stake;c.execute('INSERT INTO custom_ledger(strategy,order_id,created,kind,amount,balance) VALUES(?,?,?,?,?,?)',(key,cur.lastrowid,utcnow(),'stake',-stake,balance));c.execute('UPDATE custom_strategies SET balance=?,state=? WHERE id=?',(balance,json.dumps(state,ensure_ascii=False),key));c.execute('INSERT INTO custom_decisions VALUES(?,?,?,?,?)',(key,target,utcnow(),'order','跟随上一期'+direction if settings['strategy_type']=='previous' else '连续小时少数方向一致，进入跟随期' if settings['strategy_type']=='hourly' else '占优跟随；持平沿用上一笔方向'))
def rule_text(s):
 axis='大小' if s['axis']=='size' else '单双'
 if s['strategy_type']=='previous':return f"跟随上一期{axis}；基础{s['base_cents']/100:g}U，赢后减{s['win_decrement_cents']/100:g}U，亏后加{s['loss_increment_cents']/100:g}U，累计加额{s.get('max_increase_steps',0)}次/累计减额{s.get('max_decrease_steps',0)}次（0不限），达到上限下一单恢复基础并清零两项计数；退本金金额不变"
 trigger=f"连续{s['observe_hours']}个完整北京时间小时，每小时{axis}差≥{s['hour_difference']}且少数同向；下小时起固定跟随少数{s['follow_hours']}小时，结束重评" if s['strategy_type']=='hourly' else f"前{s['lookback']}期{axis}占优跟随，持平沿用上一笔"
 amount='固定金额' if s['stake_mode']=='fixed' else f"亏后加{s['loss_increment_cents']/100:g}U" if s['stake_mode']=='additive' else f"亏后{s['multiplier_hundredths']/100:g}倍"
 pause=f"；连续盈利{s.get('win_limit',5)}单暂停{s.get('pause_draws',20)}期" if s.get('win_limit',5 if s['strategy_type']=='majority' else 0) else ''
 return trigger+pause+f"；基础{s['base_cents']/100:g}U，{amount}，{s['max_losses']}连亏恢复基础金额"
