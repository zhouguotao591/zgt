"""Per-subscription trend admission. Independent from strategy and capital state."""
import json,math,statistics,threading,time
from datetime import datetime,timezone,timedelta
from .auth import AuthError,member_scope,require_member_scope
from .model import utcnow
DEFAULT={'source':'signal','mode':'observe','interval':'1h','lookback':20,'pause_confirm':2,'resume_confirm':2,'min_pause_bars':1,'slope_threshold':0.05,'max_stale_bars':3,'unknown_policy':'hold'}
LEGACY_KEYS=set(DEFAULT)
DEFAULT.update(decision_model='legacy',pattern_family='all',entry_confirm='breakout',candidate_bars=3)
DEFAULT.update(swing_bars=8,swing_atr=1.5,swing_efficiency=.3,reversal_buffer_atr=.1,swing_stop_atr=3.)
NEW_DEFAULT={**DEFAULT,'decision_model':'swing','candidate_bars':4,'resume_confirm':1,'pause_confirm':1}

def normalize(value):
 return {**DEFAULT,**value}
SCHEMA='''
CREATE TABLE IF NOT EXISTS trend_risk_rules(subscription_id INTEGER PRIMARY KEY,settings TEXT NOT NULL,version INTEGER NOT NULL DEFAULT 1,updated TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS trend_risk_state(subscription_id INTEGER PRIMARY KEY,version INTEGER NOT NULL,closed_at INTEGER NOT NULL,blocked INTEGER NOT NULL DEFAULT 0,manual INTEGER NOT NULL DEFAULT 0,risk_count INTEGER NOT NULL DEFAULT 0,safe_count INTEGER NOT NULL DEFAULT 0,paused_at INTEGER NOT NULL DEFAULT 0,status TEXT NOT NULL,reason TEXT NOT NULL,analysis TEXT NOT NULL,evaluated TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS trend_risk_decisions(id INTEGER PRIMARY KEY,subscription_id INTEGER,version INTEGER,closed_at INTEGER,created TEXT,status TEXT,action TEXT,reason TEXT,analysis TEXT,UNIQUE(subscription_id,version,closed_at));
CREATE INDEX IF NOT EXISTS trend_risk_decisions_subscription ON trend_risk_decisions(subscription_id,id);
CREATE TABLE IF NOT EXISTS trend_risk_events(id INTEGER PRIMARY KEY,subscription_id INTEGER,created TEXT,actor INTEGER,kind TEXT,details TEXT);
CREATE TABLE IF NOT EXISTS trend_risk_blocks(id INTEGER PRIMARY KEY,subscription_id INTEGER,target_draw INTEGER,phase TEXT,reason TEXT,created TEXT,UNIQUE(subscription_id,target_draw,phase));
'''

def validate(value):
 if not isinstance(value,dict) or not LEGACY_KEYS<=set(value) or set(value)-set(DEFAULT):raise ValueError('趋势风控参数不完整或包含未知项')
 value=normalize(value)
 if value['source'] not in ('signal','strategy'):raise ValueError('收益K线来源无效')
 if value['mode'] not in ('off','observe','auto') or value['interval'] not in ('1h','4h') or value['unknown_policy'] not in ('hold','allow'):raise ValueError('趋势风控模式或周期无效')
 for key,low,high in [('lookback',20,120),('pause_confirm',1,10),('resume_confirm',1,10),('min_pause_bars',1,168),('max_stale_bars',1,24)]:
  if type(value[key])!=int or not low<=value[key]<=high:raise ValueError(key+'参数越界')
 if type(value['slope_threshold']) not in (int,float) or not math.isfinite(value['slope_threshold']) or not .01<=value['slope_threshold']<=.5:raise ValueError('趋势阈值须为0.01—0.5')
 if value['decision_model'] not in ('legacy','hybrid','swing') or value['pattern_family'] not in ('all','fractal','engulf','star') or value['entry_confirm'] not in ('breakout','middle'):raise ValueError('趋势形态模型或确认条件无效')
 if type(value['candidate_bars'])!=int or not 1<=value['candidate_bars']<=12:raise ValueError('候选形态有效期须为1—12根')
 if type(value['swing_bars'])!=int or not 4<=value['swing_bars']<=48:raise ValueError('波段背景窗口须为4—48根')
 for key,low,high in [('swing_atr',.5,10),('swing_efficiency',.1,1),('reversal_buffer_atr',0,1),('swing_stop_atr',0,10)]:
  if type(value[key]) not in (int,float) or not math.isfinite(value[key]) or not low<=value[key]<=high:raise ValueError(key+'参数越界')
 return dict(value)

def analyze(bars,rule,closed_at):
 n=rule['lookback'];duration=3600 if rule['interval']=='1h' else 14400
 data=[b for b in bars if datetime.fromisoformat(b['time']).timestamp()+duration<=closed_at]
 if len(data)<n+1:return {'status':'unknown','reason':'已收盘K线不足，至少需要'+str(n+1)+'根','bars':len(data)}
 if sum(b['orders'] for b in data[-n:])<n or sum(bool(b['orders']) for b in data[-n:])<5:return {'status':'unknown','reason':'窗口内模拟结算不足：至少需要分析窗口笔数及5根有成交K线','bars':len(data)}
 last_active=next((b for b in reversed(data) if b['orders']),None)
 if not last_active or closed_at-(datetime.fromisoformat(last_active['time']).timestamp()+duration)>duration*rule['max_stale_bars']:return {'status':'unknown','reason':'近期无足够新模拟结算数据','bars':len(data)}
 closes=[b['close'] for b in data[-n:]];mean=statistics.mean(closes);std=statistics.pstdev(closes);x=(n-1)/2
 slope=sum((i-x)*(v-mean) for i,v in enumerate(closes))/sum((i-x)**2 for i in range(n));normalized=slope/std if std else 0
 previous=[b['close'] for b in data[-n-1:-1]];pmean=statistics.mean(previous);pstd=statistics.pstdev(previous)
 current,prior=data[-1],data[-2];support=min(b['low'] for b in data[-n-1:-1]);resistance=max(b['high'] for b in data[-n-1:-1]);body=abs(current['close']-current['open']);upper=current['high']-max(current['open'],current['close']);lower=min(current['open'],current['close'])-current['low'];epsilon=max(std*.05,1e-8)
 bearish=current['close']<current['open'] and prior['close']>prior['open'] and current['close']<=prior['open'] and current['open']>=prior['close']
 bullish=current['close']>current['open'] and prior['close']<prior['open'] and current['close']>=prior['open'] and current['open']<=prior['close']
 top=(bearish or upper>=2*max(body,epsilon)) and max(current['high'],prior['high'])>=min(resistance,pmean+2*pstd)-epsilon and current['close']<prior['close']
 bottom=(bullish or lower>=2*max(body,epsilon)) and min(current['low'],prior['low'])<=max(support,pmean-2*pstd)+epsilon and current['close']>prior['close']
 down=normalized<=-rule['slope_threshold'] and current['close']<mean;up=normalized>=rule['slope_threshold'] and current['close']>mean
 status='risk' if top or current['close']<support-epsilon or (down and not bottom) else 'safe' if bottom or up else 'neutral'
 reason='顶部反转形态' if top else '下降趋势且收益低于BOLL中轨' if down and not bottom else '跌破前窗口支撑' if status=='risk' else '底部反转形态' if bottom else '上升趋势且收益高于BOLL中轨' if up else '未形成明确趋势或反转，保持原状态'
 result={'status':status,'reason':reason,'bars':len(data),'closed_at':closed_at,'slope':slope,'normalized_slope':normalized,'boll':{'middle':mean,'upper':mean+2*std,'lower':mean-2*std},'support':support,'resistance':resistance,'top_reversal':top,'bottom_reversal':bottom,'last_bar':current,'source':'策略独立信号账本已结算100U模拟收益，不使用会员或机器人实际成交'}
 if rule.get('decision_model') in ('hybrid','swing'):
  from .trend_patterns import detect
  required=max(n+1,23,rule['swing_bars']+3 if rule.get('decision_model')=='swing' else 0)
  if len(data)<required:return {'status':'unknown','reason':'趋势形态模型至少需要'+str(required)+'根已收盘K线','bars':len(data)}
  result.update(patterns=detect(data,n,rule['pattern_family']),environment='down' if down else 'up' if up else 'range',support_break=current['close']<support-epsilon,previous_close=prior['close'],previous_closed_at=int(datetime.fromisoformat(prior['time']).timestamp()+duration))
  result.update(status='risk' if down else 'neutral',reason='下降趋势' if down else '等待形态确认')
  if rule.get('decision_model')=='swing':
   from .swing_risk import features
   result['swing']=features(data,rule,result['patterns'])
 return result

def transition(old,result,rule,closed_at):
 if rule.get('decision_model')=='swing':
  from .swing_risk import decide
  return decide(old,result,rule,closed_at)
 if rule.get('decision_model')=='hybrid':
  from .trend_patterns import decide
  return decide(old,result,rule,closed_at)
 duration=3600 if rule['interval']=='1h' else 14400
 adjacent=old.get('closed_at')==closed_at-duration
 blocked=int(old.get('blocked',0));risk=(old.get('risk_count',0) if adjacent else 0)+1 if result['status']=='risk' else 0;safe=(old.get('safe_count',0) if adjacent else 0)+1 if result['status']=='safe' else 0;paused=old.get('paused_at',0);action='keep'
 if not blocked and risk>=rule['pause_confirm']:blocked=1;paused=closed_at;action='pause'
 elif blocked and safe>=rule['resume_confirm'] and closed_at-paused>=duration*rule['min_pause_bars']:blocked=0;paused=0;action='resume'
 return {'blocked':blocked,'risk_count':risk,'safe_count':safe,'paused_at':paused,'action':action}

def reason_for(rule,state,rule_version,now=None):
 if rule['mode']!='auto':return None
 duration=3600 if rule['interval']=='1h' else 14400;now=time.time() if now is None else now;expected=int(now//duration)*duration
 reason='人工暂停本订阅' if state.get('manual') else state.get('reason') if state.get('blocked') else None
 if not reason and rule.get('decision_model') in ('hybrid','swing') and state.get('version')!=rule_version:reason='新模型等待首次形态评估与收盘确认'
 unknown=state.get('version')!=rule_version or state.get('status')=='unknown' or ((state.get('closed_at') or 0)<expected and now-expected>120)
 if not reason and unknown and rule['unknown_policy']=='hold':reason='分析未就绪、数据不足或评估已过期'
 return reason

def admit(c,bot_id,strategy_id,target_draw=None,phase='capture',now=None):
 row=c.execute("""SELECT r.subscription_id,r.settings,r.version rule_version,s.* FROM robot_subscriptions sub JOIN trend_risk_rules r ON r.subscription_id=sub.id LEFT JOIN trend_risk_state s ON s.subscription_id=sub.id WHERE sub.bot_id=? AND sub.strategy_id=?""",(bot_id,strategy_id)).fetchone()
 if not row:return None
 r=dict(row);rule=normalize(json.loads(r['settings']));reason=reason_for(rule,r,r['rule_version'],now)
 if reason:
  reason='策略趋势风控：'+reason
  c.execute('INSERT OR IGNORE INTO trend_risk_blocks(subscription_id,target_draw,phase,reason,created) VALUES(?,?,?,?,?)',(r['subscription_id'],target_draw,phase,reason,utcnow()))
 return reason

class TrendRisk:
 def __init__(self,service):
  self.service=service;self.db=service.db;self.wake=threading.Event();self.started=False;self.forced=set();self.force_lock=threading.Lock()
  with self.db.tx() as c:
   for sql in SCHEMA.split(';'):
    if sql.strip():c.execute(sql)
 def source(self,strategy,rule,closed_at):
  from .history_backtest import income_candles
  duration=3600 if rule['interval']=='1h' else 14400;start=datetime.fromtimestamp(closed_at-duration*(rule['lookback']+60),timezone.utc).isoformat();end=datetime.fromtimestamp(closed_at,timezone.utc).isoformat()
  if rule['source']=='signal':table='strategy_signals';where='s.strategy=?';args=[strategy];extra=''
  elif strategy=='legacy':table='orders';where='1=1';args=[];extra=''
  elif strategy in ('size','parity'):table='trend_orders';where='s.strategy=?';args=[strategy];extra=''
  else:table='custom_orders';where='s.strategy=?';args=[strategy];extra=" AND NOT EXISTS(SELECT 1 FROM gap_order_tags g WHERE g.engine='custom' AND g.order_id=s.id)"
  records=self.db.query("SELECT s.target_draw,s.pnl,COALESCE(s.settled,d.draw_utc) beijing_time FROM "+table+" s JOIN draws d ON d.id=s.target_draw WHERE "+where+" AND s.status='settled' AND s.pnl IS NOT NULL AND julianday(COALESCE(s.settled,d.draw_utc))>=julianday(?) AND julianday(COALESCE(s.settled,d.draw_utc))<julianday(?) AND julianday(d.draw_utc)<julianday(?) AND d.verification IN ('full_keno','crosschecked_abc') AND NOT EXISTS(SELECT 1 FROM conflicts x WHERE x.draw_id=d.id AND x.resolved=0)"+extra+" ORDER BY COALESCE(s.settled,d.draw_utc),s.target_draw,s.id",(*args,start,end,end))
  bars=income_candles(records,rule['interval'],end=datetime.fromtimestamp(closed_at-1,timezone.utc).isoformat()) if records else []
  result=analyze(bars,rule,closed_at);result['source']='标准100U独立信号模拟收益' if rule['source']=='signal' else '策略已结算模拟收益（排除历史补单）';result.update(cutoff_draw=max((r['target_draw'] for r in records),default=None),source_orders=len(records),source_hash=__import__('hashlib').sha256(json.dumps(records,sort_keys=True).encode()).hexdigest(),candles=bars[-rule['lookback']-1:]);return result
 def tick(self,now=None):
  now=time.time() if now is None else now;cache={}
  rows=self.db.query('''SELECT r.*,sub.strategy_id FROM trend_risk_rules r JOIN robot_subscriptions sub ON sub.id=r.subscription_id JOIN robot_accounts a ON a.id=sub.account_id JOIN robot_bots b ON b.id=sub.bot_id WHERE a.deleted=0 AND b.deleted=0''')
  for row in rows:
   rule=normalize(json.loads(row['settings']))
   if rule['mode']=='off':continue
   duration=3600 if rule['interval']=='1h' else 14400;closed_at=int(now//duration)*duration
   oldrows=self.db.query('SELECT * FROM trend_risk_state WHERE subscription_id=?',(row['subscription_id'],));old=oldrows[0] if oldrows else {}
   with self.force_lock:
    forced=row['subscription_id'] in self.forced;self.forced.discard(row['subscription_id'])
   same=old.get('version')==row['version'] and old.get('closed_at')==closed_at
   if same and not forced:continue
   key=(row['strategy_id'],json.dumps(rule,sort_keys=True),closed_at)
   if key not in cache:cache[key]=self.source(row['strategy_id'],rule,closed_at)
   result={**cache[key],'parameters':rule,'interval':rule['interval'],'closed_at':closed_at,'evaluation_rule':{'swing':'closed-equity-wave-reversal-v3','hybrid':'closed-equity-trend-pattern-confirm-v2'}.get(rule['decision_model'],'closed-equity-boll-slope-reversal-v1')}
   baseline=old if old.get('version')==row['version'] else {'blocked':old.get('blocked',0),'paused_at':old.get('paused_at',0)}
   if old.get('version')!=row['version'] and rule['decision_model'] in ('hybrid','swing'):baseline={'blocked':1,'paused_at':old.get('paused_at',0)}
   state={**old,'action':'refresh'} if same else transition(baseline,result,rule,closed_at)
   if same and rule['decision_model'] in ('hybrid','swing'):
    previous=json.loads(old.get('analysis') or '{}');result.update(status=old['status'],reason=old['reason'],gate=previous.get('gate',{}))
   with self.db.tx() as c:
    current=c.execute('SELECT version FROM trend_risk_rules WHERE subscription_id=?',(row['subscription_id'],)).fetchone()
    if not current or current[0]!=row['version']:continue
    wire=json.dumps(result,ensure_ascii=False);created=utcnow();action=('would_'+state['action']) if rule['mode']=='observe' else state['action']
    cur=c.execute('INSERT OR IGNORE INTO trend_risk_decisions(subscription_id,version,closed_at,created,status,action,reason,analysis) VALUES(?,?,?,?,?,?,?,?)',(row['subscription_id'],row['version'],closed_at,created,result['status'],action,result['reason'],wire))
    if not cur.rowcount:
     if not same or not forced:continue
     c.execute('UPDATE trend_risk_decisions SET created=?,status=?,action=?,reason=?,analysis=? WHERE subscription_id=? AND version=? AND closed_at=?',(created,result['status'],'refresh',result['reason'],wire,row['subscription_id'],row['version'],closed_at))
    c.execute('''INSERT INTO trend_risk_state VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(subscription_id) DO UPDATE SET version=excluded.version,closed_at=excluded.closed_at,blocked=excluded.blocked,risk_count=excluded.risk_count,safe_count=excluded.safe_count,paused_at=excluded.paused_at,status=excluded.status,reason=excluded.reason,analysis=excluded.analysis,evaluated=excluded.evaluated''',(row['subscription_id'],row['version'],closed_at,state['blocked'],old.get('manual',0),state['risk_count'],state['safe_count'],state['paused_at'],result['status'],result['reason'],wire,created))
 def start(self):
  if self.started:return
  self.started=True
  def run():
   while not self.service.stop.is_set():
    self.wake.clear()
    try:self.tick()
    except Exception as error:self.db.log_job('trend_risk','error',{'error':str(error)})
    self.wake.wait(10)
  self.thread=threading.Thread(target=run,name='pc28-trend-risk',daemon=True);self.thread.start()
 def scoped(self,user,subscription_id=None):
  scope=member_scope(user);where='a.deleted=0 AND b.deleted=0';args=[]
  if scope is not None:where+=' AND a.user_id IN (SELECT member_id FROM web_member_access WHERE admin_id=?)';args.append(scope)
  if subscription_id is not None:where+=' AND sub.id=?';args.append(subscription_id)
  return self.db.query('''SELECT sub.*,u.username,a.name account_name,b.name bot_name,COALESCE(cs.name,sub.strategy_id) strategy_name,mp.name money_plan_name,r.settings,r.version risk_version,s.version state_version,s.closed_at,s.blocked,s.manual,s.status,s.reason,s.evaluated,s.analysis FROM robot_subscriptions sub JOIN robot_accounts a ON a.id=sub.account_id JOIN web_users u ON u.id=a.user_id JOIN robot_bots b ON b.id=sub.bot_id LEFT JOIN custom_strategies cs ON cs.id=sub.strategy_id LEFT JOIN money_plans mp ON mp.id=sub.money_plan_id LEFT JOIN trend_risk_rules r ON r.subscription_id=sub.id LEFT JOIN trend_risk_state s ON s.subscription_id=sub.id WHERE '''+where+' ORDER BY sub.id DESC',args)
 def listing(self,user,q):
  rows=self.scoped(user);term=str(q.get('q','')).strip().casefold();rows=[r for r in rows if not term or term in (r['username']+r['account_name']+r['bot_name']+r['strategy_name']).casefold()]
  size=int(q.get('page_size',20));page=max(1,int(q.get('page',1)))
  if size not in (10,20,50,100):raise ValueError('每页条数无效')
  total=len(rows);pages=max(1,(total+size-1)//size);page=min(page,pages)
  for r in rows:
   r['settings']=normalize(json.loads(r['settings'])) if r['settings'] else dict(NEW_DEFAULT);r['configured']=r['risk_version'] is not None;r['analysis']=json.loads(r['analysis']) if r['analysis'] else None
   r['admission_reason']=reason_for(r['settings'],{**r,'version':r['state_version']},r['risk_version']) if r['configured'] else None
  return {'rows':rows[(page-1)*size:page*size],'page':page,'pages':pages,'total':total,'defaults':NEW_DEFAULT,'note':'按所选信号或策略模拟收益分析，不是机器人真实成交收益；观察模式不拦截。仅分析已收盘K线；合并消息有任一订阅被拦截时整条取消，不补发过期订单。'}
 def logs(self,user,q):
  sid=int(q.get('id',0))
  if not self.scoped(user,sid):raise AuthError('无权查看此订阅',403,'permission_denied')
  page=max(1,int(q.get('page',1)));size=20;total=self.db.query('SELECT COUNT(*) n FROM trend_risk_decisions WHERE subscription_id=?',(sid,))[0]['n'];pages=max(1,(total+size-1)//size);page=min(page,pages)
  return {'rows':self.db.query('SELECT * FROM trend_risk_decisions WHERE subscription_id=? ORDER BY id DESC LIMIT ? OFFSET ?',(sid,size,(page-1)*size)),'events':self.db.query('SELECT * FROM trend_risk_events WHERE subscription_id=? ORDER BY id DESC LIMIT 30',(sid,)),'blocks':self.db.query('SELECT * FROM trend_risk_blocks WHERE subscription_id=? ORDER BY id DESC LIMIT 30',(sid,)),'page':page,'pages':pages,'total':total}
 def save(self,user,body,ip):
  sid=body.get('id');action=body.get('action','save')
  if type(sid)!=int or not self.scoped(user,sid):raise AuthError('无权配置此订阅',403,'permission_denied')
  if action not in ('save','pause','resume','evaluate'):raise ValueError('趋势风控操作无效')
  with self.db.tx() as c:
   resource=c.execute('SELECT a.user_id FROM robot_subscriptions sub JOIN robot_accounts a ON a.id=sub.account_id JOIN robot_bots b ON b.id=sub.bot_id WHERE sub.id=? AND a.deleted=0 AND b.deleted=0',(sid,)).fetchone()
   if not resource:raise ValueError('订阅已失效')
   require_member_scope(c,user,resource['user_id'])
   row=c.execute('SELECT r.version FROM trend_risk_rules r WHERE subscription_id=?',(sid,)).fetchone();version=row[0] if row else 0
   if body.get('version')!=version:raise AuthError('配置已变更，请刷新后重试',409,'version_conflict')
   if action=='save':
    settings=validate(body.get('settings'));c.execute('INSERT INTO trend_risk_rules VALUES(?,?,?,?) ON CONFLICT(subscription_id) DO UPDATE SET settings=excluded.settings,version=excluded.version,updated=excluded.updated',(sid,json.dumps(settings),version+1,utcnow()));c.execute('UPDATE trend_risk_state SET risk_count=0,safe_count=0 WHERE subscription_id=?',(sid,))
   elif action in ('pause','resume'):
    if not row:raise ValueError('请先保存配置')
    c.execute("UPDATE trend_risk_state SET manual=?,blocked=CASE WHEN ?=0 THEN 0 ELSE blocked END,risk_count=0,safe_count=0 WHERE subscription_id=?",(int(action=='pause'),int(action=='pause'),sid))
    if c.execute('SELECT changes()').fetchone()[0]==0:raise ValueError('请等待首次分析完成后再操作')
    current_rule=normalize(json.loads(c.execute('SELECT settings FROM trend_risk_rules WHERE subscription_id=?',(sid,)).fetchone()[0]))
    if action=='resume' and current_rule['decision_model'] in ('hybrid','swing'):
     analysis=json.loads(c.execute('SELECT analysis FROM trend_risk_state WHERE subscription_id=?',(sid,)).fetchone()[0]);analysis['gate']={'phase':'waiting'}
     c.execute("UPDATE trend_risk_state SET blocked=1,status='neutral',reason='已取消人工暂停，等待新的形态及收盘确认',analysis=? WHERE subscription_id=?",(json.dumps(analysis,ensure_ascii=False),sid))
   else:
    if not row:raise ValueError('请先保存配置')
    settings=json.loads(c.execute('SELECT settings FROM trend_risk_rules WHERE subscription_id=?',(sid,)).fetchone()[0])
    if settings['mode']=='off':raise ValueError('关闭模式不执行分析，请先启用观察或自动模式')
   c.execute('INSERT INTO trend_risk_events(subscription_id,created,actor,kind,details) VALUES(?,?,?,?,?)',(sid,utcnow(),user['id'],action,json.dumps(body.get('settings',{}),ensure_ascii=False)))
   self.service.auth.audit(c,user['id'],'trend_risk_'+action,sid,ip,{'settings':body.get('settings',{})})
  if action=='evaluate':
   with self.force_lock:self.forced.add(sid)
  self.wake.set();return {'ok':True,'version':version+1 if action=='save' else version}
