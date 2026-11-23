import sys,json,subprocess
from pathlib import Path
root=Path(__file__).resolve().parents[1];sys.path[:0]=[str(root),str(root/'tests')]
from test_auth import AuthTests
f=AuthTests();f.setup_username='admin';f.setUp()
try:
 f.setup();member,_=f.member(f.invite()['code'],'hallmember')
 from datetime import datetime,timezone,timedelta
 with f.s.db.tx() as c:
  for i in range(260):
   total=6 if i%3 else 18;stamp=(datetime(2020,1,1,tzinfo=timezone.utc)+timedelta(seconds=i*210)).isoformat();a=min(9,total);b=min(9,total-a);third=total-a-b
   c.execute("INSERT INTO draws(id,source_time,draw_utc,numbers,a,b,c,total,size,parity,first_seen,source,raw_hash) VALUES(?,?,?,'[]',?,?,?,?,?,'双','fixture','fixture','fixture')",(100+i,stamp,stamp,a,b,third,total,'大' if total>=14 else '小'))
 f.s.algorithm_hall.snapshot();f.s.algorithm_hall.snapshots.start(f.s.stop)
 status,public=member.call('/api/algorithms?page=1&page_size=10');assert status==200 and public['total']==82 and len(public['algorithms'])==10
 member.call('/api/algorithm-favorite',{'id':'size.follow.1','favorite':True})
 status,public=member.call('/api/algorithms?page=1&favorites=1');assert status==200 and public['total']==1
 script=r'''
const path=require('path'),fs=require('fs'),{chromium}=require(path.join(process.env.USERPROFILE,'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));
(async()=>{
 const input=JSON.parse(fs.readFileSync(0,'utf8')),browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true}),report=[];
 const assert=(value,message)=>{if(!value)throw Error(message)};
 for(const role of ['admin']){
  const ctx=await browser.newContext({viewport:{width:1440,height:1000}}),cookie=input[role];await ctx.addCookies([{name:cookie.name,value:cookie.value,url:input.url+(role==='admin'?'/admin':'/'),httpOnly:true,sameSite:'Strict'}]);
  const page=await ctx.newPage(),errors=[],failed=[];page.on('pageerror',e=>{errors.push(e.message);console.error('PAGE ERROR',role,e.message)});page.on('response',r=>{if(r.status()>=400)failed.push({path:new URL(r.url()).pathname,status:r.status()})});
  await page.goto(input.url+(role==='admin'?'/admin':'/'),{waitUntil:'networkidle'});
  async function change(action){const response=page.waitForResponse(r=>r.url().includes('/algorithms?')&&r.request().method()==='GET');await action();const d=await(await response).json();console.error(role,'response',JSON.stringify({total:d.total,page:d.page,ready:d.ready,unchanged:d.unchanged}));await page.waitForTimeout(100);return d;}
  console.error(role,'loaded',await page.evaluate(()=>({scripts:[...document.scripts].map(s=>s.src).filter(s=>s.includes('algorithm')),message:document.getElementById('algo-message')?.textContent})));
  await page.screenshot({path:path.join(input.root,'data/audits/algorithm-ui-before.png')});
  let d=await change(()=>page.locator('[data-tab="algorithm-hall"]').evaluate(e=>e.click()));assert(d.total===82&&d.algorithms.length===10,'initial page');
  const first=d.algorithms.map(x=>x.id);const leaders=d.leaders.map(x=>x.id);assert(leaders.length===3,'three leader cards');assert(await page.locator('#algo-grid .algo-card').count()===3,'exactly three cards');assert(await page.locator('#algo-list tbody tr').count()===10,'algorithm table');await page.screenshot({path:path.join(input.root,'data/audits/algorithm-ranking-desktop-20261121.png')});await page.locator('.algo-list-panel').screenshot({path:path.join(input.root,'data/audits/algorithm-ranking-table-20261121.png')});await page.setViewportSize({width:390,height:844});await page.screenshot({path:path.join(input.root,'data/audits/algorithm-ranking-mobile-20261121.png')});await page.setViewportSize({width:1440,height:1000});
  d=await change(()=>page.locator('#algo-next').click());assert(d.page===2&&!d.algorithms.some(x=>first.includes(x.id)),'next page');assert(JSON.stringify(d.leaders.map(x=>x.id))===JSON.stringify(leaders),'leaders persist across pages');
  d=await change(()=>page.locator('#algo-page-size').selectOption('20'));assert(d.page===1&&d.algorithms.length===20,'page size');
  d=await change(()=>page.locator('[data-axis="size"]').click());assert(d.total===41&&d.algorithms.every(x=>x.axis==='size'),'axis');
  d=await change(()=>page.locator('[data-window="100"]').click());assert(d.algorithms.every(x=>Object.keys(x.metrics).join()==='100'),'window');assert(await page.locator('#algo-grid .algo-hit-grid').first().locator('.algo-hit').count()===100,'100 hit badges');d=await change(()=>page.locator('[data-window="500"]').click());const more=page.waitForResponse(r=>r.url().includes('source=pattern'));await page.locator('#algo-grid .algo-hit-scroll').first().evaluate(e=>{e.scrollTop=e.scrollHeight});await more;await page.waitForTimeout(150);assert(await page.locator('#algo-grid .algo-hit-grid').first().locator('.algo-hit').count()===200,'lazy hit history');d=await change(()=>page.locator('[data-window="100"]').click());
  d=await change(()=>page.locator('#algo-sort').selectOption('name'));assert(d.algorithms.length===20,'sort');
  d=await change(()=>page.locator('#algo-search').fill('上期跟随'));assert(d.total===1&&d.algorithms[0].id==='size.follow.1','search');
  await page.locator('#algo-list [data-algo-view]').first().click();await page.waitForSelector('#algo-detail-table table');assert(await page.locator('#algo-detail-dialog').evaluate(e=>e.open),'detail');await page.locator('#algo-detail-close').click();
  d=await change(()=>page.locator('#algo-lock').check());assert(d.ranking.length===1,'lock ranking');
  d=await change(()=>page.locator('#algo-refresh').click());d=await change(()=>page.locator('#algo-refresh').click());assert(d.unchanged===true,'lightweight unchanged response');
  if(role==='member'){
   await change(()=>page.locator('[data-algo-favorite]').click());d=await change(()=>page.locator('#algo-favorites').check());assert(d.total===1&&d.algorithms[0].favorite,'favorite');
  }
  await page.screenshot({path:path.join(input.root,'data/audits/algorithm-'+role+'-desktop-20261121.png')});
  await page.setViewportSize({width:390,height:844});await page.screenshot({path:path.join(input.root,'data/audits/algorithm-'+role+'-mobile-20261121.png')});
  assert(!errors.length&&!failed.length,'browser errors: '+JSON.stringify({errors,failed}));report.push({role,firstPage:10,pageSize:20,category:41,window:100,search:true,detail:true,locked:true,unchanged:true,errors,failed});await ctx.close();
 }
 console.log(JSON.stringify(report,null,2));await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
'''
 def cookie(client):
  c=next(iter(client.jar));return {'name':c.name,'value':c.value}
 result=subprocess.run(['node','-e',script],input=json.dumps({'url':f.url,'root':str(root),'admin':cookie(f.admin),'member':cookie(member)}),text=True,encoding='utf-8',stdout=subprocess.PIPE,stderr=(root/'data/audits/algorithm-ui-debug.log').open('w',encoding='utf-8'),timeout=120)
 (root/'data/audits/algorithm-ui-20261121.json').write_text(result.stdout,encoding='utf-8');print(result.stdout);print(result.stderr);sys.exit(result.returncode)
finally:
 f.s.stop.set();f.s.algorithm_hall.snapshots.wake.set()
 if f.s.algorithm_hall.snapshots.started:f.s.algorithm_hall.snapshots.thread.join(5)
 f.tearDown()
