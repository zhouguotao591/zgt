"""Deterministic, explainable size/parity algorithms evaluated strictly on prior draws."""
import hashlib,json,threading
from datetime import datetime,timezone
from zoneinfo import ZoneInfo
from collections import Counter
BJ=ZoneInfo('Asia/Shanghai')
SCHEMA="""
CREATE TABLE IF NOT EXISTS algorithm_favorites(user_id INTEGER NOT NULL,algorithm_id TEXT NOT NULL,created TEXT NOT NULL,PRIMARY KEY(user_id,algorithm_id));
"""

def definitions():
 result=[]
 for axis,title in [('size','大小'),('parity','单双')]:
  for kind,n,name,rule in [('follow',1,'上期跟随','使用上期结果作为下一期判断。'),('reverse',1,'上期反转','使用与上期相反的结果作为下一期判断。'),*[(kind,n,f'近{n}期'+('占优跟随' if kind=='majority' else '少数反转'),f'统计此前{n}期次数，'+('选择较多的一方' if kind=='majority' else '选择较少的一方')+'；持平时跟随上期结果。') for n in (5,10,20,30,50) for kind in ('majority','minority')],*[("transition",n,f'近{n}期转移跟随',f'观察此前{n}次相邻结果的转移，选择当前结果之后历史出现较多的一方；无样本或持平时跟随上期。') for n in (20,50)]]:
   result.append({'id':f'{axis}.{kind}.{n}','axis':axis,'category':title,'name':title+' · '+name,'kind':kind,'lookback':n+1 if kind=='transition' else n,'rule':rule,'version':'local-formula-v1'})
 return result
DEFINITIONS=definitions();BY_ID={r['id']:r for r in DEFINITIONS}

def predict(history,definition):
 n=definition['lookback'];axis=definition['axis']
 if len(history)<n:return None,'观察样本不足'
 h=history[-n:]
 if any(b['id']!=a['id']+1 for a,b in zip(h,h[1:])):return None,'观察窗口存在缺期或未核验开奖'
 labels=('大','小') if axis=='size' else ('单','双');last=h[-1][axis];other=next(v for v in labels if v!=last);kind=definition['kind']
 if kind=='follow':return last,'上期为'+last
 if kind=='reverse':return other,'上期为'+last+'，反向判断'
 if kind=='transition':counts=Counter(b[axis] for a,b in zip(h,h[1:]) if a[axis]==last)
 else:counts=Counter(r[axis] for r in h)
 a,b=labels;ca,cb=counts[a],counts[b]
 if ca==cb:choice=last
 elif kind=='minority':choice=a if ca<cb else b
 else:choice=a if ca>cb else b
 text=f'{a} {ca} 次，{b} {cb} 次'+('；持平跟随上期' if ca==cb else '')
 return choice,text

def metrics(records,window):
 selected=records[-window:];valid=[r for r in selected if r['hit'] is not None];wins=sum(r['hit'] for r in valid);current_hit=current_miss=max_hit=max_miss=run_hit=run_miss=0;previous=None
 for r in selected:
  if previous is not None and r['target_draw']!=previous+1:run_hit=run_miss=0
  if r['hit'] is None:run_hit=run_miss=0
  elif r['hit']:run_hit+=1;run_miss=0
  else:run_miss+=1;run_hit=0
  max_hit=max(max_hit,run_hit);max_miss=max(max_miss,run_miss);previous=r['target_draw']
 current_hit,current_miss=run_hit,run_miss
 return {'wins':wins,'count':len(valid),'observed':len(selected),'skipped':len(selected)-len(valid),'rate':wins/len(valid) if valid else None,'current_hit':current_hit,'current_miss':current_miss,'max_hit':max_hit,'max_miss':max_miss}

def evaluate(rows,definition):
 records=[]
 if rows:
  by_id={r['id']:r for r in rows};start=max(rows[0]['id'],rows[-1]['id']-99)
  for target_id in range(start,rows[-1]['id']+1):
   target=by_id.get(target_id);history=[r for r in rows if r['id']<target_id]
   if target is None:
    records.append({'target_draw':target_id,'cutoff_draw':history[-1]['id'] if history else None,'time':None,'prediction':None,'actual':None,'hit':None,'formula':'缺失或待核验','reason':'本地缺少已核验开奖，不计入有效样本'})
    continue
   choice,reason=predict(history,definition)
   if history and history[-1]['id']+1!=target_id:choice,reason=None,'目标期之前存在缺期'
   records.append({'target_draw':target_id,'cutoff_draw':history[-1]['id'] if history else None,'time':target.get('draw_utc'),'prediction':choice,'actual':target[definition['axis']],'hit':int(choice==target[definition['axis']]) if choice else None,'formula':f"{target['a']}+{target['b']}+{target['c']} = {target['total']}",'reason':reason})
 choice,reason=predict(rows,definition);latest=rows[-1] if rows else None
 pending={'target_draw':latest['id']+1 if latest else None,'cutoff_draw':latest['id'] if latest else None,'prediction':choice,'reason':reason}
 return {**definition,'metrics':{'20':metrics(records,20),'100':metrics(records,100)},'pending':pending,'records':records}

class AlgorithmHall:
 def __init__(self,db):
  self.db=db;self.lock=threading.RLock();self.fingerprint=None;self.cached=[]
  with db.tx() as c:
   for sql in SCHEMA.split(';'):
    if sql.strip():c.execute(sql)
 def snapshot(self):
  # One SQLite statement freezes the input; unresolved conflicts and future times are withheld.
  rows=self.db.query("SELECT d.id,d.draw_utc,d.a,d.b,d.c,d.total,d.size,d.parity FROM draws d WHERE d.draw_utc IS NOT NULL AND julianday(d.draw_utc)<=julianday('now') AND d.verification IN ('full_keno','crosschecked_abc') AND NOT EXISTS(SELECT 1 FROM conflicts c WHERE c.draw_id=d.id AND c.resolved=0) ORDER BY d.id DESC LIMIT 200");rows.reverse();latest_stored=self.db.query('SELECT MAX(id) id FROM draws')[0]['id'];fingerprint=hashlib.sha256(json.dumps([rows,latest_stored],sort_keys=True).encode()).hexdigest()
  with self.lock:
   if fingerprint!=self.fingerprint:
    self.cached=[evaluate(rows,d) for d in DEFINITIONS];self.fingerprint=fingerprint
    if rows and latest_stored!=rows[-1]['id']:
     for item in self.cached:item['pending'].update(prediction=None,reason='最新本地开奖尚未通过时间或冲突核验，暂不提供下一期判断')
   return self.cached,self.fingerprint
 def listing(self,user_id):
  rows,version=self.snapshot();counts={r['algorithm_id']:r['n'] for r in self.db.query('SELECT algorithm_id,COUNT(*) n FROM algorithm_favorites GROUP BY algorithm_id')};favorites={r['algorithm_id'] for r in self.db.query('SELECT algorithm_id FROM algorithm_favorites WHERE user_id=?',(user_id,))}
  return {'algorithms':[{**{k:v for k,v in r.items() if k!='records'},'favorite':r['id'] in favorites,'favorites':counts.get(r['id'],0)} for r in rows],'version':version,'latest_id':rows[0]['pending']['cutoff_draw'] if rows else None,'note':'指标为当前已核验本地数据的历史回放，并非当时发布记录。判断仅用目标期之前的数据；历史胜率不代表下一期命中。缺期或样本不足不计入有效样本，连中连挂在缺期处中断。'}
 def detail(self,algorithm_id,page=1,page_size=20):
  if algorithm_id not in BY_ID:raise ValueError('算法不存在')
  page=int(page);size=int(page_size)
  if size not in (10,20,50,100):raise ValueError('每页条数须为10、20、50、100')
  rows,version=self.snapshot();r=next(x for x in rows if x['id']==algorithm_id);records=list(reversed(r['records']));total=len(records);pages=max(1,(total+size-1)//size);page=max(1,min(page,pages))
  return {**{k:v for k,v in r.items() if k!='records'},'records':records[(page-1)*size:page*size],'page':page,'pages':pages,'page_size':size,'total':total,'version':version}
 def favorite(self,user_id,body):
  key=body.get('id');enabled=body.get('favorite')
  if key not in BY_ID or type(enabled)!=bool:raise ValueError('算法或收藏参数无效')
  from .model import utcnow
  with self.db.tx() as c:
   if enabled:c.execute('INSERT OR IGNORE INTO algorithm_favorites VALUES(?,?,?)',(user_id,key,utcnow()))
   else:c.execute('DELETE FROM algorithm_favorites WHERE user_id=? AND algorithm_id=?',(user_id,key))
  return {'ok':True,'favorite':enabled}
