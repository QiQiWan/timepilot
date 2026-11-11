/* TimePilot v0.8.0 Native BI: Perspective, DuckDB query, sparse versioned edits.
   UI routes and API are stable; no external assets are loaded from a CDN. */
(() => {
  'use strict';
  const H = x => String(x ?? '').replace(/[&<>"']/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const U = encodeURIComponent;
  const post = x => ({method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(x)});
  const nice = n => Number(n||0).toLocaleString('zh-CN');
  let ctx, current=null, info=null, assets=[], legacy=[], streams=[];
  let selectedVersion='base', latestVersion='base', lastResult=null, lastQuery=null;
  let mod=null, visualSource=null, editor=[], preview=null, previewBatch=null;
  let chartRequest=0;
  // Explicit browser API contract: an application-mode Vite build can emit
  // valid JS/CSS/WASM while stripping exported entry functions.
  const PERSPECTIVE_API=[
    'mountPerspective','mountPerspectiveRemote','getActiveTableName',
    'setPerspectivePreset','savePerspectiveState','restorePerspectiveState',
    'disposePerspective'
  ];
  const description='原生网格、透视、交互图表与版本化数据编辑。原始 Parquet 永久保留。';
  const alert = text => `<div class="helper warning">${H(text)}</div>`;
  const heading=(name,desc,actions='')=>`<div class="page-heading"><div><div class="eyebrow">TIMEPILOT / NATIVE BI</div><h1>${H(name)}</h1><p>${H(desc)}</p></div><div class="bi-actions">${actions}</div></div>`;
  const opts=(cols,empty=true)=>`${empty?'<option value="">不选择</option>':''}${cols.map(c=>`<option value="${H(c.name)}">${H(c.name)} · ${H(c.physical_type||'文本')}</option>`).join('')}`;
  const footnote = '完整数据模式通过 Perspective Virtual Server 将字段拖拽、透视、排序和图表计算交给 DuckDB；有限样本仅供快速预览。';
  const checkLatest=()=>selectedVersion===latestVersion;
  function assetItems(){return assets.length?assets.map(a=>`<a class="bi-dataset-item" href="/bi/native/${U(a.asset_id)}"><span class="bi-dataset-icon">▦</span><span class="bi-dataset-main"><b>${H(a.name)}</b><small>${nice(a.row_count)} 行 · ${a.columns.length} 列 · ${(a.file_bytes/1048576).toFixed(2)} MB</small></span><span class="bi-open">打开 →</span></a>`).join(''):'<div class="empty"><b>还没有 Parquet 数据资产</b><p>上传数据或迁移旧 BI 表格即可开始分析。</p></div>'}
  async function home(){
    const yes=!!info?.duckdb;
    ctx.root.innerHTML=heading('原生 BI 工作台','基于 DuckDB、Parquet 和 Perspective 的分析入口。','<a class="button" href="/projects">← 返回项目</a>')+
    `<div class="panel tp-native-status"><div><strong>分析引擎</strong><small>${yes?'DuckDB '+H(info.duckdb_version)+' · 已连接':H(info?.install_hint||'缺少 DuckDB')}</small></div><span class="badge ${yes?'success':'review'}">${yes?'可使用':'等待安装'}</span></div>
    <div class="grid grid-2"><div class="panel"><h2>导入数据资产</h2><p>支持 CSV、Parquet 和 Excel。大文件建议用 CLI 导入，不必经过浏览器上传。</p><form id="tp-native-upload" class="form"><div class="field"><label>数据文件</label><input name="file" type="file" accept=".csv,.parquet,.xlsx" required></div><div class="field"><label>资产名称</label><input name="name" required placeholder="例如：产品销售明细"></div><button class="button button-primary" type="submit" ${yes?'':'disabled'}>导入并创建快照</button></form></div>
    <div class="panel"><h2>从旧版 BI 迁移</h2><p>生成新 Parquet 资产，原始数据及历史报表继续保留。</p><div class="tp-native-legacy">${legacy.slice(0,25).map(t=>`<div class="tp-native-legacy-item"><span>${H(t.name)} <small>${nice(t.row_count)} 行</small></span><button class="button button-small" data-native="migrate" data-table="${H(t.table_id)}" ${yes?'':'disabled'}>迁移</button></div>`).join('')||'<span class="muted">没有可迁移的数据表</span>'}</div></div></div>
    <div class="panel tp-native-realtime"><h2>实时监测流 · Perspective Server</h2><p>后端通过 Table.update() 推送新增监测数据；实时流保留最近 N 行，重启后不会自动恢复，正式历史数据仍应写入 Parquet。</p>
      <form id="tp-native-stream-create" class="form"><div class="grid grid-2"><div class="field"><label>监测流名称</label><input name="name" placeholder="例如：设备 A 振动监测" required></div><div class="field"><label>保留最近行数</label><input name="limit" type="number" min="100" max="100000" value="20000"></div></div><button class="button" type="submit">新建实时流</button></form>
      <div class="bi-list">${streams.length?streams.map(t=>`<a class="bi-dataset-item" href="/bi/native/stream/${U(t.stream_id)}"><span class="bi-dataset-icon">≋</span><span class="bi-dataset-main"><b>${H(t.name)}</b><small>已接收 ${nice(t.events_received)} 条 · 保留最多 ${nice(t.limit)} 行</small></span><span class="bi-open">查看 →</span></a>`).join(''):'<p class="muted">目前没有正在运行的实时流。</p>'}</div></div>
    <div class="section-row"><h2>数据资产</h2><span class="muted">${assets.length} 项</span></div><div class="panel bi-list-panel"><div class="bi-list">${assetItems()}</div></div>`;
  }
  function grid(result){
    const names=(result.columns||[]).filter(x=>x!=='__tp_rowid');
    const body=(result.rows||[]).map((r,i)=>`<tr><td class="tp-native-rownum">${r.__tp_rowid===undefined?nice(result.offset+i+1):nice(Number(r.__tp_rowid)+1)}</td>${names.map(c=>`<td ${r.__tp_rowid!==undefined?`data-native="cell" data-row="${r.__tp_rowid}" data-column="${H(c)}" title="点击加入编辑草稿"`:''}>${H(r[c])}</td>`).join('')}</tr>`).join('');
    return `<div class="tp-native-grid-scroll"><table class="data-table tp-native-table"><thead><tr><th>行</th>${names.map(c=>`<th>${H(c)}</th>`).join('')}</tr></thead><tbody>${body||'<tr><td colspan="100">没有匹配记录</td></tr>'}</tbody></table></div>`;
  }
  function selectNativeCell(cell){
    if(!current || !checkLatest() || !Number.isSafeInteger(cell.row_id) || cell.row_id < 0 ||
       cell.row_id >= current.row_count || !current.columns.some(c=>c.name===cell.column))return;
    const box=document.getElementById('tp-native-selection');if(!box)return;
    const old=cell.value;
    box.innerHTML=`<b>第 ${nice(cell.row_id+1)} 行 · ${H(cell.column)}</b><p class="muted">当前值：${H(old)}</p><div class="field"><label>修改后的值</label><input id="tp-native-cell-value" value="${H(old)}"></div><label class="tp-native-null"><input id="tp-native-cell-null" type="checkbox"> 设为缺失值 (NULL)</label><button class="button button-primary" type="button" data-native="queue-cell" data-row="${cell.row_id}" data-column="${H(cell.column)}">加入编辑草稿</button>`;
    document.getElementById('native-edit')?.scrollIntoView({behavior:'smooth'});
  }
  async function runQuery(spec){
    const output=document.getElementById('tp-native-results');if(!output)return;
    output.innerHTML='<p class="muted">正在查询…</p>';
    try{
      const result=await ctx.req('/native-bi/query',post(spec));
      lastQuery=spec;lastResult=result;
      output.innerHTML=`<div class="tp-native-query-summary"><span>版本 ${H(result.version_id==='base'?'原始快照':result.version_id.slice(0,8))}</span><span>返回 ${nice(result.returned)} 行</span><span>耗时 ${H(result.elapsed_ms)} ms</span></div>${grid(result)}`;
    }catch(e){output.innerHTML=alert(e.message)}
  }
  async function renderHistory(){
    const slot=document.getElementById('tp-native-history-list');if(!slot||!current)return;
    try{
      const r=await ctx.req('/native-bi/assets/'+U(current.asset_id)+'/versions');
      latestVersion=r.current_version;
      slot.innerHTML=`<div class="tp-native-history-item"><span><b>原始数据</b><small>base · 永久保留</small></span><button class="button button-small" data-native="rollback" data-version="base" ${r.current_version==='base'?'disabled':''}>回滚到此</button></div>`+
        r.versions.map(v=>`<div class="tp-native-history-item"><span><b>${H(v.comment||v.kind)}</b><small>${H(v.version_id.slice(0,8))} · ${nice(v.changed_cells)} 个修改 · ${H(v.created_at||'')}</small></span><div><span class="subtle-tag">${v.is_current?'当前':v.kind==='edit'?'修改':'回滚'}</span><button class="button button-small" data-native="rollback" data-version="${H(v.version_id)}" ${v.is_current?'disabled':''}>恢复</button></div></div>`).join('');
      const select=document.getElementById('tp-native-version');
      if(select){select.innerHTML=`<option value="${H(latestVersion)}">当前数据 · ${H(latestVersion.slice(0,8))}</option><option value="base">原始快照</option>${r.versions.map(v=>`<option value="${H(v.version_id)}">${H(v.version_id.slice(0,8))} · ${H(v.comment)}</option>`).join('')}`;select.value=selectedVersion;}
      const badge=document.getElementById('tp-native-version-label');if(badge)badge.textContent=checkLatest()?'当前可编辑版本':'正在查看历史版本（只读）';
      const undo=document.getElementById('tp-native-undo');if(undo)undo.disabled=latestVersion==='base';
    }catch(e){slot.innerHTML=alert(e.message)}
  }
  function renderDraft(){
    const el=document.getElementById('tp-native-draft');if(!el)return;
    el.innerHTML=`<div class="tp-native-draft-header"><b>待提交 ${editor.length} 个单元格</b><button type="button" class="button button-small" data-native="clear-draft" ${editor.length?'':'disabled'}>清空草稿</button></div>`+
      (editor.length?`<div class="tp-native-draft-list">${editor.slice(-15).map((e,i)=>`<div>${nice(e.row_id+1)} 行 · ${H(e.column)} → ${H(e.value===null?'NULL':e.value)}</div>`).join('')}</div>`:'<p class="muted">点击下方数据表的单元格，或粘贴来自 Excel 的多行数据。</p>');
    const btn=document.getElementById('tp-native-preview-edit');if(btn)btn.disabled=!editor.length||!checkLatest();
  }
  function addEdit(edit){
    if(!checkLatest()){ctx.toast('当前显示历史版本，请切回最新版本后编辑');return}
    const key=x=>`${x.row_id}\u0000${x.column}`;
    const index=editor.findIndex(x=>key(x)===key(edit));
    if(index>=0)editor[index]=edit; else editor.push(edit);
    if(editor.length>5000){editor.pop();ctx.toast('每批最多修改 5000 个单元格');}
    preview=null;previewBatch=null;
    renderDraft();
  }
  async function restoreVisualSource(source, version){
    if(!source)return;
    if(source.kind==='virtual')await loadVirtual(version);
    else if(source.kind==='sample')await loadSample(version,source.limit);
    else if(source.kind==='query')await loadServerQuery({...source.query,version_id:version});
    else if(source.kind==='industrial')await industrialAnalyze({...source.industrial_spec,version_id:version});
  }
  async function refreshCurrent(previous=null){
    await render({...ctx});
    if(previous?.source){
      // detail() already loads the latest full-data source. Reusing it avoids
      // closing/reopening WebSocket twice on every edit or undo operation.
      const alreadyLoaded = previous.source.kind === 'virtual' &&
        visualSource?.kind === 'virtual' && visualSource.version_id === latestVersion;
      if(!alreadyLoaded)await restoreVisualSource(previous.source, latestVersion);
      if(previous.workspace && mod)await mod.restorePerspectiveState(previous.workspace,
        previous.root || 'timepilot_active');
    }
  }
  function industrialSelect(names,selected='',blank=true){
    return (blank?'<option value="">不使用</option>':'')+names.map(col=>`<option value="${H(col.name)}" ${col.name===selected?'selected':''}>${H(col.name)} · ${H(col.physical_type||'文本')}</option>`).join('');
  }
  async function loadIndustrialForm(){
    const panel=document.getElementById('tp-native-industrial');
    if(!panel||!ctx.projectId||!current)return;
    try{
      const prefix='/projects/'+U(ctx.projectId)+'/industrial';
      const [meta,views]=await Promise.all([ctx.req(prefix+'/assets/'+U(current.asset_id)+'/suggestions'),ctx.req(prefix+'/views')]);
      const guess=meta.suggested_fields||{},list=meta.fields||current.columns;
      panel.innerHTML=`<div class="bi-card-title"><h2>工业分析 · 维护事件与工况</h2><span class="subtle-tag">完整数据聚合 · 版本 ${H(selectedVersion.slice(0,12))}</span></div>
        <p class="muted small">先核实时间、设备和指标。事件标记与工况状态来自实际源字段；按时间桶聚合并保留源版本，超过最大点数时要求调整时间范围，不截断。</p>
        <form class="form" id="tp-industrial-form"><div class="grid grid-3">
          <label class="field"><span>分析模板</span><select name="template">${meta.templates.map(t=>`<option value="${H(t.id)}">${H(t.name)}</option>`).join('')}</select></label>
          <label class="field"><span>时间字段</span><select name="time_field" required>${industrialSelect(list,guess.time_field,false)}</select></label>
          <label class="field"><span>分析指标</span><select name="metric_field" required>${industrialSelect(list,guess.metric_field,false)}</select></label>
          <label class="field"><span>设备字段</span><select name="device_field">${industrialSelect(list,guess.device_field)}</select></label>
          <label class="field"><span>设备编号</span><input name="device_value" placeholder="选择设备字段后填写"></label>
          <label class="field"><span>时间桶</span><select name="interval"><option value="1m">1 分钟</option><option value="5m">5 分钟</option><option value="15m">15 分钟</option><option value="1h" selected>1 小时</option><option value="1d">1 天</option></select></label>
          <label class="field"><span>维护事件字段</span><select name="event_field">${industrialSelect(list,guess.event_field)}</select></label>
          <label class="field"><span>工况字段</span><select name="regime_field">${industrialSelect(list,guess.regime_field)}</select></label>
          <label class="field"><span>最大图表时间桶</span><input name="max_points" type="number" min="10" max="2000" value="1000"></label>
          <label class="field"><span>起始时间（可选）</span><input name="time_from" type="datetime-local"></label>
          <label class="field"><span>截止时间（可选）</span><input name="time_to" type="datetime-local"></label></div>
        <div class="action-row"><button type="button" class="button button-primary" data-native="industrial-analyze">生成 Perspective 工业分析</button><button type="button" class="button" data-native="industrial-save">保存工业分析模板</button><button type="button" class="button" data-native="load-virtual">返回原始明细</button></div></form>
        <div id="tp-industrial-result" role="status"><p class="muted small">请确认字段映射后生成图表。</p></div>
        <div class="tp-industrial-saved"><strong>已保存的工业视图</strong>${views.length?views.filter(v=>v.asset_id===current.asset_id).map(v=>`<button type="button" class="button button-small" data-native="industrial-restore" data-view="${H(v.view_id)}">${H(v.name)} · ${H(v.version_id.slice(0,8))}</button>`).join(''):'<p class="muted">暂无保存记录</p>'}</div>`;
    }catch(error){panel.innerHTML=alert('工业分析服务暂不可用：'+error.message)}
  }
  function industrialSpec(){
    const form=document.getElementById('tp-industrial-form');
    if(!form)throw new Error('工业分析字段表单未加载');
    const values=Object.fromEntries(new FormData(form));
    const result={asset_id:current.asset_id,version_id:selectedVersion,max_points:Number(values.max_points||1000),
      template:values.template,time_field:values.time_field,metric_field:values.metric_field,interval:values.interval};
    for(const key of ['device_field','device_value','event_field','regime_field','time_from','time_to'])if(values[key])result[key]=values[key];
    return result;
  }
  async function industrialAnalyze(spec=industrialSpec()){
    const area=document.getElementById('tp-industrial-result');
    area.innerHTML='<p class="muted">正在从完整源版本生成工业分析时间桶与事件标记…</p>';
    const prefix='/projects/'+U(ctx.projectId)+'/industrial';
    const result=await ctx.req(prefix+'/preview',post(spec));
    const events=result.events||[];
    area.innerHTML=`<div class="tp-native-query-summary"><span>聚合时间桶 ${nice(result.rows.length)}</span><span>事件 ${nice(events.length)}${result.events_truncated?'（有更多事件）':''}</span><span>混合工况桶 ${nice(result.regime_mixed_buckets)}</span><span>耗时 ${H(result.elapsed_ms)}ms</span></div>
      ${result.regime_mixed_buckets?alert('部分时间桶包含多种工况，工况字段仅显示众数。需要工况内分析时请缩小时间桶。'):''}
      <p class="muted small">下方为维护事件的原始时间和标识。原生图表包含 maintenance_marker 事件定位列；可以拖拽到线图中与指标同步查看。</p>
      <div class="tp-industrial-events">${events.length?`<table class="data-table"><thead><tr><th>时间</th><th>维护事件</th><th>工况</th></tr></thead><tbody>${events.slice(0,60).map(e=>`<tr><td>${H(e.time)}</td><td>${H(e.event)}</td><td>${H(e.regime||'—')}</td></tr>`).join('')}</tbody></table>`:'<p class="muted small">该筛选范围没有维护事件或未设置事件字段。</p>'}</div>`;
    const bytes=await getArrow('/api/v1'+prefix+'/arrow',post(spec),selectedVersion);
    await displayArrow(bytes,{kind:'industrial',version_id:selectedVersion,industrial_spec:spec});
    await mod.setPerspectivePreset('line');
    ctx.toast('已生成工业分析图表，完整源数据未被修改');
  }

  function recommendedAnalyses(asset){
    const columns=asset.columns||[],roles=asset.lineage?.confirmed_roles||{};
    const names=new Set(columns.map(x=>x.name));
    const typed=columns.filter(x=>/DOUBLE|FLOAT|DECIMAL|INTEGER|BIGINT|SMALLINT|NUMBER|REAL/i.test(x.physical_type||''));
    const time=names.has(roles.time)?roles.time:columns.find(x=>/DATE|TIMESTAMP|DATETIME/i.test(x.physical_type||''))?.name;
    const entity=names.has(roles.entity)?roles.entity:null;
    const items=[];
    if(time&&typed.length)items.push({id:'line',title:'时间趋势',reason:'时间字段 '+time+' · '+typed.length+' 项数值字段'});
    if(entity&&typed.length)items.push({id:'pivot',title:'设备/批次比较',reason:'已确认标识 '+entity+' · 支持分组汇总'});
    if(!time&&typed.length)items.push({id:'bar',title:'指标分布与对比',reason:'已有数值指标，可选择分类字段'});
    items.push({id:'grid',title:'完整字段检查',reason:'浏览全部源列及其原始顺序'});
    return items.slice(0,3);
  }
  function suggestedViewsBlock(asset,canDisplay){
    const suggestions=recommendedAnalyses(asset);
    return `<div class="tp-bi-suggestions"><div><b>根据数据结构建议</b><small>字段类型与用户确认的业务角色决定建议；图表调整不改变训练行集。</small></div><div class="tp-bi-suggestion-actions">${suggestions.map(v=>`<button type="button" class="button button-small" data-native="preset" data-preset="${v.id}" title="${H(v.reason)}" ${canDisplay?'':'disabled'}>${H(v.title)}</button>`).join('')}</div></div>`;
  }
  async function detail(id){
    current=await ctx.req('/native-bi/assets/'+U(id));
    latestVersion=current.head_version||'base';selectedVersion=latestVersion;
    const columns=current.columns, has=!!info?.perspective_bundle_ready, live=!!info?.perspective_virtual_server_ready;
    ctx.root.innerHTML=heading(current.name,`${nice(current.row_count)} 行 × ${columns.length} 列 · Parquet 原始快照 + 版本化编辑`,
      `<a href="${ctx.projectId?'/projects/'+U(ctx.projectId)+'/data':'/bi/native'}" class="button">← ${ctx.projectId?'项目数据':'数据资产'}</a><button class="button" data-native="verify">完整性校验</button>${ctx.projectId && current.lineage?.type==='project_generic_table_v016'?'<button class="button button-primary" data-native="open-modeling">确认数据范围并建模</button>':''}`)+
      `<div class="tp-native-tabs"><a href="#native-explore">数据探索</a><a href="#native-edit">版本化编辑</a><a href="#native-query">服务端查询</a><a href="#native-history">操作历史</a><a href="#native-views">已保存视图</a></div>
      <div class="panel" id="native-explore"><div class="bi-card-title"><h2>Perspective 原生分析</h2><span class="subtle-tag" id="tp-native-version-label">当前可编辑版本</span></div>
      <p>拖动字段可观察趋势、对比设备、进行透视；系统保存分析布局与数据版本。图表内部过滤只影响显示，训练范围由下方全局筛选确认。</p>
      ${suggestedViewsBlock(current,has)}
      <div class="tp-native-tools"><button class="button button-primary" data-native="load-virtual" ${live?'':'disabled'}>完整数据 · 原生网格</button>
      <button class="button" data-native="load-sample" ${has?'':'disabled'}>有限样本 · 预览</button>
      <button class="button" data-native="visualize-query" ${has?'':'disabled'}>可视化服务端聚合</button>
      <button class="button" data-native="preset" data-preset="grid" ${has?'':'disabled'}>网格</button><button class="button" data-native="preset" data-preset="pivot" ${has?'':'disabled'}>透视</button><button class="button" data-native="preset" data-preset="bar" ${has?'':'disabled'}>柱状图</button><button class="button" data-native="preset" data-preset="line" ${has?'':'disabled'}>折线图</button><button class="button" data-native="industrial-open" ${ctx.projectId?'':'disabled'}>工业分析与维护事件</button><button class="button" data-native="preset" data-preset="dashboard" ${has?'':'disabled'}>双面板看板</button>
      <button class="button" data-native="save-view" ${has?'':'disabled'}>保存视图</button></div>
      <p class="tp-native-note" id="tp-native-viz-hint">${H(footnote)}</p>
      <div class="tp-native-perspective" id="tp-native-perspective">${has?'<p class="muted">准备原生分析组件…</p>':alert('Perspective 构建不完整：'+(info?.perspective_bundle?.problems||[]).join('；')+'。需要完整 JS/CSS/WASM 离线资源。')}</div></div>
      ${ctx.projectId?'<div class="panel" id="tp-native-industrial"><p class="muted">准备工业分析模板…</p></div>':''}
      <details class="panel tp-native-advanced" id="native-advanced"><summary>高级操作：字段类型与版本编辑</summary><div class="tp-native-advanced-body"><div class="panel" id="native-types"><div class="bi-card-title"><h2>字段类型与分析语义</h2><span class="subtle-tag">生成新 Parquet 资产 · 原始数据不变</span></div>
        <p>销量、金额和日期等字段可明确指定类型；物料编码和带前导零的编号建议保留文本。转换前执行全量校验，不兼容值会阻断保存。</p>
        <div class="tp-native-types-list">${columns.map(c=>`<span class="subtle-tag">${H(c.name)}：${H(c.physical_type)}${c.semantic_type==='identifier'?' · 编码':''}</span>`).join('')}</div>
        <form class="form" id="tp-native-derive-types"><div class="grid grid-3"><div class="field"><label>字段</label><select name="column" required>${opts(columns,false)}</select></div><div class="field"><label>转换类型</label><select name="type"><option value="DOUBLE">数值（浮点）</option><option value="BIGINT">整数</option><option value="DATE">日期</option><option value="TIMESTAMP">时间戳</option><option value="BOOLEAN">布尔</option><option value="VARCHAR">文本</option></select></div><div class="field"><label>新数据资产名称</label><input name="name" placeholder="可留空，自动命名"></div></div><button class="button" type="submit">校验全表并生成新资产</button></form></div>
      </div></details><div class="panel" id="native-query"><div class="bi-card-title"><h2>全局数据选择与服务端查询</h2><span class="subtle-tag">图表分页不限制建模数据</span></div>
      <div class="tp-native-query-toolbar"><label>查看版本 <select id="tp-native-version"><option value="base">原始数据</option></select></label><button class="button button-small" data-native="reload-latest">切回最新版本</button></div>
      <form id="tp-native-query" class="form"><div class="grid grid-3"><div class="field"><label>筛选字段</label><select name="column">${opts(columns)}</select></div><div class="field"><label>筛选方式</label><select name="operator"><option value="contains">包含</option><option value="eq">等于</option><option value="gt">大于</option><option value="lt">小于</option><option value="is_null">空值</option><option value="not_null">非空</option></select></div><div class="field"><label>筛选值</label><input name="value" placeholder="可为空"></div></div>
      <div class="grid grid-3"><div class="field"><label>分组字段</label><select name="group">${opts(columns)}</select></div><div class="field"><label>统计方式</label><select name="aggregate"><option value="count">数量</option><option value="sum">求和</option><option value="avg">平均</option><option value="count_distinct">去重计数</option><option value="min">最小值</option><option value="max">最大值</option></select></div><div class="field"><label>统计字段</label><select name="metric">${opts(columns)}</select></div></div>
      <p class="muted small">下方筛选决定完整数据行集，汇总及透视仅改变图表；查看版本不改变原始文件。</p><div class="tp-native-query-footer"><div class="field"><label>每页</label><select name="limit"><option>50</option><option>100</option><option>250</option><option>500</option><option>1000</option></select></div><div class="field"><label>偏移</label><input name="offset" type="number" min="0" max="2000000" value="0"></div><button class="button button-primary" type="submit">执行查询</button></div></form><div id="tp-native-results"></div></div>
      <details class="panel tp-native-advanced" id="native-edit"><summary>高级操作：修改数据和生成新版本</summary><div class="tp-native-advanced-body"><div class="panel"><div class="bi-card-title"><h2>版本化批量编辑</h2><span class="subtle-tag">仅写修改层 · 不覆盖原始数据</span></div>
      <p>可直接点击上方 Perspective 原生明细网格的单元格进行修改。聚合或图表数据不可直接编辑；也可使用下方查询结果或 Excel 粘贴。所有修改都先预览、再确认保存。</p>
      <div class="tp-native-editor-grid"><div><div class="tp-native-editor-head">当前选中的单元格</div><div id="tp-native-selection" class="tp-native-selection">可直接点击上方 Perspective 原生明细网格，或下方查询表中的单元格。</div>
      <div class="tp-native-editor-head" style="margin-top:18px">从 Excel 批量粘贴</div>
      <form id="tp-native-paste" class="form"><div class="grid grid-2"><div class="field"><label>起始行（从 1 开始）</label><input name="row" type="number" min="1" value="1" required></div><div class="field"><label>起始字段</label><select name="column">${opts(columns,false)}</select></div></div><div class="field"><label>粘贴区域（Tab 分列、换行分行）</label><textarea name="tsv" rows="5" placeholder="100\t200\n120\t220" required></textarea></div><small>单元格输入 &lt;NULL&gt; 表示缺失值；空白表示空字符串。</small><button class="button" type="submit">加入编辑草稿</button></form></div>
      <div><div id="tp-native-draft"></div><div class="tp-native-editor-actions"><button class="button button-primary" data-native="preview-edit" id="tp-native-preview-edit" disabled>预览批量修改</button><button class="button" data-native="commit-edit" id="tp-native-commit-edit" disabled>确认并生成新版本</button></div><div id="tp-native-edit-preview"></div></div></div></div>
      </div></details><details class="panel tp-native-advanced" id="native-history"><summary>高级操作：历史版本与回滚</summary><div class="tp-native-advanced-body"><div class="panel"><div class="bi-card-title"><h2>修改历史与版本回滚</h2><button class="button" data-native="undo" id="tp-native-undo">撤销上一步</button></div><p>每次编辑生成新版本；回滚只更改当前版本指针。旧版本和原始文件均可独立校验。</p><div id="tp-native-history-list"></div></div>
      </div></details><div class="panel" id="native-views"><div class="bi-card-title"><h2>已保存的 Perspective 视图</h2></div><div id="tp-native-saved"><p class="muted">正在读取…</p></div></div>
      <details class="panel tp-native-advanced" id="native-assistant"><summary>高级操作：AI 查询建议</summary><div class="tp-native-advanced-body"><div class="panel"><h2>AI 查询建议</h2><p>大模型只接收字段和类型，返回受约束的查询草稿。用户确认后才执行。</p><form id="tp-native-ai" class="form"><div class="field"><label>分析需求</label><textarea name="request" rows="2" required placeholder="例如：按区域计算销量总和，并从高到低排序"></textarea></div><button class="button" type="submit">生成分析计划</button></form><div id="tp-native-ai-result"></div></div></div></details>`;
    // Mark loading as soon as the page exists, before any independent API
    // requests. The browser acceptance waits for this explicit state.
    const visualization=document.getElementById('tp-native-perspective');
    if(visualization && has)setPerspectiveState(visualization,'loading');
    else if(visualization)setPerspectiveState(visualization,'error','Perspective 离线资源缺失');
    await renderHistory();renderDraft();
    await listViews();
    if(ctx.projectId)await loadIndustrialForm();
    const selector=document.getElementById('tp-native-version');
    selector.addEventListener('change',async e=>{
      const oldWorkspace=mod?await mod.savePerspectiveState().catch(()=>null):null;
      const oldRoot=oldWorkspace?await mod.getActiveTableName().catch(()=>null):null;
      const source=visualSource ? structuredClone(visualSource) : {kind:'virtual'};
      selectedVersion=e.target.value;editor=[];preview=null;renderDraft();
      await runQuery({asset_id:current.asset_id,version_id:selectedVersion,limit:50,include_row_ids:true});
      await releaseWidget();await renderHistory();
      try{await restoreVisualSource(source,selectedVersion);
        if(oldWorkspace&&mod)await mod.restorePerspectiveState(oldWorkspace,oldRoot||'timepilot_active');}
      catch(err){ctx.toast('切换数据版本后加载图表失败：'+err.message)}
    });
    await runQuery({asset_id:current.asset_id,version_id:selectedVersion,limit:50,include_row_ids:true});
    if(!ctx.route.includes('/view/')){
      if(info?.perspective_virtual_server_ready){
        await loadVirtual(selectedVersion);
      }else if(visualization){
        // Never leave the initial placeholder as a permanent loading state.
        const reason=info?.perspective_virtual_server_error ||
          (info?.perspective_bundle?.problems || []).join('；') ||
          'Perspective Python Virtual Server 或离线浏览器资源尚未就绪';
        await showBasicFallback(reason);
      }
    }
  }
  async function streamDetail(id){
    const data=await ctx.req('/native-bi/streams/'+U(id));
    current=null;
    ctx.root.innerHTML=heading(data.name,'实时工业时序 · Perspective Python Server · 不覆盖历史 Parquet',
      '<a href="/bi/native" class="button">← 数据工作台</a>')+
      `<div class="panel"><h2>持续监测数据流</h2><p>当前服务已接收 ${nice(data.events_received)} 条，保留最近 ${nice(data.limit)} 行。WebSocket 新事件将自动刷新原生网格及图表，无须手动刷新页面。</p>
      <div class="tp-native-tools"><button class="button" data-native="stream-preset" data-preset="grid">网格</button><button class="button" data-native="stream-preset" data-preset="line">趋势折线</button><button class="button" data-native="stream-preset" data-preset="pivot">设备透视</button></div>
      <div class="tp-native-perspective" id="tp-native-perspective"><p class="muted">正在连接实时引擎…</p></div></div>
      <div class="panel"><h2>发送一条测试事件</h2><p>仅供手动验收实时推送。此操作不会修改原始 Parquet 或训练数据。</p>
      <form id="tp-native-stream-append" class="form"><div class="grid grid-3"><div class="field"><label>设备/序列</label><input name="series_id" value="equipment-A" required></div><div class="field"><label>监测数值</label><input name="value" type="number" step="any" value="1.5" required></div><div class="field"><label>状态</label><input name="status" value="ok"></div></div><button class="button button-primary" type="submit">发送实时事件</button></form><p id="tp-stream-status" class="muted"></p></div>`;
    await releaseWidget();
    const panel=document.getElementById('tp-native-perspective');
    setPerspectiveState(panel,'loading');
    try{
      const module=await ensurePerspectiveModule();
      const url=(location.protocol==='https:'?'wss:':'ws:')+'//'+location.host+
        '/api/v1/native-bi/streams/'+U(id)+'/ws';
      await module.mountPerspectiveRemote(panel,url,{tableName:data.table_name});
      setPerspectiveState(panel,'ready');
    }catch(err){setPerspectiveState(panel,'error',err.message);panel.innerHTML=alert('实时组件连接失败：'+err.message)}
  }
  async function listViews(){
    const root=document.getElementById('tp-native-saved');if(!root||!current)return;
    try{const v=await ctx.req('/native-bi/assets/'+U(current.asset_id)+'/views');
      root.innerHTML=v.length?v.map(x=>`<a class="tp-native-view" href="${ctx.projectId?'/projects/'+U(ctx.projectId):''}/bi/native/${U(current.asset_id)}/view/${U(x.view_id)}">${H(x.name)} <small>打开 →</small></a>`).join(''):'<p class="muted">尚无保存的视图。</p>';
    }catch(e){root.innerHTML=alert(e.message)}
  }
  function setPerspectiveState(slot,state,error=''){
    if(!slot)return;
    slot.dataset.perspectiveState=state;
    slot.dataset.perspectiveError=state==='error'?String(error).slice(0,600):'';
  }
  async function releaseWidget(){chartRequest++;if(mod){try{await mod.disposePerspective()}catch{} }mod=null;visualSource=null;}
  async function ensurePerspectiveModule(){
    if(mod)return mod;
    // The manifest contains the actual bundled module digest, so a rebuild
    // in a long-running browser tab is never confused with an old ESM import.
    const manifestResponse=await fetch('/assets/native-bi/perspective-build.json',{cache:'no-store'});
    if(!manifestResponse.ok)throw new Error('Perspective 发布清单不可用（HTTP '+manifestResponse.status+'）。请在开发环境执行 python scripts/build_native_bi.py');
    const manifest=await manifestResponse.json();
    const entry=manifest.files?.['perspective-entry.js'];
    if(!entry||!/^([0-9a-f]{64})$/.test(String(entry.sha256||''))){
      throw new Error('Perspective 发布清单缺少有效的 JS 构建指纹，请重新构建');
    }
    const stamp=entry.sha256.slice(0,16);
    const js='/assets/native-bi/perspective-entry.js?v='+stamp;
    const candidate=await import(js);
    const missing=PERSPECTIVE_API.filter(name=>typeof candidate[name]!=='function');
    if(missing.length){
      throw new Error('Perspective 浏览器模块缺少导出 ['+missing.join(', ')+']；当前 JS 构建接口不完整。请重新执行 python scripts/build_native_bi.py（Vite 应启用 preserveEntrySignatures: strict）');
    }
    if(!document.querySelector('[data-perspective-css]')){
      const link=document.createElement('link');link.rel='stylesheet';
      link.href='/assets/native-bi/perspective-entry.css?v='+stamp;
      link.dataset.perspectiveCss='1';document.head.appendChild(link);
    }
    mod=candidate;
    return mod;
  }

  function markVisualSource(source){
    visualSource=source;
    const node=document.getElementById('tp-native-viz-hint');
    if(!node)return;
    const v=source.version_id==='base'?'原始快照':source.version_id.slice(0,8);
    node.textContent=(source.kind==='virtual'?`完整数据视图 · DuckDB 查询下推 · 版本 ${v} · ${nice(current.row_count)} 行。`:
      source.kind==='sample'?`仅前 ${nice(source.limit)} 行样本 · 版本 ${v} · 不代表 ${nice(current.row_count)} 行全量数据。`:
      `DuckDB 服务端结果（最多 ${nice(source.query?.limit||1000)} 组）· 版本 ${v}。`);
  }
  function fallbackBar(rows) {
    const items=(rows||[]).slice(0,32), max=Math.max(1,...items.map(x=>Math.max(0,Number(x.value)||0)));
    const size=760, step=(size-48)/Math.max(1,items.length);
    return `<svg role="img" aria-label="服务端基础 BI 柱状图" viewBox="0 0 760 225" style="width:100%;max-width:100%;height:auto"><line x1="30" y1="180" x2="745" y2="180" stroke="#b8cde6"/>${items.map((x,i)=>{const v=Math.max(0,Number(x.value)||0),h=145*v/max,pos=32+i*step;return `<rect x="${pos}" y="${180-h}" width="${Math.max(2,step*.65)}" height="${h}" fill="#337bd2"><title>${H(x.name)}：${H(x.value)}</title></rect><text x="${pos}" y="198" font-size="10" fill="#61728c">${H(String(x.name).slice(-5))}</text>`}).join('')}</svg>`;
  }
  async function showBasicFallback(reason='Perspective 原生引擎尚未就绪') {
    const slot=document.getElementById('tp-native-perspective');if(!slot||!current)return;
    setPerspectiveState(slot,'fallback',reason);
    const cols=current.columns;
    const dims=cols.filter(c=>!/(INT|DOUBLE|DECIMAL|FLOAT|BIGINT)/i.test(c.physical_type||''));
    const vals=cols.filter(c=>/(INT|DOUBLE|DECIMAL|FLOAT|BIGINT)/i.test(c.physical_type||''));
    slot.innerHTML=`<div class="tp-native-basic"><div class="helper warning"><b>基础 BI 模式</b> · ${H(reason)}<p>数据浏览、筛选、分组汇总、编辑和版本回滚仍可使用；原生拖拽透视暂不可用。</p></div>
      <div class="tp-native-query-summary"><span>当前版本 ${H(selectedVersion==='base'?'原始数据':selectedVersion.slice(0,8))}</span><span>每次查询由 DuckDB 执行，显示结果受行数限制</span></div>
      <div class="tp-native-tools"><label>分组字段 <select id="tp-basic-group">${(dims.length?dims:cols).map(c=>`<option value="${H(c.name)}">${H(c.name)}</option>`).join('')}</select></label>
      <label>数值字段 <select id="tp-basic-metric">${(vals.length?vals:cols).map(c=>`<option value="${H(c.name)}">${H(c.name)}</option>`).join('')}</select></label>
      <button class="button button-small" data-native="fallback-pivot">查询汇总</button><button class="button button-small" data-native="fallback-grid">刷新明细</button></div>
      <div id="tp-basic-results">正在读取明细…</div><div id="tp-basic-chart"></div></div>`;
    await fallbackGrid();
  }
  async function fallbackGrid(){
    const output=document.getElementById('tp-basic-results');if(!output||!current)return;
    try {const rows=await ctx.req('/native-bi/query',post({asset_id:current.asset_id,version_id:selectedVersion,limit:80,include_row_ids:true}));
      output.innerHTML=grid(rows);}
    catch(e){output.innerHTML=alert(e.message)}
  }
  async function fallbackPivot(){
    const group=document.getElementById('tp-basic-group')?.value;
    const metric=document.getElementById('tp-basic-metric')?.value;
    const result=document.getElementById('tp-basic-chart');if(!group||!metric||!result)return;
    result.innerHTML='<p class="muted">正在汇总数据…</p>';
    try {const summary=await ctx.req('/native-bi/query',post({asset_id:current.asset_id,version_id:selectedVersion,
      group_by:[group],metrics:[{operation:'sum',column:metric,alias:'value'}],order_by:[{field:group}],limit:32}));
      result.innerHTML=fallbackBar((summary.rows||[]).map(x=>({name:x[group],value:x.value})));}
    catch(e){result.innerHTML=alert(e.message)}
  }
  function websocketUrl(version){
    const scheme=location.protocol==='https:'?'wss:':'ws:';
    return scheme+'//'+location.host+'/api/v1/native-bi/assets/'+U(current.asset_id)+'/versions/'+U(version)+'/ws'+(ctx.projectId?'?project_id='+U(ctx.projectId):'');
  }
  async function loadVirtual(version=selectedVersion){
    const request=++chartRequest;
    const slot=document.getElementById('tp-native-perspective');if(!slot)return;
    try{
      const module=await ensurePerspectiveModule();
      if(request!==chartRequest)return;
      // Retire old virtual-server views before removing their DOM host.
      // Removing the viewer first lets its deferred callbacks target a closed view.
      await module.disposePerspective();
      if(request!==chartRequest)return;
      slot.innerHTML='<p class="muted">正在连接 DuckDB 完整数据分析…</p>';
      setPerspectiveState(slot,'loading');
      await module.mountPerspectiveRemote(slot,websocketUrl(version),{
        onCell: cell => { if(checkLatest() && version===latestVersion) selectNativeCell(cell); }
      });
      if(request!==chartRequest){await module.disposePerspective();return;}
      markVisualSource({kind:'virtual',asset_id:current.asset_id,version_id:version});
      setPerspectiveState(slot,'ready');
    }catch(err){await showBasicFallback('原生分析连接失败：'+err.message);}
  }
  async function displayArrow(bytes,source){
    const request=++chartRequest;
    const slot=document.getElementById('tp-native-perspective');if(!slot)return;
    try{
      const module=await ensurePerspectiveModule();
      if(request!==chartRequest)return;
      await module.disposePerspective();
      if(request!==chartRequest)return;
      slot.innerHTML='<p class="muted">正在加载原生分析组件…</p>';
      setPerspectiveState(slot,'loading');
      await module.mountPerspective(slot,bytes);
      if(request!==chartRequest){await module.disposePerspective();return;}
      markVisualSource(source);
      setPerspectiveState(slot,'ready');
    }catch(err){setPerspectiveState(slot,'error',err.message);slot.innerHTML=alert('Perspective 原生可视化加载失败：'+err.message);throw err;}
  }
  async function getArrow(url,opts,expectedVersion){
    const response=await fetch(url,opts);
    if(!response.ok){let e;try{e=(await response.json()).detail}catch{}throw new Error(String(e||'Arrow 数据加载失败'))}
    if(response.headers.get('X-TimePilot-Version')!==expectedVersion){
      throw new Error('返回数据版本与当前选择不一致，已阻止绘图');
    }
    return response.arrayBuffer();
  }
  async function loadSample(version=selectedVersion,requestedLimit=10000){
    const limit=Math.max(1,Math.min(10000,Number(requestedLimit)||10000));
    const url='/api/v1/native-bi/assets/'+U(current.asset_id)+'/sample.arrow?limit='+limit+'&version_id='+U(version);
    await displayArrow(await getArrow(url,undefined,version),{kind:'sample',version_id:version,limit});
  }
  async function loadServerQuery(spec=lastQuery){
    if(!spec)throw new Error('请先执行服务端查询');
    const query={...spec,asset_id:current.asset_id,version_id:spec.version_id||selectedVersion,
      limit:Math.min(1000,Math.max(1,spec.limit||1000)),offset:spec.offset||0};
    await displayArrow(await getArrow('/api/v1/native-bi/query.arrow',post(query),query.version_id),
      {kind:'query',version_id:query.version_id,query});
  }
  function specFrom(form){
    const f=Object.fromEntries(new FormData(form));const spec={asset_id:current.asset_id,version_id:selectedVersion,limit:Math.min(1000,Number(f.limit||50)),offset:Number(f.offset||0),include_row_ids:!f.group};
    if(f.column){const item={column:f.column,op:f.operator};if(!['is_null','not_null'].includes(f.operator))item.value=['gt','lt'].includes(f.operator)?Number(f.value):f.value;spec.filters=[item]}
    if(f.group){spec.group_by=[f.group];spec.metrics=[{operation:f.aggregate,column:f.aggregate==='count'&&!f.metric?null:(f.metric||null),alias:'metric'}]}
    return spec;
  }
  async function render(passed){
    ctx=passed;ctx.activeViewId=null;current=null;editor=[];preview=null;lastResult=null;lastQuery=null;selectedVersion='base';latestVersion='base';
    await releaseWidget();
    ctx.root.innerHTML='<div class="panel"><p class="muted">正在连接数据分析引擎…</p></div>';
    try{
      const parts=ctx.route.split('/').filter(Boolean);
      if(ctx.projectId){
        const scoped=await Promise.all([ctx.req('/native-bi/capabilities'),ctx.req('/projects/'+U(ctx.projectId)+'/native-assets')]);
        info=scoped[0];assets=scoped[1].map(v=>v.asset||{asset_id:v.native_asset_id,columns:[],row_count:0});legacy=[];streams=[];
      }else{
        [info,assets,legacy,streams]=await Promise.all([ctx.req('/native-bi/capabilities'),ctx.req('/native-bi/assets'),ctx.req('/bi/tables'),ctx.req('/native-bi/streams')]);
      }
      if(location.pathname!==ctx.route && !(ctx.projectId && location.pathname==='/projects/'+U(ctx.projectId)+ctx.route))return;
      if(parts[2]==='stream' && parts[3]){
        await streamDetail(parts[3]);
      }else if(parts.length>=3){
        await detail(parts[2]);
        if(parts.length===5 && parts[3]==='view'){
          try{await openSavedView(parts[4])}catch(e){ctx.toast('恢复保存视图失败：'+e.message)}
        }
      }else await home();
    }catch(e){ctx.root.innerHTML=heading('原生 BI','加载失败')+alert(e.message)}
  }
  async function openSavedView(viewId){
    const saved=await ctx.req('/native-bi/views/'+U(viewId));
    if(saved.asset_id!==current.asset_id)throw new Error('视图与当前资产不匹配');
    const config=saved.config;
    if(config.format==='timepilot-perspective-v2'){
      const source=config.source;
      selectedVersion=source.version_id;
      await renderHistory();
      if(source.kind==='query')await loadServerQuery(source.query);
      else if(source.kind==='virtual')await loadVirtual(source.version_id);
      else await loadSample(source.version_id);
      await mod.restorePerspectiveState(config.workspace,config.base_table_name||'timepilot_active');
    }else{
      if(!mod)await loadSample();
      await mod.restorePerspectiveState(config);
    }
    ctx.activeViewId=viewId;document.getElementById('native-explore')?.scrollIntoView({behavior:'smooth'});
    ctx.toast('已恢复保存的 Perspective 视图及数据版本');
  }
  async function click(event){
    const btn=event.target.closest('[data-native]');if(!btn)return false;
    const type=btn.dataset.native;
    if(type==='stream-preset'){
      try{if(mod)await mod.setPerspectivePreset(btn.dataset.preset);}catch(e){ctx.toast(e.message)}return true;
    }
    if(type==='migrate'){
      btn.disabled=true;try{const a=await ctx.req('/native-bi/assets/from-legacy/'+U(btn.dataset.table),{method:'POST'});ctx.navigate('/bi/native/'+a.asset_id)}catch(e){ctx.toast(e.message)}finally{btn.disabled=false}return true;
    }
    if(!current)return true;
    try{
      if(type==='open-modeling'){
        if(!ctx.projectId || current.lineage?.type!=='project_generic_table_v016')throw new Error('请先发布项目通用数据资产');
        if(!window.TimePilotModeling)throw new Error('建模工作台未加载');
        if(selectedVersion!=='base')throw new Error('当前为 BI 编辑版本；请先把这份编辑结果发布为新的不可变通用资产，再建立可复现模型。可切换到原始数据版本直接建模。');
        const filters=visualSource?.kind==='query'?(visualSource.query?.filters||[]):[];
        const location='/projects/'+U(ctx.projectId)+'/selections';
        const selectionPayload={native_asset_id:current.asset_id,version_id:selectedVersion,filters};
        const result=await ctx.req(location+'/preview',post(selectionPayload));
        const note=visualSource?.kind==='query' ? '复用服务端明细筛选；忽略图表分组、聚合和分页。'
                   : '当前使用完整源数据；图表内部过滤和显示的样本行不会限制训练数据。';
        if(!confirm(`确认使用 ${nice(result.row_count)} 条记录建立模型？\n${note}\n源版本 ${selectedVersion}`))return true;
        const frozen=await ctx.req(location,post({...selectionPayload,confirm:true}));
        await window.TimePilotModeling.open({ctx,projectId:ctx.projectId,assetId:current.asset_id,
             versionId:selectedVersion,viewId:ctx.activeViewId||null,filters,selectionId:frozen.selection_id});
      }
      else if(type==='verify'){
        const data=await ctx.req('/native-bi/assets/'+U(current.asset_id)+'/versions/verify');
        ctx.toast(data.valid?`完整性通过：${data.patches_checked} 个修改文件`:`校验失败：${data.invalid_versions.length} 个修改文件异常`);
      }else if(type==='reload-latest'){
        const source=visualSource?structuredClone(visualSource):{kind:'virtual'};
        const workspace=mod?await mod.savePerspectiveState().catch(()=>null):null;
        const root=workspace?await mod.getActiveTableName().catch(()=>null):null;
        selectedVersion=latestVersion;editor=[];await releaseWidget();await renderHistory();renderDraft();
        await runQuery({asset_id:current.asset_id,version_id:selectedVersion,include_row_ids:true,limit:50});
        await restoreVisualSource(source,selectedVersion);
        if(workspace&&mod)await mod.restorePerspectiveState(workspace,root||'timepilot_active');
      }
      else if(type==='industrial-open'){
        if(!ctx.projectId)throw new Error('请先进入项目再使用工业分析模板');
        document.getElementById('tp-native-industrial')?.scrollIntoView({behavior:'smooth'});
      }
      else if(type==='industrial-analyze'){
        await industrialAnalyze();
      }
      else if(type==='industrial-save'){
        if(!ctx.projectId)throw new Error('保存工业分析需要项目上下文');
        const name=prompt('工业视图名称','设备工况及维护事件');if(!name?.trim())return true;
        await ctx.req('/projects/'+U(ctx.projectId)+'/industrial/views',post({name:name.trim(),spec:industrialSpec()}));
        ctx.toast('已保存工业分析数据版本与字段映射');await loadIndustrialForm();
      }
      else if(type==='industrial-restore'){
        const saved=await ctx.req('/projects/'+U(ctx.projectId)+'/industrial/views/'+U(btn.dataset.view));
        if(saved.spec.version_id!==selectedVersion)throw new Error('该视图绑定历史版本，请先切换到对应版本');
        const form=document.getElementById('tp-industrial-form');
        if(form)for(const [key,value] of Object.entries(saved.spec))if(form.elements.namedItem(key))form.elements.namedItem(key).value=value??'';
        await industrialAnalyze(saved.spec);
      }
      else if(type==='load-virtual'){ctx.activeViewId=null;await loadVirtual();}
      else if(type==='load-sample'){ctx.activeViewId=null;await loadSample();}
      else if(type==='visualize-query'){ctx.activeViewId=null;await loadServerQuery();}
      else if(type==='preset'){
        if(!mod){if(info?.perspective_virtual_server_ready)await loadVirtual();else await loadSample();}
        await mod.setPerspectivePreset(btn.dataset.preset);
      }
      else if(type==='save-view'){
        if(!mod||!visualSource)throw new Error('请先打开网格或可视化查询');
        if(visualSource.kind==='industrial')throw new Error('工业视图请使用“保存工业分析模板”，会保留事件字段和冻结版本');
        const name=prompt('给当前视图命名');if(!name)return true;
        const workspace=await mod.savePerspectiveState();
        const base_table_name=await mod.getActiveTableName();
        await ctx.req('/native-bi/views',post({asset_id:current.asset_id,name,config:{format:'timepilot-perspective-v2',workspace,source:visualSource,base_table_name}}));
        ctx.toast('已保存 Perspective 视图及数据版本');await listViews();
      }
      else if(type==='open-view'){await openSavedView(btn.dataset.view)}
      else if(type==='cell'){
        if(!checkLatest())throw new Error('历史版本为只读；请切换当前版本');
        const row=Number(btn.dataset.row), col=btn.dataset.column;
        const old=lastResult?.rows?.find(r=>Number(r.__tp_rowid)===row)?.[col];
        selectNativeCell({row_id:row,column:col,value:old});
      }
      else if(type==='queue-cell'){
        const isNull=document.getElementById('tp-native-cell-null').checked;
        const value=isNull?null:document.getElementById('tp-native-cell-value').value;
        addEdit({row_id:Number(btn.dataset.row),column:btn.dataset.column,value});
      }
      else if(type==='clear-draft'){editor=[];preview=null;previewBatch=null;renderDraft();document.getElementById('tp-native-edit-preview').replaceChildren();document.getElementById('tp-native-commit-edit').disabled=true;}
      else if(type==='preview-edit'){
        previewBatch={asset_id:current.asset_id,expected_version:latestVersion,edits:editor.map(e=>({...e})),comment:'BI 表格批量编辑'};
        preview=await ctx.req('/native-bi/edits/preview',post(previewBatch));
        const target=document.getElementById('tp-native-edit-preview');
        target.innerHTML=`<div class="helper"><b>预览：影响 ${nice(preview.affected_rows)} 行、${nice(preview.affected_cells)} 个单元格</b>${preview.examples.map(e=>`<div>${nice(e.row_id+1)} 行 · ${H(e.column)}：${H(e.before)} → ${H(e.after)}</div>`).join('')}<p class="muted">确认后保存增量文件，原始 Parquet 不会被覆盖。</p></div>`;
        document.getElementById('tp-native-commit-edit').disabled=false;
      }
      else if(type==='commit-edit'){
        if(!preview||!previewBatch)throw new Error('请先预览本批数据');
        if(!confirm('确认保存 '+preview.affected_cells+' 个修改到新的数据版本？'))return true;
        btn.disabled=true;
        const oldWorkspace=mod?await mod.savePerspectiveState().catch(()=>null):null;
        const oldRoot=oldWorkspace?await mod.getActiveTableName().catch(()=>null):null;
        const oldSource=visualSource?structuredClone(visualSource):null;
        // Release the remote Perspective session BEFORE mutating the head
        // version. This prevents late ViewSchema calls against retired views.
        await releaseWidget();
        const saved=await ctx.req('/native-bi/edits/commit',post({batch:previewBatch,preview_token:preview.preview_token}));
        ctx.toast('已保存 '+saved.changed_cells+' 个单元格，新版本 '+saved.version_id.slice(0,8));
        await refreshCurrent({workspace:oldWorkspace,root:oldRoot,source:oldSource});
      }
      else if(type==='undo'){
        if(!confirm('撤销当前版本的上一次操作？系统会保留全部历史记录。'))return true;
        const savedState={source:visualSource?structuredClone(visualSource):{kind:'virtual'},
          workspace:mod?await mod.savePerspectiveState().catch(()=>null):null};
        savedState.root=savedState.workspace?await mod.getActiveTableName().catch(()=>null):null;
        await releaseWidget();
        await ctx.req('/native-bi/assets/'+U(current.asset_id)+'/undo',post({expected_version:latestVersion,confirmed:true}));
        await refreshCurrent(savedState);ctx.toast('已撤销，原始数据保留');
      }
      else if(type==='rollback'){
        if(!confirm('要恢复到版本 '+btn.dataset.version.slice(0,8)+' 吗？不会删除其他版本。'))return true;
        const savedState={source:visualSource?structuredClone(visualSource):{kind:'virtual'},
          workspace:mod?await mod.savePerspectiveState().catch(()=>null):null};
        savedState.root=savedState.workspace?await mod.getActiveTableName().catch(()=>null):null;
        await releaseWidget();
        await ctx.req('/native-bi/assets/'+U(current.asset_id)+'/rollback',post({expected_version:latestVersion,target_version:btn.dataset.version,confirmed:true}));
        await refreshCurrent(savedState);ctx.toast('已恢复指定历史版本');
      }
      else if(type==='apply-ai-query'){
        const el=document.getElementById('tp-native-ai-result');if(el.dataset.plan){const spec=JSON.parse(el.dataset.plan);await runQuery(spec)}
      }
    }catch(e){ctx.toast('操作失败：'+e.message)}
    return true;
  }
  function submit(event){
    const form=event.target;if(!form.id?.startsWith('tp-native-'))return false;
    event.preventDefault();
    if(form.id==='tp-native-stream-create'){
      const f=Object.fromEntries(new FormData(form));
      ctx.req('/native-bi/streams',post({name:f.name,limit:Number(f.limit)}))
        .then(r=>ctx.navigate('/bi/native/stream/'+r.stream_id)).catch(e=>ctx.toast(e.message));
    }else if(form.id==='tp-native-stream-append'){
      const f=Object.fromEntries(new FormData(form));const id=ctx.route.split('/').at(-1);
      ctx.req('/native-bi/streams/'+U(id)+'/append',post({events:[{
        event_time:new Date().toISOString(),series_id:f.series_id,value:Number(f.value),status:f.status||'ok'}]}))
        .then(r=>{const el=document.getElementById('tp-stream-status');if(el)el.textContent='发送成功，累计 '+nice(r.events_received)+' 条；图表将自动更新。';})
        .catch(e=>ctx.toast(e.message));
    }else if(form.id==='tp-native-derive-types'){
      const f=Object.fromEntries(new FormData(form));
      if(!confirm('将对整个数据版本验证类型，并创建独立的新 Parquet 数据资产。确认继续？'))return true;
      ctx.req('/native-bi/assets/'+U(current.asset_id)+'/derive-types',post({
        expected_version:selectedVersion,name:f.name||null,changes:{[f.column]:f.type}}))
        .then(r=>ctx.navigate('/bi/native/'+r.asset_id)).catch(e=>ctx.toast(e.message));
    }else if(form.id==='tp-native-upload'){
      const btn=form.querySelector('button[type="submit"]');btn.disabled=true;
      ctx.req('/native-bi/assets/upload',{method:'POST',body:new FormData(form)}).then(r=>ctx.navigate('/bi/native/'+r.asset_id)).catch(e=>ctx.toast(e.message)).finally(()=>btn.disabled=false);
    }else if(form.id==='tp-native-query'){
      ctx.activeViewId=null;runQuery(specFrom(form));
    }else if(form.id==='tp-native-paste'){
      if(!checkLatest()){ctx.toast('历史版本不可编辑');return true}
      const f=Object.fromEntries(new FormData(form));const index=current.columns.findIndex(c=>c.name===f.column);
      if(index<0){ctx.toast('未选择有效起始字段');return true}
      const start=Number(f.row)-1;const lines=String(f.tsv).replace(/\r/g,'').trimEnd().split('\n');
      const pending=[];
      for(let i=0;i<lines.length;i++){
        const values=lines[i].split('\t');
        for(let j=0;j<values.length;j++){
          if(start+i<0 || start+i>=current.row_count || index+j>=current.columns.length){
            ctx.toast('粘贴区域超出数据范围，尚未修改草稿');return true;
          }
          pending.push({row_id:start+i,column:current.columns[index+j].name,
            value:values[j]==='<NULL>'?null:values[j]});
        }
      }
      if(pending.length+editor.length>5000){ctx.toast('本批超过 5,000 个单元格，尚未修改草稿');return true}
      for(const item of pending)addEdit(item);
      const count=pending.length;
      ctx.toast('已加入 '+count+' 个修改，请预览后保存');
    }else if(form.id==='tp-native-ai'){
      const el=document.getElementById('tp-native-ai-result');el.innerHTML='<p class="muted">正在生成可审阅的查询草稿…</p>';
      ctx.req('/native-bi/assistant/query-draft',post({asset_id:current.asset_id,request:form.querySelector('[name=request]').value}))
        .then(r=>{el.dataset.plan=JSON.stringify({...r.plan,version_id:selectedVersion});el.innerHTML=`<div class="helper"><b>只读查询方案（尚未执行）</b><pre>${H(JSON.stringify(r.plan,null,2))}</pre><button class="button button-primary" data-native="apply-ai-query">确认执行</button></div>`})
        .catch(e=>el.innerHTML=alert(e.message));
    }
    return true;
  }
  window.TimePilotNativeBI={render,click,submit};
})();
