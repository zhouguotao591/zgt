const {chromium}=require(process.env.USERPROFILE+'/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
(async()=>{const b=await chromium.launch({headless:true,executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe'});try{
 const c=await b.newContext({baseURL:'http://127.0.0.1:18032',viewport:{width:1440,height:1000}}),r=c.request;
 const s=await(await r.get('/admin/api/session')).json(),cap=await(await r.get('/admin/api/auth/captcha')).json();const login=await r.post('/admin/api/auth/login',{headers:{'X-PC28-Token':s.token},data:{username:'admin',password:'temporary-test-password-123',captcha_id:cap.id,captcha:'11111'}});if(!login.ok())throw Error('login');
 const p=await c.newPage(),errors=[];p.on('pageerror',e=>errors.push(e.message));await p.goto('/admin');await p.locator('[data-tab="strategies"]').click();await p.locator('#strategy-list-size').selectOption('100');
 async function options(id){const values=await p.locator('#'+id+' option:enabled').evaluateAll(es=>es.map(e=>e.value));if(values.join(',')!=='大,小,单,双')throw Error(id+' grouped options '+values);}
 for(const kind of ['reversal','hourly','majority','single']){
  for(const direction of ['大','小','单','双']){
   const add=kind==='reversal'?'strategy-add':'strategy-add-'+kind;await p.locator('#'+add).click();
   const prefix=kind==='reversal'?'se':kind==='previous'?'pe':'fe',selector=prefix+'-direction',dialog=kind==='reversal'?'strategy-editor':kind==='previous'?'previous-editor':'follow-editor';
   await options(selector);await p.locator('#'+selector).selectOption(direction);const name=kind+'-'+direction;await p.locator('#'+prefix+'-name').fill(name);await p.locator('#'+prefix+'-base').fill('10');await p.locator('#'+prefix+'-stake').fill('1000');
   if(kind==='single'){
    if(!await p.locator('#fe-adjust-fields').isVisible())throw Error('missing single adjustment parameters');
    await p.locator('#fe-base').fill('500');await p.locator('#fe-win-decrement').fill('100');await p.locator('#fe-increment').fill('100');await p.locator('#fe-max-increase').fill('3');await p.locator('#fe-max-decrease').fill('2');await p.locator('#fe-stake-floor').selectOption('market');
   }
   await p.locator('#'+dialog+'-save').click();await p.waitForFunction(id=>!document.getElementById(id).open,dialog);
   const record=(await(await r.get('/admin/api/strategies')).json()).strategies.find(v=>v.name===name);if(!record)throw Error('missing '+name);
   const actual=kind==='reversal'?record.settings.fixed.direction:record.settings.direction;if(actual!==direction||record.enabled)throw Error('wrong saved direction '+name);
   await p.locator(`[data-strategy-edit="${record.id}"]`).click();if(await p.locator('#'+selector).inputValue()!==direction)throw Error('restore '+name);
   if(kind==='single'){
    const v=record.settings;if(v.win_decrement_cents!==10000||v.loss_increment_cents!==10000||v.max_increase_steps!==3||v.max_decrease_steps!==2||v.stake_mode!=='adjust')throw Error('adjustment save');
    if(await p.locator('#fe-win-decrement').inputValue()!=='100')throw Error('decrement restore');
    await p.setViewportSize({width:390,height:844});if(await p.evaluate(()=>document.documentElement.scrollWidth>innerWidth+2))throw Error('mobile overflow');await p.screenshot({path:'data/single-adjustment-mobile.png',fullPage:true});await p.setViewportSize({width:1440,height:1000});
   }
   await p.keyboard.press('Escape');
  }
  console.log(kind,'all four directions saved/restored');
 }
 for(const direction of ['大','小','单','双']){await p.locator('[data-strategy-edit="size"]').click();await options('te-direction');await p.locator('#te-direction').selectOption(direction);await p.locator('#te-base').fill('10');await p.locator('#te-stake').fill('1000');await p.locator('#trend-editor-save').click();await p.waitForFunction(()=>!document.getElementById('trend-editor').open);}
 await p.locator('#strategy-add-single').click();await p.locator('#fe-direction').selectOption('大');await p.screenshot({path:'data/single-adjustment-desktop.png',fullPage:true});
 if(errors.length)throw Error(errors.join(';'));console.log('Built-in trend directions, single adjustment fields, mobile and browser errors verified');
 }finally{await b.close();}})().catch(e=>{console.error(e);process.exitCode=1});
