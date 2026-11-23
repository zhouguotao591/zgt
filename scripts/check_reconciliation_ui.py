import sys,json,subprocess,time
from pathlib import Path
root=Path(__file__).resolve().parents[1];sys.path[:0]=[str(root),str(root/'tests')]
from test_trend_risk_http import TrendHTTPTests
f=TrendHTTPTests();f.setUp()
try:
 _,uid,_=f.seed()
 with f.s.db.tx() as c:
  c.execute("INSERT INTO robot_risk(bot_id,capital,loss_mode,loss_limit,profit_target,trailing_bps,baseline) VALUES(1,100000,'percent',5000,50000,5000,0)")
  oid=c.execute("INSERT INTO robot_outbox(event_id,name,strategy_id,target_draw,event_time,created,request,message,status,attempts,is_test,follow_account_id,bot_id,push_cents,member_user_id,recipient_name) VALUES('review-fixture','fixture','size',3509788,?,'t','{}','小7','failed',1,0,1,1,700,?,'fixture')",(int(time.time()),uid)).lastrowid
  c.execute("INSERT INTO robot_execution VALUES(?,'submitted','','t',?)",(oid,int(time.time())-100))
  c.execute('INSERT INTO robot_risk_holds VALUES(?,1,700)',(oid,))
 cookie=next(iter(f.admin.jar))
 script=r'''
const path=require('path'),fs=require('fs'),{chromium}=require(path.join(process.env.USERPROFILE,'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));
(async()=>{const input=JSON.parse(fs.readFileSync(0,'utf8')),browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});try{const ctx=await browser.newContext({viewport:{width:1440,height:1000}});await ctx.addCookies([{name:input.name,value:input.value,url:input.url+'/admin',httpOnly:true,sameSite:'Strict'}]);const p=await ctx.newPage(),errors=[];p.on('pageerror',e=>errors.push(e.message));await p.goto(input.url+'/admin',{waitUntil:'networkidle'});await p.locator('[data-tab="admin-robot"]').click();await p.locator('[data-ra-manage]').click();await p.locator('[data-ra-risk-reconcile]').waitFor();if(!await p.locator('#ra-detail').textContent().then(t=>t.includes('待核对 1 笔')))throw Error('review count missing');await p.locator('[data-ra-risk-reconcile]').click();await p.locator('#ra-risk-outbox').waitFor();const text=await p.locator('#ra-risk-outbox').textContent();if(!text.includes('3509788')||!text.includes('小7'))throw Error('order identity missing');if(await p.locator('#ra-risk-confirmed').isChecked())throw Error('must require explicit manual check');await p.screenshot({path:path.join(input.root,'data/audits/receiver-400-20261123/reconciliation-ui.png')});if(errors.length)throw Error(JSON.stringify(errors));console.log(JSON.stringify({reviewButton:true,count:true,drawAndContent:true,explicitConfirmation:true,errors}));}finally{await browser.close();}})().catch(e=>{console.error(e);process.exit(1)});
'''
 p=subprocess.run(['node','-e',script],input=json.dumps(dict(url=f.url,root=str(root),name=cookie.name,value=cookie.value)),capture_output=True,text=True,encoding='utf-8',timeout=90)
 print(p.stdout);print(p.stderr)
 if p.returncode:raise SystemExit(p.returncode)
finally:f.tearDown()
