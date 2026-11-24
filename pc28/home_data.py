"""Bounded home reads and persistent, incrementally repaired calendar summaries."""
import json,math,threading,time
from contextlib import closing
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
from .model import parse_time
from .tools import BJ,enrich,public,summary_page
COLUMNS='id,source_time,draw_utc,a,b,c,total,size,parity,numbers,verification,source'

def clock(row,zone):
 dt=parse_time(row['draw_utc']) if row.get('draw_utc') else parse_time(row['source_time']).replace(tzinfo=BJ if any(h in row.get('source','') for h in ('pc28.world','pc28.work')) else ZoneInfo(zone))
 return dt.astimezone(BJ).strftime('%Y-%m-%d %H:%M:%S')

class HomeData:
 def __init__(self,service):self.service=service;self.lock=threading.Lock();self.ready=False;self.running=False;self.error=None
 def records(self,q):
  limit=int(q.get('limit',500));page=int(q.get('page',1));term=q.get('q','').replace(' ','');date=q.get('date','');zone=self.service.cfg['collector'].get('source_timezone') or 'America/Vancouver'
  if not 0<=limit<=200000 or not 1<=page<=100000:raise ValueError('窗口、页码参数错误')
  if date:datetime.strptime(date,'%Y-%m-%d')
  c=self.service.db.connect()
  try:
   c.execute('BEGIN');c.create_function('bj_clock',3,lambda utc,src,source:clock({'draw_utc':utc,'source_time':src,'source':source},zone))
   # Limit is applied before searching, exactly as in the historical home window.
   source='SELECT '+COLUMNS+' FROM draws ORDER BY id DESC'+(' LIMIT '+str(limit) if limit and not date else '')
   params=[]
   if date:source='SELECT '+COLUMNS+' FROM draws WHERE substr(bj_clock(draw_utc,source_time,source),1,10)=? ORDER BY id DESC';params=[date]
   if date and self.ready:
    source='SELECT '+COLUMNS+' FROM draws WHERE id IN (SELECT id FROM home_cache_rows WHERE day=? UNION SELECT id FROM home_cache_dirty) AND substr(bj_clock(draw_utc,source_time,source),1,10)=? ORDER BY id DESC';params=[date,date]
   prefix='WITH window AS ('+source+') '
   meta=dict(c.execute(prefix+"SELECT COUNT(*) count,MIN(id) first,MAX(id) last,SUM(verification='crosschecked_abc') pending_raw FROM window",params).fetchone());meta['pending_raw']=meta['pending_raw'] or 0;meta['gaps']=max(0,(meta['last'] or 0)-(meta['first'] or 0)+1-meta['count']) if meta['count'] else 0
   for key,label in [('first','start'),('last','end')]:
    r=c.execute('SELECT '+COLUMNS+' FROM draws WHERE id=?',(meta[key],)).fetchone();meta[label]=clock(dict(r),zone) if r else None
   where='';search=[]
   if term:
    formula="CAST(a AS TEXT)||'+'||b||'+'||c";where=' WHERE (CAST(id AS TEXT)=? OR '+formula+"=? OR "+formula+"||'='||total=?";search=[term]*3
    if term.isdigit() and len(term)<=2:where+=' OR total=?';search.append(int(term))
    where+=')'
   total=c.execute(prefix+'SELECT COUNT(*) FROM window'+where,params+search).fetchone()[0]
   raw=c.execute(prefix+'SELECT * FROM window'+where+' ORDER BY id DESC LIMIT 20 OFFSET ?',params+search+[(page-1)*20]).fetchall();latest=c.execute('SELECT MAX(id) FROM draws').fetchone()[0]
   return {'view':'home','coverage':meta,'last_updated_draw':latest,'timezone':'Asia/Shanghai','rows':[public(enrich(dict(r),ZoneInfo(zone))) for r in raw],'total':total,'page':page,'pages':max(1,math.ceil(total/20))}
  finally:c.close()
 def initialize(self):
  zone=self.service.cfg['collector'].get('source_timezone') or 'America/Vancouver'
  if self.ready and getattr(self,'zone',None)==zone:return
  with self.service.db.tx() as c:
   c.execute('CREATE TABLE IF NOT EXISTS home_cache_meta(key TEXT PRIMARY KEY,value TEXT)')
   c.execute('CREATE TABLE IF NOT EXISTS home_cache_dirty(id INTEGER PRIMARY KEY)')
   c.execute('CREATE TABLE IF NOT EXISTS home_cache_rows(id INTEGER PRIMARY KEY,clock TEXT,day TEXT,hour TEXT,four_hour TEXT,week TEXT,size TEXT,parity TEXT)')
   c.execute('CREATE TABLE IF NOT EXISTS home_cache_buckets(kind TEXT,key TEXT,payload TEXT,PRIMARY KEY(kind,key))')
   for kind in ('day','hour','four_hour','week'):c.execute('CREATE INDEX IF NOT EXISTS home_cache_'+kind+' ON home_cache_rows('+kind+',clock)')
   for event,ref in [('INSERT','NEW'),('UPDATE','NEW'),('DELETE','OLD')]:
    c.execute('CREATE TRIGGER IF NOT EXISTS home_dirty_'+event.lower()+' AFTER '+event+' ON draws BEGIN INSERT OR IGNORE INTO home_cache_dirty VALUES('+ref+'.id); END')
   old=c.execute("SELECT value FROM home_cache_meta WHERE key='zone'").fetchone()
   if not old or old[0]!=zone:
    c.execute('DELETE FROM home_cache_rows');c.execute('DELETE FROM home_cache_buckets');c.execute('INSERT OR IGNORE INTO home_cache_dirty SELECT id FROM draws');c.execute("INSERT OR REPLACE INTO home_cache_meta VALUES('zone',?)",(zone,))
  self.ready=True;self.zone=zone
 def refresh(self):
  zone=self.service.cfg['collector'].get('source_timezone') or 'America/Vancouver'
  while True:
   with self.service.db.tx() as c:
    ids=[r[0] for r in c.execute('SELECT id FROM home_cache_dirty LIMIT 2000')]
    if not ids:return
    marks=','.join('?' for _ in ids);touched=set()
    for old in c.execute('SELECT * FROM home_cache_rows WHERE id IN ('+marks+')',ids):
     for kind in ('day','hour','four_hour','week'):touched.add((kind,old[kind]))
    raw=[dict(r) for r in c.execute('SELECT '+COLUMNS+' FROM draws WHERE id IN ('+marks+')',ids)]
    c.execute('DELETE FROM home_cache_rows WHERE id IN ('+marks+')',ids)
    values=[]
    for r in raw:
     stamp=clock(r,zone);dt=datetime.fromisoformat(stamp).replace(tzinfo=BJ);hour=dt.replace(minute=0,second=0);four=hour.replace(hour=hour.hour//4*4);week=dt.replace(hour=0,minute=0,second=0)-timedelta(days=dt.weekday());keys=[stamp[:10],hour.isoformat(),four.isoformat(),week.isoformat()]
     values.append((r['id'],stamp,*keys,r['size'],r['parity']))
     for kind,key in zip(('day','hour','four_hour','week'),keys):touched.add((kind,key))
    c.executemany('INSERT INTO home_cache_rows VALUES(?,?,?,?,?,?,?,?)',values)
    for kind,key in touched:
     agg=dict(c.execute("SELECT COUNT(*) count,SUM(size='大') big,SUM(size='小') small,SUM(parity='单') odd,SUM(parity='双') even,MIN(clock) first,MAX(clock) last FROM home_cache_rows WHERE "+kind+'=?',(key,)).fetchone())
     if not agg['count']:c.execute('DELETE FROM home_cache_buckets WHERE kind=? AND key=?',(kind,key));continue
     item={'count':agg['count'],'大':agg['big'],'小':agg['small'],'单':agg['odd'],'双':agg['even']}
     if kind=='day':item.update(date=key,start=agg['first'][11:16],end=agg['last'][11:16])
     else:item.update(start_at=key,end_at=(datetime.fromisoformat(key)+timedelta(hours={'hour':1,'four_hour':4,'week':168}[kind])).isoformat(),first_record=agg['first'],last_record=agg['last'])
     c.execute('INSERT OR REPLACE INTO home_cache_buckets VALUES(?,?,?)',(kind,key,json.dumps(item,ensure_ascii=False)))
    c.execute('DELETE FROM home_cache_dirty WHERE id IN ('+marks+')',ids)
   time.sleep(.005)
 def worker(self):
  try:self.initialize();self.refresh();self.error=None
  except Exception:self.error='汇总暂未完成，请稍后重试'
  finally:
   with self.lock:self.running=False
 def summaries(self,q,wait=False):
  with self.lock:
   if not self.running:self.running=True;threading.Thread(target=self.worker,daemon=True).start()
  if wait:
   while self.running:time.sleep(.02)
  if not self.ready:return {'loading':True,'message':self.error or '正在建立历史汇总，开奖记录可先查看'}
  with closing(self.service.db.connect()) as c:
   c.execute('BEGIN')
   if c.execute('SELECT 1 FROM home_cache_dirty LIMIT 1').fetchone():return {'loading':True,'message':self.error or '正在更新历史汇总'}
   now=datetime.now(BJ)
   def page_for(kind):
    label='daily' if kind=='day' else kind;size=int(q.get(label+'_size',10))
    if size not in (10,20,50,100):raise ValueError('汇总每页须为10、20、50或100条')
    total=c.execute('SELECT COUNT(*) FROM home_cache_buckets WHERE kind=?',(kind,)).fetchone()[0];pages=max(1,math.ceil(total/size));page=max(1,min(pages,int(q.get(label+'_page',1))))
    rows=[json.loads(r[0]) for r in c.execute('SELECT payload FROM home_cache_buckets WHERE kind=? ORDER BY key DESC LIMIT ? OFFSET ?',(kind,size,(page-1)*size))]
    if kind!='day':
     for r in rows:r['ongoing']=r['start_at']<=now.isoformat()<r['end_at']
    return {'rows':rows,'page':page,'pages':pages,'page_size':size,'total':total,**({'today':now.date().isoformat()} if kind=='day' else {'as_of':now.isoformat()})}
   return {'loading':False,'daily':page_for('day'),'summaries':{kind:page_for(kind) for kind in ('hour','four_hour','week')}}
