"""Browser verification against an isolated server, never the live database."""
import json,subprocess,sys
from pathlib import Path
root=Path(__file__).resolve().parents[1];sys.path[:0]=[str(root),str(root/'tests')]
from test_trend_risk_http import TrendHTTPTests
f=TrendHTTPTests();f.setUp()
try:
 _,_,sid=f.seed();cookie=next(iter(f.admin.jar))
 script=r'''
const path=require('path'),fs=require('fs'),{chromium}=require(path.join(process.env.USERPROFILE,'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));
(async()=>{const input=JSON.parse(fs.readFileSync(0,'utf8'));const browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});try{const ctx=await browser.newContext({viewport:{width:1440,height:1000}});await ctx.addCookies([{name:input.name,value:input.value,url:input.url+'/admin',httpOnly:true,sameSite:'Strict'}]);const page=await ctx.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
const assert=(ok,s)=>{if(!ok)throw Error(s)};
await page.goto(input.url+'/admin?view=admin-trend-risk',{waitUntil:'networkidle'});
await page.locator('[data-tr-edit]').click();assert(await page.locator('#tr-model').inputValue()==='swing','new config defaults swing');
await page.locator('#tr-swing-bars').fill('12');await page.locator('#tr-swing-atr').fill('2');await page.locator('#tr-family').selectOption('star');await page.locator('#tr-confirmation').selectOption('middle');await page.locator('#tr-candidate-bars').fill('5');
const saved=page.waitForResponse(r=>r.url().endsWith('/trend-risk-save')&&r.request().method()==='POST');await page.locator('#tr-save').click();assert((await saved).status()===200,'hybrid save');await page.waitForFunction(()=>!document.querySelector('#tr-editor').open);
await page.locator('[data-tr-edit]').click();assert(await page.locator('#tr-family').inputValue()==='star','persist family');assert(await page.locator('#tr-confirmation').inputValue()==='middle','persist confirmation');
await page.screenshot({path:path.join(input.root,'data/audits/trend-pattern-desktop.png')});await page.setViewportSize({width:390,height:844});await page.screenshot({path:path.join(input.root,'data/audits/trend-pattern-mobile.png')});assert(await page.locator('#tr-editor').evaluate(e=>e.scrollWidth<=e.clientWidth+2),'mobile dialog fits');
await page.locator('[data-tr-close="tr-editor"]').click();await page.setViewportSize({width:1440,height:1000});await page.locator('[data-tr-backtest]').click();
await page.waitForFunction(()=>document.querySelector('#hb-risk-enabled')?.checked);assert(await page.locator('#hb-risk-enabled').isChecked(),'comparison enabled');assert(await page.locator('#hb-risk-decision_model').inputValue()==='swing','same model in backtest');assert(await page.locator('#hb-risk-swing_bars').inputValue()==='12','same wave context in backtest');assert(await page.locator('#hb-risk-swing_atr').inputValue()==='2','same wave amplitude in backtest');assert(await page.locator('#hb-risk-pattern_family').inputValue()==='star','same family in backtest');assert(await page.locator('#hb-risk-entry_confirm').inputValue()==='middle','same confirmation in backtest');assert(await page.locator('#hb-risk-candidate_bars').inputValue()==='5','same expiry in backtest');assert(!errors.length,JSON.stringify(errors));console.log(JSON.stringify({save:true,persistence:true,desktop:true,mobile:true,backtestParameters:true,errors}));}finally{await browser.close();}})().catch(e=>{console.error(e);process.exit(1)});
'''
 result=subprocess.run(['node','-e',script],input=json.dumps(dict(url=f.url,root=str(root),name=cookie.name,value=cookie.value)),capture_output=True,text=True,encoding='utf-8',timeout=90)
 print(result.stdout);print(result.stderr)
 if result.returncode:raise SystemExit(result.returncode)
 saved=f.s.db.query('SELECT settings FROM trend_risk_rules WHERE subscription_id=?',(sid,))[0]
 assert json.loads(saved['settings'])['decision_model']=='swing'
finally:f.tearDown()
