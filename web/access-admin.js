 'use strict';
(()=>{
 if(document.body.dataset.admin!=='1')return;
 const access=JSON.parse(document.body.dataset.access||'{}'),grants=new Set(access.grants||[]),can=key=>grants.has(key);
 window.pc28Access={...access,can};
 function apply(){
  for(const [page] of Object.entries(access.pages||{})){
   const denied=!can('page.'+page);
   for(const node of document.querySelectorAll('[data-tab="'+page+'"],section#'+page))node.toggleAttribute('data-permission-hidden',denied);
  }
  for(const row of access.catalog||[])if(!row.key.startsWith('page.'))for(const node of document.querySelectorAll(row.selector))node.toggleAttribute('data-permission-hidden',!can(row.key));
 }
 document.addEventListener('click',e=>{if(e.target.closest('[data-permission-hidden]')){e.preventDefault();e.stopImmediatePropagation();}},true);
 apply();document.body.removeAttribute('data-access-loading');let pending=false;new MutationObserver(()=>{if(pending)return;pending=true;queueMicrotask(()=>{pending=false;apply();});}).observe(document.body,{childList:true,subtree:true});
})();
