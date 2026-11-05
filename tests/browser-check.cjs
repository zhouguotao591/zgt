'use strict';
// Optional development test. Install playwright or use the bundled runtime on this computer.
const fs = require('fs');
const path = require('path');
const { createRequire } = require('module');
let chromium;
try { ({ chromium } = require('playwright')); }
catch { ({ chromium } = createRequire('C:/Users/周生/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/package.json')('playwright')); }
const root = path.resolve(__dirname, '..');
const dataRoot = path.join(root, 'data');
const profile = path.join(dataRoot, 'browser-test-' + Date.now());
(async () => {
  let browser;
  try {
    browser = await chromium.launchPersistentContext(profile, {
      executablePath: process.env.PC28_CHROME || 'C:/Program Files/Google/Chrome/Application/chrome.exe',
      headless: true, viewport: { width: 1440, height: 1050 }, args: ['--no-sandbox'],
      env: { ...process.env, TEMP: dataRoot, TMP: dataRoot }
    });
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.goto('http://127.0.0.1:8028');
    const ready = () => Number(document.getElementById('count').textContent.replaceAll(',', '')) >= 121455;
    await page.waitForFunction(ready);
    const status=await page.request.get('http://127.0.0.1:8028/api/status');
    const current=await status.json();
    if(current.latest.beijing_time && await page.locator('#latest-time').textContent()!==current.latest.beijing_time)throw Error('Latest draw is not displayed in Beijing time');
    await page.screenshot({ path: path.join(root, 'docs/overview.png'), fullPage: true });
    for (const tab of ['draws', 'simulation', 'research', 'settings', 'logs', 'overview']) {
      const nav=page.locator(`[data-tab="${tab}"]`);
      if(!await nav.isVisible())await nav.locator('xpath=ancestor::details').locator('summary').click();
      await nav.click();
      await page.waitForFunction(t => document.getElementById(t).classList.contains('active'), tab);
    }
    const researchNav=page.locator('[data-tab="research"]');
    if(!await researchNav.isVisible())await researchNav.locator('xpath=ancestor::details').locator('summary').click();
    await researchNav.click();
    await page.locator('#bt-start').fill('2026-10-01');
    await page.locator('#bt-end').fill('2026-10-02');
    await page.locator('#bt-risk').uncheck();
    await page.locator('#bt-multiplier').fill('1.5');
    await page.locator('#bt-base').fill('250.25');
    await page.locator('#bt-losses').fill('2');
    const requestPromise=page.waitForRequest(r=>r.url().endsWith('/api/task')&&r.method()==='POST'&&r.postDataJSON().kind==='backtest');
    await page.locator('#backtest-form button').click();
    const body=(await requestPromise).postDataJSON();
    if(body.args.strategy.base_cents!==25025||body.args.strategy.multiplier_hundredths!==150||body.args.strategy.max_losses!==2)throw Error('Custom staking parameters were not submitted');
    await page.waitForFunction(() => document.querySelector('#research-results').textContent.includes('基础金额 250.25U · 亏后 1.5 倍 · 连亏 2 期停轮'), null, { timeout: 30000 });
    await page.screenshot({ path: path.join(root, 'docs/research.png'), fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto('http://127.0.0.1:8028');
    await page.waitForFunction(ready);
    await page.screenshot({ path: path.join(root, 'docs/mobile.png'), fullPage: true });
    if (errors.length) throw Error(errors.join('; '));
    console.log('UI passed: six tabs, data, historical backtest, desktop/mobile screenshots; zero page errors.');
  } finally {
    if (browser) await browser.close();
    const resolved = path.resolve(profile);
    if (path.dirname(resolved) === path.resolve(dataRoot) && path.basename(resolved).startsWith('browser-test-')) {
      fs.rmSync(resolved, { recursive: true, force: true });
    }
  }
})().catch(e => { console.error(e); process.exitCode = 1; });
