"""Member-facing strategy statistics. Never expose execution settings or decisions."""
from .strategies import catalog

METRICS=('bets','wins','refunds','win_rate','net','investment','roi','max_drawdown','pending_stake','balance')

def execution_states(db):
 # An existing draw with a pending order means settlement is still outstanding.
 source="""(SELECT 'legacy' strategy,target_draw FROM orders WHERE status='pending'
 UNION ALL SELECT strategy,target_draw FROM trend_orders WHERE status='pending'
 UNION ALL SELECT strategy,target_draw FROM custom_orders WHERE status='pending')"""
 rows=db.query('SELECT o.strategy,MAX(d.id IS NOT NULL) drawn FROM '+source+' o LEFT JOIN draws d ON d.id=o.target_draw GROUP BY o.strategy')
 return {r['strategy']:'等待结算' if r['drawn'] else '等待开奖' for r in rows}

def listing(service):
 with service.lock:
  rows=catalog(service.db,service.cfg)['strategies'];latest=service.db.tail(1);states=execution_states(service.db)
  result=[]
  for row in sorted(rows,key=lambda r:not r['enabled']):
   settings=row['settings'];base=settings.get('base_cents',settings.get('fixed',{}).get('base_cents'))
   saved=row.get('backtest');month=saved.get('one_month') if saved else None;backtest=None
   if month:
    coverage=month.get('coverage',{})
    backtest={'net':month['net'],'bets':month['bets'],'created':saved['created'],'parameters_changed':saved['parameters_changed'],'start':coverage.get('requested_start'),'end':coverage.get('requested_end'),'actual_start':coverage.get('actual_start'),'actual_end':coverage.get('actual_end')}
   result.append({'id':row['id'],'name':row['name'],'enabled':bool(row['enabled']),'state':states.get(row['id'],'等待入场'),'base_stake':base/100 if base is not None else None,'stats':{k:row['stats'].get(k) for k in METRICS},'month_backtest':backtest})
 return {'strategies':result,'latest_id':latest[-1]['id'] if latest else None,'note':'当前收益来自本地模拟订单累计已结算数据（含历史补单），待结算订单不计入收益。近1个月回测为后台最近一次保存的独立回放结果，范围以回测数据截止时间为准。'}
