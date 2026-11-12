/* Dedicated route-level BI shell. Perspective remains the only primary viewer.
 * Governance/query/edit legacy DOM is preserved in a closed advanced drawer.
 */
(() => {
  'use strict';
  let activeShell=null;
  const qs=(root,s)=>root?.querySelector(s);
  function mount(root){
    const explore=qs(root,'#native-explore');
    if(!explore)return;
    if(qs(root,'#tp-bi-workspace-root'))return;
    const title=qs(root,'.page-heading h1')?.textContent || 'BI 工作台';
    const back=qs(root,'.page-heading a[href]')?.getAttribute('href') || '/projects';
    const shell=document.createElement('section');
    shell.id='tp-bi-workspace-root';shell.setAttribute('aria-label','Perspective BI 全屏工作台');
    shell.innerHTML=`<div class="tp-bi-shell-head">
      <a class="tp-bi-shell-back" href="${back.replace(/"/g,'&quot;')}">← 返回项目</a>
      <strong class="tp-bi-shell-name"></strong>
      <span class="tp-bi-shell-pill" id="tp-bi-shell-source">完整数据工作区</span>
      <div class="tp-bi-shell-actions">
        <button class="button button-small" type="button" data-native="save-view">保存视图</button>
        <button class="button button-small" type="button" data-workspace-action="manual" aria-label="打开 BI 使用手册">使用手册</button>
        <button class="button button-small" type="button" data-workspace-action="advanced" aria-expanded="false" aria-controls="tp-bi-advanced">高级操作</button>
        <button class="button button-small" type="button" data-workspace-action="fullscreen" aria-label="切换浏览器全屏">全屏</button>
      </div>
    </div>
    <div class="tp-bi-shell-canvas" id="tp-bi-main-canvas"></div>
    <div class="tp-bi-shell-status" id="tp-bi-shell-status" role="status">Perspective 工作区 · 当前来源固定到资产版本</div>
    <aside id="tp-bi-advanced" class="tp-bi-shell-drawer" aria-label="数据资产与高级操作" hidden>
      <header><strong>数据及版本管理</strong><button type="button" data-workspace-action="advanced" aria-label="关闭高级操作">×</button></header>
      <div class="tp-bi-drawer-body"></div>
    </aside>`;
    qs(shell,'.tp-bi-shell-name').textContent=title;
    const main=qs(shell,'#tp-bi-main-canvas'),drawer=qs(shell,'.tp-bi-drawer-body');
    // Move existing nodes intact: delegated events and version/edit listeners survive.
    root.insertBefore(shell,root.firstChild);
    main.append(explore);
    const groups=[
      {title:'数据来源和视图',help:'切换显示方式，或打开已有分析结果。',selectors:['.tp-native-tools','#native-views'],opened:true},
      {title:'筛选完整数据',help:'需要按条件查询完整记录时使用；普通图表筛选可直接在 Perspective 操作。',selectors:['#native-query']},
      {title:'编辑数据',help:'修改单元格时先预览，再生成保留原件的新版本。',selectors:['#native-edit']},
      {title:'版本与恢复',help:'查看修改历史、撤销或恢复到指定版本。',selectors:['#native-history']},
      {title:'核实字段类型',help:'字段类型错误时检查转换，并创建独立的数据版本。',selectors:['#native-advanced']},
      {title:'设备事件分析',help:'根据实际时间、设备和维护字段制作趋势与事件对照。',selectors:['#tp-native-industrial']},
      {title:'兼容工具',help:'保留旧查询表单，日常分析建议使用右下角分析助手。',selectors:['#native-assistant','.tp-native-tabs','.page-heading']},
    ];
    for(const group of groups){
      const nodes=[];
      for(const selector of group.selectors){
        for(const node of Array.from(root.querySelectorAll(selector))){
          if(node.closest('#tp-bi-advanced'))continue;
          if(selector==='.tp-native-tools'&&!node.parentElement?.matches('#native-explore'))continue;
          nodes.push(node);
        }
      }
      if(!nodes.length)continue;
      const section=document.createElement('details');section.className='tp-bi-drawer-group';
      if(group.opened)section.open=true;
      const summary=document.createElement('summary');summary.textContent=group.title;
      const note=document.createElement('p');note.className='tp-bi-drawer-hint';note.textContent=group.help;
      const content=document.createElement('div');content.className='tp-bi-drawer-group-content';
      section.append(summary,note,content);drawer.append(section);
      for(const node of nodes)content.append(node);
    }
    const extension=document.createElement('section');extension.id='tp-bi-extension-host';
    extension.className='tp-bi-extension-host';extension.hidden=true;
    extension.setAttribute('aria-label','附加统计分析面板');main.append(extension);
    shell.addEventListener('click',async e=>{
      const button=e.target.closest('[data-workspace-action]');if(!button)return;
      const action=button.dataset.workspaceAction;
      if(action==='advanced'){
        const panel=qs(shell,'#tp-bi-advanced');panel.hidden=!panel.hidden;
        qs(shell,'[data-workspace-action="advanced"]').setAttribute('aria-expanded',String(!panel.hidden));
      }else if(action==='manual'){
        window.TimePilotBIHelp?.open?.();
      }else if(action==='fullscreen'){
        try{
          if(document.fullscreenElement)await document.exitFullscreen();
          else if(shell.requestFullscreen)await shell.requestFullscreen();
          else throw new Error('浏览器不支持 Fullscreen API');
        }catch(err){status('浏览器全屏未启用：'+err.message)}
      }
    });
    activeShell=shell;
  }
  function status(message){
    const item=qs(activeShell,'#tp-bi-shell-status');if(item)item.textContent=String(message||'');
  }
  function source(text){
    const node=qs(activeShell,'#tp-bi-shell-source');if(node)node.textContent=String(text||'');
  }
  function mode(enabled){
    document.body.dataset.workspaceMode=enabled?'native-bi':'';
    if(!enabled){window.TimePilotBIHelp?.close?.();activeShell=null;}
  }
  window.TimePilotBIWorkspaceLayout={mount,mode,status,source};
})();
