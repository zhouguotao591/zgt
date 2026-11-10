"""Persisted, read-only AI probability analysis and prospective scoring worker."""
import json,threading,time,uuid,traceback,os
from datetime import datetime,timezone
from .model import utcnow
from . import ai_model,maintenance
from .ai_simulation import PaperSimulation

SCHEMA="""
CREATE TABLE IF NOT EXISTS ai_models(id TEXT PRIMARY KEY,created TEXT NOT NULL,cutoff_id INTEGER NOT NULL,version TEXT NOT NULL,artifact TEXT NOT NULL,report TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ai_validation(model_id TEXT NOT NULL,axis TEXT NOT NULL,target_draw INTEGER NOT NULL,record TEXT NOT NULL,PRIMARY KEY(model_id,axis,target_draw));
CREATE TABLE IF NOT EXISTS ai_predictions(target_draw INTEGER NOT NULL,axis TEXT NOT NULL,model_id TEXT NOT NULL,cutoff_draw INTEGER NOT NULL,created TEXT NOT NULL,features TEXT NOT NULL,probability REAL NOT NULL,baseline REAL NOT NULL,actual INTEGER,brier REAL,hit INTEGER,eligible INTEGER NOT NULL DEFAULT 1,resolved TEXT,PRIMARY KEY(target_draw,axis));
CREATE INDEX IF NOT EXISTS ai_predictions_unresolved ON ai_predictions(actual,target_draw);
CREATE TABLE IF NOT EXISTS ai_training(id INTEGER PRIMARY KEY CHECK(id=1),state TEXT NOT NULL);
"""

class AIAnalysis:
 def __init__(self,service):
  self.service=service;self.db=service.db;self.train_lock=threading.Lock();self.error_until=0;self.cache_id=None;self.cache=None
  with self.db.tx() as c:
   for sql in SCHEMA.split(';'):
    if sql.strip():c.execute(sql)
   c.execute('INSERT OR IGNORE INTO ai_training VALUES(1,?)',(json.dumps({'status':'idle','percent':0,'message':'等待首次训练'}),))
  self.paper=PaperSimulation(service)
 def state(self):return json.loads(self.db.query('SELECT state FROM ai_training WHERE id=1')[0]['state'])
 def progress(self,percent,message,status='running',**extra):
  with self.db.tx() as c:c.execute('UPDATE ai_training SET state=? WHERE id=1',(json.dumps({'status':status,'percent':percent,'message':message,'updated':utcnow(),**extra},ensure_ascii=False),))
 def model(self):
  rows=self.db.query('SELECT * FROM ai_models ORDER BY created DESC,rowid DESC LIMIT 1')
  if not rows:return None
  row=rows[0]
  if self.cache_id!=row['id']:self.cache_id=row['id'];self.cache={**row,'artifact':json.loads(row['artifact']),'report':json.loads(row['report'])}
  return self.cache
 def request_train(self):
  if not self.train_lock.acquire(False):return {'ok':True,'status':'running'}
  self.progress(1,'准备读取本地历史数据')
  def worker():
   try:self.train_now()
   finally:self.train_lock.release()
  threading.Thread(target=worker,name='pc28-ai-train',daemon=True).start();return {'ok':True,'status':'running'}
 def train_now(self,**options):
  # NumPy is optional for the rest of the site; keep heavy training off service locks.
  os.environ.setdefault('OPENBLAS_NUM_THREADS','1');os.environ.setdefault('OMP_NUM_THREADS','1')
  try:
   self.progress(2,'校验期号、时间及开奖记录')
   c=self.db.connect()
   try:
    c.execute('BEGIN');raw=[dict(r) for r in c.execute('SELECT id,draw_utc,a,b,c,total,size,parity FROM draws ORDER BY id')];conflicts={r[0] for r in c.execute('SELECT draw_id FROM conflicts WHERE resolved=0')}
   finally:c.close()
   samples,history,quality=ai_model.dataset(raw,conflicts)
   if not history:raise ValueError('没有有效的本地开奖记录')
   self.progress(5,'计算历史特征及滚动验证')
   artifact,validation=ai_model.train(samples,self.progress,**options);key=uuid.uuid4().hex;created=utcnow()
   report={'version':ai_model.VERSION,'created':created,'cutoff_id':history[-1]['id'],'quality':quality,'axes':{axis:{k:v for k,v in obj.items() if k!='model'} for axis,obj in artifact.items()},'protocol':{'train_days':options.get('train_days',90),'calibration_days':options.get('cal_days',14),'test_days':options.get('test_days',7),'folds':options.get('folds',6),'availability':'历史回放假设开奖及时可用；线上评价仅使用开奖前真实留档的预测'},'note':'历史验证不代表未来结果；未验证优势时仅作研究统计，不连接下单和机器人。'}
   self.progress(97,'保存模型及逐期验证结果')
   with self.db.tx() as c:
    c.execute('INSERT INTO ai_models VALUES(?,?,?,?,?,?)',(key,created,history[-1]['id'],ai_model.VERSION,json.dumps(artifact,allow_nan=False),json.dumps(report,ensure_ascii=False,allow_nan=False)))
    c.executemany('INSERT INTO ai_validation VALUES(?,?,?,?)',[(key,r['axis'],r['id'],json.dumps(r)) for r in validation])
   self.progress(100,'训练与滚动验证已完成',status='done',model_id=key);self.error_until=0;return report
  except Exception as e:
   message='AI训练依赖未安装，请按 requirements-ai.txt 安装 NumPy' if isinstance(e,ImportError) else str(e)
   self.progress(0,message,status='error');self.error_until=time.time()+3600
   self.db.log_job('ai-training','error',{'error':message});return None
 def settle(self):
  with self.db.tx() as c:
   conflicts={r[0] for r in c.execute('SELECT draw_id FROM conflicts WHERE resolved=0')}
   rows=c.execute('SELECT p.*,d.size,d.parity,d.draw_utc FROM ai_predictions p JOIN draws d ON d.id=p.target_draw WHERE p.actual IS NULL').fetchall()
   for row in rows:
    label='大' if row['axis']=='size' else '单';actual=int(row[row['axis']]==label)
    try:valid=row['target_draw'] not in conflicts and datetime.fromisoformat(row['created'])<datetime.fromisoformat(row['draw_utc'])<=datetime.now(timezone.utc)
    except (TypeError,ValueError):valid=False
    c.execute('UPDATE ai_predictions SET actual=?,brier=?,hit=?,eligible=?,resolved=? WHERE target_draw=? AND axis=?',(actual,(row['probability']-actual)**2 if valid else None,int((row['probability']>=.5)==bool(actual)) if valid else None,int(valid),utcnow(),row['target_draw'],row['axis']))
 def live_snapshot(self):
  raw=list(reversed(self.db.query('SELECT id,draw_utc,a,b,c,total,size,parity FROM draws ORDER BY id DESC LIMIT 201')))
  conflicts={r['draw_id'] for r in self.db.query('SELECT draw_id FROM conflicts WHERE resolved=0')};_,history,_=ai_model.dataset(raw,conflicts)
  if len(history)<ai_model.WINDOW:return history,'有效连续样本不足，等待数据'
  newest=self.db.query('SELECT MAX(id) id FROM draws')[0]['id']
  if history[-1]['id']!=newest:return history,'最新开奖尚未通过时间或数据校验'
  now=datetime.now(timezone.utc);lag=(now-history[-1]['_time']).total_seconds()
  if lag>210 or lag<0:return history,'数据尚未同步，暂停生成下一期概率'
  if maintenance.block_reason(history[-1],now):return history,'暂停休息中，待新开奖后更新'
  return history,''
 def tick(self):
  self.settle();self.paper.tick();model=self.model()
  if not model:return
  history,reason=self.live_snapshot()
  if reason:return
  latest=history[-1]
  for axis in ('size','parity'):
   x,key,context=ai_model.features(history,axis);m=model['artifact'][axis]['model'];probability,parts=ai_model.predict(m,x,key)
   with self.db.tx() as c:c.execute('INSERT OR IGNORE INTO ai_predictions(target_draw,axis,model_id,cutoff_draw,created,features,probability,baseline) VALUES(?,?,?,?,?,?,?,?)',(latest['id']+1,axis,model['id'],latest['id'],utcnow(),json.dumps({'version':ai_model.VERSION,'values':x,'condition':key,'components':parts}),probability,m['baseline']))
   self.paper.open(self.db.query('SELECT * FROM ai_predictions WHERE axis=? AND target_draw=?',(axis,latest['id']+1))[0])
 def public(self,window=20):
  window=window if window in (20,50,100) else 20
  state=self.state();model=self.model();history,reason=self.live_snapshot();latest=history[-1]['id'] if history else None;cards=[]
  for axis,positive,negative in (('size','大','小'),('parity','单','双')):
   context=ai_model.features(history,axis)[2] if len(history)>=ai_model.WINDOW else None
   pred=self.db.query('SELECT * FROM ai_predictions WHERE axis=? AND cutoff_draw=? ORDER BY target_draw DESC LIMIT 1',(axis,latest)) if model and latest else []
   prediction=pred[0] if pred else None;active=bool(prediction and not reason)
   used_model=model
   if prediction and model and prediction['model_id']!=model['id']:
    saved=self.db.query('SELECT created,version,report FROM ai_models WHERE id=?',(prediction['model_id'],))
    if saved:used_model={**saved[0],'report':json.loads(saved[0]['report'])}
   evaluation=used_model['report']['axes'][axis]['evaluation'] if used_model else ai_model.metrics([])
   online=self.db.query('SELECT COUNT(*) count,AVG(hit) hit_rate,AVG(brier) brier FROM ai_predictions WHERE axis=? AND actual IS NOT NULL AND eligible=1',(axis,))[0]
   # Projection deliberately excludes model coefficients, feature values and account data.
   cards.append({'axis':axis,'name':'大小分析' if axis=='size' else '单双分析','positive':positive,'negative':negative,'cutoff_draw':latest,'target_draw':prediction['target_draw'] if prediction else None,'generated_at':prediction['created'] if prediction else None,'probability':prediction['probability'] if active else None,'complement':1-prediction['probability'] if active else None,'state':reason or ('等待首次训练' if not model else '等待生成最新分析' if not prediction else '研究估计 · 历史验证支持' if evaluation['qualified'] else '研究估计 · 未验证优势'),'recent':context,'validation':evaluation,'online':online,'model_created':used_model['created'] if used_model else None,'model_version':used_model['version'] if used_model else ai_model.VERSION})
   card=cards[-1];card['simulation']=self.paper.public(axis,used_model['id'] if used_model and 'id' in used_model else prediction['model_id'] if prediction else model['id'] if model else None,window)
   card['direction']=(positive if prediction['probability']>.5 else negative if prediction['probability']<.5 else None) if active else None
   parts=json.loads(prediction['features']).get('components',[]) if active else []
   card['signals']=[{'name':name,'positive':p,'direction':positive if p>.5 else negative if p<.5 else '持平'} for name,p in zip(('历史基准','条件统计','校准模型'),parts)]
   card['next_draw_at']=(history[-1]['_time'].timestamp()+210) if active else None
  return {'cards':cards,'training':{k:state.get(k) for k in ('status','percent','message')},'note':'独立模拟，不连接会员推送。未中按500→600→750→500循环，命中（含退本金）恢复500。线上模拟从功能启用后留档；历史回放使用样本外预测、每个展示窗口从500起，按当前赔率重算。概率是研究估计，尚未证明稳定优势。展示期数不改变模型或线上金额。'}
 def admin(self):
  model=self.model();return {'training':self.state(),'model_id':model['id'] if model else None,'report':model['report'] if model else None,'public':self.public()}
 def start(self):
  if self.state().get('status')=='running':self.progress(0,'上次训练已中断，等待重新训练',status='idle')
  def run():
   while not self.service.stop.is_set():
    try:
     self.tick();model=self.model()
     if time.time()>=self.error_until and (not model or (datetime.now(timezone.utc)-datetime.fromisoformat(model['created'])).total_seconds()>7*86400):self.request_train()
    except Exception as e:self.db.log_job('ai-analysis','error',{'error':str(e)})
    self.service.stop.wait(5)
  threading.Thread(target=run,name='pc28-ai-analysis',daemon=True).start()
