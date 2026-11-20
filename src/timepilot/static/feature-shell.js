/* FeatureLab 2.8: one project/asset context, four focused screens, job evidence. */
(() => {
  'use strict';
  const e=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const u=encodeURIComponent;
  const h={e,u};
  const json=(method,data)=>({method,headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
  let ctx=null,s=null,pollTimer=null,planTimer=null,autoTimer=null,dialog=null,renderGeneration=0,activeAbort=null;
  let chartGeneration=0;
  const unsavedDrafts=new Map();
  const draftKey=state=>state?[state.pid,state.asset,state.version||'base'].join('|'):'';
  function preserveManualDraft(){
    if(s?.steps?.length&&s?.tab==='build'){
      unsavedDrafts.set(draftKey(s),{steps:s.steps.map(x=>JSON.parse(JSON.stringify(x))),
        manualDraft:{...s.manualDraft},workspaceMode:s.workspaceMode});
    }
  }
  const base=()=>'/projects/'+u(s.pid)+'/feature-lab';
  const api=(path,opts)=>ctx.req(base()+path,{...(opts||{}),signal:activeAbort?.signal});
  const tabs=[['bi','bi'],['analysis','analysis'],['catalog','catalog'],['profile','profile'],['build','build'],['evaluate','evaluate'],['advanced','advanced']];
  const id=()=>globalThis.crypto?.randomUUID?.()||`req-${Date.now()}-${Math.random().toString(36).slice(2)}`;
  function dispose(){preserveManualDraft();renderGeneration++;chartGeneration++;clearTimeout(pollTimer);clearTimeout(planTimer);clearTimeout(autoTimer);pollTimer=null;planTimer=null;autoTimer=null;dialog=null;activeAbort?.abort();activeAbort=null;window.TimePilotFeaturePerspective?.dispose?.();}
  function note(message){s.notice=message;ctx?.toast?.(message);show();}
  function show(){
    const root=document.getElementById('tp-feature-lab');if(!root||!s)return;
    const oldShell=root.querySelector('.tp-fl2-shell');
    const keepViewer=['build','bi'].includes(s.tab)?oldShell?.querySelector('#tp-fl2-perspective'):null;
    const draft=document.getElementById('tp-fl2-message')?.value||'';
    const assets=s.assets.map(a=>`<option value="${e(a.native_asset_id)}" ${s.asset===a.native_asset_id?'selected':''}>${e(a.asset?.name||a.native_asset_id)}</option>`).join('');
    const docs=s.contract;
    const versions=[{version_id:'base'},...(s.versions||[]).filter(v=>v.version_id!=='base')];
    const markup=`<div class="tp-fl2-shell">
    <nav class="tp-fl2-compact-nav" aria-label="FeatureLab workspace">
      <a href="/projects/${u(s.pid)}/features/analysis?asset=${u(s.asset)}&version=${u(s.version||'base')}">${s.tab==='analysis'?'<strong>\u667a\u80fd\u5206\u6790</strong>':'\u667a\u80fd\u5206\u6790'}</a>
      <a href="/projects/${u(s.pid)}/features/catalog?asset=${u(s.asset)}&version=${u(s.version||'base')}">${s.tab==='catalog'?'<strong>\u7ed3\u679c\u4e0e\u8d44\u4ea7</strong>':'\u7ed3\u679c\u4e0e\u8d44\u4ea7'}</a>
      <details ${['profile','build','evaluate','advanced'].includes(s.tab)?'open':''}><summary>\u9ad8\u7ea7\u5de5\u5177</summary>${[['profile','\u6570\u636e\u753b\u50cf'],['build','\u624b\u5de5\u7279\u5f81'],['evaluate','\u5b9e\u9a8c\u8bc4\u4ef7'],['advanced','\u9ad8\u7ea7\u7b97\u6cd5']].map(([tab,label])=>`<a href="/projects/${u(s.pid)}/features/${tab}?asset=${u(s.asset)}&version=${u(s.version||'base')}">${label}</a>`).join('')}</details>
    </nav>
    ${s.assets.length?`<div class="tp-fl2-context"><label>数据 <select data-feature-v2-change="select-asset" aria-label="选择数据">${assets}</select></label><label>版本 <select data-feature-v2-change="select-version" aria-label="选择数据版本">${versions.map(v=>`<option value="${e(v.version_id)}" ${s.version===v.version_id?'selected':''}>${e(v.version_id==='base'?'原始版本':(v.is_current?'当前 · ':'')+v.version_id.slice(0,12))}</option>`).join('')}</select></label><span>字段 ${e(docs?.fields?.length||0)}</span></div>`:''}
    ${s.notice?`<div class="tp-fl2-notice" role="status">${e(s.notice)} <button class="button button-small" data-feature-v2="dismiss-notice">关闭</button></div>`:''}
    <div class="tp-fl2-body">${s.assets.length?(window.TimePilotFeatureViews?.[s.tab]?.(s,h)||'<p>该页面尚未加载。</p>'):'<div class="tp-fl2-empty">还没有可用数据资产。请先进入项目数据管理，上传并发布数据。</div>'}</div>
    <div class="tp-fl2-assistant"><button type="button" class="button tp-fl2-chat-toggle" data-feature-v2="toggle-assistant" aria-expanded="${Boolean(s.assistantOpen)}">${s.assistantOpen?'收起助手':'特征助手'}</button>
      ${s.assistantOpen?`<form id="tp-fl2-agent-form" class="tp-fl2-chat"><label for="tp-fl2-message">描述想完成的特征分析</label><textarea name="message" id="tp-fl2-message" rows="3" placeholder="例如：将 A1 和 A2 相加，生成新特征"></textarea><div class="tp-fl2-suggestions"><button type="button" data-feature-v2="insert-prompt" data-prompt="查看数据字段">查看字段</button><button type="button" data-feature-v2="insert-prompt" data-prompt="查看数据画像">查看画像</button><button type="button" data-feature-v2="insert-prompt" data-prompt="推荐适合当前数据的特征">推荐特征</button></div><button class="button button-primary" type="submit">生成执行计划</button>
      ${s.plan?`<div class="tp-fl2-plan"><h3>本次任务</h3>
        <p>${e({ready:'可以执行',running:'正在执行',awaiting_job:'等待真实计算完成',awaiting_viewer:'等待真实 Perspective 图表渲染',succeeded:'已完成',failed:'执行失败',needs_clarification:'需要补充信息',unsupported:'当前功能不可用'}[s.plan.status]||s.plan.status)}</p>
        ${s.plan.plan.reason?`<p>${e(s.plan.plan.reason)}</p>`:''}
        ${(s.plan.steps||[]).map((x,i)=>`<div class="tp-fl2-plan-step"><strong>${i+1}. ${e({succeeded:'已完成',pending:'待执行',awaiting_job:'计算中',awaiting_viewer:'等待图表回执',failed:'失败'}[x.status]||x.status)}</strong> · ${e(x.tool_id)}
          ${x.result?.error?`<p>${e(x.result.error)}</p>`:''}</div>`).join('')}
        ${s.plan.status==='ready'?`<button class="button" type="button" data-feature-v2="execute-plan">执行此计划</button>`:''}
        ${s.plan.status==='awaiting_job'?`<button class="button" type="button" data-feature-v2="resume-plan">检查计算状态</button>`:''}
        ${s.plan.status==='awaiting_viewer'?`<button class="button" type="button" data-feature-v2="render-agent-result">在原生 BI 加载并确认图表</button>`:''}
        ${['failed','unsupported','needs_clarification'].includes(s.plan.status)?`<button class="button" type="button" data-feature-v2="replan-plan">调整要求</button>`:''}
        <details><summary>查看执行证据</summary><pre class="tp-fl2-json">${e(JSON.stringify({steps:s.plan.steps,observations:s.plan.observations},null,2))}</pre></details>
      </div>`:''}</form>`:''}
    </div><div id="tp-fl2-dialog-host"></div></div>`;
    // Keep the live Perspective subtree CONNECTED during UI updates. Inserting
    // a detached Viewer again can invoke disconnectedCallback and drop state.
    if (keepViewer && oldShell) {
      const temp=document.createElement('div');temp.innerHTML=markup;
      const freshShell=temp.querySelector('.tp-fl2-shell');
      const oldBody=oldShell.querySelector('.tp-fl2-body');
      const oldWork=oldBody?.querySelector('.tp-fl2-work');
      const nextWork=freshShell.querySelector('.tp-fl2-work');
      const viewerSection=oldWork?.querySelector('.tp-fl2-viewer-section');
      if (oldBody && oldWork && nextWork && viewerSection) {
        const bar=viewerSection.querySelector('.tp-fl2-viewer-toolbar');
        const nextBar=nextWork.querySelector('.tp-fl2-viewer-toolbar');
        if (bar && nextBar) bar.replaceWith(nextBar);
        const nextViewer=nextWork.querySelector('.tp-fl2-viewer-section');
        if (nextViewer) viewerSection.className=nextViewer.className;
        // Replace only surrounding form, preview and job nodes.
        for (const child of [...oldWork.children]) if(child!==viewerSection) child.remove();
        let past=false;
        for (const child of [...nextWork.children]) {
          if(child.classList.contains('tp-fl2-viewer-section')) {past=true;continue;}
          if(past)oldWork.appendChild(child);
          else oldWork.insertBefore(child,viewerSection);
        }
        for(const child of [...oldShell.children])if(child!==oldBody)child.remove();
        past=false;
        for(const child of [...freshShell.children]) {
          if(child.classList.contains('tp-fl2-body')){past=true;continue;}
          if(past)oldShell.appendChild(child);
          else oldShell.insertBefore(child,oldBody);
        }
      } else root.innerHTML=markup;
    } else root.innerHTML=markup;
    const msg=document.getElementById('tp-fl2-message');if(msg)msg.value=draft;
  }
  function resetAssetState(){
    if(!s)return;
    Object.assign(s,{steps:[],contract:null,operators:null,sets:[],setAudits:{},activeSet:null,
      runResult:null,previewResult:null,evalPlan:null,evalResult:null,evalSelection:null,activeEvalSet:null,profile:null,
      profileId:null,job:null,plan:null,lineage:null,autoSession:null,timeBasis:null,autoExplanation:null,autoSupervision:null,autoDelivery:null,advancedBatches:null,advancedCapabilities:null,session:null,candidatePreview:null,fieldSuggestions:null,quickSelectedFields:null,focusCandidateId:null,manualDraft:{},workspaceMode:'quick',chartMode:'trend',viewerOpen:false,advanced:null,workflow:null,candidateSession:null,uiChart:null,uiChartStatus:null,uiChartError:null,uiChartEntity:'',uiChartEntityField:'',uiChartResolution:120,uiFocused:null,uiAdvancedFocus:null,uiFamily:'all',uiAdvancedOpen:false,uiFilter:'all',uiDraftSelection:null,dataAnalysis:null});
    chartGeneration++;
  }
  async function refreshBatches(){
    const local=s;
    if(!local?.asset)return;
    const result=await api('/analysis/advanced-batches?asset_id='+u(local.asset)+
                              '&version_id='+u(local.version||'base'));
    if(local!==s)return;
    s.advancedBatches=result;
    show();
  }
  async function refreshData(expected=renderGeneration){
    const local=s;
    local.assets=(await ctx.req('/projects/'+u(s.pid)+'/native-assets')).filter(a=>a.asset?.columns?.length);
    if(expected!==renderGeneration || local!==s)return;
    if(!s.assets.some(a=>a.native_asset_id===s.asset)){
      const oldAsset=s.asset; s.asset=s.assets[0]?.native_asset_id||'';
      if(oldAsset!==s.asset)resetAssetState();
    }
    if(s.asset){
      const asset=s.asset,version=s.version||'base';
      const [contract,operators,sets,workflow,history]=await Promise.all([
        api('/contracts?asset_id='+u(asset)+'&version_id='+u(version)),api('/operators?asset_id='+u(asset)+'&version_id='+u(version)),api('/sets?asset_id='+u(asset)),api('/workflow/summary?asset_id='+u(asset)+'&version_id='+u(version)),ctx.req('/native-bi/assets/'+u(asset)+'/versions').catch(()=>({versions:[]}))]);
      if(expected!==renderGeneration || local!==s || asset!==s.asset)return;
      s.contract=contract;s.operators=operators;s.sets=(sets||[]).filter(x=>x.version_id===version);s.workflow=workflow;s.versions=history?.versions||[];
      if(s.tab==='catalog') {
        const audits=await api('/sets/temporal-audits?asset_id='+u(asset));
        if(expected!==renderGeneration||local!==s||asset!==s.asset)return;
        s.setAudits=Object.fromEntries((audits.items||[]).map(a=>[a.set_id,a]));
      }
      if(s.tab==='analysis'){
        const [recentAuto,basis]=await Promise.all([
          api('/analysis/latest?asset_id='+u(asset)+'&version_id='+u(version)),
          api('/time-basis/infer?asset_id='+u(asset)+'&version_id='+u(version))]);
        if(expected!==renderGeneration||local!==s||asset!==s.asset)return;
        s.autoSession=recentAuto.session||null;s.timeBasis=basis;
        const [batches,caps]=await Promise.allSettled([
          api('/analysis/advanced-batches?asset_id='+u(asset)+'&version_id='+u(version)),
          api('/advanced/capabilities')]);
        if(expected!==renderGeneration||local!==s||asset!==s.asset)return;
        s.advancedBatches=batches.status==='fulfilled'?batches.value:{batches:[],candidate_count:0};
        s.advancedCapabilities=caps.status==='fulfilled'?caps.value:{engines:{}};
        // Optional, read-only analysis context. Never block feature engineering.
        try {
          const eda=await api('/data-analysis?asset_id='+u(asset)+'&version_id='+u(version));
          if(expected!==renderGeneration||local!==s||asset!==s.asset)return;
          s.dataAnalysis=(eda?.source_sha256===contract?.source_sha256)?eda:null;
        } catch (_) { s.dataAnalysis=null; }
        if(s.autoSession?.candidate_session_id){
          const record=await api('/candidate-sessions/'+u(s.autoSession.candidate_session_id));
          if(expected!==renderGeneration||local!==s||asset!==s.asset)return;
          s.candidateSession=record;
        }
        if(s.autoSession?.report){
          const ids=s.autoSession.report.quality_rows||[];
          if(!ids.some(x=>x.candidate_id===s.uiFocused))s.uiFocused=ids[0]?.candidate_id||null;
          s.uiDraftSelection=[...(s.autoSession.selection||[])];
        }
        s.autoSupervision=null;s.autoDelivery=null;
        if(s.autoSession){
          const sid=s.autoSession.session_id;
          const responses=await Promise.allSettled([
            api('/analysis/sessions/'+u(sid)+'/supervision-options'),
            api('/analysis/sessions/'+u(sid)+'/delivery')]);
          if(expected!==renderGeneration||local!==s||asset!==s.asset)return;
          if(responses[0].status==='fulfilled')s.autoSupervision=responses[0].value;
          if(responses[1].status==='fulfilled')s.autoDelivery=responses[1].value;
        }
      }

      if(s.tab==='advanced') { await window.TimePilotFeatureAdvanced?.load(s,api); }
      if(s.tab==='evaluate'){
        const setId=new URLSearchParams(location.search).get('set')||s.activeEvalSet?.set_id||s.sets[0]?.set_id;
        s.activeEvalSet=s.sets.find(x=>x.set_id===setId)||s.sets[0]||null;
        if(s.activeEvalSet){
          const latest=await api('/evaluations/sets/'+u(s.activeEvalSet.set_id)+'/latest');
          if(expected!==renderGeneration||local!==s)return;
          if(latest?.evaluation){
            s.evalResult=latest.evaluation;
            const reviewed=await api('/evaluations/'+u(latest.evaluation.evaluation_id)+'/selection');
            if(expected!==renderGeneration||local!==s)return;
            s.evalSelection=reviewed.selection;
          }
        }
      }
      // Candidate sessions are source/revision pinned; stale sessions remain visible
      // only to explain why regeneration is required.
      const [recentSession,fieldSuggestions]=await Promise.all([
        api('/candidate-sessions/latest?asset_id='+u(asset)+'&version_id='+u(version)),
        api('/field-suggestions?asset_id='+u(asset)+'&version_id='+u(version))]);
      if(expected!==renderGeneration || local!==s || asset!==s.asset)return;
      s.session=recentSession.session||null;s.fieldSuggestions=fieldSuggestions;
      if(s.session && (!s.quickSelectedFields || !s.quickSelectedFields.length))
        s.quickSelectedFields=s.session.selected_field_ids||[];
      if(s.session?.layout?.mode)s.workspaceMode=s.session.layout.mode;
      if(s.session?.layout?.chart_mode)s.chartMode=s.session.layout.chart_mode;
      if(s.session?.layout?.focus_candidate_id)s.focusCandidateId=s.session.layout.focus_candidate_id;
      if(s.session&&!s.session.stale){
        const recent=await api('/candidate-sessions/'+u(s.session.session_id)+'/latest-preview');
        if(expected!==renderGeneration || local!==s || asset!==s.asset)return;
        if(recent?.job?.status==='succeeded')s.candidatePreview=recent.job.result;
        else if(recent?.job&&['queued','running'].includes(recent.job.status))s.job=recent.job;
      }

      const profiles=await api('/profiles?asset_id='+u(asset)+'&version_id='+u(version));
      if(expected!==renderGeneration || local!==s || asset!==s.asset)return;
      const match=(profiles.items||[]).find(x=>x.source_sha256===contract.source_sha256 && x.contract_revision===contract.revision);
      if(match){s.profile=await api('/profiles/'+u(match.profile_id));s.profileId=match.profile_id;}
      else {s.profile=null;s.profileId=null;}
      const recent=await api('/profile-jobs?asset_id='+u(asset)+'&limit=20');
      if(expected!==renderGeneration || local!==s || asset!==s.asset)return;
      const current=(recent.items||[]).find(x=>['queued','running','cancel_requested'].includes(x.status));
      if(current)s.job=current;
      else if(s.job && s.job.payload?.asset_id !== s.asset)s.job=null;
      const lastPreview=(recent.items||[]).find(x=>x.kind==='candidate_preview'&&
         x.status==='succeeded'&&x.payload?.session_id===s.session?.session_id&&
         x.payload?.source_sha256===s.contract?.source_sha256);
      if(lastPreview?.result && !s.session?.stale){
        s.candidatePreview=lastPreview.result;
      }
    }
  }
  async function render(context,pid,tab='analysis'){
    const old=s;dispose();ctx=context;const thisGeneration=++renderGeneration;activeAbort=new AbortController();
    const query=new URLSearchParams(location.search);
    const asset=query.get('asset')||old?.asset||'';const version=query.get('version')||(old?.pid===pid&&old.asset===asset?old.version:'base')||'base';
    const sameContext=old?.pid===pid && old.asset===asset && (old.version||'base')===version;
    const cached=unsavedDrafts.get([pid,asset,version].join('|'));
    s={pid,tab:tabs.some(t=>t[0]===tab)?tab:'analysis',asset,version,
       assets:[],versions:[],contract:null,operators:null,sets:[],setAudits:sameContext?old.setAudits:{},steps:sameContext?old.steps:(cached?.steps||[]),
       candidateSession:sameContext?old.candidateSession:null,
       dataAnalysis:sameContext?old.dataAnalysis:null,
       uiFocused:query.get('candidate')|| (sameContext?old.uiFocused:null),
       uiAdvancedFocus:query.get('advanced_candidate')||(sameContext?old.uiAdvancedFocus:null),
       uiFamily:query.get('family')||(sameContext?old.uiFamily:'all'),
       uiAdvancedOpen:sameContext?!!old.uiAdvancedOpen:false,
       advancedBatches:sameContext?old.advancedBatches:null,
       advancedCapabilities:sameContext?old.advancedCapabilities:null,
       uiFilter:query.get('filter')|| (sameContext?old.uiFilter:'all'),
       uiChartMode:query.get('chart')||(sameContext?old.uiChartMode:'distribution'),
       uiChartEntity:query.get('entity')||(sameContext?old.uiChartEntity:'')||'',
       uiChartEntityField:query.get('entity_field')||(sameContext?old.uiChartEntityField:'')||'',
       uiChartResolution:[60,120,240].includes(Number(query.get('resolution')))?Number(query.get('resolution')):(sameContext?old.uiChartResolution:120)||120,
       uiChart:sameContext?old.uiChart:null,uiChartStatus:sameContext?old.uiChartStatus:null,
       uiChartError:sameContext?old.uiChartError:null,
       uiDraftSelection:sameContext?old.uiDraftSelection:null,
       uiSearchBudget:sameContext?old.uiSearchBudget:16,
       profile:sameContext?old.profile:null,job:sameContext?old.job:null,activeSet:sameContext?old.activeSet:null,
       runResult:sameContext?old.runResult:null,previewResult:sameContext?old.previewResult:null,evalPlan:sameContext?old.evalPlan:null,evalResult:sameContext?old.evalResult:null,evalSelection:sameContext?old.evalSelection:null,activeEvalSet:sameContext?old.activeEvalSet:null,
       assistantOpen:old?.assistantOpen||false,plan:sameContext?old.plan:null,notice:null,lineage:null,
       autoSession:sameContext?old.autoSession:null,timeBasis:sameContext?old.timeBasis:null,
       autoSupervision:sameContext?old.autoSupervision:null,autoDelivery:sameContext?old.autoDelivery:null,
       autoExplanation:sameContext?old.autoExplanation:null,
       modelTaskId:query.get('tp_model_task_id')||null,
       autoGoal:query.has('tp_goal')?(query.get('tp_goal')||'').slice(0,180):(sameContext?old.autoGoal:''),
       autoObjective:query.has('tp_task')?({'regression':'regression','classification':'classification'}[query.get('tp_task')]||'auto'):(sameContext?old.autoObjective:'auto'),
       autoTargetHint:query.has('tp_target')?(query.get('tp_target')||'').slice(0,150):(sameContext?old.autoTargetHint:''),
       autoSplitHint:query.has('tp_split')?(query.get('tp_split')||'').slice(0,20):(sameContext?old.autoSplitHint:''),
       autoFeatureHints:query.has('tp_features')?(()=>{
         try{const xs=JSON.parse(query.get('tp_features')||'[]');return Array.isArray(xs)?xs.filter(x=>typeof x==='string').slice(0,32):[];}
         catch{return [];}
       })():(sameContext?old.autoFeatureHints:[]),
       autoSetName:sameContext?old.autoSetName:'',
       session:sameContext?old.session:null,candidatePreview:sameContext?old.candidatePreview:null,
       fieldSuggestions:sameContext?old.fieldSuggestions:null,
       quickSelectedFields:sameContext?old.quickSelectedFields:null,
       quickTemplate:sameContext?old.quickTemplate:'basic',quickBudget:sameContext?old.quickBudget:30,
       focusCandidateId:sameContext?old.focusCandidateId:null,
       manualDraft:sameContext?old.manualDraft:(cached?.manualDraft||{}),workspaceMode:sameContext?old.workspaceMode:(cached?.workspaceMode||(tab==='build'&&query.get('tool')==='manual'?'manual':'quick')),chartMode:sameContext?old.chartMode:'trend',
       viewerOpen:sameContext?Boolean(old.viewerOpen):false,biProof:null,biError:null,advanced:sameContext?old.advanced:null,workflow:sameContext?old.workflow:null};
    // Query-string X/Y hints are untrusted. A saved v0.39.0 task is resolved
    // from the server and pinned to the current project, asset and version.
    if(s.modelTaskId){
      const targetState=s;
      s.autoGoal='';s.autoObjective='auto';s.autoTargetHint='';
      s.autoSplitHint='';s.autoFeatureHints=[];
      try{
        const bound=await ctx.req('/projects/'+u(pid)+'/model-tasks/'+u(s.modelTaskId));
        if(thisGeneration!==renderGeneration||targetState!==s)return;
        const spec=bound.spec||{};
        if(bound.source_state!=='current'||spec.asset_id!==asset||spec.version_id!==version||
           !['tabular_regression','tabular_classification'].includes(spec.task_kind)){
          throw new Error('Saved task is stale or does not match the current data version');
        }
        s.autoGoal=spec.business_goal||'';
        s.autoObjective=spec.task_kind==='tabular_regression'?'regression':'classification';
        s.autoTargetHint=spec.target?.column||'';
        s.autoFeatureHints=(spec.features||[]).map(x=>x.column).slice(0,32);
        s.autoSplitHint=({kfold:'kfold',stratified_kfold:'stratified',
            group_kfold:'group',temporal_holdout:'time'})[spec.validation?.kind]||'';
        s.modelTaskRevision=bound.revision;
        if(bound.status!=='approved')s.notice='Saved model configuration requires explicit role/availability/split approval before formal model execution.';
      }catch(err){
        s.notice='Saved model task could not be verified: '+String(err?.message||err);
      }
    }
    try{await refreshData(thisGeneration);if(thisGeneration!==renderGeneration)return;show();
      if(s.tab==='build'&&query.get('tool')==='manual'&&query.get('set')&&!query.get('auto_bi')){
        const found=s.sets.find(x=>x.set_id===query.get('set')&&x.asset_id===s.asset&&x.version_id===(s.version||'base'));
        if(found){
          s.activeSet=found;s.steps=[];s.runResult=null;s.previewResult=null;show();
          const selectedState=s;
          const last=await api('/sets/'+u(found.set_id)+'/latest-artifact');
          if(selectedState!==s||thisGeneration!==renderGeneration)return;
          if(last?.ready&&last.artifact)s.runResult={artifact:last.artifact};
          show();
        }else{s.notice='该特征集不属于当前数据版本，已阻止切换。';show();}
      }
      if(s.tab==='analysis'&&s.autoSession?.report&&!s.autoSession.stale&&!s.uiAdvancedFocus)loadAutoChart().catch(ex=>{if(thisGeneration===renderGeneration)note(ex.message);});
      const autoBi=query.get('auto_bi');
      if(s.tab==='build'&&autoBi){
        const setId=query.get('set'),autoSid=query.get('auto_sid');
        const found=s.sets.find(x=>x.set_id===setId);
        if(found&&found.asset_id===s.asset){
          try{
            let proof=null;
            if(autoSid){
              proof=await api('/analysis/sessions/'+u(autoSid)+'/delivery');
              if(!proof.artifact||proof.artifact.artifact_id!==autoBi||
                 proof.feature_set?.set_id!==setId||proof.source.asset_id!==s.asset||
                 proof.source.version_id!==(s.version||'base'))
                 throw Error('BI 请求与当前分析的正式产物不一致');
            }
            s.activeSet=found;s.viewerOpen=true;s.runResult={artifact:{artifact_id:autoBi}};
            show();
            if(!window.TimePilotFeaturePerspective?.mount)throw Error('Perspective 组件未加载');
            await window.TimePilotFeaturePerspective.mount({pid:s.pid,aid:s.asset,
              vid:s.version||'base',artifact:autoBi,setId,sha:s.contract.source_sha256,api});
            if(autoSid&&proof){
              const claim=await window.TimePilotFeaturePerspective.captureReceipt();
              const latest=await api('/analysis/sessions/'+u(autoSid));
              await api('/analysis/sessions/'+u(autoSid)+'/viewer-receipt',json('POST',{
                ...claim,expected_revision:latest.revision,artifact_id:autoBi,
                artifact_sha256:proof.artifact.sha256}));
            }
          }catch(ex){note('BI 产物核验或显示未完成：'+ex.message);}
        }
      }

      if(s.job && ['queued','running','cancel_requested'].includes(s.job.status))refreshJob().catch(ex=>note(ex.message));
      if(s.plan?.status==='awaiting_job')refreshPlan().catch(ex=>note(ex.message));
      if(s.tab==='analysis')refreshAuto().catch(ex=>note(ex.message));
    }
    catch(ex){if(thisGeneration!==renderGeneration || ex.name==='AbortError')return;s.notice='载入特征工程失败：'+String(ex.message);show();}
  }
  async function openIntegratedBi(){
    const state=s;
    if(!state||state.tab!=='bi'||!state.asset||!state.contract)return;
    state.biError=null;state.biProof=null;
    try{
      const recent=await api('/analysis/latest?asset_id='+u(state.asset)+'&version_id='+u(state.version||'base'));
      if(state!==s||state.tab!=='bi')return;
      const session=recent?.session;
      if(session&&!session.stale&&session.artifact?.artifact_id){
        const proof=await api('/analysis/sessions/'+u(session.session_id)+'/delivery');
        if(state!==s||state.tab!=='bi')return;
        const set=state.sets.find(x=>x.set_id===proof?.feature_set?.set_id);
        if(proof?.artifact && set && proof.source?.asset_id===state.asset &&
           proof.source?.version_id===(state.version||'base') &&
           proof.source?.sha256===state.contract.source_sha256 &&
           proof.source?.contract_revision===state.contract.revision &&
           proof.artifact.artifact_id===session.artifact.artifact_id &&
           proof.artifact.sha256===session.artifact.sha256 &&
           !['blocked','stale'].includes(proof.status)){
          state.biProof=proof;state.autoSession=session;state.activeSet=set;
          state.runResult={artifact:proof.artifact};
        }
      }
      state.viewerOpen=true;show();
      await window.TimePilotFeaturePerspective.mount({pid:state.pid,aid:state.asset,
        vid:state.version||'base',artifact:state.biProof?.artifact?.artifact_id||null,
        setId:state.biProof?.feature_set?.set_id||null,
        sha:state.contract.source_sha256,api});
    }catch(error){
      if(state!==s)return;
      state.biError='Perspective 加载或来源校验失败：'+String(error.message||error);
      show();
    }
  }
  function modal(title,body,onAccept){
    const host=document.getElementById('tp-fl2-dialog-host');if(!host)return;
    host.innerHTML=`<div class="tp-fl2-overlay"><section class="tp-fl2-dialog" role="dialog" aria-modal="true" aria-label="${e(title)}"><h2>${e(title)}</h2><form id="tp-fl2-dialog-form">${body}<div class="action-row"><button class="button" type="button" data-feature-v2="close-modal">取消</button><button class="button button-primary" type="submit">确认</button></div></form></section></div>`;
    dialog=onAccept;host.querySelector('input,select,textarea,button')?.focus();
  }
  function editableContract(){
    if(!s.contract)return;
    const options=['unspecified','entity_key','event_time','available_at','ingested_at',
      'prediction_time','value','regime','label','batch_key','record_id','order_index','elapsed_time'];
    const html=`<div class="tp-fl2-contract-table">${s.contract.fields.map((f,i)=>{
      const realTime=/DATE|TIMESTAMP/i.test(f.physical_type);
      const roleChoices=options.filter(o=>realTime||!['event_time','available_at','ingested_at','prediction_time'].includes(o));
      const semantics=['unknown','numeric','string','category','id','boolean','event','target',...(realTime?['datetime']:[])];
      return `<div class="tp-fl2-field-row" data-fid="${e(f.field_id)}"><strong>${e(f.source_name)}</strong><small>${e(f.physical_type)}</small>
       <input name="alias-${i}" value="${e(f.display_name||'')}" placeholder="\u4e1a\u52a1\u540d\u79f0"><select name="role-${i}">${roleChoices.map(o=>`<option value="${o}" ${o===f.role?'selected':''}>${o}</option>`).join('')}</select>
       <select name="semantic-${i}">${semantics.map(t=>`<option value="${t}" ${f.semantic_type===t?'selected':''}>${t}</option>`).join('')}</select></div>`;}).join('')}</div>
    <label class="field"><span>\u771f\u5b9e\u65f6\u95f4\u5b57\u6bb5\u7684\u65f6\u533a</span><input name="timezone" value="${e(s.contract.timezone||'')}" placeholder="Asia/Shanghai"></label>
    <p class="muted small">\u6570\u503c Time \u53ef\u786e\u8ba4\u4e3a order_index\uff0c\u65e0\u9700\u53d8\u6210 datetime\u3002\u65e5\u5386\u65f6\u95f4\u9700\u8981\u771f\u5b9e\u65f6\u95f4\u7c7b\u578b\u548c\u65f6\u533a\u3002</p>`;
    modal('核实字段含义',html,async fd=>{
      const fields=s.contract.fields.map((f,i)=>({field_id:f.field_id,display_name:String(fd.get('alias-'+i)||'').trim()||null,
          role:fd.get('role-'+i),semantic_type:fd.get('semantic-'+i),confirmation:'confirmed'}));
      const role=k=>fields.find(x=>x.role===k)?.field_id||null;
      const patch={fields,entity_field_ids:fields.filter(x=>x.role==='entity_key').map(x=>x.field_id),
          event_time_field_id:role('event_time'),available_at_field_id:role('available_at'),
          timezone:String(fd.get('timezone')||'').trim()||null};
      const updated=await api('/contracts/'+u(s.asset),json('PATCH',{patch,expected_revision:s.contract.revision}));
      s.contract=await api('/contracts/'+u(s.asset)+'/confirm',json('POST',{expected_revision:updated.revision}));
      if(s.session)s.session={...s.session,stale:true};
      s.candidatePreview=null;
      s.fieldSuggestions=await api('/field-suggestions?asset_id='+u(s.asset)+'&version_id='+u(s.version||'base'));
      s.operators=await api('/operators?asset_id='+u(s.asset)+'&version_id='+u(s.version||'base'));
      s.notice='字段契约已保存，旧候选需要重新生成。';show();
    });
  }
  function writeViewURL(push=false){
    if(!s||s.tab!=='analysis')return;
    const uri=new URL(location.href);
    for(const key of ['candidate','advanced_candidate','filter','family','chart','entity','entity_field','resolution'])uri.searchParams.delete(key);
    if(s.uiFocused)uri.searchParams.set('candidate',s.uiFocused);
    if(s.uiAdvancedFocus)uri.searchParams.set('advanced_candidate',s.uiAdvancedFocus);
    if(s.uiFamily&&s.uiFamily!=='all')uri.searchParams.set('family',s.uiFamily);
    if(s.uiFilter&&s.uiFilter!=='all')uri.searchParams.set('filter',s.uiFilter);
    if(s.uiChartMode&&s.uiChartMode!=='distribution')uri.searchParams.set('chart',s.uiChartMode);
    if(s.uiChartEntity)uri.searchParams.set('entity',s.uiChartEntity);
    if(s.uiChartEntityField)uri.searchParams.set('entity_field',s.uiChartEntityField);
    if(s.uiChartResolution&&s.uiChartResolution!==120)uri.searchParams.set('resolution',s.uiChartResolution);
    const target=uri.pathname+uri.search;
    if(target!==location.pathname+location.search){
      if(push)history.pushState(history.state,'',target);
      else history.replaceState(history.state,'',target);
    }
  }
  async function loadAutoChart(){
    const local=s,epoch=renderGeneration,seq=++chartGeneration;
    const sid=local?.autoSession?.session_id;
    const first=local?.autoSession?.report?.quality_rows?.[0]?.candidate_id;
    const cid=local?.uiFocused||first;
    if(!sid||!cid||local.autoSession.stale)return;
    local.uiFocused=cid;local.uiChartStatus='loading';local.uiChartError=null;show();
    try{
      const chart=await api('/analysis/sessions/'+u(sid)+'/candidates/'+u(cid)+'/chart?mode='+u(local.uiChartMode||'distribution')+'&resolution='+u(local.uiChartResolution||120)+(local.uiChartEntity?'&entity_value='+u(local.uiChartEntity):'')+(local.uiChartEntityField?'&entity_field_id='+u(local.uiChartEntityField):''));
      if(epoch!==renderGeneration||local!==s||seq!==chartGeneration||sid!==s.autoSession?.session_id||cid!==s.uiFocused)return;
      local.uiChart=chart;local.uiChartStatus='ready';
    }catch(ex){
      if(epoch!==renderGeneration||local!==s||seq!==chartGeneration)return;
      local.uiChartStatus='error';local.uiChartError=ex.message;local.uiChart=null;
    }
    show();
  }
  async function refreshAuto(){
    const old=s,epoch=renderGeneration;
    clearTimeout(autoTimer);autoTimer=null;
    if(!s?.autoSession||s.autoSession.stale)return;
    if(!['profiling','previewing','materializing','evaluating'].includes(s.autoSession.status))return;
    try{
      const sid=s.autoSession.session_id;
      const next=await api('/analysis/sessions/'+u(sid));
      if(epoch!==renderGeneration||old!==s||s.tab!=='analysis'||sid!==s.autoSession?.session_id)return;
      s.autoSession=next;
      if(next.time_fields?.length&&s.timeBasis)s.timeBasis={...s.timeBasis,fields:next.time_fields};
      if(next.status==='ready'){
        const responses=await Promise.allSettled([
          api('/analysis/sessions/'+u(sid)+'/supervision-options'),
          api('/analysis/sessions/'+u(sid)+'/delivery')]);
        if(epoch!==renderGeneration||old!==s||sid!==s.autoSession?.session_id)return;
        if(responses[0].status==='fulfilled')s.autoSupervision=responses[0].value;
        if(responses[1].status==='fulfilled')s.autoDelivery=responses[1].value;
      }
      if(next.report){
        if(!s.uiFocused)s.uiFocused=next.report.quality_rows?.[0]?.candidate_id||null;
        s.uiDraftSelection=[...(next.selection||[])];
        if(next.candidate_session_id&&!s.candidateSession){
          s.candidateSession=await api('/candidate-sessions/'+u(next.candidate_session_id));
          if(epoch!==renderGeneration||old!==s)return;
        }
      }
      show();
      if(next.status==='ready'&&next.report&&!s.uiChart)loadAutoChart().catch(ex=>note(ex.message));
      if(['profiling','previewing','materializing','evaluating'].includes(next.status)){
        autoTimer=setTimeout(()=>{if(document.getElementById('tp-feature-lab'))refreshAuto().catch(ex=>note(ex.message));},1800);
      }
    }catch(ex){if(epoch===renderGeneration&&old===s)note('\u5206\u6790\u4efb\u52a1\u672a\u80fd\u7ee7\u7eed\uff1a'+ex.message);}
  }
  async function refreshJob(){
    if(!s?.job)return;
    const context=s,jobId=s.job.job_id,expected=renderGeneration;
    const result=await api('/runs/'+u(jobId));
    if(context!==s || expected!==renderGeneration || !s.job || s.job.job_id!==jobId)return;
    s.job=result;
    if(result.status==='succeeded'){
      if(result.kind==='profile'){s.profileId=result.result.profile_id;s.profile=await api('/profiles/'+u(result.result.profile_id));}
      if(result.kind==='materialize'){s.runResult=await api('/runs/'+u(result.job_id)+'/result?preview_rows=0');}
      if(result.kind==='preview'){s.previewResult=result.result;}
      if(result.kind==='candidate_preview'){s.candidatePreview=result.result;}
      if(result.kind==='evaluation'){s.evalResult=await api('/evaluations/'+u(result.job_id));
        s.evalSelection=(await api('/evaluations/'+u(result.job_id)+'/selection')).selection;}
    }
    show();
    clearTimeout(pollTimer);
    if(['queued','running','cancel_requested'].includes(result.status))
      pollTimer=setTimeout(()=>{if(document.getElementById('tp-feature-lab'))refreshJob().catch(ex=>note(ex.message));},1500);
  }
  async function refreshPlan(){
    if(!s?.plan || s.plan.status!=='awaiting_job')return;
    const context=s,expected=renderGeneration,planId=s.plan.plan_id;
    clearTimeout(planTimer);
    try{
      const next=await api('/agent/plans/'+u(planId)+'/execute',{method:'POST'});
      if(context!==s || expected!==renderGeneration || s.plan?.plan_id!==planId)return;
      s.plan=next;show();
      if(s.plan.status==='awaiting_job')
        planTimer=setTimeout(()=>{
          if(document.getElementById('tp-feature-lab'))refreshPlan().catch(ex=>note(ex.message));
        },2200);
    }catch(ex){note('任务状态读取失败：'+ex.message);}
  }
  async function click(event){
    const advanced=event.target.closest('[data-feature-advanced]');
    if(advanced){event.preventDefault();try{
       await window.TimePilotFeatureAdvanced.click(s,api,advanced,show);
       if(s?.tab==='analysis')await refreshBatches();
     }catch(ex){note('操作失败：'+ex.message);}return true;}
    const el=event.target.closest('[data-feature-v2]');if(!el)return false;
    event.preventDefault();const act=el.dataset.featureV2;
    try{
      if(act==='wb-focus'){if(s.uiFocused===el.dataset.cid&&!s.uiAdvancedFocus)return true;s.uiAdvancedFocus=null;s.uiFocused=el.dataset.cid;s.uiChart=null;s.uiChartStatus=null;writeViewURL(true);await loadAutoChart();return true;}
      if(act==='wb-advanced-panel'){
        s.uiAdvancedOpen=!s.uiAdvancedOpen;
        if(s.uiAdvancedOpen){
          await window.TimePilotFeatureAdvanced?.load?.(s,api);
        }
        show();return true;
      }
      if(act==='wb-advanced-refresh'){
        await refreshBatches();return true;
      }
      if(act==='wb-advanced-focus'){
        s.uiAdvancedFocus=el.dataset.cid;s.uiFocused=null;s.uiChart=null;s.uiChartStatus=null;
        writeViewURL(true);show();return true;
      }
      if(act==='wb-family'){
        const key=el.dataset.family||'all';
        const valid=new Set(['all','advanced',...[...document.querySelectorAll('[data-feature-v2="wb-family"]')].map(x=>x.dataset.family)]);
        if(!valid.has(key))throw Error('未知的特征类型');
        s.uiFamily=key;s.uiAdvancedFocus=null;
        const subset=(s.autoSession?.report?.quality_rows||[]).filter(x=>
          key==='all'||(s.candidateSession?.candidates||[]).find(c=>c.candidate_id===x.candidate_id)?.family===key);
        if(!subset.some(x=>x.candidate_id===s.uiFocused)){
          s.uiFocused=subset[0]?.candidate_id||null;s.uiChart=null;s.uiChartStatus=null;
        }
        writeViewURL(true);show();
        if(s.uiFocused&&!s.uiChart)await loadAutoChart();
        return true;
      }
      if(act==='wb-filter'){
        s.uiAdvancedFocus=null;
        s.uiFilter=el.dataset.filter||'all';
        const rows=s.autoSession?.report?.quality_rows||[];
        const chosen=s.uiDraftSelection||s.autoSession?.selection||[];
        const visible=rows.filter(x=>s.uiFilter==='all'||x.status===s.uiFilter||
          s.uiFilter==='selected'&&chosen.includes(x.candidate_id));
        const focus=visible.find(x=>x.candidate_id===s.uiFocused)||visible[0]||null;
        if((focus?.candidate_id||null)!==s.uiFocused){
          s.uiFocused=focus?.candidate_id||null;s.uiChart=null;s.uiChartStatus=null;
        }
        writeViewURL(true);show();
        if(s.uiFocused&&!s.uiChart)await loadAutoChart();
        return true;
      }
      if(act==='wb-chart'){s.uiChartMode=el.dataset.chart||'distribution';writeViewURL(true);await loadAutoChart();return true;}
      if(act==='auto-preset'){
        s.autoGoal=document.querySelector('#tp-fl2-auto-form textarea[name="goal"]')?.value||s.autoGoal;
        s.autoObjective=el.dataset.objective||'auto';show();
        const input=document.querySelector('#tp-fl2-auto-form textarea[name="goal"]');
        if(input)input.focus();
      }
      if(act==='auto-refresh'){await refreshAuto();show();}
      if(act==='auto-retry'){
        if(!s.autoSession || s.autoSession.stale)throw Error('数据版本已变化，请重新分析');
        const session=s.autoSession;
        s.autoSession=await api('/analysis/sessions/'+u(session.session_id)+'/retry',
          json('POST',{expected_revision:session.revision}));
        s.notice='已重新排队失败阶段；此前完成的阶段结果会保留。';
        show();refreshAuto();
      }
      if(act==='accept-time-order'){
        const row=(s.timeBasis?.fields||[]).find(x=>x.field_id===el.dataset.field);
        if(!row||row.kind!=='sequence_order_candidate')throw Error('Only a suggested sequence index can be confirmed here');
        const changed=await api('/time-basis/accept',json('POST',{asset_id:s.asset,version_id:s.version||'base',
             expected_revision:s.contract.revision,field_id:row.field_id,role:'order_index'}));
        s.contract=changed;s.autoSession=null;s.timeBasis=await api('/time-basis/infer?asset_id='+u(s.asset));
        s.notice='\u5df2\u786e\u8ba4\u8bb0\u5f55\u987a\u5e8f\uff0c\u4f46\u4e0d\u4f1a\u5c06\u5b83\u5192\u5145\u4e3a\u65e5\u671f\u65f6\u95f4\u3002';show();
      }
      if(act==='auto-materialize'){
        if(!s.autoSession?.feature_set_id)throw Error('Save the selected feature set first');
        s.autoSession=await api('/analysis/sessions/'+u(s.autoSession.session_id)+'/materialize',json('POST',{request_id:id()}));
        show();refreshAuto();
      }
      if(act==='auto-explain'){
        if(!s.autoSession?.report)throw Error('No verified result yet');
        s.autoExplanation=await api('/analysis/sessions/'+u(s.autoSession.session_id)+'/explain',{method:'POST'});
        show();
      }
      if(act==='archive-report'){
        if(!s.autoSession?.report || s.autoSession.stale)throw Error('当前分析尚不可归档');
        const sid=s.autoSession.session_id;
        const data=await api('/analysis/sessions/'+u(sid)+'/reports',{
          method:'POST',headers:{'Content-Type':'application/json'},
          body:JSON.stringify({expected_revision:s.autoSession.revision})});
        note('报告已保存为不可变版本：'+data.report_id.slice(0,8)+' · SHA256 '+data.report_sha256.slice(0,12));
        return;
      }
      if(act==='auto-report'){
        if(!s.autoSession?.report)throw Error('No report yet');
        const doc=await api('/analysis/sessions/'+u(s.autoSession.session_id)+'/report');
        const blob=new Blob([doc.markdown],{type:'text/markdown;charset=utf-8'});
        const href=URL.createObjectURL(blob);const a=document.createElement('a');
        a.href=href;a.download='TimePilot_feature_analysis_'+s.autoSession.session_id.slice(0,8)+'.md';a.click();
        setTimeout(()=>URL.revokeObjectURL(href),2000);
      }
      if(act==='auto-export-evidence'){
        if(!s.autoSession?.artifact?.artifact_id)throw Error('请先完成正式物化');
        const doc=await api('/analysis/sessions/'+u(s.autoSession.session_id)+'/report');
        const proof=doc.delivery||{};
        if(!proof.artifact||proof.artifact.sha256!==s.autoSession.artifact.sha256)
          throw Error('导出的核验清单与当前正式产物不一致');
        const evidence={schema_version:1,session_id:s.autoSession.session_id,
          report_sha256:doc.report_sha256,report_encoding:doc.report_encoding,
          source_sha256:doc.source_sha256,delivery:proof};
        const blob=new Blob([JSON.stringify(evidence,null,2)+'\n'],{type:'application/json;charset=utf-8'});
        const href=URL.createObjectURL(blob),a=document.createElement('a');
        a.href=href;a.download='TimePilot_feature_evidence_'+s.autoSession.session_id.slice(0,8)+'.json';a.click();
        setTimeout(()=>URL.revokeObjectURL(href),2000);
      }
      if(act==='auto-open-bi'){
        if(!s.autoSession?.artifact?.artifact_id)throw Error('Finish full materialization first');
        const proof=await api('/analysis/sessions/'+u(s.autoSession.session_id)+'/delivery');
        if(!proof.artifact||proof.artifact.artifact_id!==s.autoSession.artifact.artifact_id||
           proof.artifact.sha256!==s.autoSession.artifact.sha256||
           proof.source?.asset_id!==s.asset||proof.source?.version_id!==(s.version||'base')||
           proof.feature_set?.set_id!==s.autoSession.feature_set_id)
           throw Error('物化产物尚未通过同源与 SHA256 校验');
        s.autoDelivery=proof;
        const uri='/projects/'+u(s.pid)+'/features/build?asset='+u(s.asset)+
                  '&version='+u(s.version||'base')+'&auto_bi='+u(proof.artifact.artifact_id)+
                  '&set='+u(s.autoSession.feature_set_id)+'&auto_sid='+u(s.autoSession.session_id);
        // Keep the immutable artifact, FeatureSet and analysis-session IDs in the URL.
        // Switching only the embedded BI tab dropped these IDs on refresh.
        ctx.navigate(uri);
      }
      if(act==='dismiss-notice'){s.notice=null;show();}
      if(act==='toggle-assistant'){s.assistantOpen=!s.assistantOpen;show();}
      if(act==='insert-prompt'){const t=document.getElementById('tp-fl2-message');if(t){t.value=el.dataset.prompt;t.focus();}}
      if(act==='edit-contract')editableContract();
      if(act==='close-modal'){document.getElementById('tp-fl2-dialog-host').innerHTML='';dialog=null;}
      if(act==='help')modal('FeatureLab 使用说明',`<p>第一步：选择数据并描述要分析的问题；第二步：检查系统识别的数据类型、记录顺序和设备分组；第三步：开始自动分析并审阅候选质量；第四步：确认后保存特征集、正式物化和模型评价；第五步：使用 BI 查看结果并导出可追溯报告。需要时从高级工具进入手工算子。</p><p class="muted">没有真实时间字段时，窗口只按记录条数计算。完整计算与预览有不同的数据范围。</p>`,async()=>{});
      if(act==='replan-plan'&&s.plan){
        modal('调整分析要求',`<p>请补充缺少的字段、时间信息或其它限制。已完成的计算不会自动重做。</p>
           <label class="field"><span>补充要求</span><textarea name="feedback" maxlength="500" rows="3" required placeholder="例如：使用 A2 字段，按最近5条记录进行计算"></textarea></label>`,
          async fd=>{
            s.plan=await api('/agent/plans/'+u(s.plan.plan_id)+'/replan',json('POST',
              {feedback:String(fd.get('feedback')||'')}));show();
          });
      }
      if(act==='derive-evaluation-set'){
        if(!s.evalSelection?.revision||!s.evalResult?.evaluation_id)throw Error('请先保存筛选决策');
        modal('新建筛选特征集草稿',`<p>根据保存的筛选决策创建新草稿。不会继承旧物化结果或审批状态；使用前需重新计算。</p>
             <label class="field"><span>新特征集名称</span><input name="name" maxlength="120" value="${e((s.activeEvalSet?.name||'特征集')+' · 筛选版')}" required></label>`,async fd=>{
          const record=await api('/evaluations/'+u(s.evalResult.evaluation_id)+'/derive-set',json('POST',{
            selection_revision:s.evalSelection.revision,request_id:id(),name:String(fd.get('name')||'').trim()}));
          s.sets=await api('/sets?asset_id='+u(s.asset));s.activeSet=record;
          note('已生成新草稿，请前往特征资产执行完整计算和审核。');show();
        });
      }
      if(act==='run-profile'){s.job=await api('/profile-jobs',json('POST',{asset_id:s.asset,version_id:s.version||'base',request_id:id()}));show();refreshJob();}
      if(act==='refresh-job')await refreshJob();
      if(act==='mode-quick'||act==='mode-manual'||act==='mode-custom'){
        s.workspaceMode=act==='mode-quick'?'quick':act==='mode-custom'?'custom':'manual';
        if(s.session&&!s.session.stale){
          s.session=await api('/candidate-sessions/'+u(s.session.session_id),json('PATCH',{
            expected_revision:s.session.revision,layout:{...s.session.layout,mode:s.workspaceMode}}));
        }
        show();
      }
      if(['chart-trend','chart-distribution','chart-scatter'].includes(act)){
        s.chartMode=act.replace('chart-','');
        if(s.session&&!s.session.stale){
          s.session=await api('/candidate-sessions/'+u(s.session.session_id),json('PATCH',{
            expected_revision:s.session.revision,layout:{...s.session.layout,mode:s.workspaceMode,
              chart_mode:s.chartMode}}));
        }
        show();
      }
      if(act==='focus-candidate'){
        s.focusCandidateId=el.dataset.cid;
        if(s.session&&!s.session.stale){
          s.session=await api('/candidate-sessions/'+u(s.session.session_id),json('PATCH',{
            expected_revision:s.session.revision,layout:{...s.session.layout,mode:s.workspaceMode,
              focus_candidate_id:s.focusCandidateId}}));
        }
        show();
      }
      if(act==='candidate-all'||act==='candidate-none'){
        if(!s.session||s.session.stale)throw Error('请先重新生成候选');
        const ids=act==='candidate-none'?[]:s.session.candidates.map(x=>x.candidate_id);
        s.session=await api('/candidate-sessions/'+u(s.session.session_id),json('PATCH',{
          expected_revision:s.session.revision,selected_candidate_ids:ids}));
        s.candidatePreview=null;show();
      }
      if(act==='preview-candidates'){
        if(!s.session||s.session.stale)throw Error('请先生成有效候选');
        const ids=s.session.selected_candidate_ids.slice(0,8);
        if(!ids.length)throw Error('请选择至少一个候选');
        s.job=await api('/candidate-sessions/'+u(s.session.session_id)+'/preview-jobs',json('POST',{
          request_id:id(),candidate_ids:ids,max_rows:30,scan_budget:200000}));
        s.candidatePreview=null;show();refreshJob();
      }
      if(act==='remove-step'){s.steps.splice(Number(el.dataset.index),1);show();}
      if(act==='refresh-sets'){
        const [sets,audits]=await Promise.all([
          api('/sets?asset_id='+u(s.asset)),api('/sets/temporal-audits?asset_id='+u(s.asset))]);
        s.sets=sets;s.setAudits=Object.fromEntries((audits.items||[]).map(a=>[a.set_id,a]));show();}
      if(act==='open-set'){
        const found=s.sets.find(x=>x.set_id===el.dataset.sid);
        if(!found)throw Error('特征集不存在');
        s.activeSet=found;s.steps=[];s.runResult=null;s.previewResult=null;
        const last=await api('/sets/'+u(found.set_id)+'/latest-artifact');
        if(last?.ready&&last.artifact)s.runResult={artifact:last.artifact};
        show();
      }
      if(act==='toggle-viewer'){s.viewerOpen=!s.viewerOpen;show();}
      if(act==='open-viewer'||act==='compare-viewer'){
        s.viewerOpen=true;show();
        const src=s.contract;
        if(!src)throw Error('请先选择数据');
        const artifact=act==='compare-viewer'?s.runResult?.artifact?.artifact_id:null;
        if(act==='compare-viewer'&&!artifact)throw Error('请先运行完整特征计算');
        await window.TimePilotFeaturePerspective.mount({pid:s.pid,aid:s.asset,vid:s.version||'base',
            artifact,setId:s.activeSet?.set_id,sha:src.source_sha256,api});
      }
      if(act==='bi-verified-compare'){
        const proof=s.biProof;
        if(s.tab!=='bi'||!proof?.artifact||!s.activeSet||
           proof.source?.asset_id!==s.asset||proof.source?.version_id!==(s.version||'base')||
           proof.source?.sha256!==s.contract?.source_sha256||
           proof.feature_set?.set_id!==s.activeSet.set_id)
          throw Error('当前版本没有通过同源校验的正式特征产物');
        await window.TimePilotFeaturePerspective.mount({pid:s.pid,aid:s.asset,
          vid:s.version||'base',artifact:proof.artifact.artifact_id,
          setId:s.activeSet.set_id,sha:s.contract.source_sha256,api});
      }
      if(act==='bi-receipt'){
        const proof=s.biProof;
        if(s.tab!=='bi'||!proof?.artifact||!s.autoSession||
           proof.artifact.sha256!==s.autoSession.artifact?.sha256)
          throw Error('需要经过验证的正式特征及真实双面板图表');
        const receipt=await window.TimePilotFeaturePerspective.captureReceipt();
        const latest=await api('/analysis/sessions/'+u(s.autoSession.session_id));
        const saved=await api('/analysis/sessions/'+u(s.autoSession.session_id)+'/viewer-receipt',
          json('POST',{...receipt,expected_revision:latest.revision,
            artifact_id:proof.artifact.artifact_id,artifact_sha256:proof.artifact.sha256}));
        s.autoSession=saved;
        s.biProof=await api('/analysis/sessions/'+u(s.autoSession.session_id)+'/delivery');
        s.notice='已记录当前同源 Perspective 双面板回执；生产验证仍需独立复核数值。';
        // Do not re-render here: disconnecting the native Viewer loses layout.
        const status=document.getElementById('tp-fl2-viewer-status');
        if(status)status.textContent=s.notice;
      }
      if(act==='save-viewer')await window.TimePilotFeaturePerspective.save(api);
      if(act==='render-agent-result'&&s.plan?.status==='awaiting_viewer'){
        const current=s.plan;
        const step=(current.steps||[]).find(x=>x.status==='awaiting_viewer'&&x.tool_id==='feature.viewer.open');
        const result=step?.result;
        if(!result?.artifact_id||!result.render_request_id)throw Error('服务端未签发有效的图表渲染请求');
        if(s.tab!=='build'){
          ctx.navigate('/projects/'+u(s.pid)+'/features/build?asset='+u(s.asset)+'&version='+u(s.version||'base'));
          return true;
        }
        const selected=s.sets.find(x=>x.set_id===result.set_id);
        if(!selected||selected.asset_id!==s.asset||selected.version_id!==(s.version||'base'))
          throw Error('当前特征集与图表来源不一致，请重新载入');
        s.activeSet=selected;
        await window.TimePilotFeaturePerspective.mount({pid:s.pid,aid:s.asset,vid:s.version,
            artifact:result.artifact_id,setId:result.set_id,sha:result.source_sha256,api});
        const receipt=await window.TimePilotFeaturePerspective.captureReceipt();
        s.plan=await api('/agent/plans/'+u(current.plan_id)+'/render-receipt',json('POST',{
            render_request_id:result.render_request_id,artifact_id:result.artifact_id,...receipt}));
        s.notice='已核对真实特征结果和 Perspective 显示回执。';show();
      }
      if(act==='preview-features'){
        s.job=await api('/sets/'+u(el.dataset.sid)+'/preview-jobs',json('POST',
          {request_id:id(),max_rows:50,scan_budget:500000}));
        s.previewResult=null;show();refreshJob();
      }
      if(act==='run-features'){s.job=await api('/sets/'+u(el.dataset.sid)+'/runs',json('POST',{request_id:id()}));s.runResult=null;show();refreshJob();}
      if(act==='show-lineage'){
        const [lineage,audit]=await Promise.all([
          api('/sets/'+u(el.dataset.sid)+'/lineage'),
          api('/sets/'+u(el.dataset.sid)+'/temporal-audit')]);
        s.lineage={...lineage,temporal_audit:audit};show();}
      if(act==='request-review'){s.sets=await Promise.resolve(api('/sets/'+u(el.dataset.sid)+'/request-review',{method:'POST'})).then(()=>api('/sets?asset_id='+u(s.asset)));show();}
      if(act==='approve')modal('审核特征集',`<p>请核查来源、特征公式、时间可用性及当前数据状态。</p><label class="field"><span>审核意见</span><textarea name="reason" required rows="3"></textarea></label>`,async fd=>{
        await api('/sets/'+u(el.dataset.sid)+'/approve',json('POST',{confirm:true,reason:String(fd.get('reason')||'')}));
        s.sets=await api('/sets?asset_id='+u(s.asset));show();});
      if(act==='publish'){
        const pre=await api('/sets/'+u(el.dataset.sid)+'/publication-preview');
        if(!pre.artifact_ready)throw Error('请先完成全量特征计算并核验结果。');
        modal('发布特征资产',`<p>已审核来源 ${e(pre.source_sha256.slice(0,16))}。发布后形成独立不可变数据资产，原始数据不改变。</p><label><input type="checkbox" name="confirm" value="yes" required> 我确认该特征集可用于正式用途</label>`,async fd=>{
          if(fd.get('confirm')!=='yes')throw Error('发布需要明确确认');
          await api('/sets/'+u(el.dataset.sid)+'/publish',json('POST',{confirm:true,reason:'用户确认发布'}));
          s.sets=await api('/sets?asset_id='+u(s.asset));show();});
      }
      if(act==='run-eval'&&s.evalPlan){s.job=await api('/evaluations/plans/'+u(s.evalPlan.plan_id)+'/run',json('POST',{request_id:id()}));show();refreshJob();}
      if((act==='execute-plan'||act==='resume-plan')&&s.plan){
        s.plan=await api('/agent/plans/'+u(s.plan.plan_id)+'/execute',{method:'POST'});
        show();if(s.plan.status==='awaiting_job')refreshPlan();
      }
    }catch(ex){note('操作未完成：'+ex.message);}
    return true;
  }
  async function submit(event){
    if(['tp-fl2-advanced-form','tp-adv-join-form','tp-adv-eval-form'].includes(event.target?.id)){
      event.preventDefault();await window.TimePilotFeatureAdvanced.submit(s,api,event.target,show);return true;
    }
    if(!event.target?.id?.startsWith('tp-fl2-'))return false;
    event.preventDefault();const form=event.target;const data=new FormData(form);
    try{
      if(form.id==='tp-fl2-auto-form'){
        s.autoGoal=String(data.get('goal')||'').trim();
        s.autoObjective=String(data.get('task_hint')||'auto');s.autoExplanation=null;
        s.autoSession=await api('/analysis/sessions',json('POST',{
          asset_id:s.asset,version_id:s.version||'base',goal:s.autoGoal,
          task_hint:s.autoObjective,budget:Number(data.get('budget')||16),request_id:id()}));
        s.autoSupervision=null;s.autoDelivery=null;s.candidateSession=null;s.uiChart=null;
        s.uiAdvancedFocus=null;s.uiFamily='all';
        s.uiDraftSelection=null;s.uiFocused=null;s.uiChartStatus=null;chartGeneration++;
        writeViewURL();show();refreshAuto();return true;
      }
      if(form.id==='tp-fl2-auto-select'){
        if(!s.autoSession?.report)throw Error('No analysis report yet');
        s.autoSession=await api('/analysis/sessions/'+u(s.autoSession.session_id)+'/selection',json('PUT',{
          expected_revision:s.autoSession.revision,
          candidate_ids:[...new Set(s.uiDraftSelection||s.autoSession.selection||[])]}));
        s.uiDraftSelection=[...(s.autoSession.selection||[])];
        s.notice='选择已保存，可以继续生成特征集。';show();return true;
      }
      if(form.id==='tp-fl2-auto-save'){
        if(!s.autoSession?.report)throw Error('No analysis report yet');
        const name=String(data.get('name')||'').trim();s.autoSetName=name;
        let supervision=null;
        if(data.get('enable_supervised')==='yes'){
          const split=String(data.get('split_kind')||'time');
          const staticSplit=['kfold','stratified','group'].includes(split);
          if(staticSplit?!s.autoSupervision?.static_eligible:!(s.autoSupervision?.temporal_eligible??s.autoSupervision?.eligible))
            throw Error('所选监督验证方式缺少已确认的数据契约');
          supervision={confirm:true,
            label_field_id:String(data.get('label_field_id')||''),
            prediction_time_field_id:staticSplit?null:String(data.get('prediction_time_field_id')||''),
            label_available_at_field_id:staticSplit?null:String(data.get('label_available_at_field_id')||''),
            split_kind:split,group_field_ids:[],
            static_independence_confirmed:staticSplit&&data.get('static_independence_confirmed')==='yes'};
          if(!supervision.label_field_id)throw Error('请确认监督任务标签字段');
          if(staticSplit){
            if(!supervision.static_independence_confirmed)throw Error('静态任务需要确认样本独立性与特征决策时可用性');
            if(split==='group')supervision.group_field_ids=[...new Set([
              ...(s.autoSupervision?.entity_field_ids||[]),
              String(data.get('static_group_field')||'')].filter(Boolean))];
          }else{
            if(!supervision.prediction_time_field_id||!supervision.label_available_at_field_id)
              throw Error('时间监督任务须确认预测时点和标签可用时间');
            if(split==='grouped_time')supervision.group_field_ids=[...(s.autoSupervision?.entity_field_ids||[])];
          }
        }
        if(data.get('confirm_materialize')!=='yes')throw Error('请先明确确认保存并正式计算');
        s.autoSession=await api('/analysis/sessions/'+u(s.autoSession.session_id)+'/accept-and-run',json('POST',
          {name,request_id:id(),confirmed:true,supervision}));
        s.autoDelivery=await api('/analysis/sessions/'+u(s.autoSession.session_id)+'/delivery');
        s.sets=await api('/sets?asset_id='+u(s.asset));show();refreshAuto();return true;
      }
      if(form.id==='tp-fl2-dialog-form'){
        const fn=dialog;dialog=null;document.getElementById('tp-fl2-dialog-host').innerHTML='';
        if(fn)await fn(data);
      }
      if(form.id==='tp-fl2-generate'){
        const template=String(data.get('template')||'basic');
        const budget=Number(data.get('budget')||30);
        if(!Number.isInteger(budget)||budget<1||budget>60)throw Error('候选数量应在 1 至 60 之间');
        const selected=Array.from(data.getAll('candidate_field')).map(String);
        if(!selected.length)throw Error('请至少选择一个可用于计算的字段');
        s.quickTemplate=template;s.quickBudget=budget;s.quickSelectedFields=selected;
        s.session=await api('/candidate-sessions',json('POST',{
          asset_id:s.asset,version_id:s.version||'base',request_id:id(),
          template,budget,selected_field_ids:selected.length?selected:null,
          task_mode:'exploration'}));
        s.focusCandidateId=null;s.candidatePreview=null;s.workspaceMode='quick';
        s.notice=`已生成 ${s.session.candidates.length} 项候选。可勾选、预览及保存。`;
        show();
      }
      if(form.id==='tp-fl2-save-candidates'){
        if(!s.session||s.session.stale)throw Error('候选来源已变化，请重新生成');
        if(!s.session.selected_candidate_ids.length)throw Error('请选择要保存的候选特征');
        const name=String(data.get('name')||'').trim();s.candidateSetName=name;
        s.activeSet=await api('/candidate-sessions/'+u(s.session.session_id)+'/save-set',json('POST',{
          name,request_id:id(),candidate_ids:s.session.selected_candidate_ids}));
        s.sets=await api('/sets?asset_id='+u(s.asset));
        note('候选已保存为特征集草稿。可进行正式计算与实验评价。');
      }
      if(form.id==='tp-fl2-add-formula'){
        const input_field_ids=[0,1,2,3,4,5,6,7].map(i=>String(data.get('formula_field_'+i)||'')).filter(Boolean);
        if(!input_field_ids.length)throw Error('公式至少绑定一个数值字段');
        if(new Set(input_field_ids).size!==input_field_ids.length)throw Error('公式不能重复绑定同一字段');
        const expression=String(data.get('expression')||'').trim();
        const checked=await api('/expressions/validate',json('POST',{
          asset_id:s.asset,version_id:s.version||'base',
          source_sha256:s.contract.source_sha256,contract_revision:s.contract.revision,
          input_field_ids,expression,display_name:String(data.get('display_name')||'').trim()||null}));
        const definition=checked.definition;
        if(s.steps.some(x=>x.output_name===definition.output_name))throw Error('相同的公式已经加入草稿');
        s.steps.push(definition);
        note('公式语法、字段类型及单位检查通过，已加入特征集草稿。');
      }
      if(form.id==='tp-fl2-add-step'){
        const op=String(data.get('operator_id'));
        const meta=(s.operators?.operators||[]).find(o=>o.operator_id===op);
        if(!meta||!meta.available)throw Error('所选算子当前不可用');
        const controls=meta.controls||[];
        const first=String(data.get(controls.includes('event')?'event':'first')||'');
        const second=String(data.get('second')||'');
        const inputs=meta.input_count===2?[first,second]:[first];
        if(inputs.some(x=>!meta.eligible_field_ids.includes(x)))throw Error('输入字段类型不适用于当前算子');
        if(inputs.length===2&&inputs[0]===inputs[1])throw Error('两列运算请选择两个不同字段');
        let parameters={};
        if(controls.includes('category_value')){
          const val=String(data.get('category_value')||'').trim();
          if(!val||val.length>120)throw Error('请输入1至120字的类别值');
          parameters={category_value:val};
        }else if(controls.includes('sign'))parameters={operator:String(data.get('sign')||'+')};
        else if(controls.includes('event')){
          if(!s.contract.event_time_field_id)throw Error('请先确认真实事件时间');
          parameters={order_field_id:s.contract.event_time_field_id};
        }else if(controls.includes('window_seconds')){
          const seconds=Number(data.get('window_seconds'));
          if(!Number.isInteger(seconds)||seconds<1||seconds>604800)throw Error('真实时长窗口须为 1—604800 秒');
          parameters={window_seconds:seconds,order_field_id:s.contract.event_time_field_id,
                      min_periods:Number(data.get('min_periods')||1)};
        }else if(op==='lag.rows'||op==='difference.rows'||op==='relative.change'){
          parameters={lag:Number(data.get('window'))};
        }else if(controls.includes('window')){
          parameters={window:Number(data.get('window')),
                      min_periods:Number(data.get('min_periods')||1)};
        }
        const requiresOrder=op.startsWith('rolling.')||['lag.rows','difference.rows','relative.change','event.since'].includes(op);
        if(requiresOrder){
          const c=s.contract||{};
          const confirmedIndex=(c.fields||[]).filter(f=>f.role==='order_index'&&f.confirmation==='confirmed');
          const timeOrder=(c.event_time_field_id && c.fields?.find(f=>f.field_id===c.event_time_field_id)?.confirmation==='confirmed')
            ? c.event_time_field_id : null;
          const orderId=parameters.order_field_id||timeOrder||(confirmedIndex.length===1?confirmedIndex[0].field_id:null);
          if(!orderId)throw Error('请先确认唯一的记录顺序或真实时间字段。没有可靠顺序时不能手工添加窗口特征。');
          const suspectEntity=/^(?:device|machine|equipment|batch|lot|entity|设备|批次)(?:Id|ID|_id|_no|No|SN|_sn|编号|编码)$/i;
          if((c.fields||[]).some(f=>(f.role==='entity_key'||f.role==='batch_key')&&f.confirmation!=='confirmed')||
             (c.fields||[]).some(f=>f.confirmation!=='confirmed'&&suspectEntity.test(f.source_name)))
             throw Error('设备/批次分组字段尚未确认，无法安全计算窗口特征。');
          parameters.order_field_id=orderId;
          parameters.partition_field_ids=(c.entity_field_ids||[]).slice();
          if(confirmedIndex.length>1&&!timeOrder)throw Error('存在多个记录顺序字段，请先确认唯一排序字段。');
        }
        const fieldName=x=>s.contract.fields.find(f=>f.field_id===x)?.source_name||'field';
        const slug=x=>String(x).toLowerCase().replace(/[^a-z0-9_]+/g,'_').replace(/^_+|_+$/g,'').slice(0,24)||'field';
        const opName=op.split('.').slice(-1)[0];
        const suffix=controls.includes('sign')?({'+':'sum','-':'diff','*':'mul','/':'div'}[parameters.operator]):opName;
        const extra=parameters.window_seconds||parameters.window||parameters.lag||
           (op==='category.is_value'?String(parameters.category_value).replace(/[^a-zA-Z0-9_]+/g,'_').slice(0,12):'');
        let generated=inputs.map(x=>slug(fieldName(x))).join('_')+'_'+suffix+(extra?'_'+extra:'');
        if(!/^[A-Za-z_]/.test(generated))generated='f_'+generated;
        generated=generated.slice(0,55);
        const oldNames=new Set([...s.steps.map(x=>x.output_name),...s.contract.fields.map(x=>x.source_name)]);
        let output_name=generated,idx=2;
        while(oldNames.has(output_name))output_name=generated.slice(0,58)+'_'+(idx++);
        const displayName=String(data.get('display_name')||'').trim()||meta.title+' · '+inputs.map(fieldName).join('、');
        s.steps.push({feature_id:id(),output_name,display_name,
          operator_id:op,operator_version:meta.version,input_field_ids:inputs,parameters});
        s.manualDraft={operator_id:op};show();
      }
      if(form.id==='tp-fl2-save-set'){
        const taskMode=String(data.get('task_mode')||'exploration');
        let task=null;
        if(taskMode==='supervised'){
          const c=s.contract;
          const split=String(data.get('split_kind')||'time');
          const staticSplit=['kfold','stratified','group'].includes(split);
          if(!c||c.state!=='confirmed')throw Error('请先确认数据契约');
          if(!staticSplit&&(!c.timezone||!c.event_time_field_id||!c.available_at_field_id
            ||!c.entity_field_ids?.length))
            throw Error('时间预测需要已确认的设备键、事件时间、业务可用时间及真实时区');
          const prediction=String(data.get('prediction_time')||'');
          const label=String(data.get('label')||'');
          const labelAvailable=String(data.get('label_available')||'');
          if(!label)throw Error('请指定已确认的数值标签字段');
          if(!staticSplit&&(!prediction||!labelAvailable))throw Error('时间预测需指定预测时点及标签可用时间');
          if(staticSplit&&data.get('static_independence_confirmed')!=='yes')
            throw Error('静态验证需要人工确认样本独立性和特征在决策时可用');
          task={task_spec_version:1,task_id:'task-'+id(),mode:'supervised',
            task_type:String(data.get('task_type')||'binary_classification'),
            entity_field_ids:c.entity_field_ids||[],prediction_time_field_id:staticSplit?null:prediction,
            label_field_id:label,label_available_at_field_id:staticSplit?null:labelAvailable,
            split_kind:split,static_independence_confirmed:staticSplit,
            ...(split==='grouped_time'||split==='group'?{group_field_ids:c.entity_field_ids||[]}:{}),
            n_splits:3,purge_rows:0};
        }
        const spec={schema_version:2,name:String(data.get('name')).trim(),source:{
           asset_id:s.asset,version_id:s.version||'base',source_sha256:s.contract.source_sha256},
           contract_revision:s.contract.revision,features:s.steps,...(task?{task}:{})};
        await api('/sets/validate',json('POST',spec));
        s.activeSet=await api('/sets',json('POST',{spec,request_id:id()}));
        s.sets=await api('/sets?asset_id='+u(s.asset));s.steps=[];note('特征集草稿已保存，可以运行完整计算。');
      }
      if(form.id==='tp-fl2-evaluation-form'){
        const set=s.sets.find(x=>x.set_id===data.get('set_id'));
        if(!set)throw Error('请选择一个特征集');
        if(set.asset_id!==s.asset||set.version_id!==(s.version||'base'))
          throw Error('特征集与数据来源不一致');
        const task=set.spec?.task||{task_spec_version:1,task_id:'exploration-'+id(),
          mode:'exploration',task_type:'profile'};
        const models=String(data.get('models')||'linear');
        const selectedModels=models==='both'?['linear','forest']:[models];
        const isSupervised=task.mode==='supervised';
        const maxSelected=Number(data.get('max_selected'));
        const missing=Number(data.get('max_missing'));
        const correlation=Number(data.get('max_corr'));
        if(!Number.isInteger(maxSelected)||maxSelected<1||maxSelected>100||
           !Number.isFinite(missing)||missing<0||missing>.99||
           !Number.isFinite(correlation)||correlation<.5||correlation>1)
          throw Error('筛选参数超出有效范围');
        const searchEnabled=isSupervised&&data.has('search_enabled');
        const modelsSafe=isSupervised?selectedModels:['linear'];
        const sliceField=String(data.get('slice_field')||'');
        const protocol={task,set_id:set.set_id,revision:set.revision,models:modelsSafe,
          selection:{enabled:true,max_selected:maxSelected,max_missing_fraction:missing,
            max_abs_correlation:correlation,rank_method:String(data.get('rank_method')||'univariate')},
          search:{enabled:searchEnabled,max_candidates:Number(data.get('search_candidates')||32),
            max_model_fits:Number(data.get('search_fits')||8),
            max_seconds_per_fold:Number(data.get('search_seconds')||25)},
          slices:{field_ids:sliceField?[sliceField]:[]}};
        if(isSupervised&&data.has('transfer_enabled')){
          if(searchEnabled)throw Error('独立留出评价需要关闭内层自动搜索，避免根据测试结果调参');
          const transferKind=String(data.get('transfer_kind')||'unseen_device');
          const fieldId=String(data.get('transfer_field')||'');
          const declared=(s.contract?.fields||[]).find(f=>f.field_id===fieldId);
          if(!declared||declared.confirmation!=='confirmed'||declared.classification==='restricted'||
            (transferKind==='unseen_device'?!s.contract?.entity_field_ids?.includes(fieldId)||declared.role!=='entity_key':
             transferKind!=='unseen_regime'||declared.role!=='regime'||declared.semantic_type!=='category'))
            throw Error('必须选择已确认且授权的设备键或工况字段');
          const values=String(data.get('transfer_values')||'').split(',').map(x=>x.trim()).filter(Boolean);
          if(!values.length||values.length>6||new Set(values).size!==values.length)
            throw Error('留出组值需要 1—6 个明确且不重复的值');
          const cutoff=String(data.get('transfer_cutoff')||'').trim();
          if(['time','grouped_time'].includes(task.split_kind)&&!cutoff)
            throw Error('时序设备/工况迁移评价需要明确真实的时间截断');
          protocol.industrial_transfer={kind:transferKind,group_field_id:fieldId,
            holdout_values:values,...(cutoff?{time_cutoff:cutoff}:{})};
        }
        const plan=await api('/evaluations/plans',json('POST',{protocol}));
        s.evalPlan={...plan,set_id:set.set_id};s.activeEvalSet=set;
        s.evalResult=null;s.evalSelection=null;
        note('实验协议已固定。开始评价后将检查时间安全、来源与物化结果。');show();
      }
      if(form.id==='tp-fl2-review-form'){
        if(!s.evalResult?.evaluation_id)throw Error('请先完成模型评价');
        const feature_ids=data.getAll('review_feature').map(String);
        const search_candidate_ids=data.getAll('review_search').map(String);
        s.evalSelection=await api('/evaluations/'+u(s.evalResult.evaluation_id)+'/selection',json('PUT',{
          feature_ids,search_candidate_ids,expected_revision:s.evalSelection?.revision||0,
          note:String(data.get('review_note')||'')}));
        note('筛选决策已保存。可进一步生成新特征集草稿。');show();
      }
      if(form.id==='tp-fl2-agent-form'){
        const message=String(data.get('message')||'').trim();if(!message)throw Error('请输入要求');
        s.plan=await api('/agent/plans',json('POST',{message,asset_id:s.asset,
          version_id:s.version||'base',request_id:id(),
          ...(s.activeSet?.spec?.task?.mode==='supervised'?{task:s.activeSet.spec.task}:{})}));show();
      }
    }catch(ex){note('操作未完成：'+ex.message);}
    return true;
  }
  function change(event){
    if(event.target?.dataset?.featureAdvancedChange){
      window.TimePilotFeatureAdvanced.change(s,api,event.target,show).catch(ex=>note(ex.message));return true;
    }
    const kind=event.target?.dataset?.featureV2Change;
    if(kind==='evaluation-set'){
      const set=s.sets.find(x=>x.set_id===event.target.value);
      s.activeEvalSet=set||null;s.evalPlan=null;s.evalResult=null;s.evalSelection=null;show();
      if(set)api('/evaluations/sets/'+u(set.set_id)+'/latest').then(async r=>{
        if(s.activeEvalSet?.set_id!==set.set_id)return;
        s.evalResult=r.evaluation;
        s.evalSelection=r.evaluation?(await api('/evaluations/'+u(r.evaluation.evaluation_id)+'/selection')).selection:null;
        show();
      }).catch(ex=>note('读取评价结果失败：'+ex.message));
      return true;
    }
    if(kind==='manual-operator'){
      s.manualDraft={operator_id:event.target.value};show();return true;
    }
    if(kind==='candidate-selection'){
      if(s.selectionPending){show();return true;}
      s.selectionPending=true;
      if(!s.session||s.session.stale){note('候选已过期，请重新生成');return true;}
      const cid=event.target.value;
      const ids=new Set(s.session.selected_candidate_ids);
      if(event.target.checked)ids.add(cid);else ids.delete(cid);
      api('/candidate-sessions/'+u(s.session.session_id),json('PATCH',{
        expected_revision:s.session.revision,selected_candidate_ids:[...ids]})).then(next=>{
          s.session=next;s.candidatePreview=null;s.selectionPending=false;show();
        }).catch(ex=>{s.selectionPending=false;note('选择未保存：'+ex.message);});return true;
    }
    if(kind==='wb-chart-entity'||kind==='wb-chart-resolution'||kind==='wb-chart-entity-field'){
      if(kind==='wb-chart-entity')s.uiChartEntity=event.target.value;
      else if(kind==='wb-chart-entity-field'){s.uiChartEntityField=event.target.value;s.uiChartEntity='';}
      else s.uiChartResolution=Number(event.target.value);
      writeViewURL(true);loadAutoChart().catch(ex=>note(ex.message));return true;
    }
    if(kind==='wb-choice'){
      const ids=new Set(s.uiDraftSelection||s.autoSession?.selection||[]);
      if(event.target.checked)ids.add(event.target.value);else ids.delete(event.target.value);
      s.uiDraftSelection=[...ids];
      const counter=document.querySelector('.tp-wb-list-actions span');
      if(counter)counter.textContent=ids.size+' 项已选（尚未保存）';
      return true;
    }
    if(kind==='supervision-split'){
      // Toggle only the visible confirmation group; preserve manual input.
      const f=event.target.closest?.('form');
      const staticSplit=['kfold','stratified','group'].includes(event.target.value);
      const stat=f?.querySelector('.tp-wb-static-confirm');
      const time=f?.querySelector('.tp-wb-time-fields');
      if(stat)stat.hidden=!staticSplit;
      if(time)time.hidden=staticSplit;
      return true;
    }
    if(kind==='wb-objective'){s.autoObjective=event.target.value;return true;}
    if(kind==='quick-fields')return true;
    if(event.target?.dataset.featureV2Change==='select-version'){
      chartGeneration++;activeAbort?.abort();clearTimeout(autoTimer);resetAssetState();
      const currentTool=new URLSearchParams(location.search).get('tool');
      const integrated=document.getElementById('tp-analysis-workspace')&&['manual','advanced','bi'].includes(currentTool);
      ctx.navigate('/projects/'+u(s.pid)+(integrated?'/analysis?tool='+u(currentTool)+'&asset=':
        document.getElementById('tp-analysis-workspace')?'/analysis?section=features&asset=':'/features/'+s.tab+'?asset=')+
        u(s.asset)+'&version='+u(event.target.value));return true;
    }
    if(event.target?.dataset.featureV2Change==='select-asset'){
      // Invalidate previous AssetContext synchronously, before any old request resolves.
      renderGeneration++;activeAbort?.abort();clearTimeout(pollTimer);clearTimeout(planTimer);resetAssetState();
      const currentTool=new URLSearchParams(location.search).get('tool');
      const integrated=document.getElementById('tp-analysis-workspace')&&['manual','advanced','bi'].includes(currentTool);
      ctx.navigate('/projects/'+u(s.pid)+(integrated?'/analysis?tool='+u(currentTool)+'&asset=':
        document.getElementById('tp-analysis-workspace')?'/analysis?section=features&asset=':'/features/'+s.tab+'?asset=')+
        u(event.target.value)+'&version=base');return true;
    }
    return false;
  }
  window.TimePilotFeatureV2UI={render,click,submit,change,dispose,openIntegratedBi};
})();
