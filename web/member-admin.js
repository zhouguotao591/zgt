'use strict';
(()=>{
 if(document.body.dataset.admin!=='1')return;
 const el=id=>document.getElementById('mf-'+id),money=v=>v==null?'—':num(v/100),date=v=>esc(beijingTime(v));
 const state={orders:{page:1,pages:1,filters:{},busy:false},ledger:{page:1,pages:1,filters:{},busy:false}};
 let editing=null,requestId='';
 const status={pending:'待开奖',settled:'已结算',void:'作废退款'},outcome={execution_rejected:'执行异常（已冲正）',win:'盈利',loss:'亏损',refund:'退本金',void:'作废退回'};
 function pager(kind,d){const s=state[kind];Object.assign(s,{page:d.page,pages:d.pages});el(kind+'-page').textContent=`第 ${d.page} / ${d.pages} 页 · 共 ${num(d.total)} 条`;document.querySelectorAll(`[data-mf-page="${kind}"]`).forEach(b=>b.disabled=['first','prev'].includes(b.dataset.move)?d.page<=1:d.page>=d.pages);}
 async function load(kind){const s=state[kind];if(s.busy)return;s.busy=true;try{
  const q=new URLSearchParams({page:s.page,page_size:el(kind+'-size').value,...s.filters});
  el(kind+'-error').textContent='正在读取…';
  const d=await api('/api/admin/member-'+(kind==='orders'?'orders':'ledger')+'?'+q);
  el(kind+'-error').textContent='';pager(kind,d);
  if(kind==='orders')table('mf-orders-table',[
   ['会员','username'],['期号','target_draw'],['发送时间（北京时间）',r=>date(r.placed_at)],
   ['实际跟单数据',r=>`<strong class="mf-bet">${esc(r.direction)} ${money(r.stake)} U</strong>`],
   ['本期开奖结果',r=>r.total==null?'待开奖':`<strong>${[r.draw_a,r.draw_b,r.draw_c].every(v=>v!=null)&&r.draw_a+r.draw_b+r.draw_c===r.total?esc([r.draw_a,r.draw_b,r.draw_c].join('+'))+' = ':''}${r.total}</strong><small class="mf-order-sub">${esc(r.result||'')}</small>`],
   ['盈亏（U）',r=>r.pnl==null?'—':cash(r.pnl/100)],
   ['余额变化（U）',r=>`<div class="mf-balance-line"><span>下注扣款</span> ${money(r.before_balance)} → ${money(r.after_stake_balance)}</div><div class="mf-balance-line"><span>结算返还</span> ${r.before_settle_balance==null?'待结算':money(r.before_settle_balance)+' → '+money(r.after_settle_balance)}</div>`],
   ['结果',r=>r.status==='pending'?'待开奖':outcome[r.outcome]||status[r.status]],
   ['明细',r=>`<button class="secondary" data-mf-order="${r.id}">查看</button>`]
  ],d.rows);
  else table('mf-ledger-table',[
   ['时间（北京时间）',r=>date(r.created)],['会员','username'],['类型',r=>({opening:'历史会员初始化',adjust:'人工额度调整',stake:'跟单扣款',settle:'开奖结算',execution_reversal:'执行异常冲正'})[r.kind]||esc(r.kind)],['变动金额',r=>cash(r.amount/100)],['变动前余额',r=>money(r.before_balance)],['变动后余额',r=>money(r.after_balance)],['调整前本金',r=>money(r.principal_before)],['调整后本金',r=>money(r.principal_after)],['操作者',r=>esc(r.actor_name||'系统')],['订单编号',r=>r.order_id??'—'],['备注','note']
  ],d.rows);
 if(kind==='orders')document.querySelectorAll('[data-mf-order]').forEach(b=>b.onclick=()=>{const r=d.rows.find(x=>x.id===Number(b.dataset.mfOrder));el('detail-content').textContent=['会员：'+r.username,'期号：'+r.target_draw,'发送时间（北京时间）：'+beijingTime(r.placed_at),'接收时间（北京时间）：'+beijingTime(r.accepted_at),'开奖时间（北京时间）：'+beijingTime(r.draw_time),'结算时间（北京时间）：'+beijingTime(r.settled_at),'实际跟单：'+r.direction+' '+money(r.stake)+' U','策略：'+r.strategy_name,'合并编号：'+(r.batch_id||'—'),'本期开奖结果：'+(r.total==null?'待开奖':r.total+' '+r.result),'盈亏：'+money(r.pnl)+' U','下注扣款：'+money(r.before_balance)+' → '+money(r.after_stake_balance),'结算返还：'+money(r.before_settle_balance)+' → '+money(r.after_settle_balance)].join('\n');el('detail-dialog').showModal();});
 }catch(e){el(kind+'-error').textContent=e.message;}finally{s.busy=false;}}
 document.querySelectorAll('[data-mf-page]').forEach(b=>b.onclick=()=>{const s=state[b.dataset.mfPage];s.page=({first:1,prev:Math.max(1,s.page-1),next:Math.min(s.pages,s.page+1),last:s.pages})[b.dataset.move];load(b.dataset.mfPage);});
 for(const kind of ['orders','ledger'])el(kind+'-size').onchange=()=>{state[kind].page=1;load(kind);};
 el('orders-filter').onsubmit=e=>{e.preventDefault();const filters={};for(const key of ['start','end','draw','username','direction','status'])if(el(key).value)filters[key]=el(key).value;state.orders.filters=filters;state.orders.page=1;load('orders');};
 el('orders-filter').onreset=()=>{state.orders.filters={};state.orders.page=1;load('orders');};
 el('orders-refresh').onclick=()=>load('orders');
 document.querySelector('[data-tab="admin-member-orders"]').addEventListener('click',()=>load('orders'));
 document.getElementById('refresh').addEventListener('click',()=>{if(document.getElementById('admin-member-orders').classList.contains('active'))load('orders');});
 document.querySelectorAll('[data-mf-close]').forEach(b=>b.onclick=()=>{const d=document.getElementById(b.dataset.mfClose);if(!d.querySelector('button[id$="-save"]:disabled'))d.close();});
 for(const kind of ['create','adjust'])el(kind+'-dialog').addEventListener('cancel',e=>{if(el(kind+'-save').disabled)e.preventDefault();});
 document.getElementById('member-add').onclick=()=>{el('create-form').reset();el('create-error').textContent='';el('create-dialog').showModal();};
 el('create-form').onsubmit=async e=>{e.preventDefault();const b=el('create-save');b.disabled=true;try{
  if(el('create-password').value!==el('create-confirm').value)throw Error('两次密码不一致');
  await api('/api/admin/member-create',{username:el('create-name').value,password:el('create-password').value});el('create-dialog').close();el('create-form').reset();await window.pc28AdminUsersRefresh();toast('会员已添加，初始额度为0');
 }catch(e){el('create-error').textContent=e.message;}finally{b.disabled=false;}};
 el('adjust-form').onsubmit=async e=>{e.preventDefault();const b=el('adjust-save');b.disabled=true;try{
  await api('/api/admin/member-capital',{id:editing,mode:el('adjust-mode').value,amount:el('adjust-amount').value,note:el('adjust-note').value.trim(),request_id:requestId});el('adjust-dialog').close();await window.pc28AdminUsersRefresh();toast('额度已调整，完整记录已保存');
 }catch(e){el('adjust-error').textContent=e.message;}finally{b.disabled=false;}};
 window.pc28MemberAdmin={
  refreshOrders:()=>load('orders'),
  adjust(row){editing=row.id;requestId=crypto.randomUUID();el('adjust-form').reset();el('adjust-title').textContent='增减额度 · '+row.username;el('adjust-current').textContent=`当前本金 ${money(row.principal)} U · 可用余额 ${money(row.balance)} U`;el('adjust-error').textContent='';el('adjust-dialog').showModal();},
  ledger(row){state.ledger.filters={user_id:row.id};state.ledger.page=1;el('ledger-title').textContent='资金记录 · '+row.username;el('ledger-dialog').showModal();load('ledger');}
 };
})();
