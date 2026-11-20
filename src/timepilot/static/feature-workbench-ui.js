/* v0.33: evidence-led feature workbench. No chart is built from guessed data. */
(() => {
  'use strict';
  const V=window.TimePilotFeatureViews=window.TimePilotFeatureViews||{};
  const fmt=x=>x==null||!Number.isFinite(Number(x))?'—':Number(x).toLocaleString('zh-CN',{maximumFractionDigits:3});
  const pct=x=>x==null?'—':(100*Number(x)).toFixed(1)+'%';
  const labels={explore:'自动发现特征',anomaly:'异常与波动',quality:'过程质量',classification:'分类识别',regression:'数值预测',forecast:'时间预测'};
  const statuses={recommended:'质量通过',review:'待复核',exclude:'建议排除',unverified:'待计算'};
  const running=new Set(['profiling','previewing','materializing','evaluating']);
  const families={transform:'基础变换',interaction:'变量交互',history:'历史变化',statistics:'窗口统计',anomaly:'异常与波动',category:'类别指标',event:'维护与事件',mechanism:'机理与维护重置',advanced:'高级算法',other:'其他'};
  const familyOf=c=>c?.family||({'arithmetic.binary':'interaction','category.is_value':'category',
    'difference.rows':'history','lag.rows':'history','relative.change':'history',
    'rolling.mean':'statistics','rolling.median':'statistics','rolling.p90':'statistics',
    'rolling.std':'anomaly','rolling.range':'anomaly','rolling.zscore':'anomaly',
    'event.since':'event','industrial.reset_mean':'mechanism','industrial.reset_sum':'mechanism'}[c?.operator_id]||(String(c?.operator_id||'').startsWith('transform.')?'transform':'other'));

  function insight(data,h){
    const original=data?.explanation||{};
    const proof=original.evidence_analysis||{};
    const claims=proof.status==='supported'?(proof.claims||[]):[];
    const scope=data?.mode==='series';
    const description=original.what_is_shown||'当前结果由源数据计算；尚未获得图表轴信息。';
    const cautions=[...(original.cautions||[]),...(proof.limitations||[])];
    const main=claims.length?claims.map(c=>`<article class="tp-wb-evidence-claim"><strong>${h.e(c.title)}</strong><p>${h.e(c.observation)}</p><details><summary>查看数值依据</summary><p>证据 ID：<code>${h.e(c.id)}</code></p>${(c.evidence||[]).map(x=>`<p><code>${h.e(x.path)}</code> = ${h.e(fmt(x.value))}</p>`).join('')}<p>来源 SHA256：${h.e(c.scope?.source_sha256||'')}</p></details></article>`).join(''):
      `<p>${h.e(proof.message||'当前缺少完整的计算证据，无法判断。')}</p>`;
    return `<div class="tp-wb-chart-insight" data-testid="feature-chart-explanation">
      <h4>怎么看这张图</h4><p>${h.e(description)}</p><h4>这次数据看到了什么</h4>${main}
      ${cautions.length?`<p class="tp-wb-warning">${h.e(cautions.join(' '))}</p>`:''}
      <p><b>接下来：</b>${h.e((original.recommended_action||{}).label||(scope?'检查原始记录与采样顺序':'查看原始记录及模型评价'))}。当前统计不包含预测收益判断。</p>
      <details><summary>图表计算定义和来源</summary><p>${h.e(description)}</p><p>数据版本：${h.e(data?.version_id||'')} · 字段契约修订：${h.e(data?.contract_revision??'—')}</p><p>候选特征：${h.e(data?.feature_name||'')}</p></details></div>`;
  }
  function plot(data,h){
    if(!data||data.status!=='ready')return `<p class="tp-wb-muted">${h.e(data?.message||'选中特征后加载真实计算图表。')}</p>`;
    const rows=data.points||[];
    if(!rows.length)return '<p class="tp-wb-muted">没有可绘制的有限数值；可以查看质量指标和原始来源。</p>';
    const num=v=>v===null||!Number.isFinite(Number(v))?null:Number(v);
    const width=760,height=256,left=54,right=14,top=18,bottom=48;
    const plotWidth=width-left-right,plotHeight=height-top-bottom;
    const isSeries=data.mode==='series';
    let body='',max=1;
    if(isSeries){
      const originalAllowed=data.axis_compatible!==false;
      const valid=rows.flatMap(r=>[num(r.value),originalAllowed?num(r.original):null,num(r.min),num(r.max)]).filter(v=>v!==null);
      if(!valid.length)return '<p class="tp-wb-muted">这一范围内没有有效数值。</p>';
      const low=Math.min(...valid),high=Math.max(...valid),range=high-low||1;
      const xx=i=>left+(rows.length<2?0:i/(rows.length-1)*plotWidth),yy=v=>top+(high-v)/range*plotHeight;
      body+=rows.map((r,i)=>{
        const a=num(r.min),b=num(r.max);if(a===null||b===null)return '';
        return `<line x1="${xx(i).toFixed(2)}" x2="${xx(i).toFixed(2)}" y1="${yy(a).toFixed(2)}" y2="${yy(b).toFixed(2)}" stroke="#de8b30" stroke-width="1.7"><title>${h.e(r.x)}—${h.e(r.x_end)}：最小 ${h.e(fmt(a))}，最大 ${h.e(fmt(b))}</title></line>`;
      }).join('');
      for(const [key,stroke] of (originalAllowed?[['value','#2867c3'],['original','#11a78c']]:[['value','#2867c3']])){
        let segments=[],part=[];
        rows.forEach((r,i)=>{const n=num(r[key]);if(n===null){if(part.length)segments.push(part);part=[];return;}part.push(`${xx(i).toFixed(2)},${yy(n).toFixed(2)}`);});
        if(part.length)segments.push(part);
        body+=segments.map(part=>`<polyline points="${part.join(' ')}" fill="none" stroke="${stroke}" stroke-width="2.2" stroke-linejoin="round"/>`).join('');
      }
      body+=`<text x="7" y="${top+10}" class="tp-wb-axis">${h.e(fmt(high))}</text><text x="7" y="${top+plotHeight}" class="tp-wb-axis">${h.e(fmt(low))}</text>`;
    } else {
      const items=rows.map(r=>({label:r.label,value:num(data.mode==='distribution'?r.count:(r.mean??r.count))})).filter(r=>r.value!==null);
      const hi=Math.max(0,...items.map(r=>r.value));
      const lo=Math.min(0,...items.map(r=>r.value));
      const span=hi-lo||1;
      const zero=top+plotHeight*hi/span;
      const bw=plotWidth/Math.max(1,items.length),col=Math.max(2,bw-7);
      body=items.map((item,i)=>{const val=item.value,bar=Math.abs(val)/span*plotHeight;
        const x=left+i*bw+3,y=val>=0?zero-bar:zero;
        const label=item.label.length>10?item.label.slice(0,9)+'…':item.label;
        const mark=i%Math.max(1,Math.ceil(items.length/9))===0?`<text x="${(x+col/2).toFixed(2)}" y="${height-19}" text-anchor="middle" class="tp-wb-axis">${h.e(label)}</text>`:'';
        return `<rect x="${x.toFixed(2)}" y="${y.toFixed(2)}" width="${col.toFixed(2)}" height="${bar.toFixed(2)}" rx="2" fill="#3677cf"/><title>${h.e(item.label)}: ${h.e(fmt(item.value))}</title>${mark}`;
      }).join('');
      body+=`<line x1="${left}" y1="${zero.toFixed(2)}" x2="${width-right}" y2="${zero.toFixed(2)}" stroke="#94a7bf" stroke-dasharray="3 3"/>
          <text x="7" y="${top+10}" class="tp-wb-axis">${h.e(fmt(hi))}</text><text x="7" y="${top+plotHeight}" class="tp-wb-axis">${h.e(fmt(lo))}</text>`;
    }
    const title={distribution:'特征全量分布',series:'有序记录特征对照',category:'分类维度汇总',group:'设备分组汇总'}[data.mode]||'特征图表';
    return `<svg role="img" aria-label="${h.e(title+'：'+data.feature_name)}" viewBox="0 0 ${width} ${height}" class="tp-wb-svg"><line x1="${left}" y1="${top+plotHeight}" x2="${width-right}" y2="${top+plotHeight}" stroke="#becbd9"/>${body}</svg>
      <div class="tp-wb-legend">${isSeries?`<span><i class="tp-wb-dot" style="background:#2867c3"></i>新特征</span>${data.axis_compatible===false?'':`<span><i class="tp-wb-dot" style="background:#11a78c"></i>原字段</span>`}`:''}<span>${h.e(data.mode==='series'?`${data.population_count??rows.reduce((n,r)=>n+(Number(r.count)||1),0)} 条记录 / ${rows.length} 个时间桶（蓝色均值、橙色极值；设备 ${data.entity_value||'未分组'}）`:'对全部已计算记录聚合')}</span></div>
      ${insight(data,h)}<details class="tp-wb-proof"><summary>图表数据与计算来源（技术详情）</summary><div class="tp-wb-samples">${rows.slice(0,100).map(r=>`<div>${h.e(r.x??r.label)}：${h.e(fmt(r.value??r.mean??r.count))}${r.count!=null&&r.mean!=null?'（'+h.e(fmt(r.count))+' 条）':''}</div>`).join('')}</div><small>源数据 SHA256：${h.e(data.source_sha256)} · 特征指纹：${h.e(data.candidate_fingerprint)} · 契约修订 ${h.e(data.contract_revision)}</small></details>`;
  }
  function featureDetails(s,h,focus){
    if(!focus)return '<div class="tp-wb-placeholder">选择左侧候选，查看计算方法、质量指标与实际图表。</div>';
    const rows=s.autoSession.report.quality_rows;
    const source=(s.candidateSession?.candidates||[]).find(c=>c.candidate_id===focus.candidate_id)||{};
    const candidates=(s.candidateSession?.candidates||[]);
    const feat=candidates.find(c=>c.candidate_id===focus.candidate_id)||source;
    const allowed=!s.autoSession.stale&&s.uiChart?.candidate_id===focus.candidate_id&&
      s.uiChart?.source_sha256===s.autoSession.source_sha256&&
      s.uiChart?.version_id===(s.version||'base')&&
      s.uiChart?.contract_revision===s.contract?.revision;
    const chart=allowed?s.uiChart:null;
    const modes=chart?.available_modes||['distribution','series','category','group'];
    // Per-feature evidence is shown only for the verified, pinned artifact.
    // A fold selection frequency is NOT a causal effect or production gain.
    const proof=s.autoDelivery;
    const feedback=s.autoSession?.model_feedback;
    const same=!!(proof?.same_artifact_evaluation && feedback &&
      feedback.artifact_sha256===proof?.artifact?.sha256 &&
      proof?.artifact?.sha256===s.autoSession?.artifact?.sha256);
    const freq=same?(feedback.selection_frequency||[]).find(x=>
      x.feature===focus.output_name||x.feature_name===focus.output_name):null;
    const modelEvidence=freq?`${freq.selected_in_folds} / ${freq.total_folds} 折入选`:'尚未验证';
    const mechParams=feat.parameters||{};
    const isMechanism=String(feat.operator_id||focus.operator_id||'').startsWith('industrial.reset_');
    const sourceField=(s.contract?.fields||[]).find(x=>x.field_id===feat.input_field_ids?.[0]);
    const mechanismInfo=isMechanism?`<div class="tp-wb-group-evidence" data-testid="mechanism-evidence"><b>工业机理计算约束</b>
       <p>输入单位：${h.e(sourceField?.unit||'未确认')} · 维护重置字段 ${h.e(mechParams.reset_field_id||'未确认')} · 工况字段 ${h.e(mechParams.regime_field_id||'未设置')}</p>
       <p>依据：${h.e(mechParams.mechanism_reference||'未提供')}</p>
       <small>仅从首次维护事件后开始计算，设备及工况切换分段；观测值累计和只接受已人工确认的逐次增量。该特征不提供物理因果或生产收益证明。</small></div>`:'';
    return `<div id="tp-wb-feature-detail" class="tp-wb-detail" data-testid="feature-detail"><header><div><h3>${h.e(focus.display_name||focus.output_name)}</h3><span class="tp-wb-muted">${h.e(focus.output_name)}</span></div><span class="tp-wb-state ${h.e(focus.status)}">${h.e(statuses[focus.status]||focus.status)}</span></header>
      <p class="tp-wb-desc"><strong>${h.e(families[familyOf(feat)]||'候选特征')}</strong> · ${h.e(feat.reason||focus.reason||'已完成计算；请结合质量和具体任务审查。')}</p>
      ${window.TimePilotOperatorHelp?.render(feat.operator_id||focus.operator_id)||''}
      ${mechanismInfo}<div class="tp-wb-figures"><div><small>有效覆盖率</small><strong>${pct(1-(focus.quality?.null_rate??1))}</strong></div><div><small>均值</small><strong>${fmt(focus.quality?.mean)}</strong></div><div><small>标准差</small><strong>${fmt(focus.quality?.std)}</strong></div><div><small>训练折选择证据</small><strong>${h.e(modelEvidence)}</strong></div></div>
      ${s.uiChartMode==='series'?`<div class="tp-wb-series-controls"><label>分组 <select data-feature-v2-change="wb-chart-entity-field" aria-label="选择特征图表分组字段"><option value="">自动选择</option>${(chart?.entity_field_options||[]).map(x=>`<option value="${h.e(x.value)}" ${s.uiChartEntityField===x.value?'selected':''}>${h.e(x.label)}</option>`).join('')}</select></label><label>设备 <select data-feature-v2-change="wb-chart-entity" aria-label="选择图表设备"><option value="" ${!s.uiChartEntity?'selected':''}>自动选择设备</option>${(chart?.entity_options||[]).map(x=>`<option value="${h.e(x.value)}" ${String(s.uiChartEntity)===String(x.value)?'selected':''}>${h.e(x.value)} · ${h.e(x.row_count)} 条</option>`).join('')}</select></label><label>分辨率 <select data-feature-v2-change="wb-chart-resolution">${[60,120,240].map(n=>`<option value="${n}" ${(s.uiChartResolution||120)===n?'selected':''}>${n} 桶</option>`).join('')}</select></label></div>`:''}
      <div class="tp-wb-chart-head"><b>特征变化与统计</b><div class="tp-wb-modes" role="group" aria-label="切换图表">${[['distribution','分布'],['series','趋势'],['category','分类'],['group','设备']].filter(([k])=>modes.includes(k)||!chart).map(([k,v])=>`<button type="button" data-feature-v2="wb-chart" data-chart="${k}" class="${(s.uiChartMode||'distribution')===k?'active':''}">${v}</button>`).join('')}</div></div>
      <div id="tp-wb-chart" aria-live="polite">${s.uiChartStatus==='loading'?'<p class="tp-wb-muted">正在按当前来源重新计算图表…</p>':s.uiChartStatus==='error'?`<p class="tp-wb-error">${h.e(s.uiChartError||'图表读取失败')}</p>`:plot(chart,h)}</div>
      <details class="tp-wb-proof"><summary>计算定义与质量证据</summary><p>算子：<code>${h.e(focus.operator_id)}</code></p><p>输入字段：${h.e((feat.input_field_ids||[]).join('、')||'见原始计算配方')}</p><pre>${h.e(JSON.stringify(feat.parameters||{},null,2))}</pre><p>${h.e(focus.reason||'')}</p><p>实测有效记录 ${h.e(fmt(focus.quality?.finite_count))} / ${h.e(fmt(focus.quality?.rows_scanned))}。通过质量检查不代表能提高预测效果；即便训练折多次入选，仍需独立验证生产收益。</p></details>
    </div>`;
  }
  function advancedDetails(s,h,item){
    const batch=(s.advancedBatches?.batches||[]).find(b=>b.batch_id===item.batch_id)||{};
    const stale=batch.status!=='review_required';
    const joined=batch.join, evaluated=batch.evaluation;
    const progress=batch.workflow_status==='model_evaluated'?'模型对照已完成':joined?'PIT 回接已核验':'待核验 PIT 关联';
    const url='/projects/'+h.u(s.pid)+'/analysis?tool=advanced&asset='+h.u(s.asset)+
      '&version='+h.u(s.version||'base')+'&job='+h.u(batch.job_id||'');
    return `<div id="tp-wb-feature-detail" class="tp-wb-detail" data-testid="advanced-feature-detail"><header><div><h3>${h.e(item.display_name)}</h3><span class="tp-wb-muted">${h.e(batch.engine||'高级特征')} · 已生成的真实候选列</span></div><span class="tp-wb-state">${stale?'来源失效':progress}</span></header>
       <p>来自受控 ${h.e(batch.engine||'advanced')} 任务；已单独计算到高级候选数据资产。候选可能是每个预测实例一行，不能直接和逐条记录特征合并。</p>
       <div class="tp-wb-figures"><div><small>候选批次</small><strong>${h.e(batch.candidate_count)}</strong></div><div><small>结果记录数</small><strong>${h.e(batch.rows)}</strong></div><div><small>模型效果</small><strong>${evaluated?'可查看同源评价':'未评价'}</strong></div></div>
       <p class="tp-wb-muted">${stale?h.e(batch.issue||'来源指纹已失效'):evaluated?'已取得与同一 PIT 物化产物绑定的模型评价证据；需结合指标、折间稳定性和业务目标审核。':joined?'已回接预测实例，可以进入高级模型评价或查看已核验的模型输入数据。':'下一步需确认预测实例主键、可用时间和预测截止时间，再执行 PIT 回接与同折模型评价。'}</p>
       ${!stale?`<a class="button button-primary" href="${url}">${joined?'继续高级模型评价':'核查关联并进入模型评价'}</a><a class="button" href="/projects/${h.u(s.pid)}/bi/native/${h.u(batch.native_asset_id)}">在 BI 查看高级候选</a>`:''}
       ${joined?`<p>模型输入已回接 ${h.e(joined.rows)} 行 · SHA256 ${h.e(joined.joined_sha256?.slice(0,16))}</p>`:''}${evaluated?`<p>模型评价来源已核验。评价 ID：${h.e(evaluated.evaluation_id?.slice(0,16))}，请在高级工作台查看同折比较。</p>`:''}<details class="tp-wb-proof"><summary>计算来源与 SHA256</summary><p>算法 ${h.e(batch.engine)} · 任务 ${h.e(batch.job_id)}</p><p>来源指纹 ${h.e(batch.source_fingerprint)}</p><p>高级数据产物 SHA256 ${h.e(batch.artifact_sha256)}</p><p>配方 SHA256 ${h.e(batch.recipe_sha256)}</p></details>
      </div>`;
  }
  function analysis(s,h){
    const a=s.autoSession,r=a?.report,items=r?.quality_rows||[];
    const source=s.assets?.find(x=>x.native_asset_id===s.asset);
    const name=source?.asset?.name||s.asset||'请选择数据';
    const runningTask=a&&running.has(a.status),error=a?.status==='failed',stale=a?.stale;
    const review=a?.status==='ready'&&a.stage==='review'&&!stale;
    const needsInput=a?.status==='needs_input';
    const retryEvaluation=a?.status==='ready'&&a?.stage==='materialized'&&!!a?.evaluation_error&&!!a?.evaluation_job_id&&!stale;
    const filter=s.uiFilter||'all';
    const chosen=s.uiDraftSelection||a?.selection||[];
    const raw=(s.candidateSession?.candidates||[]);
    const catalog=Object.fromEntries(raw.map(x=>[x.candidate_id,x]));
    const family=s.uiFamily||'all';
    const availableFamilies=[...new Set(items.map(x=>familyOf(catalog[x.candidate_id]||x)))];
    const view=items.filter(x=>(family==='all'||familyOf(catalog[x.candidate_id]||x)===family)&&
        (filter==='all'||x.status===filter||filter==='selected'&&chosen.includes(x.candidate_id)))
        .sort((a,b)=>familyOf(catalog[a.candidate_id]||a).localeCompare(familyOf(catalog[b.candidate_id]||b)));

    const advanced=(s.advancedBatches?.batches||[]).flatMap(b=>(b.candidates||[]));
    const visibleAdvanced=(family==='all'||family==='advanced')&&filter==='all'?advanced:[];
    const advancedFocus=visibleAdvanced.find(x=>x.candidate_id===s.uiAdvancedFocus)||null;
    const focus=advancedFocus?null:(view.find(x=>x.candidate_id===s.uiFocused)||view[0]||null);
    const activeFocus=focus?.candidate_id;
    const counts=[[fmt(r?.candidate_count??0),'基础候选'],[fmt(r?.recommended_count??0),'质量通过'],[fmt((r?.review_count||0)+(r?.excluded_count||0)),'需复核或排除'],[fmt(s.advancedBatches?.candidate_count||0),'高级候选']];
    const form=`<form id="tp-fl2-auto-form" class="tp-wb-goal-form"><label for="tp-wb-objective">分析目标</label><select name="task_hint" id="tp-wb-objective" data-feature-v2-change="wb-objective">${Object.entries(labels).map(([k,v])=>`<option value="${k}" ${k===(s.autoObjective&&s.autoObjective!=='auto'?s.autoObjective:a?.objective||'explore')?'selected':''}>${v}</option>`).join('')}</select><label>搜索范围 <select name="budget" aria-label="特征搜索预算"><option value="16">快速 · 16 项</option><option value="32">标准 · 32 项</option><option value="64">深入 · 64 项</option></select></label><details class="tp-wb-optional"><summary>补充分析要求（可选）</summary><textarea name="goal" rows="2" maxlength="1000" placeholder="例如：重点分析设备压力的变化">${h.e(s.autoGoal||a?.goal||'')}</textarea></details><button type="submit" class="button button-primary">${r?'重新分析':'自动分析特征'}</button></form>`;
    const status=`<div class="tp-wb-status ${error?'is-error':needsInput?'needs-input':stale?'is-stale':''}" role="status"><div><strong>${stale?'来源已变化，需要重新分析':error?'分析失败':needsInput?'需要确认数据':runningTask?'分析任务正在执行':review?'候选已计算，等待审核':r?'分析成果已生成':'尚未开始特征分析'}</strong><small>${stale?'历史结果仅供查看，不能保存或正式计算':error?h.e(a.error||'可以检查失败信息后重新运行'):needsInput?h.e((a.warnings||[]).join('；')||'请检查字段契约'):(runningTask?'已完成的阶段记录不会因页面刷新而丢失':r?'已完成真实计算和质量筛选；尚未证明模型收益':'选择目标后，一键生成可审查的特征候选')}</small></div>${runningTask?`<div class="tp-wb-progress"><div style="width:${Math.min(100,Math.max(0,Number(a.progress||0)*100))}%"></div></div><button type="button" data-feature-v2="auto-refresh" class="button button-small">刷新</button>`:''}${(error||stale||needsInput)?'<button type="button" class="button button-small" data-feature-v2="edit-contract">检查字段</button>':''}${!stale&&(error||retryEvaluation)&&a?.recovery?.retryable?'<button type="button" class="button button-small" data-feature-v2="auto-retry">重试失败阶段</button>':''}${retryEvaluation?'<small>模型评价失败，已生成的特征产物仍然保留。</small>':''}</div>`;
    const main=`${(r||s.advancedBatches?.candidate_count)?`<div class="tp-wb-kpis">${counts.map(([v,t])=>`<div><strong>${v}</strong><small>${t}</small></div>`).join('')}</div><section class="tp-wb-results"><header class="tp-wb-title"><h2>候选特征</h2><span class="tp-wb-muted">选中特征后显示实际计算结果</span></header>
      <div class="tp-wb-columns"><div class="tp-wb-list">
      <div class="tp-wb-family-filter" role="group" aria-label="特征类型">
        <button type="button" data-feature-v2="wb-family" data-family="all" class="${family==='all'?'active':''}">全部类型</button>
        ${availableFamilies.map(k=>`<button type="button" data-feature-v2="wb-family" data-family="${h.e(k)}" class="${family===k?'active':''}">${h.e(families[k]||k)} ${items.filter(x=>familyOf(catalog[x.candidate_id]||x)===k).length}</button>`).join('')}
        ${(s.advancedBatches?.candidate_count||0)?`<button type="button" data-feature-v2="wb-family" data-family="advanced" class="${family==='advanced'?'active':''}">高级算法 ${s.advancedBatches.candidate_count}</button>`:''}
      </div><div class="tp-wb-filters" role="group" aria-label="特征筛选">${[['all','全部'],['recommended','通过'],['review','待复核'],['exclude','排除'],['selected','已选']].map(([k,t])=>`<button type="button" data-feature-v2="wb-filter" data-filter="${k}" class="${filter===k?'active':''}">${t}</button>`).join('')}</div>
      <form id="tp-fl2-auto-select"><div class="tp-wb-items">${view.length?view.map((x,i)=>`${i===0||familyOf(catalog[view[i-1].candidate_id]||view[i-1])!==familyOf(catalog[x.candidate_id]||x)?`<div class="tp-wb-group-title">${h.e(families[familyOf(catalog[x.candidate_id]||x)]||'其他特征')}</div>`:''}<div class="tp-wb-item ${x.candidate_id===activeFocus?'focused':''}"><input class="tp-wb-item-checkbox" type="checkbox" name="candidate_id" data-feature-v2-change="wb-choice" aria-label="选择${h.e(x.display_name||x.output_name)}" value="${h.e(x.candidate_id)}" ${chosen.includes(x.candidate_id)?'checked':''} ${!review||x.status==='unverified'?'disabled':''}><button class="tp-wb-item-open" type="button" data-feature-v2="wb-focus" data-cid="${h.e(x.candidate_id)}" aria-controls="tp-wb-feature-detail" ${x.candidate_id===activeFocus?'aria-current="true"':''}><strong>${h.e(x.display_name||x.output_name)}</strong><small>${h.e(families[familyOf(catalog[x.candidate_id]||x)]||'候选')} · ${h.e(statuses[x.status]||x.status)} · 完整率 ${pct(1-(x.quality?.null_rate??1))}</small></button></div>`).join(''):''}
        ${visibleAdvanced.length?'<div class="tp-wb-group-title">高级算法 · 待 PIT 回接</div>':''}${visibleAdvanced.map(x=>`<div class="tp-wb-item tp-wb-advanced-item ${x.candidate_id===s.uiAdvancedFocus?'focused':''}"><span class="tp-wb-advanced-icon" aria-label="待 PIT 回接">高</span><button class="tp-wb-item-open" type="button" data-feature-v2="wb-advanced-focus" data-cid="${h.e(x.candidate_id)}" aria-controls="tp-wb-feature-detail"><strong>${h.e(x.display_name)}</strong><small>${h.e(x.engine)} · ${h.e((s.advancedBatches?.batches||[]).find(b=>b.batch_id===x.batch_id)?.workflow_status==='model_evaluated'?'已评价':x.status==='pit_join_verified'?'已关联':'待关联及评价')}</small></button></div>`).join('')}
        ${(!view.length&&!visibleAdvanced.length)?'<p class="tp-wb-muted">没有符合筛选条件的候选。</p>':''}</div><div class="tp-wb-list-actions"><span>${chosen.length} 项已选</span><button class="button button-small" type="submit" ${review?'':'disabled'}>保存选择</button></div></form></div>
      ${advancedFocus?advancedDetails(s,h,advancedFocus):featureDetails(s,h,focus)}</div></section>`:''}`;
    // The v0.33.1 workbench overrides legacy analysis UI; the supervised
    // selector must live inside the *visible* save form. No auto-enrollment.
    const supervision=s.autoSupervision||{};
    const staticAllowed=!!supervision.static_eligible;
    const temporalAllowed=!!(supervision.temporal_eligible??supervision.eligible);
    const selector=(fieldName,options,placeholder)=>`<select name="${fieldName}" aria-label="${h.e(placeholder)}"><option value="">请选择${h.e(placeholder)}</option>${(options||[]).map(f=>`<option value="${h.e(f.field_id)}">${h.e(f.name)} · ${h.e(f.role)}</option>`).join('')}</select>`;
    const splits=[...(staticAllowed?((supervision.entity_field_ids||[]).length?[['group','设备/批次分组 K 折']]:
      (supervision.task_type==='binary_classification'?[['stratified','二分类分层 K 折'],['kfold','独立样本 K 折']]:[['kfold','独立样本 K 折']])):[]),
        ...(temporalAllowed?[['time','按时间滚动验证'],['grouped_time','分组时间验证']]:[])];
    const defaultSplit=staticAllowed?supervision.static_default_split:(temporalAllowed?'time':'');
    const supervisedEditor=staticAllowed||temporalAllowed?`<details class="tp-wb-proof tp-wb-supervised" data-testid="supervised-evaluation">
      <summary>模型效果对照（可选，需已确认的标签及切分）</summary>
      <label><input type="checkbox" name="enable_supervised" value="yes"> 在正式计算后评价原始字段、全部特征与筛选后特征</label>
      <label>目标标签 ${selector('label_field_id',supervision.label_fields,'目标标签')}</label>
      <label>验证方式 <select name="split_kind" data-feature-v2-change="supervision-split" aria-label="验证方式">${splits.map(([key,label])=>`<option value="${key}" ${key===defaultSplit?'selected':''}>${label}</option>`).join('')}</select></label>
      ${staticAllowed?`<div class="tp-wb-static-confirm" ${defaultSplit==='time'||defaultSplit==='grouped_time'?'hidden':''}><label><input type="checkbox" name="static_independence_confirmed" value="yes"> 我已确认各样本独立，所有输入字段在预测决策时可以使用；含同一设备/批次多条记录时必须采用分组验证</label></div>`:''}
      ${temporalAllowed?`<div class="tp-wb-time-fields" ${defaultSplit!=='time'&&defaultSplit!=='grouped_time'?'hidden':''}><label>预测时点 ${selector('prediction_time_field_id',supervision.prediction_time_fields,'预测时点')}</label>
        <label>标签可用时间 ${selector('label_available_at_field_id',supervision.label_available_fields,'标签可用时间')}</label></div>`:''}
      <p class="tp-wb-muted">有模型评价须使用同一训练/验证折对照；特征筛选与缺失填充只使用训练折。未勾选时只生成特征数据，不宣称模型收益。</p>
    </details>`:`<p class="tp-wb-muted">当前尚无可执行的监督评价契约；可先完成特征工程，或在数据管理核实标签、时间与设备字段。</p>`;
    const proof=s.autoDelivery;
    const proven=proof?.same_artifact_evaluation&&proof?.evaluation?.mode==='supervised'&&
      proof?.artifact?.sha256===a?.artifact?.sha256?proof.evaluation:null;
    const diagnosis=s.dataAnalysis?.status==='ready' &&
      s.dataAnalysis?.source_sha256===s.autoSession?.source_sha256 &&
      s.dataAnalysis?.contract_revision===s.contract?.revision?s.dataAnalysis:null;
    const diagnostic=diagnosis?`<section class="tp-wb-diagnostic" data-testid="source-diagnosis"><div><b>自动数据检查</b><span>${h.e(diagnosis.row_count||0)} 行 · ${h.e(diagnosis.column_count||0)} 列 · ${h.e((diagnosis.findings||[]).length)} 项提示</span></div>${(diagnosis.findings||[]).slice(0,3).map(x=>`<p>${h.e(x.summary)}</p>`).join('')||'<p>基本字段与数据质量检查完成。</p>'}<a href="/projects/${h.u(s.pid)}/analysis?asset=${h.u(s.asset)}&version=${h.u(s.version||'base')}">查看完整数据分析与真实图表</a></section>`:
      `<section class="tp-wb-diagnostic"><b>数据分析</b><p>可先打开数据分析查看来源质量与统计图表；候选仍按当前数据契约执行。</p><a href="/projects/${h.u(s.pid)}/analysis?asset=${h.u(s.asset)}&version=${h.u(s.version||'base')}">查看数据分析</a></section>`;
    const ablation=proven?.feature_group_ablation?.groups||[];
    const groupEvidence=ablation.length?`<div class="tp-wb-group-evidence"><b>特征组消融（同折验证）</b>${ablation.slice(0,3).map(x=>`<p>${h.e(x.group)}：删除后指标损失 ${h.e(fmt(x.mean_loss_when_removed))}，${h.e(x.positive_loss_folds)} / ${h.e(x.evaluated_folds)} 折模型表现变差</p>`).join('')}<small>同协议重新训练的预测敏感性，不能证明因果贡献或上线收益。</small></div>`:'';
    const holdout=proven?.industrial_transfer_holdout?.status==='measured'?proven.industrial_transfer_holdout:null;
    const holdoutModel=proven?.model_families?.[0]||'linear';
    const holdoutMetric=proven?.task_type==='regression'?'mae':'average_precision';
    const holdoutEvidence=holdout?`<div class="tp-wb-group-evidence" data-testid="industrial-transfer-evidence"><b>独立设备/工况留出评价</b>
      <p>留出 ${h.e(holdout.holdout_values?.join('、')||'')} · 训练 ${h.e(holdout.train_rows)} 条 · 留出 ${h.e(holdout.holdout_rows)} 条。</p>
      <p>${h.e(holdoutMetric)}：原始 ${fmt(holdout.model_metrics?.[holdoutModel]?.raw_only?.[holdoutMetric])}；增强 ${fmt(holdout.model_metrics?.[holdoutModel]?.with_features?.[holdoutMetric])}。</p>
      <small>${h.e(holdout.interpretation)} 该评价不得用于再选特征；上游候选生成仍可能使用全数据画像。</small></div>`:'';
    const paired=proven?.paired_comparison?.metrics||{};
    const metric=paired.mae?'MAE（越低越好）':paired.average_precision?'AP（越高越好）':null;
    const improvement=paired.mae||paired.average_precision;
    const feedback=proven?`<div class="tp-wb-evaluation-summary" data-testid="verified-supervised-feedback"><b>已完成同源模型效果对照</b>
      <p>验证方式：${h.e(proven.split_kind||'已确认协议')} · ${h.e(proven.fold_count||0)} 折 · 同一模型、同一训练/验证折</p>
      ${metric?`<p>${metric}：筛选/全部特征中已记录的同折改善指标见详情；${h.e(improvement.n_paired_folds||0)} 折参与对照；同折平均改善 ${fmt(improvement.mean_improvement)}。</p>`:''}
      <p>训练折特征选择一致性 Jaccard：${fmt(proven.selection_stability?.mean_jaccard)}（仅反映跨折稳定性）。</p>
      <p class="tp-wb-muted">此处为验证集对照，并不能证明上线效果或因果关系。</p></div>`:'';
    const end=a?.status==='ready'&&r?`<section class="tp-wb-deliver"><h2>保存与使用特征</h2><div class="tp-wb-delivery-steps"><span>① 核对候选</span><span>② 保存并计算新数据列</span><span>③ 导出报告或在 BI 查看</span></div><p>${a.artifact?'特征已保存并完成计算，原始数据保持不变。可在 BI 查看新增列，或下载分析报告。':a.feature_set_id?'已保存特征定义，系统正在计算特征列或等待模型评价。请在任务记录中查看进度。':'确认保留哪些候选后，点击“保存并生成特征”，生成可复用的新数据列。'}</p>
      ${review&&!a.feature_set_id?`<form id="tp-fl2-auto-save" class="tp-wb-save"><input name="name" value="${h.e(s.autoSetName||name+' 特征集')}" maxlength="120" required aria-label="特征集名称">${supervisedEditor}<label><input type="checkbox" name="confirm_materialize" value="yes" required> 我已核对候选，确认正式计算（不自动发布）</label><button class="button button-primary" type="submit" ${chosen.length?'':'disabled'}>保存并生成特征</button></form>`:''}
      ${feedback}${groupEvidence}${holdoutEvidence}<div class="tp-wb-outputs"><button class="button button-small" type="button" data-feature-v2="auto-report">下载本次分析报告</button><button class="button button-small" type="button" data-feature-v2="archive-report">保存可追溯报告</button>${a.artifact&&!stale?`<button class="button button-small" type="button" data-feature-v2="auto-open-bi">在 BI 查看特征</button><button class="button button-small" type="button" data-feature-v2="auto-export-evidence">导出核验记录</button>`:''}<a href="/projects/${h.u(s.pid)}/analysis?tool=reports&asset=${h.u(s.asset)}&version=${h.u(s.version||'base')}">查看特征资产与报告</a></div>
      ${a.artifact?`<details class="tp-wb-proof"><summary>产物溯源与模型评价</summary><small>物化 SHA256 ${h.e(proof?.artifact?.sha256||'待核验')}</small><p>${proof?.same_artifact_evaluation?'已有同源评价证据':'当前尚未完成可信的同源模型收益评价。'}</p></details>`:''}</section>`:'';
    const strategy=s.candidateSession?.strategy_plan||a?.strategy_plan||r?.strategy_plan;
    const familySummary=strategy?`<section class="tp-wb-strategy"><strong>本次自动特征策略</strong><p>已覆盖 ${Object.keys(strategy.family_counts||{}).length} 类算子，按特征类型轮换分配 ${strategy.budget} 项候选预算。${s.autoSession?.report?.search_summary?.rounds?.length||1} 轮可追溯质量筛选。候选质量合格不代表预测收益。</p>
      <div class="tp-wb-strategy-counts">${Object.entries(strategy.family_counts||{}).map(([k,n])=>`<span>${h.e(families[k]||k)} ${h.e(n)}</span>`).join('')}</div>
      ${(strategy.blocked||[]).length?`<details><summary>未使用的方法及原因</summary>${strategy.blocked.map(x=>`<p>${h.e(x.reason)}</p>`).join('')}</details>`:''}
    </section>`:'';
    const caps=s.advancedCapabilities?.engines||{};
    const advancedPanel=`<details class="tp-wb-advanced-tools"><summary><strong>手动操作与高级特征搜索</strong></summary><div><strong>扩展特征搜索</strong><p>可使用 tsfresh、Featuretools 生成更丰富的候选。仅在时间、实例键、数据可用性和资源预算满足要求后执行；结果进入同一候选审阅区。</p></div>
      <div class="tp-wb-advanced-buttons">${[['tsfresh','时序统计'],['featuretools','多表聚合']].map(([k,v])=>`<span class="tp-wb-advanced-engine">${v} · ${caps[k]?'可用':'需可选依赖'}</span>`).join('')}
        <button type="button" class="button button-small" data-feature-v2="wb-advanced-panel" aria-expanded="${!!s.uiAdvancedOpen}">${s.uiAdvancedOpen?'收起高级算法':'配置并计算高级特征'}</button>
        <button type="button" class="button button-small" data-feature-v2="wb-advanced-refresh">更新高级候选</button>
      </div>
      <p class="tp-wb-muted">${s.advancedBatches?.candidate_count||0} 项已计算的高级候选已纳入左侧列表；需经过 PIT 回接后才可参与模型比较，不直接勾选进普通逐行特征集。</p>
      ${s.uiAdvancedOpen?`<div class="tp-wb-advanced-drawer">${window.TimePilotFeatureAdvanced?.render?.(s)||'<p>高级算法组件未加载</p>'}</div>`:''}</details>`;
    const time=(s.timeBasis?.fields||[]).filter(x=>x.kind==='sequence_order_candidate'&&!x.confirmed);
    const extra=(needsInput||!a)&&time.length?`<div class="tp-wb-warning"><strong>可选：核实记录顺序</strong><p>数字顺序字段不会自动当作真实日期。确认后才能安全生成依赖顺序的特征。</p>${time.slice(0,2).map(x=>`<button type="button" class="button button-small" data-feature-v2="accept-time-order" data-field="${h.e(x.field_id)}">确认 ${h.e(x.name)} 为记录顺序</button>`).join('')}</div>`:'';
    return `<div class="tp-wb" data-testid="feature-workbench">${diagnostic}<section class="tp-wb-task"><div class="tp-wb-source"><b>${h.e(name)}</b><span>${h.e(s.version||'base')} · ${s.contract?.fields?.length||0} 列 · ${h.e({confirmed:'字段已确认',detected:'字段待核实',needs_review:'字段待确认',stale:'来源已失效'}[s.contract?.state]||'检查数据')}</span><button type="button" class="button button-small" data-feature-v2="edit-contract">核实字段</button></div>${status}${strategy?familySummary:''}${r?`<details class="tp-wb-restart"><summary>修改分析目标 / 重新分析</summary>${form}</details>`:form}</section>${extra}${main}${advancedPanel}${end}${!r&&!runningTask?'<section class="tp-wb-empty"><strong>分析完成后，特征与计算图表将在这里展示</strong><p>第一步检查数据，第二步自动生成候选并计算，第三步查看结果与保存。</p></section>':''}<details class="tp-wb-technical"><summary>运行记录与高级工具</summary><p>自动分析 ${h.e(a?.agent?.mode==='llm'?'模型受控增强':'规则模式')} · 结果随数据版本和 SHA256 绑定，不自动发布。</p>${a?.warnings?.map(x=>`<p>${h.e(x)}</p>`).join('')||''}<a href="/projects/${h.u(s.pid)}/analysis?tool=manual&asset=${h.u(s.asset)}&version=${h.u(s.version||'base')}">手工特征工具</a></details></div>`;
  }
  V.analysis=analysis;
})();
