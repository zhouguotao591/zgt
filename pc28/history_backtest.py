"""Read-only historical replay of current saved strategies; results use separate tables."""
import calendar,copy,json,uuid,csv
from collections import Counter
from datetime import datetime,timedelta,timezone
from zoneinfo import ZoneInfo
from dataclasses import asdict
from .model import Strategy,money,settlement,rounded_ratio,utcnow
from .trend_strategy import settings as trend_settings,signal,progress,risk_reason,summary
from .follow_strategy import hourly_signal,next_stake,previous_amount,adjusts_amount,algorithm_signal,settle_algorithm_round,rank_signal
from .algorithm_hall import VERIFIED,RollingRanking
from .router import choose
from . import win_pause,maintenance
BJ=ZoneInfo('Asia/Shanghai')
SCHEMA="""
CREATE TABLE IF NOT EXISTS strategy_backtest_summary(strategy TEXT PRIMARY KEY,run_id TEXT NOT NULL,created TEXT NOT NULL,summary TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS history_backtest_runs(id TEXT PRIMARY KEY,created TEXT NOT NULL,result TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS history_backtest_orders(run_id TEXT NOT NULL,seq INTEGER NOT NULL,record TEXT NOT NULL,PRIMARY KEY(run_id,seq));
"""
def initialize(db):
 with db.tx() as c:
  for sql in SCHEMA.split(';'):
   if sql.strip():c.execute(sql)
def selection(service,key):
 from .strategy_delete import require_available
 require_available(service.db,key)
 cfg=copy.deepcopy(service.cfg)
 if key=='legacy':return {'id':key,'name':'三十期买单反转策略','kind':'reversal','settings':copy.deepcopy(cfg['simulation']),'odds':cfg['odds'],'selections':service.db.query('SELECT * FROM selections ORDER BY month'),'snapshot_at':utcnow()}
 if key in ('size','parity'):return {'id':key,'name':'大小策略' if key=='size' else '单双策略','kind':'majority','settings':{**trend_settings(cfg,key),'axis':('size' if trend_settings(cfg,key)['direction'] in ('大','小') else 'parity') if trend_settings(cfg,key).get('direction') else key,'stake_mode':'additive'},'odds':cfg['odds'],'selections':[],'snapshot_at':utcnow()}
 rows=service.db.query('SELECT id,name,settings FROM custom_strategies WHERE id=?',(key,))
 if not rows:raise ValueError('策略不存在')
 row=rows[0];settings=json.loads(row['settings']);kind=settings.get('strategy_type','reversal')
 if kind not in ('reversal','majority','hourly','previous','single','algorithm','algorithm_rank'):raise ValueError('该策略类型无法识别')
 snapshot={'id':key,'name':row['name'],'kind':kind,'settings':settings,'odds':cfg['odds'],'selections':[],'snapshot_at':utcnow()}
 if kind in ('algorithm','algorithm_rank'):snapshot['hidden_algorithms']=sorted(service.algorithm_hall.hidden())
 return snapshot
def prepare(service,args):
 if not isinstance(args,dict) or 'strategy_id' not in args or set(args)-{'strategy_id','range_mode','start','end','periods'}:raise ValueError('回测参数无效')
 mode=args.get('range_mode','three_months');spec={'mode':mode}
 if mode not in ('three_months','dates','count'):raise ValueError('回测范围类型无效')
 if mode=='three_months' and set(args)-{'strategy_id','range_mode'}:raise ValueError('默认三个月模式不接受自定义范围')
 if mode=='count':
  n=args.get('periods')
  if type(n)!=int or not 20<=n<=100000:raise ValueError('回测期数须为20—100000整数')
  spec['periods']=n
 if mode=='dates':
  for k in ('start','end'):
   try:
    t=datetime.fromisoformat(args[k]);t=t.replace(tzinfo=BJ) if not t.tzinfo else t.astimezone(BJ)
   except (KeyError,TypeError,ValueError):raise ValueError('请选择有效的北京开始和结束时间')
   spec[k]=t.isoformat()
  if spec['start']<datetime(2024,1,1,tzinfo=BJ).isoformat() or spec['start']>=spec['end']:raise ValueError('开始时间须不早于2024年，且早于结束时间')
  if datetime.fromisoformat(spec['end'])>datetime.now(timezone.utc):raise ValueError('结束时间不能晚于当前时间')
 with service.lock:
  snap=selection(service,args['strategy_id']);snap['maintenance_guard']=service.cfg['collector'].get('maintenance_guard_enabled',True);last=service.db.query('SELECT MAX(id) id FROM draws')[0]['id']
  if last is None:raise ValueError('本地没有开奖记录')
 return {'snapshot':snap,'cutoff_id':last,'as_of':utcnow(),'range':spec}
def three_months_before(value,months=3):
 month=value.year*12+value.month-1-months;year,m=divmod(month,12);m+=1
 return value.replace(year=year,month=m,day=min(value.day,calendar.monthrange(year,m)[1]))

def replay_time_repairs(valid):
 """Resolve only a fully bracketed repeated DST hour; never reorder draw IDs.

 This is a replay-local correction, with evidence included in coverage. Raw
 database records and live accounts remain untouched.
 """
 from urllib.parse import urlsplit
 repaired=[];zone=ZoneInfo('America/Los_Angeles')
 for i in range(1,len(valid)-1):
  before=valid[i-1];r=valid[i]
  if r['_time']>=before['_time']:continue
  j=i
  while j<len(valid) and valid[j]['_time']<=before['_time']:j+=1
  if j==len(valid):continue
  group=valid[i:j];proposed=[];previous=before
  for row in group:
   try:
    local=datetime.fromisoformat(row['source_time']);a=local.replace(tzinfo=zone,fold=0);b=local.replace(tzinfo=zone,fold=1)
    if local.tzinfo or (urlsplit(row['source']).hostname or '') not in ('playnow.com','www.playnow.com') or a.utcoffset()==b.utcoffset():break
    corrected=b.astimezone(timezone.utc)
    if corrected-row['_time']!=timedelta(hours=1) or row['id']!=previous['id']+1 or corrected-previous['_time']!=timedelta(seconds=210):break
    proposed.append((row,corrected));previous={**row,'_time':corrected}
   except (ValueError,TypeError,KeyError):break
  if len(proposed)!=len(group) or valid[j]['id']!=previous['id']+1 or valid[j]['_time']-previous['_time']!=timedelta(seconds=210):continue
  for row,corrected in proposed:
   repaired.append({'id':row['id'],'old':row['draw_utc'],'corrected':corrected.isoformat(),'reason':'来源重复小时，按前后连续期号及210秒间隔核验'})
   row['_time']=corrected;row['draw_utc']=corrected.isoformat()
 return repaired
def source_window(raw,conflicts,as_of,months=3,range_spec=None):
 as_of=datetime.fromisoformat(as_of);valid=[];ignored=Counter()
 for source in raw:
  if source['id'] in conflicts:ignored['存在未解决冲突']+=1;continue
  try:t=datetime.fromisoformat(source['draw_utc'])
  except (TypeError,ValueError):ignored['缺少有效UTC时间']+=1;continue
  if t.tzinfo is None:ignored['开奖时区未确认']+=1;continue
  if t>as_of:ignored['未来时间']+=1;continue
  r=dict(source);r['_time']=t;valid.append(r)
 if not valid:raise ValueError('没有可用于历史回测的已确认时间数据')
 repairs=replay_time_repairs(valid)
 end=valid[-1]['_time'].astimezone(BJ);start=three_months_before(end,months);rows=[];last_time=None
 mode=(range_spec or {}).get('mode','three_months')
 if mode=='dates':start=datetime.fromisoformat(range_spec['start']);end=datetime.fromisoformat(range_spec['end'])
 elif mode=='count':start=datetime(2024,1,1,tzinfo=BJ)
 for r in valid:
  if not start<=r['_time']<=end:continue
  if last_time and r['_time']<last_time:ignored['窗口内时间逆序']+=1;continue
  last_time=r['_time'];r['first_seen']=r['draw_utc'];rows.append(r)
 if mode=='count':
  if len(rows)<range_spec['periods']:raise ValueError(f"本地有效数据仅{len(rows)}期，不足所选{range_spec['periods']}期")
  rows=rows[-range_spec['periods']:];start=rows[0]['_time'].astimezone(BJ)
 if range_spec and not 20<=len(rows)<=100000:raise ValueError(f'所选范围含{len(rows)}期有效数据，单次须为20—100000期；请选择更小的时间范围')
 if len(rows)<2:raise ValueError(f'最近{months}个月的有效开奖记录不足')
 gaps=sum(max(0,b['id']-a['id']-1) for a,b in zip(rows,rows[1:]))
 import hashlib
 digest=hashlib.sha256()
 for r in rows:digest.update(json.dumps([r[k] for k in ('id','draw_utc','a','b','c','total','size','parity')],ensure_ascii=False,separators=(',',':')).encode())
 gap_ranges=[{'first':a['id']+1,'last':b['id']-1,'count':b['id']-a['id']-1} for a,b in zip(rows,rows[1:]) if b['id']>a['id']+1]
 coverage={'data_sha256':digest.hexdigest(),'gap_ranges':gap_ranges,'requested_start':start.isoformat(),'requested_end':end.isoformat(),'actual_start':rows[0]['_time'].astimezone(BJ).isoformat(),'actual_end':rows[-1]['_time'].astimezone(BJ).isoformat(),'first_id':rows[0]['id'],'last_id':rows[-1]['id'],'draws':len(rows),'missing_periods':gaps,'crosschecked_abc':sum(r.get('verification')=='crosschecked_abc' for r in rows),'excluded':dict(ignored),'timezone':'Asia/Shanghai','warmup':f'观察窗口预热仅使用上述{months}个月范围内的数据'}
 coverage['time_repairs']=[r for r in repairs if coverage['first_id']<=r['id']<=coverage['last_id']]
 coverage['range_mode']=mode;coverage['warmup']='观察窗口预热仅使用本次所选范围内的数据'
 return rows,coverage

def replay(rows,snapshot,on_progress=None):
 cfg=snapshot['settings'];kind=snapshot['kind'];odds=snapshot['odds'];initial=money(cfg['initial_balance']);balance=peak=initial;daily=0;day='';orders=[];skips=Counter();hour_cache={};state={'cycle':0,'losses':0,'attempt':0,'win_streak':0,'pause_until':0};last_direction=None;curve=[];equity=0
 stored={r['month']:r for r in snapshot.get('selections',[])}
 hidden=set(snapshot.get('hidden_algorithms',[]))
 ranker=RollingRanking(cfg['axis'],cfg['rank_window'],hidden) if kind=='algorithm_rank' else None
 for i,r in enumerate(rows):
  if on_progress and i%1000==0:on_progress(i,len(rows))
  time=r['_time'];utcday=time.astimezone(timezone.utc).date().isoformat()
  if day!=utcday:day=utcday;daily=0
  if not i:skips['观察窗口预热']+=1;continue
  history=rows[max(0,i-(57 if kind=='algorithm_rank' else 1001)):i];prev=history[-1];target=r['id'];decision=time-timedelta(microseconds=1)
  if ranker is not None:ranker.update([v for v in history if v.get('verification') in VERIFIED])
  rest_reason=maintenance.block_reason(prev,decision,snapshot.get('maintenance_guard',True),time)
  if rest_reason:
   if kind=='reversal':state.update(active=False,waiting_rearm=False)
   skips[rest_reason]+=1;continue
  if win_pause.paused(state,target):skips['胜负差值暂停' if state.get('difference_paused') else '连续盈利暂停']+=1;continue
  if prev['id']+1!=target:
   if kind=='reversal' and state.get('active'):state.update(active=False,waiting_rearm=cfg.get('round_restart','condition_reset')=='condition_reset',rearm_strategy=state['strategy'])
   skips['开奖缺期边界']+=1;continue
  direction=ratio=None;end_round=False;max_bets=None;reason='';s=None;algorithm_state=None
  if kind=='reversal':
   active=state.get('active',False)
   if active and state.get('last_settled_draw')!=prev['id']:state.update(active=False,waiting_rearm=cfg.get('round_restart','condition_reset')=='condition_reset',rearm_strategy=state['strategy']);active=False
   if state.get('waiting_rearm'):
    old=Strategy(**state['rearm_strategy'])
    if not old.triggered(history):state['waiting_rearm']=False
    skips['连亏停轮等待条件退出']+=1;continue
   if active:s=Strategy(**state['strategy'])
   elif cfg.get('mode','fixed')=='fixed':s=Strategy(**cfg['fixed'])
   else:
    month=decision.astimezone(timezone.utc).strftime('%Y-%m');saved=stored.get(month)
    try:created=datetime.fromisoformat(saved['created']) if saved else None
    except (TypeError,ValueError):created=None
    if not created or not created.tzinfo or created>decision:skips['当时没有可用的月度候选快照']+=1;continue
    selected=choose(json.loads(saved['candidates']),history,cfg['mode'],odds,cfg['fixed']['base_cents'],cfg.get('round_restart','immediate'),direction=cfg['fixed'].get('direction'))
    if not selected:skips['没有触发的月度候选']+=1;continue
    s=Strategy(**selected['strategy'])
    if selected['key']!=state.get('last_key'):
     if target-state.get('last_switch',-100000)<cfg.get('switch_cooldown_draws',3):skips['策略切换冷却']+=1;continue
   h=history[-s.lookback:]
   if len(h)<s.lookback:skips['观察窗口预热']+=1;continue
   if any(b['id']!=a['id']+1 for a,b in zip(h,h[1:])):skips['观察窗口缺期']+=1;continue
   if not active and not s.triggered(history):skips['未满足入场条件']+=1;continue
   if not active:s=s.resolve(history)
   direction=s.direction;stake=state['stake'] if active else s.base_cents;max_bets=s.max_bets;step=state['attempt']+1 if active else 1
  elif kind=='single':
   direction=cfg['direction'];stake=state.get('next_stake',cfg['base_cents']) if adjusts_amount(cfg) else next_stake(cfg,state['losses']);step=state['losses']+1
  elif kind=='previous':
   direction=prev[cfg['axis']];stake=state.get('next_stake',cfg['base_cents']);step=state['attempt']+1
  elif kind in ('algorithm','algorithm_rank'):
   if r.get('verification') not in VERIFIED:skips['目标开奖尚未核验']+=1;continue
   algorithm_state=state.copy()
   direction,evidence,why=rank_signal(history,cfg,algorithm_state,target,hidden,ranker) if kind=='algorithm_rank' else algorithm_signal(history,cfg,algorithm_state,target,hidden)
   if not direction:skips[why]+=1;continue
   stake=state.get('next_stake',cfg['base_cents']);step=algorithm_state['attempt']+1
  elif kind=='majority':
   lookback=cfg['lookback'];h=history[-lookback:]
   if len(h)<lookback:skips['观察窗口预热']+=1;continue
   choice=signal(h,cfg['axis'],last_direction,lookback)
   if not choice:skips['窗口缺期或持平且无历史方向']+=1;continue
   direction,ratio=choice;stake=next_stake({**{'stake_mode':'additive'},**cfg},state['losses']);step=state['losses']+1
  else:
   hour=decision.astimezone(BJ).replace(minute=0,second=0,microsecond=0)
   if state.get('follow_end') and time<state['follow_end']:direction=state['direction']
   else:
    if time.astimezone(BJ).replace(minute=0,second=0,microsecond=0)>hour:skips['整点边界等待下一期']+=1;continue
    if hour not in hour_cache:hour_cache[hour]=hourly_signal(history,hour,cfg,decision)
    direction,evidence,why=hour_cache[hour]
    if not direction:skips[why]+=1;continue
    state.update(direction=direction,follow_end=hour+timedelta(hours=cfg['follow_hours']),follow_start=hour,cycle=state['cycle']+1,attempt=0,losses=0)
   stake=next_stake(cfg,state['losses']);step=state['attempt']+1
  if kind!='reversal' and cfg.get('direction') and direction!=cfg['direction']:skips['入场方向不符合指定方向']+=1;continue
  risk=risk_reason(balance,peak,stake,daily,cfg,odds)
  if risk:skips[risk]+=1;continue
  if algorithm_state is not None:state=algorithm_state
  if kind=='reversal' and not state.get('active'):
   state.update(active=True,strategy=asdict(s),cycle=state['cycle']+1,attempt=0,losses=0,stake=stake,waiting_rearm=False)
   if s.key!=state.get('last_key'):state.update(last_key=s.key,last_switch=target)
  if kind in ('majority','previous','single') and not state['cycle']:state['cycle']=1
  cycle=state['cycle'];axis=s.axis if s else cfg['axis'];hit=r[axis]==direction;paid=settlement(stake,direction,r['total'],hit,odds);balance+=paid['pnl'];daily+=paid['pnl'];peak=max(peak,balance);equity+=paid['pnl'];last_direction=direction
  state.update(last_settled_draw=target,attempt=step)
  win_pause.settled(state,paid['outcome'],target,cfg.get('win_limit',5 if kind=='majority' else 0),cfg.get('pause_draws',20), cfg.get('win_loss_difference_limit',0));win_stop=(state.get('difference_paused',False) or state.get('pause_until',0)>target)
  if kind=='reversal':
   state['losses']=0 if hit else state['losses']+1;state['stake']=s.base_cents if hit else rounded_ratio(stake,s.multiplier_hundredths);end_round=(s.max_losses>0 and state['losses']>=s.max_losses) or step>=s.max_bets or win_stop;state['active']=not end_round
   if end_round:state.update(waiting_rearm=not win_stop and (s.max_losses>0 and state['losses']>=s.max_losses) and cfg.get('round_restart','condition_reset')=='condition_reset',rearm_strategy=asdict(s))
  elif adjusts_amount(cfg):
   state['next_stake']=previous_amount(stake,paid['outcome'],cfg,state)
   state['losses']=0 if paid['outcome']=='win' else state['losses']+int(paid['outcome']=='loss')
   if kind=='algorithm':end_round=settle_algorithm_round(state,cfg,paid['outcome'],target)
  else:
   state['losses'],reset=progress(state['losses'],hit,cfg['max_losses'])
   if kind in ('majority','single') and reset:state['cycle']+=1
   if kind=='hourly' and win_stop:state['follow_end']=None
  orders.append({'target_draw':target,'cutoff_draw':prev['id'],'beijing_time':time.astimezone(BJ).strftime('%Y-%m-%d %H:%M:%S'),'direction':direction,'ratio':ratio,'stake':stake,'credit':paid['credit'],'status':'settled','pnl':paid['pnl'],'hit':int(hit),'outcome':paid['outcome'],'total':r['total'],'cycle':cycle,'step':step,'max_bets':max_bets,'round_end':end_round,'pause_until':state.get('pause_until',0),'equity':equity/100})
  if kind in ('algorithm','algorithm_rank'):
   orders[-1].update(algorithm_ids=evidence[0]['algorithm_ids'],algorithm_votes=evidence[0]['votes'])
  curve.append({'draw':target,'net':equity/100,'time':time.astimezone(BJ).isoformat()})
 result=summary(orders,initial);result['curve']=curve if len(curve)<=400 else [curve[i] for i in sorted({int(i*(len(curve)-1)/399) for i in range(400)})];result.update(skips=dict(skips),periods_skipped=sum(skips.values()),initial_balance=initial/100,balance=balance/100)
 if on_progress:on_progress(len(rows),len(rows))
 return result,orders

def execute(service,prepared,on_progress=None):
 from contextlib import closing
 snap=prepared['snapshot'];cutoff=prepared['cutoff_id']
 with closing(service.db.connect()) as c:
  c.execute('BEGIN');raw=[dict(r) for r in c.execute('SELECT * FROM draws WHERE id<=? ORDER BY id',(cutoff,))];conflicts={r[0] for r in c.execute('SELECT draw_id FROM conflicts WHERE resolved=0')}
 spec=prepared.get('range',{'mode':'three_months'});custom=spec['mode']!='three_months'
 rows,coverage=source_window(raw,conflicts,prepared['as_of'],range_spec=spec)
 month_rows,month_coverage=([],{}) if custom else source_window(raw,conflicts,prepared['as_of'],months=1)
 total=len(rows)+len(month_rows)
 result,orders=replay(rows,snap,(lambda n,t:on_progress(n,total)) if on_progress else None)
 month_result,_=replay(month_rows,snap,(lambda n,t:on_progress(len(rows)+n,total)) if on_progress else None)
 result['window_returns']={'selected':{'net':result['net'],'bets':result['bets'],'coverage':coverage}} if custom else {'one_month':{'net':month_result['net'],'bets':month_result['bets'],'coverage':month_coverage},'three_months':{'net':result['net'],'bets':result['bets'],'coverage':coverage},'method':'两段均以当前参数快照及独立初始本金回放，预热限于各自窗口；不是截取三个月回测的末月收益'}
 run_id=uuid.uuid4().hex;created=utcnow();prefix='history-'+run_id
 result.update(run_id=run_id,created=created,strategy=snap,coverage=coverage,file='/exports/'+prefix+'.csv',summary_file='/exports/'+prefix+'.json',notes=['仅使用本地最新开奖时间向前3个自然月范围内的记录，预热也不读取更早数据。','按目标期之前的数据判断，使用当前参数及赔率；未模拟当时网络延迟，假设开奖结果当期可用。','回测采用独立初始本金，执行资金限制、轮次、连胜暂停与胜负差值永久暂停；不修改真实模拟账户和订单。','结果为历史假设回放，不代表未来收益；月度/自适应模式仅采用当时已保存的候选快照。'])
 if snap['kind'] in ('previous','algorithm','algorithm_rank'):
  result['amount_counting']='cumulative-v2'
  result['notes'].append('加额与减额分别按已结算订单累计，输赢交替和退本金不清零；任一项达到上限后下一单恢复基础金额，两项同时重新计数。')
 result['range']=spec
 result['notes'][0]='仅使用本次所选范围的本地有效开奖记录；单次20—100000期，预热不读取范围外数据。'
 folder=service.root/'data/exports';folder.mkdir(parents=True,exist_ok=True)
 columns=['target_draw','beijing_time','direction','stake','credit','pnl','hit','outcome','total','cycle','step','max_bets','round_end','pause_until','cutoff_draw','equity']
 if snap['kind']=='algorithm':columns.extend(['algorithm_ids','algorithm_votes'])
 with (folder/(prefix+'.csv')).open('w',encoding='utf-8-sig',newline='') as f:
  w=csv.DictWriter(f,fieldnames=columns);w.writeheader()
  for o in orders:
   w.writerow({k:o[k]/100 if k in ('stake','credit','pnl') else json.dumps(o[k],ensure_ascii=False) if k in ('algorithm_ids','algorithm_votes') else o[k] for k in columns})
 (folder/(prefix+'.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
 with service.db.tx() as c:
  c.execute('INSERT INTO history_backtest_runs VALUES(?,?,?)',(run_id,created,json.dumps(result,ensure_ascii=False)))
  if not custom:c.execute('INSERT INTO strategy_backtest_summary(strategy,run_id,created,summary) VALUES(?,?,?,?) ON CONFLICT(strategy) DO UPDATE SET run_id=excluded.run_id,created=excluded.created,summary=excluded.summary',(snap['id'],run_id,created,json.dumps({'window_returns':result['window_returns'],'settings':snap['settings']},ensure_ascii=False)))
  c.executemany('INSERT INTO history_backtest_orders VALUES(?,?,?)',((run_id,i,json.dumps(o,ensure_ascii=False)) for i,o in enumerate(orders)))
 return result

PERIOD_CANDLES={f'{n}p':n for n in (1,5,10,20,50,100,500)}

def period_candles(records,interval,first,last):
 records=list(records)
 if first is None or last is None:return []
 size=PERIOD_CANDLES[interval];grouped={};equity=0
 for r in records:
  slot=(r['target_draw']-first)//size
  bar=grouped.setdefault(slot,{'open':equity,'high':equity,'low':equity,'close':equity,'orders':0,'time':r['beijing_time']})
  equity+=r['pnl'];bar['high']=max(bar['high'],equity);bar['low']=min(bar['low'],equity);bar['close']=equity;bar['orders']+=1
 result=[];carry=0
 for slot in range((last-first)//size+1):
  bar=grouped.get(slot,{'open':carry,'high':carry,'low':carry,'close':carry,'orders':0,'time':None});carry=bar['close']
  result.append({**bar,**{k:bar[k]/100 for k in ('open','high','low','close')},'first_draw':first+slot*size,'last_draw':min(last,first+(slot+1)*size-1)})
 return result

def income_candles(records,interval='1h',start=None,end=None):
 if interval not in ('1h','4h','1d'):raise ValueError('K线周期须为1h、4h或1d')
 hours={'1h':1,'4h':4,'1d':24}[interval]
 def local(value):
  t=datetime.fromisoformat(value);return t.replace(tzinfo=BJ) if not t.tzinfo else t.astimezone(BJ)
 def bucket(t):return t.replace(hour=t.hour//hours*hours,minute=0,second=0,microsecond=0)
 grouped={};equity=0;first=last=None
 for r in records:
  t=local(r['beijing_time']);slot=bucket(t);first=first or slot;last=slot
  bar=grouped.setdefault(slot,{'open':equity,'high':equity,'low':equity,'close':equity,'orders':0})
  equity+=r['pnl'];bar['high']=max(bar['high'],equity);bar['low']=min(bar['low'],equity);bar['close']=equity;bar['orders']+=1
 if first is None:return []
 cursor=bucket(local(start)) if start else first;finish=bucket(local(end)) if end else last;result=[];carry=0
 while cursor<=finish:
  bar=grouped.get(cursor,{'open':carry,'high':carry,'low':carry,'close':carry,'orders':0});carry=bar['close']
  result.append({'time':cursor.isoformat(),**{k:bar[k]/100 for k in ('open','high','low','close')},'orders':bar['orders']});cursor+=timedelta(hours=hours)
 return result

def result_page(db,run_id,page=1,page_size=20,interval='1h'):
 page=int(page);page_size=int(page_size)
 if page_size not in (10,20,50,100):raise ValueError('每页条数须为10、20、50、100')
 from contextlib import closing
 with closing(db.connect()) as c:
  c.execute('BEGIN');row=c.execute('SELECT result FROM history_backtest_runs WHERE id=?',(run_id,)).fetchone()
  if not row:raise ValueError('回测结果不存在')
  result=json.loads(row[0]);total=result['bets'];pages=max(1,(total+page_size-1)//page_size);page=max(1,min(page,pages));orders=[json.loads(r[0]) for r in c.execute('SELECT record FROM history_backtest_orders WHERE run_id=? ORDER BY seq DESC LIMIT ? OFFSET ?',(run_id,page_size,(page-1)*page_size))]
  if interval not in ('1h','4h','1d',*PERIOD_CANDLES):raise ValueError('不支持的K线周期')
  cache=getattr(db,'_income_candle_cache',None)
  if cache is None:cache={};db._income_candle_cache=cache
  key=(run_id,interval)
  candles=cache.get(key)
  if candles is None:
   records=(json.loads(r[0]) for r in c.execute('SELECT record FROM history_backtest_orders WHERE run_id=? ORDER BY seq',(run_id,)))
   candles=period_candles(records,interval,result['coverage']['first_id'],result['coverage']['last_id']) if interval in PERIOD_CANDLES else income_candles(records,interval,result['coverage']['actual_start'],result['coverage']['actual_end'])
   if len(cache)>=24:cache.pop(next(iter(cache)),None)
   cache[key]=candles
 return {'result':result,'orders':orders,'total':total,'page':page,'pages':pages,'page_size':page_size,'candles':candles,'candle_interval':interval}


def metadata(service):
 choices=[{'id':'legacy','name':'三十期买单反转策略','kind':'reversal','enabled':service.cfg['simulation']['enabled']},*({'id':axis,'name':name,'kind':'majority','enabled':axis in service.cfg['trend_simulation']['enabled_axes']} for axis,name in [('size','大小策略'),('parity','单双策略')])]
 for r in service.db.query('SELECT id,name,settings,enabled FROM custom_strategies ORDER BY created,id'):
  choices.append({'id':r['id'],'name':r['name'],'kind':json.loads(r['settings']).get('strategy_type','reversal'),'enabled':bool(r['enabled'])})
 now=datetime.now(timezone.utc);end=None;latest_id=None
 for r in service.db.query('SELECT id,draw_utc FROM draws ORDER BY id DESC LIMIT 100'):
  try:t=datetime.fromisoformat(r['draw_utc'])
  except (TypeError,ValueError):continue
  if t.tzinfo and t<=now:end=t.astimezone(BJ);latest_id=r['id'];break
 scope=None
 if end:
  start=three_months_before(end);count=service.db.query('SELECT COUNT(*) n FROM draws WHERE julianday(draw_utc)>=julianday(?) AND julianday(draw_utc)<=julianday(?)',(start.isoformat(),end.isoformat()))[0]['n'];scope={'start':start.isoformat(),'end':end.isoformat(),'count':count,'last_id':latest_id,'timezone':'Asia/Shanghai'}
 runs=[]
 for row in service.db.query('SELECT id,created,result FROM history_backtest_runs ORDER BY created DESC,rowid DESC LIMIT 20'):
  value=json.loads(row['result']);runs.append({'id':row['id'],'created':row['created'],'name':value['strategy']['name'],'bets':value['bets'],'net':value['net']})
 from .strategy_delete import deleted_ids
 removed=deleted_ids(service.db);choices=[r for r in choices if r['id'] not in removed]
 bounds=service.db.query("SELECT MIN(draw_utc) start,MAX(draw_utc) end,COUNT(*) count FROM draws WHERE julianday(draw_utc)>=julianday('2024-01-01T00:00:00+08:00') AND julianday(draw_utc)<=julianday(?)",(now.isoformat(),))[0]
 return {'strategies':choices,'scope':scope,'runs':runs,'available':bounds,'limits':{'min':20,'max':100000,'earliest':'2024-01-01T00:00:00+08:00'}}
