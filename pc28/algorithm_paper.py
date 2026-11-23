"""Independent, durable algorithm paper orders. No member or robot order writes."""
import bisect,hashlib,json,sqlite3,threading,time
from contextlib import closing
from pathlib import Path
from .model import settlement
INITIAL=1000000
BASE=10000
STEP=2500
CAP=20000
RULE_VERSION='paper-100-win-reset-loss25-cap200-refund-hold-v1'
COLUMNS='algorithm_id,target_draw,cutoff_draw,direction,stake,pnl,hit,outcome,balance_before,balance_after,next_stake,resets,investment,bets,wins,refunds,peak,drawdown'

class AlgorithmPaper:
 def __init__(self,db,odds=None):
  self.db=db;self.path=Path(db.path).with_name(Path(db.path).stem+'-algorithm-paper.sqlite3');self.odds=odds;self.lock=threading.RLock();self.next_audit=0
  with closing(self.connect()) as c:
   c.executescript('''PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sources(id INTEGER PRIMARY KEY,draw_utc TEXT,a INTEGER,b INTEGER,c INTEGER,total INTEGER,size TEXT,parity TEXT,digest TEXT,odds_id INTEGER);
CREATE TABLE IF NOT EXISTS odds(id INTEGER PRIMARY KEY,digest TEXT UNIQUE,value TEXT);
CREATE TABLE IF NOT EXISTS states(algorithm_id TEXT PRIMARY KEY,digest TEXT,definition TEXT,last_target INTEGER,state TEXT);
CREATE TABLE IF NOT EXISTS paper_orders(algorithm_id TEXT,target_draw INTEGER,cutoff_draw INTEGER,direction TEXT,stake INTEGER,pnl INTEGER,hit INTEGER,outcome TEXT,balance_before INTEGER,balance_after INTEGER,next_stake INTEGER,resets INTEGER,investment INTEGER,bets INTEGER,wins INTEGER,refunds INTEGER,peak INTEGER,drawdown INTEGER,PRIMARY KEY(algorithm_id,target_draw)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS pending(algorithm_id TEXT PRIMARY KEY,target_draw INTEGER,cutoff_draw INTEGER,direction TEXT,stake INTEGER,balance INTEGER,odds_id INTEGER);
CREATE TABLE IF NOT EXISTS audits(id INTEGER PRIMARY KEY,created REAL,report TEXT);
''')
 def connect(self):
  c=sqlite3.connect(self.path,timeout=30);c.row_factory=sqlite3.Row;c.execute('PRAGMA busy_timeout=30000');return c
 def query(self,sql,args=()):
  c=self.connect()
  try:return [dict(r) for r in c.execute(sql,args)]
  finally:c.close()
 def setting(self,c,key,default=None):
  r=c.execute('SELECT value FROM meta WHERE key=?',(key,)).fetchone();return json.loads(r[0]) if r else default
 def put(self,c,key,value):c.execute('INSERT OR REPLACE INTO meta VALUES(?,?)',(key,json.dumps(value,separators=(',',':'))))
 def audit_due(self):return time.monotonic()>=self.next_audit
 def status(self):
  rows=self.query("SELECT key,value FROM meta WHERE key IN ('progress','last_report','version')")
  return {r['key']:json.loads(r['value']) for r in rows}
 def sync(self,force_verify=False):
  from .algorithm_hall import DEFINITIONS,predict
  from .algorithm_library import FormulaHistory
  from .config import DEFAULT
  with self.lock:
   rows=self.db.query("""SELECT d.id,d.draw_utc,d.a,d.b,d.c,d.total,d.size,d.parity,d.verification FROM draws d
WHERE d.draw_utc IS NOT NULL AND julianday(d.draw_utc)<=julianday('now') AND d.verification IN ('full_keno','crosschecked_abc')
AND NOT EXISTS(SELECT 1 FROM conflicts x WHERE x.draw_id=d.id AND x.resolved=0) ORDER BY d.id""")
   ids=[r['id'] for r in rows];hashes={r['id']:hashlib.sha256(json.dumps(r,sort_keys=True).encode()).hexdigest() for r in rows}
   digest=hashlib.sha256(json.dumps([RULE_VERSION,[(i,hashes[i]) for i in ids]],separators=(',',':')).encode()).hexdigest()
   odds=self.odds() if callable(self.odds) else self.odds or DEFAULT['odds'];wire=json.dumps(odds,sort_keys=True);odds_hash=hashlib.sha256(wire.encode()).hexdigest()
   c=self.connect();report={'source_draws':len(rows),'algorithms':len(DEFINITIONS),'inserted':0,'repaired':0,'skipped':0,'verified':0}
   try:
    previous={r['id']:r['digest'] for r in c.execute('SELECT id,digest FROM sources')}
    changes=[i for i,h in hashes.items() if previous.get(i)!=h]+[i for i in previous if i not in hashes]
    dirty=min(changes) if changes else None;old_dirty=self.setting(c,'dirty_from')
    if old_dirty is not None:dirty=min(dirty,old_dirty) if dirty is not None else old_dirty
    deep=force_verify or time.time()-self.setting(c,'verified_at',0)>21600
    c.execute('INSERT OR IGNORE INTO odds(digest,value) VALUES(?,?)',(odds_hash,wire));odds_id=c.execute('SELECT id FROM odds WHERE digest=?',(odds_hash,)).fetchone()[0]
    self.put(c,'dirty_from',dirty)
    for r in rows:
     if previous.get(r['id'])==hashes[r['id']]:continue
     prior=c.execute('SELECT odds_id FROM sources WHERE id=?',(r['id'],)).fetchone()
     pending=c.execute('SELECT odds_id FROM pending WHERE target_draw=? LIMIT 1',(r['id'],)).fetchone()
     applied=prior[0] if prior else pending[0] if pending else odds_id
     c.execute('INSERT OR REPLACE INTO sources VALUES(?,?,?,?,?,?,?,?,?,?)',(r['id'],r['draw_utc'],r['a'],r['b'],r['c'],r['total'],r['size'],r['parity'],hashes[r['id']],applied))
    for i in previous.keys()-hashes.keys():c.execute('DELETE FROM sources WHERE id=?',(i,))
    self.put(c,'progress',{'running':True,'completed':0,'total':len(DEFINITIONS)});c.commit()
    source_odds={r['id']:r['odds_id'] for r in c.execute('SELECT id,odds_id FROM sources')}
    all_odds={r['id']:json.loads(r['value']) for r in c.execute('SELECT * FROM odds')};prepared=FormulaHistory(rows)
    paid={oid:{(direction,total,hit,stake):settlement(stake,direction,total,hit,value) for direction in ('大','小','单','双') for total in range(28) for hit in (False,True) for stake in range(BASE,CAP+1,STEP)} for oid,value in all_odds.items()}
    for index,d in enumerate(DEFINITIONS):
     key=d['id'];signature=hashlib.sha256(json.dumps([RULE_VERSION,d],sort_keys=True).encode()).hexdigest()
     state=c.execute('SELECT * FROM states WHERE algorithm_id=?',(key,)).fetchone()
     count=c.execute('SELECT COUNT(*) FROM paper_orders WHERE algorithm_id=?',(key,)).fetchone()[0]
     hole=None
     known_source_change=dirty is not None and state is not None and count==len(previous) and state['last_target']==(max(previous) if previous else None)
     if count!=len(rows) and not known_source_change:
      h=c.execute('SELECT s.id FROM sources s LEFT JOIN paper_orders o ON o.algorithm_id=? AND o.target_draw=s.id WHERE o.target_draw IS NULL ORDER BY s.id LIMIT 1',(key,)).fetchone();hole=h[0] if h else -2**63
     rebuild=deep or not state or state['definition']!=signature
     if not rebuild and dirty is None and hole is None and state['digest']==digest:report['skipped']+=1;continue
     start=0 if rebuild else bisect.bisect_left(ids,min(v for v in (dirty,hole) if v is not None)) if dirty is not None or hole is not None else 0
     threshold=-2**63 if rebuild else min([v for v in (ids[start] if start<len(ids) else None,dirty,hole) if v is not None],default=2**63-1)
     old={r['target_draw']:tuple(r) for r in c.execute('SELECT '+COLUMNS+' FROM paper_orders WHERE algorithm_id=? AND target_draw>=? ORDER BY target_draw',(key,threshold))}
     checkpoint=c.execute('SELECT * FROM paper_orders WHERE algorithm_id=? AND target_draw<? ORDER BY target_draw DESC LIMIT 1',(key,threshold)).fetchone()
     balance=checkpoint['balance_after'] if checkpoint else INITIAL;stake=checkpoint['next_stake'] if checkpoint else BASE
     resets=checkpoint['resets'] if checkpoint else 0;investment=checkpoint['investment'] if checkpoint else 0;bets=checkpoint['bets'] if checkpoint else 0;wins=checkpoint['wins'] if checkpoint else 0;refunds=checkpoint['refunds'] if checkpoint else 0;peak=checkpoint['peak'] if checkpoint else INITIAL;drawdown=checkpoint['drawdown'] if checkpoint else 0
     c.execute('BEGIN IMMEDIATE');batch=[];seen=set();labels=('小','大') if d['axis']=='size' else ('双','单');bits=prepared.axes[d['axis']][0]
     for i,choice in prepared.predictions(d,start):
      r=rows[i];target=r['id'];seen.add(target);before=balance
      if choice is None:direction=None;amount=0;pnl=0;hit=None;outcome='skip'
      else:
       direction=labels[choice];amount=stake;hit=int(choice==bits[i]);p=paid[source_odds[target]][direction,r['total'],bool(hit),stake];pnl=p['pnl'];outcome=p['outcome'];balance+=pnl;investment+=amount;bets+=1;wins+=hit;refunds+=int(outcome=='refund')
       following=BASE if outcome=='win' or (outcome=='loss' and stake>=CAP) else stake+STEP if outcome=='loss' else stake
       resets+=int(stake!=BASE and following==BASE);stake=following;peak=max(peak,balance);drawdown=max(drawdown,peak-balance)
      record=(key,target,rows[i-1]['id'] if i else None,direction,amount,pnl,hit,outcome,before,balance,stake,resets,investment,bets,wins,refunds,peak,drawdown)
      prior=old.get(target);report['verified']+=1
      if prior!=record:
       batch.append(record);report['inserted' if prior is None else 'repaired']+=1
      if len(batch)>=5000:c.executemany('INSERT OR REPLACE INTO paper_orders VALUES('+','.join('?' for _ in range(18))+')',batch);batch=[]
     if batch:c.executemany('INSERT OR REPLACE INTO paper_orders VALUES('+','.join('?' for _ in range(18))+')',batch)
     for target in old.keys()-seen:c.execute('DELETE FROM paper_orders WHERE algorithm_id=? AND target_draw=?',(key,target));report['repaired']+=1
     summary={'balance':balance,'next_stake':stake,'resets':resets,'investment':investment,'bets':bets,'wins':wins,'refunds':refunds,'peak':peak,'max_drawdown':drawdown}
     c.execute('INSERT OR REPLACE INTO states VALUES(?,?,?,?,?)',(key,digest,signature,ids[-1] if ids else None,json.dumps(summary)))
     self.put(c,'progress',{'running':True,'completed':index+1,'total':len(DEFINITIONS),'algorithm':key});c.commit()
    latest=self.db.query('SELECT MAX(id) id FROM draws')[0]['id'];prior_pending={r['algorithm_id']:dict(r) for r in c.execute('SELECT * FROM pending')};c.execute('DELETE FROM pending')
    for d in DEFINITIONS:
     value=c.execute('SELECT state FROM states WHERE algorithm_id=?',(d['id'],)).fetchone();state=json.loads(value[0])
     direction=predict(rows,d)[0] if rows and (ids[-1]==latest or prior_pending.get(d['id'],{}).get('target_draw')==ids[-1]+1) else None
     if direction:
      old_pending=prior_pending.get(d['id']);applied=old_pending['odds_id'] if old_pending and old_pending['target_draw']==ids[-1]+1 and old_pending['direction']==direction and old_pending['stake']==state['next_stake'] else odds_id
      c.execute('INSERT INTO pending VALUES(?,?,?,?,?,?,?)',(d['id'],ids[-1]+1,ids[-1],direction,state['next_stake'],state['balance'],applied))
    version=hashlib.sha256(json.dumps([digest,[d['id'] for d in DEFINITIONS],time.time() if report['inserted'] or report['repaired'] else self.setting(c,'version')],sort_keys=True).encode()).hexdigest() if report['inserted'] or report['repaired'] or not self.setting(c,'version') else self.setting(c,'version')
    self.put(c,'version',version);self.put(c,'dirty_from',None);self.put(c,'last_report',report);self.put(c,'progress',{'running':False,'completed':len(DEFINITIONS),'total':len(DEFINITIONS)})
    if deep:self.put(c,'verified_at',time.time())
    c.execute('INSERT INTO audits(created,report) VALUES(?,?)',(time.time(),json.dumps(report)));c.commit();self.next_audit=time.monotonic()+60
    return report
   except BaseException:c.rollback();raise
   finally:c.close()
 def source_tail(self,limit):return self.query('SELECT * FROM (SELECT id,draw_utc,a,b,c,total,size,parity FROM sources ORDER BY id DESC LIMIT ?) ORDER BY id',(limit,))
 def state(self,key):
  rows=self.query('SELECT state FROM states WHERE algorithm_id=?',(key,));return json.loads(rows[0]['state']) if rows else {}
 def records(self,key,cutoff,window=None,page=1,size=100,skips=False):
  condition='o.algorithm_id=? AND o.target_draw<=?';args=[key,cutoff or 0]
  if window is not None:condition+=' AND o.target_draw>?';args.append((cutoff or 0)-window)
  if not skips:condition+=' AND o.stake>0'
  c=self.connect()
  try:
   c.execute('BEGIN')
   if self.setting(c,'progress',{}).get('running'):raise ValueError('模拟账本正在校验补算，请稍后查看明细')
   total=c.execute('SELECT COUNT(*) n FROM paper_orders o WHERE '+condition,args).fetchone()[0];pages=max(1,(total+size-1)//size);page=max(1,min(int(page),pages))
   rows=[dict(r) for r in c.execute("SELECT o.*,s.draw_utc time,s.a,s.b,s.c,s.total FROM paper_orders o JOIN sources s ON s.id=o.target_draw WHERE "+condition+' ORDER BY o.target_draw DESC LIMIT ? OFFSET ?',(*args,size,(page-1)*size))]
  finally:c.close()
  for r in rows:r.update(prediction=r['direction'],credit=r['stake']+r['pnl'],formula=f"{r['a']}+{r['b']}+{r['c']} = {r['total']}",after_stake_balance=r['balance_before']-r['stake'],settled=r['time'])
  return {'records':rows,'total':total,'page':page,'pages':pages,'page_size':size}
 def trades(self,key,cutoff):
  c=self.connect()
  try:
   c.execute('BEGIN')
   if self.setting(c,'progress',{}).get('running'):raise ValueError('模拟账本正在校验补算，请稍后查看收益曲线')
   return [dict(r) for r in c.execute('SELECT o.pnl,s.draw_utc beijing_time FROM paper_orders o JOIN sources s ON s.id=o.target_draw WHERE o.algorithm_id=? AND o.stake>0 AND o.target_draw<=? ORDER BY o.target_draw',(key,cutoff or 0))]
  finally:c.close()
 def metrics(self,key,cutoff,windows):
  from .algorithm_hall import summarize_hits
  rows=self.query('SELECT target_draw,hit,stake,pnl,outcome,balance_before,balance_after,next_stake,resets FROM paper_orders WHERE algorithm_id=? AND target_draw>? AND target_draw<=? ORDER BY target_draw',(key,(cutoff or 0)-max(windows),cutoff or 0));result={}
  for window in windows:
   selected=[r for r in rows if r['target_draw']>(cutoff or 0)-window];bets=[r for r in selected if r['stake']];m=summarize_hits(((r['target_draw'],r['hit']) for r in selected),len(selected));net=sum(r['pnl'] for r in bets);stake=sum(r['stake'] for r in bets)
   m['simulation']={'orders':len(bets),'stake':stake,'net':net,'return_rate':net/stake if stake else None,'initial_balance':INITIAL,'start_balance':selected[0]['balance_before'] if selected else INITIAL,'balance':selected[-1]['balance_after'] if selected else INITIAL,'next_stake':selected[-1]['next_stake'] if selected else BASE,'resets':sum(r['next_stake']==BASE and r['stake'] not in (0,BASE) for r in bets)}
   m['hits']=[{k:r[k] for k in ('target_draw','hit','outcome','pnl','stake')} for r in reversed(selected[-100:])];m['pattern_total']=len(selected);result[str(window)]=m
  return result
