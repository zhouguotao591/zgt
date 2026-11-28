"""Hourly views over one continuous, immutable historical replay."""
import json
from datetime import datetime, timedelta
from .history_backtest import BJ


def local(value):
 t=datetime.fromisoformat(value)
 return t.replace(tzinfo=BJ) if t.tzinfo is None else t.astimezone(BJ)


def aggregate(orders,coverage,order_type=''):
 start=local(coverage['actual_start']).replace(minute=0,second=0,microsecond=0)
 end=local(coverage['actual_end']).replace(minute=0,second=0,microsecond=0)
 def empty(key):return dict(key=key,bets=0,wins=0,losses=0,refunds=0,hits=0,stake=0,pnl=0)
 hours={};cursor=start
 while cursor<=end:
  hours[cursor.isoformat()]=empty(cursor.isoformat());cursor+=timedelta(hours=1)
 daily={str(h):empty(str(h)) for h in range(24)};types=set()
 for o in orders:
  kind=o.get('previous_type') or o['direction'];types.add(kind)
  if order_type and kind!=order_type:continue
  t=local(o['beijing_time']);key=t.replace(minute=0,second=0,microsecond=0).isoformat()
  for b in (hours.setdefault(key,empty(key)),daily[str(t.hour)]):
   b['bets']+=1;b['wins']+=int(o['outcome']=='win');b['losses']+=int(o['outcome']=='loss');b['refunds']+=int(o['outcome']=='refund')
   b['hits']+=int(bool(o.get('hit')));b['stake']+=o['stake'];b['pnl']+=o['pnl']
 def finish(b):
  return {**b,'win_rate':b['wins']/b['bets'] if b['bets'] else None,'hit_rate':b['hits']/b['bets'] if b['bets'] else None,'roi':b['pnl']/b['stake'] if b['stake'] else None,'stake':b['stake']/100,'pnl':b['pnl']/100,'low_sample':0<b['bets']<30}
 return dict(hours=[finish(b) for b in reversed(list(hours.values()))],clock=[finish(b) for b in daily.values()],types=sorted(types))


def page(db,run_id,mode='hours',slot='',order_type='',page=1):
 if mode not in ('hours','clock'):raise ValueError('不支持的小时视图')
 page=max(1,int(page))
 from contextlib import closing
 with closing(db.connect()) as c:
  c.execute('BEGIN')
  row=c.execute('SELECT result FROM history_backtest_runs WHERE id=?',(run_id,)).fetchone()
  if not row:raise ValueError('回测结果不存在')
  result=json.loads(row[0]);orders=[json.loads(r[0]) for r in c.execute('SELECT record FROM history_backtest_orders WHERE run_id=? ORDER BY seq',(run_id,))]
 report=aggregate(orders,result['coverage'],order_type)
 selected=[]
 if slot:
  if mode=='clock' and slot not in [str(h) for h in range(24)]:raise ValueError('小时无效')
  if mode=='hours':slot=local(slot).replace(minute=0,second=0,microsecond=0).isoformat()
  for o in orders:
   t=local(o['beijing_time']);key=str(t.hour) if mode=='clock' else t.replace(minute=0,second=0,microsecond=0).isoformat()
   if key==slot and (not order_type or (o.get('previous_type') or o['direction'])==order_type):selected.append(o)
 total=len(selected);pages=max(1,(total+49)//50);page=min(page,pages)
 return {**report,'result':result,'orders':list(reversed(selected))[50*(page-1):50*page],'total':total,'page':page,'pages':pages}
