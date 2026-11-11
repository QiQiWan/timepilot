/* TimePilot BI workbench. No dynamic code/SQL execution; writes are confirmed. */
(() => {
  'use strict';
  const E = x => String(x ?? '').replace(/[&<>"']/g, s => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[s]));
  const K = x => encodeURIComponent(x);
  const jsonBody = data => ({method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
  const f = n => n===null||n===undefined?'—':Number(n).toLocaleString('zh-CN',{maximumFractionDigits:2});
  let tables=[],reports=[],sources={configured:false,tables:[],hint:'未配置数据库连接'},currentTable=null,profile=null,aggregation=null,proposal=null,workflowPreview=null,pivotState=null,recipe=[],lastContext=null;
  const goto = (ctx,path) => ctx.navigate(path);
  const options = (items,selected='') => items.map(x=>`<option value="${E(x)}" ${x===selected?'selected':''}>${E(x)}</option>`).join('');
  const names = meta => (meta?.columns || []).map(x=>x.name);
  function header(title,desc,actions=''){return `<div class="page-heading"><div><div class="eyebrow">DATA INTELLIGENCE / TIMEPILOT</div><h1>${E(title)}</h1><p>${E(desc)}</p></div><div class="bi-actions">${actions}</div></div>`}
  function biNav(route){return `<nav class="bi-tabs"><a class="${route==='/bi'?'selected':''}" href="/bi">数据资产</a><a class="${route.startsWith('/bi/report')?'selected':''}" href="/bi/reports">可视化报表</a><a href="/bi/native">原生 BI 分析</a><a href="/settings/models">AI 模型配置</a></nav>`}
  function tableCards(){return tables.length?`<div class="bi-list">${tables.map(t=>`<a class="bi-dataset-item" href="/bi/table/${E(t.table_id)}"><span class="bi-dataset-icon">▦</span><span class="bi-dataset-main"><b>${E(t.name)}</b><small>${E((t.columns||[]).length)} 个字段 · ${f(t.row_count)} 行 · ${E(t.lineage?.type||'dataset')}</small></span><span class="bi-open">打开数据表 →</span></a>`).join('')}</div>`:`<div class="empty"><b>还没有 BI 数据表</b><p>上传原始 CSV/Excel，或从现有预测数据集导入，开始分析字段和制作报表。</p></div>`}
  function sourcesPanel(){return `<div class="panel bi-section"><div class="bi-card-title"><h2>数据库数据源</h2><span class="badge ${sources.configured?'success':'review'}">${sources.configured?'已配置':'未连接'}</span></div><p>${E(sources.hint||'仅支持受控只读数据库表。')}</p>
    ${sources.tables.length?`<form id="bi-sql-form" class="form bi-formcompact"><div class="grid grid-2"><div class="field"><label>允许访问的数据表</label><select name="table_name">${options(sources.tables)}</select></div><div class="field"><label>保存为 BI 数据版本</label><input name="name" placeholder="例如：实时订单_快照" required/></div></div><button class="button" type="submit">读取并保存当前数据快照</button></form>`:'<p class="muted small">如需连接生产数据库，请管理员配置只读账号、连接地址和数据表白名单。网页不会显示数据库密码。</p>'}</div>`}
  function aiPanel(){return `<div class="panel bi-ai-panel"><div class="bi-card-title"><h2>AI 数据分析助手</h2><span class="badge review">预览与确认</span></div><p>输入目标，例如“计算利润列，再按地区汇总利润”。模型只接收表结构与行数；生成多步骤操作草稿，逐项预演成功后才允许生成数据版本。</p><form id="bi-ai-form" class="form"><div class="field"><label>你希望如何处理这张表？</label><textarea name="request" rows="4" placeholder="新增利润=销售额-成本，然后按产品类别汇总利润，并检查缺失值"></textarea></div><button class="button button-primary" type="submit">生成多步骤分析草稿</button></form><div id="bi-ai-proposal" class="bi-result-panel"></div><p class="bi-small-note">尚未配置模型服务？前往 <a class="link" href="/settings/models">系统设置 → 大模型</a>。模型无法直接修改原始数据。</p></div>`}
  async function renderHome(ctx){
    ctx.root.innerHTML=header('BI 数据分析','管理原始字段、清洗处理、关联数据并创建可复用分析报表。',`<a class="button" href="/datasets">预测数据管理</a>`)+biNav('/bi')+`
      <section class="grid grid-3 bi-stats"><div class="panel stat-card"><div class="stat-label">BI 数据表</div><div class="stat-value">${tables.length}</div><div class="stat-sub">保留原始数据和处理版本</div></div><div class="panel stat-card"><div class="stat-label">保存的分析报表</div><div class="stat-value">${reports.length}</div><div class="stat-sub">可刷新、可导出数据</div></div><div class="panel stat-card"><div class="stat-label">可复用的工具</div><div class="stat-value">8</div><div class="stat-sub">画像 / 清洗 / 计算 / 批量修改 / 关联 / 聚合 / 透视 / 报表</div></div></section>
      <div class="bi-main-grid"><div class="panel"><div class="bi-card-title"><h2>导入原始业务数据</h2><span class="subtle-tag">字段不要求预先符合预测标准</span></div><p>上传保留业务字段的完整数据表，后续可在 BI 中再做字段映射和清洗。</p>
        <form id="bi-upload-form" class="form bi-formcompact"><div class="field"><label>原始数据文件</label><input type="file" name="file" accept=".csv,.xlsx,.parquet" required /></div><div class="field"><label>数据表名称</label><input name="name" placeholder="例如：产品月度明细_2026" required /></div><button type="submit" class="button button-primary">导入 BI 数据表</button></form>
        <div class="hr"></div><h3>已有训练数据也可以复用</h3><form id="bi-from-ts-form" class="form bi-formcompact"><div class="field"><label>选择已标准化的预测数据</label><select name="dataset_id" required>${ctx.datasets.length?ctx.datasets.map(d=>`<option value="${E(d.dataset_id)}">${E(d.name)}</option>`).join(''):'<option value="">暂无可选数据集</option>'}</select></div><div class="field"><label>BI 表名称</label><input name="name" placeholder="例如：销量基线数据_分析" required /></div><button class="button" type="submit" ${ctx.datasets.length?'':'disabled'}>导入工作台</button></form></div>
        ${aiPanel()}</div>${sourcesPanel()}<div class="section-row"><h2>可管理的数据表</h2><a class="link" href="/bi/reports">查看所有报表</a></div><div class="panel bi-list-panel">${tableCards()}</div>`;
  }
  function columnsTable(){return `<div class="table-wrap"><table class="data-table"><thead><tr><th>字段</th><th>类型</th><th>缺失率</th><th>唯一值</th><th>示例</th></tr></thead><tbody>${(profile?.fields||[]).map(x=>`<tr><td class="mono">${E(x.name)}</td><td><span class="subtle-tag">${x.type==='numeric'?'数值':'文本'}</span></td><td>${f(100*x.null_rate)}%</td><td>${f(x.distinct)}</td><td class="bi-sample">${E(x.sample.join(' / '))}</td></tr>`).join('')}</tbody></table></div>`}
  function searchPreview(rows){if(!rows?.length)return `<div class="empty"><b>没有匹配的记录</b><p>调整搜索关键词或检查数据。</p></div>`;const cols=Object.keys(rows[0]);return `<div class="table-wrap"><table class="data-table"><thead><tr>${cols.map(c=>`<th>${E(c)}</th>`).join('')}</tr></thead><tbody>${rows.map(r=>`<tr>${cols.map(c=>`<td>${E(r[c])}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`}
  function stepPanel(){const n=names(currentTable);return `<div class="panel"><div class="bi-card-title"><h2>字段处理</h2><span class="subtle-tag">不修改原始数据</span></div><p>选择操作及字段，可连续添加多个步骤，再生成新的数据版本。</p>
    <form id="bi-step-form" class="form bi-formcompact"><div class="grid grid-2"><div class="field"><label>处理操作</label><select name="type"><option value="filter">筛选行</option><option value="rename">重命名字段</option><option value="drop_column">删除字段</option><option value="fill_null">填补缺失值</option><option value="drop_nulls">删除缺失行</option><option value="deduplicate">按字段去重</option><option value="cast">转换字段类型</option><option value="derive_date">提取日期部分</option></select></div><div class="field"><label>操作字段</label><select name="column">${options(n)}</select></div></div>
    <div class="grid grid-2"><div class="field"><label>操作参数</label><select name="operator"><option value="eq">等于</option><option value="ne">不等于</option><option value="gt">大于</option><option value="gte">大于等于</option><option value="lt">小于</option><option value="lte">小于等于</option><option value="contains">包含文本</option></select></div><div class="field"><label>值 / 新字段名</label><input name="value" placeholder="根据所选操作填写" /></div></div>
    <div class="grid grid-2"><div class="field"><label>类型转换</label><select name="dtype"><option value="number">数值</option><option value="date">日期</option><option value="text">文本</option></select></div><div class="field"><label>日期派生</label><select name="part"><option value="year">年份</option><option value="month">月份</option><option value="day">日期</option><option value="hour">小时</option></select></div></div><button type="submit" class="button">＋ 添加处理步骤</button></form>
    <div id="bi-recipe-list" class="recipe-list">${recipe.length?recipe.map((step,i)=>`<div><span><b>${i+1}.</b> ${E(step.type)} · ${E(step.column)}</span><button type="button" class="button button-small" data-bi="remove-step" data-index="${i}">删除</button></div>`).join(''):'<small>还没有添加操作步骤。</small>'}</div>
    <form id="bi-apply-form" class="form bi-formcompact"><div class="field"><label>处理结果名称</label><input name="name" value="${E(currentTable.name)}_已处理" required /></div><button type="submit" class="button button-primary" ${recipe.length?'':'disabled'}>生成新数据版本</button></form></div>`}
  function joinPanel(){
    const right=tables.find(t=>t.table_id!==currentTable.table_id);
    const common=right?names(currentTable).filter(x=>names(right).includes(x)):[];
    return `<div class="panel"><h2>组合 / 关联数据</h2><p>按共同字段连接两张数据表。当前默认要求右表关联键唯一，避免误合并造成行数膨胀。</p><form id="bi-join-form" class="form bi-formcompact"><div class="field"><label>右侧数据表</label><select name="right_table_id" required>${tables.filter(t=>t.table_id!==currentTable.table_id).map(t=>`<option value="${E(t.table_id)}">${E(t.name)}</option>`).join('')||'<option value="">需要至少两张表</option>'}</select></div><div class="grid grid-2"><div class="field"><label>关联字段（同名）</label><select name="key" required>${options(common)}</select></div><div class="field"><label>关联方式</label><select name="how"><option value="left">保留左表全部</option><option value="inner">仅保留匹配行</option></select></div></div><div class="field"><label>新数据名称</label><input name="name" value="${E(currentTable.name)}_关联结果" required /></div><button type="submit" class="button" ${common.length?'':'disabled'}>创建关联数据表</button></form></div>`}
  function pivotForm(){const n=names(currentTable);return `<section class="panel tp-pivot-panel"><div class="bi-card-title"><h2>透视表</h2><span class="subtle-tag">多维交叉汇总</span></div><p>选择行分组、列分组与统计指标，生成交叉表。一次最多展开 30 种列类别，避免产生难以阅读的宽表。</p>
    <form class="form bi-formcompact" id="bi-pivot-form"><div class="grid grid-2"><div class="field"><label>行分组字段</label><select name="row">${options(n)}</select></div><div class="field"><label>列分组字段</label><select name="pivot_column">${options(n.slice(1).length?n.slice(1):n)}</select></div><div class="field"><label>数值或计数字段</label><select name="column"><option value="">记录数</option>${options(n)}</select></div><div class="field"><label>统计方式</label><select name="operation"><option value="count">记录数</option><option value="sum">求和</option><option value="mean">平均</option><option value="min">最小值</option><option value="max">最大值</option><option value="nunique">去重计数</option></select></div></div><button type="submit" class="button button-primary">生成透视表</button></form>
    <div id="bi-pivot-result" class="bi-result-panel"></div><form id="bi-pivot-save" class="form bi-formcompact tp-pivot-save" hidden><div class="field"><label>报表名称</label><input name="name" value="${E(currentTable.name)}_透视分析" required/></div><button type="submit" class="button">保存透视报表</button></form></section>`}
  function pivotResult(result){const columns=result.columns||[];return `<div class="tp-pivot-meta">${f(result.total_groups)} 个行分组 · ${columns.length} 列结果</div><div class="table-wrap"><table class="data-table tp-pivot-table"><thead><tr>${columns.map(x=>`<th>${E(x)}</th>`).join('')}</tr></thead><tbody>${result.rows.map(row=>`<tr>${columns.map(c=>`<td>${E(row[c]??'—')}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`}
  function reportForm(){let n=names(currentTable);return `<div class="panel"><div class="bi-card-title"><h2>分析与可视化</h2><span class="subtle-tag">支持保存报表</span></div><p>选择分组、指标和计算方式，获得可复算的分析结果。支持柱状图、折线图、饼图与数据表。</p>
  <form id="bi-aggregate-form" class="form bi-formcompact"><div class="grid grid-2"><div class="field"><label>分组字段</label><select name="group_by"><option value="">全部数据</option>${options(n)}</select></div><div class="field"><label>统计字段</label><select name="column"><option value="">记录数</option>${options(n)}</select></div></div><div class="grid grid-2"><div class="field"><label>聚合方式</label><select name="operation"><option value="count">记录数量</option><option value="sum">总和</option><option value="mean">均值</option><option value="min">最小值</option><option value="max">最大值</option><option value="nunique">去重计数</option></select></div><div class="field"><label>图表类型</label><select name="chart_type"><option value="bar">柱状图</option><option value="line">折线图</option><option value="pie">饼图</option><option value="table">数据表</option></select></div></div><button type="submit" class="button button-primary">执行分析</button></form><div id="bi-chart-result" class="bi-result-panel"></div>
  <form id="bi-save-report-form" class="form bi-formcompact"><div class="field"><label>报表标题</label><input name="name" value="${E(currentTable.name)}_分析报表" required /></div><button class="button" type="submit">保存报表配置</button></form></div>`}
  function convertPanel(){const n=names(currentTable);const pick = (name) => n.includes(name)?name:(n[0]||'');return `<div class="panel"><h2>用于预测建模</h2><p>在 BI 完成数据清洗和字段组合后，映射为 TimePilot 的标准时序训练数据。</p>
    <form id="bi-to-ts-form" class="form bi-formcompact"><div class="field"><label>标准数据集名称</label><input name="name" value="${E(currentTable.name)}_预测" required /></div><div class="grid grid-2">
    ${[['timestamp_col','时间列','timestamp'],['target_col','预测目标列','target'],['entity_col','序列 ID','series_id'],['available_at_col','实际可用时间列','available_at']].map(x=>`<div class="field"><label>${x[1]}</label><select name="${x[0]}" required><option value="" ${n.includes(x[2])?"":"selected"}>请选择对应字段</option>${options(n,n.includes(x[2])?x[2]:"__none__")}</select></div>`).join('')}
    <div class="field"><label>频率</label><select name="frequency"><option value="MS">月度</option><option value="D">日度</option><option value="h">小时</option></select></div><div class="field"><label>单位</label><input name="unit" value="unit" /></div></div><input type="hidden" name="source_timezone" value="Asia/Shanghai"/><button type="submit" class="button">校验并转入预测数据集</button></form></div>`}
  async function renderTable(ctx,id){
    if(currentTable?.table_id!==id){recipe=[];proposal=null;workflowPreview=null;pivotState=null;aggregation=null;}
    currentTable=tables.find(t=>t.table_id===id) || await ctx.req('/bi/tables/'+K(id));
    profile=await ctx.req('/bi/tables/'+K(id)+'/profile');
    if(location.pathname!==ctx.route)return;
    const parts=ctx.route.split('/').filter(Boolean);
    const active=parts[3]||'grid';
    const sections=[['grid','数据表格'],['fields','字段画像'],['prepare','清洗与关联'],['pivot','透视表'],['reports','可视化报表'],['ai','AI 数据助手'],['forecast','转入预测']];
    const tabs=sections.map(([route,label])=>`<a class="${active===route?'selected':''}" href="/bi/table/${K(id)}${route==='grid'?'':'/'+route}">${label}</a>`).join('');
    ctx.root.innerHTML=header(currentTable.name,`${f(currentTable.row_count)} 行 × ${names(currentTable).length} 列 · 数据版本与操作记录可追溯`, `<a class="button" href="/bi">← 数据资产</a><a class="button" href="/api/v1/bi/tables/${K(id)}/export.csv" download>导出 CSV</a><a class="button" href="/api/v1/bi/tables/${K(id)}/export.xlsx" download>导出 Excel</a>`)+
      `<nav class="bi-tabs tp-worktabs" aria-label="BI 数据表视图">${tabs}</nav>`+
      (active==='grid'?'<div id="tp-grid-mount"><p>正在加载数据表格…</p></div>':
       active==='fields'?`<section class="panel"><div class="bi-card-title"><h2>字段画像与数据质量</h2><span class="subtle-tag">${f(profile.missing_cells)} 个缺失单元格</span></div><p>这里展示各字段的数据类型、缺失率、唯一值和样例。要直接查看原始行列或修改字段，请进入“数据表格”。</p>${columnsTable()}</section>`:
       active==='prepare'?`<section id="bi-prepare" class="bi-main-grid">${stepPanel()}${joinPanel()}</section>`:
       active==='reports'?`<section id="bi-analysis">${reportForm()}</section>`:
       active==='pivot'?pivotForm():
       active==='ai'?`<div class="tp-inline-ai"><div class="tp-data-hint"><strong>当前数据：${E(currentTable.name)}</strong><span>请在分析需求中明确当前数据表及需要操作的字段，模型将仅基于表结构生成候选方案，执行前需要确认。</span></div>${aiPanel()}</div>`:
       active==='forecast'?`<section id="bi-train">${convertPanel()}</section>`:
       `<div class="helper warning">未找到该数据视图。<a href="/bi/table/${K(id)}">返回数据表格</a></div>`);
    if(active==='grid')await window.TimePilotGrid.render(ctx,currentTable,profile,{get:()=>recipe,set:items=>{recipe=items}});
  }
  function chartSvg(result,type){
    if(type==='table'||!result.group_by.length)return '<p class="muted">汇总结果如下。</p>';
    const rows=result.rows.slice(0,20), key=result.group_by[0],val=result.value_field,raw=rows.map(r=>Number(r[val])||0);
    if(!rows.length)return '<p class="muted">没有可展示的数据。</p>';
    const W=760,H=252,left=56,right=16,top=20,bottom=62,w=W-left-right,h=H-top-bottom,min=Math.min(0,...raw),max=Math.max(1,...raw),range=max-min||1;
    if(type==='pie'){
      const vals=raw.map(v=>Math.max(0,v)),sum=vals.reduce((a,b)=>a+b,0);if(sum<=0)return '<p>饼图要求汇总值为非负且总和大于 0。</p>';
      const colors=['#2563eb','#14b8a6','#f59e0b','#8b5cf6','#ef4444','#64748b','#06b6d4'];let start=0;
      const circles=vals.map((v,i)=>{const a=v/sum*360;const r=73,cx=180,cy=122,from=start*Math.PI/180,to=(start+a)*Math.PI/180;start+=a;const x1=cx+r*Math.cos(from),y1=cy+r*Math.sin(from),x2=cx+r*Math.cos(to),y2=cy+r*Math.sin(to);return `<path d="M ${cx} ${cy} L ${x1} ${y1} A ${r} ${r} 0 ${a>180?1:0} 1 ${x2} ${y2} Z" fill="${colors[i%colors.length]}"><title>${E(rows[i][key])}: ${f(v)}</title></path>`}).join('');
      return `<svg class="bi-chart" viewBox="0 0 760 254" role="img" aria-label="分组占比饼图">${circles}${rows.slice(0,9).map((r,i)=>`<circle cx="340" cy="${35+i*22}" r="5" fill="${colors[i%colors.length]}"/><text x="356" y="${39+i*22}" font-size="13" fill="#51647d">${E(String(r[key]).slice(0,25))} · ${f(raw[i])}</text>`).join('')}</svg>`;
    }
    const x=i=>left+(i+.5)*(w/rows.length);const y=v=>top+h-(v-min)/range*h;
    const guides=[0,.5,1].map(t=>`<line x1="${left}" y1="${top+h-h*t}" x2="${W-right}" y2="${top+h-h*t}" stroke="#e2e8f0"/><text x="${left-7}" y="${top+h-h*t+4}" text-anchor="end" fill="#718199" font-size="11">${f(min+range*t)}</text>`).join('');
    const labels=rows.map((r,i)=>`<text x="${x(i)}" y="${H-42}" text-anchor="end" transform="rotate(-35,${x(i)},${H-42})" font-size="11" fill="#64748b">${E(String(r[key]??'空值').slice(0,9))}</text>`).join('');
    const line=rows.map((r,i)=>`${x(i)},${y(raw[i])}`).join(' ');
    const series=type==='line'?`<polyline points="${line}" fill="none" stroke="#2563eb" stroke-width="2.5"/>${rows.map((r,i)=>`<circle cx="${x(i)}" cy="${y(raw[i])}" r="4" fill="#2563eb"><title>${E(r[key])}: ${f(raw[i])}</title></circle>`).join('')}`:
      rows.map((r,i)=>`<rect x="${x(i)-(w/rows.length)*.32}" y="${y(Math.max(0,raw[i]))}" width="${(w/rows.length)*.64}" height="${Math.max(1,Math.abs(y(raw[i])-y(0)))}" rx="3" fill="#3b82f6"><title>${E(r[key])}: ${f(raw[i])}</title></rect>`).join('');
    return `<svg class="bi-chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="分组指标可视化">${guides}${series}${labels}</svg>`;
  }
  function aggregateResult(result,chartType){const rows=result.rows;return `<div class="bi-chart-wrap">${chartSvg(result,chartType)}</div><div class="table-wrap"><table class="data-table"><thead><tr>${(rows[0]?Object.keys(rows[0]):[]).map(c=>`<th>${E(c)}</th>`).join('')}</tr></thead><tbody>${rows.map(row=>`<tr>${Object.values(row).map(value=>`<td>${E(value)}</td>`).join('')}</tr>`).join('')}</tbody></table></div><small>总分组数：${f(result.total_groups)}。前端最多显示 500 条汇总结果。</small>`}
  async function renderReports(ctx){ctx.root.innerHTML=header('分析报表','保存常用图表配置并从当前数据快照重新计算。','<a class="button" href="/bi">← 返回 BI 工作台</a>')+biNav('/bi/reports')+`<div class="panel bi-list-panel">${reports.length?`<div class="bi-list">${reports.map(r=>`<a class="bi-dataset-item" href="/bi/report/${E(r.report_id)}"><span class="bi-dataset-icon">▤</span><span class="bi-dataset-main"><b>${E(r.name)}</b><small>${E(r.spec.chart_type==='pivot'?'透视表':r.spec.chart_type)} · ${E(r.spec.chart_type==='pivot'?(r.spec.pivot?.metric?.operation||'count'):r.spec.aggregate.metric.operation)} · ${E(r.table_id.slice(0,8))}</small></span><span class="bi-open">查看报表 →</span></a>`).join('')}</div>`:'<div class="empty"><b>尚无已保存报表</b><p>打开任意 BI 表，配置分组、统计方式和图表后保存。</p><a class="button button-primary" href="/bi">前往数据资产</a></div>'}</div>`}
  async function renderReport(ctx,id){const r=await ctx.req('/bi/reports/'+K(id));ctx.root.innerHTML=header(r.name,'已保存的分析定义，重新打开时根据数据快照计算。','<a class="button" href="/bi/reports">← 报表列表</a><a class="button" href="/api/v1/bi/reports/${K(id)}/export.xlsx" download>导出 Excel</a>')+biNav('/bi/reports')+`<div class="panel"><div class="bi-card-title"><h2>${E(r.name)}</h2><a class="link" href="/bi/table/${E(r.table_id)}">查看来源数据</a></div>${r.spec.chart_type==='pivot'?pivotResult(r.result):aggregateResult(r.result,r.spec.chart_type)}</div>`}
  async function render(ctx){lastContext=ctx;ctx.root.innerHTML='<div class="panel"><p>正在读取 BI 数据工作台…</p></div>';
    try{[tables,reports]=await Promise.all([ctx.req('/bi/tables'),ctx.req('/bi/reports')]);sources=await ctx.req('/bi/sources').catch(err=>({configured:false,tables:[],hint:'数据源检查失败：'+err.message}));const parts=ctx.route.split('/').filter(Boolean);
      if(location.pathname!==ctx.route)return;
      if(parts.length>=3&&parts[1]==='table')await renderTable(ctx,parts[2]);
      else if(parts.length===3&&parts[1]==='report')await renderReport(ctx,parts[2]);
      else if(parts[1]==='reports')await renderReports(ctx);
      else await renderHome(ctx);
    }catch(err){ctx.root.innerHTML=header('BI 数据工作台','数据读取失败')+`<div class="helper danger">${E(err.message)}</div>`}
  }
  async function execute(ctx,url,data,after){try{const r=await ctx.req(url,jsonBody(data));ctx.toast('操作已完成，新数据版本已保存');if(after)after(r);else await render({...ctx,route:location.pathname});}catch(err){ctx.toast('操作失败：'+err.message)}}
  function handleSubmit(e,ctx){const id=e.target?.id;if(!id||!id.startsWith('bi-'))return false;e.preventDefault();const d=Object.fromEntries(new FormData(e.target));
    if(id==='bi-upload-form'){
      const payload=new FormData(e.target);const file=payload.get('file');if(!file||!file.size){ctx.toast('请选择要导入的数据文件');return true}
      e.target.querySelector('button[type=submit]').disabled=true;
      ctx.req('/bi/tables/upload',{method:'POST',body:payload}).then(r=>goto(ctx,'/bi/table/'+r.table_id)).catch(err=>ctx.toast('导入失败：'+err.message)).finally(()=>{const b=e.target.querySelector('button[type=submit]');if(b)b.disabled=false});return true;
    }
    if(id==='bi-from-ts-form'){execute(ctx,'/bi/tables/import-timeseries',d,r=>goto(ctx,'/bi/table/'+r.table_id));return true}
    if(id==='bi-sql-form'){execute(ctx,'/bi/sources/snapshot',d,r=>goto(ctx,'/bi/table/'+r.table_id));return true}
    if(id==='bi-preview-form'){ctx.req('/bi/tables/'+K(currentTable.table_id)+'/preview?'+new URLSearchParams({search:d.search,page_size:30})).then(r=>document.getElementById('bi-preview-result').innerHTML=searchPreview(r.rows)).catch(err=>ctx.toast(err.message));return true}
    if(id==='bi-step-form'){
      const step={type:d.type,column:d.column};if(d.type==='rename'||d.type==='derive_date')step.new_name=d.value;
      if(d.type==='filter'){step.operator=d.operator;step.value=d.value}
      if(d.type==='fill_null')step.value=d.value;
      if(d.type==='cast')step.dtype=d.dtype;
      if(d.type==='derive_date')step.part=d.part;
      recipe.push(step);document.querySelector('#bi-prepare').innerHTML=stepPanel()+joinPanel();ctx.toast('已加入处理配方，尚未修改数据');return true;
    }
    if(id==='bi-apply-form'){if(!recipe.length){ctx.toast('请先添加清洗步骤');return true}if(!confirm('确认基于当前快照生成新的数据表？原始数据不会修改。'))return true;
      execute(ctx,'/bi/tables/'+K(currentTable.table_id)+'/transform',{name:d.name,steps:recipe},r=>{recipe=[];goto(ctx,'/bi/table/'+r.table_id)});return true}
    if(id==='bi-join-form'){if(!d.right_table_id){ctx.toast('请选择右侧数据表');return true}if(!confirm('关联操作将生成新的数据表，是否继续？'))return true;
      execute(ctx,'/bi/tables/join',{name:d.name,left_table_id:currentTable.table_id,right_table_id:d.right_table_id,keys:[d.key],how:d.how},r=>goto(ctx,'/bi/table/'+r.table_id));return true}
    if(id==='bi-pivot-form'){
      const spec={group_by:[d.row],pivot_column:d.pivot_column,metric:{operation:d.operation,column:d.operation==='count'?null:d.column}};
      const slot=document.getElementById('bi-pivot-result');slot.innerHTML='<p class="muted">正在生成透视表…</p>';
      ctx.req('/bi/tables/'+K(currentTable.table_id)+'/pivot',jsonBody(spec)).then(r=>{pivotState=spec;slot.innerHTML=pivotResult(r);document.getElementById('bi-pivot-save').hidden=false}).catch(err=>{pivotState=null;slot.innerHTML=`<div class="helper danger">${E(err.message)}</div>`;document.getElementById('bi-pivot-save').hidden=true});return true;
    }
    if(id==='bi-pivot-save'){if(!pivotState){ctx.toast('请先生成透视表');return true}execute(ctx,'/bi/reports',{name:d.name,table_id:currentTable.table_id,chart_type:'pivot',pivot:pivotState},r=>goto(ctx,'/bi/report/'+r.report_id));return true}
    if(id==='bi-aggregate-form'){const metric={operation:d.operation,column:d.column||null};const spec={group_by:d.group_by?[d.group_by]:[],metric,limit:100,sort_by:d.chart_type==='line'?'group':'metric',order:d.chart_type==='line'?'asc':'desc'};
      aggregation={table_id:currentTable.table_id,aggregate:spec,chart_type:d.chart_type};ctx.req('/bi/tables/'+K(currentTable.table_id)+'/aggregate',jsonBody(spec)).then(r=>{document.getElementById('bi-chart-result').innerHTML=aggregateResult(r,d.chart_type);document.getElementById('bi-chart-result').scrollIntoView({behavior:'smooth',block:'nearest'})}).catch(err=>ctx.toast('分析失败：'+err.message));return true}
    if(id==='bi-save-report-form'){if(!aggregation){ctx.toast('请先执行一次分组分析');return true}execute(ctx,'/bi/reports',{name:d.name,...aggregation},r=>goto(ctx,'/bi/report/'+r.report_id));return true}
    if(id==='bi-to-ts-form'){const req={...d};execute(ctx,'/bi/tables/'+K(currentTable.table_id)+'/to-timeseries',req,()=>goto(ctx,'/new'));return true}
    if(id==='bi-ai-form'){
      const field=document.getElementById('bi-ai-proposal');field.innerHTML='<p class="muted">正在生成结构化分析步骤…</p>';
      proposal=null;workflowPreview=null;
      if(!currentTable?.table_id){field.innerHTML='<div class="helper warning">请先打开一张数据表，再使用多步骤智能分析。</div>';return true}
      ctx.req('/assistant/bi/workflow',jsonBody({table_id:currentTable.table_id,request:d.request})).then(result=>{
        proposal=result.plan;field.innerHTML=`<div class="tp-plan-review"><h3>分析草稿 · ${E(proposal.name)}</h3><ol>${proposal.steps.map(s=>`<li><b>${E(s.tool)}</b><pre>${E(JSON.stringify(s.arguments,null,2))}</pre></li>`).join('')}</ol><p class="muted">生成计划未执行数据操作；先检查步骤并预览影响。</p><button class="button button-primary" type="button" data-bi="preview-workflow">预览全部步骤</button><div id="bi-workflow-preview"></div></div>`;
      }).catch(err=>{field.innerHTML=`<div class="helper warning">${E(err.message)} <a href="/settings/models" class="link">检查模型设置</a></div>`});return true;
    }
    return false;
  }
  function handleClick(e,ctx){const button=e.target.closest('[data-bi]');if(!button)return false;const action=button.dataset.bi;
    if(action==='remove-step'){recipe.splice(Number(button.dataset.index),1);document.querySelector('#bi-prepare').innerHTML=stepPanel()+joinPanel();return true}
    if(action==='preview-workflow'){
      if(!proposal || !currentTable)return true;
      const slot=document.getElementById('bi-workflow-preview');slot.innerHTML='<p class="muted">正在预演所有步骤…</p>';workflowPreview=null;button.disabled=true;
      ctx.req('/bi/tables/'+K(currentTable.table_id)+'/workflow/preview',jsonBody(proposal)).then(r=>{
        workflowPreview=r;slot.innerHTML=`<div class="tp-plan-result"><h4>预演通过 · 没有修改原表</h4><p>行数 ${r.before.rows} → ${r.after.rows}；字段 ${r.before.columns} → ${r.after.columns}；缺失单元格 ${r.before.missing_cells} → ${r.after.missing_cells}</p><ol>${r.steps.map(x=>`<li><b>${E(x.tool)}</b> ${x.result.total_groups?' · '+x.result.total_groups+' 个分组':x.result.columns?' · '+x.result.columns+' 列':''}${x.result.rows?.length?searchPreview(x.result.rows.slice(0,12)):''}</li>`).join('')}</ol>${r.preview?.length?searchPreview(r.preview.slice(0,8)):''}${r.writes_data?'<button class="button button-primary" type="button" data-bi="save-workflow">确认并生成新数据版本</button>':'<p>当前方案只有读取和统计分析，无需保存数据版本。</p>'}</div>`;
      }).catch(err=>{slot.innerHTML=`<div class="helper danger">预演失败，未修改数据：${E(err.message)}</div>`}).finally(()=>button.disabled=false);return true;
    }
    if(action==='save-workflow'){
      if(!proposal || !workflowPreview?.writes_data || !currentTable)return true;
      if(!confirm('已经查看处理结果，确认生成新的数据快照？原始数据不会修改。'))return true;
      button.disabled=true;ctx.req('/bi/tables/'+K(currentTable.table_id)+'/workflow/execute?confirmed=true&preview_token='+K(workflowPreview.preview_token),jsonBody(proposal)).then(result=>{
        proposal=null;workflowPreview=null;ctx.toast('已保存新数据版本');goto(ctx,'/bi/table/'+result.saved_table.table_id);
      }).catch(err=>ctx.toast('保存失败：'+err.message)).finally(()=>button.disabled=false);return true;
    }
    return false;
  }
  function handleChange(e){
    if(e.target?.name!=='right_table_id' || e.target.closest('form')?.id!=='bi-join-form')return false;
    const right=tables.find(t=>t.table_id===e.target.value);
    const common=right?names(currentTable).filter(key=>names(right).includes(key)):[];
    const form=e.target.closest('form'), select=form.elements.key;
    select.innerHTML=options(common);
    form.querySelector('button[type=submit]').disabled=!common.length;
    return true;
  }
  window.TimePilotBI={render,handleSubmit,handleClick,handleChange};
})();
