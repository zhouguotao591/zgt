from dataclasses import asdict
from . import win_pause
from bisect import bisect_left
from .model import Strategy,payout,rounded_ratio,wilson_interval,settlement,stake_limit_reason

class Prepared:
 def __init__(self,rows):
  self.rows=rows;self.times=[r['source_time'] for r in rows];self.prefix={};self.runs={}
  if any(b<a for a,b in zip(self.times,self.times[1:])):raise ValueError('源时间非单调；请核实时区/夏令时并规范化后再研究')
  for axis,pos in [('size','大'),('parity','单')]:
   p=[0];runs=[0];last=None;n=0
   for r in rows:
    v=r[axis];p.append(p[-1]+(v==pos));n=n+1 if v==last else 1;runs.append(n);last=v
   self.prefix[axis]=p;self.runs[axis]=runs
  self.breaks=[0]
  for i,r in enumerate(rows):self.breaks.append(self.breaks[-1]+int(i>0 and r['id']!=rows[i-1]['id']+1))
 def signal(self,s,i):
  if i<s.lookback or self.breaks[i]-self.breaks[i-s.lookback+1]:return False
  if self.rows[i-1]['id']+1!=self.rows[i]['id']:return False
  if s.direction in ('大小','单双'):return s.triggered(self.rows[i-s.lookback:i])
  p=self.prefix[s.axis];n=p[i]-p[i-s.lookback]
  difference=2*n-s.lookback if s.direction in ('小','双') else s.lookback-2*n
  return difference>=s.threshold and (not s.run_length or self.runs[s.axis][i]>=s.run_length and self.rows[i-1][s.axis]!=s.direction)

def replay(prepared,strategy,odds,start=None,end=None,initial_cents=None,max_stake=None,daily_limit=None,dd_limit=None,trace=False,restart_policy='immediate',win_limit=0,pause_draws=20,win_loss_difference_limit=0):
 rows=prepared.rows;s=strategy;lo=bisect_left(prepared.times,start) if start else 0;hi=bisect_left(prepared.times,end) if end else len(rows)
 net=peak=dd=bets=wins=invest=closed=profitable=stops=maxloss=losstrade=highest=capital=0
 pause_state={};refunds=profit_wins=0;waiting_rearm=False;active=False;loss=attempt=roundpnl=0;stake=s.base_cents;out=[];halt='';daily=0;day='';skips=0
 for i in range(max(lo,s.lookback),hi):
  r=rows[i]
  if win_pause.paused(pause_state,r['id']):continue
  if i and rows[i-1]['id']+1!=r['id']:active=False;loss=attempt=0
  if not active:
   if waiting_rearm:
    if not prepared.signal(s,i):waiting_rearm=False
    continue
   if not prepared.signal(strategy,i):continue
   s=strategy.resolve(rows[max(0,i-strategy.lookback):i])
   active=True;loss=attempt=roundpnl=0;stake=s.base_cents
  if day!=r['source_time'][:10]:day=r['source_time'][:10];daily=0
  market_limit=stake_limit_reason(stake,odds)
  if market_limit:halt=market_limit;break
  if initial_cents is not None:
   if initial_cents+net<stake:halt='insufficient_balance';break
   if max_stake is not None and stake>max_stake:halt='stake_limit';break
   if daily_limit is not None and daily_limit>0 and daily-stake < -daily_limit:active=False;skips+=1;continue
   if dd_limit is not None and dd_limit>0 and peak-net+stake>dd_limit:halt='drawdown_limit';break
  hit=r[s.axis]==s.direction;paid=settlement(stake,s.direction,r['total'],hit,odds);pnl=paid['pnl'];refunds+=paid['outcome']=='refund';profit_wins+=paid['outcome']=='win'
  used=stake;capital=max(capital,used-net);bets+=1;wins+=hit;invest+=used;highest=max(highest,used);net+=pnl;daily+=pnl;peak=max(peak,net);dd=max(dd,peak-net);roundpnl+=pnl;attempt+=1;losstrade=0 if hit else losstrade+1;maxloss=max(maxloss,losstrade)
  if hit:loss=0;stake=s.base_cents
  else:loss+=1;stake=rounded_ratio(stake,s.multiplier_hundredths)
  win_pause.settled(pause_state,paid['outcome'],r['id'],win_limit,pause_draws, win_loss_difference_limit);win_stop=(pause_state.get('difference_paused',False) or pause_state.get('pause_until',0)>r['id'])
  ended=(s.max_losses>0 and loss>=s.max_losses) or attempt>=s.max_bets or win_stop
  if trace:out.append({'draw':r['id'],'time':r['source_time'],'direction':s.direction,'stake':used/100,'hit':bool(hit),'outcome':paid['outcome'],'return':paid['credit']/100,'settlement_rule':paid['rule'],'pnl':pnl/100,'equity':net/100,'attempt':attempt,'losses':loss,'round_end':ended})
  if ended:closed+=1;profitable+=roundpnl>0;stops+=(s.max_losses>0 and loss>=s.max_losses);active=False;waiting_rearm=not win_stop and (s.max_losses>0 and loss>=s.max_losses) and restart_policy=='condition_reset'
 return {'restart_policy':restart_policy,'odds':odds.copy(),'refunds':refunds,'profitable_wins':profit_wins,'profit_win_rate':profit_wins/bets if bets else None,'strategy':asdict(strategy),'key':strategy.key,'bets':bets,'wins':wins,'win_rate':wins/bets if bets else None,'win_rate_ci95':wilson_interval(wins,bets),'investment':invest/100,'net':net/100,'roi':net/invest if invest else None,'max_drawdown':dd/100,'max_stake':highest/100,'minimum_path_capital':capital/100,'closed_rounds':closed,'profitable_rounds':profitable,'loss_stops':stops,'longest_trade_loss':maxloss,'halt':halt,'risk_skips':skips,'orders':out if trace else [],'segment_reset':True}

def rank_score(r):
 # Profit adjusted for realized drawdown; not a forecast probability.
 return r['net']/max(100,r['max_drawdown'])
