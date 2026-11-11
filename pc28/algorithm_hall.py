"""Deterministic, explainable size/parity algorithms evaluated strictly on prior draws."""
import hashlib,json,threading
from collections import Counter
from bisect import bisect_left
VERIFIED=('full_keno','crosschecked_abc')
RANK_WINDOW=20
HALL_WINDOWS=(20,100,500,1000,2000,5000)
ORDER_CANDLE_SIZES={'1o':1,'20o':20,'50o':50,'100o':100}
MAX_BACKTEST_DRAWS=50000
PAPER_INITIAL=1000000
PAPER_BASE=10000
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

def summarize_hits(hits,observed):
 wins=count=max_hit=max_miss=run_hit=run_miss=0;previous=None
 for target,hit in hits:
  if previous is not None and target!=previous+1:run_hit=run_miss=0
  if hit is None:run_hit=run_miss=0
  elif hit:wins+=1;count+=1;run_hit+=1;run_miss=0
  else:count+=1;run_miss+=1;run_hit=0
  if run_hit>max_hit:max_hit=run_hit
  if run_miss>max_miss:max_miss=run_miss
  previous=target
 return {'wins':wins,'count':count,'observed':observed,'skipped':observed-count,'rate':wins/count if count else None,'current_hit':run_hit,'current_miss':run_miss,'max_hit':max_hit,'max_miss':max_miss}

def metrics(records,window):
 selected=records[-window:]
 return summarize_hits(((r['target_draw'],r['hit']) for r in selected),len(selected))

def evaluate(rows,definition,window=100):
 records=[]
 if rows:
  ids=[r['id'] for r in rows];by_id={r['id']:r for r in rows};start=max(ids[0],ids[-1]-window+1)
  for target_id in range(start,rows[-1]['id']+1):
   target=by_id.get(target_id);i=bisect_left(ids,target_id);history=rows[max(0,i-definition['lookback']):i]
   if target is None:
    records.append({'target_draw':target_id,'cutoff_draw':history[-1]['id'] if history else None,'time':None,'prediction':None,'actual':None,'hit':None,'formula':'缺失或待核验','reason':'本地缺少已核验开奖，不计入有效样本'})
    continue
   choice,reason=predict(history,definition)
   if history and history[-1]['id']+1!=target_id:choice,reason=None,'目标期之前存在缺期'
   records.append({'target_draw':target_id,'cutoff_draw':history[-1]['id'] if history else None,'time':target.get('draw_utc'),'prediction':choice,'actual':target[definition['axis']],'hit':int(choice==target[definition['axis']]) if choice else None,'formula':f"{target['a']}+{target['b']}+{target['c']} = {target['total']}",'reason':reason})
 choice,reason=predict(rows,definition);latest=rows[-1] if rows else None
 pending={'target_draw':latest['id']+1 if latest else None,'cutoff_draw':latest['id'] if latest else None,'prediction':choice,'reason':reason}
 return {**definition,'metrics':{'20':metrics(records,20),'100':metrics(records,100)},'pending':pending,'records':records}

def ranked_algorithms(rows,axis,hidden,limit):
 if not rows:return []
 candidates=[]
 for d in DEFINITIONS:
  if d['axis']!=axis or d['id'] in hidden:continue
  result=evaluate(rows,d,RANK_WINDOW)
  rate=result['metrics'][str(RANK_WINDOW)]['rate']
  if rate is not None:candidates.append((-rate,d['id']))
 return [key for _,key in sorted(candidates)[:limit]]

def attributed_orders(rows):
 by_algorithm={d['id']:[] for d in DEFINITIONS}
 for row in rows:
  for evidence in json.loads(row['evidence'] or '[]'):
   keys={p['id'] for p in evidence.get('predictions',[])
         if p.get('id') in BY_ID and p.get('prediction')==row['prediction']}
   if not keys and evidence.get('rank') and len(evidence.get('algorithm_ids',[]))==1 and evidence.get('votes')=={row['prediction']:1}:
    keys={key for key in evidence['algorithm_ids'] if key in BY_ID}
   if evidence.get('cutoff_draw')!=row['cutoff_draw']:continue
   record={k:v for k,v in row.items() if k!='evidence'}
   record['credit']=row['stake']+row['pnl']
   record['reason']=evidence.get('reason','')
   for key in keys:by_algorithm[key].append(record)
   break
 return by_algorithm

def recent_orders(records,cutoff,window):
 return [r for r in records if cutoff is not None and cutoff-window<r['target_draw']<=cutoff]

def order_totals(records):
 net=sum(r['pnl'] for r in records);stake=sum(r['stake'] for r in records)
 return {'orders':len(records),'stake':stake,'net':net,'return_rate':net/stake if stake else None}

def order_candles(trades,size):
 bars=[];equity=0
 for start in range(0,len(trades),size):
  batch=trades[start:start+size]
  opening=high=low=equity
  for trade in batch:
   equity+=trade['pnl']
   high=max(high,equity);low=min(low,equity)
  bars.append({'time':batch[0]['beijing_time'],'end_time':batch[-1]['beijing_time'],
               'first_order':start+1,'last_order':start+len(batch),'orders':len(batch),
               'open':opening/100,'high':high/100,'low':low/100,'close':equity/100})
 return bars

def backtest_algorithm(draws,definition,periods,odds,interval):
 from datetime import datetime
 from .history_backtest import income_candles
 from .model import settlement
 records=evaluate(draws,definition,periods)['records']
 labels=('大','小') if definition['axis']=='size' else ('单','双')
 directions={label:{'actual':0,'predicted':0,'orders':0} for label in labels}
 totals={r['id']:r['total'] for r in draws}
 balance=peak=PAPER_INITIAL;stake=PAPER_BASE;investment=wins=refunds=drawdown=0
 trades=[]
 for row in records:
  if row['actual'] is not None:directions[row['actual']]['actual']+=1
  choice=row['prediction']
  if choice is None:continue
  directions[choice]['predicted']+=1
  if balance<stake:continue
  directions[choice]['orders']+=1
  paid=settlement(stake,choice,totals[row['target_draw']],row['hit'],odds)
  investment+=stake;wins+=row['hit'];refunds+=paid['outcome']=='refund'
  balance+=paid['pnl'];peak=max(peak,balance);drawdown=max(drawdown,peak-balance)
  trades.append({'beijing_time':row['time'],'pnl':paid['pnl']})
  if paid['outcome']=='win':stake=PAPER_BASE
  elif paid['outcome']=='loss':stake=stake+2500 if stake<20000 else PAPER_BASE
 trades.sort(key=lambda trade:datetime.fromisoformat(trade['beijing_time']))
 if interval in ORDER_CANDLE_SIZES:
  bars=order_candles(trades,ORDER_CANDLE_SIZES[interval])
 else:
  times=[row['time'] for row in records if row['time']]
  start=min(times,key=datetime.fromisoformat) if times else None
  end=max(times,key=datetime.fromisoformat) if times else None
  bars=income_candles(trades,interval,start,end)
 return {'id':definition['id'],'interval':interval,'periods':periods,'observed':len(records),
         'first_draw':records[0]['target_draw'] if records else None,
         'last_draw':records[-1]['target_draw'] if records else None,
         'orders':len(trades),'wins':wins,'refunds':refunds,'win_rate':wins/len(trades) if trades else None,
         'net':balance-PAPER_INITIAL,'max_drawdown':drawdown,'investment':investment,
         'directions':directions,'candles':bars}

class AlgorithmHall:
 def __init__(self,db):
  self.db=db;self.lock=threading.RLock();self.fingerprint=None;self.cached=[]
  with db.tx() as c:
   for sql in SCHEMA.split(';'):
    if sql.strip():c.execute(sql)
 def snapshot(self):
  with self.lock:
   draws=self.db.query("""SELECT d.id,d.draw_utc,d.a,d.b,d.c,d.total,d.size,d.parity FROM draws d
      WHERE d.draw_utc IS NOT NULL AND julianday(d.draw_utc)<=julianday('now')
      AND d.verification IN ('full_keno','crosschecked_abc')
      AND NOT EXISTS(SELECT 1 FROM conflicts c WHERE c.draw_id=d.id AND c.resolved=0)
      ORDER BY d.id DESC LIMIT 5051""")[::-1]
   latest_stored=self.db.query('SELECT MAX(id) id FROM draws')[0]['id']
   orders=self.db.query("""SELECT o.id,o.target_draw,o.cutoff_draw,o.direction AS prediction,
      o.stake,o.pnl,o.hit,o.outcome,o.settled,d.draw_utc AS time,
      d.a||'+'||d.b||'+'||d.c||' = '||d.total AS formula,
      json_extract(o.strategy_json,'$.evidence') AS evidence
      FROM custom_orders o JOIN draws d ON d.id=o.target_draw
      WHERE o.status='settled' AND o.pnl IS NOT NULL AND o.hit IN (0,1)
      AND json_extract(o.strategy_json,'$.strategy_type') IN ('algorithm','algorithm_rank')
      AND o.cutoff_draw=o.target_draw-1 AND julianday(o.created)<julianday(d.draw_utc)
      AND julianday(d.draw_utc)<=julianday('now') AND d.verification IN ('full_keno','crosschecked_abc')
      AND NOT EXISTS(SELECT 1 FROM conflicts c WHERE c.draw_id=o.target_draw AND c.resolved=0)
      AND NOT EXISTS(SELECT 1 FROM gap_order_tags g WHERE g.engine='custom' AND g.order_id=o.id)
      ORDER BY o.target_draw,o.id""")
   fingerprint=hashlib.sha256(json.dumps([draws,latest_stored,orders],sort_keys=True).encode()).hexdigest()
   if fingerprint!=self.fingerprint:
    self.cached=[]
    by_algorithm=attributed_orders(orders)
    cutoff=draws[-1]['id'] if draws else None
    for definition in DEFINITIONS:
     records=by_algorithm[definition['id']]
     historical=evaluate(draws,definition,max(HALL_WINDOWS))['records']
     windows={'all':metrics(historical,max(HALL_WINDOWS))}
     for window in HALL_WINDOWS:
      windows[str(window)]={**metrics(historical,window),
                            'actual':order_totals(recent_orders(records,cutoff,window))}
     choice,reason=predict(draws,definition)
     if cutoff!=latest_stored:choice,reason=None,'最新本地开奖尚未核验'
     pending={'target_draw':cutoff+1 if cutoff is not None else None,'cutoff_draw':cutoff,
              'prediction':choice,'reason':reason}
     self.cached.append({**definition,'metrics':windows,'pending':pending,'records':records,
                         'actual':order_totals(records),'history_records':historical})
    self.fingerprint=fingerprint
   return self.cached,self.fingerprint
 def hidden(self):
  return {r['item_id'] for r in self.db.query("SELECT item_id FROM public_visibility WHERE kind='algorithm' AND published=0")}
 def listing(self,user_id,admin=False):
  rows,version=self.snapshot();counts={r['algorithm_id']:r['n'] for r in self.db.query('SELECT algorithm_id,COUNT(*) n FROM algorithm_favorites GROUP BY algorithm_id')};favorites={r['algorithm_id'] for r in self.db.query('SELECT algorithm_id FROM algorithm_favorites WHERE user_id=?',(user_id,))}
  hidden=self.hidden()
  return {'algorithms':[{**{k:v for k,v in r.items() if k not in ('records','history_records')},'published':r['id'] not in hidden,'favorite':r['id'] in favorites,'favorites':counts.get(r['id'],0)} for r in rows if admin or r['id'] not in hidden],'version':version,'latest_id':rows[0]['pending']['cutoff_draw'] if rows else None,'note':'历史算法命中率：逐期开奖前仅使用此前已核验数据回放计算，不要求产生订单；缺期或无有效预测不计入命中率。实际下单收益：仅统计已结算的实时算法跟随模拟订单，历史回测及补单不计入；同一订单可能归属多个算法，收益不可跨算法相加。'}
 def detail(self,algorithm_id,page=1,page_size=20,admin=False,window=None,source='history'):
  if algorithm_id not in BY_ID or (not admin and algorithm_id in self.hidden()):raise ValueError('算法不存在或已下架')
  page=int(page);size=int(page_size)
  if size not in (10,20,50,100):raise ValueError('每页条数须为10、20、50、100')
  if window is not None:
   window=int(window)
   if window not in HALL_WINDOWS:raise ValueError('统计窗口须为20、100、500、1000、2000或5000期')
  rows,version=self.snapshot();r=next(x for x in rows if x['id']==algorithm_id)
  if source not in ('history','orders'):raise ValueError('明细类型无效')
  base=r['history_records'] if source=='history' else r['records']
  selected=recent_orders(base,r['pending']['cutoff_draw'],window) if window is not None else base
  records=list(reversed(selected));total=len(records);pages=max(1,(total+size-1)//size);page=max(1,min(page,pages))
  return {**{k:v for k,v in r.items() if k not in ('records','history_records')},'records':records[(page-1)*size:page*size],'page':page,'pages':pages,'page_size':size,'total':total,'version':version,'source':source}
 def income(self,algorithm_id,interval='1h',admin=False):
  if algorithm_id not in BY_ID or (not admin and algorithm_id in self.hidden()):raise ValueError('算法不存在或已下架')
  from datetime import datetime
  from .history_backtest import income_candles
  rows,_=self.snapshot()
  item=next(r for r in rows if r['id']==algorithm_id)
  records=sorted(item['records'],key=lambda r:(datetime.fromisoformat(r['settled'] or r['time']),r['id']))
  trades=[{'beijing_time':r['settled'] or r['time'],'pnl':r['pnl']} for r in records]
  bars=order_candles(trades,ORDER_CANDLE_SIZES[interval]) if interval in ORDER_CANDLE_SIZES else income_candles(trades,interval)
  return {'id':algorithm_id,'name':item['name'],'interval':interval,'candles':bars,
          'start':trades[0]['beijing_time'] if trades else None,'end':trades[-1]['beijing_time'] if trades else None,
          'orders':len(trades),'net':item['actual']['net']/100}
 def backtest(self,algorithm_id,periods,odds,interval='1h',admin=False,cutoff=None):
  if algorithm_id not in BY_ID or (not admin and algorithm_id in self.hidden()):raise ValueError('算法不存在或已下架')
  try:periods=int(periods)
  except (TypeError,ValueError):raise ValueError('回测期数须为整数') from None
  if not 1<=periods<=MAX_BACKTEST_DRAWS:raise ValueError('回测期数须为 1–50,000')
  if cutoff is not None:
   cutoff=int(cutoff)
   if not 1<=cutoff<2**63:raise ValueError('回测截止期号无效')
  if interval not in ('1h','4h','1d',*ORDER_CANDLE_SIZES):raise ValueError('K线分组无效')
  definition=BY_ID[algorithm_id]
  rows=self.db.query("""SELECT d.id,d.draw_utc,d.a,d.b,d.c,d.total,d.size,d.parity FROM draws d
    WHERE d.id<=? AND d.draw_utc IS NOT NULL AND julianday(d.draw_utc)<=julianday('now')
    AND d.verification IN ('full_keno','crosschecked_abc')
    AND NOT EXISTS(SELECT 1 FROM conflicts c WHERE c.draw_id=d.id AND c.resolved=0)
    ORDER BY d.id DESC LIMIT ?""",(cutoff if cutoff is not None else 2**63-1,periods+definition['lookback']))
  return backtest_algorithm(rows[::-1],definition,periods,odds,interval)
 def favorite(self,user_id,body):
  key=body.get('id');enabled=body.get('favorite')
  if key not in BY_ID or type(enabled)!=bool:raise ValueError('算法或收藏参数无效')
  from .model import utcnow
  with self.db.tx() as c:
   if enabled and c.execute("SELECT 1 FROM public_visibility WHERE kind='algorithm' AND item_id=? AND published=0",(key,)).fetchone():raise ValueError('算法已下架')
   if enabled:c.execute('INSERT OR IGNORE INTO algorithm_favorites VALUES(?,?,?)',(user_id,key,utcnow()))
   else:c.execute('DELETE FROM algorithm_favorites WHERE user_id=? AND algorithm_id=?',(user_id,key))
  return {'ok':True,'favorite':enabled}


class RollingRanking:
 """Incremental prior-draw hit windows; ties use stable algorithm identifiers."""
 def __init__(self,axis,window,hidden=()):
  from collections import deque
  self.window=window;self.last=None;self.observed=0
  self.definitions=[d for d in DEFINITIONS if d['axis']==axis and d['id'] not in hidden]
  self.hits={d['id']:deque() for d in self.definitions}
  self.totals={d['id']:[0,0] for d in self.definitions}
 def update(self,rows):
  for i,row in enumerate(rows):
   target=row['id']
   if self.last is not None and target<=self.last:continue
   if target<rows[-1]['id']-self.window+1:continue
   for d in self.definitions:
    h=rows[max(0,i-d['lookback']):i]
    choice,_=predict(h,d)
    if not h or h[-1]['id']!=target-1:choice=None
    q=self.hits[d['id']]
    hit=None if choice is None else int(choice==row[d['axis']]);q.append((target,hit))
    if hit is not None:self.totals[d['id']][0]+=hit;self.totals[d['id']][1]+=1
   self.last=target;self.observed+=1
  ranked=[]
  cutoff=rows[-1]['id']-self.window
  for key,q in self.hits.items():
   while q and q[0][0]<=cutoff:
    _,hit=q.popleft()
    if hit is not None:self.totals[key][0]-=hit;self.totals[key][1]-=1
   wins,count=self.totals[key]
   if count:ranked.append((key,wins/count,count))
  return sorted(ranked,key=lambda r:(-r[1],r[0]))
