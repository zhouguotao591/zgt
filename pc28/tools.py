"""Local, inspectable draw analytics for the home page and five tools."""
from collections import Counter,defaultdict
from datetime import datetime,timedelta,timezone
from zoneinfo import ZoneInfo
import math,threading,re
from .model import parse_time
BJ=ZoneInfo('Asia/Shanghai')
THEORY=Counter(a+b+c for a in range(10) for b in range(10) for c in range(10))
GROUPS={
 'regular':[('基本',['小','大','单','双']),('组合',['小单','小双','大单','大双']),('极值',['极小','极大']),('特殊',['对子','顺子','豹子','杂六']),('边类',['小边','中','大边']),('龙虎',['龙','虎','合']),('和值号码',[f'{i:02}' for i in range(28)])],
 'balls':[(f'{ball}球数字',[f'{ball}:{i}' for i in range(10)]) for ball in 'ABC']+[(f'{ball}球属性',[f'{ball}:{v}' for v in ['小','大','单','双','小单','小双','大单','大双']]) for ball in 'ABC'],
 'other':[('和值尾数',[f'尾:{i}' for i in range(10)]),('3N余数',[f'3N:{i}' for i in range(3)]),('4N余数',[f'4N:{i}' for i in range(4)]),('5N余数',[f'5N:{i}' for i in range(5)])]
}

def enrich(row,zone):
 r=dict(row);a,b,c=r['a'],r['b'],r['c'];digits=sorted([a,b,c]);n=r['total'];r['combo']=r['size']+r['parity'];r['shape']='豹子' if a==b==c else '对子' if len(set(digits))==2 else '顺子' if digits[2]-digits[0]==2 else '杂六';r['position']='小边' if n<=9 else '大边' if n>=18 else '中';r['dragon']='龙' if a>c else '虎' if a<c else '合';r['extreme']='极小' if n<=5 else '极大' if n>=22 else '—';r['odds_band']='4.75倍档' if r['combo'] in ('大双','小单') else '4.315倍档';r['formula']=f'{a}+{b}+{c}={n}'
 dt=parse_time(r['draw_utc']) if r.get('draw_utc') else parse_time(r['source_time']).replace(tzinfo=BJ if any(h in r.get('source','') for h in ('pc28.world','pc28.work')) else zone)
 r['beijing_time']=dt.astimezone(BJ).strftime('%Y-%m-%d %H:%M:%S');r['date']=r['beijing_time'][:10]
 keys={r['size'],r['parity'],r['combo'],r['shape'],r['position'],r['dragon'],f'{n:02}',f'尾:{n%10}',f'3N:{n%3}',f'4N:{n%4}',f'5N:{n%5}'}
 if r['extreme']!='—':keys.add(r['extreme'])
 for ball,v in zip('ABC',(a,b,c)):
  size='大' if v>=5 else '小';parity='单' if v%2 else '双';keys.update((f'{ball}:{v}',f'{ball}:{size}',f'{ball}:{parity}',f'{ball}:{size+parity}'))
 r['_keys']=keys;return r

def public(r):return {k:v for k,v in r.items() if not k.startswith('_')}

def coverage(rows):
 return {'count':len(rows),'first':rows[0]['id'] if rows else None,'last':rows[-1]['id'] if rows else None,'start':rows[0]['beijing_time'] if rows else None,'end':rows[-1]['beijing_time'] if rows else None,'gaps':sum(b['id']-a['id']-1 for a,b in zip(rows,rows[1:])),'pending_raw':sum(r.get('verification')=='crosschecked_abc' for r in rows)}

def daily_summary(rows):
 days={}
 for row in rows:
  day=row['date'];clock=row['beijing_time'][11:16]
  if day not in days:days[day]={'date':day,'count':0,'大':0,'小':0,'单':0,'双':0,'start':clock,'end':clock}
  item=days[day];item['count']+=1;item[row['size']]+=1;item[row['parity']]+=1
  item['start']=min(item['start'],clock);item['end']=max(item['end'],clock)
 return [days[day] for day in sorted(days,reverse=True)]

def period_summaries(rows):
 """Half-open Beijing calendar buckets, independent of the displayed draw window."""
 groups={key:{} for key in ('hour','four_hour','week')};weeks={}
 for row in rows:
  clock=row['beijing_time'];day=row['date'];hour=int(clock[11:13])
  if day not in weeks:
   date=datetime.strptime(day,'%Y-%m-%d');weeks[day]=(date-timedelta(days=date.weekday())).strftime('%Y-%m-%d')
  keys={'hour':f'{day} {hour:02d}:00:00','four_hour':f'{day} {hour//4*4:02d}:00:00','week':weeks[day]+' 00:00:00'}
  for kind,key in keys.items():
   if key not in groups[kind]:
    start=datetime.strptime(key,'%Y-%m-%d %H:%M:%S').replace(tzinfo=BJ)
    end=start+timedelta(hours={'hour':1,'four_hour':4,'week':168}[kind])
    groups[kind][key]={'start_at':start.isoformat(),'end_at':end.isoformat(),'count':0,'大':0,'小':0,'单':0,'双':0,'first_record':clock,'last_record':clock}
   item=groups[kind][key];item['count']+=1;item[row['size']]+=1;item[row['parity']]+=1
   item['first_record']=min(item['first_record'],clock);item['last_record']=max(item['last_record'],clock)
 return {kind:[items[key] for key in sorted(items,reverse=True)] for kind,items in groups.items()}

def summary_page(items,q,kind,now):
 size=int(q.get(kind+'_size',10))
 if size not in (10,20,50,100):raise ValueError('汇总每页须为10、20、50或100条')
 pages=max(1,math.ceil(len(items)/size));page=max(1,min(pages,int(q.get(kind+'_page',1))))
 rows=[{**r,'ongoing':r['start_at']<=now.isoformat()<r['end_at']} for r in items[(page-1)*size:page*size]]
 return {'rows':rows,'page':page,'pages':pages,'page_size':size,'total':len(items),'as_of':now.isoformat()}

def metrics(rows,group):
 definitions=GROUPS[group];keys=[k for _,ks in definitions for k in ks];last={};seen_last={};counts=Counter();max_miss=Counter();gap_run=Counter();segment_start=0
 for i,r in enumerate(rows):
  if i and r['id']!=rows[i-1]['id']+1:segment_start=i;last={};gap_run=Counter()
  for key in keys:
   if key in r['_keys']:
    counts[key]+=1;last[key]=i;seen_last[key]=i;gap_run[key]=0
   else:gap_run[key]+=1;max_miss[key]=max(max_miss[key],gap_run[key])
 groups=[]
 for title,ks in definitions:
  items=[]
  for k in ks:
   j=last.get(k);seen_j=seen_last.get(k);miss=len(rows)-1-j if j is not None else len(rows)-segment_start
   items.append({'key':k,'label':k.split(':')[-1],'count':counts[k],'rate':counts[k]/len(rows) if rows else None,'current':miss,'at_least':j is None,'max_observed':max_miss[k],'last_draw':rows[seen_j]['id'] if seen_j is not None else None,'last_time':rows[seen_j]['beijing_time'] if seen_j is not None else None})
  groups.append({'title':title,'items':items})
 return groups

def detect_dragons(rows,minimum=5,kind='all'):
 alerts=[];dims=[('size','大小'),('parity','单双'),('combo','组合'),('dragon','龙虎'),('position','边中'),('shape','特殊'),('odds_band','赔率')]
 for field,label in dims:
  if field=='odds_band' and kind not in ('all','odds'):continue
  values=[r[field] for r in rows];N=len(rows)
  for period in range(1,6):
   if kind not in ('all','odds') and kind!=('single' if period==1 else 'jump' if period==2 else 'cycle'):continue
   if kind=='odds' and field!='odds_band':continue
   run=0
   def emit(start,end):
    length=end-start+1
    if length<max(minimum,period*2 if period>1 else minimum):return
    pattern=values[start:start+period]
    if period>1 and len(set(pattern))==1:return
    if any(period%q==0 and all(pattern[j]==pattern[j%q] for j in range(period)) for q in range(1,period)):return
    alerts.append({'kind':'odds' if field=='odds_band' else 'single' if period==1 else 'jump' if period==2 else 'cycle','dimension':label,'length':length,'period':period,'pattern':pattern,'sequence':values[max(start,end-15):end+1],'expected':pattern[length%period],'first':rows[start]['id'],'last':rows[end]['id'],'start_time':rows[start]['beijing_time'],'end_time':rows[end]['beijing_time'],'active':end==N-1})
   if period==1:
    start=0
    for i in range(1,N+1):
     if i==N or values[i]!=values[i-1] or rows[i]['id']!=rows[i-1]['id']+1:
      if i:start0=start;emit(start0,i-1)
      start=i
   else:
    start_segment=0
    for i in range(N):
     if i and rows[i]['id']!=rows[i-1]['id']+1:start_segment=i;run=0
     if i-start_segment>=period and values[i]==values[i-period]:run+=1
     else:
      if run:emit(i-run-period,i-1)
      run=0
     if i==N-1 and run:emit(i-run-period+1,i)
 alerts.sort(key=lambda a:(-a['last'],not a['active'],-a['length'],a['dimension'],a['period']))
 return alerts[:20]

def analyze_number(rows,n):
 N=len(rows);indices=[i for i,r in enumerate(rows) if r['total']==n];hits=len(indices);p=hits/N if N else None;theory=THEORY[n]/1000;intervals=[rows[b]['id']-rows[a]['id'] for a,b in zip(indices,indices[1:]) if rows[b]['id']-rows[a]['id']==b-a]
 before=[];after=[]
 for i in indices:
  if i>0 and rows[i]['id']==rows[i-1]['id']+1:before.append(rows[i-1])
  if i+1<N and rows[i+1]['id']==rows[i]['id']+1:after.append(rows[i+1])
 M=len(after);next_sums=Counter(r['total'] for r in after);prev=Counter(r['total'] for r in before);tails=Counter(r['total']%10 for r in after);mean=N/hits if hits else None
 next_attrs=[{'label':k,'count':sum(k in r['_keys'] for r in after),'rate':sum(k in r['_keys'] for r in after)/M if M else None} for k in ['大','小','单','双','大单','大双','小单','小双']]
 abc=[{'ball':k.upper(),'items':[{'number':v,'count':sum(r[k]==v for r in after),'rate':sum(r[k]==v for r in after)/M if M else None} for v in range(10)]} for k in 'abc']
 recovery=[];threshold=max(1,math.ceil(mean)) if mean else 0
 for horizon in (5,10,20):
  eligible=success=0
  if threshold:
   for i in indices:
    trigger=i+threshold
    if trigger>=N or rows[trigger]['id']-rows[i]['id']!=threshold or any(rows[j]['total']==n for j in range(i+1,trigger+1)):continue
    observed=[]
    for j in range(trigger+1,min(N,trigger+horizon+1)):
     if rows[j]['id']!=rows[j-1]['id']+1:break
     observed.append(rows[j]['total'])
     if rows[j]['total']==n:break
    hit=n in observed
    if hit or len(observed)==horizon:eligible+=1;success+=hit
  recovery.append({'horizon':horizon,'eligible':eligible,'success':success,'rate':success/eligible if eligible else None})
 curve=[];seen=0;step=max(1,N//180)
 for i,r in enumerate(rows):
  seen+=r['total']==n
  if i%step==0 or i==N-1:curve.append({'draw':r['id'],'observed':seen,'expected':(i+1)*theory,'difference':seen-(i+1)*theory})
 def top(c):return [{'number':v,'count':count} for v,count in c.most_common(10)]
 deviation=sorted([{'number':v,'count':next_sums[v],'rate':next_sums[v]/M if M else None,'reference':THEORY[v]/1000,'difference':next_sums[v]/M-THEORY[v]/1000 if M else None} for v in range(28)],key=lambda r:abs(r['difference'] or 0),reverse=True)[:10]
 segment_start=max([i for i in range(1,N) if rows[i]['id']!=rows[i-1]['id']+1],default=0)
 last_index=indices[-1] if indices else -1;miss=N-1-last_index if last_index>=segment_start else N-segment_start
 return {'number':n,'hits':hits,'rate':p,'theory':theory,'average_interval':mean,'current_omission':miss,'current_omission_at_least':last_index<segment_start,'min_interval':min(intervals) if intervals else None,'max_interval':max(intervals) if intervals else None,'successor_count':M,'repeat_rate':next_sums[n]/M if M else None,'same_tail_rate':sum(r['total']%10==n%10 for r in after)/M if M else None,'neighbor_rate':sum(abs(r['total']-n)==1 for r in after)/M if M else None,'next_attributes':next_attrs,'abc':abc,'recovery_threshold':threshold,'recovery':recovery,'before':top(prev),'after':top(next_sums),'tails':top(tails),'deviation':deviation,'curve':curve,'note':'频率分母为所选窗口；后续统计仅使用有相邻下一期的命中，末期无后续不计。参考分布按三球独立且0—9均匀计算，不代表已验证Keno换算的精确理论分布，也不是未来预测。回补分母排除未观察完整时长且尚未命中的尾段。'}

class Tools:
 def __init__(self,service):self.service=service;self.lock=threading.RLock();self.key=None;self.rows=[];self.daily=[];self.periods=None;self.cache={}
 def snapshot(self):
  db=self.service.db;stat=db.query("SELECT COUNT(*) n,MAX(id) last,SUM(verification='crosschecked_abc') pending FROM draws")[0];rev=db.query('SELECT MAX(id) id FROM revisions')[0]['id'];zone=self.service.cfg['collector'].get('source_timezone') or 'America/Vancouver';key=(stat['n'],stat['last'],stat['pending'],rev,zone)
  with self.lock:
   if key!=self.key:
    raw=db.query('SELECT id,source_time,draw_utc,a,b,c,total,size,parity,numbers,verification,source FROM draws ORDER BY id');self.rows=[enrich(r,ZoneInfo(zone)) for r in raw];self.daily=daily_summary(self.rows);self.periods=None;self.key=key;self.cache={}
   return self.rows
 def run(self,q):
  view=q.get('view','home');limit=int(q.get('limit',40 if view=='trend' else 500));page=int(q.get('page',1));group=q.get('group','regular')
  if not 0<=limit<=200000 or not 1<=page<=100000 or group not in GROUPS:raise ValueError('窗口、页码或分组参数错误')
  if view not in ('home','trend','dragon','missing','stats','analysis'):raise ValueError('未知工具')
  if view=='trend' and not 1<=limit<=20000:raise ValueError('走势统计期数须为1—20000期')
  if view=='trend':
   zone=ZoneInfo(self.service.cfg['collector'].get('source_timezone') or 'America/Vancouver')
   raw=self.service.db.query('SELECT id,source_time,draw_utc,a,b,c,total,size,parity,numbers,verification,source FROM draws ORDER BY id DESC LIMIT ?',(limit,))
   allrows=[enrich(r,zone) for r in reversed(raw)]
  else:
   with self.lock:
    allrows=self.snapshot();daily=self.daily
    if view=='home':
     if self.periods is None:self.periods=period_summaries(allrows)
     periods=self.periods
  rows=allrows[-limit:] if limit else allrows
  if view=='home' and q.get('date'):
   selected=datetime.strptime(q['date'],'%Y-%m-%d').date().isoformat()
   rows=[r for r in allrows if r['date']==selected]
  if view=='stats':
   start=q.get('start') or datetime.now(BJ).date().isoformat();end=q.get('end') or start
   datetime.strptime(start,'%Y-%m-%d');datetime.strptime(end,'%Y-%m-%d')
   if start>end:raise ValueError('开始日期不能晚于结束日期')
   rows=[r for r in allrows if start<=r['date']<=end]
  meta=coverage(rows);base={'view':view,'coverage':meta,'last_updated_draw':allrows[-1]['id'] if allrows else None,'timezone':'Asia/Shanghai'}
  if view in ('home','trend'):
   term=q.get('q','').replace(' ','')
   filtered=[r for r in reversed(rows) if not term or (term.isdigit() and len(term)<=2 and int(term)==r['total']) or term in (str(r['id']),r['formula'],r['formula'].split('=')[0])];size=20 if view=='home' else 40
   base.update(rows=[public(r) for r in filtered[(page-1)*size:page*size]],total=len(filtered),page=page,pages=max(1,math.ceil(len(filtered)/size)))
   if view=='home':
    day_size=int(q.get('daily_size',10))
    if day_size not in (10,20,50,100):raise ValueError('每日汇总每页须为10、20、50或100条')
    pages=max(1,math.ceil(len(daily)/day_size));day_page=max(1,min(pages,int(q.get('daily_page',1))))
    base['daily']={'rows':daily[(day_page-1)*day_size:day_page*day_size],'page':day_page,'pages':pages,'page_size':day_size,
                   'total':len(daily),'today':datetime.now(BJ).date().isoformat()}
    now=datetime.now(BJ);base['summaries']={kind:summary_page(items,q,kind,now) for kind,items in periods.items()}
   if view=='trend':
    base['current']=metrics(rows,'regular');base['series']=[{k:r[k] for k in ('id','size','parity','combo','dragon')} for r in rows]
   return base
  if view in ('missing','stats'):
   key=(view,limit,group,q.get('start'),q.get('end'),meta['first'],meta['last'])
   with self.lock:
    if key not in self.cache:self.cache[key]=metrics(rows,group)
    return {**base,'groups':self.cache[key]}
  if view=='dragon':
   minimum=int(q.get('minimum',5));kind=q.get('kind','all')
   if not 3<=minimum<=50 or kind not in ('all','single','jump','cycle','odds'):raise ValueError('长龙参数错误')
   alerts=detect_dragons(rows,minimum,kind);return {**base,'alerts':alerts,'active':sum(a['active'] for a in alerts),'minimum':minimum,'note':'结构延续值只描述既有周期，不代表下一期预测。缺期会中断连期与周期识别；按结束期号由近到远排列，最多显示最近20条。赔率维度按组合4.75/4.315倍档分类。'}
  number=int(q.get('number',13))
  if not 0<=number<=27:raise ValueError('和值范围0—27')
  return {**base,'analysis':analyze_number(rows,number)}
