/* TimePilot zero-build local workbench with history routes, BI and model settings. */
(() => {
  'use strict';
  const API = '/api/v1';
  const root = document.getElementById('app');
  const titles = {projects:'项目管理',overview:'工作台概览',new:'新建预测任务',datasets:'数据集管理',imports:'智能数据导入','data-assets':'统一数据资产',agent:'Agent 任务中心',runs:'运行记录',plugins:'模型与插件',bi:'BI 数据分析',settings:'系统设置',help:'使用指南'};
  let generalSettings = {ui_theme:'light', default_budget_seconds:180, assistant_mode:'suggest'};
  let datasets = [], runs = [], plugins = [], floatCollapsed = false, toastTimer, lastRoute = '', apiError = null, apiUnavailable = false;
  const E = value => String(value ?? '').replace(/[&<>"']/g, s => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[s]));
  const dateText = v => {if(!v)return '—'; const d = new Date(v); return isNaN(d) ? E(v) : d.toLocaleString('zh-CN',{hour12:false})};
  const fmt = (v, digits=2) => v == null || Number.isNaN(Number(v)) ? '—' : Number(v).toLocaleString('zh-CN',{maximumFractionDigits:digits});
  const percent = v => v == null ? '—' : `${(Number(v)*100).toFixed(2)}%`;
  const stageMap={queued:'等待执行',starting:'准备运行',validate:'校验数据与任务',backtest:'训练与滚动回测',forecast:'独立测试与未来预测',classification:'分类训练与前推验证',quality:'生成质量报告',completed:'已完成',failed:'执行失败',cancelled:'已取消'};
  const stageText=s=>stageMap[s]||s||'—';
  const statusMap={queued:['等待执行','running'],running:['执行中','running'],succeeded:['已完成','success'],failed:['执行失败','failed'],cancel_requested:['取消请求中','review'],cancelled:['已取消','cancelled']};
  const statusBadge = s => {const [name,type]=statusMap[s]||[s,'']; return `<span class="badge ${type}">${E(name)}</span>`};
  function toast(msg) {const box=document.getElementById('toast');box.textContent=msg;box.classList.add('show');clearTimeout(toastTimer);toastTimer=setTimeout(()=>box.classList.remove('show'),4200)}
  async function req(url, opts={}) {const result=await fetch(API+url, opts); let data;try{data=await result.json()}catch{data=null}if(!result.ok){let detail=data?.detail||`HTTP ${result.status}`;if(Array.isArray(detail))detail=detail.map(x=>x.message||JSON.stringify(x)).join('；');
    else if(detail&&typeof detail==='object')detail=detail.message||detail.code||JSON.stringify(detail);
    throw new Error(String(detail))}return data}
  async function refresh(){
    const result=await Promise.allSettled([window.TIMEPILOT_ENTERPRISE?Promise.resolve([]):req('/datasets'),window.TIMEPILOT_ENTERPRISE?Promise.resolve([]):req('/runs'),req('/plugins'),window.TIMEPILOT_ENTERPRISE?Promise.resolve(generalSettings):req('/settings/general')]);
    if(result[0].status==='fulfilled')datasets=result[0].value;
    if(result[1].status==='fulfilled')runs=result[1].value;
    if(result[2].status==='fulfilled')plugins=result[2].value;
    if(result[3].status==='fulfilled')generalSettings=result[3].value;
    document.documentElement.classList.toggle('theme-dark',generalSettings.ui_theme==='dark');
    apiUnavailable=result.slice(0,3).every(x=>x.status==='rejected');
    const failures=result.slice(0,3).filter(x=>x.status==='rejected');
    apiError=failures.length ? failures.map(x=>x.reason?.message||String(x.reason)).join('；') : null;
    const badge=document.getElementById('connection-badge');
    if(badge){badge.classList.toggle('offline',Boolean(apiError));badge.innerHTML=`<span class="pulse"></span> ${apiError?'连接异常':(window.TIMEPILOT_ENTERPRISE?'企业工作空间':'本地开发环境')}`}
  }
  function errorNotice(){
    return apiError?`<div class="service-notice" role="alert"><div><strong>部分服务暂时无法访问</strong><p>${E(apiError)}。当前数据可能不完整，请检查后台运行状态后重新连接。</p></div><button class="button" type="button" data-action="refresh">重新连接</button></div>`:'';
  }
  function page(){return location.pathname.split('/').filter(Boolean)[0]||'projects'}
  function navigate(path){if(path===location.pathname)return render();history.pushState({},'',path);return render()}
  function header(h,sub,action=''){return `<div class="page-heading"><div><h1>${h}</h1><p>${sub}</p></div>${action}</div>`}
  function cardEmpty(text,desc,button=''){return `<div class="empty"><div class="empty-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18M9 3v18"/></svg></div><b>${text}</b><p>${desc}</p>${button}</div>`}
  function tableEmpty(){return '<tr><td colspan="5" class="muted" style="text-align:center;padding:30px">暂无记录</td></tr>'}
  function recentTable(list){return `<div class="table-wrap"><table class="data-table"><thead><tr><th>任务编号</th><th>当前阶段</th><th>运行状态</th><th>提交时间</th><th>操作</th></tr></thead><tbody>${list.length ? list.map(x=>`<tr><td class="mono">${E(x.task_id)}</td><td>${E(stageText(x.stage||'queued'))}</td><td>${statusBadge(x.status)}</td><td>${dateText(x.created_at)}</td><td><a class="link" href="/runs/${E(x.run_id)}">查看详情</a></td></tr>`).join(''):tableEmpty()}</tbody></table></div>`}
  function modelDescription(name, fallback){
    return {seasonal_naive:'沿用上一个季节对应时期的数值，适合作为季节性预测基线。',
      moving_average:'根据最近的历史观测求平均，适合建立简洁的对照模型。',
      last_value:'使用最近一次有效观测值预测未来，适合快速基线测试。',
      autogluon:'自动比较多种预测模型并生成候选方案；需要单独安装依赖。',autogluon_timeseries:'自动比较多种时序预测模型；需要安装 AutoGluon 扩展。'}[name] || fallback;
  }
  function qualityLabel(s){return {review_required:'需要人工复核',passed:'已通过',pass:'已通过',failed:'未通过',insufficient_evidence:'证据不足'}[s]||s;}
  function issueText(issue){
    if(issue.code==='TQE-SMALL-TEST')return '独立测试样本少于 12 个预测点，当前误差只能作为初步参考，建议扩大测试范围后再评审。';
    return issue.message||'';
  }
  function eventText(e){
    const message=String(e.message||'');
    if(message.startsWith('Validation fold'))return '滚动回测窗口运行';
    if(message.startsWith('Selected model '))return '选定模型：'+modelLabel(message.slice(15));
    if(message.startsWith('Training, independent test')||message.startsWith('Training, Independent test'))return '训练、独立测试和质量报告已完成';
    if(['quality','forecast','backtest','validate'].includes(message))return stageText(message);
    return message;
  }
  function modelLabel(name){
    return {seasonal_naive:'季节性基线',moving_average:'移动平均',last_value:'最近值预测',autogluon:'AutoGluon 自动建模',autogluon_timeseries:'AutoGluon 时序自动建模'}[name]||name;
  }
  function newPage(draft=null){
    const q=new URLSearchParams(location.search);
    const selectedDataset=datasets.find(x=>x.dataset_id===q.get('dataset'))||datasets[0];
    const spec=draft?.task_spec||{};
    const selectedModels=spec.model_candidates||['seasonal_naive','moving_average'];
    const missingModels=draft?selectedModels.filter(name=>!plugins.some(x=>x.plugin_id===name&&x.available)):[];
    const horizon=Number(spec.forecast_horizon||q.get('horizon')||3);
    const horizons=[...new Set([1,2,3,6,12,24,horizon])].sort((a,b)=>a-b);
    const source=draft?`<div class="helper success">保存视图 ${E(draft.view_id.slice(0,8))} · 资产版本 ${E(draft.version_id.slice(0,12))} · 预测草稿 ${E(draft.draft_id.slice(0,8))}。数据及建模参数已经过预检查。</div>`:'';
    return header('新建预测任务','核对数据、预测跨度和模型后提交运行。')+`
      <form id="new-task-form" class="form">
      <div class="panel"><h2>01 · 预测数据</h2><label class="field"><span>数据集</span><select id="task-dataset" name="dataset" ${draft?'disabled':''} required>${datasets.length?datasets.map(d=>`<option value="${E(d.dataset_id)}" ${selectedDataset?.dataset_id===d.dataset_id?'selected':''}>${E(d.name)} · ${E(d.series_count)} 条序列 · ${E(d.frequency)}</option>`).join(''):'<option value="">没有可用数据集</option>'}</select>${draft?`<input type="hidden" name="dataset" value="${E(draft.dataset_id)}">`:''}</label><div id="task-dataset-info" class="segment-summary">${selectedDataset?`${E(selectedDataset.name)} · ${fmt(selectedDataset.record_count,0)} 条记录 · ${fmt(selectedDataset.series_count,0)} 条序列`:'请先在项目中生成标准数据资产'}</div></div>
      <div class="panel"><h2>02 · 预测任务</h2>${source}<p class="muted small">${draft?'如需修改预测跨度、回测窗口或候选模型，请返回 BI 创建新的预测草稿。':'手动任务需要独立确认数据口径和回测条件。'}</p><div class="grid grid-2">
      <label class="field"><span>任务名称</span><input name="name" required value="${E(spec.name||q.get('name')||'月度业务预测')}"></label>
      <label class="field"><span>任务编号</span><input name="task_id" required value="${E(spec.task_id||'TP-'+Date.now().toString().slice(-7))}"></label>
      <label class="field"><span>预测类型</span><select name="task_family"><option value="demand_forecast" ${spec.task_family==='demand_forecast'?'selected':''}>销量 / 需求预测</option><option value="equipment_state" ${spec.task_family==='equipment_state'?'selected':''}>设备连续状态预测</option></select></label>
      <label class="field"><span>未来预测跨度（个周期）</span><select name="horizon" ${draft?'disabled':''}>${horizons.map(n=>`<option value="${n}" ${n===horizon?'selected':''}>${n}</option>`).join('')}</select>${draft?`<input type="hidden" name="horizon" value="${horizon}">`:''}</label></div></div>
      <div class="panel"><h2>03 · 建模算法</h2>${missingModels.length?`<div class="helper danger">草稿所需插件不可用：${E(missingModels.join('、'))}，请先检查环境。</div>`:''}<div class="options-grid">${plugins.filter(p=>p.available&&(!draft||selectedModels.includes(p.plugin_id))).map(p=>`<label class="option"><input type="checkbox" name="model" value="${E(p.plugin_id)}" ${selectedModels.includes(p.plugin_id)?'checked':''} ${draft?'disabled':''}>${draft?`<input type="hidden" name="model" value="${E(p.plugin_id)}">`:''}<span><b>${E(modelLabel(p.plugin_id))}</b><small>${E(modelDescription(p.plugin_id,p.description))}</small></span></label>`).join('')}</div>
      <details style="margin-top:16px"><summary>回测与预算设置</summary><div class="grid grid-2" style="margin-top:16px"><label class="field"><span>滚动验证窗口数量</span><input type="number" name="windows" min="1" max="24" value="${E(spec.evaluation?.validation_windows||3)}" ${draft?'readonly':''}></label><label class="field"><span>最短历史长度</span><input type="number" name="min_history" min="2" value="${E(spec.evaluation?.min_history||12)}" ${draft?'readonly':''}></label><label class="field"><span>训练时间预算（秒）</span><input type="number" name="budget" min="10" value="${Number(generalSettings.default_budget_seconds)||180}"></label></div></details>
      <div class="action-row"><button class="button button-primary" type="submit" ${datasets.length&&!missingModels.length?'':'disabled'}>确认并提交模型运行</button><a href="/projects" class="button">返回项目</a></div></div>
      </form>`;
  }
  function runsPage(){return header('运行记录','后台训练与页面独立运行。点击任意任务查看当前状态、质量指标及预测结果。')+`<div class="panel">${runs.length?recentTable(runs):cardEmpty('还没有运行任务','创建预测任务后，运行记录会自动显示在这里。','<a href="/new" class="button button-primary">创建新任务</a>')}</div>`}
  function plotForecast(rows){
    if(!rows.length)return '<p class="muted small">没有未来预测结果。</p>';
    const bars=rows.slice(0,20), max=Math.max(...bars.map(r=>Number(r.mean)||0),1), w=820, h=280;
    const step=720/bars.length, nodes=bars.map((r,i)=>{const val=Math.max(0,Number(r.mean)||0), height=190*val/max, x=65+i*step+step*.16,y=227-height;return `<rect x="${x}" y="${y}" width="${step*.68}" height="${height}" rx="4" fill="#3984db"/><text x="${x+step*.34}" y="${y-9}" font-size="12" fill="#425c78" text-anchor="middle">${fmt(r.mean,0)}</text><text x="${x+step*.34}" y="250" font-size="11" fill="#8091a5" text-anchor="middle">${E(r.series_id).slice(0,15)}</text>`}).join('');
    return `<div class="chart-shell"><svg class="forecast-chart" viewBox="0 0 ${w} ${h}" role="img" aria-label="未来预测值柱状图"><line x1="50" y1="227" x2="800" y2="227" stroke="#c5d3e3"/><line x1="50" y1="38" x2="50" y2="227" stroke="#c5d3e3"/>${nodes}</svg><small class="muted">展示最多 20 条预测记录，完整结果请下载 CSV。预测结果为模型估计值，需结合质量报告使用。</small></div>`
  }
  function runDetail(status, quality, forecast, events){
    const completed=status.status==='succeeded';const failed=status.status==='failed';const q=quality;
    return header('任务运行详情',`运行编号：${E(status.run_id)}`,`<a href="/runs" class="button">返回运行列表</a>`)+`
    <div class="grid grid-3"><div class="panel stat-card"><div class="stat-label">任务状态</div><div style="margin:16px 0">${statusBadge(status.status)}</div><div class="stat-sub">当前阶段：${E(stageText(status.stage))}</div></div>
    <div class="panel stat-card"><div class="stat-label">整体运行进度</div><div class="stat-value">${status.progress}%</div><div class="progress"><div style="width:${status.progress}%"></div></div></div>
    <div class="panel stat-card"><div class="stat-label">执行尝试次数</div><div class="stat-value">${status.attempts}</div><div class="stat-sub">中断后可复用已验证检查点</div></div></div>
    <div class="section-row"><h2>本次运行</h2><div class="action-row" style="margin:0">${failed?`<button class="button" data-action="retry" data-id="${E(status.run_id)}">↻ 使用检查点重试</button>`:''}${status.status==='queued'||status.status==='running'?`<button class="button" data-action="cancel" data-id="${E(status.run_id)}">取消运行</button>`:''}</div></div>
    ${failed?`<div class="helper danger"><strong>运行中断：${E(status.error_code)}</strong><br/>${E(status.error_message)}<br/>已完成的阶段仍保存在运行目录中。</div>`:''}
    ${q?.task_kind==='classification'?`<div class="panel"><h2>二分类独立测试</h2><p>阈值只依据前期验证集，最终测试不参与阈值选择。</p><div class="grid grid-4"><div class="quality-stat"><span>召回率</span><b>${percent(q.metrics?.recall)}</b></div><div class="quality-stat"><span>精确率</span><b>${percent(q.metrics?.precision)}</b></div><div class="quality-stat"><span>误剔率（假阳性）</span><b>${percent(q.metrics?.false_positive_rate)}</b></div><div class="quality-stat"><span>正误判数（TP/FP/FN/TN）</span><b>${[q.metrics?.tp,q.metrics?.fp,q.metrics?.fn,q.metrics?.tn].join(' / ')}</b></div></div></div>`:''}
    ${q?.task_kind!=='classification'&&q?`<div class="panel"><div class="card-header"><div><h2>独立留出测试 · 质量报告</h2><p>最终测试数据未参与候选模型选择。质量状态：${E(qualityLabel(q.status))}</p></div><span class="badge review">${E(qualityLabel(q.status))}</span></div>
      <div class="grid grid-4"><div class="quality-stat"><span>WAPE · 加权绝对误差</span><b>${percent(q.metrics.WAPE)}</b><small>不等同于预测正确概率</small></div>
      <div class="quality-stat"><span>MAE · 平均绝对误差</span><b>${fmt(q.metrics.MAE)}</b><small>与预测目标相同单位</small></div><div class="quality-stat"><span>RMSE</span><b>${fmt(q.metrics.RMSE)}</b><small>更强调较大的误差</small></div>
      <div class="quality-stat"><span>Bias · 方向性偏差</span><b>${percent(q.metrics.Bias)}</b><small>负值表示总体偏低</small></div></div>
      ${(q.issues||[]).map(x=>`<div class="issue"><strong>${E(x.code)} · ${E(x.severity)}</strong>${E(issueText(x))}</div>`).join('')}</div>`:''}
    ${forecast?`<div class="section-row"><h2>未来预测</h2><a class="button button-small" href="${API}/runs/${E(status.run_id)}/forecasts.csv">↓ 下载 CSV</a></div><div class="panel">${plotForecast(forecast.future||[])}</div>`:''}
    <div class="section-row"><h2>运行日志</h2><span class="muted tiny">共 ${events.length} 条记录</span></div>
    <div class="panel"><div class="timeline">${events.length?events.slice().reverse().slice(0,6).map(e=>`<div class="timeline-row"><span class="timeline-dot"></span><div><b>${E(eventText(e))}</b><br/><small>${dateText(e.timestamp)} · ${E(e.event)}</small></div></div>`).join(''):'<p class="muted">暂无日志</p>'}</div>
    ${events.length>6?`<details class="log-archive"><summary>查看更早的 ${events.length-6} 条运行日志</summary><div class="timeline">${events.slice().reverse().slice(6).map(e=>`<div class="timeline-row"><span class="timeline-dot"></span><div><b>${E(eventText(e))}</b><br/><small>${dateText(e.timestamp)} · ${E(e.event)}</small></div></div>`).join('')}</div></details>`:''}</div>`}
  function pluginsPage(){return header('模型与插件','所有候选模型通过统一接口声明能力和可用性；各算法通过统一插件接口提供能力。')+`<div class="grid grid-2">${plugins.map(p=>`<div class="panel"><div class="card-header"><h2>${E(modelLabel(p.plugin_id))}</h2>${p.available?'<span class="badge success">已就绪</span>':'<span class="badge review">尚未安装</span>'}</div><p>${E(modelDescription(p.plugin_id,p.description))}</p><div class="key-value"><span>插件版本</span><b>${E(p.version)}</b></div><div class="key-value"><span>支持频率</span><b>${E(p.supports_frequency.join(' / '))}</b></div><div class="key-value"><span>依赖分组</span><b>${E(p.requires_extra||'核心依赖')}</b></div></div>`).join('')}</div><div class="helper" style="margin-top:20px">时骁 v0.4.0 已实现轻量预测插件、统一模型协议及可选 AutoGluon 适配器。动态机理模型和智能体自动实验仍属于下一阶段研发。</div>`}
  function helpPage(){return header('使用指南','从 BI 准备数据、接入模型服务，到预测任务与质量报告的完整操作说明。')+`
    <div class="grid grid-2" style="margin-bottom:18px"><div class="panel"><h2>项目数据资产</h2><p>打开项目的数据管理，使用顶部搜索筛选资产名、来源工作表或字段。点击“打开”查看结构及版本；点击“进入 BI”打开全屏分析工作区。删除会移入项目回收站，原文件保留，可随时恢复。涉及运行中的模型任务会被阻止。</p></div>
    <div class="panel"><h2>Perspective 与 Agent</h2><p>BI 主画布保留 Perspective 原生字段、过滤、排序、图表和布局操作。右下角分析助手支持“查看数据字段”“查看前20行”等只读命令；复杂需求先检查字段及运行时可用工具。图表配置会记录工作区版本；没有真实 Viewer 渲染回执的步骤不视为成功。</p></div></div>
    <div class="grid grid-2"><div class="panel"><h2>一 · 导入并检查原始数据</h2><p>在 <a class="link" href="/projects">智能导入</a> 上传 ZIP、多个 Excel、CSV 或 Parquet，自动识别工作表、年度和字段；在数据版本页核对口径、来源、缺失率和原始单元格。</p></div>
    <div class="panel"><h2>二 · 可视化清洗与分析</h2><p>在智能导入版本中选择统计口径和来源，查看趋势、异常候选及质量报告；需要高级交互时可发布到原生 BI。确认编辑后生成独立版本，原始文件保留。</p></div>
    <div class="panel"><h2>三 · 接入大模型辅助</h2><p>从 <a class="link" href="/settings/models">大模型服务设置</a> 填写兼容 API 地址、模型名和密钥，保存并测试连接。模型可生成 BI 操作建议和预测任务草稿，确认后才会执行写操作。</p></div>
    <div class="panel"><h2>四 · 创建时序预测任务</h2><p>在 BI 表详情映射时间、目标、实体及真实可用时间后，转换为标准时序数据；进入 <a class="link" href="/new">新建预测任务</a> 选择跨度和算法并提交。</p></div>
    <div class="panel"><h2>五 · 查看结果与质量</h2><p>前往 <a class="link" href="/runs">运行记录</a> 查看进度、预测值、滚动回测和 TQE 质量结论。模型质量需要结合独立测试与业务要求评估。</p></div>
    <div class="panel"><h2>六 · 使用边界</h2><p>当前为本地单用户研发版。基础预测与 BI 工具已具备可运行实现；机理自适应、多轮 Agent、自主优化与正式 Pangoo 对接需要继续研发。</p></div></div>`}
  async function detailPage(id){
    root.innerHTML=errorNotice()+header('任务运行详情','正在读取真实运行状态…');
    try{const s=await req(`/runs/${encodeURIComponent(id)}`);const [q,f,e]=await Promise.allSettled([req(`/runs/${id}/quality`),req(`/runs/${id}/forecasts`),req(`/runs/${id}/events`)]);
      if((location.pathname||'').includes(id))root.innerHTML=errorNotice()+runDetail(s,q.status==='fulfilled'?q.value:null,f.status==='fulfilled'?f.value:null,e.status==='fulfilled'?e.value:[]);
    }catch(e){root.innerHTML=errorNotice()+header('任务不可用','无法读取该任务。')+`<div class="helper danger">${E(e.message)}</div>`}}
  async function render(){
    const current=page(),parts=(location.pathname||'/projects').replace(/^\//,'').split('/');lastRoute=location.pathname;
    const isNative=/^\/projects\/[^/]+\/bi\/native\/[^/]+/.test(location.pathname);
    window.TimePilotBIWorkspaceLayout?.mode(isNative);
    if(!isNative){window.TimePilotBIFloating?.leave();window.TimePilotBIConversations?.reset();}
    document.getElementById('page-title').textContent=titles[current]||'运行详情';
    document.querySelectorAll('[data-nav]').forEach(x=>x.classList.toggle('active',x.getAttribute('href')==='/'+current));
    document.getElementById('sidebar').classList.remove('open');
    await refresh();
    if(location.pathname!==lastRoute) return;
    if(apiUnavailable){
      root.innerHTML=errorNotice()+`<div class="panel empty-server"><h2>工作台暂时不可用</h2><p>无法读取数据集、任务和模型插件。请检查后台服务，连接恢复后再继续操作。</p><button class="button button-primary" type="button" data-action="refresh">重新连接</button></div>`;
      return;
    }
    if(current==='projects'){await window.TimePilotProjects.render({root,req,toast,navigate,route:location.pathname});return;}
    // The project workspace is now the single front door for business data.
    // Keep old API endpoints for source compatibility, but retire their
    // overlapping stand-alone browser pages.
    if(['imports','data-assets','datasets','overview'].includes(current) ||
       (current==='bi' && parts[1]!=='native')){
      navigate('/projects');return;
    }
    if(current==='runs'&&parts.length>1){await detailPage(parts[1]);return}
    if(current==='new'&&new URLSearchParams(location.search).has('draft')){
      const q=new URLSearchParams(location.search),pid=q.get('project'),did=q.get('draft');
      if(!pid||!q.get('dataset'))throw new Error('BI 草稿缺少项目或数据集编号');
      const draft=await req(`/projects/${encodeURIComponent(pid)}/forecast-drafts/${encodeURIComponent(did)}`);
      if(draft.status!=='dataset_ready'||draft.dataset_id!==q.get('dataset'))throw new Error('预测草稿与训练数据集不一致，请返回项目 BI 重新生成');
      root.innerHTML=errorNotice()+newPage(draft);return;
    }
    if(current==='research'){await window.TimePilotResearch.render({root,req,toast,navigate,route:location.pathname});return}
    if(current==='agent'){await window.TimePilotAgentCenter.render({root,req,toast,navigate,route:location.pathname});return}
    if(current==='settings'){await window.TimePilotSettings.render({root,req,toast,navigate,route:location.pathname});return}
    const pages={new:newPage,runs:runsPage,plugins:pluginsPage,help:helpPage};
    root.innerHTML=errorNotice()+(pages[current]||helpPage)()+`<div class="app-footer">TimePilot v0.24.6 · ${window.TIMEPILOT_ENTERPRISE?"企业受控模式":"本地研发环境"} · 数据来源与实验版本可追溯</div>`;
  }
  async function runDemo(){try{toast('正在创建合成数据示例…');const result=await req('/demo/bootstrap',{method:'POST'});toast('示例任务已提交，正在执行');navigate('/runs/'+result.run_id);await render()}catch(e){toast('示例执行失败：'+e.message)}}
  async function handleClick(event){const el=event.target.closest('[data-action]');if(!el)return;
    const action=el.dataset.action,id=el.dataset.id;
    if(action==='demo')return runDemo();
    if(action==='draft-task'){
      const description=document.getElementById('task-natural-request')?.value.trim();
      const datasetId=document.getElementById('task-dataset')?.value;
      if(!description||!datasetId){toast('请先选择数据并填写任务描述');return}
      el.disabled=true;
      try{const suggestion=await req('/assistant/task/draft',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({request:description,dataset_id:datasetId})});
        const form=document.getElementById('new-task-form');form.elements['name'].value=suggestion.name;
        form.elements['task_family'].value=suggestion.task_family;form.elements['horizon'].value=String(suggestion.horizon);
        toast('已生成候选任务配置，请核对后再执行。');
      }catch(error){toast('智能填写失败：'+error.message)}finally{el.disabled=false}return;
    }
    if(action==='refresh'){await render();updateFloating();return}
    if(action==='preview'){try{const d=await req(`/datasets/${encodeURIComponent(id)}/preview`);const box=document.getElementById('dataset-preview');if(box){box.innerHTML=`<div class="panel"><h2>数据预览 · ${E(d.manifest.name)}</h2><p>显示前 20 行。原始数据不会被浏览器修改。</p><div class="table-wrap"><table class="data-table"><thead><tr><th>序列</th><th>时间</th><th>目标</th><th>可用时间</th></tr></thead><tbody>${d.rows.map(x=>`<tr><td>${E(x.series_id)}</td><td>${E(x.timestamp)}</td><td>${fmt(x.target)}</td><td>${E(x.available_at)}</td></tr>`).join('')}</tbody></table></div></div>`;box.scrollIntoView({behavior:'smooth'})}}catch(e){toast(e.message)}return}
    if(action==='cancel'||action==='retry'){try{if(action==='cancel'&&!confirm('确认请求取消该任务？正在执行的模型可能需要到阶段边界才会停止。'))return;await req(`/runs/${id}/${action}`,{method:'POST'});toast(action==='retry'?'已重新入队':'已提交取消请求');render()}catch(e){toast(e.message)}return}
  }
  async function handleSubmit(event){const f=event.target;
    if(f.id==='new-task-form'){event.preventDefault();const data=new FormData(f);const chosen=data.getAll('model');if(!chosen.length){toast('请至少选择一个预测模型');return}
      const ds=datasets.find(x=>x.dataset_id===data.get('dataset'));if(!ds){toast('请选择有效数据集');return}
      const horizon=Number(data.get('horizon'));const task={task_id:String(data.get('task_id')),name:String(data.get('name')),task_family:String(data.get('task_family')),
      frequency:ds.frequency,forecast_horizon:horizon,model_candidates:chosen,target:'target',entity_keys:['series_id'],training_budget_seconds:Number(data.get('budget')),
      evaluation:{protocol_id:'rolling_holdout_v1',validation_windows:Number(data.get('windows')),test_horizon:horizon,primary_metric:'WAPE',min_history:Number(data.get('min_history'))},approved:true};
      const btn=f.querySelector('button[type="submit"]');btn.disabled=true;try{const payload={dataset:String(data.get('dataset')),task};await req('/tasks/validate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
      const q=new URLSearchParams(location.search),projectId=q.get('project'),draftId=q.get('draft');
      let response;
      if(projectId&&draftId){
        response=await req(`/projects/${encodeURIComponent(projectId)}/runs/submit`,{
          method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...payload,draft_id:draftId})});
      }else{
        response=await req('/runs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
        if(projectId){try{await req(`/projects/${encodeURIComponent(projectId)}/runs`,{
          method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({run_id:response.run_id})});}
          catch(e){toast('运行已提交，但项目登记失败：'+e.message)}}
      }
      toast('任务已完成检查并进入运行队列');navigate('/runs/'+response.run_id);render()}catch(e){toast('任务尚不能启动：'+e.message)}finally{btn.disabled=false}}
  }
  function updateFloating(){const active=runs.find(r=>['queued','running'].includes(r.status));const box=document.getElementById('floating-task');if(!active){box.classList.add('is-hidden');return}box.classList.remove('is-hidden');
    box.innerHTML=`<div class="float-head"><div class="float-title">${E(active.task_id)} · ${E(statusMap[active.status]?.[0]||active.status)}</div><div class="float-actions"><button id="float-collapse" type="button">${floatCollapsed?'展开':'最小化'}</button><button id="float-open" type="button">查看</button></div></div>${floatCollapsed?'':`<p>阶段：${E(stageText(active.stage))} · 切换页面不会中断训练</p><div class="progress"><div style="width:${Number(active.progress)||0}%"></div></div>`}`;
    box.querySelector('#float-collapse').onclick=()=>{floatCollapsed=!floatCollapsed;updateFloating()};box.querySelector('#float-open').onclick=()=>navigate('/runs/'+active.run_id);
  }
  document.addEventListener('click',event=>{
    if(window.TimePilotFeatureV2UI?.click && event.target.closest('[data-feature-v2]')){window.TimePilotFeatureV2UI.click(event);return;}
    if(window.TimePilotFeatureLabUI?.click && event.target.closest('[data-feature]')){window.TimePilotFeatureLabUI.click(event);return;}
    if(window.TimePilotProjectBI?.click && event.target.closest('[data-project-bi]')){window.TimePilotProjectBI.click(event);return;}
    const a=event.target.closest('a[href]');
    if(!a || a.getAttribute('href')?.startsWith('#') || a.hasAttribute('download') || event.defaultPrevented || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || event.button!==0)return;
    const url=new URL(a.href,location.origin);
    if(url.origin===location.origin && !url.pathname.startsWith('/api/') && !url.pathname.startsWith('/assets/') && a.target!=='_blank'){
      event.preventDefault();navigate(url.pathname+url.search);
    }
  });
  document.addEventListener('click',event=>{
    if(window.TimePilotBIConversations?.click && event.target.closest('[data-bi-agent]')){window.TimePilotBIConversations.click(event);return;}
    if(window.TimePilotProjects?.click && event.target.closest('[data-project]')){window.TimePilotProjects.click(event);return;}
    if(window.TimePilotResearch?.click && event.target.closest('[data-research]')){window.TimePilotResearch.click(event);return;}
    if(window.TimePilotAgentCenter?.click && event.target.closest('[data-agent]')){window.TimePilotAgentCenter.click(event);return;}
    if(window.TimePilotModeling?.click && event.target.closest('[data-modeling]')){window.TimePilotModeling.click(event);return;}
    if(window.TimePilotNativeBI?.click && event.target.closest('[data-native]')){window.TimePilotNativeBI.click(event);return;}
    if(window.TimePilotSettings.handleClick && window.TimePilotSettings.handleClick(event,{root,req,toast,navigate}))return;
    handleClick(event);
  });
  document.addEventListener('submit',event=>{
    if(window.TimePilotBIConversations?.submit && event.target?.id==='tp-bi-agent-form'){window.TimePilotBIConversations.submit(event);return;}
    if(window.TimePilotFeatureV2UI?.submit && event.target?.id?.startsWith('tp-fl2-')){window.TimePilotFeatureV2UI.submit(event);return;}
    if(window.TimePilotFeatureLabUI?.submit && event.target?.id==='tp-feature-form'){window.TimePilotFeatureLabUI.submit(event);return;}
    if(window.TimePilotProjects?.submit && event.target?.id?.startsWith('project-')){window.TimePilotProjects.submit(event);return;}
    if(window.TimePilotResearch?.submit && event.target?.id?.startsWith('research-')){window.TimePilotResearch.submit(event);return;}
    if(window.TimePilotAgentCenter?.submit && event.target?.id==='agent-job-form'){window.TimePilotAgentCenter.submit(event);return;}
    if(window.TimePilotModeling?.submit && event.target?.id==='tp-modeling-form'){window.TimePilotModeling.submit(event);return;}
    if(window.TimePilotNativeBI?.submit && event.target?.id?.startsWith('tp-native-')){window.TimePilotNativeBI.submit(event);return;}
    if(window.TimePilotSettings.handleSubmit && window.TimePilotSettings.handleSubmit(event,{root,req,toast,navigate}))return;
    handleSubmit(event);
  });
  document.addEventListener('input',event=>{
    if(event.target?.id==='tp-field-search')window.TimePilotProjects?.change?.(event);
  });
  document.addEventListener('change',event=>{
    if(window.TimePilotModeling?.change && event.target?.closest('#tp-modeling-form') && window.TimePilotModeling.change(event))return;
    if(window.TimePilotFeatureV2UI?.change && event.target?.dataset.featureV2Change){window.TimePilotFeatureV2UI.change(event);return;}
    if(window.TimePilotFeatureLabUI?.change && ['select-asset','choose-kind'].includes(event.target?.dataset.feature)){window.TimePilotFeatureLabUI.change(event);return;}
    if(window.TimePilotProjectBI?.change && event.target?.id==='project-bi-version'){window.TimePilotProjectBI.change(event);return;}
    if(window.TimePilotProjects?.change && window.TimePilotProjects.change(event))return;
    if(window.TimePilotAgentCenter?.changed && window.TimePilotAgentCenter.changed(event))return;
    if(window.TimePilotSettings.handleChange && window.TimePilotSettings.handleChange(event))return;
    if(event.target.id!=='task-dataset')return;
    const selected=datasets.find(x=>x.dataset_id===event.target.value);
    const target=document.getElementById('task-dataset-info');
    if(target&&selected)target.textContent=`已选择 ${selected.name}，共 ${Number(selected.record_count).toLocaleString('zh-CN')} 条观测，${selected.series_count} 条序列，时间频率 ${selected.frequency}。`;
  });
  window.addEventListener('popstate',render);
  document.getElementById('header-new').addEventListener('click',()=>navigate('/projects'));
  document.getElementById('menu-toggle').addEventListener('click',()=>document.getElementById('sidebar').classList.toggle('open'));
  setInterval(async()=>{try{runs=await req('/runs');updateFloating();if(page()==='runs'&&location.pathname.split('/').length>2){const id=location.pathname.split('/')[2];if(id){const r=runs.find(x=>x.run_id===id);if(r&&['running','queued'].includes(r.status)){await detailPage(id)}}}}catch{}},2500);
  render().then(updateFloating).catch(e=>{root.innerHTML=`<div class="helper danger">工作台加载失败：${E(e.message)}</div>`});
})();
