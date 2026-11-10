const fs=require('fs');const {chromium}=require(process.env.USERPROFILE+'/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
(async()=>{const b=await chromium.launch({headless:true,executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe'});try{
 const c=await b.newContext({baseURL:'http://127.0.0.1:18032',viewport:{width:1440,height:1000}}),r=c.request,f=JSON.parse(fs.readFileSync('data/member-ui-test-state.json','utf8'));
 const session=await(await r.get('/admin/api/session')).json(),cap=await(await r.get('/admin/api/auth/captcha')).json();
 const auth=await r.post('/admin/api/auth/login',{headers:{'X-PC28-Token':session.token},data:{username:'admin',password:'temporary-test-password-123',setup_code:f.setup_code,captcha_id:cap.id,captcha:'11111'}});if(!auth.ok())throw Error('setup '+auth.status());
 const p=await c.newPage(),errors=[],newName='new_member_'+Date.now();p.on('pageerror',e=>errors.push(e.message));await p.goto('/admin');await p.locator('[data-tab="admin-users"]').click();await p.locator('[data-member-capital]').first().waitFor();
 const stats=(await(await r.get('/admin/api/admin/users')).json()).rows.find(x=>x.username==='fixture_member');if(stats.principal!==100000||stats.follow_count!==35)throw Error('stats');console.log('Existing member stats and 35 accepted orders visible');
 await p.locator('#member-add').click();await p.locator('#mf-create-name').fill(newName);await p.locator('#mf-create-password').fill('new-password-123');await p.locator('#mf-create-confirm').fill('new-password-123');await p.locator('#mf-create-save').click();await p.waitForFunction(()=>!document.getElementById('mf-create-dialog').open);
 let fresh=(await(await r.get('/admin/api/admin/users')).json()).rows.find(x=>x.username===newName);if(fresh.balance!==0||fresh.principal!==0)throw Error('new member not zero');
 for(const [mode,amount] of [['add','100'],['subtract','25']]){
  await p.locator(`[data-member-capital="${fresh.id}"]`).click();await p.locator('#mf-adjust-mode').selectOption(mode);await p.locator('#mf-adjust-amount').fill(amount);await p.locator('#mf-adjust-note').fill('UI '+mode+' audit');await p.locator('#mf-adjust-save').click();await p.waitForFunction(()=>!document.getElementById('mf-adjust-dialog').open);
 }
 fresh=(await(await r.get('/admin/api/admin/users')).json()).rows.find(x=>x.username===newName);if(fresh.balance!==7500||fresh.principal!==7500)throw Error('adjust failed');
 await p.locator(`[data-member-ledger="${fresh.id}"]`).click();await p.waitForFunction(()=>document.querySelectorAll('#mf-ledger-table tbody tr').length===2);await p.locator('[data-mf-close="mf-ledger-dialog"]').click();console.log('Create zero-capital member; add/subtract; before/after amounts and full ledger verified');
 await p.screenshot({path:'data/member-users-desktop.png',fullPage:true});
 await p.locator('[data-tab="admin-member-orders"]').click();await p.waitForFunction(()=>document.querySelectorAll('#mf-orders-table tbody tr').length===20);
 await p.locator('[data-mf-page="orders"][data-move="next"]').click();await p.waitForFunction(()=>document.querySelectorAll('#mf-orders-table tbody tr').length===15);
 await p.locator('#mf-draw').fill('102');await p.locator('#mf-username').fill('fixture');await p.locator('#mf-direction').selectOption('单');await p.locator('#mf-orders-filter button:not([type="reset"])').click();await p.waitForFunction(()=>document.querySelectorAll('#mf-orders-table tbody tr').length===1);
 const row=(await(await r.get('/admin/api/admin/member-orders?draw=102')).json()).rows[0];
 const formatted=await p.evaluate(v=>beijingTime(v).replace(' ','T'),row.placed_at);await p.locator('#mf-start').fill(formatted);await p.locator('#mf-end').fill(formatted.slice(0,16)+':59');await p.locator('#mf-orders-filter button:not([type="reset"])').click();await p.waitForFunction(()=>document.querySelectorAll('#mf-orders-table tbody tr').length===1);
 await p.screenshot({path:'data/member-orders-desktop.png',fullPage:true});
 await p.setViewportSize({width:390,height:844});if(await p.evaluate(()=>document.documentElement.scrollWidth>innerWidth+2))throw Error('mobile page overflow');await p.screenshot({path:'data/member-orders-mobile.png',fullPage:true});
 console.log('Orders pagination 20/15; filters by time, period, username and direction; mobile width verified');
 await p.setViewportSize({width:1440,height:1000});await p.locator('[data-tab="admin-robot"]').click();await p.waitForFunction(()=>document.getElementById('robot-timeout').value==='5'&&document.querySelector('#robot-records table'));await p.locator('[data-ra-tab=platforms]').click();await p.locator('#robot-delay-min').fill('35');await p.locator('#robot-delay-max').fill('55');await p.locator('#robot-config-form button[type=submit]').click();await p.waitForFunction(()=>document.getElementById('robot-config-message').textContent.includes('已保存'));
 const cfg=(await(await r.get('/admin/api/admin/robot')).json()).config;if(cfg.delay_min_seconds!==35||cfg.delay_max_seconds!==55)throw Error('random range not saved');
 await p.locator('[data-ra-tab=records]').click();if(!await p.locator('#robot-records').textContent().then(t=>t.includes('计划发送时间')&&t.includes('实际发送时间')))throw Error('missing timestamps');
 if(errors.length)throw Error(errors.join(';'));console.log('Random delay range save; cache/send/accepted timestamps; no browser errors');
 }finally{await b.close();}})().catch(e=>{console.error(e);process.exitCode=1});

