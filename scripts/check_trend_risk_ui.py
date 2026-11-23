import sys,json,subprocess,time
from pathlib import Path
from datetime import datetime,timezone
root=Path(__file__).resolve().parents[1];sys.path[:0]=[str(root),str(root/'tests')]
from test_trend_risk_http import TrendHTTPTests
from pc28.trend_risk import DEFAULT
f=TrendHTTPTests();f.setUp()
try:
 member,uid,sid=f.seed();now=int(time.time()//3600)*3600
 with f.s.db.tx() as c:
  for i in range(30):
   t=datetime.fromtimestamp(now-(30-i)*3600+1800,timezone.utc).isoformat()
   c.execute("INSERT INTO draws(id,source_time,draw_utc,numbers,a,b,c,total,size,parity,first_seen,source,raw_hash) VALUES(?,?,?,'[]',1,2,3,6,'小','双','t','t','t')",(100+i,t,t))
   c.execute("INSERT INTO strategy_signals(strategy,version,target_draw,cutoff_draw,created,direction,stake,status,hit,pnl,outcome,settled,odds_json,evidence,source_engine,source_id) VALUES('size','v',?,?,?,'大',10000,'settled',0,-10000,'loss',?,'{}','{}','fixture',?)",(100+i,99+i,t,t,i))
 f.admin.call('/api/admin/trend-risk-save',{'id':sid,'version':0,'settings':DEFAULT});f.s.trend_risk.tick(now);cookie=next(iter(f.admin.jar))
 script=r'''
const path=require('path'),fs=require('fs'),{chromium}=require(path.join(process.env.USERPROFILE,'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));
(async()=>{const input=JSON.parse(fs.readFileSync(0,'utf8')),browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true}),ctx=await browser.newContext({viewport:{width:1440,height:1000}});await ctx.addCookies([{name:input.name,value:input.value,url:input.url+'/admin',httpOnly:true,sameSite:'Strict'}]);const page=await ctx.newPage(),errors=[],failed=[];page.on('pageerror',e=>{errors.push(e.message);console.error('PAGE',e.message)});page.on('response',r=>{if(r.status()>=400)failed.push({path:new URL(r.url()).pathname,status:r.status()})});const assert=(ok,s)=>{if(!ok)throw Error(s)};
await page.goto(input.url+'/admin?view=admin-trend-risk',{waitUntil:'networkidle'});assert(await page.locator('#admin-trend-risk').evaluate(e=>e.classList.contains('active')),'deep link');console.error('loaded',await page.evaluate(()=>({scripts:[...document.scripts].map(s=>s.src).filter(s=>s.includes('trend-risk')),tab:!!document.querySelector('[data-tab="admin-trend-risk"]'),panel:!!document.getElementById('admin-trend-risk')})));const loaded=page.waitForResponse(r=>r.url().includes('/admin/trend-risk?'));await page.locator('[data-tab="admin-trend-risk"]').click();const data=await(await loaded).json();assert(data.total===1&&data.rows[0].status==='risk','observe analysis');await page.waitForTimeout(100);await page.screenshot({path:path.join(input.root,'data/audits/trend-risk-desktop-20261121.png')});await page.locator('[data-tr-log]').click();await page.waitForSelector('#tr-logs table');assert(await page.locator('#tr-chart svg').count()>0,'analysis chart');await page.locator('[data-tr-close="tr-log-dialog"]').click();await page.locator('[data-tr-edit]').click();assert(await page.locator('#tr-mode').inputValue()==='observe','default observe');await page.locator('#tr-interval').selectOption('4h');console.error('opened editor');const saved=page.waitForResponse(r=>r.url().endsWith('/trend-risk-save')&&r.request().method()==='POST');await page.locator('#tr-save').click();assert((await saved).status()===200,'save config');await page.waitForTimeout(200);assert(!await page.locator('#tr-editor').evaluate(e=>e.open),'save closes editor');await page.setViewportSize({width:390,height:844});await page.locator('[data-tr-edit]').click();await page.screenshot({path:path.join(input.root,'data/audits/trend-risk-mobile-editor-20261121.png')});assert(!errors.length&&!failed.length,'browser errors '+JSON.stringify({errors,failed}));console.log(JSON.stringify({page:true,observe:true,chart:true,edit4h:true,desktop:true,mobile:true,errors,failed}));await browser.close();})().catch(e=>{console.error(e);process.exit(1)});
'''
 result=subprocess.run(['node','-e',script],input=json.dumps({'url':f.url,'root':str(root),'name':cookie.name,'value':cookie.value}),stdout=subprocess.PIPE,stderr=(root/'data/audits/trend-risk-ui-debug.log').open('w',encoding='utf-8'),text=True,encoding='utf-8',timeout=90)
 (root/'data/audits/trend-risk-ui-20261121.json').write_text(result.stdout,encoding='utf-8');print(result.stdout);print(result.stderr);sys.exit(result.returncode)
finally:f.tearDown()
