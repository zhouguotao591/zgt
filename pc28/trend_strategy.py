"""Two independent 20-draw majority strategies with additive loss progression."""
from datetime import datetime,timezone
from zoneinfo import ZoneInfo
import json
from . import win_pause,maintenance
from .model import money,settlement,stake_limit_reason,utcnow
from .strategy_capital import sync_capital
AXES={'parity':('单双策略',('单','双')),'size':('大小策略',('大','小'))}
LOOKBACK=20;BASE=50000;INCREMENT=10000;MAX_LOSSES=3

def settings(cfg,axis):
 shared=cfg['simulation']
 result={'direction':None,'win_limit':5,'pause_draws':20,'win_loss_difference_limit':0,'lookback':20,'base_cents':50000,'loss_increment_cents':10000,'max_losses':3,'initial_balance':cfg.get('trend_simulation',{}).get('initial_balance_per_strategy',shared['initial_balance']),**{k:shared[k] for k in ('daily_loss_limit','max_drawdown','max_stake')}}
 result.update(cfg.get('trend_simulation',{}).get('strategies',{}).get(axis,{}));return result

def validate_settings(value,odds):
 if isinstance(value,dict):value={**{'direction':None,'win_limit':5,'pause_draws':20,'win_loss_difference_limit':0},**value}
 required={'direction','win_limit','pause_draws','win_loss_difference_limit','lookback','base_cents','loss_increment_cents','max_losses','initial_balance','daily_loss_limit','max_drawdown','max_stake'}
 if not isinstance(value,dict) or set(value)!=required:raise ValueError('走势策略参数不完整或包含未知字段')
 if value['direction'] is not None and value['direction'] not in ('大','小','单','双'):raise ValueError('开单方向须为大、小、单、双之一')
 win_pause.validate(value['win_limit'],value['pause_draws'],value.get('win_loss_difference_limit',0))
 for k in ('lookback','base_cents','loss_increment_cents','max_losses'):
  if type(value[k]) is not int:raise ValueError('走势策略期数和金额分必须为整数')
 if not 2<=value['lookback']<=1000 or not 0<=value['max_losses']<=10 or value['loss_increment_cents']<0:raise ValueError('观察期数、连亏上限或递增金额无效')
 if stake_limit_reason(value['base_cents'],odds):raise ValueError('基础金额超出玩法限额')
 for k in ('initial_balance','daily_loss_limit','max_drawdown','max_stake'):
  from decimal import Decimal
  try:
   number=Decimal(str(value[k]))
   if not number.is_finite() or number<0 or (k in ('initial_balance','max_stake') and number==0):raise ValueError('资金参数必须为有限正数')
  except (ArithmeticError,TypeError):raise ValueError('资金参数无效')
 if value['base_cents']>money(value['max_stake']):raise ValueError('基础金额不能超过单笔上限')
 return value

def save_settings(service,body):
 import copy
 axis=body.get('id');value=body.get('settings')
 if axis not in AXES:raise ValueError('走势策略不存在')
 value=validate_settings(value,service.cfg['odds'])
 with service.lock:
  if axis in service.cfg['trend_simulation']['enabled_axes']:raise ValueError('请先停用策略再编辑参数')
  if service.db.query("SELECT 1 FROM trend_orders WHERE strategy=? AND status='pending'",(axis,)):raise ValueError('请等待已有订单结算后再编辑')
  cfg=copy.deepcopy(service.cfg);cfg['trend_simulation'].setdefault('strategies',{})[axis]=value;service.update_config(cfg)
  with service.db.tx() as c:
   sync_capital(c,axis,value['initial_balance'])
   c.execute('UPDATE trend_accounts SET losses=0,win_streak=0,pause_until=0,win_loss_difference=0,difference_paused=0,cycle=cycle+1 WHERE strategy=?',(axis,))
   c.execute('INSERT INTO strategy_edits(strategy,created,settings) VALUES(?,?,?)',(axis,utcnow(),json.dumps(value,ensure_ascii=False)))
 return {'ok':True,'id':axis}

SCHEMA='''
CREATE TABLE IF NOT EXISTS trend_accounts(strategy TEXT PRIMARY KEY,balance INTEGER,initial_balance INTEGER,peak INTEGER,losses INTEGER DEFAULT 0,cycle INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS trend_orders(id INTEGER PRIMARY KEY,strategy TEXT,target_draw INTEGER,cutoff_draw INTEGER,created TEXT,direction TEXT,ratio REAL,stake INTEGER,status TEXT,pnl INTEGER,hit INTEGER,outcome TEXT,total INTEGER,settled TEXT,cycle INTEGER,step INTEGER,odds_json TEXT,UNIQUE(strategy,target_draw));
CREATE TABLE IF NOT EXISTS trend_ledger(id INTEGER PRIMARY KEY,strategy TEXT,order_id INTEGER,created TEXT,kind TEXT,amount INTEGER,balance INTEGER,UNIQUE(order_id,kind));
CREATE TABLE IF NOT EXISTS trend_decisions(strategy TEXT,target_draw INTEGER,created TEXT,action TEXT,reason TEXT,PRIMARY KEY(strategy,target_draw));
'''

def initialize(db,cfg):
 with db.tx() as c:
  for stmt in SCHEMA.strip().split(';'):
   if stmt.strip():c.execute(stmt)
  account_columns={r[1] for r in c.execute('PRAGMA table_info(trend_accounts)')}
  for name in ('win_streak','pause_until','win_loss_difference','difference_paused'):
   if name not in account_columns:c.execute('ALTER TABLE trend_accounts ADD COLUMN '+name+' INTEGER NOT NULL DEFAULT 0')
  columns={r[1] for r in c.execute('PRAGMA table_info(trend_orders)')}
  if 'rules_json' not in columns:c.execute('ALTER TABLE trend_orders ADD COLUMN rules_json TEXT')
  for axis in AXES:
   initial=money(settings(cfg,axis)['initial_balance']);c.execute('INSERT OR IGNORE INTO trend_accounts(strategy,balance,initial_balance,peak) VALUES(?,?,?,?)',(axis,initial,initial,initial))

 from .custom_strategy import initialize as initialize_custom
 initialize_custom(db)

def signal(history,axis,previous=None,lookback=LOOKBACK):
 if len(history)<lookback:return None
 h=history[-lookback:]
 if any(b['id']!=a['id']+1 for a,b in zip(h,h[1:])):return None
 first,second=AXES[axis][1];n=sum(r[axis]==first for r in h)
 if n*2==lookback:return (previous,0.5) if previous in AXES[axis][1] else None
 return (first,n/lookback) if n*2>lookback else (second,(lookback-n)/lookback)

def progress(losses,hit,max_losses=MAX_LOSSES):
 if hit:return 0,True
 loss=losses+1
 return (0,True) if max_losses>0 and loss>=max_losses else (loss,False)

def risk_reason(balance,peak,stake,daily,settings,odds):
 reason=stake_limit_reason(stake,odds)
 if reason:return reason
 if balance<stake:return '余额不足'
 if stake>money(settings['max_stake']):return '超过账户单笔金额上限'
 if money(settings['max_drawdown'])>0 and peak-balance+stake>money(settings['max_drawdown']):return '达到最大回撤限制'
 if money(settings['daily_loss_limit'])>0 and daily-stake < -money(settings['daily_loss_limit']):return '达到当日亏损限制'
 return ''

def live_tick(db,cfg,now=None):
 now=now or datetime.now(timezone.utc)
 with db.tx() as c:
  for o in c.execute("SELECT * FROM trend_orders WHERE status='pending' ORDER BY target_draw,strategy").fetchall():
   if c.execute('SELECT 1 FROM conflicts WHERE draw_id=? AND resolved=0 LIMIT 1',(o['target_draw'],)).fetchone():continue
   r=c.execute('SELECT * FROM draws WHERE id=?',(o['target_draw'],)).fetchone()
   if r is None:continue
   a=c.execute('SELECT * FROM trend_accounts WHERE strategy=?',(o['strategy'],)).fetchone();hit=r['size' if o['direction'] in ('大','小') else 'parity']==o['direction'];paid=settlement(o['stake'],o['direction'],r['total'],hit,json.loads(o['odds_json']));balance=a['balance']+paid['credit'];losses,reset=progress(a['losses'],hit,(json.loads(o['rules_json']) if o['rules_json'] else {'max_losses':3})['max_losses'])
   pause_state=win_pause.settled({k:a[k] for k in ('win_streak','pause_until','win_loss_difference','difference_paused')},paid['outcome'],o['target_draw'],(json.loads(o['rules_json']) if o['rules_json'] else {}).get('win_limit',5),(json.loads(o['rules_json']) if o['rules_json'] else {}).get('pause_draws',20), (json.loads(o['rules_json']) if o['rules_json'] else {}).get('win_loss_difference_limit',0))
   c.execute("UPDATE trend_orders SET status='settled',pnl=?,hit=?,outcome=?,total=?,settled=? WHERE id=?",(paid['pnl'],int(hit),paid['outcome'],r['total'],utcnow(),o['id']))
   c.execute('INSERT INTO trend_ledger(strategy,order_id,created,kind,amount,balance) VALUES(?,?,?,?,?,?)',(o['strategy'],o['id'],utcnow(),'settle',paid['credit'],balance))
   c.execute('UPDATE trend_accounts SET balance=?,peak=MAX(peak,?),losses=?,cycle=cycle+?,win_streak=?,pause_until=?,win_loss_difference=?,difference_paused=? WHERE strategy=?',(balance,balance,losses,int(reset),pause_state['win_streak'],pause_state['pause_until'],pause_state['win_loss_difference'],int(pause_state.get('difference_paused',False)),o['strategy']))
 enabled=cfg.get('trend_simulation',{}).get('enabled_axes',[])
 if not enabled:return {'action':'disabled'}
 history=db.tail(max(settings(cfg,a)['lookback'] for a in enabled));latest=history[-1] if history else None
 if not latest:return {'action':'skip','reason':'没有开奖数据'}
 target=latest['id']+1;reason=''
 if not latest['draw_utc']:reason='开奖时区未确认'
 else:
  age=(now-datetime.fromisoformat(latest['draw_utc'])).total_seconds();delay=(now-datetime.fromisoformat(latest['first_seen'])).total_seconds()
  if age< -30 or age>cfg['simulation'].get('max_decision_age_seconds',120) or delay>cfg['simulation']['max_received_delay_seconds']:reason='数据超过下单时窗'
 out=[]
 with db.tx() as c:
  if c.execute('SELECT 1 FROM conflicts WHERE resolved=0 LIMIT 1').fetchone():reason='存在待核验的数据冲突'
  for axis in enabled:
   rule=settings(cfg,axis)
   if c.execute('SELECT 1 FROM trend_decisions WHERE strategy=? AND target_draw=?',(axis,target)).fetchone():continue
   a=c.execute('SELECT * FROM trend_accounts WHERE strategy=?',(axis,)).fetchone();pause_state={k:a[k] for k in ('win_streak','pause_until','win_loss_difference','difference_paused')};pause_reason=win_pause.paused(pause_state,target)
   c.execute('UPDATE trend_accounts SET win_streak=?,pause_until=?,win_loss_difference=?,difference_paused=? WHERE strategy=?',(pause_state['win_streak'],pause_state['pause_until'],pause_state['win_loss_difference'],int(pause_state.get('difference_paused',False)),axis))
   local_reason=maintenance.live_guard(c,latest,now,cfg) or pause_reason or reason
   if c.execute("SELECT 1 FROM trend_orders WHERE strategy=? AND status='pending'",(axis,)).fetchone():local_reason='等待本策略上一单结算'
   previous=c.execute('SELECT direction FROM trend_orders WHERE strategy=? ORDER BY target_draw DESC LIMIT 1',(axis,)).fetchone()
   choice=signal(history,('size' if rule['direction'] in ('大','小') else 'parity') if rule.get('direction') else axis,previous['direction'] if previous else None,rule['lookback'])
   if choice and rule.get('direction') and choice[0]!=rule['direction']:choice=None
   if not choice:local_reason=local_reason or '观察窗口持平且无历史订单方向，或窗口不足/缺期'
   a=c.execute('SELECT * FROM trend_accounts WHERE strategy=?',(axis,)).fetchone();stake=rule['base_cents']+rule['loss_increment_cents']*a['losses']
   daily=c.execute("SELECT COALESCE(SUM(pnl),0) FROM trend_orders WHERE strategy=? AND status='settled' AND settled LIKE ?",(axis,now.astimezone(timezone.utc).date().isoformat()+'%')).fetchone()[0]
   local_reason=local_reason or risk_reason(a['balance'],a['peak'],stake,daily,rule,cfg['odds'])
   if local_reason:
    c.execute('INSERT INTO trend_decisions VALUES(?,?,?,?,?)',(axis,target,utcnow(),'skip',local_reason));out.append({'strategy':axis,'action':'skip','reason':local_reason});continue
   direction,ratio=choice
   cur=c.execute("INSERT INTO trend_orders(strategy,target_draw,cutoff_draw,created,direction,ratio,stake,status,cycle,step,odds_json,rules_json) VALUES(?,?,?,?,?,?,?,'pending',?,?,?,?)",(axis,target,latest['id'],utcnow(),direction,ratio,stake,a['cycle'],a['losses']+1,json.dumps(cfg['odds'],ensure_ascii=False),json.dumps(rule,ensure_ascii=False)))
   balance=a['balance']-stake;c.execute('UPDATE trend_accounts SET balance=? WHERE strategy=?',(balance,axis));c.execute('INSERT INTO trend_ledger(strategy,order_id,created,kind,amount,balance) VALUES(?,?,?,?,?,?)',(axis,cur.lastrowid,utcnow(),'stake',-stake,balance));c.execute('INSERT INTO trend_decisions VALUES(?,?,?,?,?)',(axis,target,utcnow(),'order','观察窗口持平，沿用上一笔订单方向' if ratio==0.5 else '观察窗口占比严格大于50%'))
   out.append({'strategy':axis,'action':'order','target':target,'direction':direction,'stake':stake/100,'ratio':ratio})
 return {'orders':out}

def summary(orders,initial):
 settled=[o for o in orders if o['status']=='settled'];bets=len(settled);wins=sum(o['hit'] for o in settled);refunds=sum(o['outcome']=='refund' for o in settled);net=sum(o['pnl'] for o in settled);investment=sum(o['stake'] for o in settled);peak=dd=equity=0;curve=[]
 bydraw={}
 for o in settled:bydraw[o['target_draw']]=bydraw.get(o['target_draw'],0)+o['pnl']
 for did,pnl in sorted(bydraw.items()):
  equity+=pnl;peak=max(peak,equity);dd=max(dd,peak-equity);curve.append({'draw':did,'net':equity/100})
 pending=sum(o['stake'] for o in orders if o['status']=='pending')
 return {'bets':bets,'wins':wins,'refunds':refunds,'win_rate':wins/bets if bets else None,'profitable_win_rate':(wins-refunds)/bets if bets else None,'net':net/100,'investment':investment/100,'roi':net/investment if investment else None,'max_drawdown':dd/100,'pending_stake':pending/100,'initial_balance':initial/100,'balance':(initial+net-pending)/100,'curve':curve[-500:]}

def status(db,cfg):
 orders=db.query('SELECT * FROM trend_orders ORDER BY target_draw,id');accounts=db.query('SELECT * FROM trend_accounts ORDER BY strategy');by={}
 for a in accounts:
  axis=a['strategy'];rule=settings(cfg,axis);by[axis]={**summary([o for o in orders if o['strategy']==axis],a['initial_balance']),'name':AXES[axis][0],'enabled':axis in cfg.get('trend_simulation',{}).get('enabled_axes',[]),'win_streak':a['win_streak'],'pause_until':a['pause_until'],'pause_remaining':max(0,a['pause_until']-(db.tail(1)[-1]['id'] if db.tail(1) else 0)),'settings':rule,'next_stake':(rule['base_cents']+rule['loss_increment_cents']*a['losses'])/100,'cycle':a['cycle'],'losses':a['losses']}
 return {'strategies':by,'combined':summary(orders,sum(a['initial_balance'] for a in accounts)),'orders':list(reversed(orders[-100:])),'decisions':db.query('SELECT * FROM trend_decisions ORDER BY target_draw DESC,strategy LIMIT 30'),'rules':{'strategies':{a:settings(cfg,a) for a in AXES},'tie':'previous_order_direction'}}

def backtest(rows,cfg,start=None,end=None,with_risk=True):
 if start and end and start>=end:raise ValueError('结束日期必须晚于开始日期')
 rules={a:settings(cfg,a) for a in AXES};initials={a:money(rules[a]['initial_balance']) for a in AXES};states={a:{'balance':initials[a],'peak':initials[a],'losses':0,'cycle':1,'day':'','daily':0,'last_direction':None,'win_streak':0,'pause_until':0} for a in AXES};orders=[];skips={a:{} for a in AXES}
 for i in range(min(r['lookback'] for r in rules.values()),len(rows)):
  r=rows[i];date=r.get('beijing_time',r['source_time'])[:10]
  if (start and date<start) or (end and date>=end):continue
  if rows[i-1]['id']+1!=r['id']:continue
  for axis in AXES:
   st=states[axis];rule=rules[axis];choice=signal(rows[max(0,i-rule['lookback']):i],('size' if rule['direction'] in ('大','小') else 'parity') if rule.get('direction') else axis,st['last_direction'],rule['lookback'])
   if choice and rule.get('direction') and choice[0]!=rule['direction']:choice=None
   pause_reason=win_pause.paused(st,r['id'])
   if pause_reason:
    label='胜负差值暂停' if st.get('difference_paused') else '连胜暂停';skips[axis][label]=skips[axis].get(label,0)+1;continue
   if not choice:continue
   risk_day=r.get('draw_utc','')[:10] if r.get('draw_utc') else datetime.fromisoformat(r.get('beijing_time',r['source_time'])).replace(tzinfo=ZoneInfo('Asia/Shanghai')).astimezone(timezone.utc).date().isoformat()
   if st['day']!=risk_day:st['day']=risk_day;st['daily']=0
   stake=rule['base_cents']+rule['loss_increment_cents']*st['losses'];reason=risk_reason(st['balance'],st['peak'],stake,st['daily'],rule,cfg['odds']) if with_risk else stake_limit_reason(stake,cfg['odds'])
   if reason:skips[axis][reason]=skips[axis].get(reason,0)+1;continue
   direction,ratio=choice;st['last_direction']=direction;hit=r['size' if direction in ('大','小') else 'parity']==direction;paid=settlement(stake,direction,r['total'],hit,cfg['odds'])
   orders.append({'strategy':axis,'name':AXES[axis][0],'target_draw':r['id'],'time':r.get('beijing_time',r['source_time']),'cutoff_draw':rows[i-1]['id'],'direction':direction,'ratio':ratio,'stake':stake,'status':'settled','pnl':paid['pnl'],'hit':int(hit),'outcome':paid['outcome'],'cycle':st['cycle'],'step':st['losses']+1})
   st['balance']+=paid['pnl'];st['daily']+=paid['pnl'];st['peak']=max(st['peak'],st['balance']);st['losses'],reset=progress(st['losses'],hit,rule['max_losses']);st['cycle']+=int(reset);win_pause.settled(st,paid['outcome'],r['id'],rule['win_limit'],rule['pause_draws'], rule.get('win_loss_difference_limit',0))
 by={axis:{**summary([o for o in orders if o['strategy']==axis],initials[axis]),'name':AXES[axis][0],'settings':rules[axis],'risk_skips':skips[axis]} for axis in AXES}
 return {'strategies':by,'combined':summary(orders,sum(initials.values())),'orders':orders,'odds':cfg['odds'].copy(),'rules':{'strategies':rules,'tie':'previous_order_direction','on_hit':'reset_base','direction':'reevaluate_each_draw'},'strategy_settings':rules,'with_risk_limits':with_risk,'start':start,'end_exclusive':end}


def order_page(db,axis='all',page=1,page_size=20,enabled_axes=None):
 if axis not in ('all',*AXES):raise ValueError('无效策略筛选')
 page=int(page);page_size=int(page_size)
 if page_size not in (10,20,50,100):raise ValueError('每页条数必须为10、20、50、100')
 where='' if axis=='all' else ' WHERE strategy=?'
 args=() if axis=='all' else (axis,)
 if enabled_axes is not None:
  clause='strategy IN ('+','.join('?' for _ in enabled_axes)+')' if enabled_axes else '0'
  where+=(' AND ' if where else ' WHERE ')+clause;args=(*args,*enabled_axes)
 from contextlib import closing
 with closing(db.connect()) as c:
  c.execute('BEGIN')
  total=c.execute('SELECT COUNT(*) FROM trend_orders'+where,args).fetchone()[0]
  pages=max(1,(total+page_size-1)//page_size);page=max(1,min(page,pages))
  rows=[dict(r) for r in c.execute('SELECT * FROM trend_orders'+where+' ORDER BY target_draw DESC,id DESC LIMIT ? OFFSET ?',(*args,page_size,(page-1)*page_size))]
 return {'orders':rows,'total':total,'page':page,'pages':pages,'page_size':page_size}
