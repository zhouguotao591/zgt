"""Add member routing without changing simulated strategy/account/order data."""
import sys,json,hashlib
from pathlib import Path
from datetime import datetime
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from pc28.db import DB
from pc28.service import Service
root=Path(__file__).resolve().parent.parent;db=DB(root/'data/pc28.sqlite3')
tables=['accounts','orders','ledger','trend_accounts','trend_orders','trend_ledger','custom_strategies','custom_orders','custom_ledger','web_users']
def signature():return {t:hashlib.sha256(json.dumps(db.query('SELECT * FROM '+t+' ORDER BY rowid'),ensure_ascii=False,sort_keys=True).encode()).hexdigest() for t in tables}
before=signature();backup=root/'data/exports'/('before-member-push-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'.sqlite3');db.backup(backup)
s=Service(root);s.robot.save_config({**s.robot.config(),'enabled':False,'account_id':1,'bot_key':3,'merge_wait_seconds':3})
assert before==signature(),'Existing simulated orders or accounts changed'
assert not s.robot.config()['enabled']
report={'backup':str(backup),'existing_accounts_and_orders_unchanged':True,'push_enabled':False,'member_bindings':len(s.robot.bindings()),'members':len(s.robot.members()),'merged_history_records':s.robot.records({})['total'],'token_configured':s.robot.secret.exists(),'configured_signal_types':[r['signal_type'] for r in s.robot.rules()]}
(root/'data/exports/member-push-deployment.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(report,ensure_ascii=False))
