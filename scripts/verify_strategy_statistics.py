import sys,json,sqlite3,time
from pathlib import Path
root=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root))
from pc28.trend_strategy import stored_summaries,summary
cfg=json.loads((root/'config.json').read_text(encoding='utf-8'));c=sqlite3.connect((root/cfg['database']).resolve().as_uri()+'?mode=ro',uri=True);c.row_factory=sqlite3.Row;c.execute('BEGIN')
class Read:
 def query(self,sql,args=()):return [dict(r) for r in c.execute(sql,args)]
db=Read();results=[]
for table in ('custom_orders','trend_orders'):
 actual=stored_summaries(db,table);orders=db.query('SELECT strategy,target_draw,status,pnl,stake,hit,outcome FROM '+table+' ORDER BY target_draw,id');checked=0
 for key,stats in actual.items():
  expected=summary([o for o in orders if o['strategy']==key],0)
  for field,value in stats.items():
   if expected[field]!=value:raise AssertionError((table,key,field,expected[field],value))
  checked+=1
 results.append({'table':table,'strategies':checked,'orders':len(orders),'all_totals_drawdown_and_last500_curve_points_equal':True})
c.close();(root/'data/audits/strategy-statistics-20261121.json').write_text(json.dumps(results,indent=2),encoding='utf-8');print(json.dumps(results))
