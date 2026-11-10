"""Offline additive migration; never enables order forwarding."""
import sys,json,hashlib
from pathlib import Path
from datetime import datetime
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from pc28.db import DB
from pc28.service import Service
root=Path(__file__).resolve().parent.parent
db=DB(root/json.loads((root/'config.json').read_text())['database'])
tables=['accounts','orders','ledger','trend_orders','trend_accounts','custom_orders','custom_ledger','custom_strategies','web_users','web_invites']
def signatures():
 result={}
 for table in tables:
  rows=db.query('SELECT * FROM '+table+' ORDER BY rowid');result[table]={'count':len(rows),'hash':hashlib.sha256(json.dumps(rows,sort_keys=True,ensure_ascii=False).encode()).hexdigest()}
 return result
before=signatures();backup=root/'data/exports'/('before-robot-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'.sqlite3');db.backup(backup)
s=Service(root)
if not s.robot.rules():s.robot.save_rule(s.robot.defaults())
after=signatures()
assert before==after,'Existing data changed during migration'
assert not s.robot.config()['enabled']
assert not any(r['push_enabled'] for r in s.robot.push_strategies())
report={'backup':str(backup),'existing_data_unchanged':True,'global_enabled':False,'enabled_push_strategies':0,'strategy_count':len(s.robot.strategies()),'rule_count':len(s.robot.rules()),'queued_orders':db.query('SELECT COUNT(*) n FROM robot_outbox')[0]['n']}
(root/'data/exports/robot-deployment.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(report,ensure_ascii=False))
