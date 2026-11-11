/* Project/tenant role management; the backend is authoritative for authorization. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const esc = val => String(val??'').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  async function api(path,options={}) {
    const r=await fetch('/api/v1/'+path,options);
    if(!r.ok) { const m=await r.json().catch(()=>({}));throw new Error(m.detail||('HTTP '+r.status)); }
    return r.json();
  }
  const table = (cols,rows) => rows.length ? `<table><thead><tr>${cols.map(x=>`<th>${x[0]}</th>`).join('')}</tr></thead><tbody>${rows.map(o=>`<tr>${cols.map(x=>`<td>${esc(o[x[1]])}</td>`).join('')}</tr>`).join('')}</tbody></table>` : '<p class="muted">暂无记录</p>';
  const notice = text => { $('notice').textContent = text;};
  let users=[],projects=[];
  async function load() {
    try {
      const user=await api('auth/me');
      if(user.tenant_role!=='tenant_admin') throw new Error('当前账户没有租户管理员权限');
      $('actor').textContent=`租户：${user.tenant_id} · 当前用户：${user.username} · 租户管理员`;
      [users,projects]=await Promise.all([api('identity/users'),api('projects')]);
      $('users').innerHTML=table([['用户名','username'],['租户角色','tenant_role'],['状态','active'],['用户 ID','user_id']],users)+
        '<div class="muted" style="margin-top:12px">用户停用将立即注销其所有会话；唯一管理员和当前管理员不可停用。</div>'+
        users.filter(u=>u.user_id!==user.user_id).map(u=>`<button type="button" data-status-user="${esc(u.user_id)}" data-active="${u.active?'0':'1'}">${u.active?'停用':'重新启用'} ${esc(u.username)}</button>`).join(' ');
      $('projects').innerHTML=projects.map(p=>`<option value="${esc(p.id)}">${esc(p.name)}</option>`).join('');
      $('accounts').innerHTML=users.map(u=>`<option value="${esc(u.user_id)}">${esc(u.username)}</option>`).join('');
      const audit=await api('identity/audit?limit=60');
      $('audit').innerHTML=table([['时间戳','time'],['操作','event'],['用户 ID','user_id'],['资源','resource'],['详情','detail']],audit);
      await members();
      notice('');
    } catch(error) {notice(error.message);}
  }
  async function members() {
    const pid=$('projects').value;
    if(!pid){$('members').innerHTML='<p class="muted">暂无项目</p>';return;}
    try {const rows=await api('identity/projects/'+encodeURIComponent(pid)+'/members');
      $('members').innerHTML=table([['用户','username'],['角色','role'],['授权时间戳','granted_at']],rows);
    } catch(error){notice(error.message);}
  }
  $('new-user').addEventListener('submit',async ev=>{
    ev.preventDefault();const form=ev.target,data=Object.fromEntries(new FormData(form));
    try {await api('identity/users',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});form.reset();await load();}
    catch(error){notice(error.message);}
  });
  $('grant').addEventListener('submit',async ev=>{
    ev.preventDefault();const {project_id,...data}=Object.fromEntries(new FormData(ev.target));
    try {await api('identity/projects/'+encodeURIComponent(project_id)+'/members',
      {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});await members();}
    catch(error){notice(error.message);}
  });
  $('users').addEventListener('click',async event=>{
    const btn=event.target.closest('[data-status-user]');if(!btn)return;
    if(!confirm('确认调整该用户的访问状态？停用会立即注销其现有会话。'))return;
    btn.disabled=true;
    try {await api('identity/users/'+encodeURIComponent(btn.dataset.statusUser),{
      method:'PATCH',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({active:btn.dataset.active==='1'})});await load();}
    catch(error){notice(error.message);btn.disabled=false;}
  });
  $('projects').addEventListener('change',members);
  load();
})();
