"""Live-only signal ledger with a financially isolated, persistent direction runtime.

Existing strategy engines are reused with neutral sizing and no execution stops.
The private runtime is never used as a member account or a historical backtest.
"""
import copy,hashlib,json,uuid
from datetime import datetime,timezone
from .db import DB
from .model import utcnow,settlement
from . import router,trend_strategy,custom_strategy

TABLES={'legacy':'orders','trend':'trend_orders','custom':'custom_orders'}
SCHEMA='''
CREATE TABLE IF NOT EXISTS strategy_signal_versions(strategy TEXT,version TEXT,settings TEXT NOT NULL,created TEXT NOT NULL,PRIMARY KEY(strategy,version));
CREATE TABLE IF NOT EXISTS strategy_signals(id INTEGER PRIMARY KEY,strategy TEXT NOT NULL,version TEXT NOT NULL,target_draw INTEGER NOT NULL,cutoff_draw INTEGER NOT NULL,created TEXT NOT NULL,direction TEXT NOT NULL,stake INTEGER NOT NULL DEFAULT 10000,status TEXT NOT NULL DEFAULT 'pending',hit INTEGER,pnl INTEGER,outcome TEXT,total INTEGER,settled TEXT,cycle INTEGER,step INTEGER,odds_json TEXT NOT NULL,evidence TEXT NOT NULL,source_engine TEXT NOT NULL,source_id INTEGER NOT NULL,UNIQUE(source_engine,source_id),UNIQUE(strategy,target_draw));
CREATE INDEX IF NOT EXISTS signal_strategy_status ON strategy_signals(strategy,status,target_draw);
'''

def initialize(db):
 with db.tx() as c:
  for sql in SCHEMA.split(';'):
   if sql.strip():c.execute(sql)

def neutral(settings):
 s=copy.deepcopy(settings)
 for k in ('daily_loss_limit','max_drawdown','win_limit','win_loss_difference_limit','max_losses','algorithm_difference_limit','algorithm_cooldown_rounds','max_increase_steps','max_decrease_steps','loss_increment_cents','win_decrement_cents'):
  if k in s:s[k]=0
 s.update(initial_balance=1000000000,max_stake=20000)
 if 'base_cents' in s:s['base_cents']=10000
 if 'multiplier_hundredths' in s:s['multiplier_hundredths']=100
 if 'stake_mode' in s:s['stake_mode']='fixed'
 for k in list(s):
  if k.startswith(('low_','high_')) and k.endswith('_cents'):s[k]=0
 if 'fixed' in s:
  s['fixed'].update(base_cents=10000,multiplier_hundredths=100,max_losses=0)
 return s

def signature(settings):
 return hashlib.sha256(json.dumps(settings,sort_keys=True,ensure_ascii=False).encode()).hexdigest()[:20]

class Signals:
 def __init__(self,service):self.service=service;self.runtime=None;self.cursors={e:0 for e in TABLES};self.versions={};self.started=utcnow()
 def prepare(self):
  if self.runtime:return
  self.runtime=DB(self.service.root/'data/strategy-signals-runtime.sqlite3')
  cfg=copy.deepcopy(self.service.cfg);cfg['simulation']=neutral(cfg['simulation'])
  router.ensure_account(self.runtime,cfg);trend_strategy.initialize(self.runtime,cfg)
  with self.runtime.tx() as c:
   c.execute('CREATE TABLE IF NOT EXISTS signal_runtime_versions(strategy TEXT PRIMARY KEY,version TEXT NOT NULL)')
   c.execute("INSERT OR IGNORE INTO runtime_state VALUES('signal_epoch',?,?)",(uuid.uuid4().hex,utcnow()))
   self.epoch=c.execute("SELECT value FROM runtime_state WHERE key='signal_epoch'").fetchone()[0]
   self.versions={r['strategy']:r['version'] for r in c.execute('SELECT * FROM signal_runtime_versions')}
  # On restart export any committed but unmirrored realtime signals, never replay old draws.
  self.cursors={e:self.service.db.query('SELECT COALESCE(MAX(source_id),0) n FROM strategy_signals WHERE source_engine=?',(e+':'+self.epoch,))[0]['n'] for e in TABLES}
 def settle(self,now):
  from .algorithm_hall import VERIFIED
  with self.service.db.tx() as c:
   for o in c.execute("SELECT * FROM strategy_signals WHERE status='pending'").fetchall():
    r=c.execute('SELECT * FROM draws WHERE id=?',(o['target_draw'],)).fetchone()
    if not r or r['verification'] not in VERIFIED or not r['draw_utc'] or datetime.fromisoformat(r['draw_utc'])>now:continue
    if c.execute('SELECT 1 FROM conflicts WHERE draw_id=? AND resolved=0',(r['id'],)).fetchone():continue
    if datetime.fromisoformat(o['created'])>=datetime.fromisoformat(r['draw_utc']):
     c.execute("UPDATE strategy_signals SET status='invalid',outcome='late',settled=? WHERE id=?",(utcnow(),o['id']));continue
    axis='size' if o['direction'] in ('大','小') else 'parity';hit=r[axis]==o['direction'];paid=settlement(10000,o['direction'],r['total'],hit,json.loads(o['odds_json']))
    c.execute("UPDATE strategy_signals SET status='settled',hit=?,pnl=?,outcome=?,total=?,settled=? WHERE id=?",(int(hit),paid['pnl'],paid['outcome'],r['total'],utcnow(),o['id']))
 def tick(self,now=None):
  now=now or datetime.now(timezone.utc);self.settle(now);self.prepare();self.export();s=self.service;db=self.runtime
  # Maximum supported ranking window plus algorithm warmup; later turns only sync the tail.
  limit=10057 if not db.tail(1) else 200
  from .algorithm_hall import VERIFIED
  draws=s.db.tail(limit);latest=draws[-1] if draws else None
  pending=[r['target_draw'] for r in s.db.query("SELECT target_draw FROM strategy_signals WHERE status='pending'")]
  for target in set(pending):
   if draws and target<draws[0]['id']:draws.extend(s.db.query('SELECT * FROM draws WHERE id=?',(target,)))
  with db.tx() as c:
   fields=[r[1] for r in c.execute('PRAGMA table_info(draws)')];sql='INSERT INTO draws('+','.join(fields)+') VALUES('+','.join('?' for _ in fields)+') ON CONFLICT(id) DO UPDATE SET '+','.join(k+'=excluded.'+k for k in fields if k!='id')
   for r in draws:
    if r['verification'] not in VERIFIED or not r['draw_utc'] or datetime.fromisoformat(r['draw_utc'])>now:
     c.execute('DELETE FROM draws WHERE id=?',(r['id'],));continue
    c.execute(sql,[r[k] for k in fields])
   c.execute('DELETE FROM conflicts');conflicts=s.db.query('SELECT * FROM conflicts WHERE resolved=0')
   for r in conflicts:
    c.execute('INSERT INTO conflicts(id,draw_id,received,old_record,new_record,resolved) VALUES(?,?,?,?,?,?)',tuple(r[k] for k in ('id','draw_id','received','old_record','new_record','resolved')))
   c.execute('DELETE FROM public_visibility')
   c.executemany('INSERT INTO public_visibility VALUES(?,?,?)',((r['kind'],r['item_id'],r['published']) for r in s.db.query('SELECT * FROM public_visibility')))
   c.execute("DELETE FROM runtime_state WHERE key='draw_timer'")
   for r in s.db.query("SELECT * FROM runtime_state WHERE key='draw_timer'"):c.execute('INSERT INTO runtime_state VALUES(?,?,?)',(r['key'],r['value'],r['updated']))
   c.execute('UPDATE custom_strategies SET enabled=0')
  # Settle old immutable orders before changing a strategy version.
  cfg=copy.deepcopy(s.cfg);cfg['simulation']=neutral(cfg['simulation']);cfg['simulation']['enabled']=False;cfg['trend_simulation']['enabled_axes']=[]
  router.tick(db,cfg,now);trend_strategy.live_tick(db,cfg,now);custom_strategy.tick(db,cfg,now)
  if not latest or latest['verification'] not in VERIFIED or not latest['draw_utc'] or datetime.fromisoformat(latest['draw_utc'])>now:return
  definitions=[('legacy',neutral(s.cfg['simulation']),int(s.cfg['simulation']['enabled']),'原有反转策略')]
  definitions += [(axis,neutral(trend_strategy.settings(s.cfg,axis)),int(axis in s.cfg['trend_simulation']['enabled_axes']),trend_strategy.AXES[axis][0]) for axis in ('size','parity')]
  definitions += [(r['id'],neutral(json.loads(r['settings'])),r['enabled'],r['name']) for r in s.db.query('SELECT * FROM custom_strategies WHERE id NOT IN (SELECT id FROM deleted_strategies)')]
  with db.tx() as c,s.db.tx() as main:
   for key,settings,enabled,name in definitions:
    version=signature(settings);old=self.versions.get(key);pending_table='orders' if key=='legacy' else 'trend_orders' if key in ('size','parity') else 'custom_orders'
    pending_sql="SELECT 1 FROM "+pending_table+" WHERE status='pending'"+(' AND strategy=?' if key!='legacy' else '')
    if old!=version and c.execute(pending_sql,() if key=='legacy' else (key,)).fetchone():continue
    main.execute('INSERT OR IGNORE INTO strategy_signal_versions VALUES(?,?,?,?)',(key,version,json.dumps(settings,ensure_ascii=False),utcnow()))
    self.versions[key]=version;c.execute('INSERT OR REPLACE INTO signal_runtime_versions VALUES(?,?)',(key,version))
    if key=='legacy':
     cfg['simulation']=settings;cfg['simulation']['enabled']=bool(enabled)
     if old!=version:c.execute("UPDATE accounts SET state='{}',paused=0,pause_reason='',balance=100000000000,peak=100000000000 WHERE id=1")
    elif key in ('size','parity'):
     cfg['trend_simulation'].setdefault('strategies',{})[key]=settings
     if enabled:cfg['trend_simulation']['enabled_axes'].append(key)
     if old!=version:c.execute('UPDATE trend_accounts SET balance=100000000000,peak=100000000000,losses=0,win_streak=0,pause_until=0,win_loss_difference=0,difference_paused=0 WHERE strategy=?',(key,))
    else:
     c.execute("INSERT INTO custom_strategies(id,name,enabled,settings,balance,initial_balance,peak,state,created) VALUES(?,?,?,?,100000000000,100000000000,100000000000,'{}',?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,enabled=excluded.enabled,settings=excluded.settings",(key,name,enabled,json.dumps(settings,ensure_ascii=False),utcnow()))
     if old!=version:c.execute("UPDATE custom_strategies SET state='{}',balance=100000000000,peak=100000000000 WHERE id=?",(key,))
   # Adaptive selections keep original provenance; they cannot create historical signals.
   c.execute('DELETE FROM selections')
   for r in s.db.query('SELECT * FROM selections'):
    candidates=json.loads(r['candidates'])
    for candidate in candidates:candidate['strategy'].update(base_cents=10000,multiplier_hundredths=100,max_losses=0)
    c.execute('INSERT INTO selections VALUES(?,?,?,?,?)',(r['month'],r['created'],r['run_id'],json.dumps(candidates),r['reason']))
  router.tick(db,cfg,now);trend_strategy.live_tick(db,cfg,now);custom_strategy.tick(db,cfg,now)
  self.export()
  self.settle(now)
 def export(self):
  cursors=dict(self.cursors)
  with self.service.db.tx() as c:
   for engine,table in TABLES.items():
    for o in self.runtime.query('SELECT * FROM '+table+' WHERE id>? ORDER BY id',(self.cursors[engine],)):
     key='legacy' if engine=='legacy' else o['strategy'];direction=json.loads(o['strategy_json'])['direction'] if engine=='legacy' else o['direction']
     # Committed realtime runtime records survive a crash before main-ledger export.
     # Capture refuses drawn targets, so recovery cannot send an old order.
     evidence=o['rules_json'] if engine=='trend' else o['strategy_json']
     c.execute("INSERT OR IGNORE INTO strategy_signals(strategy,version,target_draw,cutoff_draw,created,direction,cycle,step,odds_json,evidence,source_engine,source_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",(key,self.versions.get(key,'unknown'),o['target_draw'],o['cutoff_draw'],o['created'],direction,o['round_id'] if engine=='legacy' else o['cycle'],o['round_attempt'] if engine=='legacy' else o['step'],o['odds_json'] if engine!='legacy' else json.dumps(self.service.cfg['odds']),evidence or '{}',engine+':'+self.epoch,o['id']))
     cursors[engine]=o['id']
  self.cursors=cursors

def stats(db):
 return {r['strategy']:{**r,'win_rate':r['wins']/r['bets'] if r['bets'] else None,'net':r['net']/100,'investment':r['bets']*100,'roi':r['net']/(r['bets']*10000) if r['bets'] else None} for r in db.query("SELECT strategy,COUNT(CASE WHEN status='settled' THEN 1 END) bets,COALESCE(SUM(CASE WHEN status='settled' THEN hit ELSE 0 END),0) wins,COALESCE(SUM(pnl),0) net,MIN(created) started,COUNT(CASE WHEN status='pending' THEN 1 END) pending FROM strategy_signals GROUP BY strategy")}
