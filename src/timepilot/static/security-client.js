/* TimePilot enterprise requests: HttpOnly session; double-submit CSRF; project header. */
(() => {
  'use strict';
  const getCookie = name => {
    const item = document.cookie.split('; ').find(s => s.startsWith(name + '='));
    return item ? decodeURIComponent(item.slice(name.length + 1)) : '';
  };
  window.TIMEPILOT_ENTERPRISE = !!getCookie('tp_csrf');
  const nativeFetch = window.fetch.bind(window);
  window.fetch = (input, options={}) => {
    const url = new URL(typeof input==='string'?input:input.url,location.href);
    if (url.origin !== location.origin || !url.pathname.startsWith('/api/v1/')) return nativeFetch(input,options);
    const method = (options.method || (input instanceof Request ? input.method : 'GET')).toUpperCase();
    const headers = new Headers(input instanceof Request ? input.headers : undefined);
    new Headers(options.headers||{}).forEach((v,k)=>headers.set(k,v));
    if (!['GET','HEAD','OPTIONS'].includes(method)) {
      const csrf=getCookie('tp_csrf');
      if (csrf) headers.set('X-TimePilot-CSRF',csrf);
    }
    const match=location.pathname.match(/^\/projects\/([0-9a-fA-F-]{36})(?:\/|$)/);
    const project=match?.[1] || new URLSearchParams(location.search).get('project');
    if (project && !headers.has('X-TimePilot-Project')) headers.set('X-TimePilot-Project',project);
    return nativeFetch(input,{...options,headers});
  };
})();

/* Enterprise workspace shell: truthful mode, scoped navigation and logout. */
(() => {
  'use strict';
  if (!window.TIMEPILOT_ENTERPRISE) return;
  document.addEventListener('DOMContentLoaded', async () => {
    try {
      const r=await fetch('/api/v1/auth/me',{credentials:'same-origin'});
      if (!r.ok) {location.replace('/login');return;}
      const account=await r.json();
      window.TimePilotIdentity={user:account.username,tenantId:account.tenant_id,
        tenantRole:account.tenant_role};
      for(const link of document.querySelectorAll('nav a[href="/agent"],nav a[href="/settings"]')){
        link.hidden=true;link.setAttribute('aria-hidden','true');
      }
      const status=document.querySelector('.sidebar-status');
      if(status)status.innerHTML='<span class="pulse"></span><span>企业受控工作空间</span>';
      const footer=document.querySelector('.sidebar-bottom');
      const old=footer?.querySelector('p');
      if(old)old.textContent=`当前租户 ${account.tenant_id.slice(0,8)} · ${account.username}`;
      const newButton=document.getElementById('header-new');
      if(newButton&&account.tenant_role!=='tenant_admin')newButton.hidden=true;
      if(account.tenant_role==='tenant_admin'){
        const nav=document.querySelector('nav[aria-label="主导航"]');
        if(nav){const link=document.createElement('a');link.href='/identity';link.textContent='账户与项目权限';
          nav.append(link);}
      }
      if(footer){const btn=document.createElement('button');btn.type='button';btn.className='button button-small';
        btn.textContent='退出账户';btn.style.marginTop='12px';
        btn.onclick=async()=>{
          btn.disabled=true;
          try{await fetch('/api/v1/auth/logout',{method:'POST',credentials:'same-origin'});}
          finally{location.replace('/login');}
        };
        footer.append(btn);
      }
    }catch(error){const footer=document.querySelector('.sidebar-bottom');
      if(footer)footer.textContent='账户服务不可用：'+error.message;}
  });
})();
