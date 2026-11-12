'use strict';
let token = '';
const centerScope = document.body.dataset.centerScope === '1';

async function api(path, body) {
  const endpoint = centerScope ? '/member' + path : path.replace(/^\/api\/(dashboard|orders|order)(?=\?|$)/, '/api/member/$1');
  const response = await fetch(endpoint, body ? {
    method: 'POST',
    headers: {'Content-Type':'application/json','X-PC28-Token':token},
    body: JSON.stringify(body)
  } : {});
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw Object.assign(Error(data.error || '读取失败，请重试'), {status:response.status});
  return data;
}

function toast(message) {
  document.getElementById('mc-error').textContent = message;
}

(() => {
  const el = id => document.getElementById(id);
  const signedIn = document.body.dataset.authenticated === '1';
  const statuses = {pending:'待开奖',settled:'已记账',void:'已作废'};
  const amount = cents => cents == null ? '—' : (cents/100).toLocaleString('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:2})+' U';
  const rate = value => value == null ? '—' : (value*100).toFixed(2)+'%';
  const time = value => value ? new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'}).format(new Date(value)) : '—';
  let page=1, pages=1, orderSequence=0, detailSequence=0, sessionSequence=0, botOptions=[], accountOptions=[];
  let view=['home','orders','me'].includes(location.hash.slice(1)) ? location.hash.slice(1) : 'home';
  let retry=()=>verify(true);

  function node(tag, text, className) {
    const item=document.createElement(tag);
    if (text!=null) item.textContent=text;
    if (className) item.className=className;
    return item;
  }
  function fields(target, values) {
    target.replaceChildren(...values.map(([label,value])=>{
      const item=node('div',null,'mc-value');
      item.append(node('span',label),node('strong',value));
      return item;
    }));
  }
  function clearError() {
    toast('');
    el('mc-retry').hidden=true;
  }
  function expired() {
    el('mc-private').replaceChildren();
    el('mc-user').textContent='';
    location.replace('/member?auth=login'+location.hash);
  }
  function failure(error, again) {
    if (error.status===401) { expired(); return; }
    retry=again;
    toast(error.message);
    el('mc-retry').hidden=false;
  }

  async function verify(load=false) {
    const sequence=++sessionSequence;
    try {
      const session=await api('/api/session');
      if(sequence!==sessionSequence)return;
      if (session.user?.role!=='member') {expired();return;}
      token=session.token;
      el('mc-user').textContent=session.user.username;
      el('mc-identity').textContent='会员 #'+session.user.id+' · '+session.user.username+' · 已启用';
      el('mc-private').hidden=false;
      if(load)await navigate(view);
    } catch(error) {
      if(sequence!==sessionSequence)return;
      el('mc-private').hidden=true;
      failure(error,()=>verify(load));
    }
  }
  async function navigate(next, accounts) {
    view=next;
    history.replaceState(null,'','#'+view);
    for (const name of ['home','orders','me']) el('mc-'+name).hidden=name!==view;
    for (const button of document.querySelectorAll('[data-mc-view]')) button.setAttribute('aria-current',button.dataset.mcView===view?'page':'false');
    clearError();
    if(view==='home')await loadDashboard();
    if(view==='orders')await loadOrders(accounts);
  }
  async function loadDashboard() {
    try {
      const d=await api('/api/dashboard');
      const t=d.total;
      el('mc-updated').textContent='统计截至 '+time(d.as_of);
      fields(el('mc-total'),[['会员本金',amount(d.principal)],['已结算资金',amount(d.settled_funds)],
        ['累计净收益',t.settled?amount(t.net):'暂无结算'],['累计收益率',rate(d.roi)],
        ['已记账 / 待开奖',t.settled+' / '+t.pending+' 笔'],['待结算金额',amount(t.pending_amount)]]);
      el('mc-periods').replaceChildren(...d.periods.map(p=>{
        const button=node('button',null,'mc-period');
        button.type='button';
        button.append(node('span',p.days===1?'今日':'最近 '+p.days+' 天'),
          node('strong',p.settled?amount(p.net):'暂无结算'),
          node('small','期初 '+amount(p.opening)+' · 收益率 '+rate(p.roi)));
        button.onclick=()=>downstream({start:p.start,end:p.end,status:'settled'});
        return button;
      }));
      el('mc-accounts').replaceChildren(...d.accounts.map(a=>{
        const card=node('article',null,'mc-account');
        const button=node('button','查看订单','secondary');
        button.type='button';
        button.onclick=()=>downstream({account:a.id});
        const stats=node('div',null,'mc-metrics');
        fields(stats,[['净收益',a.settled?amount(a.net):'暂无结算'],['已记账',a.settled+' 笔'],['待结算',amount(a.pending_amount)]]);
        card.append(node('h3',a.name),node('p',a.platform_name+' · '+(a.enabled?'已启用':'历史或已停用')),stats);
        if(a.id!=null)card.append(button);
        return card;
      }));
      if(!d.accounts.length) el('mc-accounts').append(node('p','暂无下单账号'));
    } catch(error) { failure(error,loadDashboard); }
  }
  function selected(id) {
    return [...el(id).querySelectorAll('input:checked')].map(x=>Number(x.value));
  }
  function options(id,rows,checked,label) {
    const box=el(id);
    box.replaceChildren(...rows.map(row=>{
      const item=node('label'), input=node('input');
      input.type='checkbox';
      input.value=row.id;
      input.checked=checked.includes(row.id);
      item.append(input,node('span',label(row)));
      return item;
    }));
    if(!rows.length)box.append(node('span','暂无选项'));
    updateSelection(box);
  }
  function updateSelection(box) {
    const count=box.querySelectorAll('input:checked').length;
    const all=box.id==='mc-filter-accounts'?'全部账号':'全部机器人';
    box.previousElementSibling.textContent=count?'已选 '+count+' 项':all;
  }
  function showBots(accounts,checked) {
    const names=new Map(accountOptions.map(a=>[a.id,a.name]));
    const grouped=new Map();
    for(const b of botOptions) {
      if(accounts.length&&!accounts.includes(b.account_id))continue;
      const existing=grouped.get(b.id);
      if(existing)existing.label+='、'+(names.get(b.account_id)||'历史账号');
      else grouped.set(b.id,{...b,label:b.name+' · '+(names.get(b.account_id)||'历史账号')});
    }
    options('mc-filter-bots',[...grouped.values()],checked,b=>b.label);
  }
  function downstream(filters) {
    el('mc-filters').reset();
    el('mc-start').value=filters.start||'';
    el('mc-end').value=filters.end||'';
    el('mc-status').value=filters.status||'';
    options('mc-filter-accounts',accountOptions,filters.account?[filters.account]:[],a=>a.name);
    showBots([],[]);
    page=1;
    navigate('orders',filters.account?[filters.account]:[]);
  }
  async function loadOrders(accounts=selected('mc-filter-accounts')) {
    const sequence=++orderSequence;
    const bots=selected('mc-filter-bots');
    const query=new URLSearchParams({time:'draw',page,page_size:el('mc-size').value,accounts:accounts.join(','),bots:bots.join(',')});
    for(const key of ['start','end','status']) query.set(key,el('mc-'+key).value);
    clearError();
    el('mc-order-list').replaceChildren(node('p','正在读取订单…'));
    el('mc-order-summary').replaceChildren();
    el('mc-page').textContent='';
    for(const key of ['first','prev','next','last'])el('mc-'+key).disabled=true;
    try {
      const d=await api('/api/orders?'+query);
      if(sequence!==orderSequence)return;
      page=d.page;pages=d.pages;
      accountOptions=d.accounts;botOptions=d.bots;
      options('mc-filter-accounts',accountOptions,accounts,a=>a.name);
      showBots(accounts,bots);
      const s=d.summary;
      fields(el('mc-order-summary'),[['订单笔数',s.total],['已结算投入',amount(s.invested)],['返还',amount(s.returned)],
        ['净收益',s.settled?amount(s.net):'暂无结算'],['待开奖',s.pending]]);
      renderOrders(d.rows);
      el('mc-page').textContent='第 '+page+' / '+pages+' 页';
      for(const key of ['first','prev'])el('mc-'+key).disabled=page<=1;
      for(const key of ['next','last'])el('mc-'+key).disabled=page>=pages;
    } catch(error) {
      if(sequence!==orderSequence)return;
      el('mc-order-list').replaceChildren();
      failure(error,()=>loadOrders(accounts));
    }
  }
  function renderOrders(rows) {
    const target=el('mc-order-list');
    if(!rows.length){target.replaceChildren(node('p','没有符合条件的订单'));return;}
    const table=node('table'), head=table.createTHead().insertRow(), body=table.createTBody(), cards=node('div',null,'mc-order-cards');
    for(const label of ['期号','下单账号','机器人','开奖时间（北京时间）','方向 / 金额','状态','净收益','操作'])head.append(node('th',label));
    for(const r of rows) {
      const direction=r.direction+' / '+amount(r.stake), state=statuses[r.status]||'—', pnl=amount(r.pnl);
      const row=body.insertRow();
      for(const value of [r.target_draw,r.account_name,r.bot_name,time(r.draw_time),direction,state,pnl])row.append(node('td',value));
      const button=node('button','详情','secondary');
      button.type='button';
      button.onclick=()=>detail(r.id);
      row.insertCell().append(button);
      const card=node('button',null,'mc-order-card');
      card.type='button';
      card.append(node('strong','第 '+r.target_draw+' 期 · '+state),node('span','开奖时间（北京时间） '+time(r.draw_time)),node('span',r.account_name+' · '+r.bot_name),node('span',direction+' · 净收益 '+pnl));
      card.onclick=()=>detail(r.id);
      cards.append(card);
    }
    const wrap=node('div',null,'mc-order-table');
    wrap.append(table);
    target.replaceChildren(wrap,cards);
  }
  async function detail(id) {
    const seq=++detailSequence, dialog=el('mc-detail'), content=el('mc-detail-body');
    content.replaceChildren(node('p','正在读取订单…'));
    if(!dialog.open)dialog.showModal();
    try {
      const r=await api('/api/order?id='+id);
      if(seq!==detailSequence)return;
      const result=r.result ? (r.a==null?'':r.a+' + '+r.b+' + '+r.c+' = '+r.total+' · ')+r.result : '—';
      const grid=node('div',null,'mc-metrics');
      fields(grid,[['订单编号','#'+r.id],['期号',r.target_draw],['下单账号',r.account_name],['机器人',r.bot_name],
        ['开奖时间（北京时间）',time(r.draw_time)],['记账时间（北京时间）',time(r.settled_at)],
        ['开奖',result],['订单方向',r.direction],['推送金额',amount(r.stake)],['状态',statuses[r.status]||'—'],
        ['结算倍数',r.odds_factor==null?'—':(r.odds_factor/100).toFixed(2)+'×'],['返还',amount(r.credit)],['净收益',amount(r.pnl)]]);
      content.replaceChildren(grid);
      if(navigator.clipboard) {
        const copy=node('button','复制订单编号','secondary');
        copy.type='button';
        copy.onclick=async()=>{
          try { await navigator.clipboard.writeText(String(r.id));copy.textContent='已复制'; }
          catch { copy.textContent='复制失败，可手动选取编号'; }
        };
        content.append(copy);
      }
    } catch(error) {
      if(seq!==detailSequence)return;
      if(error.status===401){expired();return;}
      const button=node('button','重试','secondary');
      button.onclick=()=>detail(id);
      content.replaceChildren(node('p',error.message),button);
    }
  }
  if(!signedIn)return;
  el('mc-login').hidden=true;
  el('mc-retry').onclick=()=>retry();
  for(const button of document.querySelectorAll('[data-mc-view]'))button.onclick=()=>navigate(button.dataset.mcView);
  el('mc-filters').onsubmit=e=>{e.preventDefault();page=1;loadOrders();};
  el('mc-filters').onreset=()=>{
    options('mc-filter-accounts',accountOptions,[],a=>a.name);showBots([],[]);
    if(view==='orders')setTimeout(()=>{page=1;loadOrders();},0);
  };
  el('mc-filter-accounts').onchange=()=>{
    updateSelection(el('mc-filter-accounts'));
    showBots(selected('mc-filter-accounts'),selected('mc-filter-bots'));
  };
  el('mc-filter-bots').onchange=()=>updateSelection(el('mc-filter-bots'));
  el('mc-size').onchange=()=>{page=1;loadOrders();};
  for(const key of ['first','prev','next','last'])el('mc-'+key).onclick=()=>{
    page={first:1,prev:page-1,next:page+1,last:pages}[key];loadOrders();
  };
  el('mc-detail').onclose=()=>{detailSequence++;};
  window.addEventListener('pagehide',()=>{el('mc-private').hidden=true;el('mc-user').textContent='';});
  window.addEventListener('pageshow',e=>{if(e.persisted)verify();});
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)verify();});
  setInterval(()=>{if(!document.hidden)verify();},60000);
  verify(true);
})();
