from dataclasses import asdict
from datetime import datetime,timedelta
from calendar import monthrange
import json
from .engine import Prepared,replay,rank_score
from .model import Strategy,utcnow

def month_shift(d,n):
 total=d.year*12+d.month-1+n;y,m=divmod(total,12);return d.replace(year=y,month=m+1,day=min(d.day,monthrange(y,m+1)[1]))

def grid(cfg):
 r=cfg['research'];out=[]
 for direction in ('小','大','双','单'):
  for d in r['thresholds']:
   for k in r['max_bets']:out.append(Strategy(direction=direction,threshold=d,max_bets=k,lookback=r['lookback'],base_cents=cfg['simulation']['fixed']['base_cents']))
  if r['include_reversal']:
   for length in r['run_lengths']:
    for k in r['max_bets']:out.append(Strategy(direction=direction,kind='reversal',run_length=length,max_bets=k,lookback=r['lookback'],base_cents=cfg['simulation']['fixed']['base_cents']))
 return out

def select_month(rows,cfg,as_of,progress=None):
 month=datetime.fromisoformat(as_of[:7]+'-01');r=cfg['research'];val_start=month_shift(month,-r['validation_months']);train_start=month_shift(val_start,-r['train_months']);f=lambda d:d.isoformat(timespec='seconds');prepared=Prepared(rows)
 # Completeness: no gaps or conflicts are allowed to be hidden by selection.
 eligible_rows=[x for x in rows if f(train_start)<=x['source_time']<f(month)]
 data_complete=bool(eligible_rows) and eligible_rows[0]['source_time'][:10]==f(train_start)[:10] and eligible_rows[-1]['source_time'][:10]==(month-timedelta(days=1)).isoformat()[:10]
 data_complete=data_complete and any(x['source_time']>=f(month) for x in rows)
 data_complete=data_complete and all(b['id']==a['id']+1 for a,b in zip(eligible_rows,eligible_rows[1:]))
 train=[];strategies=grid(cfg)
 for i,s in enumerate(strategies):
  q=replay(prepared,s,cfg['odds'],f(train_start),f(val_start),restart_policy=cfg['simulation'].get('round_restart','immediate'),win_limit=cfg['simulation'].get('win_limit',0),pause_draws=cfg['simulation'].get('pause_draws',20));q.pop('orders',None);train.append(q)
  if progress and i%10==0:progress(i,len(strategies))
 candidates=[];validation=[]
 for direction in ('小','大','双','单'):
  pool=sorted([q for q in train if q['strategy']['direction']==direction and q['bets']>=r['min_train_bets'] and q['net']>0],key=rank_score,reverse=True)[:r['top_train_candidates_per_direction']]
  for q in pool:
   v=replay(prepared,Strategy(**q['strategy']),cfg['odds'],f(val_start),f(month),restart_policy=cfg['simulation'].get('round_restart','immediate'),win_limit=cfg['simulation'].get('win_limit',0),pause_draws=cfg['simulation'].get('pause_draws',20));v.pop('orders',None)
   flat_params=q['strategy'].copy();flat_params['multiplier_hundredths']=100
   baseline=replay(prepared,Strategy(**flat_params),cfg['odds'],f(val_start),f(month),restart_policy=cfg['simulation'].get('round_restart','immediate'),win_limit=cfg['simulation'].get('win_limit',0),pause_draws=cfg['simulation'].get('pause_draws',20));baseline.pop('orders',None)
   passed=(not r.get('require_flat_profit',True) or baseline['net']>0) and data_complete and v['bets']>=r['min_validation_bets'] and v['net']>0 and v['roi']>r['minimum_validation_roi'] and v['max_drawdown']<=r['max_validation_drawdown']
   validation.append({'train':q,'validation':v,'flat_baseline':baseline,'passed':passed})
   if passed:candidates.append({'strategy':q['strategy'],'key':q['key'],'score':rank_score(v),'validation':v,'flat_baseline':baseline,'train':q})
 candidates.sort(key=lambda x:x['score'],reverse=True)
 return {'month':as_of[:7],'created':utcnow(),'train_start':f(train_start),'validation_start':f(val_start),'cutoff':f(month),'data_complete':data_complete,'grid_count':len(strategies),'candidates':candidates,'validation':validation,'train_results':train,'reason':'通过训练与验证门槛' if candidates else '无合格策略；暂停模拟（样本不足、数据不完整或收益/回撤不达标）','method':'过去3个月训练、过去1个月验证；收益/回撤评分；不是未来盈利保证'}

def store_selection(db,result,cfg):
 with db.tx() as c:
  unresolved=c.execute('SELECT COUNT(*) FROM conflicts WHERE resolved=0').fetchone()[0]
  if unresolved:result['candidates']=[];result['reason']='存在未解决的开奖冲突，暂停启用候选'
  cur=c.execute('INSERT INTO evaluations(created,as_of,status,parameters,result) VALUES(?,?,?,?,?)',(utcnow(),result['month'],'done',json.dumps(cfg['research'],ensure_ascii=False),json.dumps(result,ensure_ascii=False)))
  # A month's live selection is immutable: repeated research remains inspectable but cannot rewrite live decisions.
  if result.get('data_complete',True) and not unresolved:
   c.execute('INSERT OR IGNORE INTO selections VALUES(?,?,?,?,?)',(result['month'],utcnow(),cur.lastrowid,json.dumps(result['candidates'],ensure_ascii=False),result['reason']))
 return cur.lastrowid

def walk_forward(rows,cfg,start,end,progress=None):
 from .router import replay_portfolio
 start=datetime.fromisoformat(start[:7]+'-01');end=datetime.fromisoformat(end[:7]+'-01');reports=[];schedule={};month=start
 while month<end:
  result=select_month(rows,cfg,month.strftime('%Y-%m'),progress);schedule[result['month']]=result['candidates'];reports.append({k:v for k,v in result.items() if k not in ('train_results','validation')});month=month_shift(month,1)
 portfolio=replay_portfolio(rows,cfg,schedule,start.isoformat(),end.isoformat())
 return {'months':reports,'portfolio':portfolio,'note':'月度候选只使用该月开始前数据；组合一账户每期最多一单；月界完成旧轮后切换，风险限制有效。'}
