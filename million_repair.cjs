// Retain valid computed records, remove cross-round duplicates, and compute replacements.
const fs=require('fs'),path=require('path'),zlib=require('zlib'),crypto=require('crypto'),readline=require('readline');
const {Worker}=require('worker_threads');const {expand,canonical,candidate,rng}=require('./million_runner.cjs');
async function main(){
 const folder=path.resolve(process.argv[2]||'data/parameter-search-million'),manifest=JSON.parse(fs.readFileSync(path.join(folder,'manifest.json'),'utf8')),input=path.join(folder,'input.json'),idle=[],workers=[],pending=new Map();let sequence=0;
 for(let i=0;i<8;i++){const w=new Worker(path.resolve(__dirname,'million_runner.cjs'),{workerData:{input}});workers.push(w);await new Promise((resolve,reject)=>{w.once('message',()=>{idle.push(w);resolve();});w.once('error',reject);});w.on('message',m=>{if(m.error)throw Error(m.error);const task=pending.get(m.id);pending.delete(m.id);idle.push(w);task(m.rows);});w.on('error',e=>{console.error(e);process.exit(1);});}
 async function dispatch(group,batch){while(!idle.length)await new Promise(r=>setTimeout(r,2));const w=idle.pop(),id=sequence++;return new Promise(resolve=>{pending.set(id,resolve);w.postMessage({id,group,batch});});}
 const samples=[],summaries=[],sampleIndices=new Set([0,1,17,1234,9999,99999,499999,999999]),ranges={one:{min:Infinity,max:-Infinity},three:{min:Infinity,max:-Infinity},drawdown:{min:Infinity,max:-Infinity}};
 fs.renameSync(path.join(folder,'eligible.ndjson'),path.join(folder,'eligible.pre-repair.ndjson'));const eligible=fs.createWriteStream(path.join(folder,'eligible.ndjson'));
 for(const g of manifest.strategies){
  const previous=new Set(fs.readFileSync(path.join(folder,`exclude-${g.position}.txt`),'ascii').trim().split(/\r?\n/)),seen=new Set(),source=path.join(folder,`results-${g.position}.ndjson.gz`),backup=path.join(folder,`results-${g.position}.pre-repair.ndjson.gz`);fs.renameSync(source,backup);
  const gzip=zlib.createGzip({level:1}),sink=fs.createWriteStream(source);gzip.pipe(sink);let count=0,removed=0,qualified=0,buffer='',qualBuffer='',attempted=0;
  async function flush(){if(buffer){const data=buffer;buffer='';if(!gzip.write(data))await new Promise(r=>gzip.once('drain',r));}if(qualBuffer){const data=qualBuffer;qualBuffer='';if(!eligible.write(data))await new Promise(r=>eligible.once('drain',r));}}
  async function save(row){row[1]=count++;buffer+=JSON.stringify(row)+'\n';if(sampleIndices.has(row[1]))samples.push(row);const a=row[4],b=row[5];if(a[0]>0&&b[0]>0&&a[0]>g.baseline.one.net&&b[0]>g.baseline.three.net&&a[1]>=100&&b[1]>=300){qualified++;qualBuffer+=JSON.stringify(row)+'\n';for(const [k,v] of [['one',a[0]],['three',b[0]],['drawdown',b[6]]]){ranges[k].min=Math.min(ranges[k].min,v);ranges[k].max=Math.max(ranges[k].max,v);}}if(buffer.length>512*1024)await flush();}
  let read=0;
  for await(const line of readline.createInterface({input:fs.createReadStream(backup).pipe(zlib.createGunzip()),crlfDelay:Infinity})){
   if(!line)continue;const row=JSON.parse(line);read++;if(previous.has(row[2])||seen.has(row[2])){removed++;continue;}seen.add(row[2]);await save(row);
   if(read%250000===0)console.log(JSON.stringify({repair:g.name,read,retained:count,removed}));
  }
  if(read!==manifest.per_strategy)throw Error('Incomplete pre-repair file');const retained=count,rand=rng(g.seed+5000001);
  while(count<manifest.per_strategy){const batch=[];while(batch.length<2000&&count+batch.length<manifest.per_strategy){attempted++;const t=candidate(g,rand),sha=crypto.createHash('sha256').update(canonical(expand(g.base,g.kind,t))).digest('hex');if(previous.has(sha)||seen.has(sha))continue;seen.add(sha);batch.push([count+batch.length,sha,t]);}const computed=await dispatch(g,batch);for(const row of computed)await save(row);}
  await flush();gzip.end();await new Promise((resolve,reject)=>{sink.once('finish',resolve);sink.once('error',reject);});if(seen.size!==manifest.per_strategy)throw Error('Unique count mismatch');
  summaries.push({source:g.id,position:g.position,generated:count,completed:count,unique:seen.size,qualified,parameter_file:`results-${g.position}.ndjson.gz`,retained,previous_duplicates_removed:removed,replacement_computations:count-retained,replacement_attempts:attempted});
  fs.writeFileSync(path.join(folder,'repair-progress.json'),JSON.stringify({summaries,ranges},null,2));fs.writeFileSync(path.join(folder,'samples.json'),JSON.stringify(samples));console.log(JSON.stringify({repaired:g.name,count,removed,replacements:count-retained,qualified}));
 }
 eligible.end();await new Promise(r=>eligible.once('finish',r));for(const w of workers)await w.terminate();const norm=(v,r)=>r.max===r.min?.5:(v-r.min)/(r.max-r.min),best=new Map();let totalEligible=0;
 const scores=fs.createWriteStream(path.join(folder,'eligible-scores.csv'));scores.write('strategy_position,candidate_index,parameter_sha256,score,one_month_net,three_month_net,three_month_max_drawdown,one_month_bets,three_month_bets\n');
 for await(const line of readline.createInterface({input:fs.createReadStream(path.join(folder,'eligible.ndjson')),crlfDelay:Infinity})){
  if(!line)continue;const row=JSON.parse(line),score=100*(.4*norm(row[4][0],ranges.one)+.3*norm(row[5][0],ranges.three)+.3*(1-norm(row[5][6],ranges.drawdown)));totalEligible++;const b=best.get(row[0]);if(!b||score>b.score||score===b.score&&(row[4][0]>b.row[4][0]||row[4][0]===b.row[4][0]&&row[5][0]>b.row[5][0]))best.set(row[0],{row,score});if(!scores.write([row[0],row[1],row[2],score,row[4][0],row[5][0],row[5][6],row[4][1],row[5][1]].join(',')+'\n'))await new Promise(r=>scores.once('drain',r));
 }
 scores.end();await new Promise(r=>scores.once('finish',r));const acceleration=JSON.parse(fs.readFileSync(path.join(folder,'acceleration-validation.json'),'utf8'));fs.writeFileSync(path.join(folder,'winners.pre-repair.json'),fs.readFileSync(path.join(folder,'winners.json')));fs.writeFileSync(path.join(folder,'winners.json'),JSON.stringify({summaries,ranges,totalEligible,winners:[...best.values()],accelerationComparisons:acceleration.comparisons,repair:'Removed historical overlaps caused by CRLF parsing; recomputed replacements to restore one million valid unique new combinations per strategy.'},null,2));console.log(JSON.stringify({repair_finished:summaries.length,totalEligible,removed:summaries.reduce((n,s)=>n+s.previous_duplicates_removed,0)}));
}
main().catch(e=>{console.error(e);process.exit(1);});
