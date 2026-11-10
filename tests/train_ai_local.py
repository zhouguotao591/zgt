"""First offline model training; no order execution and no external messages."""
import sys,json
from pathlib import Path
from datetime import datetime
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from pc28.db import DB
from pc28.service import Service
root=Path(__file__).resolve().parent.parent;db=DB(root/'data/pc28.sqlite3');backup=root/'data/exports'/('before-ai-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'.sqlite3');db.backup(backup)
s=Service(root);report=s.ai.train_now()
if report is None:print(json.dumps(s.ai.state(),ensure_ascii=False));sys.exit(1)
s.ai.tick();path=root/'data/exports/ai-first-training.json';path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'backup':str(backup),'model_report':str(path),'cutoff':report['cutoff_id'],'quality':report['quality'],'axes':{k:v['evaluation'] for k,v in report['axes'].items()}},ensure_ascii=False))
