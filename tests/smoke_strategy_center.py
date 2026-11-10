import sys,json,threading,urllib.request,urllib.error
from pathlib import Path
from types import SimpleNamespace
root=Path(__file__).resolve().parent.parent;sys.path.insert(0,str(root))
from pc28.db import DB
from pc28.strategy_center import listing
cfg=json.loads((root/'config.json').read_text(encoding='utf-8'));base='http://127.0.0.1:'+str(cfg['port'])
with urllib.request.urlopen(base+'/') as r:page=r.read().decode();assert r.status==200
assert page.index('data-tab="algorithm-hall"')<page.index('data-tab="strategy-center"')
try:urllib.request.urlopen(base+'/api/strategy-center');raise AssertionError('guest must not read strategy center')
except urllib.error.HTTPError as e:assert e.code==401
for asset in ('strategy-center.js','strategy-center.css'):
 with urllib.request.urlopen(base+'/'+asset) as r:assert r.status==200
data=listing(SimpleNamespace(db=DB(root/cfg['database']),cfg=cfg,lock=threading.RLock()))
for r in data['strategies']:assert set(r)=={'id','name','enabled','state','base_stake','stats'}
print(json.dumps({'home':200,'guest_strategy_center':401,'assets':200,'configured_strategies':len(data['strategies']),'latest_id':data['latest_id'],'core_parameters_exposed':False},ensure_ascii=False))
