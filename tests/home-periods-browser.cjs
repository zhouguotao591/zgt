const fs=require('fs'),{createRequire}=require('module');
const {chromium}=createRequire('C:/Users/周生/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/package.json')('playwright');
(async()=>{const browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});try{
 const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
 const state=JSON.parse(fs.readFileSync('data/algorithm-ui-state.json','utf8'));await page.goto(state.entry.replace('pc28-next.localhost','127.0.0.1'));
 for(const kind of ['hour','four_hour','daily','week']){await page.click('[data-summary="'+kind+'"]');await page.waitForSelector('#home-'+kind+'-table tbody tr');if(await page.locator('.home-summary-pane:visible').count()!==1)throw Error('应只显示一个统计列表');}await page.click('[data-summary="hour"]');
 const result=await page.evaluate(async()=>{const d=await api('/api/tools?view=home&limit=1');for(const [kind,s] of Object.entries(d.summaries)){for(const r of s.rows){if(r['大']+r['小']!==r.count||r['单']+r['双']!==r.count)throw Error('分母错误：'+kind);}}return {draws:d.total,periods:Object.fromEntries(Object.entries(d.summaries).map(([k,s])=>[k,s.total]))};});
 const dailyBefore=await page.locator('#home-daily-pager select').inputValue();
 await Promise.all([page.waitForResponse(r=>r.url().includes('/api/tools?')&&r.url().includes('hour_size=20')&&!r.url().includes('four_hour_size=20')),page.selectOption('#home-hour-pager select','20')]);
 await page.waitForFunction(()=>document.querySelector('#home-hour-pager select')?.value==='20');
 if(await page.locator('#home-daily-pager select').inputValue()!==dailyBefore)throw Error('独立分页相互干扰');
 await page.locator('#home-hour-title').scrollIntoViewIfNeeded();await page.screenshot({path:'data/exports/home-hour-statistics.png'});
 await page.click('[data-summary="week"]');await page.locator('#home-week-title').scrollIntoViewIfNeeded();await page.screenshot({path:'data/exports/home-week-statistics.png'});
 await page.setViewportSize({width:390,height:844});await page.click('[data-summary="four_hour"]');await page.locator('#home-four_hour-title').scrollIntoViewIfNeeded();await page.screenshot({path:'data/exports/home-periods-mobile.png'});
 if(errors.length)throw Error(errors.join('\n'));console.log('PASS 四类汇总、统计分母、独立分页及PC/H5：'+JSON.stringify(result));
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exit(1)});
