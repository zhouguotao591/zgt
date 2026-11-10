 'use strict';
(()=>{const el=id=>document.getElementById(id),admin=document.body.dataset.admin==='1',endpoint=admin?'/api/admin/':'/api/';let items=[],axis='all',statWindow='20',page=1,pages=1,busy=false,request=0,lockedIds=[],version='',detailId='',detailPage=1,detailPages=1,detailRequest=0;const rate=x=>x==null?'—':(x*100).toFixed(1)+'%',hit=r=>r.hit==null?'<span class="algo-neutral">未判断</span>':r.hit?'<span class="algo-good">命中</span>':'<span class="algo-bad">未中</span>';const metrics=r=>r.metrics[statWindow]??r.metrics['20'];
const moneyText=cents=>cents==null?'—':(cents/100).toLocaleString('zh-CN',{maximumFractionDigits:2})+' U';
const windowTitle=()=> '最近'+statWindow+'期胜率';
const sampleText=m=>m.wins.toLocaleString('zh-CN')+' / '+m.count.toLocaleString('zh-CN')+' 有效开单期';
function orderMetrics(a={}){
 return [['已结算订单',a.orders==null?'—':num(a.orders)+' 笔'],['窗口投入',moneyText(a.stake)],['窗口净收益',moneyText(a.net)],['投入收益率',a.return_rate==null?'—':(a.return_rate*100).toFixed(2)+'%']];
}
function orderCard(a){return orderMetrics(a).map(([label,value])=>'<div><span>'+label+'</span><b>'+value+'</b></div>').join('');}
function compare(a,b){const sort=el('algo-sort').value;if(sort==='name')return a.name.localeCompare(b.name,'zh-CN');const av=sort==='favorites'?a.favorites:metrics(a)[sort];const bv=sort==='favorites'?b.favorites:metrics(b)[sort];return (bv??-1)-(av??-1)||a.id.localeCompare(b.id);}
function selected(){const term=el('algo-search').value.trim().toLowerCase();return items.filter(r=>(axis==='all'||r.axis===axis)&&(!term||(r.name+' '+r.id).toLowerCase().includes(term))&&(!el('algo-favorites').checked||r.favorite));}
function render(resetOrder=false){const rows=selected();if(el('algo-lock').checked&&!resetOrder&&lockedIds.length){const rank=new Map(lockedIds.map((id,i)=>[id,i]));rows.sort((a,b)=>(rank.get(a.id)??999)-(rank.get(b.id)??999)||compare(a,b));}else{rows.sort(compare);lockedIds=rows.map(r=>r.id);}const size=Number(el('algo-page-size').value);pages=Math.max(1,Math.ceil(rows.length/size));page=Math.max(1,Math.min(page,pages));el('algo-page').textContent=`第 ${page} / ${pages} 页 · 共 ${rows.length} 个算法`;for(const k of ['first','prev'])el('algo-'+k).disabled=page<=1;for(const k of ['next','last'])el('algo-'+k).disabled=page>=pages;el('algo-grid').innerHTML=rows.length?rows.slice((page-1)*size,page*size).map(r=>{const m=metrics(r),p=r.pending;return `<article class="algo-card" data-algorithm-id="${esc(r.id)}"><div class="algo-card-head"><div><span class="algo-category">${esc(r.category)}</span><h3>${esc(r.name)}</h3></div>${admin?`<span>${r.published?'已上架':'已下架'}</span>${!window.pc28Access||window.pc28Access.can('algorithm.publish')?`<button class="secondary" data-algo-publish="${esc(r.id)}">${r.published?'下架':'上架'}</button>`:''}`:`<button class="algo-favorite" data-algo-favorite="${esc(r.id)}" aria-pressed="${r.favorite}" aria-label="${r.favorite?'取消收藏':'收藏'} ${esc(r.name)}">${r.favorite?'★':'☆'} ${r.favorites}</button>`}</div><div class="algo-rates"><div class="algo-rate algo-rate-all selected"><span>${windowTitle()}</span><strong>${rate(m.rate)}</strong><small>${sampleText(m)}</small></div></div><div class="algo-streaks algo-paper">${orderCard(m.actual)}</div><div class="algo-streaks">${[['当前连中','current_hit'],['当前连挂','current_miss'],['最大连中','max_hit'],['最大连挂','max_miss']].map(([label,key])=>`<div><span>${label}</span><b>${m[key]}</b></div>`).join('')}</div><div class="algo-next">下一期参考预测 ${p.target_draw??'—'}<strong>${esc(p.prediction||'暂无判断')}</strong><div class="algo-tips">${esc(p.reason)}</div></div><div class="algo-card-foot"><span>实时开单记录</span><div class="algo-card-actions"><button class="secondary" data-algo-income="${esc(r.id)}">收益 K 线</button><button class="secondary" data-algo-view="${esc(r.id)}">详情</button><button class="secondary" data-algo-backtest="${esc(r.id)}">回测</button></div></div></article>`;}).join(''):'<div class="algo-empty"><strong>没有符合条件的算法</strong><p>换个关键词，或切回“全部”查看。</p><button class="secondary" id="algo-reset">重置筛选</button></div>';el('algo-reset')?.addEventListener('click',()=>{axis='all';el('algo-search').value='';el('algo-favorites').checked=false;el('algo-categories').querySelectorAll('button').forEach(b=>b.classList.toggle('selected',b.dataset.axis==='all'));page=1;render(true);});el('algo-grid').querySelectorAll('[data-algo-income]').forEach(b=>b.onclick=()=>openIncome(b.dataset.algoIncome));el('algo-grid').querySelectorAll('[data-algo-backtest]').forEach(b=>b.onclick=()=>openBacktest(b.dataset.algoBacktest));el('algo-grid').querySelectorAll('[data-algo-view]').forEach(b=>b.onclick=()=>{detailId=b.dataset.algoView;detailPage=1;el('algo-detail-title').textContent=items.find(r=>r.id===detailId).name;el('algo-detail-message').textContent='正在读取逐期明细…';el('algo-detail-table').innerHTML='';el('algo-detail-dialog').showModal();loadDetail();});el('algo-grid').querySelectorAll('[data-algo-publish]').forEach(b=>b.onclick=async()=>{
 b.disabled=true;
 const algorithm=items.find(r=>r.id===b.dataset.algoPublish);
 try{await api('/api/admin/algorithm-publish',{id:algorithm.id,published:!algorithm.published});await load();}
 catch(error){toast(error.message);b.disabled=false;}
});el('algo-grid').querySelectorAll('[data-algo-favorite]').forEach(b=>b.onclick=async()=>{b.disabled=true;const r=items.find(r=>r.id===b.dataset.algoFavorite);try{if(!token)token=(await api('/api/session')).token;await api('/api/algorithm-favorite',{id:r.id,favorite:!r.favorite});r.favorites+=r.favorite?-1:1;r.favorite=!r.favorite;render();}catch(e){toast(e.message);b.disabled=false;}});}
async function load(){if(document.body.dataset.authenticated==='0'){window.pc28Auth?.open('login','algorithm-hall');return;}if(busy)return;busy=true;if(!items.length)el('algo-message').textContent='正在计算本地算法统计…';try{const d=await api(endpoint+'algorithms');items=d.algorithms;el('algo-cutoff').textContent=d.latest_id?'数据截至第 '+d.latest_id+' 期':'本地暂无已确认开奖';el('algo-note').textContent=d.note;el('algo-message').textContent='';render();if(el('algo-detail-dialog').open){if(!items.some(r=>r.id===detailId))el('algo-detail-dialog').close();else if(version&&version!==d.version)await loadDetail();}version=d.version;}catch(e){el('algo-message').textContent='读取失败：'+e.message;}finally{busy=false;}}
async function loadDetail(){const seq=++detailRequest;el('algo-detail-message').textContent='读取中…';try{const d=await api(endpoint+'algorithm-detail?'+new URLSearchParams({id:detailId,page:detailPage,page_size:el('algo-detail-size').value,window:statWindow}));if(seq!==detailRequest)return;detailPage=d.page;detailPages=d.pages;el('algo-detail-title').textContent=d.name;el('algo-detail-category').textContent=d.category;el('algo-detail-rule').textContent=d.rule+(admin?' 观察窗口 '+d.lookback+' 期 · 规则版本 '+(items.find(r=>r.id===d.id)?.version||'—'):'');const m=metrics(d);el('algo-detail-metrics').innerHTML=[[windowTitle(),rate(m.rate)],['有效开单期',sampleText(m)],['当前连中',m.current_hit],['当前连挂',m.current_miss],['最大连中',m.max_hit],['最大连挂',m.max_miss],...orderMetrics(m.actual)].map(([k,v])=>`<div><span>${esc(k)}</span><b>${esc(v)}</b></div>`).join('');el('algo-detail-next').innerHTML=`下一期参考预测 <b>${d.pending.target_draw??'—'}</b> · 判断 <strong>${esc(d.pending.prediction||'暂无判断')}</strong><div>数据截止第 ${d.pending.cutoff_draw??'—'} 期 · ${esc(d.pending.reason)}</div>`;table('algo-detail-table',[['开奖时间（北京）',r=>esc(beijingTime(r.time))],['期号','target_draw'],['算法判断',r=>esc(r.prediction||'—')],['开奖号码','formula'],['判断结果',hit],['开单金额',r=>moneyText(r.stake)],['结算',r=>esc(({win:'盈利',loss:'亏损',refund:'退本金'})[r.outcome]||r.simulation_skip||'—')],['返还',r=>moneyText(r.credit)],['净收益',r=>moneyText(r.pnl)],['数据截止','cutoff_draw'],['依据',r=>`<div class="algo-tips">${esc(r.reason)}</div>`]],d.records);el('algo-detail-page').textContent=`第 ${d.page} / ${d.pages} 页 · 共 ${d.total} 期`;for(const k of ['first','prev'])el('algo-detail-'+k).disabled=d.page<=1;el('algo-detail-next-page').disabled=el('algo-detail-last').disabled=d.page>=d.pages;el('algo-detail-message').textContent='';}catch(e){if(seq===detailRequest)el('algo-detail-message').textContent=e.message;}}
let incomeId='',incomeInterval='1h',incomeRequest=0;
function openIncome(id){
 incomeId=id;incomeInterval='1h';
 el('algo-income-title').textContent=items.find(r=>r.id===id).name+' · 历史收益 K 线';
 el('algo-income-chart').replaceChildren();
 el('algo-income-dialog').showModal();
 loadIncome();
}
async function loadIncome(){
 const seq=++incomeRequest;
 const status=el('algo-income-status'),chart=el('algo-income-chart');
 status.textContent='正在读取已结算开单收益…';
 try{
  const data=await api(endpoint+'algorithm-detail?'+new URLSearchParams({id:incomeId,interval:incomeInterval}));
  if(seq!==incomeRequest||!el('algo-income-dialog').open)return;
  chart.innerHTML=window.pc28IncomeChart(data.candles,incomeInterval,'暂无符合口径的已结算开单记录',true);
  status.textContent=data.start?'北京时间 '+beijingTime(data.start)+' 至 '+beijingTime(data.end)+' · 已结算 '+num(data.orders)+' 笔':'';
  chart.querySelectorAll('[data-k-interval]').forEach(b=>b.onclick=()=>{incomeInterval=b.dataset.kInterval;loadIncome();});
  const scroller=chart.querySelector('.hb-k-scroll');
  if(scroller)scroller.scrollLeft=scroller.scrollWidth;
 }catch(error){if(seq===incomeRequest)status.textContent='读取失败：'+error.message;}
}
el('algo-income-close').onclick=()=>el('algo-income-dialog').close();
el('algo-income-dialog').addEventListener('close',()=>{incomeRequest++;});
let backtestId='',backtestPeriods='',backtestInterval='1h',backtestCutoff=null,backtestRequest=0;
const backtestDialog=el('algo-backtest-dialog'),backtestInput=el('algo-backtest-periods');
const backtestChoices=backtestDialog.querySelectorAll('[data-backtest-period]');
function highlightBacktestPeriod(){
 for(const button of backtestChoices){
  const selected=button.dataset.backtestPeriod===backtestInput.value;
  button.classList.toggle('selected',selected);
  button.setAttribute('aria-pressed',String(selected));
 }
}
function openBacktest(id){
 backtestId=id;backtestPeriods='';backtestInterval='1h';
 el('algo-backtest-title').textContent=items.find(r=>r.id===id).name+' · 回测';
 backtestInput.value='20';highlightBacktestPeriod();
 el('algo-backtest-result').hidden=true;el('algo-backtest-status').textContent='';
 el('algo-backtest-run').disabled=false;
 backtestDialog.showModal();
}
async function runBacktest(){
 const request=++backtestRequest,interval=backtestInterval;
 const status=el('algo-backtest-status'),result=el('algo-backtest-result'),chart=el('algo-backtest-chart');
 el('algo-backtest-run').disabled=true;
 status.textContent='正在回测…';
 try{
  const query=new URLSearchParams({id:backtestId,backtest:backtestPeriods,interval});
  if(backtestCutoff!==null)query.set('cutoff',backtestCutoff);
  const data=await api(endpoint+'algorithm-detail?'+query);
  if(request!==backtestRequest||!backtestDialog.open)return;
  backtestCutoff=data.last_draw;
  const metrics=[['净收益',moneyText(data.net)],['最大回撤',moneyText(data.max_drawdown)],
    ['累计开单金额',moneyText(data.investment)],['开单方向胜率',rate(data.win_rate)],
    ['命中 / 开单',num(data.wins)+' / '+num(data.orders)],['退本订单',num(data.refunds)+' 笔']];
  el('algo-backtest-metrics').innerHTML=metrics.map(([label,value])=>'<div><span>'+esc(label)+'</span><b>'+esc(value)+'</b></div>').join('');
  table('algo-backtest-directions',[['方向','direction'],['开奖出现','actual'],['算法预测','predicted'],['模拟开单','orders']],
    Object.entries(data.directions).map(([direction,counts])=>({direction,...counts})));
  const verified=Object.values(data.directions).reduce((sum,row)=>sum+row.actual,0);
  status.textContent=data.last_draw==null?'暂无已核验开奖':
    '第 '+data.first_draw+' 至 '+data.last_draw+' 期 · 请求 '+num(data.periods)+' 期 · 已核验 '+num(verified)+' 期';
  chart.innerHTML=window.pc28IncomeChart(data.candles,interval,'本次回测没有已结算模拟订单',true);
  chart.querySelectorAll('[data-k-interval]').forEach(button=>button.onclick=()=>{
   if(backtestInterval===button.dataset.kInterval)return;
   backtestInterval=button.dataset.kInterval;runBacktest();
  });
  const scroller=chart.querySelector('.hb-k-scroll');
  if(scroller)scroller.scrollLeft=scroller.scrollWidth;
  result.hidden=false;
 }catch(error){if(request===backtestRequest)status.textContent='回测失败：'+error.message;}
 finally{if(request===backtestRequest)el('algo-backtest-run').disabled=false;}
}
for(const button of backtestChoices)button.onclick=()=>{backtestInput.value=button.dataset.backtestPeriod;highlightBacktestPeriod();};
backtestInput.oninput=highlightBacktestPeriod;
el('algo-backtest-form').onsubmit=event=>{
 event.preventDefault();backtestPeriods=backtestInput.value;backtestInterval='1h';backtestCutoff=null;
 el('algo-backtest-result').hidden=true;runBacktest();
};
el('algo-backtest-close').onclick=()=>backtestDialog.close();
backtestDialog.addEventListener('close',()=>{backtestRequest++;});
if(admin)el('algo-favorites').closest('label').hidden=true;
for(const button of el('algo-window').querySelectorAll('[data-window]')){
 button.onclick=()=>{
  if(statWindow===button.dataset.window)return;
  statWindow=button.dataset.window;
  for(const choice of el('algo-window').querySelectorAll('[data-window]')){
   const active=choice===button;
   choice.classList.toggle('selected',active);
   choice.setAttribute('aria-pressed',String(active));
  }
  filters();
  if(el('algo-detail-dialog').open){detailPage=1;loadDetail();}
 };
}
function filters(){page=1;render(true);}el('algo-categories').querySelectorAll('button').forEach(b=>b.onclick=()=>{axis=b.dataset.axis;el('algo-categories').querySelectorAll('button').forEach(x=>x.classList.toggle('selected',x===b));filters();});for(const id of ['algo-sort','algo-favorites'])el(id).onchange=filters;el('algo-search').oninput=filters;el('algo-page-size').onchange=()=>{page=1;render();};el('algo-lock').onchange=()=>render(true);el('algo-refresh').onclick=load;el('algo-detail-close').onclick=()=>el('algo-detail-dialog').close();for(const k of ['first','prev','next','last'])el('algo-'+k).onclick=()=>{page=({first:1,prev:Math.max(1,page-1),next:Math.min(pages,page+1),last:pages})[k];render();};el('algo-detail-size').onchange=()=>{detailPage=1;loadDetail();};for(const k of ['first','prev','next-page','last'])el('algo-detail-'+k).onclick=()=>{detailPage=({first:1,prev:Math.max(1,detailPage-1),'next-page':Math.min(detailPages,detailPage+1),last:detailPages})[k];loadDetail();};document.querySelector('[data-tab="algorithm-hall"]').addEventListener('click',load);el('refresh').addEventListener('click',()=>{if(el('algorithm-hall').classList.contains('active'))load();});setInterval(()=>{if(el('algorithm-hall').classList.contains('active'))load();},10000);})();