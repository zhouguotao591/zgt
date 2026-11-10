"""Add AI research without changing any existing strategy, member or order state."""
import sys,json,hashlib
from pathlib import Path
from datetime import datetime
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from pc28.db import DB
from pc28.service import Service
root=Path(__file__).resolve().parent.parent;db=DB(root/'data/pc28.sqlite3')
tables=['accounts','orders','ledger','trend_accounts','trend_orders','trend_ledger','custom_strategies','custom_orders','custom_ledger','web_users','robot_bindings','robot_config']
def signatures():return {t:hashlib.sha256(json.dumps(db.query('SELECT * FROM '+t+' ORDER BY rowid'),ensure_ascii=False,sort_keys=True).encode()).hexdigest() for t in tables}
before=signatures();backup=root/'data/exports'/('before-ai-deployment-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'.sqlite3');db.backup(backup);s=Service(root)
assert before==signatures(),'Existing account or order state changed'
assert s.ai.model(),'First training must complete before deployment'
print(json.dumps({'backup':str(backup),'existing_state_unchanged':True,'model_id':s.ai.model()['id'],'validation_samples':{a:v['evaluation']['count'] for a,v in s.ai.model()['report']['axes'].items()}},ensure_ascii=False))
