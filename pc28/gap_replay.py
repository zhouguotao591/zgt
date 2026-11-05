"""Explicit administrative replay of a repaired gap; never called by the scheduler.
Reuses live engines in a cutoff database. Actual outcomes are added only after decisions.
The operator must stop the service before apply. Existing orders are never rewritten.
"""
import copy,json,uuid,tempfile
from pathlib import Path
from datetime import datetime,timedelta,timezone
from unittest.mock import patch
from collections import Counter
from .db import DB
from . import custom_strategy,follow_strategy
from .model import utcnow,Strategy,settlement


def preview(db,cfg,first,last,folder):
 if cfg['simulation']['enabled'] or cfg['trend_simulation']['enabled_axes']:raise ValueError('本次补单仅处理已核实启用的独立策略，内置策略需另外恢复状态')
 if not first<=last or last-first>1000:raise ValueError('补单范围错误')
 if db.query('SELECT 1 FROM conflicts WHERE resolved=0'):raise ValueError('请先核验冲突')
 enabled=db.query('SELECT * FROM custom_strategies WHERE enabled=1 ORDER BY id')
 if not enabled:raise ValueError('没有启用的策略')
 if db.query('SELECT 1 FROM custom_orders WHERE target_draw>=? LIMIT 1',(first,)):raise ValueError('补单范围之后已有订单，不能直接改写历史账户路径')
 times=db.query('SELECT * FROM draws WHERE id BETWEEN ? AND ? ORDER BY id',(first-1,last))
 if [r['id'] for r in times]!=list(range(first-1,last+1)):raise ValueError('开奖范围未补齐')
 begin=datetime.fromisoformat(times[0]['draw_utc'])
 if any(datetime.fromisoformat(a['created'])>begin for a in enabled):raise ValueError('策略在断线后才创建，不能补为历史订单')
 if db.query('SELECT 1 FROM strategy_edits WHERE created>? LIMIT 1',(begin.isoformat(),)):raise ValueError('断线后修改过参数，需要逐期还原参数版本')
 if db.query('SELECT 1 FROM settings_audit WHERE created>? LIMIT 1',(begin.isoformat(),)):raise ValueError('断线后修改过系统参数，需要核对参数版本')
 result={'run_id':uuid.uuid4().hex,'created':utcnow(),'first':first,'last':last,'accounts_before':enabled,'orders':[],'accounts_after':[],'decisions':[],'summary':[],'notes':['仅断线前已启用且期间未修改参数的策略；状态及余额按断线前已结算订单恢复。','假设断线期及时收到开奖，使用当时参数、赔率、风险限制和连胜暂停；每次决策库中不含目标及以后结果。','历史补单单独标记，订单及账本各写入一次；不重写原有订单。']}
 with tempfile.TemporaryDirectory(dir=folder) as tmp:
  path=Path(tmp)/'replay.sqlite3';db.backup(path);sim=DB(path);custom_strategy.initialize(sim)
  with sim.tx() as c:
   c.execute('DELETE FROM draws WHERE id>=?',(first,))
   c.execute('DELETE FROM custom_decisions WHERE target_draw>=?',(first,))
   c.execute('UPDATE draws SET first_seen=draw_utc WHERE draw_utc IS NOT NULL')
   for a in enabled:
    settings=json.loads(a['settings']);st=json.loads(a['state'])
    # A resumed live tick may have closed the round solely because the gap was seen.
    # Restore only a round whose last real settlement is the immediate predecessor.
    if not settings.get('strategy_type') and st.get('last_settled_draw')==first-1:
     s=Strategy(**st['strategy']);active=st.get('losses',0)<s.max_losses and st.get('attempt',0)<s.max_bets and st.get('pause_until',0)<first
     st.update(active=active,waiting_rearm=not active and st.get('losses',0)>=s.max_losses and settings['round_restart']=='condition_reset')
    c.execute('UPDATE custom_strategies SET state=? WHERE id=?',(json.dumps(st,ensure_ascii=False),a['id']))
  keys=[a['id'] for a in enabled]
  for prev,outcome in zip(times,times[1:]):
   prev_time=datetime.fromisoformat(prev['draw_utc']);target_time=datetime.fromisoformat(outcome['draw_utc'])
   hour_start=target_time.replace(minute=0,second=0,microsecond=0)
   decision=prev_time+timedelta(seconds=1)
   if not prev_time<decision<target_time:raise ValueError('开奖时间或决策时窗异常')
   with sim.tx() as c:
    timer=json.dumps({'next_draw_id':outcome['id'],'next_draw_at':target_time.isoformat()})
    c.execute("INSERT INTO runtime_state(key,value,updated) VALUES('draw_timer',?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(timer,decision.isoformat()))
   assert sim.tail(1)[-1]['id']==outcome['id']-1
   with patch('pc28.custom_strategy.utcnow',return_value=decision.isoformat()),patch('pc28.follow_strategy.utcnow',return_value=decision.isoformat()):custom_strategy.tick(sim,cfg,decision)
   # Hourly strategies may defer a crossing-hour target until the hour closes.
   # Other strategies already decided immediately after the previous result.
   if decision<hour_start<target_time:
    with patch('pc28.custom_strategy.utcnow',return_value=hour_start.isoformat()),patch('pc28.follow_strategy.utcnow',return_value=hour_start.isoformat()):custom_strategy.tick(sim,cfg,hour_start)
   with sim.tx() as c:
    r={**outcome,'first_seen':outcome['draw_utc']};columns=list(r)
    c.execute('INSERT INTO draws('+','.join(columns)+') VALUES('+','.join('?' for _ in columns)+')',tuple(r[k] for k in columns))
    c.execute('UPDATE custom_strategies SET enabled=0')
   settled_at=target_time+timedelta(seconds=1)
   with patch('pc28.custom_strategy.utcnow',return_value=settled_at.isoformat()),patch('pc28.follow_strategy.utcnow',return_value=settled_at.isoformat()):custom_strategy.tick(sim,cfg,settled_at)
   with sim.tx() as c:c.executemany('UPDATE custom_strategies SET enabled=1 WHERE id=?',[(k,) for k in keys])
  result['orders']=sim.query('SELECT * FROM custom_orders WHERE target_draw BETWEEN ? AND ? ORDER BY target_draw,strategy',(first,last))
  result['accounts_after']=[a for a in sim.query('SELECT * FROM custom_strategies') if a['id'] in keys]
  result['decisions']=sim.query('SELECT * FROM custom_decisions WHERE target_draw BETWEEN ? AND ? ORDER BY target_draw,strategy',(first,last))
  for a in enabled:
   orders=[o for o in result['orders'] if o['strategy']==a['id']];after=next(x for x in result['accounts_after'] if x['id']==a['id']);net=sum(o['pnl'] for o in orders)
   assert after['balance']==a['balance']+net
   assert all(o['status']=='settled' and o['cutoff_draw']<o['target_draw'] for o in orders)
   skips=Counter(d['reason'] for d in result['decisions'] if d['strategy']==a['id'] and d['action']=='skip')
   result['summary'].append({'id':a['id'],'name':a['name'],'orders':len(orders),'net':net/100,'before':a['balance']/100,'after':after['balance']/100,'skips':dict(skips)})
 return result


def rebase_preserving_live_orders(db,plan):
 """Post gap-only adjustments while retaining actual later orders and their state."""
 plan=copy.deepcopy(plan)
 if db.query('SELECT 1 FROM custom_orders WHERE target_draw BETWEEN ? AND ? LIMIT 1',(plan['first'],plan['last'])):raise ValueError('缺口已存在订单')
 later=db.query('SELECT * FROM custom_orders WHERE target_draw>? ORDER BY id',(plan['last'],))
 plan['preserved_later_orders']=later;original={a['id']:a for a in plan['accounts_before']}
 current={a['id']:a for a in db.query('SELECT * FROM custom_strategies WHERE enabled=1')}
 if current.keys()!=original.keys():raise ValueError('启用策略已变化')
 after={a['id']:a for a in plan['accounts_after']}
 for key,a in current.items():
  if a['settings']!=original[key]['settings']:raise ValueError('策略参数已变化')
  net=sum(o['pnl'] for o in plan['orders'] if o['strategy']==key)
  end=after[key];end['balance']=a['balance']+net;end['peak']=max(a['peak'],end['peak'])
  if any(o['strategy']==key for o in later):
   state=json.loads(a['state']);replayed=json.loads(end['state'])
   state['pause_until']=max(state.get('pause_until',0),replayed.get('pause_until',0));end['state']=json.dumps(state,ensure_ascii=False)
  summary=next(r for r in plan['summary'] if r['id']==key);summary.update(before=a['balance']/100,after=end['balance']/100)
 plan['accounts_before']=list(current.values());plan['accounts_after']=list(after.values())
 plan['notes'].append('断线后已实际生成的订单及结算全部保留；补单作为独立历史调整入账，不追溯改写后续订单。已有后续订单的策略保留当前轮次状态，并合并历史补单触发的较长暂停。')
 return plan


def apply(db,plan):
 # All writes, including account deltas and audit markers, form a single transaction.
 custom_strategy.initialize(db)
 with db.tx() as c:
  if c.execute('SELECT 1 FROM gap_order_backfills WHERE run_id=?',(plan['run_id'],)).fetchone():raise ValueError('该批补单已执行')
  if c.execute('SELECT 1 FROM custom_orders WHERE target_draw BETWEEN ? AND ? LIMIT 1',(plan['first'],plan['last'])).fetchone():raise ValueError('已有订单，拒绝重复或覆盖历史路径')
  later=[dict(r) for r in c.execute('SELECT * FROM custom_orders WHERE target_draw>? ORDER BY id',(plan['last'],))]
  if later!=plan.get('preserved_later_orders',[]):raise ValueError('补单预览后新增或结算了后续订单，请重新核对')
  for a in plan['accounts_before']:
   current=dict(c.execute('SELECT * FROM custom_strategies WHERE id=?',(a['id'],)).fetchone())
   if current!=a:raise ValueError('预览后账户或参数已变化，请重新生成补单')
  for o in plan['orders']:
   if o['status']!='settled':raise ValueError('补单尚未结算')
   expected=settlement(o['stake'],o['direction'],o['total'],bool(o['hit']),json.loads(o['odds_json']))
   if expected['pnl']!=o['pnl']:raise ValueError('补单结算金额校验失败')
  # Preserve the original skip decisions; order provenance is explicit in its own audit table.
  for o in plan['orders']:
   columns=[k for k in o if k!='id'];cur=c.execute('INSERT INTO custom_orders('+','.join(columns)+') VALUES('+','.join('?' for _ in columns)+')',tuple(o[k] for k in columns));order_id=cur.lastrowid
   a=c.execute('SELECT balance FROM custom_strategies WHERE id=?',(o['strategy'],)).fetchone();balance=a['balance']-o['stake']
   c.execute('INSERT INTO custom_ledger(strategy,order_id,created,kind,amount,balance) VALUES(?,?,?,?,?,?)',(o['strategy'],order_id,utcnow(),'stake',-o['stake'],balance))
   balance+=o['stake']+o['pnl']
   c.execute('INSERT INTO custom_ledger(strategy,order_id,created,kind,amount,balance) VALUES(?,?,?,?,?,?)',(o['strategy'],order_id,utcnow(),'settle',o['stake']+o['pnl'],balance))
   c.execute('UPDATE custom_strategies SET balance=? WHERE id=?',(balance,o['strategy']))
   c.execute('INSERT INTO gap_order_tags VALUES(?,?,?)',('custom',order_id,plan['run_id']))
  for a in plan['accounts_after']:
   actual=c.execute('SELECT balance FROM custom_strategies WHERE id=?',(a['id'],)).fetchone()[0]
   if actual!=a['balance']:raise ValueError('补单后余额对账失败')
   c.execute('UPDATE custom_strategies SET peak=?,state=? WHERE id=?',(a['peak'],a['state'],a['id']))
  audit={k:v for k,v in plan.items() if k not in ('accounts_before','accounts_after','orders','decisions')}
  c.execute('INSERT INTO gap_order_backfills VALUES(?,?,?,?,?)',(plan['run_id'],utcnow(),plan['first'],plan['last'],json.dumps(audit,ensure_ascii=False)))
 return audit
