/* v0.34.0: bounded, model-optional, source-pinned automatic data analysis. */
(() => {
  'use strict';
  let current = null, token = 0, timer = null, previewTimer = null;
  const e = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const u = encodeURIComponent;
  const num = v => Number(v ?? 0).toLocaleString('zh-CN', {maximumFractionDigits:2});
  const post = body => ({method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const link = (p, aid, vid, suffix) => '/projects/' + u(p) + suffix + '?asset=' + u(aid) + '&version=' + u(vid);
  function dispose() {
    ++token;
    if(timer)clearTimeout(timer);
    if(previewTimer)clearTimeout(previewTimer);
    timer=null;previewTimer=null;current=null;
    window.TimePilotDataDashboard372?.dispose?.();
  }
  function previewActive(z,requestToken){
    if(current!==z||requestToken!==token||!z.host?.isConnected)return false;
    const workspace=z.host.closest?.('#tp-analysis-workspace');
    return !workspace || workspace.dataset.section==='features';
  }
  async function ensureFeaturePreview(z,requestToken){
    if(!previewActive(z,requestToken))return;
    const epoch=z.previewEpoch||0;
    if(z.previewPending===epoch)return;
    z.previewPending=epoch;
    const valid=()=>previewActive(z,requestToken)&&(z.previewEpoch||0)===epoch;
    try{
      const prefix=`/projects/${u(z.pid)}/feature-lab/workspace`;
      let journey=await z.ctx.req(`${prefix}?asset_id=${u(z.aid)}&version_id=${u(z.vid)}`);
      // The user may have left FeatureLab while the status request was in flight.
      if(!valid())return;
      if(journey.feature_preview?.status==='not_started' && !z.previewStartAttempted){
        z.previewStartAttempted=true;
        try{journey=await z.ctx.req(prefix+'/start',post({asset_id:z.aid,version_id:z.vid}));}
        catch(error){z.previewStartAttempted=false;throw error;}
        if(!valid())return;
      }
      z.journey=journey;draw();
      if(['profiling','previewing','materializing','evaluating'].includes(journey.feature_preview?.status)){
        if(previewTimer)clearTimeout(previewTimer);
        previewTimer=setTimeout(()=>loadJourneyOnly(requestToken),2800);
      }
    }catch(ex){
      if(valid()){
        z.previewError=String(ex?.message||ex);
        // Basic data diagnosis remains available when optional previews fail.
        draw();
      }
    }finally{if(z.previewPending===epoch)z.previewPending=null;}
  }
  function chartSVG(chart) {
    const points = chart?.points || [];
    if (!points.length) return '<p class="muted">此字段暂无可绘制的有效观测。</p>';
    const vals=points.map(x => {
      const v=chart?.mode==='series'?x.value:(x.count??x.value);
      return v==null||v===''||!Number.isFinite(Number(v))?null:Number(v);
    });
    if (chart.mode==='series') {
      const safe=n=>n==null||n===''||!Number.isFinite(Number(n))?null:Number(n);
      const available=points.flatMap(x=>[safe(x.min),safe(x.max),safe(x.value)]).filter(x=>x!==null);
      if(!available.length)return '<p class="muted">该时间范围没有有效数值。</p>';
      const lo=Math.min(...available),hi=Math.max(...available),scale=(hi-lo)||1;
      const pts=vals.map((v,i)=>v===null?null:[43+(i/(Math.max(1,vals.length-1)))*684,190-(v-lo)/scale*155]);
      let segments=[],part=[];
      for(const p of pts){if(p===null){if(part.length)segments.push(part);part=[];}else part.push(p.map(x=>x.toFixed(1)).join(','));}
      if(part.length)segments.push(part);
      // The ends and midpoint are actual bucket timestamps/order values; unlike
      // an unlabeled chart, users can locate changes without opening raw SQL.
      const tickIndices=[0,Math.floor((points.length-1)/2),points.length-1]
        .filter((x,i,all)=>all.indexOf(x)===i);
      const ticks=tickIndices.map((i,ix)=>{
        const raw=String(points[i].x??'');
        const value=raw.length>22?raw.slice(0,19)+'…':raw;
        const x=43+(i/Math.max(1,points.length-1))*684;
        const anchor=ix===0?'start':ix===tickIndices.length-1?'end':'middle';
        return `<line x1="${x.toFixed(1)}" x2="${x.toFixed(1)}" y1="190" y2="196" stroke="#9aafc7"/><text x="${x.toFixed(1)}" y="211" text-anchor="${anchor}" font-size="10">${e(value)}</text>`;
      }).join('');
      const extremes=points.map((p,i)=>{
        const x=43+(i/(Math.max(1,points.length-1)))*684;
        const a=safe(p.min),b=safe(p.max);
        if(a===null||b===null)return '';
        return `<line x1="${x.toFixed(1)}" y1="${(190-(a-lo)/scale*155).toFixed(1)}" x2="${x.toFixed(1)}" y2="${(190-(b-lo)/scale*155).toFixed(1)}" stroke="#cf7b34" stroke-width="1.8"><title>${e(p.x)}—${e(p.x_end)} 最小 ${e(num(a))} 最大 ${e(num(b))} (${e(p.count)} 条)</title></line>`;
      }).join('');
      return `<svg viewBox="0 0 760 235" role="img" aria-label="${e(chart.field_name)} 时间序列真实采样" class="tp-da-chart"><line x1="43" y1="190" x2="727" y2="190" stroke="#9aafc7"/>${extremes}${segments.map(s=>`<polyline fill="none" stroke="#2867c3" stroke-width="2" points="${s.join(' ')}"/>`).join('')}<text x="8" y="30">${e(num(hi))}</text><text x="8" y="190">${e(num(lo))}</text>${ticks}<text x="250" y="229">${e(chart.order_field||'时间 / 已确认顺序')}</text></svg>`;
    }
    const max=Math.max(1,...vals.filter(x=>x!==null));
    const w=684/points.length, col=Math.max(2,w-6);
    const bars=points.map((x,i)=>{
      const v=vals[i]||0,h=Math.max(0,156*v/max),px=43+i*w+3,label=String(x.label??'');
      return `<rect x="${px.toFixed(2)}" y="${(190-h).toFixed(2)}" width="${col.toFixed(2)}" height="${h.toFixed(2)}" fill="#3979cf" rx="2"><title>${e(label)}：${e(num(v))}</title></rect>${i%Math.max(1,Math.ceil(points.length/9))===0?`<text x="${(px+col/2).toFixed(2)}" y="209" text-anchor="middle">${e(label.slice(0,9))}</text>`:''}`;
    }).join('');
    return `<svg viewBox="0 0 760 235" role="img" aria-label="${e(chart.field_name)} 实际数据统计" class="tp-da-chart"><line x1="43" y1="190" x2="727" y2="190" stroke="#9aafc7"/>${bars}<text x="7" y="30">${e(num(max))}</text><text x="7" y="190">0</text><text x="270" y="231">${e(chart.mode==='category'?'类别':'数值区间')}</text></svg>`;
  }
  function narrative(chart) {
    if(!chart)return '';
    const x=chart.explanation||{};
    const scope=x.scope||{},axis=x.axes||{};
    const rows=chart.points||[];
    const series=chart.mode==='series';
    const n=scope.records??chart.population_count;
    const nums=rows.map(r=>Number(r.value??r.count)).filter(Number.isFinite);
    const summary=series?
      `这里按${e(chart.order_field||'已经确认的时间/顺序')}将${n==null?'当前':e(n)+' 条'}记录分成 ${rows.length} 个区间。蓝线表示区间均值，橙色线段表示该段的最高、最低观测值。`:
      chart.mode==='category'?'每个柱子代表一种类别，柱子越高，属于该类别的记录越多。':'每个柱子代表一个数值范围，柱子越高，落在这个范围内的记录越多。';
    const range=nums.length?`本次图中统计值介于 ${e(num(Math.min(...nums)))} 和 ${e(num(Math.max(...nums)))} 之间。`:'当前没有可统计的有限数值。';
    return `<div class="tp-da-insight" data-testid="chart-explanation"><h3>怎么看这张图</h3><p>${summary}</p>
      <p>${range}</p>${series?'<p>先确认记录先后顺序；包含多台设备时请先选定设备，以免不同设备的数值连成一条曲线。</p>':''}
      <p><b>下一步：</b>${series?'查看突变区间对应的原始记录，确认是设备变化还是数据异常。':'查看占比明显偏高的区间，结合业务含义分析。'}</p>
      <details><summary>统计定义、限制和原始依据</summary><p>${e(x.what_is_shown||'按当前已计算数据汇总。')}</p>
      <p>横轴：${e(axis.x||'—')} · 纵轴：${e(axis.y||'—')} · ${e(n??'—')} 条记录${scope.start!=null?' · '+e(scope.start)+' 至 '+e(scope.end):''}</p>
      ${(x.observations||[]).map(o=>`<p>${e(o.text)}</p>`).join('')}
      ${(x.cautions||[]).length?`<p class="tp-da-caution">${e(x.cautions.join('；'))}</p>`:''}
      <small>来源 SHA256：${e(chart.source_sha256||'—')} · 字段 ${e(chart.field_id||'')} · 契约版本 ${e(chart.contract_revision||'—')}</small></details></div>`;
  }
  function recommendation(x) {
    const code=x.code||x.action;
    if(code==='inspect_quality'||x.action==='view_quality')return ['quality','查看质量问题'];
    if(code==='confirm_time')return ['features','确认时间字段'];
    if(code==='temporal_features')return ['features','查看历史特征'];
    if(code==='evaluate')return ['outputs','查看评价条件'];
    return ['features','查看候选特征'];
  }
  function draw() {
    if(!current)return;
    const {host,pid,assets,versions,aid,vid,doc,chart,chartError,fieldId,mode,journey}=current;
    if(!host.isConnected)return;
    const fields=doc?.profile?.fields||[];
    const choice=fields.find(f=>f.field_id===fieldId);
    const status=doc?.status||'not_started',ready=status==='ready';
    const summary=doc?.profile;
    // Compact multi-field quality visualization comes from the already
    // completed full-scope profile; no extra SQL/remote model call is needed.
    const missing=(doc?.charts?.missing||[]).filter(x=>Number.isFinite(Number(x.value)))
      .sort((a,b)=>Number(b.value)-Number(a.value)).slice(0,8);
    const missingPreview=missing.length?`<div class="tp-da-quality-overview" aria-label="字段缺失比例概览"><h3>字段缺失比例</h3><div class="tp-da-quality-bars">${missing.map(x=>{
      const value=Math.max(0,Math.min(100,Number(x.value)));
      return `<div class="tp-da-quality-line"><span title="${e(x.label)}">${e(x.label)}</span><div class="tp-da-quality-track"><div style="width:${value}%"></div></div><b>${e(value.toFixed(1))}%</b></div>`;
    }).join('')}</div><p class="muted small">以上来自当前数据版本的字段画像；完整列清单和异常记录可在数据管理中查看。</p></div>`:'';
    // Real workspace: render only the active task, not three hidden dashboards.
    // The stand-alone legacy renderer below remains for old bookmarks/tests.
    const task=host.closest?.('#tp-analysis-workspace')?.dataset.section;
    if(['overview','quality','statistics'].includes(task)){
      const context=`<div class="tp-da-context"><label>数据 <select data-da="asset" aria-label="选择数据">${assets.map(a=>`<option value="${e(a.native_asset_id)}" ${a.native_asset_id===aid?'selected':''}>${e(a.asset?.name||a.native_asset_id)}</option>`).join('')}</select></label><label>版本 <select data-da="version" aria-label="选择版本">${versions.map(v=>`<option value="${e(v.version_id)}" ${v.version_id===vid?'selected':''}>${e(v.version_id==='base'?'原始版本':v.version_id.slice(0,12))}</option>`).join('')}</select></label></div>`;
      const top=`<div class="tp-da-status" role="status"><strong>${e(ready?'数据分析完成':'数据准备中')}</strong><span>${e(num(doc?.row_count??0))} 行 · ${e(num(doc?.column_count??0))} 列 · 当前数据保持版本固定</span></div>`;
      let content='';
      if(!ready){
        content='<div class="tp-da-pending">当前数据画像正在生成，请在任务中心检查状态；完成后刷新当前页面。</div>';
      }else if(task==='overview'){
        content=`<section class="tp-da-overview" id="tp-analysis-overview"><h2>数据概况</h2><p>来源：${e(assets.find(a=>a.native_asset_id===aid)?.asset?.name||aid)} · 当前版本 ${e(vid)}</p><div class="tp-da-overview-metrics"><article><small>记录数</small><strong>${e(num(doc.row_count))}</strong></article><article><small>字段数</small><strong>${e(num(doc.column_count))}</strong></article><article><small>优先关注问题</small><strong>${e(num(doc.findings?.length||0))}</strong></article></div><p>${e((doc.findings||[])[0]?.summary||'未发现需要立即提示的严重质量问题。')}</p><div class="tp-da-overview-actions"><button type="button" class="button" data-da-action="quality">质量检查</button><button type="button" class="button button-primary" data-da-action="statistics">统计图表</button></div></section>`;
      }else if(task==='quality'){
        const q=(doc?.profile?.fields||[]).filter(f=>f.field_id).slice(0,100);
        content=`<section class="tp-da-summary" id="tp-analysis-quality"><h2>数据质量检查</h2><p class="muted">本页只展示当前版本的质量问题与字段扫描，不执行统计图表查询。未经业务定义的数值范围不自动判定为故障。</p><div class="tp-da-findings">${(doc.findings||[]).length?(doc.findings||[]).map(x=>`<p>${e(x.summary)}</p>`).join(''):'<p>当前画像没有需要优先提示的质量异常。</p>'}</div>${missingPreview}<div class="tp-da-table-wrap"><table class="data-table tp-da-quality-table"><thead><tr><th>字段</th><th>NULL 比例</th><th>扫描状态</th></tr></thead><tbody>${q.map(f=>`<tr><td>${e(f.source_name||f.name)}</td><td>${f.null_rate==null?'待验证':e((Number(f.null_rate)*100).toFixed(2))+'%'}</td><td>${e(f.quality_status||'完成字段画像')}</td></tr>`).join('')}</tbody></table></div><p class="muted small">NaN/Inf 与数值转换问题需在“统计图表”的字段表中单独核对。</p></section>`;
      }else{
        content='<section class="tp-da-dashboard" id="tp-analysis-dashboard"><div id="tp-da-dashboard" role="region" aria-label="多维数据统计"></div></section>';
      }
      host.innerHTML=`<div class="tp-da-workbench" data-testid="data-analysis-workbench">${context}${top}${content}</div>`;
      if(ready && task==='statistics'){
        window.TimePilotDataDashboard372?.mount(host.querySelector('#tp-da-dashboard'),{
          ctx:current.ctx,pid,aid,vid,source_sha256:doc.source_sha256,
          contract_revision:doc.contract_revision,fields:doc.profile?.fields||[]});
      }
      return;
    }
    host.innerHTML=`<div class="tp-da-workbench" data-testid="data-analysis-workbench">
      <div class="tp-da-context"><label>数据 <select data-da="asset" aria-label="选择数据">${assets.map(a=>`<option value="${e(a.native_asset_id)}" ${a.native_asset_id===aid?'selected':''}>${e(a.asset?.name||a.native_asset_id)}</option>`).join('')}</select></label><label>版本 <select data-da="version" aria-label="选择版本">${versions.map(v=>`<option value="${e(v.version_id)}" ${v.version_id===vid?'selected':''}>${e(v.version_id==='base'?'原始版本':v.version_id.slice(0,12))}</option>`).join('')}</select></label><span class="tp-da-workspace-status">自动分析：${e(journey?.feature_preview?.status||'准备中')} · 候选 ${e(journey?.feature_preview?.candidate_count||0)} 项</span></div>
      <div class="tp-da-status" role="status"><strong>${e(({ready:'数据分析完成',not_started:'准备分析',queued:'等待分析任务',running:'正在分析数据',failed_retryable:'分析失败，可检查后重试',failed_terminal:'分析失败',cancelled:'任务已取消'})[status]||status)}</strong><span>${doc?.row_count===undefined?'':e(num(doc.row_count)+' 行 · '+num(doc.column_count)+' 列')} · ${e(doc?.job?.error||'原始数据保持不变')}</span></div>
      ${ready?`<section class="tp-da-overview" id="tp-analysis-overview"><h2>数据概况</h2><p>当前分析对象为 ${e(assets.find(a=>a.native_asset_id===aid)?.asset?.name||aid)}，版本 ${e(vid==='base'?'原始版本':vid)}。数据已完成基础画像，可分别检查数据质量、或进入多字段统计图表。</p><div class="tp-da-overview-metrics"><article><small>记录总数</small><strong>${e(num(doc.row_count))}</strong></article><article><small>字段总数</small><strong>${e(num(doc.column_count))}</strong></article><article><small>需要检查</small><strong>${e(num(doc.findings?.length||0))}</strong></article></div><p class="muted small">${e((doc.findings||[])[0]?.summary||'当前没有待处理的高优先级质量提示。')}</p><div class="tp-da-overview-actions"><button type="button" class="button" data-da-action="quality">检查质量问题</button><button type="button" class="button button-primary" data-da-action="statistics">打开多字段仪表盘</button></div></section><section class="tp-da-dashboard" id="tp-analysis-dashboard"><div id="tp-da-dashboard"></div></section><section class="tp-da-summary" id="tp-analysis-quality"><h2>数据概况与质量</h2><div class="tp-da-metrics"><div><small>记录数</small><strong>${e(num(doc.row_count))}</strong></div><div><small>字段数</small><strong>${e(num(doc.column_count))}</strong></div><div><small>实际扫描</small><strong>${e(num(summary?.rows_scanned))}</strong></div><div><small>需关注问题</small><strong>${e(num(doc.findings?.length||0))}</strong></div></div><div class="tp-da-findings">${(doc.findings||[]).length?doc.findings.map(x=>`<p>${e(x.summary)}</p>`).join(''):'<p>基础检查未发现需要优先提示的问题。可继续查看字段分布。</p>'}</div>${missingPreview}</section>
      <section class="tp-da-viz" id="tp-analysis-statistics"><div class="tp-da-viz-heading"><h2>字段统计与可视化</h2><label>字段 <select data-da="field" aria-label="查看字段">${fields.map(f=>`<option value="${e(f.field_id)}" ${f.field_id===fieldId?'selected':''}>${e(f.source_name)}</option>`).join('')}</select></label><label>图表 <select data-da="mode"><option value="distribution" ${mode==='distribution'?'selected':''}>数值分布</option><option value="category" ${mode==='category'?'selected':''}>类别计数</option><option value="series" ${mode==='series'?'selected':''} ${!(doc?.profile?.topology?.safe_for_windows)?'disabled':''}>时间趋势（需确认时间与设备顺序）</option></select></label></div>
      ${mode==='series'?`<div class="tp-da-series-controls"><label>分组字段 <select data-da="entity-field" aria-label="选择分组字段"><option value="">自动选择</option>${(chart?.entity_field_options||[]).map(x=>`<option value="${e(x.value)}" ${current.entityField===x.value?'selected':''}>${e(x.label)}</option>`).join('')}</select></label><label>设备 <select data-da="entity" aria-label="选择设备"><option value="" ${!current.entityValue?'selected':''}>${chart?.entity_value?'默认设备：'+e(chart.entity_value):'按记录数选择设备'}</option>${(chart?.entity_options||[]).map(x=>`<option value="${e(x.value)}" ${String(current.entityValue)===String(x.value)?'selected':''}>${e(x.value)} · ${e(x.row_count)} 条</option>`).join('')}</select></label><label>起始 <input type="text" data-da="start" value="${e(current.start||'')}" placeholder="开始时间/序号" aria-label="开始时间"></label><label>结束 <input type="text" data-da="end" value="${e(current.end||'')}" placeholder="结束时间/序号" aria-label="结束时间"></label><label>精度 <select data-da="resolution">${[60,120,240].map(n=>`<option value="${n}" ${current.resolution===n?'selected':''}>${n} 桶</option>`).join('')}</select></label></div>`:''}
      <div class="tp-da-chart-host">${chartError?`<p class="helper warning">${e(chartError)}</p>`:chart?chartSVG(chart):'<p class="muted">正在读取真实统计图表…</p>'}</div>${narrative(chart)}</section>
      <section class="tp-da-next"><h2>本次发现与建议</h2>${(doc.recommendations||[]).map(x=>{const [target,label]=recommendation(x);return `<div class="tp-da-recommendation"><div><strong>${e(x.title)}</strong><p>${e(x.reason)}</p></div><button type="button" class="button button-small" data-da-action="${target}">${label}</button></div>`}).join('')}<div class="tp-da-actions"><a class="button" href="/projects/${u(pid)}/bi/native/${u(aid)}">在 BI 深入查看</a></div></section>`:
      `<div class="tp-da-pending"><p>${e(status==='not_started'?'将自动检查数据结构、字段缺失和分布。':'分析任务在服务端运行，离开页面后仍可恢复结果。')}</p>${['failed_terminal','failed_retryable','cancelled'].includes(status)?'<p>请查看任务中心中的失败详情，确认来源及依赖后再重试。</p>':''}</div>`}
    </div>`;
    if(ready && host.closest?.('#tp-analysis-workspace')?.dataset.section==='statistics' && window.TimePilotDataDashboard372 && typeof host.querySelector==='function'){
      window.TimePilotDataDashboard372.mount(host.querySelector('#tp-da-dashboard'),{
        ctx:current.ctx,pid,aid,vid,source_sha256:doc.source_sha256,
        contract_revision:doc.contract_revision,fields:doc.profile?.fields||[]});
    }
  }
  function urlNext(aid,vid,field,mode){
    const p=new URL(window.location.href);p.searchParams.set('asset',aid);p.searchParams.set('version',vid);p.searchParams.delete('field');p.searchParams.delete('mode');
    if(field)p.searchParams.set('da_field',field);else p.searchParams.delete('da_field');
    if(mode)p.searchParams.set('da_mode',mode);else p.searchParams.delete('da_mode');
    return p.pathname+p.search;
  }
  async function loadChart(requestToken){
    const z=current;if(!z || !z.doc || z.doc.status!=='ready')return;
    const fields=z.doc.profile?.fields||[];
    if(!fields.length){z.chart=null;draw();return;}
    if(!fields.some(f=>f.field_id===z.fieldId)){
      const numeric=f=>/^(?:U?BIGINT|U?INTEGER|U?SMALLINT|TINYINT|DOUBLE|FLOAT|REAL|DECIMAL|NUMERIC|HUGEINT)/i.test(f.physical_type||'');
      z.fieldId=(fields.find(f=>numeric(f)&&!(/^(time|timestamp|datetime|date|日期|时间|采集时间)$/i.test(f.source_name||'')))||
        fields.find(f=>numeric(f))||fields[0]).field_id;
    }
    const field=fields.find(f=>f.field_id===z.fieldId);
    const numeric=/^(?:U?BIGINT|U?INTEGER|U?SMALLINT|TINYINT|DOUBLE|FLOAT|REAL|DECIMAL|NUMERIC|HUGEINT)/i.test(field?.physical_type||'');
    if (!numeric && z.mode!=='category') z.mode='category';
    z.chart=null;z.chartError='';draw();
    try{
      const chart=await z.ctx.req(`/projects/${u(z.pid)}/feature-lab/data-analysis/chart?asset_id=${u(z.aid)}&version_id=${u(z.vid)}&field_id=${u(z.fieldId)}&mode=${u(z.mode)}&resolution=${u(z.resolution)}${z.entityField?'&entity_field_id='+u(z.entityField):''}${z.entityValue?'&entity_value='+u(z.entityValue):''}${z.start?'&start='+u(z.start):''}${z.end?'&end='+u(z.end):''}`);
      if(requestToken!==token||current!==z)return;
      if(chart.source_sha256!==z.doc.source_sha256||chart.contract_revision!==z.doc.contract_revision)throw Error('图表来源与当前分析不一致，请刷新数据版本');
      z.chart=chart;draw();
    }catch(ex){if(requestToken!==token||current!==z)return;z.chartError=String(ex.message||ex);draw();}
  }
  async function loadStatus(requestToken,autoStart=true){
    const z=current;if(!z)return;
    try{
      const url=`/projects/${u(z.pid)}/feature-lab/data-analysis?asset_id=${u(z.aid)}&version_id=${u(z.vid)}`;
      z.doc=await z.ctx.req(url);
      if(requestToken!==token||current!==z)return;
      // Only an actively visited FeatureLab page may request or start previews.
      if(autoStart && previewActive(z,requestToken))await ensureFeaturePreview(z,requestToken);

      if(z.doc.status==='not_started'&&autoStart){
        await z.ctx.req(`/projects/${u(z.pid)}/feature-lab/data-analysis/start`,post({asset_id:z.aid,version_id:z.vid}));
        if(requestToken!==token||current!==z)return;
        z.doc=await z.ctx.req(url);
      }
      if(requestToken!==token||current!==z)return;
      draw();
      if(z.doc.status==='ready'){
        // Legacy stand-alone EDA remains functional for old integrations;
        // the unified workspace avoids this second, hidden chart query.
        if(!z.host.closest?.('#tp-analysis-workspace'))await loadChart(requestToken);
        return;
      }
      if(['queued','running'].includes(z.doc.status)||
         (z.doc.status==='not_started'&&['profiling','previewing'].includes(z.journey?.feature_preview?.status)))
         timer=setTimeout(()=>loadStatus(requestToken,false),1600);
    }catch(ex){if(requestToken!==token||current!==z)return;z.host.innerHTML=`<div class="helper warning">数据分析不可用：${e(ex.message||ex)}。请检查源数据版本与运行服务。</div>`;}
  }
  async function loadJourneyOnly(requestToken){
    const z=current;
    if(!z||!previewActive(z,requestToken))return;
    try{
      const next=await z.ctx.req(`/projects/${u(z.pid)}/feature-lab/workspace?asset_id=${u(z.aid)}&version_id=${u(z.vid)}`);
      if(!previewActive(z,requestToken))return;
      const previous=z.journey?.feature_preview?.status;
      z.journey=next;
      draw();
      if(['profiling','previewing'].includes(next.feature_preview?.status)){
        previewTimer=setTimeout(()=>loadJourneyOnly(requestToken),2800);
      }else if(previous!==next.feature_preview?.status&&next.feature_preview?.session_id){
        document.dispatchEvent(new CustomEvent('timepilot:feature-preview-ready', {detail:{pid:z.pid,asset_id:z.aid,version_id:z.vid}}));
      }
    }catch(_){/* Source analysis remains available; retry on next navigation. */}
  }
  async function render(ctx,pid){
    dispose(); const t=token;
    const host=document.getElementById('tp-project-data-analysis');if(!host)return;
    host.innerHTML='<p class="muted">正在读取项目数据…</p>';
    try{
      const assets=(await ctx.req('/projects/'+u(pid)+'/native-assets')).filter(a=>a.asset?.columns?.length);
      if(t!==token)return;
      if(!assets.length){host.innerHTML='<div class="empty"><strong>还没有可分析的数据</strong><p>请先进入数据管理上传并发布数据。</p><a class="button button-primary" href="/projects/'+u(pid)+'/data/import">上传数据</a></div>';return;}
      const search=new URLSearchParams(location.search);
      const aid=assets.some(x=>x.native_asset_id===search.get('asset'))?search.get('asset'):assets[0].native_asset_id;
      const versionsResponse=await ctx.req('/native-bi/assets/'+u(aid)+'/versions').catch(()=>({versions:[]}));
      if(t!==token)return;
      const versions=[{version_id:'base'},...(versionsResponse.versions||[]).filter(x=>x.version_id!=='base')];
      const vid=versions.some(x=>x.version_id===search.get('version'))?search.get('version'):'base';
      current={host,ctx,pid,assets,aid,vid,versions,doc:null,journey:null,fieldId:search.get('da_field')||search.get('field')||null,mode:['category','series'].includes(search.get('da_mode')||search.get('mode'))?(search.get('da_mode')||search.get('mode')):'distribution',entityValue:search.get('da_entity')||'',entityField:search.get('da_entity_field')||'',start:search.get('da_start')||'',end:search.get('da_end')||'',resolution:[60,120,240].includes(Number(search.get('da_resolution')))?Number(search.get('da_resolution')):120,chart:null,chartError:''};
      host.addEventListener('click',event=>{
        const action=event.target.closest('[data-da-action]');
        if(!action)return;
        event.preventDefault();
        window.TimePilotAnalysisWorkspace?.activate(action.dataset.daAction,true);
      });
      host.addEventListener('change',async event=>{
        if(!current||current.host!==host)return;
        const key=event.target.dataset.da;if(!key)return;
        if(key==='asset'||key==='version'){
          const nextAid=key==='asset'?event.target.value:current.aid;
          const nextVid=key==='version'?event.target.value:'base';
          const url=new URL(urlNext(nextAid,nextVid,null,null),window.location.href);
          // A different asset/version must never inherit the old field, device,
          // chart or candidate filters. These identifiers are source-local.
          ['candidate','advanced_candidate','da_field','da_mode','da_entity',
           'da_entity_field','da_start','da_end','da_resolution','filter',
           'family','chart','chart_mode','entity','resolution','da_filters','da_cards','da_corr','da_box',
           'da_source','da_fields','da_corr_method'].forEach(k=>url.searchParams.delete(k));
          url.searchParams.set('section','overview');
          ctx.navigate(url.pathname+url.search);return;
        }
        if(key==='field')current.fieldId=event.target.value;
        if(key==='mode')current.mode=event.target.value;
        if(key==='field'){current.entityValue='';current.start='';current.end='';}
        if(key==='entity')current.entityValue=event.target.value;
        if(key==='entity-field'){current.entityField=event.target.value;current.entityValue='';}
        if(key==='start')current.start=event.target.value.trim();
        if(key==='end')current.end=event.target.value.trim();
        if(key==='resolution')current.resolution=Number(event.target.value);

        const next=new URL(urlNext(current.aid,current.vid,current.fieldId,current.mode),window.location.href);
        for(const [name,value] of [['da_entity',current.entityValue],['da_entity_field',current.entityField],['da_start',current.start],['da_end',current.end],['da_resolution',current.resolution!==120?String(current.resolution):'']]){
          if(value)next.searchParams.set(name,value);else next.searchParams.delete(name);
        }
        window.history.replaceState(window.history.state,'',next.pathname+next.search);
        await loadChart(token);
      });
      draw();await loadStatus(t);
    }catch(ex){if(t===token)host.innerHTML=`<div class="helper warning">${e(ex.message||ex)}</div>`;}
  }
  function onSectionChange(section){
    const z=current;
    if(!z||!z.host?.isConnected)return;
    if(section!=='statistics'){window.TimePilotDataDashboard372?.dispose?.();}
    if(z.lastSection!==section){z.lastSection=section;z.previewEpoch=(z.previewEpoch||0)+1;}
    if(section!=='features' && previewTimer){clearTimeout(previewTimer);previewTimer=null;}
    // Feature previews are an explicit, lazy task. Overview/quality/statistics
    // never create feature jobs, including after Back/Forward navigation.
    if(section==='features')void ensureFeaturePreview(z,token);
    draw();
    if(section!=='statistics'||z.doc?.status!=='ready')return;
    const host=z.host.querySelector('#tp-da-dashboard');
    if(!host)return;
    window.TimePilotDataDashboard372?.mount(host,{ctx:z.ctx,pid:z.pid,aid:z.aid,vid:z.vid,
      source_sha256:z.doc.source_sha256,contract_revision:z.doc.contract_revision,
      fields:z.doc.profile?.fields||[]});
  }
  window.TimePilotDataAnalysisUI={render,dispose,onSectionChange,
    getContext:()=>current?{pid:current.pid,aid:current.aid,vid:current.vid,source_sha256:current.doc?.source_sha256}:null};
})();
