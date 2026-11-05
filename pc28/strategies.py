"""Strategy catalog backed by the existing execution flags (one source of truth)."""
from . import trend_strategy
from .model import Strategy
import copy,json
from . import custom_strategy
IDS=('legacy','parity','size')
def catalog(db,cfg,tasks=None):
 trend=trend_strategy.status(db,cfg);settings=cfg['simulation'];account=db.query('SELECT * FROM accounts WHERE id=1')[0]
 state=json.loads(account['state']);orders=db.query('SELECT * FROM orders ORDER BY target_draw,id')
 normalized=[{**o,'hit':int(o['pnl']>=0) if o['pnl'] is not None else None,'outcome':json.loads(o['reason'] or '{}').get('settlement',{}).get('outcome')} for o in orders]
 stats=trend_strategy.summary(normalized,account['initial_balance'])
 latest=db.query('SELECT reason,action,target_draw FROM decisions ORDER BY target_draw DESC LIMIT 1');s=Strategy(**settings['fixed']);h=db.tail(s.lookback)
 counts={d:sum(r[s.axis]==d for r in h) for d in (('单','双') if s.axis=='parity' else ('大','小'))}
 pending=sum(o['status']=='pending' for o in orders)
 reason='已停用' if not settings['enabled'] else '账户已暂停：'+account['pause_reason'] if account['paused'] else '等待未结算订单开奖' if pending else '连胜暂停至'+str(state['pause_until'])+'期' if state.get('pause_until',0)>(db.tail(1)[-1]['id'] if db.tail(1) else 0) else ('本轮进行中' if state.get('active') else latest[0]['reason'] if latest else '等待下一期检查')
 rules=f"最近{s.lookback}期 · 买{s.direction} · 差值阈值{s.threshold} · 基础{s.base_cents/100:g}U · 亏后{s.multiplier_hundredths/100:g}倍 · 连亏{s.max_losses}停轮 · 每轮最多{s.max_bets}单，满单后重判入场"+(f"；连续盈利{settings['win_limit']}单暂停{settings.get('pause_draws',20)}期" if settings.get('win_limit',0) else '')
 rows=[{'id':'legacy','name':'三十期买单反转策略','enabled':settings['enabled'],'paused':bool(account['paused']),'status':reason,'rules':rules,'window_counts':counts,'mode':settings['mode'],'stats':stats}]
 for axis in ('parity','size'):
  t=trend['strategies'][axis];d=db.query('SELECT action,reason FROM trend_decisions WHERE strategy=? ORDER BY target_draw DESC LIMIT 1',(axis,))
  rule=trend_strategy.settings(cfg,axis)
  rows.append({'editable':True,'editor_type':'trend','settings':rule,'id':axis,'name':t['name'],'enabled':t['enabled'],'paused':False,'status':('已停用' if not t['enabled'] else '等待开奖' if t['pending_stake'] else '连胜暂停，剩余'+str(t['pause_remaining'])+'期' if t['pause_remaining'] else d[0]['reason'] if d else '等待下一期检查'),'rules':f"前{rule['lookback']}期占优方向，持平沿用上一笔；基础{rule['base_cents']/100:g}U，亏后加{rule['loss_increment_cents']/100:g}U，命中或{rule['max_losses']}连亏后恢复基础金额"+(f"；连续盈利{rule['win_limit']}单暂停{rule['pause_draws']}期" if rule['win_limit'] else ''),'stats':t})
 rows[0].update(editable=True,settings={k:settings.get(k,0 if k=='win_limit' else 20) for k in ('mode','initial_balance','daily_loss_limit','max_drawdown','max_stake','round_restart','fixed','win_limit','pause_draws')})
 rows.extend(custom_strategy.catalog(db))
 from .strategy_delete import deleted_ids
 removed=deleted_ids(db);rows=[r for r in rows if r['id'] not in removed]
 if not db.query('SELECT 1 FROM conflicts WHERE resolved=0 LIMIT 1'):
  for row in rows:
   if row['enabled'] and row['status'] in ('存在待核验的数据冲突','存在未解决的数据冲突'):row['status']='冲突已核验，等待下一期重新评估'
 summaries={r['strategy']:r for r in db.query('SELECT * FROM strategy_backtest_summary')}
 for r in rows:
  saved=summaries.get(r['id']);r['backtest']=None
  if saved:
   value=json.loads(saved['summary']);snapshot=value['settings'];r['backtest']={'run_id':saved['run_id'],'created':saved['created'],**value['window_returns'],'parameters_changed':any(snapshot.get(k)!=v for k,v in r['settings'].items())}
  task=next((t for t in reversed(list((tasks or {}).values())) if t.get('strategy_id')==r['id'] and t['kind']=='history_backtest'),None)
  r['backtest_task']={k:task.get(k) for k in ('id','status','progress','percent','error')} if task else None
 return {'strategies':rows,'note':'停用停止新订单，已有订单继续结算；历史统计保留。'}
def control(service,body):
 key=body.get('id')
 from .strategy_delete import require_available
 require_available(service.db,key)
 if key not in IDS:
  if type(body.get('enabled')) is not bool:raise ValueError('enabled必须为布尔值')
  with service.lock,service.db.tx() as c:
   require_available(service.db,key)
   if not c.execute('SELECT 1 FROM custom_strategies WHERE id=?',(key,)).fetchone():raise ValueError('策略不存在')
   c.execute('UPDATE custom_strategies SET enabled=? WHERE id=?',(int(body['enabled']),key))
  return {'ok':True}
 if type(body.get('enabled')) is not bool:raise ValueError('enabled必须为布尔值')
 with service.lock:
  cfg=copy.deepcopy(service.cfg)
  if key=='legacy':cfg['simulation']['enabled']=body['enabled']
  else:
   axes=set(cfg['trend_simulation']['enabled_axes'])
   if body['enabled']:axes.add(key)
   else:axes.discard(key)
   cfg['trend_simulation']['enabled_axes']=[k for k in ('parity','size') if k in axes]
  service.update_config(cfg)
 return {'ok':True}


def order_page(db,cfg,axis='all',page=1,page_size=20,active_only=True):
 from contextlib import closing
 custom=db.query('SELECT id,enabled FROM custom_strategies');ids=(*IDS,*(r['id'] for r in custom))
 if axis not in ('all',*ids):raise ValueError('无效策略筛选')
 page=int(page);page_size=int(page_size)
 if page_size not in (10,20,50,100):raise ValueError('每页条数必须为10、20、50、100')
 allowed=list(ids) if not active_only else (['legacy'] if cfg['simulation']['enabled'] else [])+cfg['trend_simulation']['enabled_axes']+[r['id'] for r in custom if r['enabled']]
 from .strategy_delete import deleted_ids
 removed=deleted_ids(db)
 if active_only:allowed=[k for k in allowed if k not in removed]
 enabled=list(allowed)
 if axis!='all':allowed=[k for k in allowed if k==axis]
 source="""(SELECT id,strategy,target_draw,cutoff_draw,created,direction,ratio,stake,status,pnl,hit,outcome,cycle,step,NULL max_bets,NULL strategy_json,NULL reason FROM trend_orders
 UNION ALL SELECT id,'legacy',target_draw,cutoff_draw,created,NULL,NULL,stake,status,pnl,NULL,NULL,round_id,round_attempt,NULL,strategy_json,reason FROM orders
 UNION ALL SELECT id,strategy,target_draw,cutoff_draw,created,direction,ratio,stake,status,pnl,hit,outcome,cycle,step,max_bets,strategy_json,NULL reason FROM custom_orders)"""
 where='strategy IN ('+','.join('?' for _ in allowed)+')' if allowed else '0'
 with closing(db.connect()) as c:
  c.execute('BEGIN');total=c.execute('SELECT COUNT(*) FROM '+source+' WHERE '+where,allowed).fetchone()[0]
  pages=max(1,(total+page_size-1)//page_size);page=max(1,min(page,pages))
  rows=[dict(r) for r in c.execute('SELECT * FROM '+source+' WHERE '+where+' ORDER BY target_draw DESC,strategy,id DESC LIMIT ? OFFSET ?',(*allowed,page_size,(page-1)*page_size))]
 backfilled={r['order_id'] for r in db.query("SELECT order_id FROM gap_order_tags WHERE engine='custom'")}
 for r in rows:
  r['backfilled']=r['strategy'] not in IDS and r['id'] in backfilled
  if r['strategy']=='legacy':
   st=json.loads(r.pop('strategy_json'));paid=json.loads(r.pop('reason') or '{}').get('settlement',{})
   r.update(direction=st['direction'],max_bets=st['max_bets'],outcome=paid.get('outcome'),hit=(int(r['pnl']>=0) if r['pnl'] is not None else None))
 return {'orders':rows,'total':total,'page':page,'pages':pages,'page_size':page_size,'enabled_strategies':enabled,'strategy_names':{k:v for k,v in {'legacy':'三十期买单反转策略','parity':'单双策略','size':'大小策略',**{r['id']:r['name'] for r in db.query('SELECT id,name FROM custom_strategies')}}.items() if not active_only or k not in removed}}
