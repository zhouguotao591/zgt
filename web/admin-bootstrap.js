'use strict';
(()=>{
 if(document.body.dataset.admin!=='1')return;
 const multiAccounts=!!document.getElementById('ra-table');
 const modules=[
  {file:'app.js',ready:()=>typeof api==='function'&&typeof table==='function'},
  {file:'access-admin.js',ready:()=>!!window.pc28Access},
  {file:'profit-curve.js',ready:()=>typeof window.pc28SimulationRows==='function'},
  {file:'admin.js',ready:()=>typeof window.pc28AdminUsersRefresh==='function'},
  {file:'strategies.js',ready:()=>!!window.pc28Strategies},
  {file:'access-management.js',ready:()=>!!window.pc28AccessManagement},
  {file:'member-admin.js',ready:()=>!!window.pc28MemberAdmin},
  {file:'history-backtest.js',ready:()=>!!window.pc28HistoryReady},
  {file:multiAccounts?'robot-management.js':'robot.js',ready:()=>!!window.pc28RobotReady&&(!multiAccounts||!!window.pc28RobotAccounts)},
  {file:'ai-admin.js',ready:()=>!!window.pc28AiAdminReady},
  {file:'trend-strategy.js',ready:()=>!!window.pc28TrendReady}
 ];
 function banner(message){let box=document.getElementById('admin-load-status');if(!box){box=document.createElement('div');box.id='admin-load-status';box.className='notice warning';box.setAttribute('role','alert');document.querySelector('main').prepend(box);}box.textContent=message;return box;}
 function reloadModule(file,attempt){return new Promise((resolve,reject)=>{
  const original=[...document.scripts].find(s=>new URL(s.src||location.href).pathname.endsWith('/'+file));
  if(!original){reject(Error('找不到'+file+'加载地址'));return;}
  const url=new URL(original.src);url.searchParams.set('retry',String(attempt)+'-'+Date.now());
  const script=document.createElement('script');script.src=url.href;const timer=setTimeout(()=>{script.remove();reject(Error(file+'加载超时'));},12000);script.onload=()=>{clearTimeout(timer);resolve();};script.onerror=()=>{clearTimeout(timer);reject(Error(file+'加载失败'));};document.head.append(script);
 });}
 async function recover(){
  const missing=modules.filter(m=>!m.ready());if(!missing.length)return;
  const box=banner('后台组件加载不完整，正在自动恢复…');
  for(const module of missing){
   if(module.file==='app.js')continue;
   for(let attempt=1;attempt<=2&&!module.ready();attempt++)try{await reloadModule(module.file,attempt);}catch(e){console.error(e);}
  }
  const remaining=modules.filter(m=>!m.ready());
  if(remaining.length){box.textContent='后台组件加载失败：'+remaining.map(m=>m.file).join('、')+'。请重新加载页面；数据已保留。';return;}
  box.remove();
  const active=document.querySelector('.tab.active')?.id;
  if(active==='strategies')window.pc28Strategies.refresh();
  else if(active==='admin-users')window.pc28AdminUsersRefresh();
  else if(active==='admin-member-orders')window.pc28MemberAdmin.refreshOrders();
  else document.querySelector('[data-tab="'+active+'"]')?.click();
 }
 if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',recover,{once:true});else recover();
})();
