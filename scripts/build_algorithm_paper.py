import json,sys,time
from pathlib import Path
root=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root))
from pc28.db import DB
from pc28.algorithm_hall import AlgorithmHall
cfg=json.loads((root/'config.json').read_text(encoding='utf-8'));db=DB.__new__(DB);db.path=root/cfg['database'];h=AlgorithmHall(db,cfg['odds']);started=time.perf_counter()
print('Starting independent historical paper ledger',flush=True)
report=h.paper.sync(force_verify=True);report['elapsed_seconds']=round(time.perf_counter()-started,2);print(json.dumps(report),flush=True)
h.snapshot();print('Display snapshot ready',flush=True)
(root/'data/audits/algorithm-paper-build-20261121.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
