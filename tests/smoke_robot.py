import sys,json,urllib.request,urllib.error
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from pc28.db import DB
result={}
for path in ['/','/api/status','/api/admin/robot','/admin/api/admin/robot','/robot.js']:
 try:
  with urllib.request.urlopen('http://127.0.0.1:8028'+path,timeout=10) as r:result[path]=r.status
 except urllib.error.HTTPError as e:result[path]=e.code
db=DB('D:/pc28/data/pc28.sqlite3')
report={'http':result,'robot_config':json.loads(db.query('SELECT settings FROM robot_config')[0]['settings']),'push_permissions':db.query('SELECT COUNT(*) n FROM robot_strategy_push WHERE enabled=1')[0]['n'],'outbox':db.query('SELECT COUNT(*) n FROM robot_outbox')[0]['n'],'worker_errors':db.query("SELECT COUNT(*) n FROM jobs WHERE kind='robot' AND status='error'")[0]['n']}
assert result=={'/':200,'/api/status':200,'/api/admin/robot':403,'/admin/api/admin/robot':401,'/robot.js':403},result
assert not report['robot_config']['enabled'] and not report['push_permissions'] and not report['outbox']
print(json.dumps(report,ensure_ascii=False))
