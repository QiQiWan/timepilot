/* v0.23.6 Project asset directory. Server-side scoped search and safe soft-delete. */
(() => {
  'use strict';
  const E=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const U=encodeURIComponent;
  const json=x=>({method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(x)});
  let session=null;
  function stop(){if(session){clearTimeout(session.timer);session=null;}}
  function path(s,tail=''){return '/projects/'+U(s.pid)+'/catalog/'+tail;}
  function href(s,aid){return '/projects/'+U(s.pid)+'/data/catalog/'+U(aid);}
  function row(s,a){const action=a.state==='trashed'?'restore':'trash';
    return `<tr><td><a href="${href(s,a.asset_id)}"><strong>${E(a.name)}</strong></a><small class="muted">${E(a.source_file)} · ${E(a.source_sheet)}</small></td>
      <td>${E(a.row_count??'—')} × ${E(a.column_count??'—')}</td><td>${E(a.version_id||'—').slice(0,12)}</td>
      <td><span class="subtle-tag">${a.state==='trashed'?'回收站':'可用'}</span></td>
      <td class="tp-assets-actions"><a class="button button-small" href="${href(s,a.asset_id)}">打开</a>
       ${a.state==='active'?`<a class="button button-small" href="/projects/${U(s.pid)}/bi/native/${U(a.native_asset_id)}">进入 BI</a>`:''}
       <button type="button" class="button button-small" data-asset-action="${action}" data-asset-id="${E(a.asset_id)}" data-revision="${a.lifecycle_revision}">${action==='trash'?'删除':'恢复'}</button></td></tr>`;}
  function display(s){const target=s.root.querySelector('[data-assets-list]');if(!target)return;
    const d=s.result;if(!d)return;
    target.innerHTML=`<p class="muted small" role="status">共 ${d.total} 项 · 第 ${d.page} 页 · 每页 ${d.page_size} 项</p>
      ${d.items.length?`<div class="table-wrap"><table class="data-table"><thead><tr><th>数据资产</th><th>行 × 列</th><th>数据版本</th><th>状态</th><th>操作</th></tr></thead><tbody>${d.items.map(a=>row(s,a)).join('')}</tbody></table></div>`
       :'<div class="empty"><b>当前没有匹配的数据资产</b><p>调整搜索词或切换回收站。</p></div>'}
      <nav class="tp-assets-pagination" aria-label="资产分页"><button type="button" class="button button-small" data-asset-action="prev" ${d.page===1?'disabled':''}>上一页</button>
       <span>第 ${d.page} 页</span><button type="button" class="button button-small" data-asset-action="next" ${!d.next_page?'disabled':''}>下一页</button></nav>`;
  }
  async function reload(s){if(s!==session)return;
    const request=++s.requestId,target=s.root.querySelector('[data-assets-list]');if(target)target.setAttribute('aria-busy','true');
    const params=new URLSearchParams({q:s.q,state:s.state,sort:s.sort,page:String(s.page),page_size:'30'});
    try{const data=await s.ctx.req(path(s,'assets')+'?'+params);
      if(s!==session||request!==s.requestId)return;
      s.result=data;display(s);
    }catch(error){if(s===session&&request===s.requestId&&target)target.innerHTML=`<div class="helper danger">${E(error.message)}</div>`}
    finally{if(s===session&&request===s.requestId)target?.setAttribute('aria-busy','false');}
  }
  async function confirmAction(s,id,action,rev){const original=s.result?.items?.find(a=>a.asset_id===id);
    if(!original)return;
    const dialog=s.root.querySelector('#tp-asset-confirm');if(!dialog)return;
    let note='';
    if(action==='trash'){
      try{const impact=await s.ctx.req(path(s,'assets/'+U(id)+'/impact'));
        if(s!==session)return;
        const count=impact.dependencies?.length||0;const blockers=impact.active_blockers?.length||0;
        note=`此资产当前有 ${count} 项历史视图、分析或模型引用。移入回收站后保留全部原文件和历史记录。${blockers?'目前有运行中的依赖，无法回收。':''}`;
        if(blockers){s.ctx.toast('运行中的模型任务阻止移入回收站');return}
      }catch(e){s.ctx.toast('无法核实删除影响：'+e.message);return;}
    }else note='恢复后重新出现在可用数据资产目录中，原有编号、版本和内容哈希保持不变。';
    dialog.innerHTML=`<form method="dialog"><h3>${action==='trash'?'将资产移入回收站？':'恢复此数据资产？'}</h3>
      <p><strong>${E(original.name)}</strong></p><p>${E(note)}</p>
      <div class="action-row"><button class="button" value="cancel">取消</button><button class="button button-primary" value="confirm">${action==='trash'?'确认移入回收站':'确认恢复'}</button></div></form>`;
    const accepted=await new Promise(resolve=>{
      dialog.addEventListener('close',()=>resolve(dialog.returnValue==='confirm'),{once:true});dialog.showModal();
    });
    if(!accepted||s!==session)return;
    try{await s.ctx.req(path(s,'assets/'+U(id)+'/'+action),json({
      request_id:crypto.randomUUID(),expected_revision:rev,confirm:true}));
      s.ctx.toast(action==='trash'?'资产已移入回收站':'资产已恢复');await reload(s);
    }catch(e){s.ctx.toast('操作失败：'+e.message);}
  }
  function mount(ctx,pid,root=document.getElementById('tp-asset-catalog')){
    stop();if(!root)return;
    const s={ctx,pid,root,q:'',state:'active',sort:'updated_desc',page:1,requestId:0,result:null,timer:null};session=s;
    root.innerHTML=`<div class="tp-assets-toolbar"><label class="field"><span>搜索名称、源文件、工作表或字段名</span><input data-assets-search type="search" placeholder="输入关键词查询整个项目"></label>
      <label class="field"><span>状态</span><select data-assets-state><option value="active">数据资产</option><option value="trashed">回收站</option></select></label>
      <label class="field"><span>排序</span><select data-assets-sort><option value="updated_desc">最近更新</option><option value="name_asc">名称 A–Z</option><option value="name_desc">名称 Z–A</option></select></label></div>
      <section data-assets-list aria-live="polite"><p class="muted">正在读取资产目录…</p></section><dialog id="tp-asset-confirm" class="tp-asset-modal"></dialog>`;
    root.addEventListener('input',e=>{if(e.target.matches('[data-assets-search]')){
      s.q=e.target.value;s.page=1;clearTimeout(s.timer);s.timer=setTimeout(()=>reload(s),220);
    }});
    root.addEventListener('change',e=>{if(e.target.matches('[data-assets-state]')){s.state=e.target.value;s.page=1;void reload(s)}
      else if(e.target.matches('[data-assets-sort]')){s.sort=e.target.value;s.page=1;void reload(s)}});
    root.addEventListener('click',e=>{const button=e.target.closest('[data-asset-action]');if(!button)return;
      const action=button.dataset.assetAction;
      if(action==='prev'){s.page=Math.max(1,s.page-1);void reload(s)}
      else if(action==='next'&&s.result?.next_page){s.page=s.result.next_page;void reload(s)}
      else if(action==='trash'||action==='restore')void confirmAction(s,button.dataset.assetId,action,Number(button.dataset.revision));
    });
    void reload(s);
  }
  window.TimePilotAssetCatalog={mount,stop};
})();
