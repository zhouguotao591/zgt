import csv,io,json,threading,queue,time,traceback
from datetime import datetime,timezone
from pathlib import Path
from dataclasses import asdict
from . import config
from .db import DB
from .model import Strategy,utcnow
from .collector import collect_once
from .ingest import import_file
from .engine import Prepared,replay
from .research import select_month,store_selection,walk_forward
from .router import tick,ensure_account
from .timing import DrawTimer
from . import trend_strategy,custom_strategy

class Service:
 def __init__(self,root):
  self.root=Path(root);self.cfg=config.load(root);self.db=DB(self.root/self.cfg['database']);ensure_account(self.db,self.cfg);self.lock=threading.RLock();self.stop=threading.Event();self.q=queue.Queue();self.collect_q=queue.Queue();self.task_lock=threading.Lock();self.tasks={};self.counter=0;self.active_task=None
  trend_strategy.initialize(self.db,self.cfg)
  from .history_backtest import initialize as initialize_history
  initialize_history(self.db)
  from .auth import Auth
  self.auth=Auth(self)
  from .algorithm_hall import AlgorithmHall
  self.algorithm_hall=AlgorithmHall(self.db)
  from .robot_signals import RobotSignals
  self.robot=RobotSignals(self)
  from .ai_analysis import AIAnalysis
  self.ai=AIAnalysis(self)
  from .tools import Tools
  self.tools=Tools(self)
  saved=self.db.query("SELECT value FROM runtime_state WHERE key='draw_timer'");self.draw_timer=DrawTimer(json.loads(saved[0]['value']) if saved else None)
  (self.root/'data/exports').mkdir(parents=True,exist_ok=True)
 def start(self):
  self.worker=threading.Thread(target=self._worker,name='pc28-jobs',daemon=True);self.worker.start();self.collect_worker=threading.Thread(target=self._worker,args=(self.collect_q,),name='pc28-collector',daemon=True);self.collect_worker.start();self.scheduler=threading.Thread(target=self._schedule,name='pc28-scheduler',daemon=True);self.scheduler.start();self.robot.start();self.ai.start()
 def submit(self,kind,args=None):
  if kind not in ('collect','archive','import','backtest','select','walkforward','tick','backup','export_draws','sync','repair_raw','repair_pending','trend_backtest','history_backtest'):raise ValueError('未知任务')
  if kind=='history_backtest':
   from .history_backtest import prepare
   args=prepare(self,args or {})
  with self.task_lock:
   if kind=='history_backtest':
    existing=next((t for t in self.tasks.values() if t['kind']==kind and t.get('strategy_id')==args['snapshot']['id'] and t['status'] in ('queued','running')),None)
    if existing:return existing['id']
   if len([x for x in self.tasks.values() if x['status'] in ('queued','running')])>=10:raise ValueError('任务队列已满')
   self.counter+=1;key=str(self.counter);self.tasks[key]={'id':key,'kind':kind,'status':'queued','created':utcnow(),'progress':''}
   if kind=='history_backtest':self.tasks[key]['strategy_id']=args['snapshot']['id']
   (self.collect_q if kind in ('collect','archive','sync','repair_raw','repair_pending') else self.q).put((key,kind,args or {}));return key
 def _worker(self,work_queue=None):
  work_queue=work_queue if work_queue is not None else self.q
  while not self.stop.is_set():
   try:key,kind,args=work_queue.get(timeout=1)
   except queue.Empty:continue
   self.tasks[key]['status']='running';self.active_task=key
   try:
    result=self.execute(kind,args,key);self.tasks[key].update(status='done',result=result,finished=utcnow())
   except Exception as e:
    self.tasks[key].update(status='error',error=str(e),finished=utcnow());self.db.log_job('task:'+kind,'error',{'error':str(e),'trace':traceback.format_exc()})
   finally:
    self.active_task=None;work_queue.task_done()
    with self.task_lock:
     finished=[k for k,v in self.tasks.items() if v['status'] in ('done','error')]
     for old in finished[:-100]:self.tasks.pop(old,None)
 def _schedule(self):
  next_poll=time.monotonic()+2;next_archive=time.monotonic()+120;next_repair=0;next_tick=0;attempted_month=None;next_select=0;selection_base=(self.cfg['simulation']['fixed']['base_cents'],self.cfg['simulation'].get('round_restart','immediate'))
  while not self.stop.wait(1):
   now=time.monotonic();c=self.cfg
   if (c['simulation']['fixed']['base_cents'],c['simulation'].get('round_restart','immediate'))!=selection_base:
    selection_base=(c['simulation']['fixed']['base_cents'],c['simulation'].get('round_restart','immediate'));attempted_month=None;next_select=0
   if now>=next_tick:
    try:
     with self.lock:
      tick(self.db,c);trend_strategy.live_tick(self.db,c);custom_strategy.tick(self.db,c)
    except Exception as e:self.db.log_job('simulation','error',{'error':str(e)})
    next_tick=now+5
   with self.lock:event_due=self.draw_timer.due(time.time())
   periodic_due=now>=next_poll
   if c['collector']['enabled'] and (event_due or periodic_due) and not any(t['status'] in ('queued','running') and t['kind']=='collect' for t in list(self.tasks.values())):
    args={'trigger':'draw_time' if event_due else 'periodic'}
    if event_due:
     with self.lock:
      args['expected_draw']=self.draw_timer.state['next_draw_id'];self.draw_timer.mark();self.save_draw_timer()
    self.submit('collect',args)
    if periodic_due:next_poll=now+c['collector']['interval_seconds']
   if c['collector']['enabled'] and now>=next_repair:
    if self.db.query("SELECT 1 FROM draws WHERE verification='crosschecked_abc' LIMIT 1") and not any(t['kind']=='repair_raw' and t['status'] in ('queued','running') for t in list(self.tasks.values())):self.submit('repair_raw')
    if self.db.query("SELECT 1 FROM trend_orders WHERE status='pending' AND target_draw NOT IN (SELECT id FROM draws) AND target_draw <= (SELECT MAX(id) FROM draws) UNION SELECT 1 FROM orders WHERE status='pending' AND target_draw NOT IN (SELECT id FROM draws) AND target_draw <= (SELECT MAX(id) FROM draws) UNION SELECT 1 FROM custom_orders WHERE status='pending' AND target_draw NOT IN (SELECT id FROM draws) AND target_draw <= (SELECT MAX(id) FROM draws) LIMIT 1") and not any(t['kind']=='repair_pending' and t['status'] in ('queued','running') for t in list(self.tasks.values())):self.submit('repair_pending')
    next_repair=now+60
   gaps=self.db.gaps(1)
   if c['collector']['enabled'] and now>=next_archive and (gaps or c['collector']['archive_url']):
    self.submit('archive');next_archive=now+c['collector']['archive_interval_hours']*3600
   month=datetime.now(timezone.utc).strftime('%Y-%m')
   if c['monthly_auto_select'] and c['simulation']['mode']!='fixed' and (attempted_month!=month or now>=next_select) and not self.db.query('SELECT month FROM selections WHERE month=?',(month,)):
    self.submit('select',{'month':month});attempted_month=month;next_select=now+21600
 def execute(self,kind,args,key=None):
  c=self.cfg
  if kind in ('collect','archive'):
   result=collect_once(self.db,self.root,c['collector'],archive=kind=='archive')
   with self.lock:
    if kind=='collect':
     result['trigger']=args.get('trigger','manual')
     latest=self.db.tail(1);self.draw_timer.update(result.get('draw_schedule'),latest[-1]['id'] if latest else 0);self.save_draw_timer()
     self.db.log_job('collection_trigger','ok',{'trigger':result['trigger'],'expected_draw':args.get('expected_draw'),'selected_source':result['selected_source']})
    result['simulation']=tick(self.db,c);result['trend_simulation']=trend_strategy.live_tick(self.db,c);custom_strategy.tick(self.db,c)
   return result
  if kind=='repair_pending':
   from .collector import repair_pending_once
   result=repair_pending_once(self.db,self.root,c['collector'])
   with self.lock:result['simulation']=tick(self.db,c);result['trend_simulation']=trend_strategy.live_tick(self.db,c);custom_strategy.tick(self.db,c)
   return result
  if kind=='repair_raw':
   from .collector import repair_raw_once
   return repair_raw_once(self.db,self.root,c['collector'])
  if kind=='sync':
   from .sync import sync_history
   return sync_history(self.db,self.root,c['collector'],int(args.get('max_pages',20)))
  if kind=='import':
   result=import_file(self.db,self.root,args['path'],c['collector']);return result
  if kind=='tick':
   with self.lock:
    result=tick(self.db,c);trend_strategy.live_tick(self.db,c);custom_strategy.tick(self.db,c);return result
  if kind=='export_draws':
   name='converted-draws-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'.csv'
   columns=['id','source_time','draw_utc','a','b','c','total','size','parity','numbers','verification','result_sources','conversion_version','first_seen','source']
   with (self.root/'data/exports'/name).open('w',encoding='utf-8-sig',newline='') as f:
    w=csv.DictWriter(f,fieldnames=columns,extrasaction='ignore');w.writeheader();w.writerows(self.db.rows())
   return {'file':'/exports/'+name}
  if kind=='backup':
   name='backup-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'.sqlite3';dest=self.root/'data/exports'/name;self.db.backup(dest);return {'file':'/exports/'+name}
  if kind=='history_backtest':
   from .history_backtest import execute as run_history
   def history_progress(done,total):
    if key:self.tasks[key].update(progress=f'已回放 {done}/{total} 期',done=done,total=total,percent=round(done*100/max(1,total),1))
   return run_history(self,args,history_progress)
  if kind=='trend_backtest':
   rows=self.tools.snapshot();result=trend_strategy.backtest(rows,c,args.get('start') or None,args.get('end') or None,args.get('with_risk_limits',True));stamp=utcnow().replace(':','').replace('+','_');name='trend-strategies-'+stamp+'.csv';items=result.pop('orders')
   export=[{**o,'stake':o['stake']/100,'pnl':o['pnl']/100} for o in items];self.export_orders(name,export);result['file']='/exports/'+name
   detail='trend-strategies-'+stamp+'.json';(self.root/'data/exports'/detail).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8');result['summary_file']='/exports/'+detail;self.db.log_job('trend_backtest','ok',result);return result
  rows=self.db.rows()
  if not rows:raise ValueError('请先导入或采集开奖数据')
  if kind=='backtest':
   s=Strategy(**args.get('strategy',c['simulation']['fixed']));risk=args.get('with_risk_limits',True);settings=c['simulation'];kwargs={}
   if risk:
    from .model import money
    kwargs=dict(initial_cents=money(settings['initial_balance']),max_stake=money(settings['max_stake']),daily_limit=money(settings['daily_loss_limit']),dd_limit=money(settings['max_drawdown']))
   result=replay(Prepared(rows),s,c['odds'],args.get('start') or None,args.get('end') or None,trace=True,restart_policy=c['simulation'].get('round_restart','immediate'),win_limit=c['simulation'].get('win_limit',0),pause_draws=c['simulation'].get('pause_draws',20),**kwargs);name='backtest-'+utcnow().replace(':','').replace('+','_')+'.csv';self.export_orders(name,result.pop('orders'));result['file']='/exports/'+name;self.db.log_job('backtest','ok',result);return result
  def progress(i,total):
   if key:self.tasks[key]['progress']=f'{i}/{total}'
  if kind=='select':
   month=args.get('month',datetime.now(timezone.utc).strftime('%Y-%m'));result=select_month(rows,c,month,progress)
   with self.lock:
    if c['simulation']['fixed']['base_cents']!=self.cfg['simulation']['fixed']['base_cents'] or c['odds']!=self.cfg['odds']:raise ValueError('评估期间金额或赔率已变更，请重新评估')
    run=store_selection(self.db,result,c)
   name=f'selection-{month}-run{run}.json';(self.root/'data/exports'/name).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8');return {'run_id':run,'month':month,'grid_count':result['grid_count'],'candidate_count':len(result['candidates']),'data_complete':result['data_complete'],'reason':result['reason'],'file':'/exports/'+name,'candidates':result['candidates'],'note':'同月首次选中的名单冻结，重复研究不改写已启用名单'}
  if kind=='walkforward':
   result=walk_forward(rows,c,args['start'],args['end'],progress);name='walkforward-'+utcnow().replace(':','').replace('+','_')+'.json';(self.root/'data/exports'/name).write_text(json.dumps(result,ensure_ascii=False),encoding='utf8');brief={k:v for k,v in result['portfolio'].items() if k!='orders'};return {'portfolio':brief,'months':len(result['months']),'file':'/exports/'+name}
  raise ValueError('未知任务')
 def export_orders(self,name,orders):
  with (self.root/'data/exports'/name).open('w',encoding='utf-8-sig',newline='') as f:
   if orders:w=csv.DictWriter(f,fieldnames=list(orders[0]));w.writeheader();w.writerows(orders)
   else:f.write('没有符合条件的模拟订单\n')
 def update_config(self,c):
  config.validate(c)
  if c['database']!=self.cfg['database'] or c['port']!=self.cfg['port'] or c['host']!=self.cfg['host']:raise ValueError('数据库/地址/端口只能停服后修改config.json')
  with self.lock:
   from .strategy_delete import deleted_ids
   removed=deleted_ids(self.db)
   if ('legacy' in removed and c['simulation']['enabled']) or removed.intersection(c['trend_simulation']['enabled_axes']):raise ValueError('已删除的策略不能重新启用')
   base_changed=c['simulation']['fixed']['base_cents']!=self.cfg['simulation']['fixed']['base_cents'] or c['simulation'].get('round_restart','immediate')!=self.cfg['simulation'].get('round_restart','immediate')
   if base_changed:
    month=datetime.now(timezone.utc).strftime('%Y-%m');old=self.db.query('SELECT * FROM selections WHERE month>=?',(month,))
    if old:
     name='selections-before-amount-change-'+utcnow().replace(':','')+'.json';(self.root/'data/exports'/name).write_text(json.dumps(old,ensure_ascii=False,indent=2),encoding='utf8')
     with self.db.tx() as conn:conn.execute('DELETE FROM selections WHERE month>=?',(month,))
   config.save(self.root,c);self.cfg=c
   audit=json.loads(json.dumps(c));audit['collector']['proxy']='[redacted]' if audit['collector']['proxy'] else ''
   with self.db.tx() as conn:conn.execute('INSERT INTO settings_audit(created,config) VALUES(?,?)',(utcnow(),json.dumps(audit,ensure_ascii=False)))
 def pause(self,paused):
  with self.lock,self.db.tx() as c:c.execute('UPDATE accounts SET paused=?,pause_reason=? WHERE id=1',(int(paused),'手动暂停' if paused else ''))
 def save_draw_timer(self):
  with self.db.tx() as c:c.execute("INSERT INTO runtime_state(key,value,updated) VALUES('draw_timer',?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated=excluded.updated",(json.dumps(self.draw_timer.state),utcnow()))
 def display_draw(self,row):
  if row is None:return None
  from zoneinfo import ZoneInfo
  from .model import parse_time
  r=dict(row);dt=None
  if r.get('draw_utc'):dt=parse_time(r['draw_utc'])
  else:
   zone=self.cfg['collector'].get('fallback_timezone','Asia/Shanghai') if 'pc28.world' in r.get('source','') else self.cfg['collector'].get('source_timezone')
   if zone:dt=parse_time(r['source_time']).replace(tzinfo=ZoneInfo(zone))
  r['beijing_time']=dt.astimezone(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d %H:%M:%S') if dt else None
  return r
 def status(self):
  db=self.db;last=db.tail(1);latest=last[0] if last else None;fresh=False;age=None
  if latest and latest['draw_utc']:
   age=(datetime.now(timezone.utc)-datetime.fromisoformat(latest['draw_utc'])).total_seconds();fresh=-30<=age<=self.cfg['collector']['max_age_seconds']
  account=db.query('SELECT * FROM accounts WHERE id=1')[0];account['state']=json.loads(account['state']);account['settled_net']=db.query("SELECT COALESCE(SUM(pnl),0) n FROM orders WHERE status='settled'")[0]['n']/100
  account['pending_stake']=db.query("SELECT COALESCE(SUM(stake),0) n FROM orders WHERE status='pending'")[0]['n']/100
  health=self.db.query("SELECT status,finished,details FROM jobs WHERE kind='latest' ORDER BY id DESC LIMIT 1")
  collection_health=health[0] if health else None
  if collection_health:collection_health['details']=json.loads(collection_health['details'])
  from .timing import infer_break,publication_state
  inferred=infer_break(db) if latest and age is not None and age>210 else None
  plan=inferred or dict(self.draw_timer.state);publication=publication_state(latest,plan)
  if publication['phase']!='live':fresh=False
  return {'collection_health':collection_health,'version':'1.0.0','count':db.query('SELECT COUNT(*) n FROM draws')[0]['n'],'latest':self.display_draw(latest),'publication':publication,'next_draw':plan,'fresh':fresh,'age_seconds':age,'gaps':db.gaps(),'pending_raw':db.query("SELECT COUNT(*) n FROM draws WHERE verification='crosschecked_abc'")[0]['n'],'conflicts':db.query('SELECT COUNT(*) n FROM conflicts WHERE resolved=0')[0]['n'],'account':account,'orders':db.query('SELECT * FROM orders ORDER BY id DESC LIMIT 60'),'decisions':db.query('SELECT * FROM decisions ORDER BY target_draw DESC LIMIT 30'),'jobs':db.query('SELECT * FROM jobs ORDER BY id DESC LIMIT 20'),'tasks':list(self.tasks.values())[-20:],'selections':db.query('SELECT month,created,run_id,reason,candidates FROM selections ORDER BY month DESC LIMIT 12'),'switches':db.query('SELECT * FROM switches ORDER BY id DESC LIMIT 20'),'rounds':db.query('SELECT * FROM rounds ORDER BY id DESC LIMIT 30'),'restart_policy':self.cfg['simulation'].get('round_restart','immediate'),'mode':self.cfg['simulation']['mode'],'enabled':self.cfg['simulation']['enabled'],'server_time':utcnow()}
