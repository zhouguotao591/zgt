import sqlite3,json,hashlib
from pathlib import Path
from contextlib import contextmanager
from .model import convert,utcnow,parse_time,strict_int
def source_datetime(value,source,source_timezone=None):
 from datetime import timezone
 from zoneinfo import ZoneInfo
 from urllib.parse import urlsplit
 dt=parse_time(value)
 if dt.tzinfo is None and source_timezone:
  zone=ZoneInfo(source_timezone)
  if source_timezone=='America/Vancouver' and (urlsplit(source).hostname or '') in ('playnow.com','www.playnow.com'):
   from .maintenance import bc_zone
   zone=bc_zone(dt.replace(tzinfo=timezone.utc))
  dt=dt.replace(tzinfo=zone)
 return dt

SCHEMA='''
CREATE TABLE IF NOT EXISTS draws(id INTEGER PRIMARY KEY,source_time TEXT NOT NULL,draw_utc TEXT,numbers TEXT NOT NULL,a INTEGER,b INTEGER,c INTEGER,total INTEGER,size TEXT,parity TEXT,conversion_version TEXT,first_seen TEXT NOT NULL,source TEXT NOT NULL,raw_hash TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS runtime_state(key TEXT PRIMARY KEY,value TEXT NOT NULL,updated TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS source_observations(id INTEGER PRIMARY KEY,draw_id INTEGER,source TEXT,source_time TEXT,draw_utc TEXT,raw_hash TEXT,first_seen TEXT,UNIQUE(draw_id,source,source_time,raw_hash));
CREATE INDEX IF NOT EXISTS ix_draws_time ON draws(source_time);
CREATE TABLE IF NOT EXISTS jobs(id INTEGER PRIMARY KEY,started TEXT,finished TEXT,kind TEXT,status TEXT,details TEXT);
CREATE TABLE IF NOT EXISTS conflicts(id INTEGER PRIMARY KEY,draw_id INTEGER,received TEXT,old_record TEXT,new_record TEXT,resolved INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS evaluations(id INTEGER PRIMARY KEY,created TEXT,as_of TEXT,status TEXT,parameters TEXT,result TEXT);
CREATE TABLE IF NOT EXISTS selections(month TEXT PRIMARY KEY,created TEXT,run_id INTEGER,candidates TEXT,reason TEXT);
CREATE TABLE IF NOT EXISTS accounts(id INTEGER PRIMARY KEY CHECK(id=1),balance INTEGER NOT NULL,initial_balance INTEGER NOT NULL,peak INTEGER NOT NULL,paused INTEGER NOT NULL DEFAULT 0,pause_reason TEXT DEFAULT '',state TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS rounds(id INTEGER PRIMARY KEY,created TEXT,ended TEXT,status TEXT,strategy_json TEXT,max_bets INTEGER,orders_count INTEGER DEFAULT 0,settled_count INTEGER DEFAULT 0,end_reason TEXT,first_draw INTEGER,last_draw INTEGER);
CREATE TABLE IF NOT EXISTS orders(id INTEGER PRIMARY KEY,target_draw INTEGER UNIQUE NOT NULL,created TEXT NOT NULL,cutoff_draw INTEGER NOT NULL,strategy_key TEXT NOT NULL,strategy_json TEXT NOT NULL,selection_month TEXT,stake INTEGER NOT NULL,status TEXT NOT NULL,pnl INTEGER,total INTEGER,settled TEXT,reason TEXT);
CREATE TABLE IF NOT EXISTS ledger(id INTEGER PRIMARY KEY,created TEXT,order_id INTEGER,kind TEXT,amount INTEGER,balance INTEGER,UNIQUE(order_id,kind));
CREATE TABLE IF NOT EXISTS decisions(target_draw INTEGER PRIMARY KEY,created TEXT,cutoff_draw INTEGER,action TEXT,reason TEXT,strategy_key TEXT);
CREATE TABLE IF NOT EXISTS switches(id INTEGER PRIMARY KEY,created TEXT,target_draw INTEGER,old_key TEXT,new_key TEXT,reason TEXT);
CREATE TABLE IF NOT EXISTS revisions(id INTEGER PRIMARY KEY,created TEXT,draw_id INTEGER,conflict_id INTEGER,choice TEXT,old_record TEXT,new_record TEXT);
CREATE TABLE IF NOT EXISTS settings_audit(id INTEGER PRIMARY KEY,created TEXT,config TEXT);
PRAGMA user_version=1;
'''
class DB:
 def __init__(self,path):self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True);self.init()
 def connect(self):
  c=sqlite3.connect(self.path,timeout=30);c.row_factory=sqlite3.Row;c.execute('PRAGMA busy_timeout=30000');c.execute('PRAGMA foreign_keys=ON');return c
 @contextmanager
 def tx(self):
  c=self.connect()
  try:c.execute('BEGIN IMMEDIATE');yield c;c.commit()
  except BaseException:c.rollback();raise
  finally:c.close()
 def init(self):
  c=self.connect();c.execute('PRAGMA journal_mode=WAL');c.executescript(SCHEMA)
  columns={r[1] for r in c.execute('PRAGMA table_info(draws)')}
  for name,definition in [('verification',"TEXT NOT NULL DEFAULT 'full_keno'"),('result_sources',"TEXT NOT NULL DEFAULT '[]'"),('raw_completed_at','TEXT')]:
   if name not in columns:c.execute('ALTER TABLE draws ADD COLUMN '+name+' '+definition)
  order_columns={r[1] for r in c.execute('PRAGMA table_info(orders)')}
  for name in ('round_id','round_attempt'):
   if name not in order_columns:c.execute('ALTER TABLE orders ADD COLUMN '+name+' INTEGER')
  self.migrate_rounds(c)
  c.execute('CREATE UNIQUE INDEX IF NOT EXISTS uq_order_round_slot ON orders(round_id,round_attempt)')
  c.commit();c.close()
 def migrate_rounds(self,c):
  if c.execute('SELECT COUNT(*) FROM rounds').fetchone()[0] or not c.execute('SELECT 1 FROM orders WHERE round_id IS NULL LIMIT 1').fetchone():return
  current=None;last=None;count=losses=0
  for o in c.execute('SELECT * FROM orders ORDER BY id').fetchall():
   strategy=json.loads(o['strategy_json'])
   if current is None or (last and (o['strategy_key']!=last['strategy_key'] or o['target_draw']!=last['target_draw']+1)):
    if current is not None:c.execute("UPDATE rounds SET status='ended',ended=?,end_reason='历史订单间断或策略变更' WHERE id=?",(last['settled'] or last['created'],current))
    current=c.execute("INSERT INTO rounds(created,status,strategy_json,max_bets,first_draw) VALUES(?,'active',?,?,?)",(o['created'],o['strategy_json'],strategy['max_bets'],o['target_draw'])).lastrowid;count=losses=0
   count+=1;c.execute('UPDATE orders SET round_id=?,round_attempt=? WHERE id=?',(current,count,o['id']))
   done=o['status']=='settled'
   if done:losses=losses+1 if o['pnl']<0 else 0
   c.execute('UPDATE rounds SET orders_count=?,settled_count=settled_count+?,last_draw=? WHERE id=?',(count,int(done),o['target_draw'],current))
   if done and (count>=strategy['max_bets'] or losses>=strategy['max_losses']):
    why='连续亏损达到上限' if losses>=strategy['max_losses'] else '每轮下单数达到上限'
    c.execute("UPDATE rounds SET status='ended',ended=?,end_reason=? WHERE id=?",(o['settled'],why,current));current=None
   last=o
  account=c.execute('SELECT state FROM accounts WHERE id=1').fetchone()
  if account and current is not None:
   state=json.loads(account['state']);state['round_id']=current;c.execute('UPDATE accounts SET state=? WHERE id=1',(json.dumps(state,ensure_ascii=False),))
 def query(self,sql,args=()):
  c=self.connect()
  try:return [dict(x) for x in c.execute(sql,args)]
  finally:c.close()
 def rows(self):return self.query('SELECT * FROM draws ORDER BY id')
 def tail(self,n=200):return list(reversed(self.query('SELECT * FROM draws ORDER BY id DESC LIMIT ?',(n,))))
 def import_records(self,records,source,received=None,source_timezone=None):
  from zoneinfo import ZoneInfo
  received=received or utcnow();new=duplicate=conflicts=upgraded=0
  with self.tx() as c:
   for row in records:
    did=strict_int(row['id']);numbers=row['numbers'];partial=numbers is None;sources=row.get('result_sources',[])
    if partial:
     from urllib.parse import urlsplit
     hosts={(urlsplit(u).hostname or '').removeprefix('www.') for u in sources}
     if row.get('verification')!='crosschecked_abc' or not {'pc28.world','pc28.work'}<=hosts:raise ValueError('三球结果必须经过指定双源核对')
     digits=[strict_int(row[k]) for k in ('a','b','c')];total=strict_int(row['total'])
     if any(not 0<=v<=9 for v in digits) or sum(digits)!=total:raise ValueError('三球结果不合法')
     v=dict(a=digits[0],b=digits[1],c=digits[2],total=total,size='大' if total>=14 else '小',parity='单' if total%2 else '双',conversion_version='crosschecked-abc-v1')
    else:v=convert(numbers)
    if did<=0:raise ValueError('期号必须大于0')
    dt=source_datetime(row['time'],source,source_timezone);ut=None
    if dt.tzinfo is not None:ut=dt.astimezone(__import__('datetime').timezone.utc).isoformat()
    elif source_timezone:ut=dt.replace(tzinfo=ZoneInfo(source_timezone)).astimezone(__import__('datetime').timezone.utc).isoformat()
    local=dt.replace(tzinfo=None).isoformat(timespec='seconds');encoded=json.dumps(numbers,separators=(',',':'));sha=hashlib.sha256((json.dumps([v['a'],v['b'],v['c']]) if partial else encoded).encode()).hexdigest()
    c.execute('INSERT OR IGNORE INTO source_observations(draw_id,source,source_time,draw_utc,raw_hash,first_seen) VALUES(?,?,?,?,?,?)',(did,source,local,ut,sha,received))
    old=c.execute('SELECT * FROM draws WHERE id=?',(did,)).fetchone()
    if old:
     if partial or old['verification']=='crosschecked_abc':
      same=all(old[k]==v[k] for k in ('a','b','c','total'))
      if same:
       duplicate+=1
       if not partial and old['verification']=='crosschecked_abc':
        c.execute("UPDATE draws SET numbers=?,raw_hash=?,verification='full_keno',conversion_version=?,raw_completed_at=? WHERE id=?",(encoded,sha,v['conversion_version'],received,did));upgraded+=1
       if ut and not old['draw_utc']:c.execute('UPDATE draws SET draw_utc=? WHERE id=?',(ut,did))
       continue
      old_json=json.dumps(dict(old),ensure_ascii=False);new_json=json.dumps(row,ensure_ascii=False)
      if not c.execute('SELECT 1 FROM conflicts WHERE draw_id=? AND new_record=?',(did,new_json)).fetchone():
       c.execute('INSERT INTO conflicts(draw_id,received,old_record,new_record) VALUES(?,?,?,?)',(did,received,old_json,new_json));conflicts+=1
      c.execute("UPDATE accounts SET paused=1,pause_reason='三球结果与后续原始号码冲突，请人工核验' WHERE id=1")
      continue
     old_secondary=any(host in old['source'] for host in ('pc28.world','pc28.work'));new_secondary=any(host in source for host in ('pc28.world','pc28.work'))
     cross_source=old['source']!=source and (old_secondary or new_secondary)
     same_instant=bool(ut and old['draw_utc'] and parse_time(ut)==parse_time(old['draw_utc']))
     if old['raw_hash']==sha and old['source_time']!=local and not cross_source and same_instant:
      c.execute('UPDATE draws SET source_time=? WHERE id=?',(local,did))
      c.execute('INSERT INTO revisions(created,draw_id,conflict_id,choice,old_record,new_record) VALUES(?,?,NULL,?,?,?)',(utcnow(),did,'time_normalize',json.dumps(dict(old),ensure_ascii=False),json.dumps({'record':row,'reason':'完整号码与UTC时刻一致，仅规范来源时区显示；保留UTC、号码和订单'},ensure_ascii=False)))
      duplicate+=1;continue
     if old['raw_hash']!=sha or (old['source_time']!=local and not cross_source):
      old_json=json.dumps(dict(old),ensure_ascii=False);new_json=json.dumps(row,ensure_ascii=False)
      if not c.execute('SELECT 1 FROM conflicts WHERE draw_id=? AND old_record=? AND new_record=?',(did,old_json,new_json)).fetchone():
       c.execute('INSERT INTO conflicts(draw_id,received,old_record,new_record) VALUES(?,?,?,?)',(did,received,old_json,new_json));conflicts+=1
      else:duplicate+=1
     else:
      duplicate+=1
      if ut and not old['draw_utc']:c.execute('UPDATE draws SET draw_utc=? WHERE id=?',(ut,did))
      if cross_source and old_secondary and not new_secondary:
       c.execute('UPDATE draws SET source_time=?,source=? WHERE id=?',(local,source,did))
     continue
    c.execute('INSERT INTO draws(id,source_time,draw_utc,numbers,a,b,c,total,size,parity,conversion_version,first_seen,source,raw_hash,verification,result_sources,raw_completed_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(did,local,ut,encoded,v['a'],v['b'],v['c'],v['total'],v['size'],v['parity'],v['conversion_version'],received,source,sha,'crosschecked_abc' if partial else 'full_keno',json.dumps(sources),None if partial else received));new+=1
  return dict(inserted=new,duplicates=duplicate,conflicts=conflicts,upgraded=upgraded)
 def gaps(self,limit=100):
  return self.query('SELECT id+1 AS first_missing,next_id-1 AS last_missing,next_id-id-1 AS count FROM (SELECT id,LEAD(id) OVER(ORDER BY id) next_id FROM draws) WHERE next_id>id+1 LIMIT ?',(limit,))
 def log_job(self,kind,status,details,started=None):
  with self.tx() as c:c.execute('INSERT INTO jobs(started,finished,kind,status,details) VALUES(?,?,?,?,?)',(started or utcnow(),utcnow(),kind,status,json.dumps(details,ensure_ascii=False)))
 def backup(self,dest):
  d=sqlite3.connect(dest);c=self.connect()
  try:c.backup(d)
  finally:c.close();d.close()

 def reconcile_time_conflicts(self):
  from urllib.parse import urlsplit
  repaired=[]
  with self.tx() as c:
   for conflict in c.execute('SELECT * FROM conflicts WHERE resolved=0').fetchall():
    old=c.execute('SELECT * FROM draws WHERE id=?',(conflict['draw_id'],)).fetchone();incoming=json.loads(conflict['new_record'])
    if not old or old['verification']!='full_keno' or (urlsplit(old['source']).hostname or '') not in ('playnow.com','www.playnow.com'):continue
    if incoming.get('numbers')!=json.loads(old['numbers']):continue
    dt=source_datetime(incoming['time'],old['source'],'America/Vancouver')
    if not dt.tzinfo or not old['draw_utc'] or dt!=parse_time(old['draw_utc']):continue
    local=dt.replace(tzinfo=None).isoformat(timespec='seconds')
    c.execute('UPDATE draws SET source_time=? WHERE id=?',(local,old['id']))
    c.execute('UPDATE conflicts SET resolved=1 WHERE id=?',(conflict['id'],))
    c.execute('INSERT INTO revisions(created,draw_id,conflict_id,choice,old_record,new_record) VALUES(?,?,?,?,?,?)',(utcnow(),old['id'],conflict['id'],'time_normalize',json.dumps(dict(old),ensure_ascii=False),json.dumps({'record':incoming,'reason':'已核对完整20码及BC永久UTC-7时间，UTC时刻一致，仅来源时钟差异；不修改UTC、号码、账户或订单'},ensure_ascii=False)))
    repaired.append(old['id'])
  return repaired

 def resolve_conflict(self,conflict_id,choice,reason):
  if choice not in ('keep','replace') or not reason.strip():raise ValueError('请选择保留/替换并填写核对依据')
  with self.tx() as c:
   conflict=c.execute('SELECT * FROM conflicts WHERE id=? AND resolved=0',(conflict_id,)).fetchone()
   if not conflict:raise ValueError('冲突不存在或已处理')
   old=dict(c.execute('SELECT * FROM draws WHERE id=?',(conflict['draw_id'],)).fetchone());incoming=json.loads(conflict['new_record'])
   if choice=='replace':
    v=convert(incoming['numbers']);dt=parse_time(incoming['time']);ut=dt.astimezone(__import__('datetime').timezone.utc).isoformat() if dt.tzinfo else None;encoded=json.dumps(incoming['numbers'],separators=(',',':'));sha=hashlib.sha256(encoded.encode()).hexdigest()
    c.execute('UPDATE draws SET source_time=?,draw_utc=?,numbers=?,a=?,b=?,c=?,total=?,size=?,parity=?,conversion_version=?,raw_hash=?,verification=?,raw_completed_at=? WHERE id=?',(dt.replace(tzinfo=None).isoformat(timespec='seconds'),ut,encoded,v['a'],v['b'],v['c'],v['total'],v['size'],v['parity'],v['conversion_version'],sha,'full_keno',utcnow(),old['id']))
    c.execute("UPDATE accounts SET paused=1,pause_reason='历史数据已修正；请核对已有订单和重新评估策略' WHERE id=1")
    c.execute("UPDATE evaluations SET status='invalidated_by_correction'")
    c.execute("UPDATE selections SET candidates='[]',reason='开奖数据修正，旧候选失效；请下月或新版本重新评估'")
   c.execute('UPDATE conflicts SET resolved=1 WHERE id=?',(conflict_id,))
   c.execute('INSERT INTO revisions(created,draw_id,conflict_id,choice,old_record,new_record) VALUES(?,?,?,?,?,?)',(utcnow(),old['id'],conflict_id,choice,json.dumps(old,ensure_ascii=False),json.dumps({'record':incoming,'reason':reason},ensure_ascii=False)))
  return {'ok':True,'choice':choice,'note':'既有模拟订单保留原结算，不自动改写账本；替换数据后账户已暂停' if choice=='replace' else '保留现有数据，已记录核对依据'}
