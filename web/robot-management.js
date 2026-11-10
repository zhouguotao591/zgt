(()=>{'use strict';if(document.body.dataset.admin!=='1')return;
const $=id=>document.getElementById('robot-'+id),E=esc;let data,bindingEditing=null,page=1,pages=1;const selectedRecords=new Set();let recordRows=[];
const statuses={pending:'延迟缓存/待发送',sending:'发送中',sent:'接口已接收',retry:'等待重试',failed:'失败',expired:'已过期',cancelled:'已取消'};
const call=(p,b)=>api('/api/admin/robot'+p,b);
async function action(button,fn){button.disabled=true;try{await fn();}catch(e){toast(e.message);}finally{button.disabled=false;}}
async function records(){const d=await call('-records?'+new URLSearchParams({page,page_size:$('size').value,status:$('status').value,...window.pc28RobotRecordFilters}));page=d.page;pages=d.pages;recordRows=d.rows;const visible=new Set(d.rows.filter(r=>deletable(r)).map(r=>r.id));for(const id of selectedRecords)if(!visible.has(id))selectedRecords.delete(id);$('page').textContent=`第 ${page} / ${pages} 页 · 共 ${d.total} 条消息`;for(const k of ['first','prev'])$(k).disabled=page<=1;for(const k of ['next','last'])$(k).disabled=page>=pages;const selection=r=>`<input type="checkbox" data-record-select="${r.id}" aria-label="选择第${r.target_draw||''}期记录" ${selectedRecords.has(r.id)?'checked':''} ${deletable(r)?'':'disabled'}>`;
const actions=r=>`<div class="admin-actions"><button class="secondary" data-ra-batch="${r.id}">查看详情</button>${['failed','retry'].includes(r.status)?`<button class="secondary" data-robot-retry="${r.id}">重试合并消息</button>`:''}<button class="secondary" data-record-delete="${r.id}" ${deletable(r)?'':'disabled'}>删除</button></div>`;
table('robot-records', [
 ['会员',r=>selection(r)+E(r.recipient_name)],
 ['下单账号',r=>E(r.account_name||'历史账号')],
 ['机器人',r=>E(r.bot_name||'历史机器人')],
 ['期号','target_draw'],
 ['合并内容','message'],
 ['实际发送时间',r=>E(beijingTime(r.sent_at))],
 ['成功接收时间',r=>E(beijingTime(r.accepted_at))],
 ['结果',r=>E(r.last_error||(r.last_status?'HTTP '+r.last_status:'—'))+'<details class="robot-record-actions"><summary>记录操作</summary>'+actions(r)+'</details>'],
 ['状态',r=>E(statuses[r.status]||r.status)]
],d.rows);$('records').querySelectorAll('[data-record-select]').forEach(b=>b.onchange=()=>{const id=Number(b.dataset.recordSelect);b.checked?selectedRecords.add(id):selectedRecords.delete(id);selectionState();});$('records').querySelectorAll('[data-record-delete]').forEach(b=>b.onclick=()=>removeRecords([Number(b.dataset.recordDelete)],b));selectionState();$('records').querySelectorAll('[data-robot-retry]').forEach(b=>b.onclick=()=>action(b,async()=>{await call('-retry',{id:Number(b.dataset.robotRetry)});await records();}));}

function deletable(r){return ['sent','failed','expired','cancelled'].includes(r.status);}
function selectionState(){const eligible=recordRows.filter(deletable);$('selected-count').textContent=`已选 ${selectedRecords.size} 条`;$('delete-selected').disabled=!selectedRecords.size;$('select-all').disabled=!eligible.length;$('select-all').checked=!!eligible.length&&eligible.every(r=>selectedRecords.has(r.id));$('select-all').indeterminate=selectedRecords.size>0&&!$('select-all').checked;}
async function removeRecords(ids,button){if(!ids.length||!confirm(`确定删除这 ${ids.length} 条合并发送记录？订单、收益和去重数据会保留。`))return;await action(button,async()=>{await call('-records-delete',{ids});selectedRecords.clear();await records();toast('发送记录已删除');});selectionState();}
$('select-all').onchange=()=>{selectedRecords.clear();if($('select-all').checked)recordRows.filter(deletable).forEach(r=>selectedRecords.add(r.id));$('records').querySelectorAll('[data-record-select]').forEach(b=>b.checked=selectedRecords.has(Number(b.dataset.recordSelect)));selectionState();};$('delete-selected').onclick=e=>removeRecords([...selectedRecords],e.target);

async function load(){data=await call('');window.dispatchEvent(new CustomEvent('pc28:robot-meta',{detail:data}));const c=data.config;$('endpoint').value=data.endpoint;$('secret-state').textContent=data.token_configured?'（已配置）':'（未配置）';$('token').value='';$('enabled').checked=c.enabled;$('timeout').value=c.timeout_seconds;$('attempts').value=c.max_attempts;$('age').value=c.max_age_seconds;$('interval').value=c.send_interval_seconds;$('account').value=c.account_id;$('bot').value=c.bot_key;$('merge').value=c.merge_wait_seconds;$('delay-min').value=c.delay_min_seconds;$('delay-max').value=c.delay_max_seconds;
renderPlatforms();
table('robot-bindings',[['平台','platform_name'],['会员',r=>E(r.username)+(r.member_active?'':'（已停用）')],['接收账号','account_id'],['机器人','bot_key'],['信号类型','signal_type'],['默认每单金额',r=>num(r.default_cents/100)+' U'],['订阅策略',r=>E(r.strategy_ids.map(id=>data.strategies.find(s=>s.id===id)?.name||id).join('、'))],['状态',r=>r.enabled?'已启用':'已关闭'],['管理',r=>`<div class="admin-actions"><button class="secondary" data-binding-edit="${r.id}">编辑</button><button class="secondary" data-binding-toggle="${r.id}">${r.enabled?'关闭':'启用'}</button></div>`]],data.bindings);$('bindings').querySelectorAll('[data-binding-edit]').forEach(b=>b.onclick=()=>editBinding(data.bindings.find(r=>r.id===Number(b.dataset.bindingEdit))));$('bindings').querySelectorAll('[data-binding-toggle]').forEach(b=>b.onclick=()=>action(b,async()=>{const r=data.bindings.find(r=>r.id===Number(b.dataset.bindingToggle));await call('-binding-control',{id:r.id,enabled:!r.enabled});await load();}));
await records();}
$('refresh').onclick=e=>action(e.target,load);
$('config-form').onsubmit=async e=>{e.preventDefault();await action(e.submitter,async()=>{const c={enabled:$('enabled').checked,timeout_seconds:Number($('timeout').value),max_attempts:Number($('attempts').value),max_age_seconds:Number($('age').value),send_interval_seconds:Number($('interval').value),account_id:Number($('account').value),bot_key:Number($('bot').value),merge_wait_seconds:Number($('merge').value),delay_min_seconds:Number($('delay-min').value),delay_max_seconds:Number($('delay-max').value)};if($('token').value)c.token=$('token').value;await call('-config',c);await load();$('config-message').textContent='已保存。仅处理启用后新生成且允许推送的策略订单。';});};
for(const k of ['first','prev','next','last'])$(k).onclick=()=>{page=({first:1,prev:Math.max(1,page-1),next:Math.min(pages,page+1),last:pages})[k];records().catch(e=>toast(e.message));};for(const k of ['size','status'])$(k).onchange=()=>{page=1;records().catch(e=>toast(e.message));};document.querySelector('[data-tab="admin-robot"]').addEventListener('click',()=>load().catch(e=>toast(e.message)));setInterval(()=>{if($(''+ 'records')&&document.getElementById('admin-robot').classList.contains('active')&&!$('binding-dialog').open)records().catch(()=>{});},5000);
function editBinding(r){$('binding-enabled').disabled=!!r&&!!window.pc28Access&&!window.pc28Access.can('robot.control');if(!data.members.length){toast('当前没有会员，请先在邀请码管理生成邀请码，完成会员注册后再绑定');return;}bindingEditing=r?.id||null;$('binding-platform').innerHTML=data.platforms.map(p=>`<option value="${p.id}">${E(p.name)}${p.enabled?'':'（已停用）'}</option>`).join('');$('binding-platform').value=r?.platform_id||1;$('binding-title').textContent=r?'编辑会员推送':'添加会员推送';$('binding-user').innerHTML=data.members.map(u=>`<option value="${u.id}">${E(u.username)}${u.active?'':'（已停用）'}</option>`).join('');if(r)$('binding-user').value=r.user_id;$('binding-enabled').checked=r?.enabled||false;$('binding-account').value=r?.account_id||data.config.account_id||1;$('binding-bot').value=r?.bot_key||data.config.bot_key||'';$('binding-amount').value=r?r.default_cents/100:10;$('binding-type').value=r?.signal_type||'BUY_SYSTEM';$('binding-strategies').innerHTML=data.strategies.map(t=>`<option value="${E(t.id)}" ${r?.strategy_ids.includes(t.id)?'selected':''}>${E(t.name)}${t.enabled?'':'（策略停用）'}</option>`).join('');$('binding-amounts').innerHTML='';renderAmounts(r?.strategy_amounts||{});$('binding-error').textContent='';$('binding-dialog').showModal();}
function amountSettings(){return Object.fromEntries([...$('binding-amounts').querySelectorAll('[data-amount-row]')].map(row=>{const value=row.querySelector('input').value.trim(),mode=row.querySelector('select').value;if(!value)return [row.dataset.amountRow,null];if(mode==='fixed')return [row.dataset.amountRow,{mode,cents:stakeCents(value)}];if(!/^\d+(?:\.\d{1,4})?$/.test(value))throw Error('比例最多支持4位小数');const units=Math.round(Number(value)*10000);if(units<1||units>1000000)throw Error('比例须为0.0001—100倍');return [row.dataset.amountRow,{mode,ratio_units:units}];}).filter(([,v])=>v));}
function renderAmounts(saved){$('binding-amounts').innerHTML=[...$('binding-strategies').selectedOptions].map(o=>{const v=saved[o.value],mode=v?.mode||'fixed',value=typeof v==='number'?v/100:mode==='ratio'?v.ratio_units/10000:v?.cents?v.cents/100:'';return `<div data-amount-row="${E(o.value)}" class="robot-amount-row"><span>${E(data.strategies.find(t=>t.id===o.value)?.name||o.value)}</span><select aria-label="${E(o.textContent)} 金额模式"><option value="fixed" ${mode==='fixed'?'selected':''}>固定金额（U）</option><option value="ratio" ${mode==='ratio'?'selected':''}>模拟订单比例（倍）</option></select><input aria-label="${E(o.textContent)} 金额或比例" type="number" min="${mode==='ratio'?0.0001:1}" max="${mode==='ratio'?100:20000}" step="${mode==='ratio'?0.0001:0.01}" placeholder="${mode==='ratio'?'例如0.5、1':'留空使用会员默认金额'}" value="${value}"></div>`;}).join('');$('binding-amounts').querySelectorAll('select').forEach(select=>select.onchange=()=>{const input=select.parentElement.querySelector('input'),ratio=select.value==='ratio';input.value=ratio?'1':'';input.min=ratio?'0.0001':'1';input.max=ratio?'100':'20000';input.step=ratio?'0.0001':'0.01';input.placeholder=ratio?'例如0.5、1':'留空使用会员默认金额';});}
$('binding-strategies').onchange=()=>{try{renderAmounts(amountSettings());}catch(e){$('binding-error').textContent=e.message;}};
$('binding-add').onclick=()=>editBinding(null);$('binding-close').onclick=()=>$('binding-dialog').close();$('binding-form').onsubmit=async e=>{e.preventDefault();await action(e.submitter,async()=>{try{await call('-binding-save',{...(bindingEditing?{id:bindingEditing}:{}),platform_id:Number($('binding-platform').value),user_id:Number($('binding-user').value),account_id:Number($('binding-account').value),bot_key:Number($('binding-bot').value),signal_type:$('binding-type').value.trim(),enabled:$('binding-enabled').checked,default_cents:stakeCents($('binding-amount').value),strategy_amounts:amountSettings(),strategy_ids:[...$('binding-strategies').selectedOptions].map(o=>o.value)});$('binding-dialog').close();await load();toast('会员推送绑定已保存');}catch(error){$('binding-error').textContent=error.message;}});};

let platformEditing=null;
function renderPlatforms(){table('robot-platforms',[['平台','name'],['接口地址','endpoint'],['密钥',r=>r.token_configured?'已配置':'未配置'],['订单记账',r=>r.execution_required?'逐笔确认':'接口受理'],['状态',r=>r.enabled?'已启用':'已停用'],['管理',r=>`<div class="admin-actions"><button class="secondary" data-platform-edit="${r.id}">编辑</button><button class="secondary" data-platform-toggle="${r.id}">${r.enabled?'停用':'启用'}</button></div>`]],data.platforms);$('platforms').querySelectorAll('[data-platform-edit]').forEach(b=>b.onclick=()=>editPlatform(data.platforms.find(p=>p.id===Number(b.dataset.platformEdit))));$('platforms').querySelectorAll('[data-platform-toggle]').forEach(b=>b.onclick=()=>action(b,async()=>{const p=data.platforms.find(p=>p.id===Number(b.dataset.platformToggle));await call('-platform-save',{id:p.id,version:p.version,name:p.name,endpoint:p.endpoint,enabled:!p.enabled});await load();}));}
function editPlatform(p){$('platform-enabled').disabled=!!p&&!!window.pc28Access&&!window.pc28Access.can('robot.platform.control');platformEditing=p?.id||null;$('platform-title').textContent=p?'编辑推送平台':'添加推送平台';$('platform-name').value=p?.name||'';$('platform-url').value=p?.endpoint||'';$('platform-token').value='';$('platform-enabled').checked=p?.enabled||false;$('platform-execution').checked=!!p?.execution_required;$('platform-error').textContent='';$('platform-dialog').showModal();}
$('platform-add').onclick=()=>editPlatform(null);$('platform-close').onclick=$('platform-cancel').onclick=()=>$('platform-dialog').close();$('platform-form').onsubmit=async e=>{e.preventDefault();await action(e.submitter,async()=>{try{const body={...(platformEditing?{id:platformEditing,version:data.platforms.find(p=>p.id===platformEditing).version}:{}),name:$('platform-name').value.trim(),endpoint:$('platform-url').value.trim(),enabled:$('platform-enabled').checked,execution_required:$('platform-execution').checked};if($('platform-token').value)body.token=$('platform-token').value;await call('-platform-save',body);$('platform-dialog').close();await load();toast('推送平台已保存');}catch(error){$('platform-error').textContent=error.message;}});};
window.pc28RobotRefreshRecords=records;
})();

window.pc28RobotReady=true;

(()=>{'use strict';
 if(document.body.dataset.admin!=='1')return;
 const $=id=>document.getElementById('ra-'+id),E=esc,apiGet=(p)=>api('/api/admin/robot'+(p?'-'+p:'')),apiPost=(p,b)=>api('/api/admin/robot-'+p,b);
 let meta=null,catalog=[],rows=[],page=1,pages=1,detail=null,selected=new Map(),modal=null,busy=false,loading=false,dirty=false,listRequest=0;
 const dialog=$('dialog'),form=$('form');
 const money=v=>num(v/100)+'U',bool=v=>!!v;
 const btn=(label,attr,id)=>`<button type="button" class="secondary" ${attr}="${id}">${label}</button>`;
 function feedback(text){$('error').textContent=text;}
 async function metadata(value){meta=value||await apiGet('');for(const [id,list,label] of [['member',meta.members,'全部会员'],['platform',meta.platforms,'全部平台'],['r-member',meta.members,'全部会员'],['r-platform',meta.platforms,'全部平台']]){const value=$(id).value;$(id).innerHTML=`<option value="">${label}</option>`+list.map(x=>`<option value="${x.id}">${E(x.username||x.name)}</option>`).join('');$(id).value=value;} }
 async function loadCatalog(render=false){
  catalog=await apiGet('catalog');
  if(render)table('ra-catalog-table',[
   ['机器人','name'],['平台','platform_name'],['订阅账号数','subscriber_count'],
   ['管理',r=>btn('编辑名称','data-ra-catalog-edit',r.id)]
  ],catalog);
 }
 async function load(){if(loading)return;loading=true;try{if(!meta)await metadata();await list();}catch(e){feedback(e.message);}finally{loading=false;}}
 async function list(){const seq=++listRequest;feedback('正在读取账号…');const d=await apiGet('accounts?'+new URLSearchParams({page,page_size:$('size').value,user_id:$('member').value,platform_id:$('platform').value,enabled:$('enabled').value,q:$('query').value.trim()}));if(seq!==listRequest)return;rows=d.rows;page=d.page;pages=d.pages;feedback('');$('conflicts').hidden=!d.conflicts.length;$('conflicts').textContent=d.conflicts.map(x=>`外部账号#${x.external_account_id}：${x.reason}`).join('；');
 table('ra-table',[['选择',r=>`<input type="checkbox" data-ra-select="${r.id}" aria-label="选择账号${E(r.name)}" ${selected.has(r.id)?'checked':''}>`],['会员 / 下单账号',r=>`<strong>${E(r.username)} · ${E(r.name)}</strong><small>接收端账号 #${r.external_account_id}</small>`],['平台','platform_name'],['机器人 / 策略',r=>`${r.bot_count} 个机器人<small>${r.subscription_count} 个有效订阅</small>`],['状态',r=>r.enabled?(r.member_active?'已启用':'会员已停用'):'已停用'],['操作',r=>`<div class="admin-actions">${btn(detail?.id===r.id?'收起':'管理','data-ra-manage',r.id)}${btn('编辑','data-ra-edit',r.id)}${btn(r.enabled?'停用':'启用','data-ra-control',r.id)}${btn('复制','data-ra-copy',r.id)}${btn('归档','data-ra-delete',r.id)}</div>`]],rows);
 $('page').textContent=`第 ${page} / ${pages} 页 · 共${d.total}个账号`;for(const k of ['first','prev'])$(k).disabled=page<=1;for(const k of ['next','last'])$(k).disabled=page>=pages;$('selected').textContent='已选'+selected.size+'个';
 $('table').querySelectorAll('[data-ra-select]').forEach(x=>x.onchange=()=>{const row=rows.find(r=>r.id===+x.dataset.raSelect);if(x.checked)selected.set(row.id,{id:row.id,version:row.version});else selected.delete(row.id);$('selected').textContent='已选'+selected.size+'个';});
 if(detail){detail=await getAccount(detail.id);renderDetail();}else $('detail').innerHTML='';
 }
 async function getAccount(id){
  const account=await apiGet('account?id='+id);
  for(const bot of account.bots)bot.name=bot.catalog_name||bot.name;
  return account;
 }
 function riskSummary(r){
  if(!r)return '<p class="muted">尚未配置配资风控</p>';
  return '<p class="muted">配资 '+money(r.capital)+' · 已结算净盈亏 '+money(r.net)+' · 盈利峰值 '+money(r.peak)+
   ' · 止损线 '+money(r.floor)+' · 在途 '+money(r.pending+r.held)+(r.paused?' · 风控暂停 '+E(r.paused):'')+
   (r.uncertain?' · 待核对 '+r.uncertain+' 笔':'')+'</p>';
 }
 function renderDetail(){const a=detail;if(!a)return;const botName=id=>a.bots.find(b=>b.id===id)?.name||'已归档机器人';$('detail').innerHTML=`<div class="ra-detail-box"><div class="section-head"><div><h3>${E(a.username)} · ${E(a.name)} / 机器人</h3><p>账号 #${a.external_account_id} · ${E(a.platform_name)}${a.sending?' · 有'+a.sending+'条发送中任务，修改无法撤回':''}</p></div>${btn('选择机器人','data-ra-bot-add',a.id)}</div>${a.bots.map(b=>`<div class="ra-bot-card"><div class="section-head"><div><strong>${E(b.name)}</strong> ${a.default_bot_id===b.id?'<span class="pill">默认订单机器人</span>':''}<small>机器人 #${b.external_bot_key} · ${E(b.signal_type)} · ${b.enabled?'当前账号已启用':'当前账号已停用'}</small></div><div class="admin-actions">${btn('配置订阅与金额','data-ra-subs',b.id)}${btn('编辑','data-ra-bot-edit',b.id)}${btn('配资风控','data-ra-risk-save',b.id)}${b.risk?.paused?btn('人工恢复','data-ra-risk-resume',b.id):''}${b.risk?.uncertain?btn('核对待确认订单','data-ra-risk-reconcile',b.id):''}${btn(b.enabled?'停用':'启用','data-ra-bot-control',b.id)}${a.default_bot_id!==b.id?btn('设为默认','data-ra-default',b.id):''}${btn('解除关联','data-ra-bot-delete',b.id)}</div></div>${riskSummary(b.risk)}<p class="muted">${a.subscriptions.filter(s=>s.bot_id===b.id&&s.enabled).map(s=>`${E(meta.strategies.find(t=>t.id===s.strategy_id)?.name||s.strategy_id)} · ${s.amount_mode==='fixed'?money(s.fixed_cents):'模拟金额 × '+num(s.ratio_units/10000)+'（向上取整到U）'}`).join('；')||'尚无有效策略订阅'}</p></div>`).join('')}${a.recent_skips?.length?'<p class="notice warning">最近拦截：'+a.recent_skips.map(x=>'第'+x.target_draw+'期 '+E(x.reason)).join('；')+'</p>':''}<p class="muted">默认机器人只预填新订阅；已有订阅不会自动改投。实际额度沿用会员记账，不改变模拟账户。</p></div>`;}
 function open(title,state){modal=state;dirty=false;$('title').textContent=title;$('dialog-error').textContent='';renderModal();dialog.showModal();}
 function close(){if(busy)return;if(dirty&&!confirm('配置尚未保存，是否放弃修改？'))return;dialog.close();modal=null;dirty=false;}
 $('close').onclick=close;dialog.addEventListener('cancel',e=>{e.preventDefault();close();});form.addEventListener('input',()=>dirty=true);form.addEventListener('change',()=>dirty=true);
 function options(list,chosen,label=x=>x.name){return list.map(x=>`<option value="${x.id}" ${String(x.id)===String(chosen)?'selected':''}>${E(label(x))}</option>`).join('');}
 function subRows(values,bots,creation=false,bulk=false){return `<div class="ra-sub-row muted"><span>策略</span><span>接收机器人</span><span>金额方式</span><span>金额 / 倍数</span></div>`+(creation?bots.flatMap((bot,index)=>meta.strategies.map(strategy=>({strategy,bot,index}))):meta.strategies.map(strategy=>({strategy}))).map(({strategy:s,bot,index})=>{const v=values.find(x=>x.strategy_id===s.id&&(!creation||x.bot_index===index)),on=!!v?.enabled,mode=v?.amount_mode||'ratio',fixed=(v?.fixed_cents??modal.draft?.default_cents??modal.account?.default_cents??10000)/100,ratio=(v?.ratio_units??10000)/10000;return `<div class="ra-sub-row" data-sid="${E(s.id)}" ${creation?`data-bot-index="${index}"`: ''} data-legacy="${v?.legacy_fraction||0}"><label><input type="checkbox" data-ra-sub value="${E(s.id)}" ${on?'checked':''}>${E(s.name)}</label><select data-ra-route ${on?'':'disabled'} aria-label="${E(s.name)}接收机器人">${bulk?'<option value="">各账号默认机器人</option>':creation?`<option value="${index}">${E(bot.name)}</option>`:bots.map((b,i)=>`<option value="${creation?i:b.id}" ${(creation?(v?.bot_index??0)==i:v?.bot_id===b.id||(!v&&b.id===modal.account?.default_bot_id))?'selected':''}>${E(b.name)}</option>`).join('')}</select><select data-ra-mode ${on?'':'disabled'} aria-label="${E(s.name)}金额方式"><option value="ratio" ${mode==='ratio'?'selected':''}>模拟比例</option><option value="fixed" ${mode==='fixed'?'selected':''}>固定金额</option></select><input data-ra-value type="number" min="${mode==='fixed'?1:.0001}" max="${mode==='fixed'?20000:100}" step="${mode==='fixed'?(v?.legacy_fraction ? 0.01 : 1):.0001}" value="${mode==='fixed'?fixed:ratio}" ${on?'required':'disabled'} aria-label="${E(s.name)}金额或比例"></div>`;}).join('')+'<div class="ra-summary" id="ra-summary"></div>';}
 function readSubs(creation=false,bulk=false){return [...$('body').querySelectorAll('[data-sid]')].filter(r=>r.querySelector('[data-ra-sub]')?.checked).map(r=>{const mode=r.querySelector('[data-ra-mode]').value,value=r.querySelector('[data-ra-value]').value;
 if(!/^\d+(?:\.\d{1,4})?$/.test(value))throw Error('金额或比例格式无效');const n=Number(value),result={strategy_id:r.dataset.sid,amount_mode:mode,enabled:true};if(mode==='fixed')result.fixed_cents=Math.round(n*100);else result.ratio_units=Math.round(n*10000);if(!bulk)result[creation?'bot_index':'bot_id']=Number(r.querySelector('[data-ra-route]').value);return result;});}
 function summary(){const box=$('summary');if(!box)return;try{const values=readSubs(modal.type==='wizard',modal.type==='bulk'),botNames=modal.type==='wizard'?modal.draft.bots:modal.account?.bots||[];box.textContent=(modal.type==='wizard'?`接收账号：${modal.draft.name} #${modal.draft.external_account_id}；${meta.platforms.find(p=>p.id===modal.draft.platform_id)?.name}\n`:'')+(values.map(s=>`${meta.strategies.find(x=>x.id===s.strategy_id)?.name} → ${modal.type==='bulk'?'各账号默认机器人':modal.type==='wizard'?botNames[s.bot_index]?.name:botNames.find(b=>b.id===s.bot_id)?.name}；${s.amount_mode==='fixed'?money(s.fixed_cents):'模拟金额 × '+num(s.ratio_units/10000)+'，500U示例 → '+num(Math.ceil(500*s.ratio_units/10000))+'U'}`).join('\n')||'请选择订阅策略');}catch(e){box.textContent=e.message;}}
 function botFields(bot,index){
  const platform=modal.draft?.platform_id??modal.account.platform_id;
  const choices=catalog.filter(item=>item.platform_id===platform);
  return `<div class="ra-bot-card ra-form-grid" data-wizard-bot="${index}">
   <label>目录机器人<select data-b-catalog required><option value="">请选择目录机器人</option>${options(choices,bot.catalog_id)}</select></label>
   <label>此账号的接收端机器人 ID<input data-b-key type="number" min="1" max="2147483647" value="${bot.external_bot_key||''}" required></label>
   <label>此账号的信号类型<input data-b-signal value="${E(bot.signal_type||'BUY_SYSTEM')}" pattern="[A-Z][A-Z0-9_]{1,63}" required></label>
   <p class="muted">请填写接收工具为此账号配置的机器人编号。</p>
  </div>`;
 }
 function readBot(row){
  const catalogId=Number(row.querySelector('[data-b-catalog]').value);
  return {catalog_id:catalogId||null,name:catalog.find(item=>item.id===catalogId)?.name||'',external_bot_key:Number(row.querySelector('[data-b-key]').value),signal_type:row.querySelector('[data-b-signal]').value.trim(),enabled:true};
 }
 function collectBots(){modal.draft.bots=[...$('body').querySelectorAll('[data-wizard-bot]')].map(readBot);}
 function renderModal(){const m=modal;let html='',actions='';
 if(m.type==='wizard'){const d=m.draft;html=`<div class="ra-steps"><span class="${m.step===1?'active':''}">① 账号归属</span><span>→</span><span class="${m.step===2?'active':''}">② 接收机器人</span><span>→</span><span class="${m.step===3?'active':''}">③ 策略与金额</span></div>`;
 if(m.step===1)html+=`<div class="ra-form-grid"><label>所属会员<select id="ra-f-member" required>${options(meta.members,d.user_id,x=>x.username)}</select></label><label>推送平台<select id="ra-f-platform" required>${options(meta.platforms,d.platform_id)}</select></label><label>账号名称<input id="ra-f-name" value="${E(d.name)}" maxlength="50" required></label><label>接收端账号ID<input id="ra-f-external" type="number" min="1" max="2147483647" value="${d.external_account_id||''}" required></label></div>`;
 if(m.step===2)html+=d.bots.map(botFields).join('')+`<div class="actions">${m.copyFrom?'':btn('再添加一个机器人','data-ra-wizard-addbot','')}<p class="muted">${m.copyFrom?'复制不沿用外部机器人ID，请逐一填写。':'机器人ID来自接收工具，同一账号内不能重复。'}</p></div>`;
 if(m.step===3)html+=`<label>账号默认固定金额（U，预填新配置）<input id="ra-f-default-cents" type="number" min="1" max="20000" step="1" value="${(d.default_cents||10000)/100}" required></label>`+subRows(d.subscriptions,d.bots,true)+`<p class="muted">实际推送金额不回写模拟账户或回测。${m.copyFrom?'复制账号默认停用。':'保存后启用，只推送之后的新订单。'}</p>`;
 actions=btn('取消','data-ra-modal-close','')+(m.step>1?btn('上一步','data-ra-wizard-prev',''):'')+`<button type="submit">${m.step<3?'下一步':m.copyFrom?'保存为停用账号':'保存并启用'}</button>`;
 }else if(m.type==='subs'||m.type==='bulk'){html=(m.type==='bulk'?`<p class="notice warning">将替换${selected.size}个账号的完整策略订阅；未勾选策略会停用，使用各账号默认机器人。逐项返回结果。</p>`:'<p class="muted">只保存当前机器人的订阅和金额。同一策略可由其他机器人独立订阅。</p>')+subRows(m.type==='bulk'?[]:m.account.subscriptions.filter(s=>s.bot_id===m.botScope.id),m.botScope?[m.botScope]:[],false,m.type==='bulk');actions=btn('取消','data-ra-modal-close','')+'<button type="submit">保存配置</button>';
 }else if(m.type==='edit'){const a=m.account;html=`<p class="muted">${E(a.username)} · ${E(a.platform_name)} · 接收端账号 #${a.external_account_id}（归属不可改投）</p><div class="ra-form-grid"><label>账号名称<input id="ra-f-name" value="${E(a.name)}" maxlength="50" required></label><label>默认机器人<select id="ra-f-default">${options(a.bots,a.default_bot_id)}</select></label><label>默认固定金额（U，仅预填新配置）<input id="ra-f-default-cents" type="number" min="1" max="20000" step="1" value="${a.default_cents/100}" required></label></div><p class="muted">默认机器人仅预填新订阅，不改变已有策略路由。${a.sending?'有发送中任务，无法撤回。':''}</p>`;actions=btn('取消','data-ra-modal-close','')+'<button type="submit">保存账号</button>';
 }else if(m.type==='catalog'){
  const item=m.item;
  html=`<div class="ra-form-grid"><label>机器人名称<input id="ra-c-name" value="${E(item?.name||'')}" maxlength="50" required></label><label>推送平台<select id="ra-c-platform" ${item?'disabled':''} required>${options(meta.platforms,item?.platform_id)}</select></label></div>`;
  actions=btn('取消','data-ra-modal-close','')+'<button type="submit">保存目录机器人</button>';
 }else if(m.type==='bot'){const b=m.bot||{name:'',external_bot_key:'',signal_type:'BUY_SYSTEM',enabled:true};html=botFields(b,0)+`<label><input id="ra-f-bot-enabled" type="checkbox" ${b.enabled?'checked':''}>启用机器人</label>`;if(b.id)html+=`<p class="muted">机器人ID不可改投。修改影响新订单，旧待发送任务取消。</p>`;actions=btn('取消','data-ra-modal-close','')+'<button type="submit">保存机器人</button>';
 }else if(m.type.startsWith('risk-')){
  const r=m.bot.risk,number=(id,title,value,max='')=>'<label>'+title+'<input id="ra-risk-'+id+'" type="number" min="0.01" step="0.01" '+(max?'max="'+max+'"':'')+' value="'+E(value)+'" required></label>';
  html=riskSummary(r);
  if(m.type==='risk-save'){
   html+='<div class="ra-form-grid">'+number('capital','机器人配资（U）',r?r.capital/100:'')+
    '<label>初始止损方式<select id="ra-risk-mode"><option value="percent">按配资比例（%）</option><option value="amount">按固定金额（U）</option></select></label>'+
    number('limit','最大亏损比例或金额',r?r.loss_limit/100:'')+
    number('target','启用移动止损的盈利目标（U）',r?r.profit_target/100:'')+
    number('trailing','从盈利峰值允许回吐（%）',r?r.trailing_bps/100:50,50)+'</div>'+
    '<p class="muted">首次配置从当前结算起算，未结算订单仍占用额度。固定止损及移动回撤比例均不得超过 50%。</p>';
   actions=btn('取消','data-ra-modal-close','')+'<button type="submit">保存配资风控</button>';
  }else if(m.type==='risk-resume'){
   html+='<label>恢复时的盈亏基准<select id="ra-risk-reset"><option value="keep">保留原盈亏与盈利峰值</option><option value="reset">从当前重新计算盈亏与峰值</option></select></label><p class="muted">恢复前会重新校验止损线和在途敞口，已结算历史和未结算订单继续保留。</p>';
   actions=btn('取消','data-ra-modal-close','')+'<button type="submit">确认恢复推送</button>';
  }else{
   html+='<label>待核对发送记录<select id="ra-risk-outbox">'+r.unresolved.map(id=>'<option value="'+id+'">记录 #'+id+'</option>').join('')+'</select></label><label><input id="ra-risk-confirmed" type="checkbox" required> 已与接收端核对，确认此条订单未执行</label>';
   actions=btn('取消','data-ra-modal-close','')+'<button type="submit">释放该笔预留</button>';
  }
 }else if(m.type==='batch'){const d=m.data;html=`<p>第${d.target_draw}期 · ${E(d.recipient_name)} · 平台#${d.platform_id}</p><p>发往账号 #${d.account_id??'—'} / 机器人 #${d.bot_key??'—'} / ${E(d.signal_type)}</p><p class="muted">事件编号 ${E(d.event_id)}；尝试${d.attempts}次</p><div class="ra-batch-message">${E(d.message)}</div><div id="ra-batch-items" class="table-wrap"></div><p>计划发送：${E(beijingTime(d.scheduled_at))}；实际发送：${E(beijingTime(d.sent_at))}</p><p>接口接收：${E(beijingTime(d.accepted_at))} ${E(d.last_error||'')}</p><p class="muted">HTTP已接收不等同实际下单成功。</p>`;actions=btn('关闭','data-ra-modal-close','');}
 $('body').innerHTML=html;$('dialog-actions').innerHTML=actions;if(m.type==='risk-save')$('risk-mode').value=m.bot.risk?.loss_mode||'percent';if(m.type==='batch')table('ra-batch-items',[['订单号',r=>'PC28-'+r.outbox_id],['执行状态',r=>E(({awaiting:'等待执行确认',executed:'执行成功',failed:'执行失败',timeout:'执行超时',rejected:'已拒绝',cancelled:'已取消',unconfirmed:'待核对'})[r.execution_status]||'未启用执行确认')+(r.execution_detail?' · '+E(r.execution_detail):'')],['策略','strategy_name'],['模拟金额',r=>r.simulation_cents==null?'历史未记录':money(r.simulation_cents)],['实际金额',r=>money(r.push_cents)],['内容','message'],['状态','status']],m.data.items);if(m.type==='bot'&&m.bot?.id)$('body').querySelector('[data-b-key]').readOnly=true;summary();}
 form.addEventListener('change',e=>{const row=e.target.closest('[data-sid]');if(row){const on=row.querySelector('[data-ra-sub]').checked;row.querySelectorAll('select,input:not([type=checkbox])').forEach(x=>{x.disabled=!on;x.required=on;});if(e.target.matches('[data-ra-mode]')){const fixed=e.target.value==='fixed',input=row.querySelector('[data-ra-value]');input.value=fixed?String((modal.draft?.default_cents||modal.account?.default_cents||10000)/100):'1';input.min=fixed?'1':'.0001';input.max=fixed?'20000':'100';input.step=fixed?'1':'.0001';}summary();}});form.addEventListener('input',e=>{if(e.target.id==='ra-f-default-cents'&&modal?.draft)modal.draft.default_cents=Number(e.target.value)*100;summary();});
 async function saved(id){dialog.close();modal=null;dirty=false;selected.clear();detail=id?await getAccount(id):null;page=1;await list();}
 form.onsubmit=async e=>{e.preventDefault();if(busy)return;busy=true;form.querySelectorAll('button').forEach(b=>b.disabled=true);$('dialog-error').textContent='正在保存…';try{const m=modal;
 if(m.type==='wizard'&&m.step<3){if(m.step===1){Object.assign(m.draft,{user_id:Number($('f-member').value),platform_id:Number($('f-platform').value),name:$('f-name').value.trim(),external_account_id:Number($('f-external').value)});}else{collectBots();if(m.draft.bots.some(bot=>!bot.catalog_id))throw Error('请选择目录机器人');}m.step++;$('dialog-error').textContent='';renderModal();return;}
 if(m.type==='wizard'){m.draft.default_cents=Number($('f-default-cents').value)*100;m.draft.subscriptions=readSubs(true);if(!m.draft.subscriptions.length)throw Error('请至少选择一个策略');const result=await apiPost('account-save',{...m.draft,enabled:!m.copyFrom,copy_from:m.copyFrom});await saved(result.id);toast('下单账号已保存'+(m.copyFrom?'，复制账号当前停用':''));}
 else if(m.type==='subs'){await apiPost('subscriptions',{id:m.account.id,version:m.account.version,bot_id:m.botScope.id,subscriptions:readSubs()});await saved(m.account.id);toast('订阅已保存，仅影响后续新订单');}
 else if(m.type==='bulk'){const result=await apiPost('account-bulk',{operation:'subscriptions',items:[...selected.values()],subscriptions:readSubs(false,true)});dialog.close();modal=null;dirty=false;feedback(result.results.map(r=>`账号#${r.id}：${r.ok?'成功':r.error}`).join('；'));selected.clear();await list();feedback(`批量完成${result.success}/${result.results.length}；`+result.results.filter(x=>!x.ok).map(x=>'#'+x.id+' '+x.error).join('；'));}
 else if(m.type==='edit'){await apiPost('account-save',{id:m.account.id,version:m.account.version,name:$('f-name').value.trim(),enabled:bool(m.account.enabled),default_bot_id:Number($('f-default').value),default_cents:Number($('f-default-cents').value)*100});await saved(m.account.id);}
 else if(m.type==='catalog'){
  await apiPost('catalog-save',{id:m.item?.id,version:m.item?.version,name:$('c-name').value.trim(),platform_id:Number($('c-platform').value)});
  dialog.close();modal=null;dirty=false;await loadCatalog(true);
 }
 else if(m.type.startsWith('risk-')){
  const action=m.type.slice(5),body={id:m.account.id,bot_id:m.bot.id,bot_version:m.bot.version,action};
  if(action==='save'){
   const scaled=id=>Math.round(Number($('risk-'+id).value)*100);
   Object.assign(body,{capital:scaled('capital'),loss_mode:$('risk-mode').value,loss_limit:scaled('limit'),profit_target:scaled('target'),trailing_bps:scaled('trailing')});
  }else if(action==='resume')body.reset=$('risk-reset').value==='reset';
  else Object.assign(body,{outbox_id:Number($('risk-outbox').value),confirmed:$('risk-confirmed').checked});
  await apiPost('bot-risk',body);await saved(m.account.id);
 }
 else if(m.type==='bot'){const box=$('body').querySelector('[data-wizard-bot]'),bot={...(m.bot||{}),...readBot(box),enabled:$('f-bot-enabled').checked};await apiPost('bot-save',{id:m.account.id,version:m.account.version,bot});await saved(m.account.id);}
 }catch(err){$('dialog-error').textContent=err.message;}finally{busy=false;form.querySelectorAll('button').forEach(b=>b.disabled=false);}};
 async function act(button,fn){button.disabled=true;try{await fn();}catch(e){feedback(e.message);}finally{button.disabled=false;}}
 const noticeForm=document.getElementById('robot-notice-form');
 const notice=id=>document.getElementById('robot-notice-'+id);
 const noticeLabels={order:'下单异常',strategy:'策略异常',ratio:'开奖占比'};
 let noticeId=null;
 function editNotice(record){
  noticeId=record?.id??null;
  notice('name').value=record?.name??'';notice('chat').value=record?.chat_id??'';
  notice('token').value='';notice('token').required=!record;notice('enabled').checked=record?!!record.enabled:true;
  for(const item of noticeForm.querySelectorAll('[name=notice_event]'))item.checked=!record||record.events.includes(item.value);
 }
 async function loadNotices(){
  try{
   const data=await apiGet('notifications');
   table('robot-notice-list',[['名称',r=>E(r.name)],['Chat ID',r=>E(r.chat_id)],['订阅类型',r=>r.events.map(x=>noticeLabels[x]).join('、')],
    ['状态',r=>r.enabled?'启用':'停用'],['操作',r=>btn('编辑','data-notice-edit',r.id)]],data.subscriptions);
   notice('list').querySelectorAll('[data-notice-edit]').forEach(b=>b.onclick=()=>editNotice(data.subscriptions.find(r=>r.id===Number(b.dataset.noticeEdit))));
   table('robot-notice-recent',[['时间',r=>E(beijingTime(r.created))],['订阅','subscription_id'],
    ['状态',r=>E(({pending:'待发送',sent:'已发送',failed:'失败',cancelled:'已取消'})[r.status]||r.status)],['尝试','attempts'],['原因',r=>E(r.error)]],data.recent);
  }catch(error){notice('status').textContent=error.message;}
 }
 if(window.pc28Access&&!window.pc28Access.owner)notice('tab').hidden=true;
 notice('new').onclick=()=>editNotice(null);
 noticeForm.onsubmit=async event=>{
  event.preventDefault();const button=event.submitter;button.disabled=true;
  try{
   await apiPost('notifications',{id:noticeId,name:notice('name').value.trim(),chat_id:notice('chat').value.trim(),
    token:notice('token').value.trim(),enabled:notice('enabled').checked,
    events:[...noticeForm.querySelectorAll('[name=notice_event]:checked')].map(x=>x.value)});
   notice('status').textContent='订阅已保存';editNotice(null);await loadNotices();
  }catch(error){notice('status').textContent=error.message;}
  finally{button.disabled=false;}
 };
 document.getElementById('admin-robot').addEventListener('click',e=>{const b=e.target.closest('button');if(!b)return;
 if(b.dataset.raTab){document.querySelectorAll('[data-ra-tab]').forEach(x=>x.setAttribute('aria-selected',String(x===b)));document.querySelectorAll('[data-ra-view]').forEach(x=>x.hidden=x.dataset.raView!==b.dataset.raTab);if(b.dataset.raTab==='accounts')load();if(b.dataset.raTab==='notices')loadNotices();if(b.dataset.raTab==='catalog')loadCatalog(true).catch(err=>{$('catalog-error').textContent=err.message;});return;}
 if(b.id==='ra-catalog-add'||b.hasAttribute('data-ra-catalog-edit'))act(b,async()=>{
  if(!meta)await metadata();
  const item=catalog.find(row=>row.id===Number(b.dataset.raCatalogEdit));
  open(item?'编辑目录机器人':'添加目录机器人',{type:'catalog',item});
 });
 if(b.hasAttribute('data-ra-manage'))act(b,async()=>{detail=detail?.id===+b.dataset.raManage?null:await getAccount(b.dataset.raManage);await list();});
 if(b.hasAttribute('data-ra-edit'))act(b,async()=>open('编辑下单账号',{type:'edit',account:await apiGet('account?id='+b.dataset.raEdit)}));
 if(b.hasAttribute('data-ra-copy'))act(b,async()=>{await loadCatalog();const a=await getAccount(b.dataset.raCopy),bots=a.bots.map(x=>({name:x.name,catalog_id:x.catalog_id,external_bot_key:'',signal_type:x.signal_type,enabled:true}));open('复制下单账号',{type:'wizard',step:1,copyFrom:a.id,draft:{user_id:a.user_id,platform_id:a.platform_id,name:a.name+'副本',external_account_id:'',default_cents:a.default_cents,bots,subscriptions:a.subscriptions.map(s=>({...s,bot_index:a.bots.findIndex(b=>b.id===s.bot_id)}))}});});
 if(b.hasAttribute('data-ra-control')||b.hasAttribute('data-ra-delete'))act(b,async()=>{const id=+(b.dataset.raControl||b.dataset.raDelete),a=await apiGet('account?id='+id),del=b.hasAttribute('data-ra-delete');if(del&&!confirm('归档账号并停止后续推送？历史发送与资金流水保留。'))return;await apiPost(del?'account-delete':'account-control',{id,version:a.version,enabled:!a.enabled});if(del&&detail?.id===id)detail=null;await list();});
 if(b.hasAttribute('data-ra-subs'))act(b,async()=>{const account=await getAccount(detail.id),botScope=account.bots.find(x=>x.id===+b.dataset.raSubs);if(!botScope)throw Error('机器人不存在，请刷新列表');open(botScope.name+' · 配置订阅与金额',{type:'subs',account,botScope});});
 if(b.hasAttribute('data-ra-bot-add'))act(b,async()=>{await loadCatalog();open('选择目录机器人',{type:'bot',account:await getAccount(b.dataset.raBotAdd)});});
 for(const action of ['save','resume','reconcile']){
  if(!b.hasAttribute('data-ra-risk-'+action))continue;
  act(b,async()=>{
   const account=await getAccount(detail.id);
   const bot=account.bots.find(item=>item.id===Number(b.getAttribute('data-ra-risk-'+action)));
   if(!bot)throw Error('机器人不存在，请刷新');
   if(action!=='save'&&!bot.risk)throw Error('请先配置配资风控');
   if(action==='reconcile'&&!bot.risk.uncertain)throw Error('没有待核对的订单');
   open(bot.name+' · '+({save:'配资风控',resume:'人工恢复',reconcile:'核对发送记录'})[action],{type:'risk-'+action,account,bot});
  });
  return;
 }
 if(b.hasAttribute('data-ra-bot-edit'))act(b,async()=>{await loadCatalog();const a=await getAccount(detail.id);open('编辑账号机器人路由',{type:'bot',account:a,bot:a.bots.find(x=>x.id===+b.dataset.raBotEdit)});});
 if(b.hasAttribute('data-ra-bot-control')||b.hasAttribute('data-ra-bot-delete'))act(b,async()=>{const a=await getAccount(detail.id),bot=a.bots.find(x=>x.id===+(b.dataset.raBotControl||b.dataset.raBotDelete)),del=b.hasAttribute('data-ra-bot-delete');if(del&&!confirm('解除此账号与机器人的关联？请先停用或迁移有效订阅。'))return;await apiPost('bot-control',{id:a.id,version:a.version,bot_id:bot.id,bot_version:bot.version,enabled:!bot.enabled,deleted:del});await list();});
 if(b.hasAttribute('data-ra-default'))act(b,async()=>{const a=await getAccount(detail.id);await apiPost('account-save',{id:a.id,version:a.version,name:a.name,enabled:!!a.enabled,default_bot_id:+b.dataset.raDefault});await list();});
 if(b.hasAttribute('data-ra-batch'))act(b,async()=>open('合并发送详情',{type:'batch',data:await apiGet('batch?id='+b.dataset.raBatch)}));
 });
 form.addEventListener('click',e=>{const b=e.target.closest('button');if(!b)return;if(b.hasAttribute('data-ra-modal-close'))close();if(b.hasAttribute('data-ra-wizard-prev')){if(modal.step===3){modal.draft.default_cents=Number($('f-default-cents').value)*100;modal.draft.subscriptions=readSubs(true);}else collectBots();modal.step--;renderModal();}if(b.hasAttribute('data-ra-wizard-addbot')){collectBots();modal.draft.bots.push({name:'新机器人',external_bot_key:'',signal_type:'BUY_SYSTEM',enabled:true});renderModal();}});
 $('add').onclick=async()=>{try{if(!meta)await metadata();await loadCatalog();}catch(e){feedback(e.message);return;}if(!meta.members.length){feedback('请先添加会员');return;}open('添加下单账号',{type:'wizard',step:1,draft:{user_id:meta.members[0].id,platform_id:meta.platforms[0].id,name:'',external_account_id:'',default_cents:10000,bots:[{name:'综合机器人',external_bot_key:'',signal_type:'BUY_SYSTEM',enabled:true}],subscriptions:[]}});};
 $('filter').onsubmit=e=>{e.preventDefault();page=1;detail=null;list().catch(e=>feedback(e.message));};$('size').onchange=()=>{page=1;detail=null;list().catch(e=>feedback(e.message));};for(const k of ['first','prev','next','last'])$(k).onclick=()=>{page=({first:1,prev:Math.max(1,page-1),next:Math.min(pages,page+1),last:pages})[k];list().catch(e=>feedback(e.message));};
 async function bulk(operation){if(!selected.size){feedback('请先选择账号');return;}if(operation==='subscriptions'){open('批量替换订阅',{type:'bulk'});return;}if(!confirm(`将对${selected.size}个账号执行${operation==='enable'?'启用':operation==='disable'?'停用':'归档'}，是否继续？`))return;try{const result=await apiPost('account-bulk',{operation,items:[...selected.values()]});selected.clear();detail=null;await list();feedback(`完成${result.success}/${result.results.length}；`+result.results.filter(x=>!x.ok).map(x=>'#'+x.id+' '+x.error).join('；'));}catch(e){feedback(e.message);}}
 for(const [key,operation] of [['enable','enable'],['disable','disable'],['delete','delete'],['subs','subscriptions']])$('bulk-'+key).onclick=()=>bulk(operation);
 async function recordAccounts(){let all=[],page=1,d;do{d=await apiGet('accounts?page_size=100&page='+page);all.push(...d.rows);page++;}while(page<=d.pages);$('r-account').innerHTML='<option value="">全部账号</option>'+all.map(a=>`<option value="${a.id}">${E(a.username)} · ${E(a.name)} #${a.external_account_id}</option>`).join('');}
 $('r-account').onchange=async()=>{if(!$('r-account').value){$('r-bot').innerHTML='<option value="">全部机器人</option>';return;}const a=await apiGet('account?id='+$('r-account').value);$('r-bot').innerHTML='<option value="">全部机器人</option>'+options(a.bots,null);};
 $('record-filter').onsubmit=e=>{e.preventDefault();window.pc28RobotRecordFilters={user_id:$('r-member').value,platform_id:$('r-platform').value,account_id:$('r-account').value,bot_id:$('r-bot').value,draw:$('r-draw').value};window.pc28RobotRefreshRecords().catch(e=>feedback(e.message));};
 window.addEventListener('pc28:robot-meta',e=>{metadata(e.detail).catch(err=>feedback(err.message));});
 document.querySelector('[data-tab="admin-robot"]').addEventListener('click',()=>{load();recordAccounts().catch(e=>feedback(e.message));});document.getElementById('robot-refresh').addEventListener('click',()=>{meta=null;load();recordAccounts().catch(e=>feedback(e.message));});
 window.pc28RobotAccounts={refresh:load};
})();


