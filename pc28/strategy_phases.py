"""Read-only strategy screening from settled standard 100U signals."""
import json,time
from datetime import datetime,timezone
from .trend_risk import NEW_DEFAULT,analyze,transition
from .history_backtest import income_candles
LABELS={'up':'上行中','bottom':'底部待确认','reversal':'反转已确认','down':'继续下行','range':'震荡观察','unknown':'样本不足或过期'}

def classify(bars,rule,cutoff):
 duration=3600 if rule['interval']=='1h' else 14400
 bars=[b for b in bars if datetime.fromisoformat(b['time']).timestamp()+duration<=cutoff]
 old={};last={'status':'unknown','reason':'尚无已结算信号','bars':0};confirmed=None
 for i,b in enumerate(bars):
  at=int(datetime.fromisoformat(b['time']).timestamp())+duration
  last=analyze(bars[:i+1],rule,at);state=transition(old,last,rule,at)
  if state['action']=='resume':confirmed=at
  if state['action']=='pause':confirmed=None
  old={**state,'closed_at':at,'analysis':json.dumps(last)}
 stage='unknown'
 if last['status']!='unknown':
  gate=last.get('gate',{});slope=last.get('normalized_slope',0);cs=bars[-3:]
  rising=len(cs)==3 and all(b['high']>a['high'] and b['low']>a['low'] for a,b in zip(cs,cs[1:]))
  if gate.get('candidate'):stage='bottom'
  elif confirmed and cutoff-confirmed<=duration*rule['candidate_bars'] and not old.get('blocked'):stage='reversal'
  elif slope>=rule['slope_threshold'] and last['last_bar']['close']>last['boll']['middle'] and rising and not gate.get('exit_candidate'):stage='up'
  elif slope<=-rule['slope_threshold'] and last['last_bar']['close']<last['boll']['middle'] or last.get('support_break'):stage='down'
  else:stage='range'
 return stage,last

def listing(service,interval='1h',now=None):
 if interval not in ('1h','4h'):raise ValueError('周期须为1h或4h')
 duration=3600 if interval=='1h' else 14400;cutoff=int((time.time() if now is None else now)//duration)*duration
 rule={**NEW_DEFAULT,'interval':interval};start=datetime.fromtimestamp(cutoff-duration*100,timezone.utc).isoformat();end=datetime.fromtimestamp(cutoff,timezone.utc).isoformat()
 definitions=[{'id':'legacy','name':'原有反转策略','enabled':service.cfg['simulation']['enabled']},*({'id':axis,'name':name,'enabled':axis in service.cfg['trend_simulation']['enabled_axes']} for axis,name in [('size','大小策略'),('parity','单双策略')])]
 definitions+=service.db.query('SELECT id,name,enabled FROM custom_strategies WHERE id NOT IN (SELECT id FROM deleted_strategies) ORDER BY created')
 records=service.db.query("SELECT s.strategy,s.target_draw,s.pnl,s.settled beijing_time FROM strategy_signals s JOIN draws d ON d.id=s.target_draw WHERE s.status='settled' AND s.pnl IS NOT NULL AND julianday(s.settled)>=julianday(?) AND julianday(s.settled)<julianday(?) AND julianday(d.draw_utc)<julianday(?) AND d.verification IN ('full_keno','crosschecked_abc') AND NOT EXISTS(SELECT 1 FROM conflicts x WHERE x.draw_id=d.id AND x.resolved=0) ORDER BY s.settled,s.target_draw,s.id",(start,end,end))
 grouped={}
 for r in records:grouped.setdefault(r['strategy'],[]).append(r)
 rows=[]
 for definition in definitions:
  orders=grouped.get(definition['id'],[]);bars=income_candles(orders,interval,end=datetime.fromtimestamp(cutoff-1,timezone.utc).isoformat()) if orders else []
  stage,analysis=classify(bars,rule,cutoff)
  equity=peak=drawdown=0;trough=0
  for o in orders:
   equity+=o['pnl']/100;peak=max(peak,equity);drawdown=max(drawdown,peak-equity);trough=min(trough,equity)
  recent=bars[-rule['lookback']:];high=max((b['high'] for b in recent),default=0);low=min((b['low'] for b in recent),default=0);close=recent[-1]['close'] if recent else 0
  rows.append({**definition,'stage':stage,'label':LABELS[stage],'reason':('收益斜率向上、站上BOLL中轨且最近三根高低点抬升' if stage=='up' else analysis['reason']),'orders':len(orders),'returns':{str(n):sum(o['pnl'] for o in orders[-n:])/100 if len(orders)>=n else None for n in (20,50,100)},'slope':analysis.get('normalized_slope'),'drawdown':peak-equity,'max_drawdown':drawdown,'repair':(close-low)/(high-low) if high>low else None,'cutoff_draw':max((o['target_draw'] for o in orders),default=None),'candles':bars,'analysis':analysis})
 return {'rows':rows,'labels':LABELS,'interval':interval,'cutoff':end,'start':start,'rule':rule,'source':'标准100U独立信号已结算收益，排除未收盘时段、未结算及冲突开奖；不含策略历史补单。近N单不足时不显示收益。阶段仅描述近期表现，不自动启停策略或机器人。'}
