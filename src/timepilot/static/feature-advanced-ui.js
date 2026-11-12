/* v0.30 advanced feature workbench. No dynamic SQL, code evaluation or auto-publish. */
window.TimePilotFeatureViews=window.TimePilotFeatureViews||{};
window.TimePilotFeatureAdvanced=(()=>{
  'use strict';
  const e=s=>String(s??'').replace(/[&<>"']/g,x=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[x]));
  const u=encodeURIComponent;
  let timer=null;
  const state=s=>s.advanced||(s.advanced={engine:'tsfresh',secondary:'',secondaryContract:null,
    capabilities:null,plan:null,recipe:null,draft:null,job:null,artifact:null,error:null,joinDecision:'',joinContract:null,joinPlan:null,joinDraft:null,joinJob:null,joined:null,evalJob:null,evalReport:null});
  const role=(f,roles)=>f?.confirmation==='confirmed'&&roles.includes(f.role);
  function selector(fields,roles,name,title,required=true,selected=''){
    const options=(fields||[]).filter(f=>role(f,roles)).map(f=>`<option value="${e(f.field_id)}" ${f.field_id===selected?'selected':''}>${e(f.display_name||f.source_name)} · ${e(f.role)}</option>`).join('');
    return `<label class="field"><span>${e(title)}</span><select name="${e(name)}" ${required?'required':''}>${options||'<option value="">需先确认字段契约</option>'}</select></label>`;
  }
  function numericSelect(fields,selected=[]){
    const options=(fields||[]).filter(f=>role(f,['value'])&&f.semantic_type==='numeric').map(f=>
      `<label class="tp-adv-signal"><input type="checkbox" name="adv_values" value="${e(f.field_id)}" ${selected.includes(f.field_id)?'checked':''}><span>${e(f.display_name||f.source_name)}</span></label>`).join('');
    return `<fieldset class="tp-adv-signals"><legend>要提取的传感器变量（至多 8 个）</legend>${options||'<p>请先在数据画像中确认数值字段的 value 角色。</p>'}</fieldset>`;
  }
  function ensureResult(a,s){
    if(!a.job)return '';
    const message={queued:'排队中',running:'正在计算',succeeded:'计算成功',
      failed_terminal:'计算失败',failed_retryable:'依赖未就绪或可重试',cancelled:'已取消'}[a.job.status]||a.job.status;
    const artifact=a.artifact;
    return `<section class="panel tp-adv-result"><h3>后台计算结果</h3><p>状态：${e(message)} · 任务 ${e(a.job.job_id)}</p>
       ${a.job.error_text?`<p role="alert">${e(a.job.error_text)}</p>`:''}
       ${artifact?`<p>已生成 ${e(artifact.rows)} 行、${e(artifact.columns.length-1)} 个候选特征，PIT：${e(artifact.pit.pit)}</p>
        <p>源数据指纹：<code>${e(artifact.source_fingerprint.slice(0,20))}</code> · 数据完整性已复核</p>
        <p class="muted">这些结果仍需人工筛选、重新评价和审核，尚未自动发布到模型。</p>
        <a class="button button-primary" href="/projects/${u(s.pid)}/bi/native/${u(artifact.native_asset_id)}">在 Perspective BI 查看真实结果</a>`:''}
       <button class="button" type="button" data-feature-advanced="refresh">刷新任务</button></section>`;
  }
  function joinWorkflow(a,s){
    if(!a.artifact)return '';
    const selectedAsset=a.joinDecision||s.asset;
    const decisionOpts=(s.assets||[]).map(x=>`<option value="${e(x.native_asset_id)}" ${x.native_asset_id===selectedAsset?'selected':''}>${e(x.asset?.name||x.native_asset_id)}</option>`).join('');
    const current=selectedAsset===s.asset?s.contract:a.joinContract;
    const verified=current?.state==='confirmed';
    const fields=current?.fields||[];
    const columns=(a.artifact.columns||[]).filter(x=>!String(x).startsWith('__'));
    const selected=a.joinDraft?.selected_columns||[];
    const pick=columns.map((name,i)=>`<label class="tp-adv-signal"><input type="checkbox" name="chosen" value="${e(name)}" ${!a.joinDraft&&i<12||selected.includes(name)?'checked':''}><span>${e(name)}</span></label>`).join('');
    const joined=a.joined;
    const evalState=a.evalJob;
    return `<section class="panel tp-adv-join" aria-label="关联预测实例并评价"><h3>第二步：关联预测实例</h3>
      <p class="muted">将高级特征按预测实例主键和实际截止时间一对一回接。来源哈希和 PIT 必须全部匹配，原始数据不会更改。</p>
      <form id="tp-adv-join-form"><div class="tp-adv-two"><label class="field"><span>预测实例数据</span><select name="decision_asset" data-feature-advanced-change="joinDecision">${decisionOpts}</select></label>
      <div><p>字段契约：${e(current?.state||'加载中')}。tsfresh 时序测点表通常需要选择另一个逐实例预测表。</p></div></div>
      ${selector(fields,['record_id','batch_key'],'decision_key','预测实例唯一标识',true,a.joinDraft?.decision_key)}
      ${selector(fields,['prediction_time'],'prediction_time','实际预测时点',true,a.joinDraft?.prediction_time)}
      <fieldset class="tp-adv-signals"><legend>拟用于建模的高级特征（最多 60 项）</legend>${pick||'<p>当前产物没有可选数值候选列</p>'}</fieldset>
      <button class="button" type="submit" ${!verified||!columns.length?'disabled':''}>核查 PIT 与候选列</button></form>
      ${a.joinPlan?`<p>已核验候选源 <code>${e(a.joinPlan.candidate_sha256.slice(0,12))}</code>；预测源 <code>${e(a.joinPlan.decision_sha256.slice(0,12))}</code>。确认后将提交持久化计算任务。</p>
      <button class="button button-primary" data-feature-advanced="join-confirm">确认并物化模型输入</button>`:''}
      ${a.joinJob?`<p>回接状态：${e(a.joinJob.status)} · ${e(a.joinJob.job_id)} <button class="button" data-feature-advanced="join-refresh">刷新</button></p>`:''}
      ${joined?`<p>已物化 ${e(joined.rows)} 个预测实例 · SHA256 <code>${e(joined.joined_sha256.slice(0,24))}</code></p>
      <a class="button" href="/projects/${u(s.pid)}/bi/native/${u(joined.native_asset_id)}">在原生 BI 查看同一模型输入资产</a>
      <section><h3>第三步：同源模型对照</h3><p>评价将读取刚才物化的同一 Parquet；标签可用时间与训练/验证切分必须经过校验。</p>
      <form id="tp-adv-eval-form">
      ${selector(fields,['label'],'label','已确认质量标签',true)}
      ${selector(fields,['available_at'],'label_available','标签可用时间',true)}
      <label class="field"><span>任务类型</span><select name="task_type"><option value="binary_classification">质量分类</option><option value="regression">数值回归</option></select></label>
      <label class="field"><span>模型方案</span><select name="model"><option value="linear">线性模型</option><option value="forest">随机森林</option><option value="both">线性与随机森林</option></select></label>
      <label class="field"><span>设备/工况切片</span><select name="slice" ><option value="">无</option>${fields.filter(f=>role(f,['entity_key','batch_key','regime'])).map(f=>`<option value="${e(f.field_id)}">${e(f.display_name||f.source_name)}</option>`).join('')}</select></label>
      <button type="submit" class="button button-primary">提交训练折评价</button></form></section>`:''}
      ${evalState?`<p>评价状态：${e(evalState.status)} · ${e(evalState.job_id)} <button class="button" data-feature-advanced="eval-refresh">刷新评价</button></p>`:''}
      ${a.evalReport?`<p>模型评价已计算；使用产物 SHA256 <code>${e(a.evalReport.report.evaluated_artifact_sha256.slice(0,24))}</code>，仍需审阅实际收益和切片表现。</p>
      <details><summary>查看完整模型评价数据</summary><pre class="tp-fl2-json">${e(JSON.stringify(a.evalReport.report,null,2))}</pre></details>`:''}
    </section>`;
  }
  function render(s){
    const a=state(s),c=s.contract, sec=a.secondaryContract, draft=a.draft||{};
    const ok=c?.state==='confirmed';
    const opts=(s.assets||[]).filter(x=>x.native_asset_id!==s.asset).map(x=>`<option value="${e(x.native_asset_id)}" ${x.native_asset_id===a.secondary?'selected':''}>${e(x.asset?.name||x.native_asset_id)}</option>`).join('');
    const mainFields=c?.fields||[], secondary=sec?.fields||[];
    const engineReady=Boolean(a.capabilities?.engines?.[a.engine]);
    const missing=!a.capabilities?'检查中':engineReady?'已安装':'未安装（可选）';
    const f=a.engine==='tsfresh'?`
      ${selector(mainFields,['record_id','batch_key','entity_key'],'sample','样本/循环唯一标识',true,draft.sample)}
      ${selector(mainFields,['event_time'],'event','测量采集时间',true,draft.event)}
      ${selector(mainFields,['available_at'],'available','测量数据实际可用时间',true,draft.available)}
      ${selector(mainFields,['prediction_time'],'cutoff','特征决策截止时间',true,draft.cutoff)}
      ${numericSelect(mainFields,draft.adv_values||[])}
      <label class="field"><span>计算预设</span><select name="preset"><option value="minimal">精简统计（默认）</option><option value="efficient">更多时序统计（耗时较高）</option></select></label>`: `
      <label class="field"><span>关联的事件/测点数据资产</span><select data-feature-advanced-change="secondary" name="secondary"><option value="">请选择</option>${opts}</select></label>
      <p class="muted">事件表的字段契约：${e(sec?.state||'未读取')}。仅聚合预测时间之前已发生且已可获取的记录。</p>
      <div class="tp-adv-two"><div><h3>预测实例表</h3>
      ${selector(mainFields,['record_id','batch_key'],'decision_id','实例唯一标识',true,draft.decision_id)}
      ${selector(mainFields,['entity_key'],'decision_entity','设备编号',true,draft.decision_entity)}
      ${selector(mainFields,['prediction_time'],'cutoff','预测时点',true,draft.cutoff)}</div><div><h3>历史事件表</h3>
      ${selector(secondary,['entity_key'],'event_entity','设备编号',true,draft.event_entity)}
      ${selector(secondary,['event_time'],'event','事件发生时间',true,draft.event)}
      ${selector(secondary,['available_at'],'available','事件可用时间',true,draft.available)}</div></div>
      ${numericSelect(secondary,draft.adv_values||[])}
      <label class="field"><span>允许的聚合函数</span><select name="primitive"><option value="mean">均值</option><option value="max">最大值</option><option value="min">最小值</option><option value="count">事件数</option></select></label>`;
    return `<section class="tp-fl2-work tp-adv-work"><header><div><h2>高级特征发现</h2><p>按实际时间安全契约计算高成本特征；所有产物进入待审核区。</p></div>
      <a class="button" href="/projects/${u(s.pid)}/bi/native/${u(s.asset)}">查看源数据</a></header>
      <div class="tp-adv-toolbar"><label class="field"><span>算法引擎</span><select data-feature-advanced-change="engine"><option value="tsfresh" ${a.engine==='tsfresh'?'selected':''}>tsfresh · 时序特征</option><option value="featuretools" ${a.engine==='featuretools'?'selected':''}>Featuretools · 多表聚合</option></select></label>
      <div class="tp-adv-status">${e(a.engine)} · ${e(missing)}<small>不会安装依赖或直接发布特征</small></div></div>
      ${!ok?'<p role="alert" class="tp-fl2-notice">字段契约尚未确认，请先进入「数据画像」核实时间、标识和数值角色。</p>':''}
      <form id="tp-fl2-advanced-form"><div class="tp-adv-fields">${f}</div>
      <div class="tp-adv-limits"><label class="field"><span>最多扫描行数</span><input name="max_rows" type="number" min="100" max="200000" value="30000"></label>
      <label class="field"><span>最多生成特征</span><input name="max_features" type="number" min="1" max="256" value="80"></label>
      <label class="field"><span>最长运行秒数</span><input name="max_seconds" type="number" min="10" max="240" value="90"></label></div>
      <button type="submit" class="button button-primary" ${!ok||!engineReady?'disabled':''}>第一步：检查数据契约与预算</button>${!engineReady?'<p class="muted">当前算法尚不可用。请先在现有 Python 环境安装 requirements-featurelab-advanced.txt，然后刷新页面。</p>':''}</form>
      ${a.error?`<p class="tp-fl2-notice" role="alert">${e(a.error)}</p>`:''}
      ${a.plan?`<section class="panel tp-adv-review"><h3>执行前确认</h3><p>来源 ${e(Object.keys(a.plan.sources).length)} 份 · 行数/列数预算已核实 · 预计生成最多 ${e(a.plan.estimated_max_features)} 个候选</p>
        <p>任务指纹 <code>${e(a.plan.recipe_sha256.slice(0,20))}</code></p>
        <p>高成本计算可能占用较多 CPU/内存。是否提交持久化任务？</p>
        <button type="button" class="button button-primary" data-feature-advanced="confirm">确认执行</button></section>`:''}
      ${ensureResult(a,s)}
      ${joinWorkflow(a,s)}
      <details><summary>算法限制与审计规则</summary><p>严格校验真实日期时间字段、设备/批次键及数据可用时间。限制输入和关联量；候选不能自动替代训练特征。工具只接受结构化字段 ID，不允许直接执行模型生成的 SQL、Python 或路径。</p></details>
      </section>`;
  }
  async function load(s,api){
    const a=state(s);try{a.capabilities=await api('/advanced/capabilities');}
    catch(ex){a.error='算法能力检查失败：'+ex.message;}
    try{
      const recent=await api('/advanced/jobs/latest?asset_id='+u(s.asset));
      if(recent?.job){a.job=recent.job;a.artifact=recent.job.result?.native_asset_id?recent.job.result:null;}
    }catch{ /* old project may not have advanced records */ }
  }
  async function change(s,api,element,show){
    const a=state(s);const kind=element.dataset.featureAdvancedChange;
    if(kind==='engine') {a.engine=element.value;a.plan=null;a.recipe=null;a.draft=null;a.joinPlan=null;a.joinDraft=null;a.joined=null;a.error=null;show();return true;}
    if(kind==='joinDecision'){
      a.joinDecision=element.value;a.joinPlan=null;a.joinDraft=null;a.joined=null;a.joinContract=null;
      if(a.joinDecision && a.joinDecision!==s.asset){
        try{a.joinContract=await api('/contracts?asset_id='+u(a.joinDecision)+'&version_id=base');}
        catch(ex){a.error='预测表字段契约读取失败：'+ex.message;}
      }
      show();return true;
    }
    if(kind==='secondary'){
      a.secondary=element.value;a.secondaryContract=null;a.plan=null;a.recipe=null;a.draft=null;
      if(a.secondary){try{a.secondaryContract=await api('/contracts?asset_id='+u(a.secondary)+'&version_id=base');}
        catch(ex){a.error='关联表契约读取失败：'+ex.message;}}
      show();return true;
    }
    return false;
  }
  const required=(data,key)=>{const v=String(data.get(key)||'');if(!v)throw Error('缺少字段：'+key);return v;};
  async function submit(s,api,form,show){
    const a=state(s);
    if(form.id==='tp-adv-join-form'){
      const data=new FormData(form);
      try{
        const selected=data.getAll('chosen').map(String);
        if(!selected.length||selected.length>60)throw Error('请选择 1—60 个高级候选列');
        const plan={advanced_job_id:a.job?.job_id,decisions_asset_id:String(data.get('decision_asset')||''),decisions_version_id:'base',
          decision_key_field_id:required(data,'decision_key'),prediction_time_field_id:required(data,'prediction_time'),selected_columns:selected,
          max_rows:30000,max_memory_mb:512};
        a.joinPlan=await api('/advanced/join/validate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(plan)});
        a.joinDraft={...plan,selected_columns:selected};a.error=null;
      }catch(ex){a.joinPlan=null;a.error=ex.message;}
      show();return true;
    }
    if(form.id==='tp-adv-eval-form'){
      const data=new FormData(form),contract=(a.joinDecision||s.asset)===s.asset?s.contract:a.joinContract;
      try{
        if(!a.joined||!contract||contract.state!=='confirmed')throw Error('请先物化预测实例并确认字段契约');
        const slice=String(data.get('slice')||'');const model=String(data.get('model')||'both');
        const task={task_spec_version:1,task_id:'advanced_'+a.joined.job_id,mode:'supervised',
          task_type:String(data.get('task_type')),entity_field_ids:contract.entity_field_ids||[],
          prediction_time_field_id:a.joinDraft.prediction_time_field_id};
        task.label_field_id=required(data,'label');task.label_available_at_field_id=required(data,'label_available');
        task.split_kind='time';task.n_splits=3;task.purge_rows=0;
        a.evalJob=await api('/advanced/join/evaluations',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
          join_job_id:a.joined.job_id,task,models:model==='both'?['linear','forest']:[model],
          slice_field_ids:slice?[slice]:[],min_relative_improvement:0.01,
          request_id:(globalThis.crypto?.randomUUID?.()||String(Date.now()))})});a.error=null;
      }catch(ex){a.error=ex.message;}show();return true;
    }
    if(form.id!=='tp-fl2-advanced-form')return false;
    const data=new FormData(form);
    a.draft=Object.fromEntries(data.entries());a.draft.adv_values=data.getAll('adv_values').map(String);
    try{
      const value_field_ids=data.getAll('adv_values').map(String);
      if(!value_field_ids.length||value_field_ids.length>8)throw Error('请选择 1 至 8 个测点字段');
      const limits={max_rows:Number(required(data,'max_rows')),
        max_features:Number(required(data,'max_features')),max_seconds:Number(required(data,'max_seconds'))};
      const recipe=a.engine==='tsfresh'?{
        engine:'tsfresh',asset_id:s.asset,version_id:s.version||'base',
        sample_id_field_id:required(data,'sample'),event_time_field_id:required(data,'event'),
        available_at_field_id:required(data,'available'),prediction_time_field_id:required(data,'cutoff'),
        value_field_ids,preset:required(data,'preset'),limits} : {
        engine:'featuretools',decisions_asset_id:s.asset,decisions_version_id:s.version||'base',
        events_asset_id:required(data,'secondary'),events_version_id:'base',
        decision_id_field_id:required(data,'decision_id'),
        decision_entity_field_id:required(data,'decision_entity'),
        prediction_time_field_id:required(data,'cutoff'),event_entity_field_id:required(data,'event_entity'),
        event_time_field_id:required(data,'event'),event_available_at_field_id:required(data,'available'),
        event_value_field_ids:value_field_ids,primitives:[required(data,'primitive')],limits};
      a.plan=await api('/advanced/validate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(recipe)});
      a.recipe=recipe;a.error=null;
    }catch(ex){a.plan=null;a.recipe=null;a.error=ex.message;}
    show();return true;
  }
  async function click(s,api,element,show){
    const a=state(s),act=element.dataset.featureAdvanced;
    if(act==='confirm'){
      if(!a.capabilities?.engines?.[a.engine])throw Error('此高级算法尚未安装，不能提交计算');
      if(!a.recipe||!a.plan)throw Error('请先完成字段与预算检查');
      a.job=await api('/advanced/tool-call',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
        tool_id:'feature.advanced.submit',recipe:a.recipe,request_id:(globalThis.crypto?.randomUUID?.()||String(Date.now())),confirm:true})});
      a.plan=null;a.artifact=null;a.error=null;show();
      return true;
    }
    if(act==='join-confirm'){
      if(!a.joinDraft||!a.joinPlan)throw Error('请先校验高级候选回接计划');
      a.joinJob=await api('/advanced/join/jobs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
        plan:a.joinDraft,confirm:true,request_id:(globalThis.crypto?.randomUUID?.()||String(Date.now()))})});
      a.joinPlan=null;a.joined=null;a.error=null;show();return true;
    }
    if(act==='join-refresh'){
      if(!a.joinJob)return true;
      a.joinJob=await api('/runs/'+u(a.joinJob.job_id));
      if(a.joinJob.status==='succeeded')a.joined=await api('/advanced/join/results/'+u(a.joinJob.job_id));
      show();return true;
    }
    if(act==='eval-refresh'){
      if(!a.evalJob)return true;
      a.evalJob=await api('/runs/'+u(a.evalJob.job_id));
      if(a.evalJob.status==='succeeded')a.evalReport=await api('/advanced/join/evaluations/'+u(a.evalJob.job_id));
      show();return true;
    }
    if(act==='refresh'){
      if(!a.job)return true;
      a.job=await api('/advanced/jobs/'+u(a.job.job_id));
      if(a.job.status==='succeeded')a.artifact=await api('/advanced/artifacts/'+u(a.job.job_id));
      show();return true;
    }
    return false;
  }
  return {render,load,change,submit,click};
})();
window.TimePilotFeatureViews.advanced=(s)=>window.TimePilotFeatureAdvanced.render(s);
