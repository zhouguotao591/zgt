const fs=require('fs');const {chromium}=require(process.env.USERPROFILE+'/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
(async()=>{const b=await chromium.launch({headless:true,executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe'});try{
 const c=await b.newContext({baseURL:'http://127.0.0.1:18031',viewport:{width:1440,height:1000}}),r=c.request,fixture=JSON.parse(fs.readFileSync('data/auth-ui-test-state.json','utf8'));
 const s=await(await r.get('/admin/api/session')).json(),cap=await(await r.get('/admin/api/auth/captcha')).json();
 const setup=await r.post('/admin/api/auth/setup',{headers:{'X-PC28-Token':s.token},data:{username:'layout_test_admin',password:'temporary-test-password-123',setup_code:fixture.setup_code,captcha_id:cap.id,captcha:'11111'}});if(!setup.ok())throw Error('setup');
 const p=await c.newPage();
 await p.route('**/admin/api/strategies',async route=>{const response=await route.fetch(),d=await response.json();d.strategies.forEach(row=>{row.backtest_task={status:'done'};row.backtest={created:new Date().toISOString(),parameters_changed:true,one_month:{net:199},three_months:{net:-397}};});await route.fulfill({response,json:d});});
 await p.goto('/admin');await p.locator('[data-tab="strategies"]').click();await p.locator('#strategy-catalog tbody tr').first().waitFor();
 for(const width of [1440,1024,390]){
  await p.setViewportSize({width,height:1000});
  for(const end of [false,true]){
   await p.locator('#strategy-catalog').evaluate((el,end)=>el.scrollLeft=end?el.scrollWidth:0,end);
   const metrics=await p.evaluate(()=>{const table=document.querySelector('#strategy-catalog table'),row=table.querySelector('tbody tr'),last=row.lastElementChild,progress=last.querySelector('.strategy-backtest-progress');return {columns:table.querySelectorAll('th').length,actionWidth:last.getBoundingClientRect().width,rowHeight:row.getBoundingClientRect().height,progressHeight:progress.getBoundingClientRect().height,overflow:document.documentElement.scrollWidth>innerWidth+2};});
   console.log(width,end?'right':'left',JSON.stringify(metrics));
   if(metrics.columns!==11||metrics.actionWidth<290||metrics.rowHeight>130||metrics.progressHeight>25||metrics.overflow)throw Error('layout regression');
  }
  await p.screenshot({path:`data/strategy-layout-${width}.png`,fullPage:true});
 }
 console.log('Desktop/mobile widths, both scroll extremes, controls and completion text passed');
 }finally{await b.close();}})().catch(e=>{console.error(e);process.exitCode=1});
