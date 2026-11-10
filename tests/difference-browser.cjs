const fs=require('fs');
const {chromium}=require(process.env.USERPROFILE+'/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
(async()=>{
 const b=await chromium.launch({headless:true,executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe'});
 try{
 const c=await b.newContext({baseURL:'http://127.0.0.1:18031'}),r=c.request;
 const fixture=JSON.parse(fs.readFileSync('data/auth-ui-test-state.json','utf8'));
 const session=await(await r.get('/admin/api/session')).json();
 const captcha=await(await r.get('/admin/api/auth/captcha')).json();
 const auth=await r.post('/admin/api/auth/setup',{headers:{'X-PC28-Token':session.token},data:{username:'difference_test_admin',password:'temporary-test-password-123',setup_code:fixture.setup_code,captcha_id:captcha.id,captcha:'11111'}});
 if(!auth.ok())throw Error('Fixture setup failed: '+auth.status());
 const page=await c.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto('/admin');await page.locator('[data-tab="strategies"]').click();
 await page.locator('[data-strategy-edit="legacy"]').waitFor();
 async function saveEditor(prefix,button,dialog){
  await page.locator('#'+prefix+'-difference-limit').fill('5');
  await page.locator('#'+prefix+'-stake').fill('2000');
  await page.locator('#'+button).click();
  await page.waitForFunction(id=>!document.getElementById(id).open,dialog);
 }
 for(const [key,prefix,button,dialog] of [['legacy','se','strategy-editor-save','strategy-editor'],['size','te','trend-editor-save','trend-editor'],['parity','te','trend-editor-save','trend-editor']]){
  await page.locator(`[data-strategy-edit="${key}"]`).click();
  await saveEditor(prefix,button,dialog);
  const row=(await(await r.get('/admin/api/strategies')).json()).strategies.find(x=>x.id===key);
  if(row.settings.win_loss_difference_limit!==5)throw Error(key+' not saved');
  console.log(key,'save OK');
 }
 for(const kind of ['hourly','majority','previous']){
  await page.locator('#strategy-add-'+kind).click();
  const prefix=kind==='previous'?'pe':'fe',dialog=kind==='previous'?'previous-editor':'follow-editor';
  await page.locator('#'+prefix+'-name').fill('difference-'+kind);
  await saveEditor(prefix,dialog+'-save',dialog);
  const row=(await(await r.get('/admin/api/strategies')).json()).strategies.find(x=>x.name==='difference-'+kind);
  if(row?.settings.win_loss_difference_limit!==5)throw Error(kind+' not saved');
  await page.locator(`[data-strategy-edit="${row.id}"]`).click();
  if(await page.locator('#'+prefix+'-difference-limit').inputValue()!=='5')throw Error(kind+' restore');
  await page.setViewportSize({width:390,height:844});
  if(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+2))throw Error('mobile overflow');
  await page.keyboard.press('Escape');await page.setViewportSize({width:1440,height:1000});
  console.log(kind,'save/restore/mobile OK');
 }
 if(errors.length)throw Error(errors.join(';'));
 console.log('All six strategy editors passed; no page errors');
 }finally{await b.close();}
})().catch(e=>{console.error(e);process.exitCode=1});
