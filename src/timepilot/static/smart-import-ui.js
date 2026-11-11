/* TimePilot Smart Import: upload -> inspect file tree -> select data -> versioned analysis.
   Dependency-free frontend; does not require Perspective or an LLM. */
(() => {
  'use strict';
  let ctx, current, activeVersion, latestImports = [], busy = false, agentPlan = null, agentPolling = false;
  const H = v => String(v ?? '').replace(/[&<>"']/g, s => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[s]));
  const U = v => encodeURIComponent(String(v));
  const fmt = v => Number(v ?? 0).toLocaleString('zh-CN');
  const r = (path, opts={}) => ctx.req('/smart-import'+path,opts);
  const jsonOpts = value => ({method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(value)});
  const busyLabel = (label='正在读取文件结构与工作表…') => `<div class="smart-loading" role="status"><span class="smart-spinner"></span>${H(label)}</div>`;
  const table = (cols, rows, max=12) => `<div class="smart-table-wrap"><table class="data-table"><thead><tr>${cols.map(x=>`<th>${H(x)}</th>`).join('')}</tr></thead><tbody>${rows.slice(0,max).map(row=>`<tr>${cols.map(key=>`<td>${H(row[key])}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
  const error = msg => `<div class="helper danger" role="alert">${H(msg)}</div>`;
  function subtitle() {
    return `<div class="page-heading"><div><h1>智能数据导入</h1><p>上传文件后自动发现工作表、时间列和统计口径；选择数据后生成可追溯版本。</p></div><a class="button" href="/bi/native">BI 数据分析</a></div>`;
  }
  function uploadPanel() {
    return `<div class="panel smart-upload-panel"><form id="smart-upload-form" class="form"><div class="smart-upload-row"><div><h2>上传并识别数据</h2><p>支持 ZIP（可包含多个 Excel），也支持直接多选 XLSX、CSV、Parquet。无需填写时间列、序列标识或目标列。</p><p class="muted small">默认最大 512 MiB，原始文件保留；不上传到外部模型服务。</p></div><div class="smart-upload-action"><input type="file" name="file" accept=".zip,.xlsx,.csv,.parquet" multiple required><button class="button button-primary" type="submit" ${busy?'disabled':''}>识别数据</button></div></div><div id="smart-upload-status"></div></form></div>`;
  }
  function summaryPage(imports) {
    return subtitle()+uploadPanel()+`<div class="panel"><h2>最近导入</h2>${imports.length?`<div class="smart-import-list">${imports.map(x=>`<a class="smart-file-link" href="/imports/${U(x.import_id)}"><strong>${H(x.filename)}</strong><span>${fmt(x.file_count)} 个文件 · ${fmt(x.candidate_count)} 个数据区域</span></a>`).join('')}</div>`:'<p class="muted">尚未导入业务文件。</p>'}</div>`;
  }
  const kindLabel = {wide_monthly:'年度横向月度表',long_monthly:'年月明细表',ordinary_timeseries:'时间序列表',ordinary_table:'普通数据表',metadata:'说明或配置'};
  const roleLabel={country:'国家/地区',brand:'品牌',vehicle:'车型',group_name:'集团',source_name:'来源名称',source_url:'来源网址',statistical_scope:'统计口径',series_id:'序列标识',timestamp:'时间',year:'年份',target:'数值指标',available_at:'真实发布时间'};
  function groupCard(g, selected) {
    const eligible=['wide_monthly','long_monthly','ordinary_timeseries'].includes(g.kind);
    return `<label class="smart-group ${selected?'is-selected':''}"><input type="checkbox" name="smart-region" data-group="${H(g.group_id)}" ${selected?'checked':''} ${eligible?'':'disabled'}><div class="smart-group-info"><strong>${H(g.sheet)}</strong><div class="smart-meta">${H(kindLabel[g.kind]||g.kind)} · ${fmt(g.files)} 个文件 · ${fmt(g.columns.length)} 列</div></div><span class="smart-choice">${selected?'已选择':eligible?'可选择':'仅预览'}</span><button type="button" class="button button-small" data-smart="preview-region" data-candidate="${H(g.candidate_ids[0])}">查看样例</button></label>`;
  }
  function agentPanel() {
    return `<div class="panel agent-panel" id="data-agent-panel"><div class="smart-head"><div><h2>数据理解与整理</h2><p>读取已选择的工作表，检查布局、字段含义和数据质量；确认后生成版本。<strong>不会自动训练模型。</strong></p></div><span class="agent-phase">受控工具 · 人工确认</span></div>
      <div class="field"><label for="agent-user-request">分析目标（可选）</label><textarea id="agent-user-request" rows="2" placeholder="例如：识别各品牌的月度销量，区分产量与销量，保留缺失值和数据来源"></textarea></div>
      <div class="agent-actions"><button type="button" class="button button-primary" data-smart="agent-propose">自动检查与生成方案</button><label class="agent-optin"><input id="agent-use-model" type="checkbox"> 使用已配置的大模型辅助解释字段</label></div>
      <p class="muted small">默认在本机确定性处理。主动开启模型后，仅发送字段名和表结构摘要；原始行数据、文件名和来源链接不传输给模型。</p><div id="agent-results" aria-live="polite"></div></div>`;
  }
  function renderAgentPlan(plan) {
    const slot=document.getElementById('agent-results');if(!slot)return;
    agentPlan=plan;
    const layout=plan.findings['layout.detect'];
    const sampleQuality=plan.findings['quality.evaluate'];
    const scopes=plan.scope_samples||[];
    const common=(layout.candidates||[]).reduce((set,c)=>set===null?new Set(c.columns):new Set([...set].filter(x=>c.columns.includes(x))),null);
    const opts=[...(common||[])].slice(0,70);
    const metric=opts.filter(x=>/销量|数量|产量|target|value|A\d+/i.test(x));
    const toolNames={'file.inspect':'文件检查','layout.detect':'布局识别','semantics.map':'语义映射','data.clean_preview':'清洗预览','quality.evaluate':'质量评估','series.preview':'序列预估','series.build':'构建版本'};
    slot.innerHTML=`<div class="agent-report"><div class="smart-head"><h3>结构识别结果</h3><span class="muted small">方案 ${H(plan.plan_id.slice(0,8))} · 未执行写入</span></div>
      <div class="agent-flow">${plan.steps.map(x=>`<span class="agent-step ${x.permission==='write_confirm'?'waiting':''}">${H(toolNames[x.tool]||x.tool)} · ${x.status==='completed'?'已检查':'待确认'}</span>`).join('')}</div>
      <div class="agent-kpis"><div><small>选定区域</small><strong>${fmt(plan.candidate_ids.length)}</strong></div><div><small>数据结构</small><strong>${H(kindLabel[layout.structure]||layout.structure)}</strong></div><div><small>抽查数值单元格</small><strong>${fmt(sampleQuality.counts.sampled_cells)}</strong></div><div><small>需人工确认</small><strong>${fmt(plan.issues.length)}</strong></div></div>
      <div class="agent-report-grid"><div><h4>语义映射</h4><div class="agent-role-list">${(plan.findings['semantics.map'].fields[0]?.roles?Object.entries(plan.findings['semantics.map'].fields[0].roles):[]).slice(0,12).map(([name,x])=>`<div><span>${H(roleLabel[name]||name)}</span><strong>${H(x.suggested)}</strong><small>${H(x.confidence==='high'?'高': '待核实')}</small></div>`).join('')}</div><small class="muted">按首个所选数据区域展示。跨年来源和统计口径保留单独标记。</small></div>
        <div><h4>分析建议</h4><p>${H(plan.advice.summary)}</p>${plan.advice.recommended_analysis.map(x=>`<p class="agent-suggestion">${H(x)}</p>`).join('')}${plan.advice.cautions.map(x=>`<p class="smart-warning">${H(x)}</p>`).join('')}</div></div>
      <h4>确认数据处理规则</h4><p class="muted small">原始文件与单元格坐标保持不变。缺失值不填补，跨来源冲突保留并标记。候选口径来自样本，可能未包含全部标签。</p>
      <div class="grid grid-2"><div class="field"><label for="agent-scope">统计口径</label><select id="agent-scope"><option value="">请选择口径</option><option value="__keep__">保留全部口径并分别标记（不合并）</option>${scopes.map(x=>`<option value="${H(x)}">${H(x)}</option>`).join('')}</select></div>
      <div class="field"><label for="agent-metric">预测目标数值列</label><select id="agent-metric"><option value="">自动识别（需要确认的情况请手动选）</option>${metric.map(x=>`<option value="${H(x)}">${H(x)}</option>`).join('')}</select></div>
      <div class="field"><label for="agent-series">序列标识列（可选）</label><select id="agent-series"><option value="">按来源和业务维度形成序列</option>${opts.map(x=>`<option value="${H(x)}">${H(x)}</option>`).join('')}</select></div>
      <div class="field"><label for="agent-availability">真实发布时间列（如有）</label><select id="agent-availability"><option value="">自动识别 / 原文件没有</option>${opts.map(x=>`<option value="${H(x)}">${H(x)}</option>`).join('')}</select></div><div class="field"><label for="agent-duplicate">重复数据</label><select id="agent-duplicate"><option value="keep_and_flag">全部保留并标记</option><option value="drop_identical">仅剔除完全相同观测（须额外确认）</option></select></div></div>
      <div class="agent-issues">${plan.issues.map(x=>`<label class="agent-issue"><input type="checkbox" name="agent-review" value="${H(x.code)}"><span><strong>${H(x.title)}</strong><small>${H(x.reason)}</small></span></label>`).join('') || '<p class="muted">当前未发现必须确认的字段歧义。仍请核对映射与数据口径。</p>'}</div>
      <label class="agent-consent"><input id="agent-confirm" type="checkbox"> 已核对上述数据定义，确认生成新版本（不会启动训练）</label>
      <div class="agent-actions"><button type="button" class="button button-primary" data-smart="agent-execute">确认并构建数据版本</button><a class="button button-small" href="/settings/models">模型设置</a></div>
      <div id="agent-execution-status" aria-live="polite"></div></div>`;
  }
  async function proposeAgent(button) {
    if(!current)return;
    const selected=new Set([...document.querySelectorAll('input[name="smart-region"]:checked')].map(x=>x.dataset.group));
    const ids=current.groups.filter(g=>selected.has(g.group_id)).flatMap(g=>g.candidate_ids);
    const slot=document.getElementById('agent-results');button.disabled=true;slot.innerHTML=busyLabel('正在检查工作表结构、字段含义与样本质量…');
    try{
      const payload={candidate_ids:ids,name:String(current.filename).replace(/\.[^.]+$/,'')+' · 智能整理',request:document.getElementById('agent-user-request')?.value||'',use_model:document.getElementById('agent-use-model')?.checked||false};
      const plan=await r(`/imports/${U(current.import_id)}/agent/plans`,jsonOpts(payload));renderAgentPlan(plan);
    }catch(e){slot.innerHTML=error(e.message)}finally{button.disabled=false}
  }
  async function executeAgent(button) {
    const plan=agentPlan;if(!current||!plan||agentPolling)return;
    const slot=document.getElementById('agent-execution-status');
    const reviewed=[...document.querySelectorAll('input[name="agent-review"]:checked')].map(x=>x.value);
    const scope=document.getElementById('agent-scope')?.value||'';
    const metric=document.getElementById('agent-metric')?.value||'';
    const series=document.getElementById('agent-series')?.value||'';
    const availability=document.getElementById('agent-availability')?.value||'';
    const duplicate=document.getElementById('agent-duplicate')?.value||'keep_and_flag';
    if(!document.getElementById('agent-confirm')?.checked){slot.innerHTML=error('请先确认数据定义和执行操作');return}
    if(duplicate==='drop_identical' && !window.confirm('将剔除完全相同的重合观测。原始文件不会被删除，是否确认？'))return;
    if(duplicate==='drop_identical')reviewed.push('duplicate_review');
    const decision={plan_sha256:plan.plan_sha256,confirm:true,acknowledged_issues:reviewed,
      scope_selection_mode:scope==='__keep__'?'keep_separate':'explicit',
      allowed_scopes:scope&&scope!=='__keep__'?[scope]:[],
      duplicate_policy:duplicate,field_mapping:series?{series_id:series}:{}};
    if(metric)decision.metric_column=metric;
    button.disabled=true;agentPolling=true;slot.innerHTML=busyLabel('已确认，正在创建不可变数据版本…');
    try{
      let job=await r(`/imports/${U(current.import_id)}/agent/plans/${U(plan.plan_id)}/execute`,jsonOpts(decision));
      while(['queued','running'].includes(job.status)){
        if(!document.getElementById('agent-execution-status'))break;
        slot.innerHTML=busyLabel(`数据处理中：${fmt(job.completed_candidates)} / ${fmt(job.total_candidates)} 个区域，${fmt(job.output_rows)} 条观测`);
        await new Promise(resolve=>setTimeout(resolve,950));
        job=await r(`/imports/${U(current.import_id)}/agent/plans/${U(plan.plan_id)}/execution`);
      }
      if(job.status!=='completed')throw new Error(job.error||`执行状态：${job.status}`);
      const versions=await r(`/imports/${U(current.import_id)}/versions`);
      const panel=document.getElementById('smart-versions-panel');if(panel)panel.innerHTML=versionsPanel(current.import_id,versions);
      slot.innerHTML=`<div class="helper success">已生成 ${fmt(job.output_rows)} 条观测，来源及方案哈希已记录。<a href="/imports/${U(current.import_id)}/versions/${U(job.version_id)}/workbench">进入 BI 分析和预测草稿</a></div>`;
      await displayVersion(current.import_id,job.version_id);
    }catch(e){slot.innerHTML=error(e.message)}finally{button.disabled=false;agentPolling=false}
  }
  function manifestPage(m, versions) {
    const picked = new Set(m.suggested_candidate_ids);
    const groups = m.groups.slice().sort((a,b)=>Number(b.suggested)-Number(a.suggested)||b.files-a.files);
    return subtitle()+`<div class="smart-breadcrumb"><a href="/imports">数据导入</a> / ${H(m.filename)}</div><div class="grid grid-3"><div class="panel stat-card"><div class="stat-label">文件</div><div class="stat-value">${fmt(m.files.length)}</div></div><div class="panel stat-card"><div class="stat-label">工作表与数据区域</div><div class="stat-value">${fmt(m.candidates.length)}</div></div><div class="panel stat-card"><div class="stat-label">已生成数据版本</div><div class="stat-value">${fmt(versions.length)}</div></div></div>
      ${agentPanel()}
      <div class="panel"><div class="smart-head"><div><h2>手动选择要整理的数据</h2><p>已自动选择可能是明细的数据区域。汇总表和来源记录默认不混入，请先核对。</p></div><button class="button button-small" type="button" data-smart="select-recommended">恢复推荐选择</button></div><form id="smart-transform-form"><div class="smart-group-grid">${groups.map(g=>groupCard(g,g.candidate_ids.some(x=>picked.has(x)))).join('')}</div><details class="smart-options"><summary>转换选项（可选）</summary><div class="grid grid-2"><div class="field"><label>新版本名称</label><input name="name" value="${H(m.filename.replace(/\.[^.]+$/,''))} · 月度明细"></div><div class="field"><label>数值指标列（非标准表可选择）</label><input name="metric_column" placeholder="自动判断"></div><div class="field"><label>限定统计口径（逗号分隔）</label><input name="scopes" placeholder="留空：分别保留全部口径"></div><div class="field"><label>相同观测重复处理</label><select name="duplicate_policy"><option value="keep_and_flag">全部保留并标记</option><option value="drop_identical">只移除完全相同值</option></select></div></div><small>未核实的发布时间保留为空；本步骤不会自动填补缺失月份，也不会合并不同来源的统计口径。</small></details><div class="smart-actions"><button type="submit" class="button button-primary" id="smart-transform-btn">生成分析数据版本</button><span class="muted small">数据大时会持续处理，完整结果可下载 CSV。</span></div></form><div id="smart-transform-status"></div><div id="smart-region-preview"></div></div>
      <div class="panel"><h2>原始文件目录</h2><div class="smart-file-index">${m.files.map(x=>`<details><summary>${H(x.filename)} <span class="muted small">${fmt(x.sheet_count)} 个工作表 · ${fmt(x.bytes)} 字节</span></summary>${x.warning?error(x.warning):m.candidates.filter(c=>c.file_id===x.file_id).map(c=>`<div class="smart-sheet-line">${H(c.sheet)} · ${H(kindLabel[c.kind]||c.kind)} · ${fmt(c.rows_estimate)} 行（估算）</div>`).join('')}</details>`).join('')}</div></div>
      <div id="smart-versions-panel">${versionsPanel(m.import_id,versions)}</div>`;
  }
  function versionsPanel(id,versions) {
    if (!versions.length) return '<div class="panel"><h2>2. 分析数据版本</h2><p class="muted">生成版本后可直接预览质量统计与 BI 图表。</p></div>';
    return `<div class="panel"><h2>2. 查看分析数据版本</h2><div class="smart-version-list">${versions.map(v=>`<button class="button button-small" type="button" data-smart="open-version" data-version="${H(v.version_id)}">${H(v.name)} · ${fmt(v.counts.output_rows)} 行</button>`).join('')}</div><div id="smart-version-preview"></div></div>`;
  }
  function chartSvg(rows) {
    if (!rows?.length) return '<p class="muted">此条件下没有可绘制的数据。</p>';
    const values=rows.slice(0,45), max=Math.max(1,...values.map(x=>Math.max(0,Number(x.value)||0))),w=840,h=230;
    const bw=(w-78)/values.length;
    return `<div class="smart-chart"><svg role="img" aria-label="数据汇总柱状图" viewBox="0 0 ${w} ${h}"><line x1="40" y1="185" x2="820" y2="185" stroke="#c5d2e4"/>${values.map((x,i)=>{const val=Math.max(0,Number(x.value)||0);const hh=149*val/max;const xx=46+i*bw;return `<rect x="${xx}" y="${185-hh}" width="${Math.max(2,bw*.65)}" height="${hh}" rx="2" fill="#2767c7"><title>${H(x.name)}: ${H(x.value)}</title></rect>${i%Math.max(1,Math.ceil(values.length/11))===0?`<text x="${xx}" y="207" font-size="10" fill="#576b86">${H(String(x.name).slice(-8))}</text>`:''}`}).join('')}</svg></div>`;
  }
  async function displayVersion(id,vid,scope='',source='') {
    const container=document.getElementById('smart-version-preview');if (!container)return;
    container.innerHTML=busyLabel('正在读取不可变版本…');
    try{
      const [meta,data,pivot]=await Promise.all([r(`/imports/${U(id)}/versions/${U(vid)}`),
          r(`/imports/${U(id)}/versions/${U(vid)}/preview?limit=35&scopes=${U(scope)}&sources=${U(source)}`),
          r(`/imports/${U(id)}/versions/${U(vid)}/pivot?group=timestamp&limit=60&scopes=${U(scope)}&sources=${U(source)}`)]);
      activeVersion=vid;
      const fields=['timestamp','target','statistical_scope','country','brand','vehicle','source_file','source_sheet','source_row','source_cell','quality_flags'];
      container.innerHTML=`<div class="smart-version-head"><h3>版本 ${H(vid)}</h3><div class="smart-actions"><a class="button button-small" download href="/api/v1/smart-import/imports/${U(id)}/versions/${U(vid)}/export.csv">导出完整 CSV</a><a class="button button-primary button-small" href="/imports/${U(id)}/versions/${U(vid)}/workbench">分析并用于预测</a><a class="button button-small" href="/data-assets/si_${H(id.replaceAll('-',''))}/versions/${H(vid)}">统一 BI</a><a class="button button-small" href="/data-assets/si_${H(id.replaceAll('-',''))}/versions/${H(vid)}/native">原生 BI（只读）</a></div></div>
      <div class="smart-kpis"><div><small>观测记录</small><strong>${fmt(meta.counts.output_rows)}</strong></div><div><small>序列</small><strong>${fmt(meta.series_count)}</strong></div><div><small>缺失值</small><strong>${fmt(meta.counts.missing_values)}</strong></div><div><small>数值冲突</small><strong>${fmt(meta.counts.conflicting_overlap)}</strong></div></div>
      ${meta.warnings.concat(pivot.warning?[pivot.warning]:[]).map(x=>`<p class="smart-warning">${H(x)}</p>`).join('')}<div class="smart-chart-bar"><h3>按月汇总</h3><div class="field"><label>仅展示统计口径</label><select id="smart-scope"><option value="">全部（可能混合口径）</option>${Object.keys(meta.statistical_scopes).map(k=>`<option value="${H(k)}" ${scope===k?'selected':''}>${H(k)} · ${fmt(meta.statistical_scopes[k])}</option>`).join('')}</select></div><div class="field"><label>数据来源</label><select id="smart-source"><option value="">全部（可能有重合记录）</option>${Object.keys(meta.source_counts).map(k=>`<option value="${H(k)}" ${source===k?'selected':''}>${H(k)} · ${fmt(meta.source_counts[k])}</option>`).join('')}</select></div></div>${chartSvg(pivot.rows)}
      <h3>样本明细与原始单元格追溯</h3>${table(fields,data.rows,35)}<small class="muted">表格显示最多 35 行；原始文件不可变，可通过 ZIP 与来源单元格核对。</small>`;
    }catch(e){container.innerHTML=error(e.message)}
  }
  async function render(context){
    ctx=context;current=null;activeVersion=null;agentPlan=null;
    const parts=location.pathname.split('/').filter(Boolean);
    ctx.root.innerHTML=busyLabel();
    try {
      if (parts.length<2) {
        latestImports=await r('/imports');ctx.root.innerHTML=summaryPage(latestImports);
      } else {
        current=await r('/imports/'+U(parts[1]));
        const versions=await r('/imports/'+U(parts[1])+'/versions');
        ctx.root.innerHTML=manifestPage(current,versions);
        if(versions.length)await displayVersion(parts[1],versions.at(-1).version_id);
      }
    }catch(e){ctx.root.innerHTML=subtitle()+error(e.message)}
  }
  async function submit(ev){
    if (ev.target.id==='smart-upload-form') {
      ev.preventDefault();const form=ev.target;const btn=form.querySelector('button[type="submit"]');const status=document.getElementById('smart-upload-status');
      btn.disabled=true;status.innerHTML=busyLabel('正在接收并检查全部工作表，文件较大时需要较长时间…');
      try{
        const files=[...form.elements.file.files]; if(!files.length)throw new Error('请选择文件');
        const batch=files.length>1;
        const body=new FormData();files.forEach(file=>body.append(batch?'files':'file',file));
        const created=await r(batch?'/uploads/batch':'/uploads',{method:'POST',body});
        ctx.navigate('/imports/'+created.import_id);
      }catch(e){status.innerHTML=error(e.message);btn.disabled=false}
      return;
    }
    if (ev.target.id==='smart-transform-form') {
      ev.preventDefault();const f=ev.target;const groups=current.groups;const selected=new Set([...f.querySelectorAll('input[name="smart-region"]:checked')].map(x=>x.dataset.group));
      const ids=groups.filter(g=>selected.has(g.group_id)).flatMap(g=>g.candidate_ids);
      const status=document.getElementById('smart-transform-status');const btn=document.getElementById('smart-transform-btn');
      if(!ids.length){status.innerHTML=error('请选择一个或多个时间序列数据区域');return}
      const data=new FormData(f),opts={candidate_ids:ids,name:String(data.get('name')||'解析数据'),
        duplicate_policy:String(data.get('duplicate_policy')||'keep_and_flag'),
        allowed_scopes:String(data.get('scopes')||'').split(',').map(x=>x.trim()).filter(Boolean)};
      const metric=String(data.get('metric_column')||'').trim();if(metric)opts.metric_column=metric;
      btn.disabled=true;status.innerHTML=busyLabel('正在按工作表转换数据，保留来源和质量标记…');
      try{
        const importId=current.import_id;
        let job=await r('/imports/'+U(importId)+'/transform-jobs',jsonOpts(opts));
        while(['queued','running'].includes(job.status)){
          if(document.getElementById('smart-transform-status')){
            status.innerHTML=busyLabel(`正在转换 ${fmt(job.completed_candidates)} / ${fmt(job.total_candidates)} 个区域 · 已处理 ${fmt(job.output_rows)} 条观测…`);
          }
          await new Promise(resolve=>setTimeout(resolve,850));
          job=await r(`/imports/${U(importId)}/transform-jobs/${U(job.job_id)}`);
        }
        if(job.status!=='completed')throw new Error(job.error||`转换任务 ${job.status}`);
        const [version,versions]=await Promise.all([
          r(`/imports/${U(importId)}/versions/${U(job.version_id)}`),
          r(`/imports/${U(importId)}/versions`)]);
        const vpanel=document.getElementById('smart-versions-panel');
        if(vpanel)vpanel.innerHTML=versionsPanel(importId,versions);
        if(status)status.innerHTML=`<div class="helper success">已生成数据版本：${H(version.version_id)}，共 ${fmt(version.counts.output_rows)} 行。</div>`;
        await displayVersion(importId,version.version_id);
      }catch(e){if(status)status.innerHTML=error(e.message)}finally{btn.disabled=false}
    }
  }
  async function click(ev){
    const button=ev.target.closest('[data-smart]');if(!button)return;
    ev.preventDefault();
    const op=button.dataset.smart;
    if(op==='agent-propose'){await proposeAgent(button);return}
    if(op==='agent-execute'){await executeAgent(button);return}
    if(op==='select-recommended'){
      const selected=new Set(current.suggested_candidate_ids);
      for(const c of document.querySelectorAll('input[name="smart-region"]')){
        const g=current.groups.find(g=>g.group_id===c.dataset.group);
        c.checked=g.candidate_ids.some(x=>selected.has(x));c.closest('.smart-group')?.classList.toggle('is-selected',c.checked);
      }
    }
    if(op==='preview-region'){
      const slot=document.getElementById('smart-region-preview');slot.innerHTML=busyLabel('正在读取数据区域…');
      try {const data=await r(`/imports/${U(current.import_id)}/regions/${U(button.dataset.candidate)}/preview`);
        const columns=data.candidate.columns.slice(0,25);
        slot.innerHTML=`<h3>${H(data.candidate.logical_file)} · ${H(data.candidate.sheet)}</h3>${table(columns,data.rows.map(x=>x.values),12)}`;
        slot.scrollIntoView({behavior:'smooth',block:'nearest'});
      }catch(e){slot.innerHTML=error(e.message)}
    }
    if(op==='open-version')await displayVersion(current.import_id,button.dataset.version);
    if(op==='publish-bi'){
      button.disabled=true;
      try {const result=await r(`/imports/${U(current.import_id)}/versions/${U(button.dataset.version)}/publish-bi`,{method:'POST'});
        ctx.navigate('/bi/native/'+result.asset_id);
      }catch(e){ctx.toast('原生 BI 未就绪，基础 BI 和 CSV 导出仍可使用：'+e.message)}finally{button.disabled=false}
    }
  }
  function changed(ev){
    if(['smart-scope','smart-source'].includes(ev.target.id)&&activeVersion&&current){
      const scope=document.getElementById('smart-scope')?.value||'';
      const source=document.getElementById('smart-source')?.value||'';
      void displayVersion(current.import_id,activeVersion,scope,source);
      return true;
    }
    if(ev.target.matches('input[name="smart-region"]')){
      ev.target.closest('.smart-group')?.classList.toggle('is-selected',ev.target.checked);
      return true;
    }
    return false;
  }
  window.TimePilotSmartImport={render,submit,click,changed};
})();
