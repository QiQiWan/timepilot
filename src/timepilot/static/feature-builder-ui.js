/* v0.28.0 FeatureLab: quick candidate workflow + v0.26.2 typed manual editor. */
window.TimePilotFeatureViews=window.TimePilotFeatureViews||{};
window.TimePilotFeatureViews.build=function(s,h){
  const fields=s.contract?.fields||[];
  const fieldName=id=>fields.find(x=>x.field_id===id)?.source_name||id;
  const operators=s.operators?.operators||[];
  const enabled=operators.filter(x=>x.available && x.operator_id!=='expression.safe');
  const mode=s.workspaceMode||'quick';
  const chartMode=s.chartMode||s.session?.layout?.chart_mode||'trend';
  const d=s.manualDraft||{};
  const current=enabled.find(x=>x.operator_id===d.operator_id)||enabled[0];
  const controls=current?.controls||[];
  const choices=fields.filter(f=>(current?.eligible_field_ids||[]).includes(f.field_id));
  const eventMode=current?.input_kind==='boolean';
  const optionList=(name,selected)=>`<select name="${name}" required>${choices.map(f=>`<option value="${h.e(f.field_id)}" ${f.field_id===selected?'selected':''}>${h.e(f.source_name)}${f.display_name?' · '+h.e(f.display_name):''}</option>`).join('')}</select>`;
  const genericFieldOptions=(current?.eligible_field_ids||[]);
  const opOptions=operators.filter(o=>o.operator_id!=='expression.safe').map(o=>`<option value="${h.e(o.operator_id)}" ${o.operator_id===current?.operator_id?'selected':''} ${o.available?'':'disabled'}>${h.e(o.title)}${o.available?'':'（不可用）'}</option>`).join('');
  const fieldEditor=current?`<form id="tp-fl2-add-step" class="tp-fl2-manual-form">
    <label class="field"><span>计算方法</span><select name="operator_id" data-feature-v2-change="manual-operator">${opOptions}</select></label>
    ${controls.includes('first')?`<label class="field"><span>输入字段</span>${optionList('first',d.first)}</label>`:''}
    ${controls.includes('event')?`<label class="field"><span>已核实的维护事件标记（BOOLEAN）</span>${optionList('event',d.event)}</label>`:''}
    ${controls.includes('second')?`<label class="field"><span>第二个数值字段</span>${optionList('second',d.second)}</label>`:''}
    ${controls.includes('window')?`<label class="field"><span>${current.operator_id.startsWith('lag.')||current.operator_id.startsWith('difference.')||current.operator_id==='relative.change'?'历史滞后条数':'窗口记录数'}</span><input type="number" name="window" value="${h.e(d.window||5)}" min="1" max="240" required></label>`:''}
    ${controls.includes('window_seconds')?`<label class="field"><span>真实时间窗口（秒）</span><input name="window_seconds" type="number" min="1" max="604800" value="${h.e(d.window_seconds||1800)}" required></label>`:''}
    ${controls.includes('min_periods')?`<label class="field"><span>最少有效观测数</span><input name="min_periods" type="number" min="1" max="240" value="${h.e(d.min_periods||1)}" required></label>`:''}
    ${controls.includes('sign')?`<label class="field"><span>运算符</span><select name="sign">${[['+','加法'],['-','减法'],['*','乘法'],['/','安全除法（除零置空）']].map(([v,label])=>`<option value="${v}" ${d.sign===v?'selected':''}>${label}</option>`).join('')}</select></label>`:''}
    <label class="field"><span>中文显示名称（可选）</span><input name="display_name" maxlength="160" placeholder="默认使用字段与算子说明" value="${h.e(d.display_name||'')}"></label>
    <div class="tp-fl2-inline-info">技术名将自动生成，原始数据不会修改。</div>
    <button class="button button-primary" type="submit">加入特征</button>
  </form>`:'<p class="tp-fl2-empty">当前没有适用算子。请先核实字段语义。</p>';
  const draftItems=s.steps.map((x,i)=>`<div class="tp-fl2-step"><span><strong>${h.e(x.display_name||x.output_name)}</strong><small>${h.e(x.output_name)} · ${h.e(x.operator_id)} · ${h.e(x.input_field_ids.map(fieldName).join('、'))}</small></span><button class="button button-small" data-feature-v2="remove-step" data-index="${i}">移除</button></div>`).join('');
  const session=s.session;
  const candidates=session?.candidates||[];
  const selected=session?.selected_candidate_ids||[];
  const focused=candidates.find(c=>c.candidate_id===s.focusCandidateId)||candidates.find(c=>selected.includes(c.candidate_id))||candidates[0];
  const typeMap={numeric:'数值',datetime:'时间',boolean:'布尔',unknown:'待确认',id:'标识符',category:'类别',string:'文本'};
  const suggestions=session?.field_suggestions||s.fieldSuggestions?.fields||[];
  const sourceFields=fields.map(f=>{
    const info=suggestions.find(x=>x.field_id===f.field_id);
    const eligible=info?.eligible_numeric ?? genericFieldOptions.includes(f.field_id);
    const selectedField=!s.quickSelectedFields?.length||s.quickSelectedFields.includes(f.field_id);
    return `<label class="tp-fl2-field-check"><input type="checkbox" name="candidate_field" value="${h.e(f.field_id)}" ${selectedField?'checked':''} ${eligible?'':'disabled'}><span><strong>${h.e(f.display_name||f.source_name)}</strong><small>${h.e(typeMap[info?.proposed_semantic_type]||f.physical_type)} · ${h.e(f.source_name)}</small>${!eligible?`<em>${h.e(info?.warnings?.[0]||'该字段不参与数值特征生成')}</em>`:''}</span></label>`;
  }).join('');
  const candidateRows=candidates.map(c=>`<div class="tp-fl2-candidate ${focused?.candidate_id===c.candidate_id?'is-focused':''}">
    <label><input type="checkbox" data-feature-v2-change="candidate-selection" value="${h.e(c.candidate_id)}" ${selected.includes(c.candidate_id)?'checked':''}><span class="tp-fl2-candidate-name">${h.e(c.display_name)}</span></label>
    <button class="tp-fl2-preview-link" type="button" data-feature-v2="focus-candidate" data-cid="${h.e(c.candidate_id)}">查看</button>
    <small>${h.e(c.reason)} · ${h.e(c.operator_id)}</small></div>`).join('');
  const preview=s.candidatePreview;
  const selectedQuality=preview?.quality||[];
  const chartCol=focused&&preview?.columns?.includes(focused.output_name)?focused.output_name:null;
  const chartBase=focused?.input_field_ids?.[0]?fieldName(focused.input_field_ids[0]):null;
  const drawSeries=(column)=>{
    if(!column||!preview?.rows?.length)return '';
    const values=preview.rows.map(r=>r[column]===null||r[column]===undefined?NaN:Number(r[column]));
    const valid=values.filter(Number.isFinite);
    if(valid.length<2)return '';
    const low=Math.min(...valid),high=Math.max(...valid),span=high-low||1;
    const pieces=[];let previous=[];
    values.forEach((value,i)=>{
      if(!Number.isFinite(value)){
        if(previous.length>1)pieces.push(previous.join(' '));
        previous=[];return;
      }
      previous.push(`${(18+i*540/Math.max(values.length-1,1)).toFixed(1)},${(118-90*(value-low)/span).toFixed(1)}`);
    });
    if(previous.length>1)pieces.push(previous.join(' '));
    return pieces;
  };
  const samplePairs=()=>{
    if(!chartCol||!chartBase||!preview?.rows)return [];
    return preview.rows.map(r=>[Number(r[chartBase]),Number(r[chartCol])]).filter((_,i)=>{
      const r=preview.rows[i];return r[chartBase]!==null&&r[chartCol]!==null;
    }).filter(pair=>pair.every(Number.isFinite));
  };
  const chartHeader=`<div class="tp-fl2-chart-tabs" role="group" aria-label="预览图表类型">
    ${[['trend','变化趋势'],['distribution','样本分布'],['scatter','原始—特征散点']].map(([key,title])=>`<button type="button" class="${chartMode===key?'active':''}" data-feature-v2="chart-${key}">${title}</button>`).join('')}
    </div>`;
  const histogram=()=>{
    const values=(preview?.rows||[]).map(r=>r[chartCol]).filter(v=>v!==null&&v!==undefined).map(Number).filter(Number.isFinite);
    if(!values.length)return '<p class="muted small">当前样本没有有限数值，无法计算直方图。</p>';
    const low=Math.min(...values), high=Math.max(...values), n=10, width=(high-low)||1;
    const bins=Array(n).fill(0);
    values.forEach(v=>bins[Math.max(0,Math.min(n-1,Math.floor((v-low)/width*n)))]++);
    const ymax=Math.max(...bins)||1;
    return `<svg role="img" aria-label="预览样本的10组频数直方图" viewBox="0 0 580 155" preserveAspectRatio="xMidYMid meet">
      <path d="M24 12 V129 H560" fill="none" stroke="#b8c9df"/>
      ${bins.map((v,i)=>`<rect x="${29+i*52}" y="${128-101*v/ymax}" width="43" height="${101*v/ymax}" rx="2" fill="#497fd7"/>`).join('')}
      <text x="25" y="146" font-size="11" fill="currentColor">${Number(low).toPrecision(4)}</text>
      <text x="495" y="146" font-size="11" fill="currentColor">${Number(high).toPrecision(4)}</text>
    </svg>`;
  };
  const scatter=()=>{
    const points=samplePairs();
    if(!points.length)return '<p class="muted small">需同时存在数值原字段和派生字段，才能画散点图。</p>';
    const xs=points.map(x=>x[0]),ys=points.map(x=>x[1]);
    const xmin=Math.min(...xs),ymin=Math.min(...ys),dx=Math.max(...xs)-xmin||1,dy=Math.max(...ys)-ymin||1;
    return `<svg role="img" aria-label="原始字段与派生特征的样本散点图" viewBox="0 0 580 150" preserveAspectRatio="xMidYMid meet">
      <path d="M30 8 V126 H558" fill="none" stroke="#b8c9df" />
      ${points.map(([x,y])=>`<circle cx="${30+510*(x-xmin)/dx}" cy="${126-110*(y-ymin)/dy}" r="3.3" fill="#3687ac" opacity=".75"/>`).join('')}
      <text x="260" y="145" fill="currentColor" font-size="11">${h.e(chartBase||'原始字段')}</text>
    </svg>`;
  };
  const trend=()=>`<svg aria-label="派生特征与原始字段样本趋势" viewBox="0 0 580 138" preserveAspectRatio="xMidYMid meet" role="img">
    <path d="M18 18 V118 H558" fill="none" stroke="#d8e3ef" stroke-width="1.2"/>
    ${drawSeries(chartCol).map(line=>`<polyline points="${line}" fill="none" stroke="#366fd7" stroke-width="2.7"/>`).join('')}
    ${chartBase&&preview.columns.includes(chartBase)?drawSeries(chartBase).map(line=>`<polyline points="${line}" fill="none" stroke="#17a18d" stroke-width="2" stroke-dasharray="5 4"/>`).join(''):''}</svg>`;
  const chartVisual=preview&&chartCol?`<div class="tp-fl2-spark-shell"><strong>特征可视化</strong>${chartHeader}
    ${chartMode==='distribution'?histogram():chartMode==='scatter'?scatter():trend()}
    <small>${chartMode==='trend'?'不同数值列独立缩放，只比较变化形态。':chartMode==='distribution'?'基于预览返回样本的频数分布，不能代表源表完整分布。':'基于预览样本的散点关系，不能证明因果或预测增益。'}</small></div>`:'';
  const qualityItems=preview?.quality||[];
  const formatNumber=value=>value===null||value===undefined?'—':Number(value).toLocaleString('zh-CN',{maximumFractionDigits:4});
  const previewTable=preview?`<div class="tp-fl2-preview-evidence">${chartVisual}
    <p class="muted small">源表共 ${h.e(qualityItems[0]?.rows_scanned??'—')} 行；质量指标在全量计算视图上统计，下面趋势、直方图、散点图及表格只展示前 ${h.e(preview.rows_returned)} 行。尚未计算预测增益。</p>
    <div class="tp-fl2-quality">${qualityItems.map(q=>`<div><strong>${h.e(q.feature_name)}</strong>
    <small>全量缺失/无穷：${q.null_rate===null?'—':(100*q.null_rate).toFixed(2)+'%'} ${q.constant_on_full_data?' · 全量无有效变化':''}</small>
    <small>均值 ${h.e(formatNumber(q.mean))} · 标准差 ${h.e(formatNumber(q.std))}</small>
    <small>P10 ${h.e(formatNumber(q.p10))} · P90 ${h.e(formatNumber(q.p90))}</small></div>`).join('')}</div>
    <div class="table-wrap tp-fl2-candidate-table"><table class="data-table"><thead><tr>${preview.columns.map(name=>`<th>${h.e(name)}</th>`).join('')}</tr></thead><tbody>${preview.rows.map(row=>`<tr>${preview.columns.map(name=>`<td>${h.e(row[name])}</td>`).join('')}</tr>`).join('')}</tbody></table></div></div>`:'';
  const quick=`<div class="tp-fl2-auto-layout">
    <aside class="tp-fl2-auto-panel"><h3>分析配置</h3><p class="muted small">选择模板和字段，生成可编辑的候选列表。</p>
      <form id="tp-fl2-generate"><label class="field"><span>特征模板</span><select name="template">
        ${[['basic','基础统计与变化'],['temporal','时序与窗口'],['industrial','设备运行状态'],['quality','质量与工艺分析'],['advanced','高级时序统计']].map(([key,label])=>`<option value="${key}" ${key===(s.quickTemplate||session?.template||'basic')?'selected':''}>${label}</option>`).join('')}</select></label>
        <label class="field"><span>候选数量上限</span><input type="number" name="budget" min="1" max="60" value="${h.e(s.quickBudget||session?.budget||30)}"></label>
        <div class="tp-fl2-field-list-heading"><strong>可用字段</strong><button type="button" class="tp-fl2-preview-link" data-feature-v2="edit-contract">核实字段语义</button></div>
        <div class="tp-fl2-field-list">${sourceFields||'<p>尚未识别字段</p>'}</div>
        <button class="button button-primary" type="submit">${session?'重新生成候选':'自动生成候选'}</button>
        <p class="muted small">不勾选字段时按全部适用字段生成。真实时间窗口须先确认时间类型和时区。</p>
      </form>
    </aside>
    <section class="tp-fl2-auto-panel tp-fl2-center"><div class="tp-fl2-panel-head"><div><h3>特征预览</h3><p class="muted small">${focused?h.e(focused.display_name+' · '+focused.output_name):'先生成候选特征'}</p></div><button class="button button-small" data-feature-v2="preview-candidates" ${selected.length?'':'disabled'}>${selected.length>8?'预览前 8 项':'预览所选'}</button></div>
      ${focused?`<div class="tp-fl2-focus"><span class="badge running">候选计算</span><strong>${h.e(focused.display_name)}</strong><p>${h.e(focused.reason)}</p><small>来源字段：${h.e(focused.input_field_ids.map(fieldName).join('、'))} · ${h.e(focused.operator_id)}</small></div>`:''}
      ${previewTable||'<div class="tp-fl2-preview-placeholder"><strong>等待预览</strong><p>生成候选后勾选最多 8 项，点击“预览所选”查看实际计算值和样本质量。正式物化仍需保存特征集后单独运行。</p></div>'}
      <div class="tp-fl2-quick-summary">源数据 ${h.e(s.contract?.source_sha256?.slice(0,12)||'—')} · 契约修订 ${h.e(s.contract?.revision??'—')} ${session?.stale?'<strong class="tp-fl2-stale"> · 数据或字段定义已变化，须重新生成</strong>':''}</div>
    </section>
    <aside class="tp-fl2-auto-panel tp-fl2-candidate-panel"><div class="tp-fl2-panel-head"><div><h3>候选特征 <span class="badge">${candidates.length}</span></h3><p class="muted small">已选 ${selected.length} 项 · 勾选后自动保存</p></div></div>
      <div class="tp-fl2-candidate-tools"><button class="button button-small" data-feature-v2="candidate-all" ${candidates.length?'':'disabled'}>全选</button><button class="button button-small" data-feature-v2="candidate-none" ${candidates.length?'':'disabled'}>清空</button></div>
      <div class="tp-fl2-candidate-list">${candidateRows||'<p class="muted small">当前没有适用候选。可调整模板、核实字段或手工构造。</p>'}</div>
      <form id="tp-fl2-save-candidates"><label class="field"><span>特征集名称</span><input name="name" maxlength="120" required value="${h.e(s.candidateSetName||'自动生成基础特征')}" placeholder="例如 设备运行状态特征"></label>
        <button class="button button-primary" type="submit" ${(!selected.length||session?.stale)?'disabled':''}>保存所选特征集</button></form>
      <p class="muted small">候选处于探索模式；有标签的预测任务请先完成任务绑定，再使用正式特征集与实验评价。</p>
    </aside>
  </div>`;
  const confirmedTime=Boolean(s.contract?.event_time_field_id && s.contract?.available_at_field_id &&
      s.contract?.entity_field_ids?.length && s.contract?.timezone && s.contract?.state==='confirmed');
  const dateFields=fields.filter(f=>f.semantic_type==='datetime'&&f.confirmation==='confirmed');
  const selectTime=()=>`<option value="">请选择</option>${dateFields.map(f=>`<option value="${h.e(f.field_id)}">${h.e(f.source_name)}</option>`).join('')}`;
  const manual=`<div class="tp-fl2-manual-layout"><div class="tp-fl2-auto-panel"><h3>手工构造特征</h3><p class="muted small">算子决定输入类型及所需参数；只展示当前需要填写的内容。</p>${fieldEditor}</div>
    <div class="tp-fl2-auto-panel"><h3>特征集草稿 <span class="badge">${s.steps.length}</span></h3>${draftItems||'<p class="tp-fl2-empty">添加一个字段计算后，可以保存特征集草稿。</p>'}
      <form id="tp-fl2-save-set" class="tp-fl2-manual-save"><label class="field"><span>特征集名称</span><input name="name" required maxlength="120" placeholder="例如 设备运行状态特征"></label>
      <label class="field"><span>特征用途</span><select name="task_mode"><option value="exploration">探索性分析</option><option value="supervised">监督预测（需绑定标签和时间）</option></select></label>
      <details class="tp-fl2-task-binding"><summary>设置监督预测的标签与评价协议</summary><p class="muted small">${confirmedTime?'已具备时间与实体契约':'请先在数据画像中确认事件时间、可用时间、实体键和时区。'}</p>
        <label class="field"><span>目标类型</span><select name="task_type"><option value="binary_classification">二分类</option><option value="regression">回归</option></select></label>
        <label class="field"><span>预测时刻</span><select name="prediction_time">${selectTime()}</select></label>
        <label class="field"><span>标签字段</span><select name="label"><option value="">请选择</option>${fields.filter(f=>f.role!=='entity_key'&&f.semantic_type!=='datetime').map(f=>`<option value="${h.e(f.field_id)}">${h.e(f.source_name)}</option>`).join('')}</select></label>
        <label class="field"><span>标签可用时刻</span><select name="label_available">${selectTime()}</select></label>
        <label class="field"><span>评价切分</span><select name="split_kind"><option value="time">时间滚动切分</option><option value="grouped_time">设备/批次隔离时间切分</option></select></label>
      </details><button class="button button-primary" type="submit" ${s.steps.length?'':'disabled'}>保存特征集草稿</button></form>
    </div></div>`;
  const formulaFields=fields.filter(f=>suggestions.find(x=>x.field_id===f.field_id)?.eligible_numeric??
    (s.operators?.operators||[]).find(x=>x.operator_id==='expression.safe')?.eligible_field_ids?.includes(f.field_id));
  const expressionSelect=(idx)=>`<label class="field"><span>x${idx} 绑定字段 ${idx===0?'（必选）':'（可选）'}</span><select name="formula_field_${idx}" ${idx===0?'required':''}>
    <option value="">${idx===0?'请选择数值字段':'不使用'}</option>
    ${formulaFields.map(f=>`<option value="${h.e(f.field_id)}">${h.e(f.display_name||f.source_name)} · ${h.e(f.source_name)}</option>`).join('')}</select></label>`;
  const formulaEditor=`<h3>自定义特征公式</h3><p class="muted small">先按顺序绑定输入字段，再使用 x0、x1…编写公式。可用 + − × ÷、abs、sqrt、log1p、round、clip、safe_div、coalesce、min、max；不执行 Python/SQL。</p>
    <form id="tp-fl2-add-formula" class="tp-fl2-formula-form">
    <div class="tp-fl2-formula-fields">${[0,1,2,3].map(expressionSelect).join('')}</div>
    <details><summary>更多输入字段（x4—x7，可选）</summary><div class="tp-fl2-formula-fields">${[4,5,6,7].map(expressionSelect).join('')}</div></details>
    <label class="field"><span>计算公式</span><textarea name="expression" required maxlength="240" rows="3" placeholder="例如 safe_div(x0 - x1, x1)" spellcheck="false"></textarea></label>
    <label class="field"><span>中文名称</span><input name="display_name" maxlength="160" placeholder="例如：两处压力的相对偏差"></label>
    <button class="button button-primary" type="submit">验证并加入草稿</button>
    <small>变量需连续绑定；不能引用时间、编号、标签或未经核实的字段。检查通过后仍需预览与正式计算。</small></form>`;
  const manualIntro=`<h3>手工构造特征</h3><p class="muted small">算子决定输入类型及所需参数；只展示当前需要填写的内容。</p>${fieldEditor}`;
  const custom=manual.replace(manualIntro,formulaEditor);
  const job=s.job;
  return `<section class="tp-fl2-work tp-fl2-workbench"><header><div><h2>特征工作台</h2><p>识别字段、生成候选、检查实际计算值，再选择进入模型或 BI 的特征。</p></div><a class="button" href="/projects/${h.u(s.pid)}/bi/native/${h.u(s.asset)}">在 BI 中查看源数据</a></header>
    <nav class="tp-fl2-mode-nav" aria-label="特征构造方式"><button type="button" class="${mode==='quick'?'active':''}" data-feature-v2="mode-quick">快速生成</button><button type="button" class="${mode==='manual'?'active':''}" data-feature-v2="mode-manual">手工构造</button><button type="button" class="${mode==='custom'?'active':''}" data-feature-v2="mode-custom">自定义公式</button></nav>
    ${mode==='quick'?quick:(mode==='custom'?custom:manual)}
    ${job?`<div class="tp-fl2-job" role="status"><strong>${job.kind==='candidate_preview'?'候选预览任务':'计算任务'}：</strong>${h.e(job.status)}${job.error_text?` · ${h.e(job.error_text)}`:''} <button class="button button-small" data-feature-v2="refresh-job">刷新状态</button></div>`:''}
    <section class="tp-fl2-viewer-section ${s.viewerOpen?'is-open':'is-closed'}" aria-label="Perspective 特征对照工作区"><div class="tp-fl2-viewer-toolbar"><strong>Perspective 数据工作区</strong><div class="action-row"><button class="button button-small" data-feature-v2="toggle-viewer">${s.viewerOpen?'折叠 BI 区域':'展开 BI 区域'}</button><button class="button button-small" data-feature-v2="open-viewer">加载源数据</button>${s.runResult?.artifact?'<button class="button button-small" data-feature-v2="compare-viewer">查看完整特征结果</button>':''}<button class="button button-small" data-feature-v2="save-viewer" ${s.activeSet?'':'disabled'}>保存布局</button></div></div><p class="muted small" id="tp-fl2-viewer-status" role="status">源数据及正式物化后的特征结果可在此独立探索。候选预览是有范围标记的样本，不会代替正式结果。</p><div id="tp-fl2-perspective" class="tp-fl2-perspective"><p class="muted">选择加载后可使用 Perspective 原生图表与透视</p></div></section>
    ${s.sets?.length?`<div class="tp-fl2-hints"><strong>已保存的特征集</strong><div class="action-row">${s.sets.slice(0,12).map(r=>`<button class="button button-small" data-feature-v2="open-set" data-sid="${h.e(r.set_id)}">${h.e(r.name)} · v${h.e(r.revision)}</button>`).join('')}</div></div>`:''}
    ${s.activeSet?`<div class="tp-fl2-hints"><strong>当前特征集：${h.e(s.activeSet.name)}</strong> · 修订 ${h.e(s.activeSet.revision)} <button class="button" data-feature-v2="preview-features" data-sid="${h.e(s.activeSet.set_id)}">预览 50 条</button><button class="button button-primary" data-feature-v2="run-features" data-sid="${h.e(s.activeSet.set_id)}">完整计算</button></div>`:''}
    ${s.previewResult?`<div class="tp-fl2-hints"><strong>已保存配方的样本预览：${h.e(s.previewResult.rows_returned)} 条</strong><div class="table-wrap"><table class="data-table"><thead><tr>${(s.previewResult.columns||[]).map(n=>`<th>${h.e(n)}</th>`).join('')}</tr></thead><tbody>${(s.previewResult.rows||[]).map(r=>`<tr>${s.previewResult.columns.map(n=>`<td>${h.e(r[n])}</td>`).join('')}</tr>`).join('')}</tbody></table></div></div>`:''}
    ${s.runResult?`<div class="tp-fl2-hints tp-fl2-result-ready"><strong>全量物化已完成 · ${h.e(s.runResult.artifact.row_count)} 行</strong><span>SHA256 ${h.e(s.runResult.artifact.sha256.slice(0,16))}</span><button class="button button-primary" type="button" data-feature-v2="compare-viewer">在 Perspective 查看原始值与特征结果</button></div>`:''}
  </section>`;
};
