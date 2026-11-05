// Integer-cent replay, validated against pc28.history_backtest before publication.
const fs=require('fs'),{Worker,isMainThread,parentPort,workerData}=require('worker_threads');
function replay(w,s,o){
 const c=s.settings,f=c.fixed||c,rev=s.kind==='reversal',hourly=s.kind==='hourly',axis=rev?(['大','小'].includes(f.direction)?'size':'parity'):c.axis;
 const own=rev?(['大','单'].includes(f.direction)?1:-1):0,look=f.lookback,base=f.base_cents,initial=Math.round(c.initial_balance*100),maxdd=Math.round(c.max_drawdown*100),maxstake=Math.round(c.max_stake*100),daylimit=Math.round(c.daily_loss_limit*100);
 const labels=w[axis],prefix=w[axis+'Prefix'],limit=c.win_limit??(s.kind==='majority'?5:0),pause=c.pause_draws??20;
 let balance=initial,peak=initial,daily=0,day=-1,active=false,waiting=false,losses=0,attempt=0,stake=base,last=0,lastSettle=-1,winStreak=0,pauseUntil=0,followEnd=0,direction=0,bets=0,wins=0,refunds=0,invest=0,dd=0;
 const stepStake=(n)=>{if(c.stake_mode==='fixed')return base;if((c.stake_mode||'additive')==='additive')return base+(c.loss_increment_cents||0)*n;let v=base;for(let j=0;j<n;j++)v=Math.floor((v*c.multiplier_hundredths+50)/100);return v;};
 for(let i=1;i<w.ids.length;i++){
  // No future stake can be smaller than base under the generated parameter bounds.
  if(peak-balance+base>maxdd||balance<base||base>maxstake)break;
  const id=w.ids[i],prev=w.ids[i-1];if(day!==w.days[i]){day=w.days[i];daily=0;}
  if(w.rest[i]){if(rev){active=false;waiting=false;}continue;}
  if(pauseUntil&&id<=pauseUntil)continue;if(pauseUntil){pauseUntil=0;winStreak=0;}
  if(prev+1!==id){if(rev&&active){active=false;waiting=c.round_restart==='condition_reset';}continue;}
  let dir=0,step=0;
  if(rev){
   if(active&&lastSettle!==prev){active=false;waiting=c.round_restart==='condition_reset';}
   const available=i>=look,trigger=available&&(f.kind==='reversal'?w[axis+'Run'][i-1]*own<=-f.run_length:-(prefix[i]-prefix[i-look])*own>=f.threshold);
   if(waiting){if(!trigger)waiting=false;continue;}
   if(!available||w.gaps[i-1]-w.gaps[i-look]>0)continue;
   if(!active&&!trigger)continue;
   dir=own;stake=active?stake:base;step=active?attempt+1:1;
  }else if(!hourly){
   if(i<look||w.gaps[i-1]-w.gaps[i-look]>0)continue;
   let n=prefix[i]-prefix[i-look];dir=n>0?1:n<0?-1:last;if(!dir)continue;
   stake=stepStake(losses);step=losses+1;
  }else{
   if(followEnd&&w.times[i]<followEnd)dir=direction;
   else{
    if(w.boundary[i])continue;
    dir=w.hours[axis+'_'+c.observe_hours+'_'+c.hour_difference][w.hourIndex[i]];
    if(!dir)continue;
    direction=dir;followEnd=w.hourStart[i]+c.follow_hours*3600;attempt=0;losses=0;
   }
   stake=stepStake(losses);step=attempt+1;
  }
  if(stake<o.min_stake_cents||stake>o.max_stake_cents||balance<stake||stake>maxstake||peak-balance+stake>maxdd||daily-stake < -daylimit)continue;
  if(rev&&!active){active=true;attempt=0;losses=0;waiting=false;}
  const hit=labels[i]===dir,total=w.totals[i],refund=hit&&o.refund_winning_totals.includes(total);
  let credit=0;
  if(hit){if(refund)credit=stake;else{let factor=o.full_payout_totals.includes(total)?o.normal:stake>=o.high_stake_threshold_cents?o.high_stake_factor:(total===13||total===14)?o[axis==='size'?'special_size':'special_parity']:o.normal;credit=Math.min(Math.floor((stake*factor+50)/100),o.max_return_per_draw_cents);}}
  const pnl=credit-stake;balance+=pnl;daily+=pnl;peak=Math.max(peak,balance);dd=Math.max(dd,peak-balance);last=dir;lastSettle=id;attempt=step;bets++;wins+=hit?1:0;refunds+=refund?1:0;invest+=stake;
  winStreak=hit&&!refund?winStreak+1:0;if(limit&&winStreak>=limit)pauseUntil=id+pause;const stopped=pauseUntil>id;
  if(rev){losses=hit?0:losses+1;stake=hit?base:Math.floor((stake*f.multiplier_hundredths+50)/100);active=!(losses>=f.max_losses||step>=f.max_bets||stopped);if(!active)waiting=!stopped&&losses>=f.max_losses&&c.round_restart==='condition_reset';}
  else{losses=hit?0:losses+1;if(losses>=c.max_losses)losses=0;if(hourly&&stopped)followEnd=0;}
 }
 return {net:(balance-initial)/100,bets,wins,refunds,investment:invest/100,roi:invest?(balance-initial)/invest:null,max_drawdown:dd/100};
}
module.exports={replay};
if(require.main===module){
 if(!isMainThread){const data=JSON.parse(fs.readFileSync(workerData.input,'utf8'));let output=[];for(const p of workerData.plans){output.push({index:p.index,one:replay(data.one,p,data.odds),three:replay(data.three,p,data.odds)});}parentPort.postMessage(output);}
 else{const input=process.argv[2],plans=JSON.parse(fs.readFileSync(process.argv[3],'utf8')),out=process.argv[4],workers=Number(process.argv[5]||8);let done=0,results=[];const chunks=[];for(let start=0;start<plans.length;start+=1000)chunks.push(plans.slice(start,start+1000));let next=0;
  function launch(){if(next>=chunks.length)return;const chunk=chunks[next++],worker=new Worker(__filename,{workerData:{input,plans:chunk}});worker.on('message',r=>{results.push(...r);done+=r.length;if(done%10000===0||done===plans.length)console.log(JSON.stringify({done,total:plans.length}));if(done===plans.length){results.sort((a,b)=>a.index-b.index);fs.writeFileSync(out,JSON.stringify(results));}else launch();});worker.on('error',e=>{console.error(e);process.exit(1);});worker.on('exit',code=>{if(code){console.error('Worker failed',code);process.exit(1);}});}
  for(let j=0;j<Math.min(workers,chunks.length);j++)launch();
 }
}
