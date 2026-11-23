"""Profile read-only admin queries against production data; never starts workers."""
import sys,json,time,sqlite3,threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
root=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root))
from pc28.db import DB
from pc28.auth import Auth
from pc28.admin import get
from pc28.algorithm_hall import AlgorithmHall
from pc28.robot_signals import RobotSignals
from pc28.bot_secret import SecretStore
from pc28.strategies import catalog
class ReadOnly(DB):
 def __init__(self,path):self.path=path
 def connect(self):
  c=sqlite3.connect(self.path.resolve().as_uri()+'?mode=ro',uri=True,timeout=30);c.row_factory=sqlite3.Row;return c
 def tx(self):raise RuntimeError('Profiling forbids writes')
cfg=json.loads((root/'config.json').read_text(encoding='utf-8'));db=ReadOnly(root/cfg['database']);s=SimpleNamespace(db=db,cfg=cfg,root=root,lock=threading.RLock(),tasks={})
a=Auth.__new__(Auth);a.db=db;a.service=s;a.root=root;s.auth=a
h=AlgorithmHall.__new__(AlgorithmHall);h.db=db;h.lock=threading.RLock();h.fingerprint=None;h.cached=[];s.algorithm_hall=h
r=RobotSignals.__new__(RobotSignals);r.db=db;r.service=s;r.secret=SecretStore(root);r.installation='';s.robot=r
user=db.query("SELECT * FROM web_users WHERE username='admin'")[0]
report=[]
with patch('pc28.member_orders.sync',lambda service:None):
 for name in ('overview','users','member-orders','member-ledger','invites','audit','money-plans','robot','robot-accounts','robot-catalog','robot-records','algorithms'):
  t=time.perf_counter()
  try:
   result=get(s,name,{},user);record={'page':name,'seconds':round(time.perf_counter()-t,3),'bytes':len(json.dumps(result,ensure_ascii=False).encode())}
  except Exception as e:record={'page':name,'error':type(e).__name__+': '+str(e)}
  report.append(record);print(json.dumps(record),flush=True)
for i in range(2):
 t=time.perf_counter();result=catalog(db,cfg);report.append({'page':'strategies-'+str(i),'seconds':round(time.perf_counter()-t,3),'bytes':len(json.dumps(result).encode())})
(root/'data/audits/admin-performance-20261121.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
