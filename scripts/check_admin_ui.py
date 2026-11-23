import sys,json,subprocess
from pathlib import Path
root=Path(__file__).resolve().parents[1];sys.path[:0]=[str(root),str(root/'tests')]
from test_auth import AuthTests
f=AuthTests();f.setup_username='admin';f.setUp()
try:
 f.setup()
 f.member(f.invite()["code"],"uitest_member")
 cookie=next(iter(f.admin.jar))
 script=r'''
const path=require('path'),{chromium}=require(path.join(process.env.USERPROFILE,'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));
(async()=>{
 const input=JSON.parse(require('fs').readFileSync(0,'utf8'));
 const browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
 const ctx=await browser.newContext({viewport:{width:1440,height:1000}});
 await ctx.addCookies([{name:input.name,value:input.value,url:input.url+'/admin',httpOnly:true,sameSite:'Strict'}]);
 const page=await ctx.newPage(),errors=[],failed=[],requests=[];
 page.on('pageerror',e=>errors.push(e.message));
 page.on('response',r=>{if(r.status()>=400)failed.push({url:new URL(r.url()).pathname,status:r.status()})});
 await page.goto(input.url+'/admin',{waitUntil:'networkidle'});
 const tabs=await page.locator('nav [data-tab]').evaluateAll(es=>es.map(e=>({id:e.dataset.tab,name:e.textContent.trim()})));
 const results=[];
 for(const tab of tabs){
  let started=Date.now(),calls=[];
  const handler=async r=>{if(r.url().includes('/api/'))calls.push({url:new URL(r.url()).pathname,status:r.status(),ms:Math.round((await r.request().timing()).responseEnd)});};
  page.on('response',handler);
  await page.locator('[data-tab="'+tab.id+'"]').evaluate(e=>e.click());
  await page.waitForTimeout(750);await page.waitForLoadState('networkidle');
  const detail=await page.locator('#'+tab.id).evaluate(e=>({visible:getComputedStyle(e).display!=='none',tables:e.querySelectorAll('table').length,overflow:e.scrollWidth>e.clientWidth+2,text:e.innerText.slice(0,100)}));
  page.off('response',handler);results.push({...tab,ms:Date.now()-started,...detail,calls});
 }
 await page.screenshot({path:path.join(input.root,'data/audits/admin-desktop-20261121.png'),fullPage:false});
 await page.setViewportSize({width:390,height:844});
 await page.locator('[data-tab="strategies"]').evaluate(e=>e.click());await page.waitForTimeout(800);
 await page.screenshot({path:path.join(input.root,'data/audits/admin-mobile-20261121.png'),fullPage:false});
 const buttons=[];
 await page.setViewportSize({width:1440,height:1000});
 for(const [tab,selectors] of Object.entries({'strategies':['#strategy-add','#strategy-add-hourly','#strategy-add-majority','#strategy-add-single','#strategy-add-previous','#strategy-add-rank','#strategy-add-algorithm'],'admin-users':['#member-add','[data-member-capital]','[data-member-ledger]'],'money-plans':['#mp-add'],'admin-administrators':['#am-add'],'admin-invites':['#admin-invite-add'],'admin-robot':['#ra-add','#ra-catalog-add','#robot-platform-add']})){
  await page.locator('[data-tab="'+tab+'"]').evaluate(e=>e.click());await page.waitForTimeout(500);
  for(const selector of selectors){
   if(selector==='#ra-catalog-add'){await page.locator('[data-ra-tab="catalog"]').click();await page.waitForTimeout(300);}
   if(selector==='#robot-platform-add'){await page.locator('[data-ra-tab="platforms"]').click();await page.waitForTimeout(300);}
   const b=page.locator(selector).first();if(!await b.count()||!await b.isVisible()){buttons.push({selector,skipped:true});continue;}
   await b.click();await page.waitForTimeout(150);const dialog=page.locator('dialog[open]');buttons.push({selector,opened:await dialog.count()>0});await dialog.evaluateAll(es=>es.forEach(e=>e.close()));
  }
 }
 console.log(JSON.stringify({pages:results,buttons,errors,failed},null,2));await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
'''
 result=subprocess.run(['node','-e',script],input=json.dumps({'name':cookie.name,'value':cookie.value,'url':f.url,'root':str(root)}),text=True,encoding="utf-8",capture_output=True,timeout=150)
 (root/'data/audits/admin-ui-20261121.json').write_text(result.stdout,encoding='utf-8')
 print(result.stdout);print(result.stderr);sys.exit(result.returncode)
finally:f.tearDown()
