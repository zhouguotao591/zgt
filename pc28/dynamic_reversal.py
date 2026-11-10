"""One-time, audited conversion of fixed-side settings to minority-axis entry.
Open round state and immutable historical order snapshots are deliberately retained.
"""
import copy,json
from . import config
from .model import utcnow

def axis_direction(direction):return '大小' if direction in ('大','小','大小') else '单双'

def migrate(db,root):
 cfg=config.load(root);old=copy.deepcopy(cfg);changed=[]
 fixed=cfg['simulation']['fixed'];target=axis_direction(fixed['direction'])
 if fixed['direction']!=target:fixed['direction']=target;changed.append('legacy')
 try:
  with db.tx() as c:
   rows=c.execute('SELECT * FROM custom_strategies WHERE id NOT IN (SELECT id FROM deleted_strategies)').fetchall()
   for r in rows:
    settings=json.loads(r['settings'])
    if settings.get('strategy_type'):continue
    fixed=settings['fixed'];target=axis_direction(fixed['direction'])
    if fixed['direction']==target:continue
    fixed['direction']=target;c.execute('UPDATE custom_strategies SET settings=? WHERE id=?',(json.dumps(settings,ensure_ascii=False),r['id']))
    c.execute('INSERT INTO strategy_edits(strategy,created,settings) VALUES(?,?,?)',(r['id'],utcnow(),json.dumps({'name':r['name'],'settings':settings,'migration':'minority-axis-v1','note':'下一轮入场时比较两边次数，买少的一方；原轮次和订单方向保留'},ensure_ascii=False)));changed.append(r['id'])
   if cfg!=old:
    config.save(root,cfg);c.execute('INSERT INTO settings_audit(created,config) VALUES(?,?)',(utcnow(),json.dumps(cfg,ensure_ascii=False)))
 except Exception:
  if cfg!=old:config.save(root,old)
  raise
 return changed
