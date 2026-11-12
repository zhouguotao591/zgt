 'use strict';
(()=>{
 if(document.body.dataset.admin!=='1')return;
 const el=id=>document.getElementById(id),access=window.pc28Access,can=k=>access.can(k);let memberAccess=null,page=1,pages=1,rows=[],editing=null,loadId=0,usersRequest=0,permissionTarget=null;
 async function administrators(){
  try{el('am-error').textContent='正在读取…';const d=await api('/api/admin/administrators?'+new URLSearchParams({page,page_size:el('am-size').value}));rows=d.rows;page=d.page;pages=d.pages;el('am-error').textContent='';el('am-page').textContent=`第 ${page} / ${pages} 页 · 共 ${d.total} 位管理员`;el('am-prev').disabled=page<=1;el('am-next').disabled=page>=pages;
   table('am-table',[['用户名','username'],['状态',r=>r.active?'已启用':'已停用'],['谷歌验证器',r=>r.mfa_bound?'已绑定':'未绑定'],['创建时间',r=>esc(beijingTime(r.created))],['最近登录',r=>esc(beijingTime(r.last_login))],['操作',r=>r.protected?'系统主管理员':r.id===access.user_id?'当前账号':`<div class="admin-actions">${access.main_admin?`<button class="secondary" data-am-members="${r.id}">配置可见会员</button>`: ''}<button class="secondary" data-am-permissions="${r.id}">编辑权限</button><button class="secondary" data-am-control="${r.id}">${r.active?'停用':'启用'}</button><button class="secondary" data-am-password="${r.id}">重置密码</button><button class="secondary" data-am-delete="${r.id}">删除管理员</button>${access.owner&&r.mfa_bound?`<button class="secondary" data-am-mfa="${r.id}">解除谷歌验证器绑定</button>`:''}</div>`]],rows);
   el('am-table').querySelectorAll('[data-am-mfa]').forEach(b=>b.onclick=()=>{
    const r=rows.find(x=>x.id===Number(b.dataset.amMfa));
    el('am-mfa-form').reset();el('am-mfa-target').value=String(r.id);
    el('am-mfa-title').textContent='解除谷歌验证器绑定 · '+r.username;
    el('am-mfa-error').textContent='';el('am-mfa-dialog').showModal();
   });
   el('am-table').querySelectorAll('[data-am-members]').forEach(b=>b.onclick=()=>openMemberAccess(+b.dataset.amMembers));
   el('am-table').querySelectorAll('[data-am-control]').forEach(b=>b.onclick=()=>action(b,async()=>{const r=rows.find(x=>x.id===+b.dataset.amControl);await api('/api/admin/administrator-control',{id:r.id,active:!r.active});await administrators();}));
   el('am-table').querySelectorAll('[data-am-permissions]').forEach(b=>b.onclick=()=>{permissionTarget=Number(b.dataset.amPermissions);document.querySelector('[data-tab="admin-permissions"]').click();});
   el('am-table').querySelectorAll('[data-am-password]').forEach(b=>b.onclick=()=>open(+b.dataset.amPassword));
   el('am-table').querySelectorAll('[data-am-delete]').forEach(b=>b.onclick=()=>action(b,async()=>{if(!confirm('确定删除该管理员？将停用账号并从管理员列表移除，历史审计保留。'))return;await api('/api/admin/administrator-delete',{id:+b.dataset.amDelete});await administrators();}));
  }catch(e){el('am-error').textContent=e.message;}
 }
 function renderMemberAccess(){
  if(!memberAccess)return;
  const term=el('am-members-search').value.trim().toLowerCase();
  const matches=memberAccess.members.filter(r=>!term||r.username.toLowerCase().includes(term)||String(r.id).includes(term));
  table('am-members-list',[
   ['可见',r=>`<input type="checkbox" data-member-access="${r.id}" aria-label="${esc(r.username)}" ${memberAccess.selected.has(r.id)?'checked':''}>`],
   ['会员','username'],['编号','id'],['状态',r=>r.active?'已启用':'已停用']
  ],matches);
  el('am-members-count').textContent='已选 '+memberAccess.selected.size+' 位会员';
 }
 async function openMemberAccess(id){
  const dialog=el('am-members-dialog'),save=el('am-members-save');
  if(dialog.open)return;
  memberAccess=null;
  el('am-members-title').textContent='配置可见会员';
  el('am-members-search').value='';
  el('am-members-count').textContent='';
  el('am-members-list').textContent='';
  el('am-members-error').textContent='正在读取…';
  save.disabled=true;el('am-members-close').disabled=true;dialog.showModal();
  try{
   const data=await api('/api/admin/administrator-members?id='+id);
   const original=new Set(data.members.filter(r=>r.assigned).map(r=>r.id));
   memberAccess={id,members:data.members,original,selected:new Set(original)};
   el('am-members-title').textContent='配置可见会员 · '+data.administrator.username;
   el('am-members-error').textContent='';save.disabled=false;renderMemberAccess();
  }catch(error){el('am-members-error').textContent=error.message;}
  finally{el('am-members-close').disabled=false;}
 }
 el('am-members-close').onclick=()=>el('am-members-dialog').close();
 el('am-members-dialog').addEventListener('cancel',e=>{if(el('am-members-close').disabled)e.preventDefault();});
 el('am-members-search').oninput=renderMemberAccess;
 el('am-members-list').onchange=e=>{
  if(!e.target.matches('[data-member-access]')||!memberAccess)return;
  const id=Number(e.target.dataset.memberAccess);
  if(e.target.checked)memberAccess.selected.add(id);else memberAccess.selected.delete(id);
  el('am-members-count').textContent='已选 '+memberAccess.selected.size+' 位会员';
 };
 el('am-members-form').onsubmit=async e=>{
  e.preventDefault();
  const save=el('am-members-save'),close=el('am-members-close');
  if(!memberAccess||save.disabled)return;
  const {id,original,selected}=memberAccess;
  save.disabled=true;close.disabled=true;
  try{
   await api('/api/admin/administrator-members',{id,add:[...selected].filter(uid=>!original.has(uid)),remove:[...original].filter(uid=>!selected.has(uid))});
   el('am-members-dialog').close();toast('会员可见范围已保存');
  }catch(error){el('am-members-error').textContent=error.message;}
  finally{save.disabled=false;close.disabled=false;}
 };
 async function action(button,fn){button.disabled=true;try{await fn();}catch(e){el('am-error').textContent=e.message;}finally{button.disabled=false;}}
 function open(id=null){editing=id;el('am-form').reset();el('am-title').textContent=id?'重置管理员密码':'添加管理员';el('am-name-label').hidden=!!id;el('am-name').required=!id;el('am-dialog-error').textContent='';el('am-dialog').showModal();}
 el('am-add').onclick=()=>open();el('am-close').onclick=()=>el('am-dialog').close();el('am-refresh').onclick=administrators;el('am-size').onchange=()=>{page=1;administrators();};el('am-prev').onclick=()=>{page--;administrators();};el('am-next').onclick=()=>{page++;administrators();};
 el('am-form').onsubmit=async e=>{e.preventDefault();if(el('am-password').value!==el('am-confirm').value){el('am-dialog-error').textContent='两次密码不一致';return;}const b=el('am-create-save');b.disabled=true;try{await api('/api/admin/administrator-'+(editing?'password':'create'),editing?{id:editing,password:el('am-password').value}:{username:el('am-name').value.trim(),password:el('am-password').value});el('am-dialog').close();await administrators();toast(editing?'密码已重置，旧会话已退出':'管理员已添加，请到权限管理授权');}catch(err){el('am-dialog-error').textContent=err.message;}finally{b.disabled=false;}};
 el('am-mfa-close').onclick=()=>el('am-mfa-dialog').close();
 el('am-mfa-dialog').addEventListener('cancel',e=>{if(el('am-mfa-save').disabled)e.preventDefault();});
 el('am-mfa-form').onsubmit=async e=>{
  e.preventDefault();const save=el('am-mfa-save'),close=el('am-mfa-close');
  if(save.disabled)return;save.disabled=true;close.disabled=true;el('am-mfa-error').textContent='';
  try{
   await api('/api/admin/administrator-mfa-unbind',{id:Number(el('am-mfa-target').value),reason:el('am-mfa-reason').value.trim(),code:el('am-mfa-code').value.trim()});
   el('am-mfa-dialog').close();el('am-mfa-form').reset();await administrators();
   toast('验证器已解绑，该管理员下次登录需重新绑定');
  }catch(error){el('am-mfa-error').textContent=error.message;}
  finally{save.disabled=false;close.disabled=false;}
 };
 async function permissionUsers(target=null){const seq=++usersRequest;++loadId;el('pm-save').disabled=true;try{let all=[],p=1,d;do{d=await api('/api/admin/administrators?page='+p+'&page_size=100');all.push(...d.rows);p++;}while(p<=d.pages);if(seq!==usersRequest)return;el('pm-user').innerHTML='<option value="">请选择管理员</option>'+all.filter(r=>!r.protected&&r.id!==access.user_id).map(r=>`<option value="${r.id}">${esc(r.username)}${r.active?'':'（停用）'}</option>`).join('');el('pm-grid').innerHTML='';el('pm-save').disabled=true;el('pm-error').textContent=all.some(r=>!r.protected)?'':'暂无可授权管理员，请先添加管理员。';if(target){el('pm-user').value=String(target);if(el('pm-user').value)await loadPermissions();else el('pm-error').textContent='该管理员已不可编辑，请刷新管理员列表。';}}catch(e){el('pm-error').textContent=e.message;}}
 async function loadPermissions(){const seq=++loadId;el('pm-grid').innerHTML='';el('pm-save').disabled=true;if(!el('pm-user').value)return;try{const d=await api('/api/admin/permissions?id='+el('pm-user').value);if(seq!==loadId)return;const selected=new Set(d.grants);el('pm-grid').innerHTML=Object.entries(access.pages).map(([page,title])=>`<fieldset data-pm-page="${page}"><legend>${esc(title)}</legend><label><input type="checkbox" data-pm-all>全选本页可授权功能</label>${access.catalog.filter(r=>r.page===page).map(r=>`<label><input type="checkbox" data-grant="${r.key}" ${selected.has(r.key)?'checked':''} ${can(r.key)?'':'disabled'}>${esc(r.label)}</label>`).join('')}</fieldset>`).join('');el('pm-error').textContent='';el('pm-save').disabled=!can('permission.save');}catch(e){el('pm-error').textContent=e.message;}}
 el('pm-user').onchange=loadPermissions;
 el('pm-grid').onchange=e=>{const box=e.target;if(box.hasAttribute('data-pm-all')){box.closest('fieldset').querySelectorAll('[data-grant]:not(:disabled)').forEach(n=>n.checked=box.checked);return;}if(!box.dataset.grant)return;const field=box.closest('fieldset'),view=field.querySelector('[data-grant^="page."]');if(box===view&&!box.checked)field.querySelectorAll('input').forEach(n=>n.checked=false);else if(box.checked)view.checked=true;};
 el('pm-form').onsubmit=async e=>{e.preventDefault();const b=el('pm-save');b.disabled=true;el('pm-error').textContent='正在保存…';try{await api('/api/admin/permissions-save',{id:+el('pm-user').value,grants:[...el('pm-grid').querySelectorAll('[data-grant]:checked')].map(n=>n.dataset.grant)});el('pm-error').textContent='权限已保存，该管理员需重新登录。';toast('权限已保存');}catch(err){el('pm-error').textContent=err.message;}finally{b.disabled=false;}};
 document.querySelector('[data-tab="admin-administrators"]').addEventListener('click',administrators);document.querySelector('[data-tab="admin-permissions"]').addEventListener('click',()=>{const target=permissionTarget;permissionTarget=null;permissionUsers(target);});
 window.pc28AccessManagement={refresh:()=>{const active=document.querySelector('.tab.active')?.id;if(active==='admin-administrators')administrators();else if(active==='admin-permissions')permissionUsers();}};
 el('refresh').addEventListener('click',()=>window.pc28AccessManagement.refresh());
})();
