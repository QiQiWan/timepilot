/* FeatureLab 2.8: one project/asset context, four focused screens, job evidence. */
(() => {
  'use strict';
  const e=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const u=encodeURIComponent;
  const h={e,u};
  const json=(method,data)=>({method,headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
  let ctx=null,s=null,pollTimer=null,planTimer=null,dialog=null,renderGeneration=0,activeAbort=null;
  const base=()=>'/projects/'+u(s.pid)+'/feature-lab';
  const api=(path,opts)=>ctx.req(base()+path,{...(opts||{}),signal:activeAbort?.signal});
  const tabs=[['profile','数据画像'],['build','特征工作台'],['evaluate','实验评价'],['advanced','高级发现'],['catalog','特征资产']];
  const id=()=>globalThis.crypto?.randomUUID?.()||`req-${Date.now()}-${Math.random().toString(36).slice(2)}`;
  function dispose(){clearTimeout(pollTimer);clearTimeout(planTimer);pollTimer=null;planTimer=null;dialog=null;activeAbort?.abort();activeAbort=null;window.TimePilotFeaturePerspective?.dispose?.();}
  function note(message){s.notice=message;ctx?.toast?.(message);show();}
  function show(){
    const root=document.getElementById('tp-feature-lab');if(!root||!s)return;
    const oldShell=root.querySelector('.tp-fl2-shell');
    const keepViewer=s.tab==='build'?oldShell?.querySelector('#tp-fl2-perspective'):null;
    const draft=document.getElementById('tp-fl2-message')?.value||'';
    const assets=s.assets.map(a=>`<option value="${e(a.native_asset_id)}" ${s.asset===a.native_asset_id?'selected':''}>${e(a.asset?.name||a.native_asset_id)}</option>`).join('');
    const docs=s.contract;
    const markup=`<div class="tp-fl2-shell"><div class="tp-fl2-top"><div><h1>特征工程</h1><p>从数据画像到特征发布，计算结果与来源版本保持关联。</p></div><button class="button" data-feature-v2="help">使用手册</button></div>
    ${s.workflow?.steps?.length?`<div class="tp-fl2-guided" aria-label="工作流进度"><span class="tp-fl2-guided-title">当前任务</span>${s.workflow.steps.map((step,i)=>`<a class="tp-fl2-guided-step ${step.completed?'is-done':''} ${s.workflow.next_step?.key===step.key?'is-next':''}" href="/projects/${u(s.pid)}/features/${u(step.tab)}?asset=${u(s.asset)}&version=${u(s.version||'base')}" title="${e(step.completed?'已完成该阶段的系统证据':'需要继续处理：'+step.label)}"><span>${i+1}</span>${e(step.label)}</a>`).join('')}<span class="tp-fl2-guided-hint">${e(s.workflow.status==='published'?'已发布 · 可复核':'下一步：'+s.workflow.next_step?.label)}</span></div>`:''}
    <nav class="tp-fl2-tabs" aria-label="特征工程页面">${tabs.map(([key,label])=>`<a class="${s.tab===key?'active':''}" href="/projects/${u(s.pid)}/features/${key}?asset=${u(s.asset)}&version=${u(s.version||'base')}">${label}</a>`).join('')}</nav>
    ${s.assets.length?`<div class="tp-fl2-context"><label>当前数据 <select data-feature-v2-change="select-asset">${assets}</select></label><span>字段 ${e(docs?.fields?.length||0)}</span><span>契约：${e({confirmed:'已确认',detected:'待核实',needs_review:'待确认',stale:'来源已变化'}[docs?.state]||'待核实')}</span><span>版本：${e(s.version||'base')}</span></div>`:''}
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
    Object.assign(s,{steps:[],contract:null,operators:null,sets:[],activeSet:null,
      runResult:null,previewResult:null,evalPlan:null,evalResult:null,evalSelection:null,activeEvalSet:null,profile:null,
      profileId:null,job:null,plan:null,lineage:null,session:null,candidatePreview:null,fieldSuggestions:null,quickSelectedFields:null,focusCandidateId:null,manualDraft:{},workspaceMode:'quick',chartMode:'trend',viewerOpen:false,advanced:null,workflow:null});
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
      const [contract,operators,sets,workflow]=await Promise.all([
        api('/contracts?asset_id='+u(asset)+'&version_id='+u(version)),api('/operators?asset_id='+u(asset)+'&version_id='+u(version)),api('/sets?asset_id='+u(asset)),api('/workflow/summary?asset_id='+u(asset)+'&version_id='+u(version))]);
      if(expected!==renderGeneration || local!==s || asset!==s.asset)return;
      s.contract=contract;s.operators=operators;s.sets=(sets||[]).filter(x=>x.version_id===version);s.workflow=workflow;
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
  async function render(context,pid,tab='profile'){
    const old=s;dispose();ctx=context;const thisGeneration=++renderGeneration;activeAbort=new AbortController();
    const query=new URLSearchParams(location.search);
    const asset=query.get('asset')||old?.asset||'';const version=query.get('version')||(old?.pid===pid&&old.asset===asset?old.version:'base')||'base';
    const sameContext=old?.pid===pid && old.asset===asset && (old.version||'base')===version;
    s={pid,tab:tabs.some(t=>t[0]===tab)?tab:'profile',asset,version,
       assets:[],contract:null,operators:null,sets:[],steps:sameContext?old.steps:[],
       profile:sameContext?old.profile:null,job:sameContext?old.job:null,activeSet:sameContext?old.activeSet:null,
       runResult:sameContext?old.runResult:null,previewResult:sameContext?old.previewResult:null,evalPlan:sameContext?old.evalPlan:null,evalResult:sameContext?old.evalResult:null,evalSelection:sameContext?old.evalSelection:null,activeEvalSet:sameContext?old.activeEvalSet:null,
       assistantOpen:old?.assistantOpen||false,plan:sameContext?old.plan:null,notice:null,lineage:null,
       session:sameContext?old.session:null,candidatePreview:sameContext?old.candidatePreview:null,
       fieldSuggestions:sameContext?old.fieldSuggestions:null,
       quickSelectedFields:sameContext?old.quickSelectedFields:null,
       quickTemplate:sameContext?old.quickTemplate:'basic',quickBudget:sameContext?old.quickBudget:30,
       focusCandidateId:sameContext?old.focusCandidateId:null,
       manualDraft:sameContext?old.manualDraft:{},workspaceMode:sameContext?old.workspaceMode:'quick',chartMode:sameContext?old.chartMode:'trend',
       viewerOpen:sameContext?Boolean(old.viewerOpen):false,advanced:sameContext?old.advanced:null,workflow:sameContext?old.workflow:null};
    try{await refreshData(thisGeneration);if(thisGeneration!==renderGeneration)return;show();
      if(s.job && ['queued','running','cancel_requested'].includes(s.job.status))refreshJob().catch(ex=>note(ex.message));
      if(s.plan?.status==='awaiting_job')refreshPlan().catch(ex=>note(ex.message));
    }
    catch(ex){if(thisGeneration!==renderGeneration || ex.name==='AbortError')return;s.notice='载入特征工程失败：'+String(ex.message);show();}
  }
  function modal(title,body,onAccept){
    const host=document.getElementById('tp-fl2-dialog-host');if(!host)return;
    host.innerHTML=`<div class="tp-fl2-overlay"><section class="tp-fl2-dialog" role="dialog" aria-modal="true" aria-label="${e(title)}"><h2>${e(title)}</h2><form id="tp-fl2-dialog-form">${body}<div class="action-row"><button class="button" type="button" data-feature-v2="close-modal">取消</button><button class="button button-primary" type="submit">确认</button></div></form></section></div>`;
    dialog=onAccept;host.querySelector('input,select,textarea,button')?.focus();
  }
  function editableContract(){
    if(!s.contract)return;
    const options=['unspecified','entity_key','event_time','available_at','ingested_at','prediction_time','value','regime','label','batch_key','record_id'];
    const html=`<div class="tp-fl2-contract-table">${s.contract.fields.map((f,i)=>`<div class="tp-fl2-field-row" data-fid="${e(f.field_id)}"><strong>${e(f.source_name)}</strong><small>${e(f.physical_type)}</small><input name="alias-${i}" value="${e(f.display_name||'')}" placeholder="可选业务名称"><select name="role-${i}">${options.map(o=>`<option value="${o}" ${o===f.role?'selected':''}>${o}</option>`).join('')}</select><select name="semantic-${i}">${['unknown','numeric','string','datetime','category','id','boolean','event','target'].map(t=>`<option value="${t}" ${f.semantic_type===t?'selected':''}>${t}</option>`).join('')}</select></div>`).join('')}</div>
    <label class="field"><span>确认的时区（存在实际时间字段时必填）</span><input name="timezone" value="${e(s.contract.timezone||'')}" placeholder="例如 Asia/Shanghai"></label><p class="muted small">不会改写源列名或源文件。只有实际 DATETIME/TIMESTAMP 类型才能标为真实时间。</p>`;
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
    if(advanced){event.preventDefault();try{await window.TimePilotFeatureAdvanced.click(s,api,advanced,show);}catch(ex){note('操作失败：'+ex.message);}return true;}
    const el=event.target.closest('[data-feature-v2]');if(!el)return false;
    event.preventDefault();const act=el.dataset.featureV2;
    try{
      if(act==='dismiss-notice'){s.notice=null;show();}
      if(act==='toggle-assistant'){s.assistantOpen=!s.assistantOpen;show();}
      if(act==='insert-prompt'){const t=document.getElementById('tp-fl2-message');if(t){t.value=el.dataset.prompt;t.focus();}}
      if(act==='edit-contract')editableContract();
      if(act==='close-modal'){document.getElementById('tp-fl2-dialog-host').innerHTML='';dialog=null;}
      if(act==='help')modal('FeatureLab 使用说明',`<p>第一步：核实字段和时间含义；第二步：生成完整画像；第三步：选择字段和算子建立特征草稿；第四步：执行计算；第五步：评价并提交人工审核，发布后可以直接在 BI/模型中使用。</p><p class="muted">没有真实时间字段时，窗口只按记录条数计算。完整计算与预览有不同的数据范围。</p>`,async()=>{});
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
      if(act==='refresh-sets'){s.sets=await api('/sets?asset_id='+u(s.asset));show();}
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
      if(act==='show-lineage'){s.lineage=await api('/sets/'+u(el.dataset.sid)+'/lineage');show();}
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
        if(controls.includes('sign'))parameters={operator:String(data.get('sign')||'+')};
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
        if(s.contract?.state==='confirmed'&&s.contract.event_time_field_id &&
           (op.startsWith('rolling.')||['lag.rows','difference.rows','relative.change'].includes(op)) &&
           !parameters.order_field_id) parameters.order_field_id=s.contract.event_time_field_id;
        const fieldName=x=>s.contract.fields.find(f=>f.field_id===x)?.source_name||'field';
        const slug=x=>String(x).toLowerCase().replace(/[^a-z0-9_]+/g,'_').replace(/^_+|_+$/g,'').slice(0,24)||'field';
        const opName=op.split('.').slice(-1)[0];
        const suffix=controls.includes('sign')?({'+':'sum','-':'diff','*':'mul','/':'div'}[parameters.operator]):opName;
        const extra=parameters.window_seconds||parameters.window||parameters.lag||'';
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
          if(!c||c.state!=='confirmed'||!c.timezone||!c.event_time_field_id
            ||!c.available_at_field_id||!c.entity_field_ids?.length)
            throw Error('请先在数据画像中确认设备键、采集时间、业务可用时间及真实时区');
          const prediction=String(data.get('prediction_time')||'');
          const label=String(data.get('label')||'');
          const labelAvailable=String(data.get('label_available')||'');
          if(!prediction||!label||!labelAvailable)throw Error('请指定预测时点、标签以及标签可用时间');
          task={task_spec_version:1,task_id:'task-'+id(),mode:'supervised',
            task_type:String(data.get('task_type')||'binary_classification'),
            entity_field_ids:c.entity_field_ids,prediction_time_field_id:prediction,
            label_field_id:label,label_available_at_field_id:labelAvailable,
            split_kind:String(data.get('split_kind')||'time'),
            ...(data.get('split_kind')==='grouped_time'?{group_field_ids:c.entity_field_ids}:{}),
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
    if(kind==='quick-fields')return true;
    if(event.target?.dataset.featureV2Change==='select-asset'){
      // Invalidate previous AssetContext synchronously, before any old request resolves.
      renderGeneration++;activeAbort?.abort();clearTimeout(pollTimer);clearTimeout(planTimer);resetAssetState();
      ctx.navigate('/projects/'+u(s.pid)+'/features/'+s.tab+'?asset='+u(event.target.value)+'&version=base');return true;
    }
    return false;
  }
  window.TimePilotFeatureV2UI={render,click,submit,change,dispose};
})();
