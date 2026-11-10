const fs=require('fs');const {chromium}=require(process.env.USERPROFILE+'/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
(async()=>{const b=await chromium.launch({headless:true,executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe'});try{
 const c=await b.newContext({baseURL:'http://127.0.0.1:18031'}),r=c.request,fixture=JSON.parse(fs.readFileSync('data/auth-ui-test-state.json','utf8'));
 const s=await(await r.get('/admin/api/session')).json(),cap=await(await r.get('/admin/api/auth/captcha')).json();
 const setup=await r.post('/admin/api/auth/setup',{headers:{'X-PC28-Token':s.token},data:{username:'history_test_admin',password:'temporary-test-password-123',setup_code:fixture.setup_code,captcha_id:cap.id,captcha:'11111'}});if(!setup.ok())throw Error('setup '+setup.status());
 const p=await c.newPage(),errors=[];p.on('pageerror',e=>errors.push(e.message));let submissions=0,polls=0;
 await p.route('**/admin/api/task',async route=>{submissions++;if(submissions===1)await route.fulfill({status:502,body:''});else await route.continue();});
 await p.route('**/admin/api/history-backtest-task?*',async route=>{polls++;if(polls<=2)await route.fulfill({status:502,body:''});else await route.continue();});
 await p.goto('/admin');await p.locator('[data-tab="history-backtest"]').click();await p.locator('#hb-strategy option').first().waitFor({state:'attached'});
 await p.locator('#hb-range-mode').selectOption('count');await p.locator('#hb-range-count').fill('90');await p.locator('#hb-start').click();
 await p.waitForFunction(()=>document.getElementById('hb-progress').textContent.includes('回测提交失败'));
 if(!await p.locator('#hb-start').isEnabled())throw Error('submit remains disabled');console.log('Empty POST 502: readable message; button restored; no automatic duplicate submission');
 await p.locator('#hb-start').click();
 await p.waitForFunction(()=>document.getElementById('hb-progress').textContent.includes('自动重试'));
 console.log('Polling interruption: task retained and retrying');
 await p.waitForFunction(()=>!document.getElementById('hb-results').hidden,{},{timeout:30000});
 if(submissions!==2||polls<3)throw Error('unexpected request counts');if(errors.length)throw Error(errors.join(';'));
 console.log('Backtest completed and rendered:',await p.locator('#hb-title').textContent());console.log('No browser script errors');
 }finally{await b.close();}})().catch(e=>{console.error(e);process.exitCode=1});
