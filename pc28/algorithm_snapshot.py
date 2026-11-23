"""Durable, revision-driven presentation snapshots. Never used for strategy execution."""
import hashlib,json,logging,threading,time,zlib

class HallSnapshots:
 def __init__(self,hall):
  self.hall=hall;self.db=hall.db;self.lock=threading.RLock();self.bundle=None;self.wake=threading.Event();self.started=False;self.error=False
  from .algorithm_hall import DEFINITIONS,HALL_WINDOWS
  from .algorithm_paper import RULE_VERSION
  self.schema=hashlib.sha256(json.dumps(['hall-paper-snapshot-v2',RULE_VERSION,DEFINITIONS,HALL_WINDOWS],sort_keys=True).encode()).hexdigest()
  self.install()
  try:
   rows=self.db.query('SELECT payload FROM algorithm_display_snapshot WHERE id=1 AND schema_version=?',(self.schema,))
   if rows:
    self.bundle=json.loads(zlib.decompress(rows[0]['payload']))
    status=self.hall.paper.status()
    if status.get('version') and not status.get('progress',{}).get('running'):self.hall.paper.next_audit=time.monotonic()+60
    else:self.bundle=None
  except (ValueError,TypeError,zlib.error):self.bundle=None
 def install(self):
  with self.db.tx() as c:
   c.execute('CREATE TABLE IF NOT EXISTS algorithm_display_revision(id INTEGER PRIMARY KEY,source_revision INTEGER NOT NULL,display_revision INTEGER NOT NULL)')
   c.execute('INSERT OR IGNORE INTO algorithm_display_revision VALUES(1,0,0)')
   c.execute('CREATE TABLE IF NOT EXISTS algorithm_display_snapshot(id INTEGER PRIMARY KEY,schema_version TEXT NOT NULL,payload BLOB NOT NULL)')
   tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
   for table in ('custom_orders','gap_order_tags'):
    for operation in ('INSERT','UPDATE','DELETE'):c.execute(f'DROP TRIGGER IF EXISTS hall_revision_{table}_{operation}')
   for table in ('draws','conflicts','algorithm_favorites','public_visibility','algorithm_library_backtests'):
    if table not in tables:continue
    column='source_revision' if table in ('draws','conflicts','custom_orders','gap_order_tags') else 'display_revision'
    for operation in ('INSERT','UPDATE','DELETE'):
     c.execute(f'CREATE TRIGGER IF NOT EXISTS hall_revision_{table}_{operation} AFTER {operation} ON {table} BEGIN UPDATE algorithm_display_revision SET {column}={column}+1 WHERE id=1; END')
 def revision(self):return self.db.query('SELECT source_revision,display_revision FROM algorithm_display_revision WHERE id=1')[0]
 def current(self,bundle,revision):
  return bundle is not None and not self.hall.paper.audit_due() and bundle['revision']==revision['source_revision'] and (bundle['next_at'] is None or time.time()<bundle['next_at'])
 def read(self,stale=False):
  bundle=self.bundle;revision=self.revision()
  if self.current(bundle,revision):return bundle
  self.wake.set()
  if stale:return bundle
  with self.lock:
   for attempt in range(3):
    revision=self.revision()
    if self.current(self.bundle,revision):return self.bundle
    # Capture the next clock boundary before reading inputs. If it passes while
    # calculating, retry rather than publish an apparently fresh incomplete result.
    future=self.db.query("SELECT (MIN(julianday(draw_utc))-2440587.5)*86400 next_at FROM draws WHERE julianday(draw_utc)>julianday('now') AND verification IN ('full_keno','crosschecked_abc')")[0]['next_at']
    rows,version=self.hall._calculate_snapshot()
    if self.revision()['source_revision']!=revision['source_revision'] or (future is not None and time.time()>=future):continue
    candidate={'rows':rows,'version':version,'draws':self.hall.history_draws,'revision':revision['source_revision'],'next_at':future,'updated_at':time.time()}
    payload=zlib.compress(json.dumps(candidate,ensure_ascii=False,separators=(',',':')).encode(),1)
    with self.db.tx() as c:
     if c.execute('SELECT source_revision FROM algorithm_display_revision WHERE id=1').fetchone()[0]!=revision['source_revision']:continue
     c.execute('INSERT OR REPLACE INTO algorithm_display_snapshot VALUES(1,?,?)',(self.schema,payload))
    self.bundle=candidate;self.error=False;return candidate
   raise RuntimeError('算法统计正在更新，请稍后重试')
 def start(self,stop):
  if self.started:return
  self.install();self.started=True
  def run():
   while not stop.is_set():
    self.wake.clear()
    try:self.read()
    except Exception:
     self.error=True;logging.getLogger(__name__).exception('Algorithm presentation snapshot refresh failed')
    self.wake.wait(2)
  self.thread=threading.Thread(target=run,name='pc28-algorithm-snapshot',daemon=True);self.thread.start()
