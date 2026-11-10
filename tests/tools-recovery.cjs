const {chromium}=require(process.env.USERPROFILE+'/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe'});
 try{
  const page=await browser.newPage();let fail=true, failures=0;const errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/api/tools?*',async route=>{
   if(fail&&new URL(route.request().url()).searchParams.get('view')==='trend'){failures++;await route.fulfill({status:502,body:''});}
   else await route.continue();
  });
  await page.goto('http://127.0.0.1:8028/?view=trend');
  await page.waitForTimeout(4000);console.log('diagnostic',page.url(),await page.locator('#trend-coverage').textContent(),errors,failures);await page.screenshot({path:'data/tools-recovery.png'});await page.waitForFunction(()=>document.querySelector('#trend-coverage').textContent.includes('HTTP 502'));
  console.log('Empty 502 handled; requests:',failures);
  fail=false;
  await page.waitForFunction(()=>document.querySelector('#trend-coverage').textContent.includes('本地窗口'),{},{timeout:20000});
  console.log('Automatic recovery:',await page.locator('#trend-coverage').textContent());
  for(const view of ['dragon','missing','stats','analysis']){
   await page.evaluate(v=>window.pc28Tools.open(v),view);
   await page.waitForFunction(v=>document.getElementById(v+'-coverage').textContent.includes('本地窗口'),view,{timeout:30000});
   console.log(view,'OK');
  }
  if(errors.length)throw Error(errors.join(';'));
  console.log('No browser script errors');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1});
