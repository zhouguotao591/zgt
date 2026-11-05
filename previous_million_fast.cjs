// Exact integer-cent implementation of the previous-draw follower.
function prepare(w){w.nextDay=new Int32Array(w.ids.length);for(let i=w.ids.length-1;i>=0;i--)w.nextDay[i]=(i+1===w.ids.length||w.days[i]!==w.days[i+1])?i+1:w.nextDay[i+1];return w;}
function replay(w,s,o){
 const c=s.settings,base=c.base_cents,floor=c.stake_floor==='base'?base:100,initial=Math.round(c.initial_balance*100),maxdd=Math.round(c.max_drawdown*100),maxstake=Math.round(c.max_stake*100),daylimit=Math.round(c.daily_loss_limit*100),labels=w[c.axis];
 let balance=initial,peak=initial,stake=base,daily=0,day=-1,pauseUntil=0,streak=0,inc=0,dec=0,bets=0,wins=0,refunds=0,investment=0,dd=0;
 for(let i=1;i<w.ids.length;i++){
  if(stake<o.min_stake_cents||stake>o.max_stake_cents||balance<stake||stake>maxstake||peak-balance+stake>maxdd)break;
  const id=w.ids[i];if(day!==w.days[i]){day=w.days[i];daily=0;}
  if(w.rest[i])continue;
  if(pauseUntil&&id<=pauseUntil){let lo=i+1,hi=w.ids.length;while(lo<hi){const m=(lo+hi)>>>1;if(w.ids[m]<=pauseUntil)lo=m+1;else hi=m;}i=lo-1;continue;}
  if(pauseUntil){pauseUntil=0;streak=0;}
  if(w.ids[i-1]+1!==id)continue;
  if(daily-stake < -daylimit){i=w.nextDay[i]-1;continue;}
  const hit=labels[i]===labels[i-1],total=w.totals[i],refund=hit&&o.refund_winning_totals.includes(total);let credit=0;
  if(hit){if(refund)credit=stake;else{const factor=o.full_payout_totals.includes(total)?o.normal:stake>=o.high_stake_threshold_cents?o.high_stake_factor:(total===13||total===14)?o[c.axis==='size'?'special_size':'special_parity']:o.normal;credit=Math.min(Math.floor((stake*factor+50)/100),o.max_return_per_draw_cents);}}
  const pnl=credit-stake;balance+=pnl;daily+=pnl;peak=Math.max(peak,balance);dd=Math.max(dd,peak-balance);bets++;wins+=hit?1:0;refunds+=refund?1:0;investment+=stake;
  streak=hit&&!refund?streak+1:0;if(c.win_limit&&streak>=c.win_limit)pauseUntil=id+c.pause_draws;
  if(refund){inc=0;dec=0;continue;}
  const increasing=!hit,next=increasing?stake+c.loss_increment_cents:Math.max(floor,stake-c.win_decrement_cents),limit=increasing?c.max_increase_steps:c.max_decrease_steps;
  if(increasing)dec=0;else inc=0;
  if(next===stake){if(increasing)inc=0;else dec=0;}
  else if(limit&&(increasing?inc:dec)>=limit){inc=0;dec=0;stake=base;}
  else{if(increasing)inc++;else dec++;stake=next;}
 }
 return {net:(balance-initial)/100,bets,wins,refunds,investment:investment/100,roi:investment?(balance-initial)/investment:null,max_drawdown:dd/100};
}
module.exports={prepare,replay};
