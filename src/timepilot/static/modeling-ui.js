/* v0.40.3: signed prediction query, sampled row selection and historical input edges. */
(() => {
  'use strict';
  const E=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const U=encodeURIComponent;
  const json=(method,body)=>({method,headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const post=x=>json('POST',x);
  const sel=(items,choice,empty='请选择')=>`<option value="">${E(empty)}</option>`+(items||[]).map(x=>`<option value="${E(x)}" ${x===choice?'selected':''}>${E(x)}</option>`).join('');
  const btn=(action,title,primary=false,extra='')=>`<button type="button" class="button${primary?' button-primary':''}" data-modeling="${E(action)}" ${extra}>${E(title)}</button>`;
  const F=(name,label,value='',type='text',extra='')=>`<label class="field"><span>${E(label)}</span><input name="${E(name)}" type="${type}" value="${E(value??'')}" ${extra}></label>`;
  let ctx,pid,aid,source,suggestions,active,selectedKind='continuous_forecast';
  let businessGoal='', selectedGeneral='', generalTarget='', generalFeatures=[],requestGeneration=0;
  let savedTasks=[],savedModelTask=null,savedValidation='',savedRun=null,temporalEvidence=null,savedExecution=null,savedDiagnostics=null,savedRunSteps=null,savedLineage=null,savedWindowLineage=null,savedPredictionQuery=null,temporalBudgetProfile='15m',nativePredictionAssetId=null;
  const taskBase=()=>'/projects/'+U(pid)+'/model-tasks';
  const base=()=>'/projects/'+U(pid)+'/modeling';
  const slot=()=>document.getElementById('tp-modeling-body');
  function status(message,kind=''){const node=document.getElementById('tp-modeling-status');if(node){node.textContent=message;node.dataset.kind=kind;}}
  function shell(){
    document.getElementById('tp-modeling-dialog')?.remove();
    document.body.insertAdjacentHTML('beforeend',`<div class="tp-modeling-backdrop" id="tp-modeling-dialog" role="dialog" aria-modal="true" aria-labelledby="tp-modeling-title">
      <section class="tp-modeling-panel"><header class="tp-modeling-header"><div><h2 id="tp-modeling-title">新建预测任务</h2><p>先确定预测目标，再选择可用输入；按任务类型展示必要配置。</p></div>${btn('close','关闭')}</header>
      <div class="tp-modeling-steps" aria-label="任务配置进度"><span>1 选择任务</span><span>2 确认 Y 与 X</span><span>3 检查并运行</span></div>
      <p id="tp-modeling-status" class="muted small" role="status"></p><div id="tp-modeling-body"></div></section></div>`);
  }
  async function open(opts){
    ctx=opts.ctx;pid=opts.projectId;aid=opts.assetId;
    source={view_id:opts.viewId||null,filters:opts.filters||[],selection_id:opts.selectionId||null,version_id:opts.versionId||'base'};
    active=null;selectedKind='continuous_forecast';businessGoal='';selectedGeneral='';generalTarget='';generalFeatures=[];savedModelTask=null;savedTasks=[];savedValidation='';savedRun=null;temporalEvidence=null;savedExecution=null;savedDiagnostics=null;savedLineage=null;savedWindowLineage=null;savedPredictionQuery=null;nativePredictionAssetId=null;temporalBudgetProfile='15m';const generation=++requestGeneration;shell();status('正在识别字段和时间分布…');
    try{const response=await ctx.req(base()+'/assets/'+U(aid)+'/suggestions?version_id='+U(source.version_id));
      if(generation!==requestGeneration)return;suggestions=response;renderSuggestions();status('已生成字段建议；预测目标和输入仍需你确认');
      try{const list=await ctx.req(taskBase());if(generation===requestGeneration){savedTasks=list.items||[];if(slot()?.querySelector?.('[data-testid="model-task-selector"]'))renderSuggestions();}}catch(_){/* Old local server remains usable */}}
    catch(err){status('无法读取数据建议：'+err.message,'error');}
  }
  async function openDraft(opts){
    ctx=opts.ctx;pid=opts.projectId;aid=opts.assetId;source=null;active=null;savedModelTask=null;savedRun=null;temporalEvidence=null;savedExecution=null;savedDiagnostics=null;savedLineage=null;savedWindowLineage=null;savedPredictionQuery=null;nativePredictionAssetId=null;temporalBudgetProfile='15m';businessGoal='';++requestGeneration;shell();
    try{active=await ctx.req(base()+'/drafts/'+U(opts.draftId));aid=active.asset_id;
      selectedKind=active.plan.task_kind;
      suggestions=await ctx.req(base()+'/assets/'+U(aid)+'/suggestions?version_id='+U(active.version_id));
      if(['review_required','ready'].includes(active.status))renderMapping();else renderPreflight();}
    catch(err){status(err.message,'error');}
  }
  const designer=()=>suggestions?.task_designer||{};
  const roleFor=name=>(designer().role_candidates||[]).find(x=>x.name===name)||{};
  const sourceCols=()=>suggestions?.profile?.fields?.map(x=>x.name)||[];
  function suggestedFeatures(target){
    return (designer().suggested_features||[]).filter(name=>name!==target);
  }
  function modeTitle(mode){
    return ({continuous_forecast:'时间序列预测',classification:'工序提前分类',
      tabular_regression:'一般数值预测',tabular_classification:'一般分类'})[mode]||mode;
  }
  function renderSuggestions(){
    const p=suggestions?.profile||{},rec=p.recommendation||{};
    const tasks=designer().tasks||[];
    slot().innerHTML=`<section class="tp-modeling-start" data-testid="model-task-selector">
      <div class="tp-modeling-source"><b>${E(p.source_rows)} 行 · ${E(p.fields?.length)} 列</b>
        <p>时间 ${E(rec.time||'未确认')} · 实体 ${E(rec.entity||'未确认')} · 频率 ${E(rec.frequency?.frequency||'未确认')}</p>
        <small>基于 ${E(p.sample_rows)} 行抽样给出建议；完整训练数据仍需单独预检查。</small></div>
      <label class="field"><span><b>你希望预测什么？</b>（可选）</span>
        <div class="tp-modeling-goal"><input name="business_goal" maxlength="180" placeholder="例如：预测明天的设备温度，或在下一工序前识别不良" value="${E(businessGoal)}" />
          ${btn('analyze-goal','更新建议')}</div></label>
      <h3>选择预测任务</h3>
      <div class="tp-modeling-task-grid">${tasks.map(t=>`<article class="tp-modeling-task" data-testid="model-task-${E(t.id)}">
          <div><b>${E(t.title)}</b><small>${E(t.subtitle)}</small><p>${E(t.explanation)}</p>
            <small>${t.route==='featurelab'?'使用现有 FeatureLab 监督评价':'使用模型工作台'} · ${t.runnable?'具备适用字段候选':'字段条件待确认'}</small></div>
          ${btn('choose-task','配置',true,`data-mode="${E(t.id)}"`)}</article>`).join('')}</div>
      <details><summary>查看字段识别依据与风险</summary><div class="tp-modeling-fields">
        ${(designer().role_candidates||[]).map(x=>`<div><b>${E(x.name)}</b><small>${E(({numeric:'数值',time:'时间',available_at:'数据可用时间',entity:'实体标识',label:'分类标签',category:'类别或文本'})[x.role]||x.role)} · ${E(x.reasons?.join('；'))}${x.warnings?.length?' · '+E(x.warnings.join('；')):''}</small></div>`).join('')}</div></details>
      ${savedTasks.length?`<details><summary>继续已保存的任务（${savedTasks.length}）</summary><div class="tp-modeling-fields">${savedTasks.map(x=>`<div><b>${E(x.name||x.task_id.slice(0,8))}</b><small>${E(x.target||"—")} · ${E(x.status)} · ${E(x.version_id)} · 修订 ${E(x.revision)}</small>${btn('resume-spec','继续配置',false,`data-task-id="${E(x.task_id)}"`)}</div>`).join('')}</div></details>`:''}
      <p class="muted small">所有推荐都需要人工确认；系统不会根据列名自动批准训练任务。</p>
    </section>`;
  }
  function featurePicker(fields,selected,target,kind){
    const reserved=new Set([target]);
    const recommended=new Set(suggestedFeatures(target));
    const usable=fields.filter(name=>!reserved.has(name));
    return `<div class="tp-modeling-feature-head"><div><strong>自变量 X</strong> <small data-modeling-feature-count>已选择 ${selected.length} 项</small></div>
      <input type="search" data-modeling-search placeholder="搜索输入字段" aria-label="搜索输入字段" /></div>
      <div class="tp-modeling-feature-tools">${btn('select-recommended','采用安全数值候选')}${btn('clear-features','清空')}</div>
      <div class="tp-modeling-feature-grid" data-modeling-feature-list>
        ${usable.map(name=>{const info=roleFor(name),advised=recommended.has(name),warn=(info.warnings||[]).join('；');
          const blocked=['time','available_at','entity','label','category'].includes(info.role) || (info.warnings||[]).some(w=>w.includes('事后'));
          const checked=selected.includes(name) && !blocked;return `<label data-feature-name="${E(name.toLowerCase())}" class="${blocked?'is-blocked':(advised?'is-recommended':'is-review')}">
          <input name="observed_features" type="checkbox" value="${E(name)}" ${checked?'checked':''} ${blocked?'disabled':''} />
          <span><strong>${E(name)}</strong><small>${blocked?'不允许直接使用':(advised?'推荐候选':'需核实')}${warn?' · '+E(warn):''}</small></span></label>`;}).join('')}
      </div><p class="muted small">${kind==='continuous_forecast'?'只有在历史时刻已观测且可用的 X 才能用于预测；未知未来协变量不得直接输入。':'必须确认每个 X 在实际判断时点已经存在；标签和事后结果不得作为输入。'}</p>`;
  }
  function renderGeneralTask(mode){
    const switched=selectedGeneral!==mode;
    if(switched){generalTarget='';generalFeatures=[];savedValidation='';}
    selectedGeneral=mode;
    const cols=sourceCols();
    const preferred=mode==='tabular_regression'?designer().targets?.preferred_numeric:designer().targets?.preferred_label;
    if(!cols.includes(generalTarget))generalTarget=preferred||'';
    if(switched&&generalTarget)generalFeatures=suggestedFeatures(generalTarget).slice(0,32);
    generalFeatures=generalFeatures.filter(x=>cols.includes(x)&&x!==generalTarget&&roleFor(x).suggest_x);
    slot().innerHTML=`<form id="tp-modeling-general-form" class="tp-modeling-form" data-testid="general-model-form">
      <div class="tp-modeling-mode-header"><div><h3>${E(modeTitle(mode))}</h3><p class="muted small">对每行样本进行监督学习评价，可使用现有 FeatureLab 的线性/随机森林基线。</p></div>${btn('back','返回任务')}</div>
      <div class="grid grid-2"><label class="field"><span><b>因变量 Y</b>（要预测的结果）</span><select name="target" required>${sel(cols,generalTarget,'请选择目标字段')}</select></label>
        <label class="field"><span>验证方式</span><select name="split_kind">
           ${mode==='tabular_regression'?'<option value="kfold">独立样本 K 折</option>':'<option value="stratified">独立样本分层 K 折</option>'}
           <option value="group">分组 K 折（仅设备隔离）</option><option value="time">单次时间留出</option>
           <option value="rolling">滚动时间多折</option><option value="month">跨月份前向留出</option>
           <option value="device">跨设备 + 时间留出</option>
          </select></label></div>
      <div class="grid grid-2"><label class="field"><span>分组列（仅分组验证必需）</span><select name="group_column">${sel(cols,(savedModelTask?.spec?.time?.entity_columns||[])[0]||suggestions?.profile?.recommendation?.entity||'','选择设备/批次')}</select></label><label class="field"><span>日历时间列（仅时间验证必需）</span><select name="time_column">${sel(cols,savedModelTask?.spec?.time?.event_time_column||suggestions?.profile?.recommendation?.time||'','选择时间')}</select></label></div>
      <div class="grid grid-2">
        ${F('folds','验证折数（2–8）',savedModelTask?.spec?.validation?.folds??3,'number','min="2" max="8"')}
        ${F('gap','时间隔离间隔（独立时间点数量）',savedModelTask?.spec?.validation?.gap??0,'number','min="0" max="10000"')}
      </div>
      <details class="tp-modeling-advanced" data-testid="time-holdout-contract"><summary>按时间留出：数据可用性与标签成熟（选择时间验证时必填）</summary>
        <div class="grid grid-2">
          <label class="field"><span>特征发布时刻字段</span><select name="feature_available_at_column">${sel(cols,savedModelTask?.spec?.time?.feature_available_at_column||'','选择 X 可用时间')}</select></label>
          <label class="field"><span>标签成熟时刻字段</span><select name="label_available_at_column">${sel(cols,savedModelTask?.spec?.time?.label_available_at_column||'','选择标签可用时间')}</select></label>
          <label class="field"><span>业务决策时刻（可选，默认事件时刻）</span><select name="decision_time_column">${sel(cols,savedModelTask?.spec?.time?.decision_time_column||'','默认事件时刻')}</select></label>
          ${F('timezone','数据原始时区（IANA）',savedModelTask?.spec?.time?.timezone||'Asia/Shanghai','text','required placeholder="Asia/Shanghai"')}
        </div><p class="muted small">时间留出同时检查 X 在决策时刻是否已发布、训练标签在切分时点是否成熟，未填写会阻止审批。</p></details>
      <div class="tp-modeling-feature-shell">${featurePicker(cols,generalFeatures,generalTarget,mode)}</div>
      <details class="tp-modeling-advanced" open data-testid="per-x-availability"><summary>逐 X 发布时间账本（时间/设备外推时核验）</summary>
        <p class="muted small">选择真实发布时间列；未选时使用上面的公共发布时间列。发布延迟推断会在结果中标记为业务假设。</p>
        <div data-modeling-x-availability></div>
      </details>
      ${mode==='tabular_classification'?`<div class="grid grid-2" data-testid="model-label-mapping">
         ${F('positive_values','正类 / 不良标签值（逗号分隔）',(savedModelTask?.spec?.target?.positive_values||[]).join(','),'text','required placeholder="例如 NG,1"')}
         ${F('negative_values','负类 / 合格标签值（逗号分隔）',(savedModelTask?.spec?.target?.negative_values||[]).join(','),'text','required placeholder="例如 OK,0"')}
       </div>`:''}
      <details class="tp-modeling-advanced"><summary>数据与模型评价说明</summary><p class="muted small">静态 K 折只适合样本确实独立的情况。重复设备、相邻批次和随时间变化的业务数据需要分组或时间切分，防止信息泄漏。</p><p class="muted small">已确认的配置可直接创建正式模型评价任务，与常数基线、线性模型和随机森林在相同数据分折上比较。需要高级特征工程时可继续进入 FeatureLab。</p></details>
      <label class="check-row"><input type="checkbox" name="confirm_roles" required> 已核对 Y 的业务含义。</label>
      <details class="tp-modeling-advanced"><summary>可选：保存时同步完成配置审批</summary>
        <label class="check-row"><input type="checkbox" name="confirm_availability"> 已核查每项 X 在预测决策时点可以取得。</label>
        <label class="check-row"><input type="checkbox" name="confirm_split"> 已核查分组/时间切分没有训练和验证泄漏。</label>
      </details>
      <div class="tp-modeling-actions">${btn('back','返回任务')}<button class="button button-primary" type="submit">保存任务并检查</button></div>
    </form>`;
    const split=slot()?.querySelector('[name=split_kind]');
    if(split&&savedValidation){const map={kfold:'kfold',stratified_kfold:'stratified',group_kfold:'group',temporal_holdout:'time',rolling_origin:'rolling',leave_month_out:'month',leave_device_out:'device'};split.value=map[savedValidation]||split.value;}
    refreshFeatureCount();refreshXAvailability();
  }
  function refreshFeatureCount(){
    const f=slot()?.querySelector('form');if(!f)return;
    const count=f.querySelectorAll('[name=observed_features]:checked').length;
    const el=f.querySelector('[data-modeling-feature-count]');if(el)el.textContent=`已选择 ${count} 项`;
  }
  function refreshXAvailability(){
    const f=slot()?.querySelector?.('#tp-modeling-general-form');
    const holder=f?.querySelector?.('[data-modeling-x-availability]');if(!holder)return;
    const old={};
    holder.querySelectorAll?.('[data-feature-availability]').forEach(x=>{old[x.dataset.column]=x.value;});
    const picked=[...f.querySelectorAll('[name=observed_features]:checked')].map(x=>x.value);
    const cols=sourceCols();
    holder.innerHTML=picked.length?picked.map(col=>{
      const saved=savedModelTask?.spec?.features?.find(x=>x.column===col)?.available_at_column;
      return `<label class="field"><span>${E(col)} · X 发布时刻</span>
        <select name="x_available_at" data-feature-availability data-column="${E(col)}">
          ${sel(cols.filter(x=>x!==col),old[col]??saved??'','使用公共发布时间')}</select></label>`;
    }).join(''):'<p class="muted small">请先选择自变量 X。</p>';
  }

  function featureAction(action){
    const f=slot()?.querySelector('form');if(!f)return;
    const target=f.querySelector('[name=target]')?.value;
    const recommended=new Set(suggestedFeatures(target));
    f.querySelectorAll('[name=observed_features]').forEach(el=>{
      if(action==='clear-features')el.checked=false;
      else if(action==='select-recommended')el.checked=!el.disabled&&recommended.has(el.value);
    });refreshFeatureCount();refreshXAvailability();
  }
  function renderMapping(){
    const rec=suggestions?.profile?.recommendation||{},cols=suggestions?.profile?.fields?.map(f=>f.name)||[];
    const persisted=savedModelTask?.spec;
    const m=active?.mapping||(persisted?{target:persisted.target.column,time:persisted.time.event_time_column,entity:persisted.time.entity_columns[0]||null,observed_features:persisted.features.map(f=>f.column),available_at:persisted.time.feature_available_at_column,timezone:persisted.time.timezone,availability_policy:persisted.time.feature_available_at_column?'source_column':persisted.time.publication_lag_days!=null?'declared_lag':'unconfirmed',publication_lag_days:persisted.time.publication_lag_days}:{});
    const p=active?.plan||(persisted?{task_kind:persisted.task_kind,horizon_steps:persisted.target.horizon_steps,frequency:persisted.time.frequency,feature_policy:persisted.features.length?'historical_observed':'target_history_only'}:{});
    selectedKind=active?.plan?.task_kind||selectedKind;
    const features=m.observed_features||[];
    const event=m.event||{},regime=m.regime||{},sampleFrequency=rec.frequency?.frequency;
    const res=p.resampling||{},mechanism=p.mechanism||{},cl=p.classification||{};
    const name=active?.name|| (selectedKind==='classification'?'生产质量风险分类':'工业时序预测');
    const time=m.time??rec.time??'',entity=m.entity??rec.entity??'',
      target=m.target??(selectedKind==='classification'?(designer().targets?.preferred_label||''):
        (designer().targets?.preferred_numeric||rec.target||''));
    const unit=m.target_unit??((target.match(/[（(]([^()（）]+)[）)]/)||[])[1]||'');
    slot().innerHTML=`<form id="tp-modeling-form" class="form tp-modeling-form">
      <div class="tp-modeling-mode-header"><div><h3>${E(modeTitle(selectedKind))}</h3><p class="muted small">确认业务目标和输入变量，然后只设置当前模型需要的参数。</p></div>${btn('back','重新选择任务')}</div>
      <div class="grid grid-2 tp-modeling-essential" data-testid="modeling-essential-fields">
        ${F('name','任务名称',name,'text','required maxlength="120"')}
        <label class="field"><span>任务类型</span><select name="task_kind"><option value="continuous_forecast" ${selectedKind==='continuous_forecast'?'selected':''}>时间序列预测</option><option value="classification" ${selectedKind==='classification'?'selected':''}>工序质量二分类</option></select></label>
        <label class="field"><span><b>因变量 Y</b>（预测目标 / 质量标签）</span><select name="target" required>${sel(cols,target,'确认业务目标字段')}</select></label>
        <label class="field"><span>采集时间 / 判断节点时间</span><select name="time" required>${sel(cols,time,'确认真实日历时间')}</select></label>
        <label class="field"><span>设备或序列标识</span><select name="entity">${sel(cols,entity,'只有一条序列')}</select></label>
        <label class="field"><span>时间粒度</span><select name="frequency">${['h','D','MS','min','5min','s'].map(x=>`<option value="${x}" ${x===(p.frequency||((sampleFrequency==='min'||sampleFrequency==='5min')?'h':sampleFrequency)||'h')?'selected':''}>${E(({h:'小时',D:'天',MS:'月',min:'分钟（需重采样）','5min':'5分钟（需重采样）',s:'秒（需重采样）'})[x])}</option>`).join('')}</select></label>
        <label class="field" data-task-only="continuous_forecast"><span>向未来预测多远</span><input name="horizon_steps" type="number" min="1" max="24" value="${E(p.horizon_steps??3)}" /></label>
      </div>
      <details class="tp-modeling-advanced" ${m.availability_policy==='source_column'||m.availability_policy==='declared_lag'?'':'open'} data-testid="availability-settings"><summary>数据可用时间（运行前必须确认）</summary>
        <div class="grid grid-2">
          <label class="field"><span>可用时间来源</span><select name="availability_policy"><option value="unconfirmed">待确认</option><option value="source_column" ${m.availability_policy==='source_column'?'selected':''}>原始可用时间字段</option><option value="declared_lag" ${m.availability_policy==='declared_lag'?'selected':''}>声明发布延迟</option></select></label>
          <label class="field" data-dependency="source_column"><span>可用时间字段</span><select name="available_at">${sel(cols,m.available_at,'请选择')}</select></label>
          <label class="field" data-dependency="declared_lag"><span>发布延迟（天）</span><input name="publication_lag_days" type="number" min="0" max="3650" value="${E(m.publication_lag_days??0)}"></label>
          ${F('timezone','原始数据时区',m.timezone||'Asia/Shanghai','text','required')}
        </div><p class="muted small">预测时刻尚未产生的数据不得进入模型；不确认可用时间将阻断训练。</p></details>
      <details class="tp-modeling-advanced"><summary>业务场景、目标单位和批次（可选）</summary><div class="grid grid-2">
        <label class="field"><span>业务任务类别</span><select name="task_family"><option value="equipment_state" ${(p.task_family||'equipment_state')==='equipment_state'?'selected':''}>设备状态 / 工艺质量</option><option value="demand_forecast" ${p.task_family==='demand_forecast'?'selected':''}>业务需求预测</option></select></label>
        ${F('target_unit','目标物理单位',unit,'text','maxlength="32" placeholder="例如 ℃"')}
        <label class="field" data-task-only="classification"><span>批次标识（可选）</span><select name="batch">${sel(cols,m.batch||'','不检查跨批次泄漏')}</select></label>
      </div></details>
      <div class="tp-modeling-feature-shell">${featurePicker(cols.filter(x=>![time,entity,m.available_at,cl.label_available_at,m.batch].includes(x)),features,target,selectedKind)}</div>
      <label class="field" data-task-only="continuous_forecast"><span>预测输入模式</span><select name="feature_policy"><option value="target_history_only">仅目标历史（单变量基线）</option><option value="historical_observed" ${p.feature_policy==='historical_observed'?'selected':''}>目标历史 + 已知传感器/事件/工况</option></select></label>
      <details ${res.enabled?'open':''} data-task-only="continuous_forecast" class="tp-modeling-advanced"><summary>分钟数据聚合到小时（逐桶审计）</summary>
        <label class="check-row"><input type="checkbox" name="resample_enabled" ${res.enabled?'checked':''}>启用显式重采样（不会填补缺失时间点）</label>
        <div class="grid grid-2"><label class="field"><span>原始周期</span><select name="source_period_seconds">${[1,30,60,300,900].map(v=>`<option value="${v}" ${v===(res.source_period_seconds||60)?'selected':''}>${v} 秒</option>`).join('')}</select></label>
        <label class="field"><span>聚合指标</span><select name="aggregation">${['mean','last','min','max'].map(v=>`<option value="${v}" ${v===(res.aggregation||'mean')?'selected':''}>${E(({mean:'均值',last:'最后观测',min:'最小值',max:'最大值'})[v])}</option>`).join('')}</select></label>
        ${F('min_coverage','最低桶覆盖率（0.5–1.0）',res.min_coverage??1,'number','min="0.5" max="1" step="0.05"')}</div>
        <p class="muted small">源时间按 UTC 小时边界分桶；桶结束前的数据不可作为完整桶读数使用。出现缺桶会阻断。</p></details>
      <details class="tp-modeling-advanced"><summary>维护事件与工况（高级设置）</summary><div class="grid grid-2">
        <label class="field"><span>维护事件列</span><select name="event_column">${sel(cols,event.column,'不使用事件')}</select></label>
        ${F('event_truthy','事件触发值（逗号分隔）',(event.truthy_values||[]).join(','),'text','placeholder="例如 检修,更换滤芯"')}
        <label class="field"><span>工况字段</span><select name="regime_column">${sel(cols,regime.column,'不使用工况')}</select></label>
        <label class="field"><span>训练是否排除跨维护事件窗口</span><select name="event_reset_policy"><option value="indicator_only">保留事件信号</option><option value="exclude_cross_event_windows" ${event.reset_policy==='exclude_cross_event_windows'?'selected':''}>排除跨事件窗口</option></select></label>
      </div><p class="muted small">工况切换若发生在同一聚合桶内部，将阻止聚合；不将未来事件当作已知特征。</p></details>
      <details data-task-only="continuous_forecast" class="tp-modeling-advanced"><summary>预测步数、验证与机理约束</summary><div class="grid grid-2">
        ${F('validation_windows','滚动验证窗口',p.validation_windows??3,'number','min="1" max="24"')}
        ${F('min_history','最低历史点数',p.min_history??24,'number','min="2" max="10000"')}
        <label class="field"><span>主要指标</span><select name="primary_metric"><option value="MAE">MAE</option><option value="WAPE" ${p.primary_metric==='WAPE'?'selected':''}>WAPE</option></select></label>
        ${F('target_min','目标物理下界',mechanism.target_min??'','number','step="any"')}
        ${F('target_max','目标物理上界',mechanism.target_max??'','number','step="any"')}
        ${F('max_step_change','最大单步变化',mechanism.max_step_change??'','number','step="any" min="0"')}
        <label class="field"><span>物理约束执行</span><select name="mechanism_enforcement"><option value="block">超界阻断</option><option value="warn" ${mechanism.enforcement==='warn'?'selected':''}>告警但允许运行</option></select></label>
      </div><p class="muted small">上下界与变化率由工艺工程师确认；机理证据独立保存，目前只执行显式数值约束。</p></details>
      <details data-task-only="classification" open><summary>分类标签、判断节点与验收</summary><div class="grid grid-2">
        <label class="field"><span>验证协议</span><select name="validation_strategy">
          <option value="time_forward" ${p.validation_strategy!=='leave_device_out'?'selected':''}>同设备时间前向验证</option>
          <option value="leave_device_out" ${p.validation_strategy==='leave_device_out'?'selected':''}>跨设备留出 + 时间前向（未见设备零样本）</option>
        </select></label>
        ${F('classification_folds','验证折数 / 设备留出组数',p.validation_windows??2,'number','min="2" max="8"')}
        ${F('positive_values','不良标签原始取值',cl.positive_values?.join(',')||'NG','text','required')}
        ${F('negative_values','合格标签原始取值',cl.negative_values?.join(',')||'OK','text','required')}
        <label class="field"><span>标签成熟时间字段</span><select name="label_available_at">${sel(cols,cl.label_available_at||'','选择实际标签确认时间')}</select></label>
        ${F('label_delay_seconds','标签延迟秒数（没有时间列时填）',cl.label_delay_seconds??'','number','min="0"')}
        ${F('decision_stage','判断节点',cl.decision_stage||'节点A')}
        ${F('min_recall','最低目标召回率',cl.min_recall??0.9,'number','min="0.01" max="1" step="0.01"')}
      </div><p class="muted small">跨设备留出要求至少与折数相同数量的不同设备：训练折彻底排除留出设备，验证折按时间前向。独立测试仍为后续时段的时间测试，不能将其误记为完全未见设备测试；零样本设备外推目前仅支持二分类。</p></details>
      <div class="tp-modeling-warning">训练数据独立冻结；模型无法调用未来尚未获得的传感器、质量标签或维护结果。预检查将展示具体阻断原因。</div>
      <label class="check-row"><input type="checkbox" name="confirm_scope" required checked>确认训练使用冻结数据版本和明细级筛选，图表样本与分页不限制训练行数。</label>
      <div class="tp-modeling-actions">${btn('back','返回任务')}<button type="submit" class="button button-primary">保存并检查数据</button></div></form>`;
    const form=document.getElementById('tp-modeling-form');
    if(m.availability_policy==='unconfirmed'||!m.availability_policy)form.querySelector('[name=availability_policy]').value='unconfirmed';
    for(const field of cols){if(field===target||field===time)form.querySelectorAll(`input[name=observed_features]`).forEach(x=>{if(x.value===field)x.disabled=true;});}
    form.querySelector('[name=feature_policy]').value=p.feature_policy||'target_history_only';
    updateVisibility();refreshFeatureCount();
  }
  function updateVisibility(){
    const f=document.getElementById('tp-modeling-form');if(!f)return;
    const kind=f.querySelector('[name=task_kind]').value;
    const availability=f.querySelector('[name=availability_policy]').value;
    f.querySelectorAll('[data-task-only]').forEach(el=>{el.hidden=el.dataset.taskOnly!==kind;});
    f.querySelectorAll('[data-dependency]').forEach(el=>{el.hidden=el.dataset.dependency!==availability;});
  }
  function change(event){
    if(event.target?.matches?.('[data-modeling-search]')){
      const query=String(event.target.value||'').toLocaleLowerCase();
      slot()?.querySelectorAll('[data-feature-name]').forEach(x=>{x.hidden=!x.dataset.featureName.includes(query);});
      return true;
    }
    if(event.target?.name==='observed_features' && event.target.closest?.('#tp-modeling-general-form, #tp-modeling-form')){
      if(event.target.checked&&event.target.closest('#tp-modeling-form')){
        const mode=event.target.closest('form')?.querySelector('[name=task_kind]')?.value;
        const policy=event.target.closest('form')?.querySelector('[name=feature_policy]');
        if(mode==='continuous_forecast'&&policy?.value==='target_history_only')policy.value='historical_observed';
      }refreshFeatureCount();refreshXAvailability();return true;}
    if(event.target?.name==='target' && event.target.closest?.('#tp-modeling-general-form')){
      const form=event.target.form;const picked=[...form.querySelectorAll('[name=observed_features]:checked')].map(x=>x.value);
      generalTarget=event.target.value;generalFeatures=picked.filter(x=>x!==generalTarget);
      renderGeneralTask(selectedGeneral);return true;
    }
    if(event.target.closest?.('#tp-modeling-form') && ['availability_policy','task_kind','time','target','entity'].includes(event.target.name)){
      const f=document.getElementById('tp-modeling-form');
      if(event.target.name==='task_kind' && event.target.value==='classification') f.querySelector('[name=task_family]').value='equipment_state';
      const reserved=['time','target','entity','available_at','batch','label_available_at'].map(n=>f.querySelector('[name='+n+']')?.value).filter(Boolean);
      f.querySelectorAll('[name=observed_features]').forEach(field=>{field.disabled=reserved.includes(field.value);if(field.disabled)field.checked=false;});
      updateVisibility();refreshFeatureCount();return true;
    }
    return false;
  }
  const executionEndpoint=()=>taskBase()+'/'+U(savedModelTask.task_id)+'/execution';
  function executionMetrics(execution){
    if(!execution?.metric_sets)return '';
    const groups=Object.entries(execution.metric_sets);
    if(!groups.length)return '';
    return `<div data-testid="model-execution-metrics">${groups.map(([group,metrics])=>{
      const records=Object.entries(metrics||{}).flatMap(([name,values])=>
        values&&typeof values==='object'&&!Array.isArray(values)
          ? Object.entries(values).filter(([,v])=>v===null||typeof v==='number'||typeof v==='string').map(([metric,value])=>[name+'.'+metric,value])
          : (metrics[name]===null||typeof metrics[name]==='number'||typeof metrics[name]==='string')?[[name,values]]:[]);
      return `<section><strong>${E(group==='comparison'?'同折模型对照':group==='test'?'独立测试指标':'验证集指标')}</strong>
        <table><thead><tr><th>指标</th><th>数值</th></tr></thead><tbody>${records.map(([name,value])=>`<tr><td>${E(name)}</td><td>${E(value===null?'不可计算':value)}</td></tr>`).join('')}</tbody></table></section>`;
    }).join('')}</div>`;
  }
  function confusionMatrix(record,source){
    if(!record)return '';
    return `<div class="tp-modeling-confusion" data-testid="model-confusion-matrix"><p class="muted small">${E(source)} · 正类：1 / 不良，阈值 ${E(record.threshold??0.5)}</p>
      <table><thead><tr><th>实际 / 预测</th><th>负类 0</th><th>正类 1</th></tr></thead><tbody>
      <tr><th>负类 0</th><td>${E(record.tn)}</td><td>${E(record.fp)}</td></tr>
      <tr><th>正类 1</th><td>${E(record.fn)}</td><td>${E(record.tp)}</td></tr></tbody></table></div>`;
  }
  function diagnosticsPanel(diag){
    if(!diag)return '';
    if(diag.status!=='available')return `<div data-testid="model-diagnostics-panel"><p class="muted small">${E(diag.reason||diag.status)}</p>
       ${confusionMatrix(diag.aggregate_confusion_matrix,'原时序执行器的测试集聚合计数，缺少逐折证据')}</div>`;
    const headers=Object.keys(diag.fold_scores?.[0]||{});
    const scoreRows=(diag.fold_scores||[]).map((row,i)=>`<tr><td>${i+1}</td>${headers.map(key=>`<td>${E(row[key]??'不可计算')}</td>`).join('')}</tr>`);
    const foldTable=headers.length?`<details open><summary>逐折指标 · 相同数据和分折</summary><table><thead><tr><th>折</th>${headers.map(h=>`<th>${E(h)}</th>`).join('')}</tr></thead><tbody>${scoreRows.join('')}</tbody></table></details>`:'';
    const diagnostics=diag.fold_diagnostics||[];
    const isClassification=diagnostics.some(x=>x.kind==='confusion_matrix');
    const errors=diagnostics.map(item=>`<tr>${['fold',...(isClassification?['tn','fp','fn','tp','evaluated_rows']:['mean_signed_error','p50_absolute_error','p90_absolute_error','under_prediction_count','over_prediction_count','evaluated_rows'])].map(key=>`<td>${E(item[key]??'—')}</td>`).join('')}</tr>`).join('');
    const columns=(isClassification?['折','TN','FP','FN','TP','验证量']:['折','平均有符号误差','绝对误差 P50','绝对误差 P90','低估量','高估量','验证量']);
    const errorTable=errors?`<details open><summary>${isClassification?'混淆矩阵':'误差分析'} · 验证集</summary><table><thead><tr>${columns.map(x=>`<th>${E(x)}</th>`).join('')}</tr></thead><tbody>${errors}</tbody></table>
      ${isClassification?confusionMatrix(diagnostics.at(-1),'末折验证集混淆矩阵'):''}</details>`:'';
    const importance=diag.feature_contribution||{};
    const contrib=importance.items?.length?`<details open><summary>特征贡献 · 末折置换法</summary><table><thead><tr><th>输入 X</th><th>主指标退化幅度</th></tr></thead><tbody>${importance.items.map(x=>`<tr><td>${E(x.feature)}</td><td>${E(x.delta_metric)}</td></tr>`).join('')}</tbody></table><p class="muted small">主指标 ${E(importance.primary_metric)}；固定种子仅置换 1 次，最多 12 个 X。该数值是预测关联证据，不能作为因果解释。</p></details>`:`<p class="muted small">特征贡献：${E(diag.selected_model===diag.best_observed_model?importance.status||'证据缺失':'仅对当前验证最优模型计算置换贡献')}</p>`;
    const slices=diag.slices?.length?`<details open><summary>BI 验证切片 · ${E(diag.selection?.dimension||'')}（${diag.slices.length} 组）</summary><table><thead><tr><th>设备/月度</th><th>验证行数</th><th>指标</th></tr></thead><tbody>${diag.slices.map(x=>`<tr><td>${E(x.value)}</td><td>${E(x.validation_rows)}</td><td>${E(JSON.stringify(x.metrics))}</td></tr>`).join('')}</tbody></table></details>`:diag.selection?.dimension?'<p class="muted small">该筛选条件没有满足最小 15 条验证记录的统计切片。</p>':'';
    return `<div data-testid="model-diagnostics-panel">
      <p class="muted small">指标来源：${E(diag.metric_provenance?.metric_scope)} · 模型 ${E(diag.selected_model)} · 当前任务修订 ${E(diag.task_revision)}；结果 SHA256 ${E(diag.result_sha256)}</p>
      ${foldTable}${errorTable}${contrib}${slices}
      <p class="muted small">切片仅聚合验证集预测记录，不更改训练范围；每模型每折可强制终止的计算预算，所有候选单线程，实际计算量仍可能不同。</p></div>`;
  }

  async function loadDiagnostics(model=null,dimension=null,value=null){
    model=model||savedExecution?.available_models?.[0]||'forest';
    if(!savedExecution?.diagnostics_available){savedDiagnostics=null;return;}
    const params=new URLSearchParams({model});
    if(dimension)params.set('dimension',dimension);
    if(value)params.set('value',value);
    savedDiagnostics=await ctx.req(executionEndpoint()+'/diagnostics?'+params.toString());
  }

  function predictionLineageMarkup(){
    return savedLineage?`<div data-testid="model-prediction-lineage">
        <p>血缘状态 ${E(savedLineage.status)} · 验证行 ${E(savedLineage.prediction_row_id)} · 源记录 ${E(savedLineage.selected_source_count??'未认证')} 条</p>
        ${savedLineage.selected_source_rows?.length?`<details><summary>已签名的源行 ID（最多显示 120 条）</summary><table><thead><tr><th>筛选行序号</th><th>版本绑定源行 ID</th></tr></thead><tbody>
        ${savedLineage.selected_source_rows.slice(0,120).map(x=>`<tr><td>${E(x.selected_row_position)}</td><td><code>${E(x.selected_row_id)}</code></td></tr>`).join('')}</tbody></table></details>`:''}
        ${savedLineage.source_page?.items?.length?`<details><summary>查看源数据字段值（当前页 ${E(savedLineage.source_page.items.length)} 条）</summary>
          ${savedLineage.source_page.items.map(x=>`<pre style="max-height:200px;overflow:auto;white-space:pre-wrap">${E(JSON.stringify({selected_row_position:x.selected_row_position,values:x.values},null,2))}</pre>`).join('')}
          <small>源记录来自已审核的 BI 数据版本，仅展示前 20 行；更多源行请分页调用受控接口。</small></details>`:''}
        <small>${E(savedLineage.source_identity_semantics||savedLineage.reason||'')} · 上传原文件的物理行号未认证</small></div>`:'';
  }
  function windowLineageMarkup(){
    if(!savedWindowLineage)return '';
    const w=savedWindowLineage;
    return `<section data-testid="model-window-lineage"><strong>历史输入窗口来源 · ${E(w.status)}</strong>
      <p class="muted small">折 ${E(w.fold)} · 历史观测 ${E(w.history_normalized_rows)} · 源记录关联 ${E(w.source_edge_total)} · 截止 ${E(w.cutoff_utc)}</p>
      <p class="muted small">签名索引 ${E(w.train_index_sha256)} · ${E(w.reason||'')} · 逐特征使用尚未认证</p>
      ${w.source_edges?.length?`<table><thead><tr><th>规范化观测</th><th>筛选源行</th><th>签名源行 ID</th></tr></thead><tbody>
        ${w.source_edges.map(x=>`<tr><td>${E(x.normalized_row_position)}</td><td>${E(x.selected_row_position)}</td><td><code>${E(x.selected_row_id)}</code></td></tr>`).join('')}
        </tbody></table>`:''}
      ${w.next_offset!==null&&w.next_offset!==undefined?btn('more-window-edges','继续读取历史源行'):''}</section>`;
  }
  function predictionQueryMarkup(q){
    if(!q)return '';
    const m=q.metrics||{};
    return `<div data-testid="model-prediction-query-result">
      <p class="muted small">经校验的 ${E(m.prediction_rows)} 条预测 · MAE ${E(m.mae)} · RMSE ${E(m.rmse)} · ${E(m.metric_scope)} · ${E(q.query_backend)} · 耗时 ${E(q.elapsed_ms??'—')}ms</p>
      <p class="muted small">该指标为筛选记录的逐行汇总，不直接作为模型选择分数。选中一行可查看原始数据和窗口来源。</p>
      <table><thead><tr><th>选中</th><th>模型</th><th>折</th><th>设备</th><th>时间</th><th>实际</th><th>预测</th><th>绝对误差</th></tr></thead><tbody>
      ${(q.items||[]).map(x=>`<tr data-prediction-row-id="${E(x.__tp_rowid)}"><td>${btn('select-prediction-row','追溯',false,`data-row-id="${E(x.__tp_rowid)}"`)}</td>
        <td>${E(x.model)}</td><td>${E(x.fold)}</td><td>${E(x.series_id)}</td><td>${E(x.event_time_utc)}</td>
        <td>${E(x.actual)}</td><td>${E(x.prediction)}</td><td>${E(x.absolute_error)}</td></tr>`).join('')}
      </tbody></table>${q.next_offset!==null?btn('more-prediction-query','加载下一页'):''}</div>`;
  }
  async function tracePredictionRow(value){
    savedLineage=await ctx.req(executionEndpoint()+'/predictions/lineage?row_id='+U(value));
    if(savedLineage.prediction_asset_sha256!==savedExecution?.prediction_asset?.manifest_sha256)throw new Error('预测资产版本已发生变化，请刷新任务');
    savedWindowLineage=null;
    const target=document.getElementById('tp-prediction-lineage-slot');
    if(target)target.innerHTML=predictionLineageMarkup()+windowLineageMarkup();
    const id=Number(value);
    const picker=slot()?.querySelector('[name=trace_prediction_row]');if(picker)picker.value=String(id);
    slot()?.querySelectorAll('[data-prediction-row-id]').forEach(row=>{
      row.classList.toggle('tp-selected-row',Number(row.dataset.predictionRowId)===id);
      row.setAttribute('aria-selected',String(Number(row.dataset.predictionRowId)===id));
    });
    // Preserve the mounted Perspective component and its current native filters.
    if(nativePredictionAssetId===savedExecution?.prediction_asset?.asset_id){
      try{const native=await window.TimePilotPerspectiveLoader.load();
        await native.focusPredictionRow?.(id);}catch(_){/* Source trace remains valid */}
    }
    status(savedLineage.status==='traced_to_selected_snapshot'?'来源行已核验':'来源无法唯一认证：'+savedLineage.status);
  }
  async function runPredictionQuery(offset=0){
    const root=document.getElementById('tp-prediction-query-controls');
    if(!root)throw new Error('查询配置未加载');
    const params=new URLSearchParams({offset:String(offset),limit:'50'});
    for(const [name,field] of [['model','query_model'],['fold','query_fold'],['month','query_month'],
                                 ['device','query_device'],['min_abs_error','query_min_error']]){
      const value=root.querySelector('[name='+field+']')?.value?.trim()||'';
      if(value)params.set(name,value);
    }
    const expectedRun=savedExecution?.run_id,expectedAsset=savedExecution?.prediction_asset?.manifest_sha256;
    const next=await ctx.req(executionEndpoint()+'/predictions/query?'+params.toString());
    if(savedExecution?.run_id!==expectedRun||next.manifest_sha256!==expectedAsset)throw new Error('预测结果已失效，刷新后重查');
    savedPredictionQuery=next;
    const panel=document.getElementById('tp-prediction-query-results');
    if(panel)panel.innerHTML=predictionQueryMarkup(savedPredictionQuery);
    status('已读取同一签名资产的筛选指标和逐样本预测');
  }

  function executionPanel(execution){
    if(!execution)return '';
    const current=execution.current&&execution.state==='succeeded';
    const report=current&&execution.report_available?`<a class="button" href="/api/v1${executionEndpoint()}/report" target="_blank" rel="noopener">导出评价摘要 MD</a>`:'';
    const ledger=current?execution.availability_ledger:null;
    const options=execution.available_models||['baseline','linear','forest'];
    const derived=current?execution.prediction_asset:null;
    const runBudget=execution.experiment;
    const stepList=savedRunSteps&&savedRunSteps.run_id===execution.run_id?savedRunSteps.items||[]:[];
    const runPanel=runBudget?`<details open data-testid="experiment-budget-panel"><summary>实验总预算与执行步骤</summary>
      <p class="muted small">预算 ${E(runBudget.budget?.run_wall_seconds_total)} 秒 · 剩余约 ${E(runBudget.remaining_wall_seconds)} 秒 · ${E(runBudget.budget?.budget_profile)} · ${E(runBudget.cancel_requested?'已请求取消':'执行状态 '+runBudget.state)}</p>
      <p class="muted small">当前尝试 ${E(runBudget.attempts)} 次；租约代次 ${E(runBudget.lease_epoch)}。预算从首次领取时开始计算，不含排队时间。</p>
      ${savedRunSteps?.worker_logs?.length?`<details data-testid="temporal-worker-logs"><summary>Worker 受限日志（脱敏、最多 1800 字符）</summary>
        ${savedRunSteps.worker_logs.map(x=>`<p>尝试 ${E(x.attempt)} · SHA256 ${E(x.log_sha256)}</p><pre style="white-space:pre-wrap;max-height:180px;overflow:auto">${E(x.tail)}</pre>`).join('')}</details>`:''}
      ${stepList.length?`<table><thead><tr><th>折</th><th>模型</th><th>状态</th><th>墙钟(s)</th><th>CPU(s)</th></tr></thead><tbody>${stepList.map(x=>`<tr><td>${E(x.fold)}</td><td>${E(x.model_id)}</td><td>${E(x.state)}</td><td>${E(x.wall_seconds)}</td><td>${E(x.cpu_seconds)}</td></tr>`).join('')}</tbody></table>`:''}
      <div class="action-row">${btn('refresh-steps','刷新步骤')}
      ${['queued','running','cancel_requested'].includes(execution.state)?btn('cancel-execution','取消训练'):''}</div>
    </details>`:'';
    const predictionPanel=derived?`<details open data-testid="model-predictions-asset"><summary>验证集逐样本预测 · ${E(derived.rows)} 行</summary>
      <p class="muted small">资产 ${E(derived.asset_id)} · 源 ${E(derived.source_sha256)} · 校验 ${E(derived.manifest_sha256)} · ${E(derived.perspective_format)}</p>
      <div class="action-row"><label class="field" style="max-width:200px">预测行号 <input name="trace_prediction_row" type="number" min="0" max="${E(Math.max(0,Number(derived.rows||1)-1))}" value="0"></label>${btn('trace-prediction','追溯所选预测行')}${btn('trace-history-window','核验历史输入窗口')}${btn('open-predictions','用原生 Perspective 查看与筛选')}
        <a class="button" href="/api/v1${executionEndpoint()}/predictions/rows?limit=100" target="_blank" rel="noopener">查看逐行证据 JSON</a></div>
      <section id="tp-prediction-query-controls" data-testid="model-prediction-query-controls">
        <p><strong>验证预测筛选与逐样本联动</strong></p>
        <div class="action-row">
          <label class="field">模型 <select name="query_model">${sel(options,null,'全部模型')}</select></label>
          <label class="field">折 <input name="query_fold" type="number" min="1" max="1000" placeholder="全部"></label>
          <label class="field">月份 <input name="query_month" type="month"></label>
          <label class="field">设备 <input name="query_device" type="text" maxlength="160" placeholder="全部"></label>
          <label class="field">最小绝对误差 <input name="query_min_error" type="number" min="0" step="any"></label>
          ${btn('query-predictions','按条件查询验证结果')}
        </div>
        <div id="tp-prediction-query-results">${predictionQueryMarkup(savedPredictionQuery)}</div>
      </section>
      <div id="tp-prediction-lineage-slot">${predictionLineageMarkup()}${windowLineageMarkup()}</div>
      <div id="tp-prediction-perspective" data-testid="prediction-perspective-panel" style="min-height:420px;max-height:70vh;overflow:auto"></div></details>`:'';
    return `<section class="tp-modeling-source" data-testid="unified-model-execution">
      <b>模型运行 · ${E(execution.state)}</b>
      <p class="muted small">${E(execution.executor)} · ${E(execution.validation_protocol)} · 数据版本 ${E(execution.data_version)} · 任务修订 ${E(execution.revision)}</p>
      ${current?`<p class="muted small">指标来源：${E(execution.metric_provenance?.metric_scope||'缺乏来源标识')} · 结果 SHA256：${E(execution.result_sha256)}</p>`:''}
      ${execution.state==='stale'?'<p class="helper warning">当前任务或来源已变化，历史指标不可继续使用。</p>':''}
      ${execution.state==='failed'?`<p class="helper warning">失败原因：${E(execution.failure_code||'unknown')} · ${E(execution.failure_reason||'请查阅运行日志')}</p>`:''}
      ${runPanel}
      ${current?executionMetrics(execution):''}
      ${ledger?`<details open data-testid="model-availability-ledger"><summary>逐 X 数据可用时间账本（${E(ledger.status)}）</summary>
        <p class="muted small">检查 ${E(ledger.rows)} 行 · 决策字段 ${E(ledger.decision_column)} · 标签来源 ${E(ledger.label_source?.kind)} · 时区 ${E(ledger.timezone)} · 假设数 ${E(ledger.assumption_count)}</p>
        ${ledger.assumption_count?'<p class="helper warning">部分时间来自人工申报延迟，未由原始发布时间逐行证实。</p>':''}
        <table><thead><tr><th>X</th><th>发布时间来源</th><th>审查行数</th><th>迟到行数</th></tr></thead><tbody>
          ${(ledger.features||[]).map(x=>`<tr><td>${E(x.feature)}</td><td>${E(x.source?.column||x.source?.kind)}</td><td>${E(x.checked_rows)}</td><td>${E(x.late_rows)}</td></tr>`).join('')}</tbody></table>
        ${ledger.status==='partial'?'<p class="helper warning">标签成熟证据未提供，仅完成 X 发布时间审查；折级成熟证据不能据此确认。</p>':''}
      </details>`:''}
      ${current&&execution.task_kind==='classification'&&['tn','fp','fn','tp'].every(x=>Number.isFinite(execution.metric_sets?.test?.[x]))
        ?confusionMatrix(execution.metric_sets.test,'原时序执行器独立测试集聚合计数'):''}
      ${current&&execution.diagnostics_available?`<div class="tp-modeling-diagnostic-controls" data-testid="model-diagnostic-controls">
        <label class="field">比较模型 <select name="diag_model">${options.map(m=>`<option value="${m}" ${m===(savedDiagnostics?.selected_model||options[0])?'selected':''}>${E(m)}</option>`).join('')}</select></label>
        <label class="field">BI 切片 <select name="diag_dimension"><option value="">全验证集</option><option value="device" ${savedDiagnostics?.selection?.dimension==='device'?'selected':''}>设备</option><option value="month" ${savedDiagnostics?.selection?.dimension==='month'?'selected':''}>月份</option></select></label>
        <label class="field">切片值（可不填） <input name="diag_value" maxlength="200" placeholder="例：2024-03" value="${E(savedDiagnostics?.selection?.value||'')}" /></label>
        ${btn('load-diagnostics','查看误差与特征贡献')}</div>${diagnosticsPanel(savedDiagnostics)}`:''}
      ${current&&!execution.diagnostics_available?'<p class="muted small">此执行器当前仅提供聚合指标，缺乏统一的逐折误差、特征贡献和 BI 切片证据。</p>':''}
      ${current?`<a class="button" href="/projects/${U(pid)}/bi/native/${U(aid)}">在原生 BI 查看同一数据资产</a>`:''}
      ${predictionPanel}
      ${report}<p class="muted small">评价证据 SHA256：${E(execution.evidence_sha256||'—')}。回顾性指标不代表生产收益。</p>
    </section>`;
  }
  function renderGeneralSaved(){
    if(!savedModelTask)return;
    const doc=savedModelTask;
    const state=doc.status||'review_required';
    const canRun=state==='approved'&&doc.effective_status!=='stale_source'&&
      !savedExecution?.run_id && !savedRun?.run_id;
    const summary=savedRun?.result?.summary||{};
    const metric=savedRun?.result?.primary_metric||'mae';
    const rows=savedRun?.state==='succeeded'&&savedRun?.effective_state!=='superseded'?Object.entries(summary).map(([name,metrics])=>
      `<tr><td>${E(({baseline:'常数基线',linear:'线性模型',forest:'随机森林'})[name]||name)}</td><td>${E(metrics[metric]??'不可计算')}</td></tr>`).join(''):'';
    slot().innerHTML=`<section class="tp-modeling-saved" data-testid="model-run-workbench">
      <header><h3>${E(doc.spec?.name||'预测任务')}</h3><p class="muted small">目标 ${E(doc.spec?.target?.column)} · 修订 ${E(doc.revision)} · 来源 ${E(doc.spec?.version_id)} · ${E(state)}</p></header>
      ${savedExecution?executionPanel(savedExecution):''}
      ${savedRun&&!savedExecution?`<p data-testid="model-run-state">运行 ${E(savedRun.state)} · ${E(savedRun.effective_state)} · ${E(savedRun.error_message||savedRun.error_code||'')}</p>`:''}
      ${rows&&!savedExecution?`<table><thead><tr><th>同折对照</th><th>${E(metric)}</th></tr></thead><tbody>${rows}</tbody></table>
        <p class="muted small">${E(savedRun.result?.rows)} 行，${E(savedRun.result?.folds?.length)} 折。仅为回顾性验证；无已部署业务收益证据。</p>`:''}
      ${!savedExecution&&savedRun?.effective_state==='superseded'?'<p class="helper warning">来源或任务修订已变化，本次历史结果不可作为当前模型证据。</p>':''}
      ${!savedExecution&&savedRun?.state==='failed'?'<p class="muted small">运行失败，请检查字段类型、分折数量和标签映射，调整后重新审批。</p>':''}
      <div class="tp-modeling-actions">
        ${state==='ready'?btn('approve-spec','确认并批准',true):''}
        ${canRun?`<label class="field">实验总预算 <select name="budget_profile"><option value="5m">5 分钟</option><option value="15m" selected>15 分钟</option><option value="30m">30 分钟</option></select></label>${btn('run-task','开始模型对照',true)}`:''}
        ${savedExecution?.run_id && savedExecution?.state==='failed'?'<p class="helper warning">该任务修订的运行已结束。需调整配置、保存新修订并重新审批后再训练。</p>':''}
        ${(savedExecution&&['queued','running','succeeded','failed','stale'].includes(savedExecution.state)) || savedRun&&['queued','running'].includes(savedRun.state)?btn('refresh-run','刷新运行结果',true):''}
        ${btn('continue-featurelab','进入 FeatureLab')}
        ${btn('back','返回任务选择')}
      </div>
      <p class="muted small">保存的训练对照只包含指标与折证据，不保存可直接部署的模型文件。</p>
    </section>`;
  }
  function enterFeatureLab(){
    if(!savedModelTask)return;
    const url='/projects/'+U(pid)+'/features/analysis?asset='+U(aid)+'&version='+U(source.version_id)
      +'&tp_model_task_id='+U(savedModelTask.task_id);
    ++requestGeneration;document.getElementById('tp-modeling-dialog')?.remove();
    window.location.assign(url);
  }
  function renderPreflight(){
    if(!active)return;const pf=active.preflight;
    const checks=pf?`<div class="grid grid-3 tp-modeling-metrics"><div><small>源行数</small><b>${E(pf.source_rows)}</b></div><div><small>筛选行数</small><b>${E(pf.selected_rows)}</b></div><div><small>训练记录</small><b>${E(pf.eligible_rows??'—')}</b></div></div>
      <p class="muted small">${pf.task_kind==='classification'?'前推分类验证':'时间滚动预测'} · ${E(pf.feature_count)} 个历史/决策特征 · ${E(pf.series_count)} 个实体</p>
      ${pf.resampling?.enabled?`<p class="muted small">重采样：${E(pf.resampling.bucket_count)} 桶 · ${E(pf.resampling.aggregation)} · 原始 ${E(pf.resampling.raw_rows)} 行</p>`:''}
      ${pf.classification?`<p class="muted small">时间切分：训练 ${E(pf.classification.train_rows)} · 验证 ${E(pf.classification.validation_rows)} · 测试 ${E(pf.classification.test_rows)}</p>`:''}
      ${pf.classification?.device_ood_preflight?`<details open data-testid="device-ood-fold-preflight"><summary>跨设备分折预检查：${E(pf.classification.device_ood_preflight.ready?'通过':'阻断')}</summary>
        <table><thead><tr><th>折</th><th>留出设备</th><th>训练/验证行</th><th>训练/验证正例</th><th>结果</th></tr></thead><tbody>${(pf.classification.device_ood_preflight.folds||[]).map(f=>`<tr><td>${E(f.fold)}</td><td>${E(f.heldout_devices.join(', '))}</td><td>${E(f.train_rows)} / ${E(f.validation_rows)}</td><td>${E(f.positive_train)} / ${E(f.positive_validation)}</td><td>${E(f.ready?'可用':f.blockers.join('、'))}</td></tr>`).join('')}</tbody></table>
        <small>仅证明样本/标签/设备可行性；不代表模型已经完成训练。</small></details>`:''}
      ${(pf.blockers||[]).map(t=>`<div class="helper warning">${E(t)}</div>`).join('')}
      ${(pf.warnings||[]).map(t=>`<p class="muted small">${E(t)}</p>`).join('')}`:'<p class="muted">尚未执行预检查</p>';
    const evidence=temporalEvidence&&!savedExecution?`<div class="tp-modeling-source"><b>同源模型证据</b><p>状态 ${E(temporalEvidence.status)} · 验证 ${E(temporalEvidence.validation_protocol||'未执行')} · ${E(temporalEvidence.evidence_level||'')}</p>${temporalEvidence.metrics?`<small>${E(JSON.stringify(temporalEvidence.metrics))}</small>`:''}<p class="muted small">历史评价不证明生产效益，已屏蔽逐行预测与敏感数据。</p></div>`:'';
    const actions=(savedModelTask?.status==='approved'&&active?.preflight?.ready)
      ? (active.run_id?btn('refresh-unified','刷新模型结果',true):btn('start-unified','开始模型评价',true))
      : ['review_required','ready'].includes(active.status)
      ? btn('edit','调整方案')+(pf?.ready?btn('approve','确认并锁定',true):btn('preflight','重新检查',true))
      : active.status==='approved'?btn('materialize','生成训练数据',true)
      : active.status==='dataset_ready'?btn('submit','开始建模',true)
      : active.run_id?`<a class="button button-primary" href="/runs/${U(active.run_id)}">查看运行任务</a>`:btn('preflight','检查数据');
    const budgetControl=(savedModelTask?.status==='approved'&&!active.run_id)?`<label class="field">时序实验总预算
      <select name="temporal_budget_profile" data-testid="temporal-budget-profile">
      ${[['5m','5 分钟'],['15m','15 分钟（默认）'],['30m','30 分钟']].map(([v,label])=>`<option value="${v}" ${v===temporalBudgetProfile?'selected':''}>${label}</option>`).join('')}
      </select></label><small>首次领取任务时启动冻结预算，重试不会延长截止时间。</small>`:'';
    slot().innerHTML=`${savedExecution?executionPanel(savedExecution):''}${budgetControl}<div class="tp-modeling-source"><b>${E(active.name)}</b><p>任务：${E(active.plan.task_kind)} · 目标 ${E(active.mapping.target)} · 原始版本 ${E(active.version_id)} · 状态 ${E(active.status)}</p></div>${checks}${evidence}<div class="action-row">${actions}${savedModelTask?btn('refresh-evidence','核对模型评价'):''}${btn('lineage','查看版本和审计记录')}</div>`;
  }
  async function persistModelTask(spec, confirmAvailability=false, confirmSplit=false){
    const isExisting=savedModelTask && savedModelTask.spec?.asset_id===spec.asset_id &&
      savedModelTask.spec?.version_id===spec.version_id;
    const endpoint=isExisting?taskBase()+'/'+U(savedModelTask.task_id):taskBase();
    savedExecution=null;
    savedModelTask=await ctx.req(endpoint,isExisting?json('PATCH',{
      expected_revision:savedModelTask.revision,spec}):post(spec));
    const check=await ctx.req(taskBase()+'/'+U(savedModelTask.task_id)+'/preflight',{method:'POST'});
    if(!check.ready){status('任务已保存，但仍有预检查问题：'+check.blockers.join('；'),'error');return check;}
    savedModelTask.status=check.ready?'ready':'review_required';
    savedTasks=[savedModelTask,...savedTasks.filter(t=>t.task_id!==savedModelTask.task_id)];
    if(confirmAvailability&&confirmSplit){
      savedModelTask=await ctx.req(taskBase()+'/'+U(savedModelTask.task_id)+'/approve',post({
        expected_revision:savedModelTask.revision,confirm_target:true,
        confirm_feature_availability:true,confirm_split:true}));
    }
    return check;
  }
  function genericSpec(form,target,xs){
    const kind=selectedGeneral;
    const split=form.querySelector('[name=split_kind]').value;
    const splitKind={kfold:'kfold',stratified:'stratified_kfold',group:'group_kfold',time:'temporal_holdout',rolling:'rolling_origin',month:'leave_month_out',device:'leave_device_out'}[split];
    const group=['group','device'].includes(split)?form.querySelector('[name=group_column]').value:'';
    const temporal=['time','rolling','month','device'].includes(split);
    const time=temporal?form.querySelector('[name=time_column]').value:'';
    const xTimes=Object.fromEntries([...(form.querySelectorAll?.('[data-feature-availability]')||[])].map(x=>[x.dataset.column,x.value]));
    return {name:(businessGoal||modeTitle(kind)).slice(0,120),asset_id:aid,version_id:source.version_id,
      business_goal:businessGoal.slice(0,180),task_kind:kind,target:{column:target,
        positive_values:kind==='tabular_classification'?splitValues(form.querySelector('[name=positive_values]')?.value):[],
        negative_values:kind==='tabular_classification'?splitValues(form.querySelector('[name=negative_values]')?.value):[]},
      features:xs.map(column=>({column,role:'static',available_at_column:temporal?(xTimes[column]||null):null})),
      time:{event_time_column:time||null,entity_columns:group?[group]:[],
        timezone:form.querySelector('[name=timezone]')?.value?.trim()||'Asia/Shanghai',
        feature_available_at_column:temporal?form.querySelector('[name=feature_available_at_column]').value||null:null,
        label_available_at_column:temporal?form.querySelector('[name=label_available_at_column]').value||null:null,
        decision_time_column:temporal?form.querySelector('[name=decision_time_column]').value||null:null},
      validation:{kind:splitKind,folds:Number(form.querySelector('[name=folds]')?.value||3),
        gap:temporal?Number(form.querySelector('[name=gap]')?.value||0):0},confirmations:{target_reviewed:true,
        feature_availability_reviewed:Boolean(form.querySelector('[name=confirm_availability]:checked')),
        split_reviewed:Boolean(form.querySelector('[name=confirm_split]:checked'))}};
  }
  function temporalSpec(f,kind,features){
    const group=f.entity?[f.entity]:[];
    const effectiveFeatures=kind==='continuous_forecast'&&f.feature_policy!=='historical_observed'?[]:features;
    return {name:f.name,asset_id:aid,version_id:source.version_id,
      business_goal:businessGoal.slice(0,180),task_kind:kind,
      target:{column:f.target,horizon_steps:kind==='continuous_forecast'?Number(f.horizon_steps):1,
        positive_values:kind==='classification'?splitValues(f.positive_values):[],
        negative_values:kind==='classification'?splitValues(f.negative_values):[]},
      features:effectiveFeatures.map(column=>({column,role:'observed_past'})),
      time:{event_time_column:f.time,entity_columns:group,timezone:f.timezone||'Asia/Shanghai',
        frequency:f.frequency,feature_available_at_column:f.availability_policy==='source_column'?f.available_at||null:null,
        publication_lag_days:f.availability_policy==='declared_lag'?Number(f.publication_lag_days):null,
        label_available_at_column:kind==='classification'?f.label_available_at||null:null,
        label_maturity_lag_seconds:kind==='classification'&&!f.label_available_at?optionalNum(f.label_delay_seconds):null},
      validation:{kind:kind==='classification'&&f.validation_strategy==='leave_device_out'?'leave_device_out':kind==='classification'?'temporal_holdout':'rolling_origin',
        folds:kind==='classification'?Number(f.classification_folds||2):Number(f.validation_windows||3)},
      confirmations:{target_reviewed:false,feature_availability_reviewed:false,split_reviewed:false}};
  }
  const splitValues=x=>String(x||'').split(/[,，]/).map(s=>s.trim()).filter(Boolean);
  const optionalNum=x=>String(x??'').trim()===''?null:Number(x);
  async function submit(event){
    if(event.target?.id==='tp-modeling-general-form'){
      event.preventDefault();
      const form=event.target,target=form.querySelector('[name=target]').value;
      const xs=[...form.querySelectorAll('[name=observed_features]:checked')].map(x=>x.value);
      if(!target||!sourceCols().includes(target)||!xs.length||xs.length>32||xs.includes(target)){
        status('请确认 Y，选择 1–32 个不同于 Y 的有效 X。','error');return true;
      }
      const forbidden=xs.filter(x=>{const info=roleFor(x);return ['time','available_at','entity','label','category'].includes(info.role)||(info.warnings||[]).some(w=>w.includes('事后'));});
      if(forbidden.length){status('存在不能直接参与训练的 X：'+forbidden.join('、')+'。请先在特征工程中构造时点正确的特征。','error');return true;}
      const risk=xs.filter(x=>!suggestedFeatures(target).includes(x));
      if(risk.length&&!confirm(`以下 X 尚未通过字段安全候选检查：${risk.join('、')}。仍需在 FeatureLab 核实可用时间和角色。继续吗？`))return true;
      businessGoal=businessGoal||`预测 ${target}`;
      try{const check=await persistModelTask(genericSpec(form,target,xs),
        Boolean(form.querySelector('[name=confirm_availability]:checked')),
        Boolean(form.querySelector('[name=confirm_split]:checked')));
        renderGeneralSaved();
        status(check.ready?'任务已保存；确认后可以直接运行真实模型对照':'任务已保存，但预检查存在问题：'+check.blockers.join('；'),'error');
        return true;
      }catch(err){status('保存统一任务失败：'+err.message,'error');return true;}
    }
    if(event.target?.id!=='tp-modeling-form')return false;
    event.preventDefault();const f=Object.fromEntries(new FormData(event.target));
    const kind=f.task_kind;const features=new FormData(event.target).getAll('observed_features');
    if(kind==='classification'&&f.validation_strategy==='leave_device_out'&&!f.entity){status('跨设备外推必须指定设备标识字段。','error');return true;}
    const mapping={time:f.time,entity:f.entity||null,batch:kind==='classification'?(f.batch||null):null,
      target:f.target,target_unit:f.target_unit?.trim()||null,
      timezone:f.timezone,available_at:f.availability_policy==='source_column'?f.available_at:null,
      availability_policy:f.availability_policy,
      publication_lag_days:f.availability_policy==='declared_lag'?Number(f.publication_lag_days):null,
      observed_features:kind==='continuous_forecast'&&f.feature_policy!=='historical_observed'?[]:features,event:{column:f.event_column||null,truthy_values:splitValues(f.event_truthy),
                                         reset_policy:f.event_reset_policy},
      regime:{column:f.regime_column||null}};
    const multimodal=f.feature_policy==='historical_observed';
    const plan={task_kind:kind,task_family:kind==='classification'?'equipment_state':f.task_family,frequency:f.frequency,
      horizon_steps:kind==='classification'?1:Number(f.horizon_steps),
      validation_windows:kind==='classification'?Number(f.classification_folds||2):Number(f.validation_windows),
      min_history:kind==='classification'?24:Number(f.min_history),
      primary_metric:kind==='classification'?'MAE':f.primary_metric,
      feature_policy:kind==='classification'?'snapshot_at_decision':f.feature_policy,
      validation_strategy:kind==='classification'&&f.validation_strategy==='leave_device_out'?'leave_device_out':'time_forward',
      model_candidates:kind==='classification'?['binary_logistic']:(multimodal?['historical_exog_ridge']:['last_value','moving_average']),
      resampling:{enabled:kind==='continuous_forecast'&&Boolean(f.resample_enabled),
                  source_period_seconds:Number(f.source_period_seconds),aggregation:f.aggregation,
                  min_coverage:Number(f.min_coverage)},
      mechanism:{target_min:optionalNum(f.target_min),target_max:optionalNum(f.target_max),
                 max_step_change:optionalNum(f.max_step_change),enforcement:f.mechanism_enforcement||'block'},
      classification:kind==='classification'?{positive_values:splitValues(f.positive_values),negative_values:splitValues(f.negative_values),
        label_available_at:f.label_available_at||null,label_delay_seconds:f.label_available_at?null:optionalNum(f.label_delay_seconds),
        decision_stage:f.decision_stage,min_recall:Number(f.min_recall)}:null};
    try{status('正在核对来源、数据完整性及训练时间边界…');
      const unified=await persistModelTask(temporalSpec(f,kind,features));
      if(!unified.ready)return true;
      if(active){active=await ctx.req(base()+'/drafts/'+U(active.draft_id),json('PATCH',{expected_revision:active.revision,name:f.name,mapping,plan}));}
      else{active=await ctx.req(base()+'/drafts',post({asset_id:aid,version_id:source.version_id,view_id:source.view_id,
         filters:source.view_id?null:source.filters,selection_id:source.selection_id,name:f.name,mapping,plan,confirm_scope:true}));}
      active.preflight=await ctx.req(base()+'/drafts/'+U(active.draft_id)+'/preflight',{method:'POST'});
      active.status=active.preflight.ready?'ready':'review_required';
      let linkWarning='';
      if(active.preflight.ready&&savedModelTask){
        try{await ctx.req(taskBase()+'/'+U(savedModelTask.task_id)+'/temporal-link',post({draft_id:active.draft_id,expected_revision:savedModelTask.revision}));}
        catch(linkError){linkWarning='；统一证据关联未通过：'+linkError.message;}
      }
      temporalEvidence=null;renderPreflight();status((active.preflight.ready?'预检查通过，待人工确认':'发现阻断项，请调整字段或规则')+linkWarning,linkWarning?'error':'');
    }catch(err){status(err.message,'error');ctx.toast?.(err.message);}
    return true;
  }
  async function click(event){
    const el=event.target.closest('[data-modeling]');if(!el)return false;
    event.preventDefault();const action=el.dataset.modeling;
    try{
      if(action==='trace-prediction'&&savedModelTask){
        const value=Number(slot()?.querySelector('[name=trace_prediction_row]')?.value);
        if(!Number.isInteger(value)||value<0||value>=Number(savedExecution?.prediction_asset?.rows||0)){
          status('请填写有效的验证预测行号。','error');return true;
        }
        await tracePredictionRow(value);return true;
      }
      if(action==='query-predictions'&&savedModelTask){
        await runPredictionQuery(0);return true;
      }
      if(action==='more-prediction-query'&&savedModelTask){
        await runPredictionQuery(Number(savedPredictionQuery?.next_offset||0));return true;
      }
      if(action==='select-prediction-row'&&savedModelTask){
        const value=Number(el.dataset.rowId);
        if(!Number.isInteger(value)||value<0||value>=Number(savedExecution?.prediction_asset?.rows||0)){
          status('预测行不在当前资产中','error');return true;
        }
        const field=slot()?.querySelector('[name=trace_prediction_row]');if(field)field.value=value;
        document.querySelectorAll('[data-prediction-row-id]').forEach(x=>{
          x.style.outline=Number(x.dataset.predictionRowId)===value?'2px solid var(--accent, #64748b)':'';
        });
        await tracePredictionRow(value);return true;
      }
      if(action==='trace-history-window'&&savedModelTask){
        const value=Number(slot()?.querySelector('[name=trace_prediction_row]')?.value);
        savedWindowLineage=await ctx.req(executionEndpoint()+'/predictions/window-lineage?row_id='+U(value));
        const root=document.getElementById('tp-prediction-lineage-slot');
        if(root)root.innerHTML=predictionLineageMarkup()+windowLineageMarkup();
        status('已核验历史窗口来源：'+savedWindowLineage.status);return true;
      }
      if(action==='more-window-edges'&&savedModelTask&&savedWindowLineage?.next_offset!=null){
        const value=Number(slot()?.querySelector('[name=trace_prediction_row]')?.value);
        const next=savedWindowLineage.next_offset;
        savedWindowLineage=await ctx.req(executionEndpoint()+'/predictions/window-lineage?row_id='+U(value)+'&offset='+U(next));
        const root=document.getElementById('tp-prediction-lineage-slot');
        if(root)root.innerHTML=predictionLineageMarkup()+windowLineageMarkup();
        status('已读取历史源记录第 '+next+' 页');return true;
      }
      if(action==='open-predictions'&&savedExecution?.prediction_asset){
        const asset=savedExecution.prediction_asset;
        const target=document.getElementById('tp-prediction-perspective');
        if(!target)throw new Error('Prediction Perspective panel is missing');
        target.textContent='Loading signed validation predictions...';
        const module=await window.TimePilotPerspectiveLoader.load();
        const scheme=window.location.protocol==='https:'?'wss:':'ws:';
        const ws=scheme+'//'+window.location.host+'/api/v1/native-bi/model-predictions/'+U(pid)+'/'+
          U(savedExecution.task_id)+'/'+U(asset.asset_id)+'/ws?project_id='+U(pid);
        const runId=savedExecution.run_id,expectedSha=asset.manifest_sha256,taskId=savedExecution.task_id;
        await module.mountPerspectiveRemote(target,ws,{tableName:'timepilot_active',
          onCell:async ({row_id})=>{
            if(savedExecution?.run_id!==runId || savedExecution?.task_id!==taskId ||
               savedExecution?.prediction_asset?.manifest_sha256!==expectedSha)return;
            try{await tracePredictionRow(row_id);}catch(err){status('预测行追溯失败：'+err.message,'error');}
          }});
        nativePredictionAssetId=asset.asset_id;
        status('验证预测已加载，点击未聚合的数据行可核验源记录');return true;
      }
      if(action==='close'){++requestGeneration;document.getElementById('tp-modeling-dialog')?.remove();return true;}
      if(action==='back'){renderSuggestions();return true;}
      if(action==='analyze-goal'){
        const input=slot()?.querySelector('[name=business_goal]');businessGoal=(input?.value||'').trim().slice(0,180);
        const token=++requestGeneration;status('正在结合业务目标更新字段建议…');
        const reply=await ctx.req(base()+'/assets/'+U(aid)+'/suggestions?version_id='+U(source.version_id)+'&business_goal='+U(businessGoal));
        if(token!==requestGeneration)return true;suggestions=reply;renderSuggestions();status('建议已更新；字段仍需确认');return true;
      }
      if(action==='continue-featurelab'){enterFeatureLab();return true;}
      if(action==='approve-spec'&&savedModelTask){
        if(!confirm('已确认 Y/X 业务含义、输入数据的时点可用性和验证集隔离？'))return true;
        savedModelTask=await ctx.req(taskBase()+'/'+U(savedModelTask.task_id)+'/approve',post({
          expected_revision:savedModelTask.revision,confirm_target:true,
          confirm_feature_availability:true,confirm_split:true}));
        renderGeneralSaved();status('模型任务已批准，可以运行');return true;
      }
      if(action==='run-task'&&savedModelTask){
        const profile=slot()?.querySelector('[name=budget_profile]')?.value||'15m';
        savedExecution=await ctx.req(executionEndpoint(),post({expected_revision:savedModelTask.revision,budget_profile:profile}));
        savedLineage=null;savedWindowLineage=null;savedPredictionQuery=null;nativePredictionAssetId=null;savedRun=null;savedDiagnostics=null;renderGeneralSaved();status('运行任务已登记，可刷新查看同源评价结果');return true;
      }
      if(action==='refresh-evidence'&&savedModelTask){
        temporalEvidence=await ctx.req(taskBase()+'/'+U(savedModelTask.task_id)+'/evidence');
        renderPreflight();status('已读取同源评价记录：'+temporalEvidence.status);return true;
      }
      if(action==='refresh-steps'&&savedModelTask){
        savedRunSteps=await ctx.req(executionEndpoint()+'/steps');
        if(active)renderPreflight();else renderGeneralSaved();
        status('已更新折级执行回执');return true;
      }
      if(action==='cancel-execution'&&savedModelTask){
        if(!confirm('取消当前训练？已经完成的分折不会被标记为完整实验结果。'))return true;
        await ctx.req(executionEndpoint()+'/cancel',post({
          expected_revision:savedModelTask.revision,run_id:savedExecution?.run_id||null}));
        savedExecution=await ctx.req(executionEndpoint());
        if(active)renderPreflight();else renderGeneralSaved();
        status('取消请求已提交；独立 Worker 将终止训练进程');return true;
      }
      if(action==='refresh-run'&&savedModelTask){
        savedExecution=await ctx.req(executionEndpoint());
        savedLineage=null;savedWindowLineage=null;savedPredictionQuery=null;nativePredictionAssetId=null;savedRun=null;savedDiagnostics=null;
        if(savedExecution.run_id){try{savedRunSteps=await ctx.req(executionEndpoint()+'/steps');}catch(_){savedRunSteps=null;}}
        if(savedExecution.state==='succeeded'&&savedExecution.diagnostics_available)await loadDiagnostics();
        renderGeneralSaved();status(savedExecution.state==='succeeded'?'已取得经核验的同源评价':
          savedExecution.state==='failed'?'任务失败，请检查诊断':'任务状态：'+savedExecution.state);return true;
      }
      if(action==='load-diagnostics'&&savedModelTask){
        const model=slot()?.querySelector('[name=diag_model]')?.value||'forest';
        const dim=slot()?.querySelector('[name=diag_dimension]')?.value||null;
        const value=slot()?.querySelector('[name=diag_value]')?.value?.trim()||null;
        await loadDiagnostics(model,dim,value);
        if(active)renderPreflight();else renderGeneralSaved();
        status('已读取与当前来源和任务版本绑定的验证诊断');return true;
      }
      if(action==='select-recommended'||action==='clear-features'){featureAction(action);return true;}
      if(action==='choose-task'){
        const mode=el.dataset.mode;
        const typed=(slot()?.querySelector('[name=business_goal]')?.value||'').trim().slice(0,180);
        if(typed!==businessGoal){
          businessGoal=typed;const generation=++requestGeneration;
          const updated=await ctx.req(base()+'/assets/'+U(aid)+'/suggestions?version_id='+U(source.version_id)+'&business_goal='+U(businessGoal));
          if(generation!==requestGeneration)return true;suggestions=updated;
        }
        if(['tabular_regression','tabular_classification'].includes(mode)){renderGeneralTask(mode);return true;}
        if(['continuous_forecast','classification'].includes(mode)){selectedKind=mode;renderMapping();return true;}
        return true;
      }
      if(action==='resume-spec'){
        savedRun=null;savedExecution=null;savedDiagnostics=null;savedRunSteps=null;savedLineage=null;savedWindowLineage=null;savedPredictionQuery=null;nativePredictionAssetId=null;savedModelTask=await ctx.req(taskBase()+'/'+U(el.dataset.taskId));
        if(savedModelTask.effective_status==='stale_source'){
          status('数据来源已变化，旧合同已失效。请在新版本重新创建任务。','error');return true;
        }
        const spec=savedModelTask.spec;
        aid=spec.asset_id;source.version_id=spec.version_id;businessGoal=spec.business_goal||'';
        suggestions=await ctx.req(base()+'/assets/'+U(aid)+'/suggestions?version_id='+U(source.version_id));
        selectedKind=spec.task_kind;active=null;
        savedExecution=await ctx.req(executionEndpoint());
        if(savedExecution.state==='succeeded'&&savedExecution.diagnostics_available)await loadDiagnostics();
        if(spec.task_kind.startsWith('tabular')){
          selectedGeneral=spec.task_kind;generalTarget=spec.target.column;
          generalFeatures=spec.features.map(x=>x.column);savedValidation=spec.validation.kind;
          if(savedModelTask.status==='approved'){
            try{const runs=await ctx.req(taskBase()+'/'+U(savedModelTask.task_id)+'/runs');savedRun=runs.items?.[0]||null;}
            catch(_){savedRun=null;}
            renderGeneralSaved();
          }else renderGeneralTask(spec.task_kind);
        }else{
          temporalEvidence=null;
          const link=await ctx.req(taskBase()+'/'+U(savedModelTask.task_id)+'/temporal-link');
          if(link.draft_id&&link.status!=='stale'){
            active=await ctx.req(base()+'/drafts/'+U(link.draft_id));
            renderPreflight();
          }else{
            if(link.status==='stale'){
              slot().innerHTML='<section class="tp-modeling-source"><h3>旧任务证据已失效</h3><p>来源或执行合同发生变化，请从当前数据版本新建预测任务；旧指标不能继续使用。</p></section>';
              status('历史建模草稿或源版本发生变化；需重新创建任务。','error');
            }else{
              renderMapping();
              status('该时序合同尚未关联经过核验的执行草稿，请确认后保存。','error');
            }
            return true;
          }
        }
        status('已恢复服务端保存的任务配置，修订 '+savedModelTask.revision);
        return true;
      }
      if(action==='recommend'){selectedKind=suggestions.proposals[Number(el.dataset.index)].task_kind;renderMapping();return true;}
      if(action==='edit'){renderMapping();return true;}
      if(!active)return true;
      const endpoint=base()+'/drafts/'+U(active.draft_id);
      if(action==='preflight'){active.preflight=await ctx.req(endpoint+'/preflight',{method:'POST'});renderPreflight();}
      if(action==='approve'){
        if(!confirm('确认目标/标签、特征采集时刻、维护事件与工况含义，以及数据可用时间？'))return true;
        if(savedModelTask){savedModelTask=await ctx.req(taskBase()+'/'+U(savedModelTask.task_id)+'/approve',post({
          expected_revision:savedModelTask.revision,confirm_target:true,
          confirm_feature_availability:true,confirm_split:true}));}
        else{active=await ctx.req(endpoint+'/approve',post({expected_revision:active.revision,confirm:true,
          acknowledge_single_target:true,acknowledge_availability:true}));}
        if(savedModelTask){temporalEvidence=await ctx.req(taskBase()+'/'+U(savedModelTask.task_id)+'/evidence');}
        renderPreflight();status('已完成任务审批；执行器仍保留独立的时间因果检查');}
      if(action==='start-unified'&&savedModelTask){
        temporalBudgetProfile=slot()?.querySelector('[name=temporal_budget_profile]')?.value||temporalBudgetProfile;
        if(!['5m','15m','30m'].includes(temporalBudgetProfile))throw new Error('无效实验总预算');
        if(!confirm('确认按照已审核的 Y/X、数据可用时点和前向验证方案执行？'))return true;
        savedExecution=await ctx.req(executionEndpoint(),post({expected_revision:savedModelTask.revision,confirm_temporal_execution:true,budget_profile:temporalBudgetProfile}));
        savedLineage=null;savedWindowLineage=null;savedPredictionQuery=null;savedDiagnostics=null;
        if(savedExecution.run_id){active.run_id=savedExecution.run_id;active.status='queued';}
        renderPreflight();status('模型执行已提交，统一结果可以随时刷新');return true;
      }
      if(action==='refresh-unified'&&savedModelTask){
        savedExecution=await ctx.req(executionEndpoint());
        savedLineage=null;savedWindowLineage=null;savedPredictionQuery=null;savedDiagnostics=null;if(savedExecution.state==='succeeded'&&savedExecution.diagnostics_available)await loadDiagnostics();
        renderPreflight();
        status('模型状态：'+savedExecution.state);return true;
      }
      if(action==='materialize'){status('正在生成独立训练快照…');active=await ctx.req(endpoint+'/materialize',{method:'POST'});renderPreflight();status('训练数据已冻结');}
      if(action==='submit'){const out=await ctx.req(endpoint+'/submit',{method:'POST'});active.run_id=out.run_id;active.status='queued';
        if(savedModelTask)temporalEvidence=await ctx.req(taskBase()+'/'+U(savedModelTask.task_id)+'/evidence');
        renderPreflight();status('任务已入队，可在项目模型管理查看');}
      if(action==='lineage'){const audit=await ctx.req(endpoint+'/audit?limit=5');
        alert(JSON.stringify({dataset_id:audit.dataset_id,source_hash:audit.selection_hash,
          resampling:audit.resampling_summary,first_buckets:audit.buckets},null,2));}
    }catch(err){status('操作未完成：'+err.message,'error');ctx.toast?.(err.message);}
    return true;
  }
  window.TimePilotModeling={open,openDraft,click,submit,change};
})();
