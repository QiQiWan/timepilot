/* v0.35.6: one source-pinned analysis workspace with in-place expert tools. */
(() => {
  'use strict';
  const encode=encodeURIComponent;
  const sections=['overview','quality','statistics','features','outputs'];
  const labels=['概览','数据质量','统计图表','自动特征','成果'];
  const tools={manual:['build','手工构造'],advanced:['advanced','高级算法'],bi:['bi','BI 数据'],reports:[null,'报告与成果']};
  let host=null, currentProject=null, previousListener=null, workspaceGeneration=0;
  const getTool=()=>new URLSearchParams(location.search).get('tool')||'';
  const validTool=t=>Object.hasOwn(tools,t)?t:'';
  function sourceQuery(){
    const params=new URLSearchParams(location.search);
    const ctx=window.TimePilotDataAnalysisUI?.getContext?.()||{};
    // Never borrow the previous project's source when opening an expert tool.
    const sameProject=ctx.pid===currentProject;
    return {asset:params.get('asset')||(sameProject?ctx.aid:''),
      version:params.get('version')||(sameProject?ctx.vid:'base')};
  }
  function route(tool,section=null,setId=null){
    const url=new URL(window.location.href);
    if(tool)url.searchParams.set('tool',tool);else url.searchParams.delete('tool');
    if(tool==='manual' && setId)url.searchParams.set('set',setId);
    else url.searchParams.delete('set');
    if(!tool){
      if(section&&section!=='overview')url.searchParams.set('section',section);
      else url.searchParams.delete('section');
    }
    const source=sourceQuery();
    if(source.asset)url.searchParams.set('asset',source.asset);
    if(source.version)url.searchParams.set('version',source.version);
    // A tool must never inherit stale values from a different source or a
    // different tool, including an old evaluation or candidate selection.
    if(tool){
      for(const key of ['candidate','advanced_candidate','chart','filter','family','entity','resolution'])url.searchParams.delete(key);
    }
    return url.pathname+url.search;
  }
  function openTool(tool,options={}){
    const selected=validTool(tool);
    if(tool&& !selected)throw Error('未知的工作台工具');
    const next=route(selected,null,options.setId||null);
    if(next!==location.pathname+location.search){
      if(!host?.isConnected) return;
      // Use the host router so Back/Forward, data version and pending async
      // loads always agree. No navigation to a second feature workspace.
      host.__tpCtx?.navigate(next);
    }
  }
  function activate(section,push=false,scroll=true){
    if(!sections.includes(section))section='overview';
    const root=host;
    if(!root?.isConnected)return;
    if(getTool()){
      const next=route('',section);
      root.__tpCtx?.navigate(next);
      return;
    }
    root.querySelectorAll('[data-analysis-section]').forEach(button=>{
      const selected=button.dataset.analysisSection===section;
      button.classList.toggle('active',selected);
      button.setAttribute('aria-current',selected?'step':'false');
    });
    const url=new URL(window.location.href);
    if(section==='overview')url.searchParams.delete('section');
    else url.searchParams.set('section',section);
    const target=url.pathname+url.search;
    if(target!==location.pathname+location.search){
      if(push)history.pushState(history.state,'',target);
      else history.replaceState(history.state,'',target);
    }
    // Switch the visible task without scrolling through three stacked workbenches.
    root.dataset.section=section;
    window.TimePilotDataAnalysisUI?.onSectionChange?.(section);
    if(section==='features')root.__tpEnsureFeature?.();
    if(section==='outputs')root.__tpEnsureOutput?.();
    if(typeof root.setAttribute==='function')root.setAttribute('aria-label','数据分析任务：'+({'overview':'概况','quality':'质量检查','statistics':'统计图表','features':'自动特征','outputs':'成果'}[section]||'概况'));
    if(scroll)root.scrollIntoView?.({block:'start',behavior:'instant'});

  }
  function header(pid,tool){
    // Two navigation layers max: project destinations and the active analysis task.
    const steps=sections.map((name,i)=>`<button type="button" data-analysis-section="${name}">${['概况','质量检查','统计图表','自动特征','成果'][i]}</button>`).join('');
    return `<div class="tp-analysis-top" role="navigation" aria-label="分析任务">
      <div class="tp-analysis-top-line">
        <div class="tp-analysis-steps" role="group" aria-label="切换任务">${steps}</div>
        <details class="tp-analysis-more"><summary>${tool?tools[tool][1]:'其他工具'} <span aria-hidden="true">⌄</span></summary>
          <div class="tp-analysis-tools" role="group" aria-label="更多工具">
           <button type="button" data-analysis-tool="">返回自动分析</button>
           ${Object.entries(tools).map(([name,config])=>`<button type="button" data-analysis-tool="${name}" class="${tool===name?'active':''}">${config[1]}</button>`).join('')}
          </div>
        </details>
      </div>
    </div>`;
  }
  async function render(ctx,pid){
    const generation=++workspaceGeneration;
    host=document.getElementById('tp-analysis-workspace');currentProject=pid;
    if(!host)return;
    const node=host,tool=validTool(getTool());
    node.__tpCtx=ctx;
    node.dataset.tool=tool||'dashboard';
    const requested=new URLSearchParams(location.search).get('section')||'overview';
    node.dataset.section=sections.includes(requested)?requested:'overview';
    // Only one live feature controller exists at a time. The report browser
    // uses its own controller; others reuse the existing guarded operations.
    if(previousListener)document.removeEventListener('timepilot:feature-preview-ready',previousListener);
    previousListener=null;
    const detail=tool==='reports'?'<div id="tp-analysis-delivery" aria-label="报告与成果"></div>':
      tool?'<section id="tp-unified-features" aria-label="工作台工具"><div id="tp-feature-lab" aria-live="polite"></div></section>':
      `<section class="tp-analysis-data-surface" aria-label="数据概况与检查"><div id="tp-project-data-analysis"></div><div id="tp-unified-insights" aria-label="有证据的自动研判"></div></section>
       <section class="tp-analysis-feature-surface" id="tp-unified-features" aria-label="特征与成果"><div id="tp-feature-lab" aria-live="polite"></div><div id="tp-analysis-delivery-inline" aria-live="polite"></div></section>`;
    node.innerHTML=header(pid,tool)+detail;
    node.__tpFeaturePromise=null;node.__tpEnsureFeature=null;
    node.__tpOutputPromise=null;node.__tpEnsureOutput=null;
    if(node.__tpWorkspaceClick)node.removeEventListener('click',node.__tpWorkspaceClick);
    node.__tpWorkspaceClick=event=>{
      const button=event.target.closest('[data-analysis-tool],[data-analysis-section]');
      if(!button||!node.contains(button)||node!==host)return;
      event.preventDefault();
      if(button.hasAttribute('data-analysis-tool'))openTool(button.dataset.analysisTool||'');
      else activate(button.dataset.analysisSection,true);
    };
    node.addEventListener('click',node.__tpWorkspaceClick);
    if(tool==='reports'){
      window.TimePilotFeatureV2UI?.dispose?.();
      window.TimePilotDataAnalysisUI?.dispose?.();
      const source=sourceQuery();
      await window.TimePilotAnalysisDeliveryUI?.render(ctx,pid,
        node.querySelector('#tp-analysis-delivery'),source.asset,source.version);
      return;
    }
    if(tool){
      window.TimePilotDataAnalysisUI?.dispose?.();
      await window.TimePilotFeatureV2UI?.render(ctx,pid,tools[tool][0]);
      if(node!==host||currentProject!==pid||generation!==workspaceGeneration)return;
      if(tool==='bi')await window.TimePilotFeatureV2UI?.openIntegratedBi?.();
      return;
    }
    // The results task reads immutable assets and reports directly; it must
    // not initialize the costly feature-construction controller.
    node.__tpEnsureOutput=()=>{
      if(node.__tpOutputPromise)return node.__tpOutputPromise;
      const source=sourceQuery();
      node.__tpOutputPromise=Promise.resolve(window.TimePilotAnalysisDeliveryUI?.render(
        ctx,pid,node.querySelector('#tp-analysis-delivery-inline'),source.asset,source.version))
        .catch(error=>{
          const target=node.querySelector('#tp-analysis-delivery-inline');
          if(target&&node===host&&generation===workspaceGeneration)
            target.textContent='成果读取失败：'+String(error?.message||error);
          node.__tpOutputPromise=null;
        });
      return node.__tpOutputPromise;
    };
    previousListener=async event=>{
      const params=new URLSearchParams(location.search);
      if(node!==host||!host.isConnected||currentProject!==pid||getTool()||
         event.detail?.pid!==pid||params.get('asset')!==event.detail.asset_id||
         (params.get('version')||'base')!==event.detail.version_id)return;
      if(!node.__tpFeaturePromise)return;
      try{await window.TimePilotFeatureV2UI?.render(ctx,pid,'analysis');
        await window.TimePilotAnalysisInsightsUI?.refresh();}
      catch(_){/* Keep independently validated data diagnosis visible. */}
    };
    document.addEventListener('timepilot:feature-preview-ready',previousListener);
    // Feature UI is mounted on demand; overview/quality/statistics must not
    // create a hidden feature controller or start extra chart requests.
    node.__tpEnsureFeature=()=>{
      if(node.__tpFeaturePromise)return node.__tpFeaturePromise;
      node.__tpFeaturePromise=Promise.resolve(window.TimePilotFeatureV2UI?.render(ctx,pid,'analysis'))
        .catch(()=>{const target=node.querySelector('#tp-feature-lab');
          if(target)target.textContent='特征分析暂不可用，请检查任务中心并重试。';});
      return node.__tpFeaturePromise;
    };
    try{await window.TimePilotDataAnalysisUI?.render(ctx,pid);}
    catch(_){const target=node.querySelector('#tp-project-data-analysis');
      if(target)target.textContent='数据画像暂不可用，请检查日志并刷新。';}
    if(node!==host||currentProject!==pid||generation!==workspaceGeneration)return;
    const source=window.TimePilotDataAnalysisUI?.getContext?.();
    if((new URLSearchParams(location.search).get('section')||'overview')==='overview')
      window.TimePilotAnalysisInsightsUI?.mount(ctx,pid,node.querySelector('#tp-unified-insights'),
        source?.aid,source?.vid);
    activate(new URLSearchParams(location.search).get('section')||'overview',false,
      !!new URLSearchParams(location.search).get('section'));
  }
  window.TimePilotAnalysisWorkspace={render,activate,openTool,sourceQuery};
})();
