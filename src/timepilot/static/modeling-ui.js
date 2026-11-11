/* v0.22.1: one reviewed data-to-model workflow for forecasting/classification. */
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
  const base=()=>'/projects/'+U(pid)+'/modeling';
  const slot=()=>document.getElementById('tp-modeling-body');
  function status(message,kind=''){const node=document.getElementById('tp-modeling-status');if(node){node.textContent=message;node.dataset.kind=kind;}}
  function shell(){
    document.getElementById('tp-modeling-dialog')?.remove();
    document.body.insertAdjacentHTML('beforeend',`<div class="tp-modeling-backdrop" id="tp-modeling-dialog" role="dialog" aria-modal="true" aria-labelledby="tp-modeling-title">
      <section class="tp-modeling-panel"><header class="tp-modeling-header"><div><h2 id="tp-modeling-title">建立建模任务</h2><p>模型只使用已确认的字段和可用时间；审核后保存独立训练快照。</p></div>${btn('close','关闭')}</header>
      <div class="tp-modeling-steps"><span>1 识别数据</span><span>2 配置模型</span><span>3 检查与运行</span></div>
      <p id="tp-modeling-status" class="muted small" role="status"></p><div id="tp-modeling-body"></div></section></div>`);
  }
  async function open(opts){
    ctx=opts.ctx;pid=opts.projectId;aid=opts.assetId;
    source={view_id:opts.viewId||null,filters:opts.filters||[],selection_id:opts.selectionId||null,version_id:opts.versionId||'base'};
    active=null;selectedKind='continuous_forecast';shell();status('正在识别字段和时间分布…');
    try{suggestions=await ctx.req(base()+'/assets/'+U(aid)+'/suggestions?version_id='+U(source.version_id));renderSuggestions();status('字段建议来自统计画像，业务含义需要核实');}
    catch(err){status('无法读取数据建议：'+err.message,'error');}
  }
  async function openDraft(opts){
    ctx=opts.ctx;pid=opts.projectId;aid=opts.assetId;source=null;active=null;shell();
    try{active=await ctx.req(base()+'/drafts/'+U(opts.draftId));aid=active.asset_id;
      selectedKind=active.plan.task_kind;
      suggestions=await ctx.req(base()+'/assets/'+U(aid)+'/suggestions?version_id='+U(active.version_id));
      if(['review_required','ready'].includes(active.status))renderMapping();else renderPreflight();}
    catch(err){status(err.message,'error');}
  }
  function renderSuggestions(){
    const p=suggestions?.profile||{},rec=p.recommendation||{};
    slot().innerHTML=`<div class="tp-modeling-source"><b>${E(p.source_rows)} 条源记录 · ${E(p.fields?.length)} 个字段</b><p>建议时间：${E(rec.time||'待确认')}　实体：${E(rec.entity||'待确认')}　频率：${E(rec.frequency?.frequency||'未知')}</p><small>识别使用 ${E(p.sample_rows)} 行抽样；实际训练会核验完整筛选行集。</small></div>
      <h3>选择任务</h3>${(suggestions?.proposals||[]).map((x,i)=>`<div class="tp-modeling-choice"><div><b>${E(x.title)}</b><small>${E(x.reason)}</small><small>${E(x.status)}</small></div>${btn('recommend','配置任务',true,`data-index="${i}" ${x.task_kind==='classification'||x.task_kind==='continuous_forecast'?'':'disabled'}`)}</div>`).join('')}
      <details><summary>全部字段与识别证据</summary><div class="tp-modeling-fields">${(p.fields||[]).map(x=>`<div><b>${E(x.name)}</b>　${(x.suggested_roles||[]).map(r=>E(r.role)+' '+Math.round(r.confidence*100)+'%').join(' / ')||'含义待确认'}</div>`).join('')}</div></details>`;
  }
  function renderMapping(){
    const rec=suggestions?.profile?.recommendation||{},cols=suggestions?.profile?.fields?.map(f=>f.name)||[];
    const m=active?.mapping||{},p=active?.plan||{};
    selectedKind=active?.plan?.task_kind||selectedKind;
    const features=m.observed_features||[];
    const event=m.event||{},regime=m.regime||{},sampleFrequency=rec.frequency?.frequency;
    const res=p.resampling||{},mechanism=p.mechanism||{},cl=p.classification||{};
    const name=active?.name|| (selectedKind==='classification'?'生产质量风险分类':'工业时序预测');
    const time=m.time??rec.time??'',entity=m.entity??rec.entity??'',target=m.target??(selectedKind==='classification'?'':(rec.target||''));
    const unit=m.target_unit??((target.match(/[（(]([^()（）]+)[）)]/)||[])[1]||'');
    slot().innerHTML=`<form id="tp-modeling-form" class="form tp-modeling-form">
      <h3>任务和字段</h3>
      <div class="grid grid-2">
        ${F('name','任务名称',name,'text','required maxlength="120"')}
        <label class="field"><span>任务类型</span><select name="task_kind"><option value="continuous_forecast" ${selectedKind==='continuous_forecast'?'selected':''}>趋势预测</option><option value="classification" ${selectedKind==='classification'?'selected':''}>质量不良二分类</option></select></label>
        <label class="field"><span>业务任务类别</span><select name="task_family"><option value="equipment_state" ${(p.task_family||'equipment_state')==='equipment_state'?'selected':''}>设备状态 / 工艺质量</option><option value="demand_forecast" ${p.task_family==='demand_forecast'?'selected':''}>业务需求预测</option></select></label>
        <label class="field"><span>预测目标 / 分类标签</span><select name="target" required>${sel(cols,target,'选择实际目标字段')}</select></label>
        ${F('target_unit','目标物理单位',unit,'text','maxlength="32" placeholder="例如 ℃"')}
        <label class="field"><span>采集或节点时间</span><select name="time" required>${sel(cols,time,'确认真实的日历时间')}</select></label>
        <label class="field"><span>设备标识（可选）</span><select name="entity">${sel(cols,entity,'只有一条序列')}</select></label>
        <label class="field"><span>批次标识（分类可选）</span><select name="batch">${sel(cols,m.batch||'','不检查跨批次泄漏')}</select></label>
        ${F('timezone','原始数据时区',m.timezone||'Asia/Shanghai','text','required')}
        <label class="field"><span>目标频率 / 分类时间轴</span><select name="frequency">${['h','D','MS','min','5min','s'].map(x=>`<option value="${x}" ${x===(p.frequency||((sampleFrequency==='min'||sampleFrequency==='5min')?'h':sampleFrequency)||'h')?'selected':''}>${E(({h:'小时',D:'天',MS:'月',min:'分钟（暂不可直接运行）','5min':'5分钟（暂不可直接运行）',s:'秒（暂不可直接运行）'})[x])}</option>`).join('')}</select></label>
        <label class="field"><span>数据可用时间来源</span><select name="availability_policy"><option value="unconfirmed">待确认</option><option value="source_column" ${m.availability_policy==='source_column'?'selected':''}>原始可用时间字段</option><option value="declared_lag" ${m.availability_policy==='declared_lag'?'selected':''}>明确声明发布延迟</option></select></label>
        <label class="field" data-dependency="source_column"><span>数据可用时间字段</span><select name="available_at">${sel(cols,m.available_at,'请选择')}</select></label>
        <label class="field" data-dependency="declared_lag"><span>发布延迟（天）</span><input name="publication_lag_days" type="number" min="0" max="3650" value="${E(m.publication_lag_days??0)}"></label>
      </div>
      <h3>输入特征</h3><p class="muted small">系统保留全部原始字段。仅被选中的传感器会进入模型；分类特征必须在判断时刻已可获取。</p>
      <div class="tp-modeling-feature-grid">${cols.filter(x=>![time,target,entity,m.available_at,cl.label_available_at,m.batch].includes(x)).map(x=>`<label><input name="observed_features" type="checkbox" value="${E(x)}" ${features.includes(x)?'checked':''}> ${E(x)}</label>`).join('')}</div>
      <label class="field" data-task-only="continuous_forecast"><span>预测输入模式</span><select name="feature_policy"><option value="target_history_only">仅目标历史（单变量基线）</option><option value="historical_observed" ${p.feature_policy==='historical_observed'?'selected':''}>目标历史 + 已知传感器/事件/工况</option></select></label>
      <details ${res.enabled?'open':''} data-task-only="continuous_forecast"><summary>分钟数据聚合到小时（逐桶审计）</summary>
        <label class="check-row"><input type="checkbox" name="resample_enabled" ${res.enabled?'checked':''}>启用显式重采样（不会填补缺失时间点）</label>
        <div class="grid grid-2"><label class="field"><span>原始周期</span><select name="source_period_seconds">${[1,30,60,300,900].map(v=>`<option value="${v}" ${v===(res.source_period_seconds||60)?'selected':''}>${v} 秒</option>`).join('')}</select></label>
        <label class="field"><span>聚合指标</span><select name="aggregation">${['mean','last','min','max'].map(v=>`<option value="${v}" ${v===(res.aggregation||'mean')?'selected':''}>${E(({mean:'均值',last:'最后观测',min:'最小值',max:'最大值'})[v])}</option>`).join('')}</select></label>
        ${F('min_coverage','最低桶覆盖率（0.5–1.0）',res.min_coverage??1,'number','min="0.5" max="1" step="0.05"')}</div>
        <p class="muted small">源时间按 UTC 小时边界分桶；桶结束前的数据不可作为完整桶读数使用。出现缺桶会阻断。</p></details>
      <details><summary>维护事件与工况</summary><div class="grid grid-2">
        <label class="field"><span>维护事件列</span><select name="event_column">${sel(cols,event.column,'不使用事件')}</select></label>
        ${F('event_truthy','事件触发值（逗号分隔）',(event.truthy_values||[]).join(','),'text','placeholder="例如 检修,更换滤芯"')}
        <label class="field"><span>工况字段</span><select name="regime_column">${sel(cols,regime.column,'不使用工况')}</select></label>
        <label class="field"><span>训练是否排除跨维护事件窗口</span><select name="event_reset_policy"><option value="indicator_only">保留事件信号</option><option value="exclude_cross_event_windows" ${event.reset_policy==='exclude_cross_event_windows'?'selected':''}>排除跨事件窗口</option></select></label>
      </div><p class="muted small">工况切换若发生在同一聚合桶内部，将阻止聚合；不将未来事件当作已知特征。</p></details>
      <details data-task-only="continuous_forecast"><summary>预测跨度、验证与机理约束</summary><div class="grid grid-2">
        ${F('horizon_steps','未来预测步数',p.horizon_steps??3,'number','min="1" max="24"')}
        ${F('validation_windows','滚动验证窗口',p.validation_windows??3,'number','min="1" max="24"')}
        ${F('min_history','最低历史点数',p.min_history??24,'number','min="2" max="10000"')}
        <label class="field"><span>主要指标</span><select name="primary_metric"><option value="MAE">MAE</option><option value="WAPE" ${p.primary_metric==='WAPE'?'selected':''}>WAPE</option></select></label>
        ${F('target_min','目标物理下界',mechanism.target_min??'','number','step="any"')}
        ${F('target_max','目标物理上界',mechanism.target_max??'','number','step="any"')}
        ${F('max_step_change','最大单步变化',mechanism.max_step_change??'','number','step="any" min="0"')}
        <label class="field"><span>物理约束执行</span><select name="mechanism_enforcement"><option value="block">超界阻断</option><option value="warn" ${mechanism.enforcement==='warn'?'selected':''}>告警但允许运行</option></select></label>
      </div><p class="muted small">上下界与变化率由工艺工程师确认；机理证据独立保存，目前只执行显式数值约束。</p></details>
      <details data-task-only="classification" open><summary>分类标签、判断节点与验收</summary><div class="grid grid-2">
        ${F('positive_values','不良标签原始取值',cl.positive_values?.join(',')||'NG','text','required')}
        ${F('negative_values','合格标签原始取值',cl.negative_values?.join(',')||'OK','text','required')}
        <label class="field"><span>标签成熟时间字段</span><select name="label_available_at">${sel(cols,cl.label_available_at||'','选择实际标签确认时间')}</select></label>
        ${F('label_delay_seconds','标签延迟秒数（没有时间列时填）',cl.label_delay_seconds??'','number','min="0"')}
        ${F('decision_stage','判断节点',cl.decision_stage||'节点A')}
        ${F('min_recall','最低目标召回率',cl.min_recall??0.9,'number','min="0.01" max="1" step="0.01"')}
      </div><p class="muted small">使用时间顺序拆分训练、验证、测试集；阈值只从验证集选择，最终测试只评估一次。</p></details>
      <div class="tp-modeling-warning">训练数据独立冻结；模型无法调用未来尚未获得的传感器、质量标签或维护结果。预检查将展示具体阻断原因。</div>
      <label class="check-row"><input type="checkbox" name="confirm_scope" required checked>确认训练使用冻结数据版本和明细级筛选，图表样本与分页不限制训练行数。</label>
      <div class="action-row">${btn('back','返回建议')}<button type="submit" class="button button-primary">保存并检查数据</button></div></form>`;
    const form=document.getElementById('tp-modeling-form');
    if(m.availability_policy==='unconfirmed'||!m.availability_policy)form.querySelector('[name=availability_policy]').value='unconfirmed';
    for(const field of cols){if(field===target||field===time)form.querySelectorAll(`input[name=observed_features]`).forEach(x=>{if(x.value===field)x.disabled=true;});}
    form.querySelector('[name=feature_policy]').value=p.feature_policy||'target_history_only';
    updateVisibility();
  }
  function updateVisibility(){
    const f=document.getElementById('tp-modeling-form');if(!f)return;
    const kind=f.querySelector('[name=task_kind]').value;
    const availability=f.querySelector('[name=availability_policy]').value;
    f.querySelectorAll('[data-task-only]').forEach(el=>{el.hidden=el.dataset.taskOnly!==kind;});
    f.querySelectorAll('[data-dependency]').forEach(el=>{el.hidden=el.dataset.dependency!==availability;});
  }
  function change(event){
    if(event.target.closest('#tp-modeling-form') && ['availability_policy','task_kind','time','target','entity'].includes(event.target.name)){
      const f=document.getElementById('tp-modeling-form');
      if(event.target.name==='task_kind' && event.target.value==='classification') f.querySelector('[name=task_family]').value='equipment_state';
      const reserved=['time','target','entity'].map(n=>f.querySelector('[name='+n+']')?.value).filter(Boolean);
      f.querySelectorAll('[name=observed_features]').forEach(field=>{field.disabled=reserved.includes(field.value);if(field.disabled)field.checked=false;});
      updateVisibility();return true;
    }
    return false;
  }
  function renderPreflight(){
    if(!active)return;const pf=active.preflight;
    const checks=pf?`<div class="grid grid-3 tp-modeling-metrics"><div><small>源行数</small><b>${E(pf.source_rows)}</b></div><div><small>筛选行数</small><b>${E(pf.selected_rows)}</b></div><div><small>训练记录</small><b>${E(pf.eligible_rows??'—')}</b></div></div>
      <p class="muted small">${pf.task_kind==='classification'?'前推分类验证':'时间滚动预测'} · ${E(pf.feature_count)} 个历史/决策特征 · ${E(pf.series_count)} 个实体</p>
      ${pf.resampling?.enabled?`<p class="muted small">重采样：${E(pf.resampling.bucket_count)} 桶 · ${E(pf.resampling.aggregation)} · 原始 ${E(pf.resampling.raw_rows)} 行</p>`:''}
      ${pf.classification?`<p class="muted small">时间切分：训练 ${E(pf.classification.train_rows)} · 验证 ${E(pf.classification.validation_rows)} · 测试 ${E(pf.classification.test_rows)}</p>`:''}
      ${(pf.blockers||[]).map(t=>`<div class="helper warning">${E(t)}</div>`).join('')}
      ${(pf.warnings||[]).map(t=>`<p class="muted small">${E(t)}</p>`).join('')}`:'<p class="muted">尚未执行预检查</p>';
    const actions=['review_required','ready'].includes(active.status)
      ? btn('edit','调整方案')+(pf?.ready?btn('approve','确认并锁定',true):btn('preflight','重新检查',true))
      : active.status==='approved'?btn('materialize','生成训练数据',true)
      : active.status==='dataset_ready'?btn('submit','开始建模',true)
      : active.run_id?`<a class="button button-primary" href="/runs/${U(active.run_id)}">查看运行任务</a>`:btn('preflight','检查数据');
    slot().innerHTML=`<div class="tp-modeling-source"><b>${E(active.name)}</b><p>任务：${E(active.plan.task_kind)} · 目标 ${E(active.mapping.target)} · 原始版本 ${E(active.version_id)} · 状态 ${E(active.status)}</p></div>${checks}<div class="action-row">${actions}${btn('lineage','查看版本和审计记录')}</div>`;
  }
  const splitValues=x=>String(x||'').split(/[,，]/).map(s=>s.trim()).filter(Boolean);
  const optionalNum=x=>String(x??'').trim()===''?null:Number(x);
  async function submit(event){
    if(event.target?.id!=='tp-modeling-form')return false;
    event.preventDefault();const f=Object.fromEntries(new FormData(event.target));
    const kind=f.task_kind;const features=new FormData(event.target).getAll('observed_features');
    const mapping={time:f.time,entity:f.entity||null,batch:kind==='classification'?(f.batch||null):null,
      target:f.target,target_unit:f.target_unit?.trim()||null,
      timezone:f.timezone,available_at:f.availability_policy==='source_column'?f.available_at:null,
      availability_policy:f.availability_policy,
      publication_lag_days:f.availability_policy==='declared_lag'?Number(f.publication_lag_days):null,
      observed_features:features,event:{column:f.event_column||null,truthy_values:splitValues(f.event_truthy),
                                         reset_policy:f.event_reset_policy},
      regime:{column:f.regime_column||null}};
    const multimodal=f.feature_policy==='historical_observed';
    const plan={task_kind:kind,task_family:kind==='classification'?'equipment_state':f.task_family,frequency:f.frequency,
      horizon_steps:kind==='classification'?1:Number(f.horizon_steps),
      validation_windows:kind==='classification'?2:Number(f.validation_windows),
      min_history:kind==='classification'?24:Number(f.min_history),
      primary_metric:kind==='classification'?'MAE':f.primary_metric,
      feature_policy:kind==='classification'?'snapshot_at_decision':f.feature_policy,
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
      if(active){active=await ctx.req(base()+'/drafts/'+U(active.draft_id),json('PATCH',{expected_revision:active.revision,name:f.name,mapping,plan}));}
      else{active=await ctx.req(base()+'/drafts',post({asset_id:aid,version_id:source.version_id,view_id:source.view_id,
         filters:source.view_id?null:source.filters,selection_id:source.selection_id,name:f.name,mapping,plan,confirm_scope:true}));}
      active.preflight=await ctx.req(base()+'/drafts/'+U(active.draft_id)+'/preflight',{method:'POST'});
      active.status=active.preflight.ready?'ready':'review_required';renderPreflight();status(active.preflight.ready?'预检查通过，待人工确认':'发现阻断项，请调整字段或规则');
    }catch(err){status(err.message,'error');ctx.toast?.(err.message);}
    return true;
  }
  async function click(event){
    const el=event.target.closest('[data-modeling]');if(!el)return false;
    event.preventDefault();const action=el.dataset.modeling;
    try{
      if(action==='close'){document.getElementById('tp-modeling-dialog')?.remove();return true;}
      if(action==='back'){renderSuggestions();return true;}
      if(action==='recommend'){selectedKind=suggestions.proposals[Number(el.dataset.index)].task_kind;renderMapping();return true;}
      if(action==='edit'){renderMapping();return true;}
      if(!active)return true;
      const endpoint=base()+'/drafts/'+U(active.draft_id);
      if(action==='preflight'){active.preflight=await ctx.req(endpoint+'/preflight',{method:'POST'});renderPreflight();}
      if(action==='approve'){
        if(!confirm('确认目标/标签、特征采集时刻、维护事件与工况含义，以及数据可用时间？'))return true;
        active=await ctx.req(endpoint+'/approve',post({expected_revision:active.revision,confirm:true,
          acknowledge_single_target:true,acknowledge_availability:true}));renderPreflight();status('已完成任务审批');}
      if(action==='materialize'){status('正在生成独立训练快照…');active=await ctx.req(endpoint+'/materialize',{method:'POST'});renderPreflight();status('训练数据已冻结');}
      if(action==='submit'){const out=await ctx.req(endpoint+'/submit',{method:'POST'});active.run_id=out.run_id;active.status='queued';renderPreflight();status('任务已入队，可在项目模型管理查看');}
      if(action==='lineage'){const audit=await ctx.req(endpoint+'/audit?limit=5');
        alert(JSON.stringify({dataset_id:audit.dataset_id,source_hash:audit.selection_hash,
          resampling:audit.resampling_summary,first_buckets:audit.buckets},null,2));}
    }catch(err){status('操作未完成：'+err.message,'error');ctx.toast?.(err.message);}
    return true;
  }
  window.TimePilotModeling={open,openDraft,click,submit,change};
})();
