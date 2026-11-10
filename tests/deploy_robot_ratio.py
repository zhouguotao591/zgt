"""Preserve existing live settings and fixed member amounts while updating delivery code."""
import sys,json,hashlib
from pathlib import Path
from datetime import datetime
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from pc28.db import DB
from pc28.service import Service
from pc28.model import utcnow
root=Path(__file__).resolve().parent.parent;db=DB(root/'data/pc28.sqlite3')
tables=['accounts','orders','ledger','trend_accounts','trend_orders','trend_ledger','custom_strategies','custom_orders','custom_ledger','web_users','robot_bindings','robot_config']
def signatures():return {t:hashlib.sha256(json.dumps(db.query('SELECT * FROM '+t+' ORDER BY rowid'),ensure_ascii=False,sort_keys=True).encode()).hexdigest() for t in tables}
before=signatures();backup=root/'data/exports'/('before-fixed-ratio-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'.sqlite3');db.backup(backup);s=Service(root)
with db.tx() as c:c.execute("UPDATE robot_outbox SET status='cancelled',last_error='推送格式升级，旧请求不补发',finished=? WHERE rule_id!=0 AND is_test=0 AND status IN ('pending','retry','sending')",(utcnow(),))
assert before==signatures(),'Existing orders, subscriptions or enabled flags changed'
print(json.dumps({'backup':str(backup),'existing_orders_and_member_amounts_unchanged':True,'enabled':s.robot.config()['enabled'],'bindings':len(s.robot.bindings())},ensure_ascii=False))
