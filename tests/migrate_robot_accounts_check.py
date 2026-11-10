"""Read live DB into a backup, migrate only the disposable copy; never send."""
import sys,json,sqlite3,hashlib,shutil,tempfile
from contextlib import closing
from pathlib import Path
from datetime import datetime
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from pc28.service import Service
root=Path(__file__).resolve().parent.parent
stamp=datetime.now().strftime('%Y%m%d-%H%M%S')
backup=root/'data/exports'/('before-multi-account-'+stamp+'.sqlite3')
backup.parent.mkdir(exist_ok=True)
cfg=json.loads((root/'config.json').read_text(encoding='utf-8-sig'))
with closing(sqlite3.connect(root/cfg['database'])) as source,closing(sqlite3.connect(backup)) as dest:source.backup(dest)
tables=['accounts','orders','ledger','trend_accounts','trend_orders','trend_ledger','custom_strategies','custom_orders','custom_ledger','member_accounts','member_cash_ledger','member_orders','robot_batches','robot_batch_items','robot_delivery_slots']
def digest(c,table,cols):
 return hashlib.sha256(json.dumps(c.execute('SELECT '+','.join(cols)+' FROM '+table+' ORDER BY rowid').fetchall(),ensure_ascii=False,default=str).encode()).hexdigest()
with tempfile.TemporaryDirectory(dir=root/'data') as tmp:
 temp=Path(tmp);(temp/'data').mkdir();shutil.copy2(backup,temp/'data/pc28.sqlite3');cfg['database']='data/pc28.sqlite3';cfg['collector']['enabled']=False;(temp/'config.json').write_text(json.dumps(cfg),encoding='utf8')
 with closing(sqlite3.connect(temp/'data/pc28.sqlite3')) as c:
  columns={t:[r[1] for r in c.execute('PRAGMA table_info('+t+')')] for t in tables};before={t:digest(c,t,columns[t]) for t in tables}
  old=c.execute("SELECT id,event_id,recipient_id,request,status FROM robot_outbox WHERE status IN ('sent','sending','failed','expired','cancelled') ORDER BY id").fetchall()
  bindings=c.execute('SELECT COUNT(*) FROM robot_bindings WHERE deleted=0').fetchone()[0]
 s=Service(temp);s.robot.init_accounts();s.robot.init_accounts()
 with closing(sqlite3.connect(temp/'data/pc28.sqlite3')) as c:
  after={t:digest(c,t,columns[t]) for t in tables};assert before==after,[t for t in tables if before[t]!=after[t]]
  now=c.execute("SELECT id,event_id,recipient_id,request,status FROM robot_outbox WHERE id IN ("+','.join('?' for _ in old)+") ORDER BY id",[r[0] for r in old]).fetchall() if old else []
  assert now==old,'Historical dispatch snapshots changed'
  assert s.robot.capture()==0,'Migration replayed source orders'
  counts={t:c.execute('SELECT COUNT(*) FROM '+t).fetchone()[0] for t in ['robot_accounts','robot_bots','robot_subscriptions','robot_binding_map','robot_account_conflicts']}
  assert counts['robot_binding_map']+counts['robot_account_conflicts']==bindings,'Missing legacy binding mapping'
 report={'backup':str(backup),'checked_tables':tables,'simulation_and_member_finances_unchanged':True,'historical_event_snapshots_unchanged':True,'no_source_replay':True,'migration_idempotent':True,'legacy_bindings':bindings,'counts':counts}
 (root/'data/audits/multi-account-migration-check.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8');print(json.dumps(report,ensure_ascii=False))
