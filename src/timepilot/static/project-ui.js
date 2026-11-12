/* Project-first workspace. Imported originals stay immutable; raw rows and
 * interactive charts live only in the Perspective BI page. */
(() => {
  'use strict';
  let ctx=null, project=null, importInfo=null, importCatalog=null, importCurrentRegion=null, executing=null, timer=null, summaryTimer=null;
  const esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const u=x=>encodeURIComponent(String(x));
  const post=x=>({method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(x)});
  const s=x=>Number(x||0).toLocaleString('zh-CN');
  const api=(p,o)=>ctx.req('/projects'+p,o);
  const projectPath=p=>'/projects/'+u(p);
  const breadcrumb=(label,extra='')=>`<div class="project-breadcrumb"><a href="/projects">全部项目</a><span>/</span><a href="${projectPath(project.id)}">${esc(project.name)}</a><span>/</span><b>${esc(label)}</b>${extra}</div>`;
  const frame=(label,body,actions='')=>`${breadcrumb(label)}<div class="page-heading"><div><h1>${esc(label)}</h1><p>${esc(project.description||'项目内的数据、分析、模型和运行记录统一管理。')}</p></div>${actions}</div><div class="project-tabs">${[['','概览'],['data','数据管理'],['features','特征工程'],['models','模型管理'],['tasks','任务记录']].map(([suffix,name])=>`<a href="${projectPath(project.id)}${suffix?'/'+suffix:''}" class="${(location.pathname===projectPath(project.id)+(suffix?'/'+suffix:''))?'active':''}">${name}</a>`).join('')}</div>${body}`;
  const message=(t,type='warning')=>`<div class="helper ${type}">${esc(t)}</div>`;
  const empty=(t,subtitle='')=>`<div class="empty"><b>${esc(t)}</b><p>${esc(subtitle)}</p></div>`;
  function stop(){if(timer){clearInterval(timer);timer=null}if(summaryTimer){clearTimeout(summaryTimer);summaryTimer=null}}
  function makeLink(label,path,primary=false){return `<a class="button${primary?' button-primary':''}" href="${path}">${label}</a>`}
  async function allProjects(){
    const list=await api('');project=null;
    ctx.root.innerHTML=`<div class="page-heading"><div><h1>项目</h1><p>每个项目统一管理数据、分析视图、模型和执行任务。</p></div><button class="button button-primary" data-project="new">＋ 新建项目</button></div><div class="project-list">${list.filter(x=>x.status!=='trash').map(p=>`<a class="project-tile" href="${projectPath(p.id)}"><span class="project-tile-icon">▦</span><strong>${esc(p.name)}</strong><small>${esc(p.description||p.domain)}</small><span class="badge ${p.status==='active'?'success':'review'}">${p.status==='active'?'进行中':'已归档'}</span></a>`).join('')}</div>${list.length?'':empty('还没有项目','新建一个项目，开始导入业务数据。')}<div class="panel project-create" id="project-create-panel" hidden><h2>创建项目</h2><form id="project-create-form" class="form"><div class="field"><label>项目名称</label><input name="name" placeholder="例如：车辆销量预测" maxlength="100" required></div><div class="field"><label>研究或业务说明（可选）</label><textarea name="description" rows="2" maxlength="1500"></textarea></div><div class="field"><label>项目类别</label><select name="domain"><option value="forecast">预测分析</option><option value="manufacturing">工业制造</option><option value="research">科研实验</option><option value="general">通用分析</option></select></div><button class="button button-primary" type="submit">创建并进入</button></form></div>`;
  }
  async function overview(){
    const stats=await api('/'+u(project.id));
    ctx.root.innerHTML=frame('项目概览',`<div class="grid grid-4">${[['数据资产',stats.assets],['导入记录',stats.imports],['模型运行',stats.models],['任务记录',stats.runs]].map(([name,count])=>`<div class="panel stat-card"><div class="stat-label">${name}</div><div class="stat-value">${s(count)}</div></div>`).join('')}</div><div class="grid grid-2 project-next"><div class="panel"><h2>管理数据</h2><p>查看当前项目的数据清单和解析状态，或导入新的 Excel、CSV、ZIP 数据。</p>${makeLink('进入数据管理',projectPath(project.id)+'/data',true)}</div><div class="panel"><h2>开展建模</h2><p>模型运行与实验引用项目数据版本，训练前校验实体、发布时间和统计口径。</p>${makeLink('进入模型管理',projectPath(project.id)+'/models')}</div></div><div class="project-meta"><button class="button button-small" data-project="edit">修改项目</button><button class="button button-small" data-project="archive">${project.status==='archived'?'恢复项目':'归档项目'}</button><button class="button button-small" data-project="trash">移入回收站</button></div>`);
  }
  async function dataPage(){
    const [catalog,imports,nativeAssets,uploadJobs,publishJobs]=await Promise.all([
      api('/'+u(project.id)+'/data-catalog'),api('/'+u(project.id)+'/imports'),
      api('/'+u(project.id)+'/native-assets'),api('/'+u(project.id)+'/imports/jobs'),api('/'+u(project.id)+'/publish-jobs')]);
    const publishedImports=new Set(catalog.assets.map(a=>a.import_id).filter(Boolean));
    const pending=imports.filter(x=>x.version_count===0&&!publishedImports.has(x.import_id));
    const activeUploads=uploadJobs.filter(x=>!['succeeded','cancelled'].includes(x.status));
    const activePublications=(publishJobs.jobs||[]).filter(x=>x.status!=='succeeded').slice(0,20);
    ctx.root.innerHTML=frame('数据管理',`<div class="panel"><div class="section-row"><h2>项目数据资产</h2>${makeLink('＋ 上传新数据',projectPath(project.id)+'/data/import',true)}</div><p class="muted small">可按资产名称、源文件和真实字段查询整个项目；删除将资产移入可恢复的回收站。</p><div id="tp-asset-catalog"></div></div>${nativeAssets.length?`<div class="panel" style="margin-top:22px"><details><summary>派生编辑版本（高级）</summary>${nativeAssets.map(x=>`<a class="project-pending" href="${projectPath(project.id)+'/bi/native/'+u(x.native_asset_id)}">${esc(x.asset?.name||x.native_asset_id)}<span>编辑与回滚 →</span></a>`).join('')}</details></div>`:''}${activeUploads.length?`<div class="panel" style="margin-top:22px"><h2>上传与结构扫描</h2>${activeUploads.map(x=>`<a class="project-pending" href="${projectPath(project.id)+'/data/import-job/'+u(x.job_id)}">${esc(x.files.map(f=>f.name).join('、'))}<span>${esc(x.status)} · ${s(x.completed_sheets)}/${s(x.total_sheets)} 张表</span></a>`).join('')}</div>`:''}${activePublications.length?`<div class="panel" style="margin-top:22px"><div class="section-row"><h2>数据发布与 BI 注册</h2><button class="button button-small" data-project="refresh-catalog">刷新进度</button></div>${activePublications.map(x=>`<div class="project-pending"><span><b>${esc(x.request?.name||'数据发布')}</b><small> · ${esc(({queued:'排队中',running:'后台处理中',prepared:'数据已保存，待注册 BI',failed:'失败'})[x.status]||x.status)} · ${esc(x.stage)} · 尝试 ${s(x.attempts)} 次</small>${x.error?`<small class="muted">原因：${esc(x.error)}</small>`:''}</span><div>${['failed','prepared'].includes(x.status)?`<button class="button button-small" data-project="retry-publication" data-job="${esc(x.job_id)}">重新完成发布</button>`:''}</div></div>`).join('')}</div>`:''}${pending.length?`<div class="panel" style="margin-top:22px"><h2>待完成解析 ${pending.length}</h2>${pending.map(x=>`<a class="project-pending" href="${projectPath(project.id)+'/data/import/'+u(x.import_id)}">${esc(x.filename)}<span>${s(x.candidate_count)} 个候选区域 · 继续整理</span></a>`).join('')}</div>`:''}`);
    window.TimePilotAssetCatalog?.mount(ctx,project.id);
  }
  async function dataCatalogAsset(aid){
    const base='/'+u(project.id)+'/catalog/assets/'+u(aid);
    const summary=await api(base);
    if(summary.requires_restore){
      ctx.root.innerHTML=frame('回收站资产',`<div class="panel"><h2>资产已移入回收站</h2><p>恢复后才能打开原生 BI。原文件与版本记录仍然保留。</p><button type="button" class="button button-primary" data-project="restore-catalog" data-asset="${esc(aid)}" data-revision="${summary.lifecycle.revision}">恢复数据资产</button></div>`);
      return;
    }
    const [schema,versions,quality,integrity,impact]=await Promise.all([
      api(base+'/schema'),api(base+'/versions'),api(base+'/quality'),
      api(base+'/integrity'),api(base+'/impact')]);
    const allCols=schema.fields||[];
    const required=Object.values(schema.roles||{}).filter(Boolean);
    const statusName={passed:'质量检查通过',warning:'存在需核实项',blocked:'存在质量阻断项',unchecked:'尚未执行质量检查'};
    const items=(quality.issues||[]).map(x=>`<div class="project-pending"><span><b>${esc(x.rule)}</b><small>　${s(x.affected_count)} 条记录 · ${esc(x.severity)} · ${esc(x.status)}</small><small>　源文件行号：${(x.sample_rows||[]).map(s).join('、')||'见完整检查报告'}</small></span>${x.status==='open'?`<button class="button button-small" data-project="quality-decision" data-issue="${esc(x.issue_id)}">记录处理意见</button>`:''}</div>`).join('');
    const fields=allCols.map(f=>`<tr data-profile-search="${esc(String(f.name).toLowerCase())}"><td><strong>${esc(f.name)}</strong></td><td>${esc(f.physical_type||f.inferred_type||'待确定')}</td><td>${esc(f.source_cell||'—')}</td><td>${f.nonempty_count===undefined?'抽样分析':(Number((1-(f.missing_fraction||0))*100).toFixed(1)+'%')}</td></tr>`).join('');
    ctx.root.innerHTML=frame('数据资产详情',`<div class="panel"><div class="section-row"><div><h2>${esc(schema.name)}</h2><p class="muted">${s(schema.rows)} 行 · ${allCols.length} 列 · 版本 ${esc((schema.version_id||'').slice(0,12))}</p></div><div class="action-row">${makeLink('打开正式 BI',projectPath(project.id)+'/bi/native/'+u(versions.versions[0]?.native_asset_id||''),true)}<button class="button" data-project="rename-catalog" data-asset="${esc(aid)}">修改名称</button><button class="button" data-project="trash-catalog" data-asset="${esc(aid)}" data-revision="${summary.lifecycle.revision}">移入回收站</button></div></div><p class="muted small">数据版本固定；重命名只更改目录名称，不重新生成内容。</p></div>
      <div class="project-tabs" style="margin-top:12px"><a href="#tp-catalog-preview">数据预览</a><a href="#tp-catalog-fields">字段</a><a href="#tp-catalog-quality">质量</a><a href="#tp-catalog-history">版本与使用</a></div>
      <section class="panel" id="tp-catalog-preview"><h2>前 20 行真实记录</h2><p class="muted small">完整数据请进入 Perspective；这里不会以样本冒充全表。</p><div id="tp-asset-preview"><p class="muted">正在读取源版本…</p></div></section>
      <section class="panel" id="tp-catalog-fields"><h2>字段结构</h2><p class="muted small">源列顺序与名称保持不变，未确认的业务含义不会自动改名。</p><input id="tp-field-search" placeholder="搜索字段名称"><div class="table-wrap"><table class="data-table"><thead><tr><th>源字段</th><th>类型</th><th>原始表头单元格</th><th>完整率</th></tr></thead><tbody>${fields}</tbody></table></div></section>
      <section class="panel" id="tp-catalog-quality" style="margin-top:16px"><div class="section-row"><h2>数据质量 · ${esc(statusName[quality.status]||quality.status)}</h2><button class="button" data-project="quality-run" data-asset="${esc(aid)}">按已保存规则重新检查</button></div><p class="muted small">按实际发布数据全量检查；源文件不修改。重要问题处理需填写说明并留存审计。</p>
       ${items||'<p class="muted">没有未处理的质量问题，或尚未执行检查。</p>'}
       <details><summary>配置检查规则（高级）</summary><form id="project-quality-form" data-asset="${esc(aid)}" class="form"><h3>必填字段</h3><div class="grid grid-3">${allCols.map(f=>`<label class="check-row"><input type="checkbox" data-quality-required value="${esc(f.name)}" ${required.includes(f.name)?'checked':''}>${esc(f.name)}</label>`).join('')}</div><label class="field"><span>唯一键（多个列名用英文逗号分隔，可留空）</span><input name="unique_key" placeholder="例如 设备编号,采集时间"></label><label class="field"><span>必填字段允许的最大缺失比例</span><input name="max_missing_fraction" type="number" value="0" step="0.01" min="0" max="1"></label><div class="grid grid-3"><label class="field"><span>时间字段（可选）</span><select name="time_field"><option value="">不启用时间规则</option>${allCols.map(f=>`<option value="${esc(f.name)}" ${schema.roles?.time===f.name?'selected':''}>${esc(f.name)}</option>`).join('')}</select></label><label class="field"><span>设备或批次字段（可选）</span><select name="entity_field"><option value="">单个时间序列</option>${allCols.map(f=>`<option value="${esc(f.name)}" ${schema.roles?.entity===f.name?'selected':''}>${esc(f.name)}</option>`).join('')}</select></label><label class="field"><span>最大采样缺口（秒，可留空）</span><input name="max_gap_seconds" type="number" min="1" step="any" placeholder="例如 120"></label></div><label class="check-row"><input name="require_monotonic" type="checkbox"> 按源文件顺序检查时间单调性，逆序时阻断建模</label><button class="button button-primary" type="submit">保存规则并重新检查</button></form></details></section>
      <section class="panel" id="tp-catalog-history" style="margin-top:16px"><h2>版本、使用与存储</h2><p>已登记 ${s(versions.versions.length)} 个内容版本 · ${integrity.all_valid?'文件完整性校验通过':'发现文件缺失或损坏'}</p><div class="project-pending">下游：${impact.versions.reduce((n,v)=>n+(v.views?.length||0),0)} 个 BI 视图 · ${impact.versions.reduce((n,v)=>n+(v.models?.length||0),0)} 个建模任务</div>${versions.versions.map(v=>`<div class="project-pending"><span>版本 ${esc(v.version_id.slice(0,12))} · ${s(v.row_count)} 行 × ${s(v.column_count)} 列</span><span>${esc(v.created_at||'')}</span></div>`).join('')}</section>`);
    const area=document.getElementById('tp-asset-preview');
    if(area)api(base+'/preview?limit=20').then(data=>{
      if(!area.isConnected)return;
      const rows=data.rows||[],fields=data.columns||allCols.map(x=>x.name);
      area.innerHTML=rows.length?`<div class="table-wrap"><table class="data-table"><thead><tr>${fields.map(x=>`<th>${esc(x)}</th>`).join('')}</tr></thead><tbody>${rows.map(r=>`<tr>${fields.map(x=>`<td>${esc(r[x])}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`:empty('没有可展示的记录');
    }).catch(e=>{if(area.isConnected)area.innerHTML=message('预览暂不可用：'+e.message)});
  }

  async function dataAsset(aid){
    await api('/'+u(project.id)+'/assets/'+u(aid));
    ctx.navigate(projectPath(project.id)+'/bi/'+u(aid));
  }
  async function importStart(){
    ctx.root.innerHTML=frame('上传数据',`<div class="project-import-layout"><div class="panel project-upload"><h2>导入文件</h2><p class="muted">自动扫描工作表和独立子表；保留源字段名、字段顺序与数据。无需逐列指定业务角色。</p><form id="project-upload-form" class="form"><label class="project-drop"><span class="project-upload-glyph">＋</span><strong>拖入文件或点击选择</strong><small>支持 CSV、XLSX、Parquet、ZIP 及多文件上传</small><input type="file" name="files" accept=".xlsx,.csv,.parquet,.zip" multiple required></label><div id="project-files" class="muted tiny">尚未选择文件</div><button class="button button-primary" type="submit">上传并自动扫描</button><div class="project-upload-progress" id="project-upload-progress" role="status"></div></form></div><div class="panel project-explain"><h2>导入流程</h2>${[['01','扫描文件与各工作表'],['02','识别独立子表及表头'],['03','保存原始字段与结构证据'],['04','一键发布所有选中表格至 BI']].map(([n,x])=>`<div class="project-step"><span>${n}</span>${esc(x)}</div>`).join('')}<p class="muted small">可选大模型仅辅助解释表结构；不影响文件扫描或数据发布。数据筛选、计算、绘图和模型配置在 BI/模型管理中完成。</p></div></div>`);
  }
  async function uploadJob(jid){
    const job=await api('/'+u(project.id)+'/imports/jobs/'+u(jid));
    const stages={queued:'文件已收到，等待结构扫描',running:'后台正在解析工作簿与工作表',succeeded:'结构扫描已完成',failed:'结构扫描失败',cancelled:'已取消'};
    if(job.status==='succeeded'&&job.import_id){stop();ctx.navigate(projectPath(project.id)+'/data/import/'+u(job.import_id));return;}
    ctx.root.innerHTML=frame('数据结构识别',`<div class="panel project-scan"><h2>${esc(stages[job.status]||job.status)}</h2><p>任务 ${esc(job.job_id)} · 已接收 ${s(job.files.length)} 个文件</p><div class="project-scan-files">${job.files.map(f=>`<div class="project-pending">${esc(f.name)} <span>${s(f.bytes)} B · SHA256 已记录</span></div>`).join('')}</div><div class="project-bi-scan-summary"><span>已扫描 <b>${s(job.completed_sheets)} / ${s(job.total_sheets)}</b> 张工作表</span><span>恢复复用 <b>${s(job.reused_sheets)}</b> 张</span><span>阶段：${esc(({verifying:'验证源文件',unpacking:'展开文件',scanning:'解析工作表',assembling:'汇总结构',complete:'已完成',queued:'排队中',failed:'失败'})[job.phase]||job.phase)}</span></div>${job.total_sheets?`<div class="progress"><div style="width:${Math.min(100,job.progress_percent||0)}%"></div></div>`:job.status==='running'?'<div class="project-scan-loader" role="progressbar" aria-label="后台扫描中"></div>':''}<p class="muted tiny">${job.current_sheet?'当前工作表：'+esc(job.current_sheet)+' · ':''}${job.heartbeat_at?'最近心跳：'+new Date(job.heartbeat_at*1000).toLocaleString('zh-CN'):''}</p>${job.error?message(job.error):''}<div class="action-row">${job.status==='failed'?'<button class="button button-primary" data-project="retry-upload">重试扫描</button>':''}${job.status==='queued'?'<button class="button" data-project="cancel-upload">取消排队</button>':''}${makeLink('返回数据清单',projectPath(project.id)+'/data')}</div><p class="muted small">逐表扫描结果可在异常中断后复用。上传与解析任务在后台执行，可关闭页面。</p></div>`);
    if(['queued','running'].includes(job.status)){
      stop();timer=setInterval(()=>{if(location.pathname.endsWith('/import-job/'+jid)){stop();uploadJob(jid).catch(e=>{stop();ctx.toast('任务刷新失败：'+e.message)})}else stop()},1200);
    }else stop();
  }
  function chosenImportRegions(){
    return [...document.querySelectorAll('[data-import-region]:checked')].map(x=>x.value);
  }
  function sourcePreview(values,cols){
    const shown=cols.slice(0,12);
    return `<div class="table-wrap tp-import-preview"><table class="data-table"><thead><tr>${shown.map(f=>`<th title="${esc(f.source_cell||'')}">${esc(f.source_name||'(空白表头)')}</th>`).join('')}</tr></thead><tbody>${values.slice(0,7).map(row=>`<tr>${shown.map(f=>`<td>${esc(String((row.values||{})[f.internal_alias]??'').slice(0,130))}</td>`).join('')}</tr>`).join('')}</tbody></table></div>${cols.length>shown.length?`<p class="muted tiny">仅显示前 ${shown.length} 列；发布保留全部 ${cols.length} 列。</p>`:''}`;
  }
  async function showImportRegion(rid){
    importCurrentRegion=rid;
    const target=document.getElementById('tp-import-region-preview');if(!target)return;
    const files=importCatalog?.files||[];
    const region=files.flatMap(f=>f.worksheets.flatMap(sheet=>sheet.regions)).find(x=>x.region_id===rid);
    if(!region)return;
    target.innerHTML='<p class="muted small">正在读取区域来源与原始记录…</p>';
    try{
      const [detail,contract]=await Promise.all([
        api('/'+u(project.id)+'/imports/'+u(importInfo.import_id)+'/regions/'+u(rid)+'/preview?limit=8'),
        api('/'+u(project.id)+'/imports/'+u(importInfo.import_id)+'/regions/'+u(rid)+'/contract')]);
      if(importCurrentRegion!==rid || !document.getElementById('tp-import-region-preview'))return;
      const fields=region.fields||[];
      const pos=region.bbox?`${region.sheet}!${region.bbox.row_start}行/${region.bbox.col_start}列 — ${region.bbox.row_end}行/${region.bbox.col_end}列`:'源文件数据表';
      target.innerHTML=`<div class="section-row"><div><h3>${esc(region.name)}</h3><p class="muted tiny">${esc(pos)} · ${s(region.row_count_estimate)} 行（结构估计） · ${s(fields.length)} 列 · 源表头 ${region.header_row?s(region.header_row)+' 行':'无表头'}</p></div><button class="button button-small" data-project="edit-import-region" data-region="${esc(rid)}">核实/修改表头</button>${contract.review?.needs_review?`<button class="button button-primary button-small" data-project="accept-import-region" data-region="${esc(rid)}" ${contract.review.decision==='accepted'?'disabled':''}>${contract.review.decision==='accepted'?'结构已确认':'确认表结构'}</button>`:''}</div><p class="muted small">以下字段名直接来自源文件；空白和重复表头保留原始位置，仅内部查询使用唯一别名。</p>${sourcePreview(detail.rows||[],fields)}<details class="tp-import-field-details"><summary>字段结构与来源单元格（${fields.length} 列）</summary><div class="table-wrap"><table class="data-table"><thead><tr><th>原始列</th><th>源字段名</th><th>内部查询列名</th></tr></thead><tbody>${fields.map(f=>`<tr><td>${esc(f.source_cell||('#'+(f.ordinal+1)))}</td><td>${esc(f.source_name||'(空白)')}</td><td>${esc(f.internal_alias)}</td></tr>`).join('')}</tbody></table></div></details>${(region.warnings||[]).map(x=>message(x)).join('')}`;
    }catch(err){target.innerHTML=message(err.message)}
  }
  async function refreshImportSummary(iid,jobId=null){
    const slot=document.getElementById('tp-import-summary');if(!slot)return;
    const route=location.pathname;
    try{
      const data=await api('/'+u(project.id)+'/imports/'+u(iid)+'/summary');
      if(!document.getElementById('tp-import-summary')||location.pathname!==route)return;
      const summaries=data.summaries||[];
      const pending=(data.regions||[]).filter(r=>r.status!=='ready');
      slot.innerHTML=`<div class="section-row"><h3>数据摘要</h3><span class="muted small">${s(data.ready)} / ${s(data.region_count)} 张表已核实</span></div>
        <div class="tp-import-summary-grid">${summaries.slice(0,12).map(r=>`<article class="tp-import-summary-card">
          <b>${esc(r.source.sheet)} · ${esc(r.source.file_id.slice(0,7))}</b>
          <strong>${s(r.shape.row_count)} 行 × ${s(r.shape.column_count)} 列</strong>
          <p>${esc(r.summary_text)}</p>
          <small>${esc(r.fields.slice(0,6).map(f=>f.source_name).join('、'))}${r.fields.length>6?'…':''}</small>
          ${r.quality.warnings.length?`<small>待核实：${esc(r.quality.warnings.join('；'))}</small>`:''}
          <button type="button" class="button button-small" data-project="select-import-region" data-region="${esc(r.region_id)}">查看字段证据</button></article>`).join('')}
        ${summaries.length>12?`<p class="muted">其余 ${s(summaries.length-12)} 张表可在原始结构中逐张查看。</p>`:''}</div>
        ${pending.length?`<p class="muted small">还有 ${s(pending.length)} 张表等待完整扫描；摘要计算不阻断数据发布。</p>`:''}`;
      if(pending.length){
        if(!jobId){const job=await api('/'+u(project.id)+'/imports/'+u(iid)+'/summary-jobs',post({region_ids:pending.map(x=>x.region_id)}));jobId=job.job_id;}
        const job=await api('/'+u(project.id)+'/imports/'+u(iid)+'/summary-jobs/'+u(jobId));
        if(job.status==='queued'||job.status==='running'){
          const footer=document.createElement('p');footer.className='muted small';
          footer.textContent=`正在扫描源表 ${job.completed}/${job.total}`;slot.append(footer);
          summaryTimer=setTimeout(()=>refreshImportSummary(iid,jobId),1300);
        }else if(job.status==='succeeded'){summaryTimer=setTimeout(()=>refreshImportSummary(iid),350);}
        else{const footer=document.createElement('p');footer.className='helper warning';
          footer.textContent='摘要扫描失败：'+job.error+'。原始发布流程仍可继续。';slot.append(footer);}
      }
    }catch(error){slot.textContent='无法读取统计摘要：'+error.message+'。可继续检查原始表结构和发布。';}
  }
  async function importDetail(iid){
    stop();importInfo=await api('/'+u(project.id)+'/imports/'+u(iid));
    importCatalog=await api('/'+u(project.id)+'/imports/'+u(iid)+'/catalog');
    const files=importCatalog.files||[];
    const tree=files.map((f,index)=>`<details class="tp-import-file" ${index===0?'open':''}><summary><strong>${esc(f.name)}</strong><small>${s(f.region_count)} 张数据表</small></summary>${f.worksheets.map(sheet=>`<div class="tp-import-sheet"><div class="tp-import-sheet-heading">${esc(sheet.sheet)} <small>${s(sheet.region_count)} 张子表</small></div>${sheet.regions.map(r=>`<div class="tp-import-region"><label><input type="checkbox" data-import-region value="${esc(r.region_id)}" checked aria-label="发布 ${esc(r.name)}"></label><button class="tp-import-region-open" data-project="select-import-region" data-region="${esc(r.region_id)}"><strong>${esc(r.name)}</strong><small>${esc(r.bbox?'行 '+r.bbox.row_start+'–'+r.bbox.row_end+' · 列 '+r.bbox.col_start+'–'+r.bbox.col_end:'来源 Schema')} · ${s(r.column_count)} 列</small></button>${r.needs_review?'<span class="badge review">待核实</span>':''}</div>`).join('')||'<p class="muted tiny">没有可发布表格</p>'}</div>`).join('')}</details>`).join('');
    const total=importCatalog.regions_total||0;
    ctx.root.innerHTML=frame('数据结构与发布',`<div class="panel tp-import-heading"><div class="section-row"><div><h2>${esc(importInfo.filename)}</h2><p class="muted small">扫描完成：${s(files.length)} 个文件、${s(total)} 张数据表。字段保持原始名称与顺序。</p></div>${makeLink('返回项目数据',projectPath(project.id)+'/data')}</div><div class="tp-import-quick-actions"><button class="button button-small" data-project="select-all-import">全部选择</button><button class="button button-small" data-project="select-none-import">取消选择</button><button class="button button-small" data-project="explain-import">解释表结构（本地）</button><label class="check-row"><input id="tp-import-use-model" type="checkbox"> 启用已配置模型辅助说明</label></div><div id="tp-import-semantic-note"></div></div><div class="panel" id="tp-import-summary"><p class="muted">正在准备可追溯的数据摘要…</p></div><details class="tp-import-source-details"><summary>查看原始结构、表头与样本</summary><div class="tp-import-review-grid"><div class="panel tp-import-tree"><h3>文件 / 工作表 / 子表</h3>${tree||empty('未发现数据表','请核实源文件和扫描日志。')}</div><div class="panel tp-import-preview-panel" id="tp-import-region-preview"><p class="muted">选择左侧的一张数据表查看原始字段与样例。</p></div></div></details>${(importCatalog.unassigned_regions||[]).length?`<div class="panel" style="margin-top:14px"><details><summary>未发布的说明及待核实区域 · ${s(importCatalog.unassigned_regions.length)}</summary>${importCatalog.unassigned_regions.map(x=>`<div class="project-pending">${esc(x.sheet)}：第 ${s(x.bbox?.row_start)} 行 · ${esc(x.reason||x.kind)}</div>`).join('')}</details></div>`:''}<div class="panel tp-import-batch-bar"><div class="section-row"><div><h3>发布数据表</h3><p class="muted small">每张子表生成独立且可追溯的正式 BI 数据版本；不自动合并表格。后台可安全重试。</p></div><button class="button button-primary" data-project="publish-import-batch">导入选中数据表并进入 BI</button></div><div id="tp-import-batch-progress" aria-live="polite"></div></div>`);
    const first=files.flatMap(f=>f.worksheets.flatMap(x=>x.regions))[0];
    void refreshImportSummary(iid);
    try{
      const batches=await api('/'+u(project.id)+'/imports/'+u(iid)+'/publish-batches');
      if(batches.batches?.length)void showPublishBatch(batches.batches[0].batch_id,false);
    }catch(_){ /* v0.21 API remains compatible during staged upgrade */ }
  }
  async function showPublishBatch(batchId,autoPoll=true){
    const slot=document.getElementById('tp-import-batch-progress');if(!slot)return;
    const batch=await api('/'+u(project.id)+'/publish-batches/'+u(batchId));
    const label={queued:'等待后台处理',running:'后台发布中',succeeded:'全部导入完成',prepared:'数据已保存，等待 BI 服务',partial_failed:'部分区域失败'};
    const lines=batch.items.map(x=>`<div class="tp-import-batch-item"><span>表 ${s(x.ordinal)} · ${esc(x.status)} ${x.error?' · '+esc(x.error):''}</span>${x.native_asset_id?makeLink('进入 BI',projectPath(project.id)+'/bi/native/'+u(x.native_asset_id),true):''}</div>`).join('');
    slot.innerHTML=`<p><strong>${esc(label[batch.status]||batch.status)}</strong> · ${s(batch.succeeded)}/${s(batch.count)} 张表完成，${s(batch.failed)} 张失败，${s(batch.prepared)} 张待注册。</p><div class="progress"><div style="width:${batch.count?100*batch.succeeded/batch.count:0}%"></div></div>${lines}${['partial_failed','prepared'].includes(batch.status)?`<button class="button button-small" data-project="retry-import-batch" data-batch="${esc(batchId)}">重试未完成表格</button>`:''}`;
    if(autoPoll && ['queued','running'].includes(batch.status)){
      stop();timer=setInterval(()=>{if(document.getElementById('tp-import-batch-progress')){
        showPublishBatch(batchId,false).then(b=>{if(!['queued','running'].includes(b.status))stop()}).catch(()=>stop());
      }else stop()},1800);
    }else if(!['queued','running'].includes(batch.status))stop();
    return batch;
  }
  async function publishImportBatch(){
    const regions=chosenImportRegions();
    if(!regions.length)throw Error('请选择至少一张数据表');
    if(!confirm(`将选中的 ${regions.length} 张数据表分别发布到正式 BI？保留原始字段、原文件和独立版本。`))return;
    const unassigned=importCatalog?.unassigned_regions||[];
    const confirmUnassigned=unassigned.length>0;
    if(confirmUnassigned&&!confirm(`当前原文件还有 ${unassigned.length} 个未归属的非空区域，可能未能包含全部数据。已检查来源并确认仅发布当前选中区域？`))return;
    const batch=await api('/'+u(project.id)+'/imports/'+u(importInfo.import_id)+'/publish-batch',post({region_ids:regions,confirm:true,confirm_unassigned:confirmUnassigned}));
    ctx.toast('批量发布已提交，后台继续执行');
    await showPublishBatch(batch.batch_id,true);
  }
  async function executionStatus(iid,planId){
    const root=document.getElementById('project-execution');if(!root)return;
    const result=await api('/'+u(project.id)+'/imports/'+u(iid)+'/plans/'+u(planId)+'/execution');
    root.hidden=false;
    root.innerHTML=`<h2>正在构建数据版本</h2><p>任务状态：${esc(result.status)} · 已处理 ${s(result.completed_candidates)} / ${s(result.total_candidates)} 个区域</p><div class="progress"><div style="width:${result.total_candidates?Math.min(100,100*result.completed_candidates/result.total_candidates):0}%"></div></div>${result.error?message(result.error):''}${result.status==='completed'?`<div class="helper success">完成 ${s(result.output_rows)} 条观测，已建立可追溯的数据版本。</div>${makeLink('进入 BI 查看数据',projectPath(project.id)+'/bi/'+u('si_'+iid.replaceAll('-','')),true)}`:''}`;
    if(result.status==='completed'||result.status==='failed'||result.status==='interrupted')stop();
    return result;
  }
  async function models(){
    const [list,plugins,drafts,modelDrafts]=await Promise.all([
      api('/'+u(project.id)+'/models'),ctx.req('/plugins'),
      api('/'+u(project.id)+'/forecast-drafts'),
      api('/'+u(project.id)+'/modeling/drafts')]);
    const reusable=modelDrafts.map(d=>`<div class="project-pending"><div><b>${esc(d.name)}</b><small class="muted"> · ${esc(({review_required:'待检查',ready:'待确认',approved:'已确认',dataset_ready:'训练数据就绪',queued:'已提交'})[d.status]||d.status)} · 数据 ${esc(d.version_id)}</small><br><small class="muted">目标 ${esc(d.mapping.target||'待确认')} · 频率 ${esc(d.plan.frequency)} · 视图 ${esc(d.view_id?.slice(0,8)||'完整源版本 / 行级筛选')}</small></div><span><button type="button" class="button button-small" data-project="open-modeling-draft" data-draft-id="${esc(d.draft_id)}" data-asset-id="${esc(d.asset_id)}">${d.run_id?'查看来源':'继续建模'}</button> <button type="button" class="button button-small" data-project="reproduce-modeling-draft" data-draft-id="${esc(d.draft_id)}" data-asset-id="${esc(d.asset_id)}">复现实验</button> ${makeLink('返回 BI',projectPath(project.id)+'/bi/native/'+u(d.asset_id)+(d.view_id?'/view/'+u(d.view_id):''))}</span></div>`).join('');
    ctx.root.innerHTML=frame('模型管理',
      `<div class="panel"><div class="section-row"><h2>模型运行</h2>${makeLink('＋ 从已有数据创建模型',projectPath(project.id)+'/data',true)}</div>
      ${list.length?`<div class="table-wrap"><table class="data-table"><thead><tr><th>运行编号</th><th>任务</th><th>数据来源</th><th>状态</th><th>操作</th></tr></thead><tbody>${list.map(x=>`<tr><td class="mono">${esc(x.run_id)}</td><td>${esc(x.task_id||'—')}</td><td>${x.view_id?'BI 视图 '+esc(x.view_id.slice(0,8)):x.asset_id?'数据资产 '+esc(x.asset_id.slice(0,8)):'手动配置'}</td><td>${esc(x.status)}</td><td>${makeLink('查看运行','/runs/'+u(x.run_id))}</td></tr>`).join('')}</tbody></table></div>`:empty('暂无模型运行','先进入数据资产 BI，点击“从当前数据建立模型”。')}</div>
      <div class="panel" style="margin-top:20px"><h2>数据建模方案</h2>${reusable||empty('没有建模方案','上传数据并发布为通用资产后，从 BI 保存视图创建模型任务。')}</div>
      <div class="panel" style="margin-top:20px"><h2>既有预测草稿</h2>${drafts.length?drafts.map(x=>`<div class="project-pending"><span>${esc(x.name||x.draft_id)} · ${esc(x.status)}</span>${makeLink('打开历史视图',projectPath(project.id)+'/bi/'+u(x.asset_id)+'?view_id='+u(x.view_id))}</div>`).join(''):empty('暂无既有预测草稿')}</div>
      <div class="panel" style="margin-top:20px"><h2>可用算法插件</h2><div class="grid grid-2">${plugins.map(x=>`<div class="project-plugin"><b>${esc(x.plugin_id)}</b><small>${x.available?'已就绪':'缺少可选依赖'}</small></div>`).join('')}</div></div>`);
  }
  async function tasks(){
    const tasks=await api('/'+u(project.id)+'/tasks');
    ctx.root.innerHTML=frame('任务记录',`<div class="panel"><h2>模型运行</h2>${tasks.runs.length?tasks.runs.map(r=>`<div class="project-pending">${esc(r.task_id||r.run_id)}<span>${esc(r.status)}　${makeLink('详情','/runs/'+u(r.run_id))}</span></div>`).join(''):empty('没有模型任务')}</div><div class="panel" style="margin-top:20px"><h2>数据导入</h2>${tasks.imports.map(r=>`<a class="project-pending" href="${projectPath(project.id)+'/data/import/'+u(r.import_id)}">${esc(r.filename||r.import_id)}<span>${r.version_count?'已生成版本':'待整理'}</span></a>`).join('')||empty('没有导入任务')}</div>`);
  }
  async function biPage(assetId){
    if(!window.TimePilotProjectBI)throw Error('项目 BI 页面尚未加载');
    await window.TimePilotProjectBI.render(assetId,{...ctx,projectId:project.id});
  }
  async function nativeEditablePage(aid,viewId=null){
    await api('/'+u(project.id)+'/native-assets/'+u(aid));
    if(!window.TimePilotNativeBI)throw Error('原生 BI 编辑组件未加载');
    await window.TimePilotNativeBI.render({...ctx,projectId:project.id,
       route:'/bi/native/'+u(aid)+(viewId?'/view/'+u(viewId):'')});
  }
  async function render(context){
    stop();window.TimePilotAssetCatalog?.stop();ctx=context;const parts=location.pathname.split('/').filter(Boolean);
    if(parts[2]!=='bi'||parts[3]==='native')await window.TimePilotProjectBI?.dispose?.();
    if(parts[2]!=='features')window.TimePilotFeatureV2UI?.dispose?.();
    if(parts.length<2)return allProjects();
    project=(await api('/'+u(parts[1]))).project;
    const action=parts[2]||'';
    if(!action)return overview();
    if(action==='data'){
      if(parts[3]==='catalog'&&parts[4])return dataCatalogAsset(parts[4]);
      if(parts[3]==='import')return parts[4]?importDetail(parts[4]):importStart();
      if(parts[3]==='import-job'&&parts[4])return uploadJob(parts[4]);
      if(parts[3])return dataAsset(parts[3]);
      return dataPage();
    }
    if(action==='bi'&&parts[3]==='native'&&parts[4])return nativeEditablePage(parts[4],parts[5]==='view'?parts[6]:null);
    if(action==='bi'&&parts[3])return biPage(parts[3]);
    if(action==='features'){
      ctx.root.innerHTML=frame('特征工程','<div id="tp-feature-lab" aria-live="polite"></div>');
      if(new URLSearchParams(location.search).get('legacy')==='1')return window.TimePilotFeatureLabUI?.render(ctx,project.id);
      return window.TimePilotFeatureV2UI?.render(ctx,project.id,parts[3]||'profile');
    }
    if(action==='models')return models();
    if(action==='tasks')return tasks();
    ctx.root.innerHTML=message('页面不存在');
  }
  async function click(event){
    const el=event.target.closest('[data-project]');if(!el)return false;
    event.preventDefault();const action=el.dataset.project;
    try{
      if(action==='new'){const panel=document.getElementById('project-create-panel');panel.hidden=false;panel.scrollIntoView({behavior:'smooth'});}
      else if(action==='edit'){
        const name=prompt('项目名称',project.name);if(name===null)return true;
        await api('/'+u(project.id),{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({name})});ctx.toast('项目已更新');await render(ctx);
      }
      else if(action==='archive'){
        await api('/'+u(project.id)+(project.status==='archived'?'/restore':'/archive'),{method:'POST'});await render(ctx);
      }
      else if(action==='trash'){
        if(confirm('确认将项目移入回收站？数据原文件不会删除。')){
          await api('/'+u(project.id),{method:'DELETE'});ctx.navigate('/projects');
        }
      }
      else if(action==='rename-catalog'){
        const aid=el.dataset.asset;
        const name=prompt('新的数据资产名称');if(!name)return true;
        await api('/'+u(project.id)+'/catalog/assets/'+u(aid),
                  {method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({name})});
        await dataCatalogAsset(aid);
      }
      else if(action==='trash-catalog'||action==='restore-catalog'){
        const aid=el.dataset.asset,rev=Number(el.dataset.revision);
        const requested=action==='trash-catalog'?'trash':'restore';
        if(requested==='trash'){
          const impact=await api('/'+u(project.id)+'/catalog/assets/'+u(aid)+'/impact');
          if(impact.active_blockers?.length)throw Error('运行中的任务引用了该资产，无法回收');
          if(!confirm(`移入回收站？已发现 ${impact.dependencies?.length||0} 项历史引用。此操作不会删除源文件。`))return true;
        }
        await api('/'+u(project.id)+'/catalog/assets/'+u(aid)+'/'+requested,post({
          request_id:crypto.randomUUID(),expected_revision:rev,confirm:true}));
        ctx.toast(requested==='trash'?'数据资产已移入回收站':'数据资产已恢复');
        if(requested==='trash')ctx.navigate(projectPath(project.id)+'/data');
        else await dataCatalogAsset(aid);
      }
      else if(action==='refresh-catalog'){await dataPage();}
      else if(action==='retry-publication'){
        await api('/'+u(project.id)+'/publish-jobs/'+u(el.dataset.job)+'/retry',{method:'POST'});
        ctx.toast('发布任务已重新排队');await dataPage();
      }
      else if(action==='quality-run'){
        const aid=el.dataset.asset;ctx.toast('正在执行全量质量检查…');
        await api('/'+u(project.id)+'/catalog/assets/'+u(aid)+'/quality/evaluate',{method:'POST'});
        await dataCatalogAsset(aid);
      }
      else if(action==='quality-decision'){
        const status=prompt('处理状态：acknowledged（带说明放行）或 resolved（已修复）','acknowledged');
        if(!status)return true;
        const reason=prompt('记录核实依据或实际处理说明（至少 4 个字）');if(!reason)return true;
        await api('/'+u(project.id)+'/quality/issues/'+u(el.dataset.issue)+'/decision',post({status,reason}));
        const aid=location.pathname.split('/').filter(Boolean)[4];await dataCatalogAsset(aid);
      }
      else if(action==='reproduce-modeling-draft'){
        const draft=await api('/'+u(project.id)+'/modeling/drafts/'+u(el.dataset.draftId)+'/reproduce',{method:'POST'});
        await window.TimePilotModeling.openDraft({ctx,projectId:project.id,
          assetId:draft.asset_id,draftId:draft.draft_id});
        ctx.toast('已建立可审阅的独立复现实验草稿');
      }
      else if(action==='open-modeling-draft'){
        if(!window.TimePilotModeling)throw Error('建模面板未加载');
        await window.TimePilotModeling.openDraft({ctx,projectId:project.id,
          assetId:el.dataset.assetId,draftId:el.dataset.draftId});
      }
      else if(action==='select-import-region')await showImportRegion(el.dataset.region);
      else if(action==='select-all-import'||action==='select-none-import'){
        document.querySelectorAll('[data-import-region]').forEach(item=>item.checked=action==='select-all-import');
      }
      else if(action==='publish-import-batch')await publishImportBatch();
      else if(action==='retry-import-batch'){
        await api('/'+u(project.id)+'/publish-batches/'+u(el.dataset.batch)+'/retry',{method:'POST'});
        await showPublishBatch(el.dataset.batch,true);
      }
      else if(action==='explain-import'){
        const slot=document.getElementById('tp-import-semantic-note');
        if(slot)slot.innerHTML='<p class="muted tiny">正在读取结构证据…</p>';
        const result=await api('/'+u(project.id)+'/imports/'+u(importInfo.import_id)+'/semantic-explain',post({
          use_model:!!document.getElementById('tp-import-use-model')?.checked}));
        if(slot)slot.innerHTML=`<div class="helper"><strong>表结构说明</strong><p>${esc(result.summary)}</p>${(result.semantic_notes||[]).map(n=>`<p class="muted tiny">${esc(n.note)}</p>`).join('')}${result.model_status==='fallback'?'<small>模型调用失败，已保留本地结构扫描结果。</small>':''}</div>`;
      }
      else if(action==='accept-import-region'){
        const rid=el.dataset.region;
        if(!confirm('已核对该区域的表头、边界、原始字段和原始记录。确认将当前结构用于发布？'))return true;
        await api('/'+u(project.id)+'/imports/'+u(importInfo.import_id)+'/regions/'+u(rid)+'/review',post({decision:'accepted',confirmed:true,reason:'已核对表头、列顺序、边界与原始样本'}));
        ctx.toast('已记录当前源指纹的人工结构确认');await showImportRegion(rid);
      }
      else if(action==='edit-import-region'){
        const rid=el.dataset.region;
        const region=(importCatalog?.files||[]).flatMap(f=>f.worksheets.flatMap(x=>x.regions)).find(r=>r.region_id===rid);
        if(!region)throw Error('无法找到当前数据区域');
        const entered=prompt('请输入实际表头所在行（会保留原始名称；需修正区域边界时可后续扩展）',String(region.header_row||region.bbox?.row_start||1));
        if(entered===null)return true;
        const h=Number(entered);
        if(!Number.isInteger(h)||h<1)throw Error('表头行号无效');
        await api('/'+u(project.id)+'/imports/'+u(importInfo.import_id)+'/regions/'+u(rid)+'/layout',{
          method:'PATCH',headers:{'Content-Type':'application/json'},
          body:JSON.stringify({header_row:h,confirm:true})});
        ctx.toast('已生成表格布局修订记录');await importDetail(importInfo.import_id);
      }

      else if(action==='retry-upload'||action==='cancel-upload'){
        const jid=location.pathname.split('/').filter(Boolean)[4];
        await api('/'+u(project.id)+'/imports/jobs/'+u(jid)+'/'+(action==='retry-upload'?'retry':'cancel'),{method:'POST'});
        await uploadJob(jid);
      }
      else if(action==='make-editable'){
        const aid=el.dataset.assetId||location.pathname.split('/').filter(Boolean)[3];
        if(!confirm('可编辑 BI 会生成独立的派生工作副本，原始标准数据版本保留。继续？'))return true;
        const result=await api('/'+u(project.id)+'/assets/'+u(aid)+'/editable',{method:'POST'});
        ctx.navigate(projectPath(project.id)+'/bi/native/'+u(result.asset_id));
      }
    }catch(e){ctx.toast(e.message)}return true;
  }
  async function submit(event){const form=event.target;if(!['project-create-form','project-upload-form','project-quality-form'].includes(form.id))return false;
    event.preventDefault();const submitter=form.querySelector('button[type="submit"]');if(submitter)submitter.disabled=true;
    try{
      if(form.id==='project-quality-form'){
        const aid=form.dataset.asset;
        const required_fields=[...form.querySelectorAll('[data-quality-required]:checked')].map(x=>x.value);
        const unique_key=form.elements.unique_key.value.split(',').map(x=>x.trim()).filter(Boolean);
        const max_gap_seconds=form.elements.max_gap_seconds.value.trim();
        await api('/'+u(project.id)+'/catalog/assets/'+u(aid)+'/quality/policy',post({
          required_fields,unique_key,ranges:{},max_missing_fraction:Number(form.elements.max_missing_fraction.value),
          time_field:form.elements.time_field.value||null,entity_field:form.elements.entity_field.value||null,
          max_gap_seconds:max_gap_seconds?Number(max_gap_seconds):null,require_monotonic:form.elements.require_monotonic.checked}));
        await api('/'+u(project.id)+'/catalog/assets/'+u(aid)+'/quality/evaluate',{method:'POST'});
        await dataCatalogAsset(aid);
      }else if(form.id==='project-create-form'){
        const payload=Object.fromEntries(new FormData(form));const p=await api('',post(payload));ctx.navigate(projectPath(p.id));
      }else if(form.id==='project-upload-form'){
        const fd=new FormData(form);const files=fd.getAll('files').filter(x=>x&&x.size>0);
        if(!files.length)throw new Error('请选择需要上传的数据文件');
        const data=new FormData();for(const file of files)data.append('files',file,file.name);
        const state=document.getElementById('project-upload-progress');if(state)state.textContent='正在安全接收文件；随后后台识别工作簿，可离开此页面。';
        const response=await api('/'+u(project.id)+'/imports/jobs',{method:'POST',body:data});
        ctx.toast('文件已收到，后台开始结构扫描');ctx.navigate(projectPath(project.id)+'/data/import-job/'+u(response.job_id));
      }
    }catch(e){ctx.toast('操作失败：'+e.message)}finally{if(submitter)submitter.disabled=false;}return true;
  }
  function change(event){
    if(event.target.id==='tp-field-search'){
      const q=event.target.value.trim().toLowerCase();
      document.querySelectorAll('[data-profile-search]').forEach(row=>row.hidden=!row.dataset.profileSearch.includes(q));
      return true;
    }
    if(event.target.closest('#project-upload-form input[type=file]')){
    const files=[...event.target.files];const el=document.getElementById('project-files');
    if(el)el.textContent=files.map(x=>x.name).join('、')||'尚未选择文件';return true;
  }return false;}
  window.TimePilotProjects={render,click,submit,change};
})();
