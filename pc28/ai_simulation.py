"""Independent paper ledger. Never writes strategy accounts or robot queues."""
import json
from datetime import datetime, timezone
from .model import settlement, utcnow

STAKES=(50000,60000,75000)
SCHEMA='''CREATE TABLE IF NOT EXISTS ai_paper_orders(
 axis TEXT NOT NULL,target_draw INTEGER NOT NULL,created TEXT NOT NULL,
 direction TEXT NOT NULL,stake INTEGER NOT NULL,odds TEXT NOT NULL,
 probability REAL NOT NULL,total INTEGER,actual TEXT,hit INTEGER,pnl INTEGER,
 outcome TEXT,resolved TEXT,PRIMARY KEY(axis,target_draw));'''

def next_stake(rows):
 index=0
 for row in rows:
  if row.get('outcome') in ('win','refund'):index=0
  elif row.get('outcome')=='loss':index=(index+1)%3
 return STAKES[index]

def summary(rows):
 settled=[r for r in rows if r.get('pnl') is not None]
 equity=peak=drawdown=run=longest=0
 for r in settled:
  equity+=r['pnl'];peak=max(peak,equity);drawdown=max(drawdown,peak-equity)
  run=run+1 if not r['hit'] else 0;longest=max(longest,run)
 investment=sum(r['stake'] for r in settled)
 return {'count':len(settled),'hits':sum(r['hit'] for r in settled),
 'hit_rate':sum(r['hit'] for r in settled)/len(settled) if settled else None,
 'investment':investment,'net':equity,'roi':equity/investment if investment else None,
 'max_drawdown':drawdown,'max_losses':longest}

class PaperSimulation:
 def __init__(self,service):
  self.s=service;self.db=service.db
  with self.db.tx() as c:c.execute(SCHEMA)
 def tick(self):
  with self.db.tx() as c:
   pending=c.execute('SELECT o.*,d.total draw_total,d.size,d.parity,d.draw_utc,p.eligible FROM ai_paper_orders o JOIN draws d ON d.id=o.target_draw JOIN ai_predictions p ON p.axis=o.axis AND p.target_draw=o.target_draw WHERE o.resolved IS NULL').fetchall()
   for r in pending:
    valid=r['eligible'] and datetime.fromisoformat(r['created'])<datetime.fromisoformat(r['draw_utc'])<=datetime.now(timezone.utc)
    if c.execute('SELECT 1 FROM conflicts WHERE draw_id=? AND resolved=0',(r['target_draw'],)).fetchone():continue
    actual=r[r['axis']];hit=int(actual==r['direction'])
    result=settlement(r['stake'],r['direction'],r['draw_total'],hit,json.loads(r['odds'])) if valid else {'pnl':None,'outcome':'void'}
    c.execute('UPDATE ai_paper_orders SET total=?,actual=?,hit=?,pnl=?,outcome=?,resolved=? WHERE axis=? AND target_draw=?',(r['draw_total'],actual,hit if valid else None,result['pnl'],result['outcome'],utcnow(),r['axis'],r['target_draw']))
 def open(self,pred):
  if pred['probability']==.5:return
  axis=pred['axis'];positive,negative=('大','小') if axis=='size' else ('单','双')
  with self.db.tx() as c:
   if c.execute('SELECT 1 FROM ai_paper_orders WHERE axis=? AND resolved IS NULL',(axis,)).fetchone():return
   rows=[dict(r) for r in c.execute('SELECT outcome FROM ai_paper_orders WHERE axis=? ORDER BY target_draw',(axis,))]
   c.execute('INSERT OR IGNORE INTO ai_paper_orders(axis,target_draw,created,direction,stake,odds,probability) VALUES(?,?,?,?,?,?,?)',(axis,pred['target_draw'],utcnow(),positive if pred['probability']>.5 else negative,next_stake(rows),json.dumps(self.s.cfg['odds']),pred['probability']))
 def public(self,axis,model_id,window):
  live=self.db.query('SELECT * FROM ai_paper_orders WHERE axis=? ORDER BY target_draw',(axis,))
  pending=next((r for r in reversed(live) if not r['resolved']),None)
  selected=[r for r in live if r['resolved']][-window:]
  # Historical predictions are walk-forward validation, explicitly separate from live orders.
  history=self.db.query('SELECT v.record,d.total,d.size,d.parity FROM ai_validation v JOIN draws d ON d.id=v.target_draw WHERE v.model_id=? AND v.axis=? ORDER BY v.target_draw DESC LIMIT ?',(model_id,axis,window)) if model_id else []
  replay=[];positive,negative=('大','小') if axis=='size' else ('单','双')
  for row in reversed(history):
   v=json.loads(row['record']);p=v['p']
   if p==.5:continue
   direction=positive if p>.5 else negative;stake=next_stake(replay);hit=int(direction==row[axis]);result=settlement(stake,direction,row['total'],hit,self.s.cfg['odds'])
   replay.append({'target_draw':v['id'],'created':v['time'],'direction':direction,'probability':p,'stake':stake,'total':row['total'],'actual':row[axis],'hit':hit,**result})
  def clean(r):return {k:v for k,v in r.items() if k not in ('odds',)}
  return {'next_stake':pending['stake'] if pending else next_stake(live),'pending':clean(pending) if pending else None,
   'live':{'summary':summary(selected),'rows':[clean(r) for r in reversed(selected)]},
   'backtest':{'summary':summary(replay),'rows':list(reversed(replay))},'window':window}
