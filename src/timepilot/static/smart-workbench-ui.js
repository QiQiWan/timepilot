/* TimePilot v0.8.0: connected BI selections, immutable views and forecast drafts.
   Dependency-free; works when Perspective is not installed. */
(() => {
  'use strict';
  let ctx, importId, versionId, result=null, saved=[], drafts=[], activeView=null, activeDraft=null, seq=0;
  let selection={filters:{},date_from:null,date_to:null}, chosenSeries=new Set();
  const E=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const U=x=>encodeURIComponent(String(x));
  const fmt=x=>Number(x??0).toLocaleString('zh-CN');
  const digits=x=>Number(x??0).toLocaleString('zh-CN',{maximumFractionDigits:2});
  const fields=[['country','国家/地区'],['brand','品牌'],['vehicle','车型'],['source_name','数据来源'],['statistical_scope','统计口径']];
  const reasons={history_short:'历史数据长度不足',missing_period:'存在月份缺口',duplicate_period:'相同月份重复',missing_target:'目标数值缺失',invalid_target:'数值异常',missing_available_at:'没有真实发布时间',scope_unverified:'统计口径未核实',conflict_review:'来源数值冲突',duplicate_identical:'存在重合记录',invalid_period:'时间不是月初日期',non_numeric:'非数值内容'};
  const opts=data=>({method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
  const uri=p=>`/smart-import/imports/${U(importId)}/versions/${U(versionId)}/workbench${p}`;
  const api=(p,o={})=>ctx.req(uri(p),o);
  const block=t=>`<div class="wb-loading" role="status"><span class="smart-spinner"></span>${E(t)}</div>`;
  const issue=t=>`<div class="helper danger" role="alert">${E(t)}</div>`;
  function selectionFromUi(){
    const next={filters:{},date_from:document.getElementById('wb-from')?.value||null,date_to:document.getElementById('wb-to')?.value||null};
    for(const [field] of fields){const value=document.getElementById(`wb-filter-${field}`)?.value;if(value!==undefined&&value!=='__all__')next.filters[field]=[value];}
    if(chosenSeries.size)next.filters.series_id=[...chosenSeries];
    return next;
  }
  function shell(){
    return `<div class="smart-breadcrumb"><a href="/imports">智能导入</a> / <a href="/imports/${U(importId)}">数据版本</a> / BI 与预测</div>
    <div class="page-heading"><div><h1>数据分析与预测</h1><p>筛选业务系列，查看趋势和质量，保存分析视图后创建预测任务草稿。</p></div><a class="button button-small" href="/imports/${U(importId)}">返回数据版本</a></div>
    <div class="wb-layout"><section class="panel wb-filters"><div class="wb-title"><h2>筛选条件</h2><button type="button" class="button button-small" data-wb="reset">清空</button></div><p class="muted small">筛选只影响分析视图，原始数据不变。</p><div id="wb-facets">${block('正在加载维度...')}</div>
      <div class="wb-months"><label>起始月份<input type="month" id="wb-from" value="${E(selection.date_from||'')}"></label><label>结束月份<input type="month" id="wb-to" value="${E(selection.date_to||'')}"></label></div>
      <div class="wb-selections" id="wb-selected-series"></div></section>
    <div class="wb-main"><section class="panel"><div class="wb-title"><h2>数据概览</h2><span class="wb-help">指标随筛选条件更新</span></div><div id="wb-summary">${block('正在分析数据...')}</div></section>
    <section class="panel"><div class="wb-title"><h2>月度趋势</h2><span class="wb-help">按当前条件汇总</span></div><div id="wb-trend"></div></section>
    <section class="panel"><div class="wb-title"><h2>系列选择与覆盖率</h2><span class="wb-help">最多显示 80 个系列，支持勾选多个</span></div><div id="wb-series"></div></section>
    <section class="panel"><div class="wb-title"><h2>来源数据抽样</h2><span class="wb-help">保留原工作表及单元格位置</span></div><div id="wb-samples"></div></section>
    <section class="panel"><h2>保存分析视图</h2><p class="muted small">保存时固定数据版本、筛选条件和来源哈希；修改筛选条件不会改变已保存视图。</p>
      <div class="wb-save-row"><input id="wb-view-name" maxlength="100" placeholder="例如：中国 · 丰田 · 销量分析" aria-label="视图名称"><button type="button" class="button button-primary" data-wb="save-view">保存当前视图</button></div><div id="wb-view-status"></div><div id="wb-saved"></div></section>
    <section class="panel"><div class="wb-title"><h2>用于预测</h2><span class="wb-help">先保存视图，再生成任务草稿</span></div><div id="wb-forecast"></div></section>
    </div></div>`;
  }
  function facetOptions(field, values){
    const chosen=selection.filters[field]?.[0]??'__all__';
    const list=values?.items||[];
    const options=[...list];
    if(chosen!=='__all__'&&!options.some(x=>x.value===chosen))options.unshift({value:chosen,count:'—'});
    return `<select id="wb-filter-${E(field)}" data-wb-filter="${E(field)}" aria-label="${E(fields.find(x=>x[0]===field)?.[1])}">
      <option value="__all__" ${chosen==='__all__'?'selected':''}>全部</option>${options.map(v=>`<option value="${E(v.value)}" ${chosen===v.value?'selected':''}>${E(v.value||'（未填写）')} · ${Number.isFinite(Number(v.count))?fmt(v.count):'已选'}</option>`).join('')}</select>`;
  }
  function drawFacets(facets){
    document.getElementById('wb-facets').innerHTML=fields.map(([field,label])=>`<div class="wb-field"><label for="wb-filter-${field}">${E(label)}</label>${facetOptions(field,facets[field])}${facets[field]?.truncated?'<small class="muted">显示高频选项，可搜索更多值</small>':''}${(facets[field]?.truncated||field==='vehicle'||field==='brand')?`<div class="wb-find"><input id="wb-query-${E(field)}" type="search" placeholder="搜索${E(label)}" aria-label="搜索${E(label)}"><button type="button" class="button button-small" data-wb="search-facet" data-field="${E(field)}">查找</button></div>`:''}</div>`).join('');
    document.getElementById('wb-selected-series').innerHTML=chosenSeries.size?`已暂选 ${fmt(chosenSeries.size)} 个系列 <div class="wb-inline"><button class="button button-small" data-wb="apply-series" type="button">只看选中系列</button><button class="button button-small" data-wb="clear-series" type="button">取消系列选择</button></div>`:'<span class="muted small">还可在右侧勾选具体车型序列。</span>';
  }
  function kpi(label,number,help=''){
    return `<div class="wb-kpi"><small>${E(label)}</small><strong>${E(number)}</strong>${help?`<span>${E(help)}</span>`:''}</div>`;
  }
  function drawSummary(summary,warnings){
    const el=document.getElementById('wb-summary');
    el.innerHTML=`<div class="wb-kpis">${kpi('观测记录',fmt(summary.rows))}${kpi('数据系列',fmt(summary.series))}${kpi('有效月份覆盖',(summary.coverage*100).toFixed(1)+'%')}${kpi('缺失目标值',fmt(summary.missing))}${kpi('重复与冲突',fmt(summary.duplicates+summary.conflict))}${kpi('缺少发布时间',fmt(summary.unpublished))}</div>
      <div class="wb-coverage"><div class="wb-coverage-line"><span>有效目标值的月份覆盖</span><strong>${(summary.coverage*100).toFixed(1)}%</strong></div><div class="wb-track"><span style="width:${Math.min(100,Math.max(0,summary.coverage*100))}%"></span></div><small class="muted">以每个系列首末月份区间的自然月份为分母，有效数值月份为分子；记录月份覆盖为 ${(summary.calendar_coverage*100).toFixed(1)}%。</small></div>
      <div class="wb-warnings">${warnings.map(x=>`<p class="smart-warning">${E(x)}</p>`).join('')}</div>`;
  }
  function trendSVG(rows){
    if(!rows.length)return '<p class="muted">当前筛选没有月度数据。</p>';
    const available=rows.filter(x=>x.total!==null).slice(-240);
    if(!available.length)return '<p class="muted">当前条件下均为缺失值。</p>';
    const w=1000,h=270,left=72,right=22,top=25,bottom=46;
    const values=available.map(x=>Number(x.total)||0);
    const lo=Math.min(0,...values),hi=Math.max(0,...values);const delta=hi-lo||1;
    const x=i=>left+(w-left-right)*i/Math.max(1,available.length-1);
    const y=v=>top+(h-top-bottom)*(hi-v)/delta;
    const path=available.map((r,i)=>`${i?'L':'M'}${x(i).toFixed(2)} ${y(values[i]).toFixed(2)}`).join(' ');
    const labels=[0,.25,.5,.75,1].map(t=>{const v=lo+t*delta,yy=y(v);return `<line x1="${left}" y1="${yy}" x2="${w-right}" y2="${yy}" stroke="#e8edf5"/><text x="${left-10}" y="${yy+4}" text-anchor="end" fill="#60738d" font-size="12">${E(digits(v))}</text>`}).join('');
    const ticks=[0,.25,.5,.75,1].map(t=>{const i=Math.round(t*(available.length-1));return `<text x="${x(i)}" y="${h-12}" text-anchor="middle" fill="#61738b" font-size="12">${E(available[i].month)}</text>`}).join('');
    return `<div class="wb-plot"><svg viewBox="0 0 ${w} ${h}" role="img" aria-label="月度趋势折线图，${E(available[0].month)} 至 ${E(available.at(-1).month)}">${labels}<line x1="${left}" y1="${y(0)}" x2="${w-right}" y2="${y(0)}" stroke="#9aacbf"/><path d="${path}" fill="none" stroke="#2469c5" stroke-width="2.7" stroke-linejoin="round" stroke-linecap="round"/>${available.length<=48?available.map((r,i)=>`<circle cx="${x(i)}" cy="${y(values[i])}" r="2.5" fill="#2469c5"><title>${E(r.month)} · ${E(digits(values[i]))}</title></circle>`).join(''):''}${ticks}</svg></div><small class="muted">使用有效数值求和，包含负值；缺失目标不计入。涉及多个来源或口径时，必须先确认筛选条件。</small>`;
  }
  function seriesTable(rows){
    if(!rows.length)return '<p class="muted">当前条件下没有数据系列。</p>';
    return `<div class="smart-table-wrap wb-table"><table class="data-table"><thead><tr><th>选择</th><th>国家/品牌/车型</th><th>来源/口径</th><th>起止月份</th><th>覆盖</th><th>观测</th><th>缺失</th></tr></thead><tbody>${rows.map(s=>`<tr><td><input type="checkbox" data-wb-series="${E(s.series_id)}" ${chosenSeries.has(s.series_id)?'checked':''} aria-label="选择${E(s.vehicle)}"></td><td><b>${E(s.vehicle||'未命名车型')}</b><span class="wb-cell-meta">${E(s.country)} · ${E(s.brand)}</span></td><td>${E(s.source_name)}<span class="wb-cell-meta">${E(s.statistical_scope)}</span></td><td>${E(s.first_month?.slice(0,7))} — ${E(s.last_month?.slice(0,7))}</td><td>${(s.coverage*100).toFixed(0)}%</td><td>${fmt(s.records)}</td><td>${fmt(s.missing)}</td></tr>`).join('')}</tbody></table></div>`;
  }
  function samplesTable(rows){
    if(!rows.length)return '<p class="muted">暂无可显示的记录。</p>';
    return `<div class="smart-table-wrap wb-table"><table class="data-table"><thead><tr><th>月份</th><th>数值</th><th>车型</th><th>来源</th><th>原始位置</th><th>质量标记</th></tr></thead><tbody>${rows.slice(0,35).map(x=>`<tr><td>${E(x.timestamp)}</td><td>${E(x.target||'—')}</td><td>${E(x.vehicle||'—')}</td><td>${E(x.source_name)}</td><td title="${E(x.source_file)} / ${E(x.source_sheet)}">${E(x.source_sheet)} · ${E(x.source_cell)}</td><td>${E(x.quality_flags||'正常')}</td></tr>`).join('')}</tbody></table></div>`;
  }
  function savedPanel(){
    const el=document.getElementById('wb-saved');if(!el)return;
    el.innerHTML=`${activeView?`<p class="wb-current">当前已保存视图：<b>${E(activeView.name)}</b> <span>（只读）</span></p>`:'<p class="muted small">当前筛选尚未保存。</p>'}
    ${saved.length?`<div class="wb-list">${saved.map(v=>`<button type="button" class="wb-list-item ${activeView?.view_id===v.view_id?'is-active':''}" data-wb="load-view" data-id="${E(v.view_id)}"><span>${E(v.name)}</span><small>${E(v.created_at.slice(0,10))} · ${fmt(v.summary_at_save.rows)} 行</small></button>`).join('')}</div>`:''}`;
  }
  function draftPanel(){
    const panel=document.getElementById('wb-forecast');if(!panel)return;
    panel.innerHTML=`${activeView?`<p class="wb-current">已关联视图：<b>${E(activeView.name)}</b></p>
      <form id="wb-draft-form" class="wb-draft-form"><div class="wb-draft-grid"><label>任务名称<input name="name" required maxlength="120" value="${E(activeView.name)} · 预测"></label>
      <label>预测跨度<select name="forecast_horizon"><option value="1">1 个月</option><option value="3" selected>3 个月</option><option value="6">6 个月</option><option value="12">12 个月</option></select></label>
      <label>发布时间假定（天）<input name="publication_lag_days" type="number" min="0" max="3650" placeholder="未知则不填写"></label>
      <label>不合格系列的处理<select name="series_policy"><option value="all">全部纳入，发现问题则阻断</option><option value="eligible_only">只保留通过检查的系列</option></select></label></div>
      <small class="muted">不填写发布时间时，不会伪造可用时间。“仅合格系列”是明确选择，将在草稿中记录排除原因；生成草稿不会启动训练。</small>
      <div class="smart-actions"><button type="submit" class="button button-primary">生成预测任务草稿</button></div></form>`:'<p class="muted">请先保存当前筛选视图，再生成任务草稿。</p>'}
      <div id="wb-draft-status"></div>${activeDraft?draftSummary(activeDraft):''}
      ${drafts.length?`<div class="wb-list"><h3>历史草稿</h3>${drafts.map(d=>`<button class="wb-list-item" data-wb="open-draft" data-id="${E(d.draft_id)}" type="button"><span>${E(d.name)}</span><small>${d.status==='dataset_ready'?'已生成训练数据':d.status==='ready'?'可准备数据':'需补充条件'}</small></button>`).join('')}</div>`:''}`;
  }
  function draftSummary(d){
    const p=d.preflight||{};
    return `<div class="wb-draft-result"><h3>${E(d.name)}</h3><div class="wb-draft-metrics"><span>筛选 ${fmt(p.matched_rows)} 行</span><span>合格系列 ${fmt(p.eligible_series)}</span><span>最短历史 ${fmt(p.min_required_history)} 期</span></div>
      ${p.blockers?.map(x=>`<p class="smart-warning">${E(x)}</p>`).join('')||''}${p.warnings?.map(x=>`<p class="wb-note">${E(x)}</p>`).join('')||''}
      ${Object.keys(p.reason_counts||{}).length?`<p class="muted small">系列问题：${Object.entries(p.reason_counts).map(([k,v])=>E(reasons[k]||k)+' '+fmt(v)).join('；')}</p>`:''}
      ${d.status==='ready'?`<button type="button" class="button button-primary" data-wb="materialize" data-id="${E(d.draft_id)}">准备标准训练数据集</button>`:''}
      ${d.status==='dataset_ready'?`<div class="wb-next"><p>数据集已建立，训练尚未启动。请核对模型、训练预算和回测设置。</p><a class="button button-primary" href="/new?dataset=${U(d.dataset_id)}&draft=${U(d.draft_id)}&name=${U(d.name)}&horizon=${U(d.options.forecast_horizon)}">前往任务确认</a></div>`:''}</div>`;
  }
  async function reload(){
    const id=++seq, summary=document.getElementById('wb-summary');if(!summary)return;
    summary.innerHTML=block('正在更新多维统计...');
    try{
      const data=await api('/analyze',opts(selection));if(id!==seq)return;
      result=data;drawFacets(data.facets);drawSummary(data.summary,data.warnings);
      document.getElementById('wb-trend').innerHTML=(data.summary.distinct_scopes>1||data.summary.distinct_sources>1)?'<p class="smart-warning">当前混合多种统计口径或数据来源。请选择单一口径和来源后再查看合计趋势，避免重复或错误汇总。</p>':trendSVG(data.trend);
      document.getElementById('wb-series').innerHTML=seriesTable(data.series);
      document.getElementById('wb-samples').innerHTML=samplesTable(data.samples);
      savedPanel();draftPanel();
    }catch(ex){if(id===seq)summary.innerHTML=issue(ex.message);}
  }
  async function render(context){
    ctx=context;result=null;activeView=null;activeDraft=null;chosenSeries=new Set();selection={filters:{},date_from:null,date_to:null};
    const m=(context.route||location.pathname).match(/^\/imports\/([^/]+)\/versions\/([^/]+)\/workbench\/?$/);
    if(!m){ctx.root.innerHTML=issue('分析页面路径无效');return}
    importId=m[1];versionId=m[2];ctx.root.innerHTML=shell();
    try{
      let index=await api('/index-status');
      if(index.status!=='ready'){
        if(index.status==='unbuilt'||index.status==='failed')index=await api('/build-index',{method:'POST'});
        while(['queued','running'].includes(index.status)){
          const el=document.getElementById('wb-summary');
          if(!el||!location.pathname.includes('/workbench'))return;
          el.innerHTML=block(`首次创建 BI 分析索引：${fmt(index.indexed_rows)} / ${fmt(index.expected_rows)} 条记录`);
          await new Promise(ok=>setTimeout(ok,800));
          index=await api('/index-status');
        }
        if(index.status!=='ready')throw new Error(index.error||'分析索引创建失败');
      }
      const responses=await Promise.all([api('/views'),api('/forecast-drafts')]);saved=responses[0];drafts=responses[1];
      const id=new URLSearchParams(location.search).get('view_id');
      if(id){
        const target=saved.find(v=>v.view_id===id);
        if(target){const record=await api('/views/'+U(id));activeView=record;
          selection={...record.selection,filters:{...record.selection.filters}};
          chosenSeries=new Set(selection.filters.series_id||[]);
          const from=document.getElementById('wb-from'),to=document.getElementById('wb-to');
          if(from)from.value=selection.date_from||'';if(to)to.value=selection.date_to||'';
        }
      }
      await reload();
    }catch(ex){ctx.root.innerHTML=issue(ex.message)}
  }
  function changed(event){
    if(event.target.matches('[data-wb-series]')){
      const id=event.target.dataset.wbSeries;
      if(event.target.checked){if(chosenSeries.size>=200){event.target.checked=false;ctx.toast('最多同时选择 200 个系列');return true}chosenSeries.add(id)}else chosenSeries.delete(id);
      const target=document.getElementById('wb-selected-series');if(target)target.innerHTML=chosenSeries.size?`已暂选 ${fmt(chosenSeries.size)} 个系列 <div class="wb-inline"><button class="button button-small" data-wb="apply-series" type="button">只看选中系列</button><button class="button button-small" data-wb="clear-series" type="button">取消系列选择</button></div>`:'';
      return true;
    }
    if(event.target.matches('[data-wb-filter]')||['wb-from','wb-to'].includes(event.target.id)){
      chosenSeries.clear();selection=selectionFromUi();activeView=null;activeDraft=null;void reload();return true;
    }
    return false;
  }
  async function click(ev){
    const btn=ev.target.closest('[data-wb]');if(!btn)return;
    ev.preventDefault();const action=btn.dataset.wb;
    if(action==='reset'){
      selection={filters:{},date_from:null,date_to:null};chosenSeries.clear();activeView=null;activeDraft=null;
      const from=document.getElementById('wb-from'),to=document.getElementById('wb-to');if(from)from.value='';if(to)to.value='';await reload();return;
    }
    if(action==='clear-series'||action==='apply-series'){
      if(action==='clear-series')chosenSeries.clear();
      else if(!chosenSeries.size){ctx.toast('请先在系列表格中勾选系列');return}
      selection=selectionFromUi();if(action==='clear-series')delete selection.filters.series_id;
      activeView=null;activeDraft=null;await reload();return;
    }
    if(action==='search-facet'){
      const field=btn.dataset.field;
      if(!fields.some(([name])=>name===field))return;
      const q=document.getElementById(`wb-query-${field}`)?.value.trim()||'';
      try{
        const data=await api('/facets/'+U(field)+'?q='+U(q),opts(selection));
        const select=document.getElementById(`wb-filter-${field}`);const chosen=select?.value||'__all__';
        if(select)select.innerHTML=facetOptions(field,data).match(/<select[^>]*>([\s\S]*)<\/select>/)?.[1]||'';
        if(select)select.value=[...select.options].some(x=>x.value===chosen)?chosen:'__all__';
      }catch(e){ctx.toast(e.message)}return;
    }
    if(action==='save-view'){
      const name=document.getElementById('wb-view-name')?.value.trim();if(!name){ctx.toast('请先输入视图名称');return}
      btn.disabled=true;
      try{
        const data=await api('/views',opts({name,selection}));activeView=data;activeDraft=null;
        saved=await api('/views');savedPanel();draftPanel();ctx.toast('筛选视图已保存');
      }catch(e){ctx.toast('保存失败：'+e.message)}finally{btn.disabled=false}return;
    }
    if(action==='load-view'){
      try{
        const view=await api('/views/'+U(btn.dataset.id));activeView=view;activeDraft=null;
        selection={...view.selection,filters:{...view.selection.filters}};
        chosenSeries=new Set(selection.filters.series_id||[]);
        const from=document.getElementById('wb-from'),to=document.getElementById('wb-to');if(from)from.value=selection.date_from||'';if(to)to.value=selection.date_to||'';
        await reload();ctx.toast('已恢复保存的筛选视图');
      }catch(e){ctx.toast(e.message)}return;
    }
    if(action==='open-draft'){
      try{activeDraft=await api('/forecast-drafts/'+U(btn.dataset.id));draftPanel();}
      catch(e){ctx.toast(e.message)}return;
    }
    if(action==='materialize'){
      if(!confirm('确认将当前草稿中通过检查的数据生成为标准训练数据集？该操作不会启动模型训练。'))return;
      btn.disabled=true;
      try{
        activeDraft=await api('/forecast-drafts/'+U(btn.dataset.id)+'/materialize',{method:'POST'});
        drafts=await api('/forecast-drafts');draftPanel();ctx.toast('标准训练数据集已建立，尚未启动训练');
      }catch(e){ctx.toast('准备数据失败：'+e.message)}finally{btn.disabled=false}return;
    }
  }
  async function submit(ev){
    if(ev.target.id!=='wb-draft-form')return;
    ev.preventDefault();if(!activeView){ctx.toast('请先保存筛选视图');return}
    const form=ev.target,button=form.querySelector('button[type=submit]');const data=new FormData(form);
    const request={name:String(data.get('name')),forecast_horizon:Number(data.get('forecast_horizon')),
      series_policy:String(data.get('series_policy'))};
    if(data.get('publication_lag_days')!=='')request.publication_lag_days=Number(data.get('publication_lag_days'));
    button.disabled=true;document.getElementById('wb-draft-status').innerHTML=block('正在检查连续月份、历史长度和发布时间...');
    try{
      activeDraft=await api('/views/'+U(activeView.view_id)+'/forecast-drafts',opts(request));
      drafts=await api('/forecast-drafts');draftPanel();ctx.toast(activeDraft.status==='ready'?'已生成可准备训练数据的草稿':'草稿已保存；需要处理检查项');
    }catch(e){const slot=document.getElementById('wb-draft-status');if(slot)slot.innerHTML=issue(e.message);}
    finally{button.disabled=false}
  }
  window.TimePilotWorkbench={render,changed,click,submit};
})();
