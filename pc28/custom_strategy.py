"""Independent fixed-parameter strategies, balances and immutable order snapshots."""
import json,copy,uuid
from dataclasses import asdict
from . import win_pause,maintenance
from datetime import datetime,timezone
from .model import Strategy,money,utcnow,settlement,rounded_ratio
from .trend_strategy import risk_reason,summary
SCHEMA="""
CREATE TABLE IF NOT EXISTS gap_order_backfills(run_id TEXT PRIMARY KEY,created TEXT NOT NULL,first_draw INTEGER,last_draw INTEGER,report TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS gap_order_tags(engine TEXT,order_id INTEGER,run_id TEXT NOT NULL,PRIMARY KEY(engine,order_id));
CREATE TABLE IF NOT EXISTS deleted_strategies(id TEXT PRIMARY KEY,name TEXT NOT NULL,deleted_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS custom_strategies(id TEXT PRIMARY KEY,name TEXT NOT NULL,enabled INTEGER NOT NULL DEFAULT 0,settings TEXT NOT NULL,balance INTEGER,initial_balance INTEGER,peak INTEGER,state TEXT NOT NULL DEFAULT '{}',created TEXT);
CREATE TABLE IF NOT EXISTS custom_orders(id INTEGER PRIMARY KEY,strategy TEXT,target_draw INTEGER,cutoff_draw INTEGER,created TEXT,direction TEXT,ratio REAL,stake INTEGER,status TEXT,pnl INTEGER,hit INTEGER,outcome TEXT,total INTEGER,settled TEXT,cycle INTEGER,step INTEGER,max_bets INTEGER,strategy_json TEXT,odds_json TEXT,restart_policy TEXT,UNIQUE(strategy,target_draw));
CREATE TABLE IF NOT EXISTS custom_ledger(id INTEGER PRIMARY KEY,strategy TEXT,order_id INTEGER,created TEXT,kind TEXT,amount INTEGER,balance INTEGER,UNIQUE(order_id,kind));
CREATE TABLE IF NOT EXISTS custom_decisions(strategy TEXT,target_draw INTEGER,created TEXT,action TEXT,reason TEXT,PRIMARY KEY(strategy,target_draw));
CREATE TABLE IF NOT EXISTS strategy_edits(id INTEGER PRIMARY KEY,strategy TEXT,created TEXT,settings TEXT);
"""
def initialize(db):
 with db.tx() as c:
  for sql in SCHEMA.split(';'):
   if sql.strip():c.execute(sql)
  if 'pause_json' not in {r[1] for r in c.execute('PRAGMA table_info(custom_orders)')}:c.execute('ALTER TABLE custom_orders ADD COLUMN pause_json TEXT')
def validate_settings(cfg,settings):
 if not isinstance(settings,dict):raise ValueError('策略参数必须是对象')
 allowed=('mode','initial_balance','daily_loss_limit','max_drawdown','max_stake','round_restart','fixed','win_limit','pause_draws')
 if any(k not in allowed for k in settings):raise ValueError('存在不支持的策略参数')
 candidate=copy.deepcopy(cfg);candidate['simulation'].update(copy.deepcopy(settings))
 from .config import validate
 validate(candidate)
 for k in ('initial_balance','daily_loss_limit','max_drawdown','max_stake'):
  if money(candidate['simulation'][k])<=0:raise ValueError('资金参数必须大于0')
 return {k:candidate['simulation'].get(k,0 if k=='win_limit' else 20) for k in allowed}
def save(service,body):
 from .strategy_delete import require_available
 require_available(service.db,body.get("id"))
 if isinstance(body.get('settings'),dict) and body['settings'].get('strategy_type') in ('hourly','majority','previous'):
  from .follow_strategy import save as save_follow
  return save_follow(service,body)
 if body.get('id') in ('parity','size'):
  from .trend_strategy import save_settings
  return save_settings(service,body)
 name=body.get('name','').strip() if isinstance(body.get('name',''),str) else ''
 if not 1<=len(name)<=60:raise ValueError('策略名称需要1—60个字符')
 key=body.get('id')
 settings=validate_settings(service.cfg,body.get('settings'))
 with service.lock:
  require_available(service.db,key)
  if key=='legacy':
   if service.cfg['simulation']['enabled']:raise ValueError('请先停用策略再编辑参数')
   pause_changed=(settings['win_limit'],settings['pause_draws'])!=(service.cfg['simulation'].get('win_limit',0),service.cfg['simulation'].get('pause_draws',20))
   if pause_changed and service.db.query("SELECT 1 FROM orders WHERE status='pending'"):raise ValueError('请等待已有订单结算后再修改连胜暂停参数')
   cfg=copy.deepcopy(service.cfg);cfg['simulation'].update(settings);service.update_config(cfg)
   if pause_changed:
    with service.db.tx() as c:
     state=json.loads(c.execute('SELECT state FROM accounts WHERE id=1').fetchone()[0]);state.update(win_streak=0,pause_until=0);c.execute('UPDATE accounts SET state=? WHERE id=1',(json.dumps(state,ensure_ascii=False),))
   return {'ok':True,'id':'legacy'}
  if settings['mode']!='fixed':raise ValueError('新增策略目前使用固定参数模式')
  with service.db.tx() as c:
   if key:
    old=c.execute('SELECT * FROM custom_strategies WHERE id=?',(key,)).fetchone()
    if not old:raise ValueError('策略不存在')
    if json.loads(old['settings']).get('strategy_type'):raise ValueError('请使用该策略对应的编辑入口')
    if old['enabled']:raise ValueError('请先停用策略再编辑参数')
    if c.execute("SELECT 1 FROM custom_orders WHERE strategy=? AND status='pending'",(key,)).fetchone():raise ValueError('请等待已有订单结算后再编辑')
    c.execute('UPDATE custom_strategies SET name=?,settings=?,state=? WHERE id=?',(name,json.dumps(settings,ensure_ascii=False),json.dumps({'cycle':json.loads(old['state']).get('cycle',0)+1}),key))
   else:
    if c.execute('SELECT COUNT(*) FROM custom_strategies WHERE id NOT IN (SELECT id FROM deleted_strategies)').fetchone()[0]>=100:raise ValueError('最多添加100条策略')
    key='custom_'+uuid.uuid4().hex;initial=money(settings['initial_balance'])
    c.execute('INSERT INTO custom_strategies(id,name,settings,balance,initial_balance,peak,created) VALUES(?,?,?,?,?,?,?)',(key,name,json.dumps(settings,ensure_ascii=False),initial,initial,initial,utcnow()))
   c.execute('INSERT INTO strategy_edits(strategy,created,settings) VALUES(?,?,?)',(key,utcnow(),json.dumps({'name':name,'settings':settings},ensure_ascii=False)))
 return {'ok':True,'id':key}
def tick(db,cfg,now=None):
 now=now or datetime.now(timezone.utc)
 with db.tx() as c:
  for o in c.execute("SELECT * FROM custom_orders WHERE status='pending' ORDER BY target_draw,id").fetchall():
   if c.execute('SELECT 1 FROM conflicts WHERE draw_id=? AND resolved=0',(o['target_draw'],)).fetchone():continue
   r=c.execute('SELECT * FROM draws WHERE id=?',(o['target_draw'],)).fetchone()
   if not r:continue
   a=c.execute('SELECT * FROM custom_strategies WHERE id=?',(o['strategy'],)).fetchone()
   if json.loads(o['strategy_json']).get('strategy_type') in ('hourly','majority','previous'):
    from .follow_strategy import settle_one
    settle_one(c,o,r,a);continue
   st=json.loads(a['state']);s=Strategy(**json.loads(o['strategy_json']));hit=r[s.axis]==s.direction;paid=settlement(o['stake'],s.direction,r['total'],hit,json.loads(o['odds_json']));balance=a['balance']+paid['credit']
   st.update(losses=0 if hit else st.get('losses',0)+1,attempt=o['step'],last_settled_draw=o['target_draw'],stake=s.base_cents if hit else rounded_ratio(o['stake'],s.multiplier_hundredths))
   pause_cfg=json.loads(o['pause_json']) if o['pause_json'] else {};win_pause.settled(st,paid['outcome'],o['target_draw'],pause_cfg.get('win_limit',0),pause_cfg.get('pause_draws',20))
   st['active']=st['losses']<s.max_losses and st['attempt']<s.max_bets and st.get('pause_until',0)<=o['target_draw']
   if not st['active']:st.update(waiting_rearm=st.get('pause_until',0)<=o['target_draw'] and st['losses']>=s.max_losses and o['restart_policy']=='condition_reset',rearm_strategy=json.loads(o['strategy_json']))
   c.execute("UPDATE custom_orders SET status='settled',pnl=?,hit=?,outcome=?,total=?,settled=? WHERE id=?",(paid['pnl'],int(hit),paid['outcome'],r['total'],utcnow(),o['id']))
   c.execute('INSERT INTO custom_ledger(strategy,order_id,created,kind,amount,balance) VALUES(?,?,?,?,?,?)',(o['strategy'],o['id'],utcnow(),'settle',paid['credit'],balance))
   c.execute('UPDATE custom_strategies SET balance=?,peak=MAX(peak,?),state=? WHERE id=?',(balance,balance,json.dumps(st,ensure_ascii=False),o['strategy']))
 history=db.tail(1001)
 if not history:return
 latest=history[-1];target=latest['id']+1
 with db.tx() as c:
  for a in c.execute('SELECT * FROM custom_strategies WHERE enabled=1').fetchall():
   key=a['id'];settings=json.loads(a['settings']);st=json.loads(a['state'])
   if settings.get('strategy_type') in ('hourly','majority','previous'):
    from .follow_strategy import tick_one
    tick_one(c,a,history,cfg,now);continue
   if c.execute('SELECT 1 FROM custom_decisions WHERE strategy=? AND target_draw=?',(key,target)).fetchone():continue
   pause_reason=win_pause.paused(st,target)
   c.execute('UPDATE custom_strategies SET state=? WHERE id=?',(json.dumps(st,ensure_ascii=False),key))
   if pause_reason:c.execute('INSERT INTO custom_decisions VALUES(?,?,?,?,?)',(key,target,utcnow(),'skip',pause_reason));continue
   reason=''
   if not latest['draw_utc']:reason='开奖时区未确认'
   else:
    age=(now-datetime.fromisoformat(latest['draw_utc'])).total_seconds();delay=(now-datetime.fromisoformat(latest['first_seen'])).total_seconds()
    if age< -30 or age>cfg['simulation'].get('max_decision_age_seconds',120) or delay>cfg['simulation']['max_received_delay_seconds']:reason='数据超过下单时窗'
   if c.execute('SELECT 1 FROM conflicts WHERE resolved=0 LIMIT 1').fetchone():reason='存在待核验的数据冲突'
   if c.execute("SELECT 1 FROM custom_orders WHERE strategy=? AND status='pending'",(key,)).fetchone():reason='等待上一单结算'
   rest_reason=maintenance.live_guard(c,latest,now,cfg)
   if rest_reason:
    st.update(active=False,waiting_rearm=False);c.execute('UPDATE custom_strategies SET state=? WHERE id=?',(json.dumps(st,ensure_ascii=False),key));c.execute('INSERT INTO custom_decisions VALUES(?,?,?,?,?)',(key,target,utcnow(),'skip',rest_reason));continue
   s=Strategy(**(st['strategy'] if st.get('active') else settings['fixed']))
   h=history[-s.lookback:]
   if len(h)<s.lookback or any(b['id']!=a['id']+1 for a,b in zip(h,h[1:])):reason='策略窗口不足或缺期'
   if st.get('active') and st.get('last_settled_draw',latest['id'])!=latest['id']:
    st.update(active=False,waiting_rearm=settings['round_restart']=='condition_reset',rearm_strategy=st['strategy']);s=Strategy(**settings['fixed'])
   if not st.get('active') and st.get('waiting_rearm') and st.get('strategy'):
    previous=Strategy(**st['strategy'])
    if st.get('attempt',0)>=previous.max_bets and st.get('losses',0)<previous.max_losses:st['waiting_rearm']=False
   if st.get('waiting_rearm'):
    previous=Strategy(**st['rearm_strategy'])
    if not reason and not previous.triggered(history):st['waiting_rearm']=False;reason='入场条件已退出，等待再次满足'
    else:reason=reason or '等待入场条件先退出后再次满足'
   elif not st.get('active') and not s.triggered(history):reason=reason or '未达到入场条件'
   stake=st['stake'] if st.get('active') else s.base_cents
   day=now.astimezone(timezone.utc).date().isoformat();daily=c.execute("SELECT COALESCE(SUM(pnl),0) FROM custom_orders WHERE strategy=? AND status='settled' AND substr(settled,1,10)=?",(key,day)).fetchone()[0]
   reason=reason or risk_reason(a['balance'],a['peak'],stake,daily,settings,cfg['odds'])
   if reason:
    c.execute('UPDATE custom_strategies SET state=? WHERE id=?',(json.dumps(st,ensure_ascii=False),key));c.execute('INSERT INTO custom_decisions VALUES(?,?,?,?,?)',(key,target,utcnow(),'skip',reason));continue
   if not st.get('active'):
    s=s.resolve(history)
    if s is None:raise ValueError('动态反转方向未满足入场条件')
    st.update(active=True,strategy=asdict(s),stake=stake,attempt=0,losses=0,cycle=st.get('cycle',0)+1)
   step=st['attempt']+1
   cur=c.execute("INSERT INTO custom_orders(strategy,target_draw,cutoff_draw,created,direction,stake,status,cycle,step,max_bets,strategy_json,odds_json,restart_policy,pause_json) VALUES(?,?,?,?,?,?,'pending',?,?,?,?,?,?,?)",(key,target,latest['id'],utcnow(),s.direction,stake,st['cycle'],step,s.max_bets,json.dumps(st['strategy']),json.dumps(cfg['odds']),settings['round_restart'],json.dumps({'win_limit':settings.get('win_limit',0),'pause_draws':settings.get('pause_draws',20)})))
   balance=a['balance']-stake;c.execute('INSERT INTO custom_ledger(strategy,order_id,created,kind,amount,balance) VALUES(?,?,?,?,?,?)',(key,cur.lastrowid,utcnow(),'stake',-stake,balance));c.execute('UPDATE custom_strategies SET balance=?,state=? WHERE id=?',(balance,json.dumps(st),key));c.execute('INSERT INTO custom_decisions VALUES(?,?,?,?,?)',(key,target,utcnow(),'order','场景符合且通过资金检查'))
def catalog(db):
 rows=[]
 for a in db.query('SELECT * FROM custom_strategies ORDER BY created,id'):
  settings=json.loads(a['settings']);orders=db.query('SELECT * FROM custom_orders WHERE strategy=? ORDER BY target_draw,id',(a['id'],));last=db.query('SELECT reason FROM custom_decisions WHERE strategy=? ORDER BY target_draw DESC LIMIT 1',(a['id'],))
  if settings.get('strategy_type') in ('hourly','majority','previous'):
   from .follow_strategy import rule_text,next_stake
   settings={**{'win_limit':5 if settings['strategy_type']=='majority' else 0,'pause_draws':20},**settings};state=json.loads(a['state']);stats=summary(orders,a['initial_balance']);stats['next_stake']=next_stake(settings,state.get('losses',0))/100
   rows.append({'id':a['id'],'name':a['name'],'enabled':bool(a['enabled']),'paused':False,'editable':True,'editor_type':settings['strategy_type'],'settings':settings,'status':'已停用' if not a['enabled'] else '等待开奖' if any(o['status']=='pending' for o in orders) else '连胜暂停至'+str(state['pause_until'])+'期' if state.get('pause_until',0)>(db.tail(1)[-1]['id'] if db.tail(1) else 0) else last[0]['reason'] if last else '等待下一期检查','rules':rule_text(settings),'win_streak':state.get('win_streak',0),'pause_until':state.get('pause_until',0),'stats':stats,'follow_start':state.get('follow_start'),'follow_end':state.get('follow_end'),'follow_direction':state.get('direction')});continue
  settings={**{'win_limit':0,'pause_draws':20},**settings};s=Strategy(**settings['fixed'])
  rows.append({'id':a['id'],'name':a['name'],'enabled':bool(a['enabled']),'paused':False,'editable':True,'settings':settings,'status':'已停用' if not a['enabled'] else '等待开奖' if any(o['status']=='pending' for o in orders) else last[0]['reason'] if last else '等待下一期检查','rules':f"{s.lookback}期 · {s.kind} · 买{s.direction} · 基础{s.base_cents/100:g}U · 亏后{s.multiplier_hundredths/100:g}倍 · 连亏{s.max_losses}停轮 · 每轮最多{s.max_bets}单，满单后重判入场"+(f"；连续盈利{settings['win_limit']}单暂停{settings['pause_draws']}期" if settings['win_limit'] else ''),'stats':summary(orders,a['initial_balance'])})
 return rows
