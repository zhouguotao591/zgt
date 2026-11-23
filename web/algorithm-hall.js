 'use strict';
(()=>{const el=id=>document.getElementById(id),admin=document.body.dataset.admin==='1',endpoint=admin?'/api/admin/':'/api/';let items=[],axis='all',statWindow='20',page=1,pages=1,busy=false,request=0,lockedIds=[],version='',detailId='',detailPage=1,detailPages=1,detailRequest=0,total=0,leaders=[],retryTimer=null,searchTimer=null;const rate=x=>x==null?'—':(x*100).toFixed(1)+'%',hit=r=>r.hit==null?'<span class="algo-neutral">未判断</span>':r.hit?'<span class="algo-good">命中</span>':'<span class="algo-bad">未中</span>';const metrics=r=>r.metrics[statWindow]??r.metrics['20'];
const moneyText=cents=>cents==null?'—':(cents/100).toLocaleString('zh-CN',{maximumFractionDigits:2})+' U';
const windowTitle=()=> '近'+statWindow+'期模拟命中率';
const sampleText=m=>m.wins.toLocaleString('zh-CN')+' / '+m.count.toLocaleString('zh-CN')+' 有效预测期';
function orderMetrics(a={}){
 return [['模拟已结算订单',a.orders==null?'—':num(a.orders)+' 笔'],['模拟投入',moneyText(a.stake)],['模拟净收益',moneyText(a.net)],['模拟投入收益率',a.return_rate==null?'—':(a.return_rate*100).toFixed(2)+'%']];
}
function orderCard(a){return orderMetrics(a).map(([label,value])=>'<div><span>'+label+'</span><b>'+value+'</b></div>').join('');}
function libraryCard(result){if(!result)return '<div class="algo-library-backtest"><strong>全历史自动回测</strong><p>尚无全历史回测结果。</p></div>';return `<div class="algo-library-backtest"><strong>全历史回测 · 固定每期100U</strong><div class="algo-streaks"><div><span>模拟命中率</span><b>${rate(result.win_rate)}</b></div><div><span>有效预测</span><b>${num(result.count)}期</b></div><div><span>回测净收益</span><b>${moneyText(result.net_cents)}</b></div><div><span>回测最大回撤</span><b>${moneyText(result.max_drawdown_cents)}</b></div></div><small>${esc(beijingTime(result.start).slice(0,10))}—${esc(beijingTime(result.end).slice(0,10))} · 截至${result.last_id}期 · 本地已核验${num(result.observed)}期；独立历史模拟，非模拟下单收益。</small></div>`;}
function algorithm(id){return [...items,...leaders].find(r=>r.id===id);}
function direction(r){return `<span class="algo-direction ${['大','双'].includes(r.pending.prediction)?'algo-direction-blue':'algo-direction-amber'}">${esc(r.pending.prediction||'—')}</span>`;}
function controls(r){return `<div class="algo-card-actions"><button class="secondary" data-algo-view="${esc(r.id)}">详情</button><button class="secondary" data-algo-income="${esc(r.id)}">收益 K 线</button><button class="secondary" data-algo-backtest="${esc(r.id)}">回测</button>${admin?`${!window.pc28Access||window.pc28Access.can('algorithm.publish')?`<button class="secondary" data-algo-publish="${esc(r.id)}">${r.published?'下架':'上架'}</button>`:''}`:`<button class="algo-favorite" data-algo-favorite="${esc(r.id)}" aria-pressed="${r.favorite}">${r.favorite?'取消收藏':'收藏'} ${r.favorites}</button>`}</div>`;}
function badge(r){const label=r.outcome==='skip'?'—':r.outcome==='refund'?'退':r.hit?'中':'失';return `<span class="algo-hit ${r.outcome==='skip'?'neutral':r.hit?'won':'lost'}" title="${esc('第'+r.target_draw+'期 · '+label+' · 模拟下注'+moneyText(r.stake)+' · 净收益'+moneyText(r.pnl))}">${label}</span>`;}
function pattern(r){const m=metrics(r),a=m.simulation;return `<div class="algo-pattern" data-pattern-id="${esc(r.id)}" data-cutoff="${r.pending.cutoff_draw}" data-page="1" data-total="${m.pattern_total}"><p>下期模拟金额 ${moneyText(r.pending.stake)} · 本窗口恢复100U ${num(a.resets)}次 · 历史 ${num(a.history_resets)}次</p><p>最近${statWindow}期模拟命中 · 最新在前 · <span class="algo-pattern-count">${(m.hits||[]).length}</span> / ${m.pattern_total}期</p><div class="algo-hit-scroll" tabindex="0" aria-label="${esc(r.name)} 模拟命中记录"><div class="algo-hit-grid">${(m.hits||[]).map(badge).join('')}</div></div><small class="algo-pattern-status">${m.pattern_total>100?'向下滚动加载更多；退表示命中退本，—表示未下注。':'退表示命中退本，—表示预热或缺期未下注。'}</small></div>`;}
function wirePatterns(root){root.querySelectorAll('.algo-pattern').forEach(pane=>{const scroller=pane.querySelector('.algo-hit-scroll');scroller.onscroll=async()=>{const grid=pane.querySelector('.algo-hit-grid'),count=grid.children.length;if(pane.dataset.busy==='1'||count>=Number(pane.dataset.total)||scroller.scrollTop+scroller.clientHeight<scroller.scrollHeight-40)return;pane.dataset.busy='1';const selectedWindow=statWindow,next=Number(pane.dataset.page)+1;try{const d=await api(endpoint+'algorithm-detail?'+new URLSearchParams({id:pane.dataset.patternId,source:'pattern',window:selectedWindow,page:next,page_size:100,cutoff:pane.dataset.cutoff}));if(!pane.isConnected||selectedWindow!==statWindow)return;grid.insertAdjacentHTML('beforeend',d.records.map(badge).join(''));pane.dataset.page=next;pane.querySelector('.algo-pattern-count').textContent=grid.children.length;pane.querySelector('.algo-pattern-status').textContent=grid.children.length>=d.total?'已显示本窗口全部记录。':'继续向下滚动加载更多。';}catch(e){if(pane.isConnected)pane.querySelector('.algo-pattern-status').textContent='加载未完成，请继续滚动重试。';}finally{pane.dataset.busy='0';}};});}
function render(){
 el('algo-page').textContent=`第 ${page} / ${pages} 页 · 共 ${total} 个算法`;
 for(const k of ['first','prev'])el('algo-'+k).disabled=page<=1;
 for(const k of ['next','last'])el('algo-'+k).disabled=page>=pages;
 el('algo-leaders-title').textContent=`近${statWindow}期模拟投入收益率前三`;
 el('algo-list-count').textContent=total;
 el('algo-list-window').textContent=`最近${statWindow}期 · 包含前三名 · 各算法独立统计`;
 el('algo-grid').innerHTML=leaders.length?leaders.map((r,i)=>{const m=metrics(r),a=m.simulation;return `<article class="algo-card algo-leader" data-algorithm-id="${esc(r.id)}"><div class="algo-card-head"><strong class="algo-rank">#${i+1}</strong><span class="algo-category">${esc(r.category)}${admin?' · '+(r.published?'已上架':'已下架'):''}</span></div><h3>${esc(r.name)}</h3><div class="algo-leader-main"><div><span>近${statWindow}期模拟投入收益率</span><strong class="${a.return_rate<0?'negative':'positive'}">${rate(a.return_rate)}</strong></div><div class="algo-leader-prediction"><span>下期预测</span>${direction(r)}</div></div><div class="algo-leader-metrics"><div><span>模拟命中率</span><b>${rate(m.rate)}</b></div><div><span>模拟净收益</span><b>${cash(a.net/100)} U</b></div><div><span>模拟已结算</span><b>${num(a.orders)} 笔</b></div></div><div class="algo-leader-metrics"><div><span>最长连中</span><b class="algo-good">${m.max_hit}期</b></div><div><span>最长连挂</span><b class="algo-bad">${m.max_miss}期</b></div><div><span>模拟投入</span><b>${moneyText(a.stake)}</b></div></div><p class="algo-tips">目标第 ${r.pending.target_draw??'—'} 期 · ${esc(r.pending.reason)}</p>${pattern(r)}<div class="algo-card-foot">${controls(r)}</div></article>`;}).join(''):'<div class="algo-empty">当前筛选暂无已结算收益率，算法和预测仍可在下方列表查看。</div>';
 const size=Number(el('algo-page-size').value);
 table('algo-list',[
  ['序号 / 算法',(r,i)=>`<div class="algo-table-name"><span class="algo-row-rank">${(page-1)*size+items.indexOf(r)+1}</span><div><strong>${esc(r.name)}</strong><small>${esc(r.category)}${admin?' · '+(r.published?'已上架':'已下架'):''}</small></div></div>`],
  ['当前连中 / 连挂',r=>`<strong class="algo-good">${metrics(r).current_hit}期连中</strong><small class="algo-bad">${metrics(r).current_miss}期连挂</small>`],
  ['历史命中 / 命中率',r=>`<strong>${rate(metrics(r).rate)}</strong><small>${sampleText(metrics(r))}</small>`],
  ['最长连中 / 连挂',r=>`<strong class="algo-good">${metrics(r).max_hit}期</strong><small class="algo-bad">${metrics(r).max_miss}期</small>`],
  ['窗口模拟收益率 / 净收益',r=>`<strong class="${metrics(r).simulation.return_rate==null?'algo-neutral':metrics(r).simulation.return_rate<0?'negative':'positive'}">${rate(metrics(r).simulation.return_rate)}</strong><small>${cash(metrics(r).simulation.net/100)} U</small>`],
  ['已结算 / 模拟投入',r=>`<strong>${num(metrics(r).simulation.orders)}笔</strong><small>${moneyText(metrics(r).simulation.stake)}</small>`],
  ['下期预测',r=>`${direction(r)}<small>第 ${r.pending.target_draw??'—'} 期</small>`],
  ['操作',controls]
 ],items);
 const root=el('algorithm-hall');wirePatterns(root);
 root.querySelectorAll('[data-algo-income]').forEach(b=>b.onclick=()=>openIncome(b.dataset.algoIncome));
 root.querySelectorAll('[data-algo-backtest]').forEach(b=>b.onclick=()=>openBacktest(b.dataset.algoBacktest));
 root.querySelectorAll('[data-algo-view]').forEach(b=>b.onclick=()=>{detailId=b.dataset.algoView;detailPage=1;el('algo-detail-source').value='history';el('algo-detail-title').textContent=algorithm(detailId).name;el('algo-detail-message').textContent='正在读取逐期明细…';el('algo-detail-table').innerHTML='';el('algo-detail-dialog').showModal();loadDetail();});
 root.querySelectorAll('[data-algo-publish]').forEach(b=>b.onclick=async()=>{b.disabled=true;try{const r=algorithm(b.dataset.algoPublish);await api('/api/admin/algorithm-publish',{id:r.id,published:!r.published});await load();}catch(e){toast(e.message);b.disabled=false;}});
 root.querySelectorAll('[data-algo-favorite]').forEach(b=>b.onclick=async()=>{b.disabled=true;try{const r=algorithm(b.dataset.algoFavorite);if(!token)token=(await api('/api/session')).token;await api('/api/algorithm-favorite',{id:r.id,favorite:!r.favorite});version='';await load();}catch(e){toast(e.message);b.disabled=false;}});
}
async function load(poll=false){
 if(document.body.dataset.authenticated==='0'){window.pc28Auth?.open('login','algorithm-hall');return;}
 if(poll===true&&busy)return;
 const seq=++request;busy=true;clearTimeout(retryTimer);
 if(!items.length)el('algo-message').textContent='正在读取算法统计…';
 const q=new URLSearchParams({page,page_size:el('algo-page-size').value,axis,window:statWindow,sort:el('algo-sort').value,search:el('algo-search').value,favorites:el('algo-favorites').checked?'1':'0',lock:el('algo-lock').checked?'1':'0'});
 if(el('algo-lock').checked&&lockedIds.length)q.set('order',lockedIds.join(','));
 if(version)q.set('known',version);
 try{
  const d=await api(endpoint+'algorithms?'+q);if(seq!==request)return;
  if(d.ready&&!d.unchanged&&d.algorithms.some(r=>!r.metrics?.[statWindow]?.simulation)){el('algo-message').textContent='独立模拟账本正在升级，完成后自动显示。';return;}
  el('algo-message').textContent=d.refresh_error?'统计更新暂未成功，当前显示上次完成结果，系统会自动重试。':d.updating?(d.ready?'正在更新最新统计，先显示上次完成结果。':'历史模拟正在后台补算，完成后自动显示。'):'';
  if(d.simulation_progress?.running)el('algo-message').textContent+=' 模拟进度 '+d.simulation_progress.completed+' / '+d.simulation_progress.total+' 个算法。';
  if(d.ready&&!d.unchanged){
   items=d.algorithms;leaders=d.leaders||[];page=d.page;pages=d.pages;total=d.total;
   if(el('algo-lock').checked)lockedIds=d.ranking;
   el('algo-cutoff').textContent=(d.latest_id?'数据截至第 '+d.latest_id+' 期':'本地暂无已确认开奖')+(d.updated_at?' · 更新于 '+beijingTime(new Date(d.updated_at*1000).toISOString()):'');
   el('algo-note').textContent=d.note;render();
   if(el('algo-detail-dialog').open&&version&&version!==d.version)await loadDetail();
   version=d.version;
  }
  if(d.updating)retryTimer=setTimeout(()=>{if(!document.hidden&&el('algorithm-hall').classList.contains('active'))load(true);},1500);
 }catch(e){if(seq===request)el('algo-message').textContent='读取失败：'+e.message;}finally{if(seq===request)busy=false;}
}
async function loadDetail(){const seq=++detailRequest;el('algo-detail-message').textContent='读取中…';try{const d=await api(endpoint+'algorithm-detail?'+new URLSearchParams({id:detailId,page:detailPage,page_size:el('algo-detail-size').value,window:statWindow,source:el('algo-detail-source').value}));if(seq!==detailRequest)return;detailPage=d.page;detailPages=d.pages;el('algo-detail-title').textContent=d.name;el('algo-detail-category').textContent=d.category;el('algo-detail-rule').textContent=d.rule+' · 独立模拟：基础100U，赢后回100U，亏后加25U至200U，200U输后下一单回100U。';const m=metrics(d);el('algo-detail-metrics').innerHTML=[[windowTitle(),rate(m.rate)],['模拟有效下注期',sampleText(m)],['当前连中',m.current_hit],['当前连挂',m.current_miss],['最大连中',m.max_hit],['最大连挂',m.max_miss],...orderMetrics(m.simulation)].map(([k,v])=>`<div><span>${esc(k)}</span><b>${esc(v)}</b></div>`).join('');el('algo-detail-next').innerHTML=`下一期模拟预测 <b>${d.pending.target_draw??'—'}</b> · <strong>${esc(d.pending.prediction||'暂无判断')}</strong> · ${moneyText(d.pending.stake)}`;el('algo-detail-record-title').textContent='独立模拟订单明细';table('algo-detail-table',[['开奖时间（北京）',r=>esc(beijingTime(r.time))],['期号','target_draw'],['模拟方向','prediction'],['开奖','formula'],['命中',hit],['模拟金额',r=>moneyText(r.stake)],['结算',r=>esc(({win:'盈利',loss:'亏损',refund:'退本金'})[r.outcome]||'未下注')],['模拟净收益',r=>moneyText(r.pnl)],['余额变化',r=>moneyText(r.balance_before)+' → '+moneyText(r.after_stake_balance)+' → '+moneyText(r.balance_after)],['下一单金额',r=>moneyText(r.next_stake)]],d.records);el('algo-detail-page').textContent=`第 ${d.page} / ${d.pages} 页 · 共 ${d.total} 笔`;for(const k of ['first','prev'])el('algo-detail-'+k).disabled=d.page<=1;el('algo-detail-next-page').disabled=el('algo-detail-last').disabled=d.page>=d.pages;el('algo-detail-message').textContent='';}catch(e){if(seq===detailRequest)el('algo-detail-message').textContent=e.message;}}

let incomeId='',incomeInterval='1h',incomeRequest=0;
function openIncome(id){
 incomeId=id;incomeInterval='1h';
 el('algo-income-title').textContent=[...items,...leaders].find(r=>r.id===id).name+' · 历史收益 K 线';
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
 el('algo-backtest-title').textContent=[...items,...leaders].find(r=>r.id===id).name+' · 回测';
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
function filters(){page=1;lockedIds=[];version='';load();}el('algo-categories').querySelectorAll('button').forEach(b=>b.onclick=()=>{axis=b.dataset.axis;el('algo-categories').querySelectorAll('button').forEach(x=>x.classList.toggle('selected',x===b));filters();});for(const id of ['algo-sort','algo-favorites'])el(id).onchange=filters;el('algo-search').oninput=()=>{clearTimeout(searchTimer);searchTimer=setTimeout(filters,250);};el('algo-page-size').onchange=filters;el('algo-lock').onchange=()=>{lockedIds=[];version='';load();};el('algo-refresh').onclick=load;el('algo-detail-close').onclick=()=>el('algo-detail-dialog').close();for(const k of ['first','prev','next','last'])el('algo-'+k).onclick=()=>{page=({first:1,prev:Math.max(1,page-1),next:Math.min(pages,page+1),last:pages})[k];load();};el('algo-detail-source').onchange=()=>{detailPage=1;loadDetail();};el('algo-detail-size').onchange=()=>{detailPage=1;loadDetail();};for(const k of ['first','prev','next-page','last'])el('algo-detail-'+k).onclick=()=>{detailPage=({first:1,prev:Math.max(1,detailPage-1),'next-page':Math.min(detailPages,detailPage+1),last:detailPages})[k];loadDetail();};document.querySelector('[data-tab="algorithm-hall"]').addEventListener('click',load);el('refresh').addEventListener('click',()=>{if(el('algorithm-hall').classList.contains('active'))load();});setInterval(()=>{if(!document.hidden&&el('algorithm-hall').classList.contains('active'))load(true);},10000);})();
