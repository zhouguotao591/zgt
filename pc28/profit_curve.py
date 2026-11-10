"""Cumulative P&L from settled live order tables, never backtest results."""
from .model import utcnow

def result(db,key,include_backfill=True):
 names={'legacy':'三十期买单反转策略','size':'大小策略','parity':'单双策略'};custom=db.query('SELECT id,name,initial_balance,balance FROM custom_strategies WHERE id=?',(key,))
 if key not in names and not custom:raise ValueError('策略不存在')
 if key=='legacy':
  orders=db.query('SELECT id,target_draw,stake,status,pnl,settled FROM orders ORDER BY target_draw,id');account=db.query('SELECT initial_balance,balance FROM accounts WHERE id=1')[0];name=names[key];tagged=set()
 elif key in ('size','parity'):
  orders=db.query('SELECT id,target_draw,stake,status,pnl,settled FROM trend_orders WHERE strategy=? ORDER BY target_draw,id',(key,));account=db.query('SELECT initial_balance,balance FROM trend_accounts WHERE strategy=?',(key,))[0];name=names[key];tagged=set()
 else:
  orders=db.query('SELECT id,target_draw,stake,status,pnl,settled FROM custom_orders WHERE strategy=? ORDER BY target_draw,id',(key,));account=custom[0];name=account['name'];tagged={r['order_id'] for r in db.query("SELECT order_id FROM gap_order_tags WHERE engine='custom'")}
 settled=[o for o in orders if o['status']=='settled' and o['pnl'] is not None and (include_backfill or o['id'] not in tagged)]
 net=peak=drawdown=0;points=[]
 for o in settled:
  net+=o['pnl'];peak=max(peak,net);drawdown=max(drawdown,peak-net);points.append({'draw':o['target_draw'],'time':o['settled'],'pnl':o['pnl']/100,'net':net/100,'stake':o['stake']/100,'backfilled':o['id'] in tagged})
 # Preserve bucket minima/maxima if a mature account has a large number of orders.
 if len(points)>2000:
  sampled=[points[0]];step=max(1,(len(points)+997)//998)
  for start in range(1,len(points)-1,step):
   bucket=points[start:min(len(points)-1,start+step)];indices=sorted({min(range(len(bucket)),key=lambda i:bucket[i]['net']),max(range(len(bucket)),key=lambda i:bucket[i]['net'])});sampled.extend(bucket[i] for i in indices)
  sampled.append(points[-1]);points=sampled
 return {'id':key,'name':name,'created':utcnow(),'include_backfill':include_backfill,'orders':len(settled),'net':net/100,'max_drawdown':drawdown/100,'backfill_count':sum(o['id'] in tagged for o in settled),'pending':sum(o['status']=='pending' for o in orders),'initial_balance':account['initial_balance']/100,'balance':account['balance']/100,'points':points,'source':'已结算模拟订单累计净收益；不包含历史回测。待结算订单不计入曲线。'}
