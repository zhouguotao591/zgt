// Stream one million unique combinations per strategy through persistent workers.
const fs=require('fs'),path=require('path'),crypto=require('crypto'),zlib=require('zlib'),readline=require('readline');
const {Worker,isMainThread,parentPort,workerData}=require('worker_threads');
const fast=require('./million_fast.cjs'),gold=require('./search_fast_100k.cjs');
function expand(base,kind,t){const c={...base};if(kind==='reversal'){c.fixed={...base.fixed,threshold:t[0],max_bets:t[1],max_losses:t[2],multiplier_hundredths:t[3]};c.win_limit=t[4];c.pause_draws=t[5];}else if(kind==='majority'){Object.assign(c,{lookback:t[0],max_losses:t[1],loss_increment_cents:t[2],win_limit:t[3],pause_draws:t[4]});}else Object.assign(c,{observe_hours:t[0],hour_difference:t[1],follow_hours:t[2],win_limit:t[3],pause_draws:t[4]});return c;}
function canonical(x){if(x===null||typeof x!=='object')return JSON.stringify(x).replace(/[\u007f-\uffff]/g,c=>'\\u'+c.charCodeAt(0).toString(16).padStart(4,'0'));if(Array.isArray(x))return '['+x.map(canonical).join(',')+']';return '{'+Object.keys(x).sort().map(k=>canonical(k)+':'+canonical(x[k])).join(',')+'}';}
const metric=r=>[r.net,r.bets,r.wins,r.refunds,r.investment,r.roi,r.max_drawdown];
function rng(seed){let a=seed>>>0;return n=>{a=(a+0x6D2B79F5)|0;let t=a;t=Math.imul(t^(t>>>15),t|1);t^=t+Math.imul(t^(t>>>7),t|61);return Math.floor((((t^(t>>>14))>>>0)/4294967296)*n);};}
function candidate(g,rand){let limit=rand(31),pause=limit?1+rand(1000):1;if(g.kind==='reversal'){let bets=1+rand(80),loss=Math.min(1+rand(3),bets),mult=loss===1?100:100+rand(101);return [1+rand(g.base.fixed.lookback),bets,loss,mult,limit,pause];}if(g.kind==='majority'){let increment=1000*rand(11);return [2+rand(499),increment?1+rand(3):1,increment,limit,pause];}return [1+rand(8),1+rand(12),1+rand(24),limit,pause];}
function same(a,b){for(const k of ['net','bets','wins','refunds','investment','roi','max_drawdown'])if(a[k]===null?b[k]!==null:b[k]===null||Math.abs(a[k]-b[k])>1e-8)return false;return true;}
module.exports={expand,canonical,candidate,rng};

if(require.main===module){
 if(!isMainThread){
  const data=JSON.parse(fs.readFileSync(workerData.input,'utf8'));fast.prepare(data.one);fast.prepare(data.three);
  parentPort.on('message',job=>{try{const out=[];for(const p of job.batch){const snap={kind:job.group.kind,settings:expand(job.group.base,job.group.kind,p[2])};out.push([job.group.position,p[0],p[1],p[2],metric(fast.replay(data.one,snap,data.odds)),metric(fast.replay(data.three,snap,data.odds))]);}parentPort.postMessage({id:job.id,rows:out});}catch(e){parentPort.postMessage({error:e.stack});}});
  parentPort.postMessage({ready:true});
 }else main().catch(e=>{console.error(e);process.exit(1);});
}

async function main(){
 const folder=path.resolve(process.argv[2]||'data/parameter-search-million'),manifest=JSON.parse(fs.readFileSync(path.join(folder,'manifest.json'),'utf8')),input=path.join(folder,'input.json'),workerCount=Number(process.argv[3]||8),data=JSON.parse(fs.readFileSync(input,'utf8'));
 fast.prepare(data.one);fast.prepare(data.three);let checks=0;
 for(const g of manifest.strategies){const rand=rng(g.seed+99);const settings=[g.base,...Array.from({length:32},()=>expand(g.base,g.kind,candidate(g,rand)))];for(const c of settings){const s={kind:g.kind,settings:c};for(const w of ['one','three']){const a=gold.replay(data[w],s,data.odds),b=fast.replay(data[w],s,data.odds);if(!same(a,b))throw Error('Accelerated replay parity failed '+g.id+' '+w+' '+JSON.stringify({a,b,c}));checks+=7;}}}
 fs.writeFileSync(path.join(folder,'acceleration-validation.json'),JSON.stringify({comparisons:checks,errors:[]},null,2));console.log(JSON.stringify({acceleration_checks:checks,errors:0}));
 const workers=[];const idle=[];const pending=new Map();let sequence=0;
 for(let i=0;i<workerCount;i++){
  const worker=new Worker(__filename,{workerData:{input}});workers.push(worker);
  await new Promise((resolve,reject)=>{worker.once('message',()=>{idle.push(worker);resolve();});worker.once('error',reject);});
  worker.on('message',message=>{if(message.error)throw Error(message.error);const task=pending.get(message.id);pending.delete(message.id);idle.push(worker);task.resolve(message.rows);});worker.on('error',e=>{console.error(e);process.exit(1);});
 }
 async function dispatch(group,batch){while(!idle.length)await new Promise(r=>setTimeout(r,2));const worker=idle.pop(),id=sequence++;return new Promise((resolve,reject)=>{pending.set(id,{resolve,reject});worker.postMessage({id,group,batch});});}
 const samples=[],summaries=[],sampleIndices=new Set([0,1,17,1234,9999,99999,499999,999999]);
 const eligible=fs.createWriteStream(path.join(folder,'eligible.ndjson'));
 const ranges={one:{min:Infinity,max:-Infinity},three:{min:Infinity,max:-Infinity},drawdown:{min:Infinity,max:-Infinity}};
 for(const g of manifest.strategies){
  const excluded=new Set(fs.readFileSync(path.join(folder,`exclude-${g.position}.txt`),'ascii').trim().split(/\r?\n/)),seen=new Set(),rand=rng(g.seed),gzip=zlib.createGzip({level:1}),sink=fs.createWriteStream(path.join(folder,`results-${g.position}.ndjson.gz`));gzip.pipe(sink);let attempted=0,repeated=0,old=0,generated=0,completed=0,qualified=0;const inFlight=new Set();
  const save=async rows=>{let output='',qual='';for(const row of rows){output+=JSON.stringify(row)+'\n';if(sampleIndices.has(row[1]))samples.push(row);const a=row[4],b=row[5];if(a[0]>0&&b[0]>0&&a[0]>g.baseline.one.net&&b[0]>g.baseline.three.net&&a[1]>=100&&b[1]>=300){qualified++;qual+=JSON.stringify(row)+'\n';for(const [key,v] of [['one',a[0]],['three',b[0]],['drawdown',b[6]]]){ranges[key].min=Math.min(ranges[key].min,v);ranges[key].max=Math.max(ranges[key].max,v);}}}
   if(!gzip.write(output))await new Promise(r=>gzip.once('drain',r));if(qual&&!eligible.write(qual))await new Promise(r=>eligible.once('drain',r));completed+=rows.length;
   if(completed%50000===0||completed===manifest.per_strategy)console.log(JSON.stringify({strategy:g.name,completed,total:manifest.per_strategy,qualified}));
  };
  while(generated<manifest.per_strategy){
   if(inFlight.size>=workerCount*2){await Promise.race(inFlight);continue;}
   const batch=[];while(batch.length<2000&&generated<manifest.per_strategy){attempted++;const tuple=candidate(g,rand),settings=expand(g.base,g.kind,tuple),sha=crypto.createHash('sha256').update(canonical(settings)).digest('hex');if(excluded.has(sha)){old++;continue;}if(seen.has(sha)){repeated++;continue;}seen.add(sha);batch.push([generated++,sha,tuple]);}
   const task=dispatch(g,batch).then(save);inFlight.add(task);task.finally(()=>inFlight.delete(task));
  }
  await Promise.all(inFlight);gzip.end();await new Promise((resolve,reject)=>{sink.once('finish',resolve);sink.once('error',reject);});
  if(seen.size!==manifest.per_strategy||completed!==manifest.per_strategy)throw Error('Candidate count mismatch');
  summaries.push({source:g.id,position:g.position,generated,completed,unique:seen.size,attempted,duplicates_skipped:repeated,previous_duplicates_skipped:old,qualified,parameter_file:`results-${g.position}.ndjson.gz`});
  fs.writeFileSync(path.join(folder,'progress.json'),JSON.stringify({summaries,ranges},null,2));fs.writeFileSync(path.join(folder,'samples.json'),JSON.stringify(samples));
 }
 eligible.end();await new Promise(r=>eligible.once('finish',r));for(const w of workers)await w.terminate();
 const normalize=(v,range)=>range.max===range.min?.5:(v-range.min)/(range.max-range.min),best=new Map();let totalEligible=0;
 const ranking=fs.createWriteStream(path.join(folder,'eligible-scores.csv'));ranking.write('strategy_position,candidate_index,parameter_sha256,score,one_month_net,three_month_net,three_month_max_drawdown,one_month_bets,three_month_bets\n');
 for await(const line of readline.createInterface({input:fs.createReadStream(path.join(folder,'eligible.ndjson')),crlfDelay:Infinity})){if(!line)continue;const row=JSON.parse(line),score=100*(.4*normalize(row[4][0],ranges.one)+.3*normalize(row[5][0],ranges.three)+.3*(1-normalize(row[5][6],ranges.drawdown))),item={row,score};totalEligible++;const oldBest=best.get(row[0]);if(!oldBest||score>oldBest.score||score===oldBest.score&&(row[4][0]>oldBest.row[4][0]||row[4][0]===oldBest.row[4][0]&&row[5][0]>oldBest.row[5][0]))best.set(row[0],item);if(!ranking.write([row[0],row[1],row[2],score,row[4][0],row[5][0],row[5][6],row[4][1],row[5][1]].join(',')+'\n'))await new Promise(r=>ranking.once('drain',r));}
 ranking.end();await new Promise(r=>ranking.once('finish',r));fs.writeFileSync(path.join(folder,'winners.json'),JSON.stringify({summaries,ranges,totalEligible,winners:[...best.values()],accelerationComparisons:checks},null,2));console.log(JSON.stringify({finished:summaries.length,totalEligible,winners:[...best.values()].map(x=>({position:x.row[0],score:x.score,one:x.row[4][0],three:x.row[5][0]}))}));
}
