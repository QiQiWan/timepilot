/* v0.37.2 bounded, DuckDB-backed visual overview. No client-side metric fabrication. */
(() => {
  'use strict';
  const escape = v => String(v ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const u=encodeURIComponent;
  const n=(v,d=2)=>v==null||!Number.isFinite(Number(v))?'—':Number(v).toLocaleString('zh-CN',{maximumFractionDigits:d});
  const numeric=f=>Boolean(f?.numeric||/^(?:U?BIGINT|U?INTEGER|U?SMALLINT|TINYINT|DOUBLE|FLOAT|REAL|DECIMAL|NUMERIC|HUGEINT)/i.test(f?.physical_type||''));
  const kinds={histogram:'数值分布',box:'箱线图',quantiles:'分位数',category:'类别占比',scatter:'散点关联',trend:'时间趋势'};
  const ops={eq:'等于',ne:'不等于',gt:'大于',gte:'大于等于',lt:'小于',lte:'小于等于',between:'区间',is_null:'为空',not_null:'非空'};
  const safeJSON=(value,fallback)=>{if(typeof value!=='string'||value.length>8192)return fallback;try{return JSON.parse(value)??fallback;}catch(_){return fallback;}};
  let state=null, serial=0;
  let nextCardId=0;
  const viewScope=z=>JSON.stringify([z.pid,z.aid,z.vid,z.sha,z.revision]);
  const scopedParam=(z,key)=>{
    const search=new URLSearchParams(window.location.search);
    return search.get('da_source')===viewScope(z)?search.get(key):null;
  };
  const giveCardId=card=>{if(!card._tpCardId)card._tpCardId='chart-'+(++nextCardId);return card._tpCardId;};
  const form=body=>({method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  function fields(z){return z?.report?.fields||z?.sourceFields||[];}
  function defaultCards(fs){
    const nums=fs.filter(numeric),cats=fs.filter(f=>!numeric(f));
    const main=(nums.find(f=>!/(time|序号|index|id)$/i.test(f.name||f.source_name||''))||nums[0]||fs[0]);
    if(!main)return [];
    const first=main.field_id,other=(nums.find(f=>f.field_id!==first&&!/(time|序号|index|id)$/i.test(f.name||f.source_name||''))||nums.find(f=>f.field_id!==first)||main).field_id;
    return [{kind:numeric(main)?'histogram':'category',field_id:first,bins:16},
      ...(cats.length?[{kind:'category',field_id:cats[0].field_id,bins:16}]:[]),
      ...(nums.length>1?[{kind:'scatter',field_id:first,other_field_id:other,bins:16}]:[])];
  }
  function restoreCards(z){
    const saved=safeJSON(scopedParam(z,'da_cards'),[]);
    const allowed=new Set(z.sourceFields.map(f=>f.field_id));
    const selected=Array.isArray(saved)?saved.filter(x=>x&&kinds[x.kind]&&allowed.has(x.field_id)&&(!x.other_field_id||allowed.has(x.other_field_id))).slice(0,6):[];
    return (selected.length?selected:defaultCards(z.sourceFields)).map(card=>{giveCardId(card);return card;});
  }
  function restoreSelection(z, key, max, fallback){
    const saved=safeJSON(scopedParam(z,key),null);
    const available=new Set(z.sourceFields.filter(numeric).map(f=>f.field_id));
    return Array.isArray(saved)?[...new Set(saved.filter(id=>available.has(id)))].slice(0,max):fallback;
  }
  function normalizeCard(card,fs,confirmedTime){
    const nums=fs.filter(numeric);
    const all=new Set(fs.map(f=>f.field_id));
    if(!kinds[card.kind])card.kind='histogram';
    if(card.kind==='trend'&&!confirmedTime)card.kind='histogram';
    if(card.kind!=='category'&&!nums.some(f=>f.field_id===card.field_id)){
      if(!nums.length)card.kind='category';
      else card.field_id=nums[0].field_id;
    }
    if(!all.has(card.field_id))card.field_id=(card.kind==='category'?fs:nums)[0]?.field_id;
    if(card.kind==='scatter'){
      const others=nums.filter(f=>f.field_id!==card.field_id);
      if(!others.length)card.kind='histogram';
      else if(!others.some(f=>f.field_id===card.other_field_id))card.other_field_id=others[0].field_id;
    }
    card.bins=Math.min(50,Math.max(4,Number(card.bins)||16));
    card.limit=Math.min(25,Math.max(3,Number(card.limit)||12));
    return card;
  }
  function restoreFilters(z){
    const saved=safeJSON(scopedParam(z,'da_filters'),[]);
    const allowed=new Set(z.sourceFields.map(f=>f.field_id));
    return Array.isArray(saved)?saved.filter(x=>x&&allowed.has(x.field_id)&&ops[x.op]).slice(0,5):[];
  }
  function restoreStatFields(z){
    const fallback=z.sourceFields.slice(0,24).map(f=>f.field_id);
    const saved=safeJSON(scopedParam(z,'da_fields'),null);
    const allowed=new Set(z.sourceFields.map(f=>f.field_id));
    if(!Array.isArray(saved))return fallback;
    const ids=[...new Set(saved.filter(id=>typeof id==='string'&&allowed.has(id)))].slice(0,24);
    return ids.length?ids:fallback;
  }
  function saveURL(z){
    const url=new URL(window.location.href);
    url.searchParams.set('da_source',viewScope(z));
    url.searchParams.set('da_fields',JSON.stringify(z.statIds));
    if(z.filters.length)url.searchParams.set('da_filters',JSON.stringify(z.filters));else url.searchParams.delete('da_filters');
    if(z.cards.length)url.searchParams.set('da_cards',JSON.stringify(z.cards));else url.searchParams.delete('da_cards');
    if(z.corrIds?.length)url.searchParams.set('da_corr',JSON.stringify(z.corrIds));else url.searchParams.delete('da_corr');
    if(z.boxIds?.length)url.searchParams.set('da_box',JSON.stringify(z.boxIds));else url.searchParams.delete('da_box');
    if(z.associationMethod==='spearman')url.searchParams.set('da_corr_method','spearman');else url.searchParams.delete('da_corr_method');
    window.history.replaceState(window.history.state,'',url.pathname+url.search);
  }
  function option(fs,value,subset='all'){
    return fs.filter(f=>subset==='all'||(subset==='numeric'&&numeric(f))||
        (subset==='category'&&!numeric(f))).map(f=>`<option value="${escape(f.field_id)}" ${f.field_id===value?'selected':''}>${escape(f.name||f.source_name)}</option>`).join('');
  }
  const fieldLabel=(z,id)=>fields(z).find(f=>f.field_id===id)?.name||z.sourceFields.find(f=>f.field_id===id)?.source_name||id;
  function chartSVG(data){
    if(!data || !Array.isArray(data.points)||!data.points.length) return '<div class="tp-dash-empty">当前筛选范围没有可绘制的数据。</div>';
    const pts=data.points;
    const simple=data.kind==='histogram'||data.kind==='category';
    if(simple){
      const max=Math.max(1,...pts.map(p=>Number(p.count)||0));
      const width=570/pts.length;
      return `<svg class="tp-dash-svg" viewBox="0 0 650 210" role="img" aria-label="${escape(data.field_name)} 的${escape(kinds[data.kind])}"><line x1="45" x2="625" y1="175" y2="175" stroke="#b5c8dd"/>${pts.map((p,i)=>{
        const value=Number(p.count)||0,barH=145*value/max,x=46+i*width;
        const label=data.kind==='category'?(p.label??'未标注'):`${n(p.lower,2)}—${n(p.upper,2)}`;
        return `<rect x="${x+2}" y="${175-barH}" width="${Math.max(2,width-5)}" height="${barH}" fill="${p.other?'#91afcf':'#377acb'}" rx="2"><title>${escape(label)}：${escape(value)} 条</title></rect>${i%Math.max(1,Math.ceil(pts.length/7))===0?`<text x="${x+2}" y="193" font-size="9">${escape(String(label).slice(0,9))}</text>`:''}`;
      }).join('')}<text x="8" y="25">${escape(n(max,0))}</text></svg>`;
    }
    if(data.kind==='box'||data.kind==='quantiles'){
      const s=data.stats||{};if(s.min==null||s.max==null)return '<div class="tp-dash-empty">没有有限数值。</div>';
      const lo=Number(s.min),hi=Number(s.max),scale=t=>46+(Number(t)-lo)/(hi-lo||1)*560;
      return `<svg class="tp-dash-svg" viewBox="0 0 650 210" role="img" aria-label="分位数箱线图"><line x1="45" x2="625" y1="104" y2="104" stroke="#a7c2e0"/>`+
      `<line x1="${scale(s.whisker_low??s.min)}" x2="${scale(s.whisker_high??s.max)}" y1="95" y2="95" stroke="#1b5c9f" stroke-width="3"/>`+
      `<line x1="${scale(s.whisker_low??s.min)}" x2="${scale(s.whisker_low??s.min)}" y1="83" y2="107" stroke="#1b5c9f" stroke-width="2"/>`+
      `<line x1="${scale(s.whisker_high??s.max)}" x2="${scale(s.whisker_high??s.max)}" y1="83" y2="107" stroke="#1b5c9f" stroke-width="2"/>`+
      `<rect x="${scale(s.p25)}" y="60" width="${Math.max(1,scale(s.p75)-scale(s.p25))}" height="65" fill="#bad9f4" stroke="#3979c8" rx="4"/>`+
      `<line x1="${scale(s.p50)}" x2="${scale(s.p50)}" y1="60" y2="125" stroke="#1d4e86" stroke-width="3"/>`+
      `<text x="46" y="164">最小 ${escape(n(s.min))}</text><text x="255" y="164">中位 ${escape(n(s.p50))}</text><text x="470" y="164">最大 ${escape(n(s.max))}</text>`+
      `<text x="46" y="188" font-size="11">Tukey 须端；离群点 ${escape(n(s.outlier_count,0))} 条（极值单列统计）</text></svg>`;
    }
    if(data.kind==='scatter'){
      const points=pts.filter(p=>p.x!=null&&p.y!=null&&Number.isFinite(Number(p.x))&&Number.isFinite(Number(p.y)));
      if(!points.length)return '<div class="tp-dash-empty">当前没有可核对的两字段有效配对记录。</div>';
      const xs=points.map(p=>Number(p.x)),ys=points.map(p=>Number(p.y));
      const loX=Math.min(...xs),hiX=Math.max(...xs),loY=Math.min(...ys),hiY=Math.max(...ys);
      return `<svg class="tp-dash-svg" viewBox="0 0 650 210" role="img" aria-label="两字段的散点关系"><path d="M45 15V175H625" fill="none" stroke="#9bb2cd"/>${points.map(p=>{
        const x=46+(Number(p.x)-loX)/(hiX-loX||1)*560,y=174-(Number(p.y)-loY)/(hiY-loY||1)*148;
        return `<circle cx="${x.toFixed(2)}" cy="${y.toFixed(2)}" r="2.4" fill="#377acb" opacity=".65"><title>${escape(n(p.x))}，${escape(n(p.y))}</title></circle>`;
      }).join('')}<text x="45" y="195">${escape(n(loX))}</text><text x="560" y="195">${escape(n(hiX))}</text></svg>`;
    }
    if(data.kind==='trend'){
      const vals=pts.map(p=>Number(p.value)),valid=vals.filter(Number.isFinite);
      if(!valid.length)return '<div class="tp-dash-empty">该时间范围没有可用数据。</div>';
      const low=Math.min(...valid),high=Math.max(...valid);
      const path=pts.map((p,i)=>Number.isFinite(Number(p.value))?`${i===0?'M':'L'}${(45+i*560/Math.max(1,pts.length-1)).toFixed(2)},${(176-(Number(p.value)-low)/(high-low||1)*145).toFixed(2)}`:'').join(' ');
      return `<svg class="tp-dash-svg" viewBox="0 0 650 210" role="img" aria-label="按已确认时间汇总的趋势"><path d="M45 15V176H625" fill="none" stroke="#9bb2cd"/><path d="${path}" fill="none" stroke="#3576c1" stroke-width="2.5"/><text x="7" y="22">${escape(n(high))}</text><text x="7" y="174">${escape(n(low))}</text><text x="44" y="199">${escape(String(pts[0].x).slice(0,10))}</text><text x="536" y="199">${escape(String(pts.at(-1).x).slice(0,10))}</text></svg>`;
    }
    return '';
  }
  function fieldStats(z){
    if(!z.report)return '';
    return `<div class="tp-dash-table-scroll"><table class="tp-dash-table"><thead><tr><th>字段</th><th>类型</th><th>有效记录</th><th>缺失率</th><th>不同值</th><th>均值</th><th>中位数</th><th>标准差</th></tr></thead><tbody>${z.report.fields.map(f=>`<tr><td>${escape(f.name)}</td><td>${escape(f.role==='unspecified'?f.type:f.role)}</td><td>${n(f.non_null,0)}</td><td>${f.null_rate==null?'—':n(f.null_rate*100,2)+'%'}</td><td>${n(f.distinct_count,0)}</td><td>${n(f.mean)}</td><td>${n(f.median)}</td><td>${n(f.stddev)}</td></tr>`).join('')}</tbody></table></div>`;
  }
  function correlations(z){
    const report=z.report||{},pairs=report.correlations||[];
    const all=z.sourceFields.filter(f=>z.statIds.includes(f.field_id)&&numeric(f)&&!['record_id','order_index','entity_key','event_time','label','prediction_time','available_at'].includes(f.role)),selected=z.corrIds||[];
    const controls=`<label class="tp-dash-association-method">关联方法 <select data-dash="association-method"><option value="pearson" ${z.associationMethod==='pearson'?'selected':''}>Pearson（线性）</option><option value="spearman" ${z.associationMethod==='spearman'?'selected':''}>Spearman（秩相关）</option></select></label><details class="tp-dash-pick"><summary>选择相关性字段 (${selected.length}/10)</summary><div class="tp-dash-pick-options">${all.map(f=>`<label><input type="checkbox" data-dash="corr-field" value="${escape(f.field_id)}" ${selected.includes(f.field_id)?'checked':''}> ${escape(f.name||f.source_name)}</label>`).join('')}</div></details>`;
    if(!selected.length)return controls+'<p class="muted">请选择至少两个可比较数值字段。</p>';
    const pairMap=new Map(pairs.map(x=>[[x.x_field_id,x.y_field_id].sort().join('|'),x]));
    const stats=new Map((report.fields||[]).map(f=>[f.field_id,f]));
    const headers=selected.map(id=>escape(fieldLabel(z,id)));
    let html='<div class="tp-dash-matrix-scroll"><table class="tp-dash-matrix" aria-label="数值字段 Pearson 相关性矩阵"><thead><tr><th>字段</th>'+headers.map(x=>`<th title="${x}">${x}</th>`).join('')+'</tr></thead><tbody>';
    for(const row of selected){html+=`<tr><th>${escape(fieldLabel(z,row))}</th>`;
      for(const col of selected){const diagonal=row===col;
        const pair=pairMap.get([row,col].sort().join('|'));
        const f=stats.get(row);
        // Pearson(x,x)=1 only with at least two finite, non-constant observations.
        const validDiagonal=diagonal&&Number(f?.finite_count)>=2&&Number(f?.stddev)>0;
        const value=diagonal?(validDiagonal?1:null):pair?.[z.associationMethod||'pearson'];
        const v=value==null?null:Number(value);
        const count=diagonal?f?.finite_count:pair?.pair_count;
        const light=v==null?'background:#f0f3f7;color:#77899b':(v<0?`background:rgba(204,94,70,${(.10+.74*Math.abs(v)).toFixed(3)})`:`background:rgba(50,113,194,${(.10+.74*Math.abs(v)).toFixed(3)})`);
        html+=`<td style="${light}" title="${escape(fieldLabel(z,row))} × ${escape(fieldLabel(z,col))} · 配对 ${escape(count??'未核实')}" ${diagonal?'data-matrix-diagonal="true"':''}>${v==null?'—':n(v,2)}</td>`;
      }html+='</tr>';
    }
    html+='</tbody></table></div>';
    return controls+html+`<p class="tp-dash-note">行列顺序一致；常量或样本不足的字段显示“—”。配对样本数可悬停查看，${escape(z.associationMethod==='spearman'?'Spearman 基于配对样本平均秩计算单调相关':'Pearson 表示线性相关')}，不代表因果关系。</p>`;
  }
  function boxCollection(z){
    const fs=(z.report?.fields||[]).filter(f=>f.numeric&&f.min!=null&&f.max!=null);
    const selected=z.boxIds||[],show=fs.filter(f=>selected.includes(f.field_id)).slice(0,16);
    const controls=`<details class="tp-dash-pick"><summary>选择箱线字段 (${selected.length}/16)</summary><div class="tp-dash-pick-options">${fs.map(f=>`<label><input type="checkbox" data-dash="box-field" value="${escape(f.field_id)}" ${selected.includes(f.field_id)?'checked':''}> ${escape(f.name)}</label>`).join('')}</div></details>`;
    if(!show.length)return controls+'<p class="muted">请选择有有效数值的字段。</p>';
    const h=show.length*54+52;
    const rows=show.map((f,i)=>{
      const lo=Number(f.min),hi=Number(f.max),delta=hi-lo||1;
      const scale=x=>180+Math.max(0,Math.min(1,(Number(x)-lo)/delta))*750;
      const y=46+i*54,low=scale(f.whisker_low??f.min),q1=scale(f.p25),median=scale(f.median),q3=scale(f.p75),high=scale(f.whisker_high??f.max);
      return `<g data-field="${escape(f.field_id)}"><text x="12" y="${y+4}" font-size="13" fill="#183a59">${escape(f.name)}</text><path d="M${low} ${y}H${high}" stroke="#347cc0" stroke-width="2"/><path d="M${low} ${y-8}v16M${high} ${y-8}v16" stroke="#347cc0"/><rect x="${q1}" y="${y-12}" width="${Math.max(1,q3-q1)}" height="24" fill="#bdd9f1" stroke="#347cc0" rx="2"/><path d="M${median} ${y-12}v24" stroke="#17549d" stroke-width="3"/><text x="937" y="${y+4}" font-size="11" fill="#526782">${escape(n(f.median,3))}</text><title>${escape(f.name)}：最小 ${escape(n(f.min,3))}，Tukey 下须 ${escape(n(f.whisker_low??f.min,3))}，Q1 ${escape(n(f.p25,3))}，中位 ${escape(n(f.median,3))}，Q3 ${escape(n(f.p75,3))}，Tukey 上须 ${escape(n(f.whisker_high??f.max,3))}，最大 ${escape(n(f.max,3))}，离群记录 ${escape(n(f.outlier_count??0,0))}</title></g>`;
    }).join('');
    return controls+`<div class="tp-dash-large-figure"><svg viewBox="0 0 1080 ${h}" role="img" aria-label="多字段箱线图"><text x="12" y="21" fill="#627b94" font-size="12">字段</text><text x="180" y="21" fill="#627b94" font-size="12">单字段最小值 ← 分布位置 → 单字段最大值</text><text x="937" y="21" fill="#627b94" font-size="12">中位数</text>${rows}</svg></div><p class="tp-dash-note">为兼容不同物理单位，每行独立以该字段最小值—最大值归一化定位箱体。图中每个字段采用独立刻度，仅比较分布形状。须端采用 Tukey 1.5×IQR 内真实观测极值，超出须端的记录计为离群点；悬停可查看原始最小、最大值及离群数。不同量纲不得直接比较横坐标。小提琴图需要真实密度估计，当前不伪造。</p>`;
  }
  // v0.37.3: Evidence and wording belong to the server, never re-compute
  // analytical claims in the browser from a partial chart payload.
  function evidenceFindings(z){
    const proof=z.report?.evidence_analysis;
    if(!proof||proof.status!=='supported')return `<section class="tp-dash-findings" data-testid="dashboard-evidence-373"><h3>这批数据的主要发现</h3><p>${escape(proof?.message||'当前尚无可复核的数值依据，无法判断。')}</p></section>`;
    const cards=(proof.claims||[]).map(c=>`<article class="tp-evidence-item"><strong>${escape(c.title)}</strong><p>${escape(c.observation)}</p><details><summary>查看数值依据</summary><p>证据编号：<code>${escape(c.id)}</code></p>${(c.evidence||[]).map(x=>`<p><code>${escape(x.path)}</code> = ${escape(n(x.value,6))} ${escape(x.unit||'')}</p>`).join('')}<p>数据版本：${escape(c.scope?.version_id||'')} · 来源 SHA256：${escape(c.scope?.source_sha256||'')} · 筛选 SHA256：${escape(c.scope?.scope_sha256||'')}</p></details>${(c.limitations||[]).map(x=>`<p class="tp-dash-note">${escape(x)}</p>`).join('')}</article>`).join('');
    return `<section class="tp-dash-findings" data-testid="dashboard-evidence-373"><h3>这批数据的主要发现</h3>${cards}<p class="tp-dash-note">每项发现绑定数值、数据版本和筛选指纹。无标签或缺少模型评价时不提供预测增益。</p></section>`;
  }
  function cardResult(z,id){
    const data=z.chartData[id],bad=z.chartErrors[id];
    if(bad)return `<p class="helper warning" role="alert">${escape(bad)}</p>`;
    if(!data)return '<p class="muted">正在计算图表…</p>';
    return chartSVG(data)+`<p class="tp-dash-card-foot">${n(data.rows_filtered,0)} 条筛选记录 · ${data.sampled?'确定性抽样':'全量统计'} · ${escape(data.field_name)}${data.truncated?' · 仅显示前 160 个时间桶':''}</p>`;
  }
  function refreshCard(z,id){
    const node=z.host?.querySelector?.(`[data-dash-card-id="${id}"] [data-dash-card-result]`);
    if(node)node.innerHTML=cardResult(z,id);
    else paint(z); // test mocks and initial render can lack DOM querySelector
  }
  function paint(z){
    if(!z.host?.isConnected)return;
    // Preserve an expanded multi-field picker across server refreshes.
    // Re-rendering must not force users to re-open it after each checkbox.
    const picker=z.host.querySelector?.('details.tp-dash-field-picker');
    if(picker)z.fieldPickerOpen=Boolean(picker.open);
    const report=z.report,fs=fields(z),metric=report?.metrics||{};
    const simpleFields=z.sourceFields;
    const selected=z.filters.map((f,i)=>`<span class="tp-dash-chip">${escape(fieldLabel(z,f.field_id))} ${escape(ops[f.op]||f.op)} ${escape(f.value??'')}${f.upper!=null?'～'+escape(f.upper):''} <button type="button" data-dash-action="drop-filter" data-index="${i}" aria-label="移除筛选">×</button></span>`).join('');
    z.host.innerHTML=`<section class="tp-dash" data-testid="data-dashboard-372"><div class="tp-dash-head"><div><h2>数据一览</h2><p>当前版本与筛选条件下的真实 DuckDB 统计 · ${escape(z.vid)} · SHA256 ${escape(z.sha.slice(0,12))}…</p></div><div class="tp-dash-head-actions"><button class="button" type="button" data-dash-action="export-report" ${report?.evidence_analysis?.status==='supported'?'':'disabled'}>导出证据报告</button><button class="button" type="button" data-dash-action="refresh">重新统计</button></div></div>
      <div class="tp-dash-filters"><label>字段 <select data-dash="filter-field">${option(simpleFields,z.newFilterField)}</select></label><label>条件 <select data-dash="filter-op">${Object.entries(ops).map(([k,v])=>`<option value="${k}" ${z.newFilterOp===k?'selected':''}>${v}</option>`).join('')}</select></label><label>值 <input data-dash="filter-value" placeholder="值 / 下限" value="${escape(z.newFilterValue)}"></label>${z.newFilterOp==='between'?`<label>上限 <input data-dash="filter-upper" value="${escape(z.newFilterUpper)}"></label>`:''}<button type="button" class="button button-primary" data-dash-action="add-filter">应用筛选</button><button type="button" class="button" data-dash-action="clear-filters">清除</button></div>
      ${selected?`<div class="tp-dash-chips">${selected}</div>`:''}
      <details class="tp-dash-pick tp-dash-field-picker" ${z.fieldPickerOpen?'open':''}><summary>本次统计字段（${z.statIds.length}/${simpleFields.length}，最多 24 列）</summary>
        <p class="tp-dash-note">已选字段用于总体缺失率、分位数表、多字段箱线图、相关分析和证据报告；独立图表卡片可选择其他字段，但始终共享当前版本与筛选条件。</p>
        <div class="tp-dash-pick-options">${simpleFields.map(f=>`<label><input type="checkbox" data-dash="stat-field" value="${escape(f.field_id)}" ${z.statIds.includes(f.field_id)?'checked':''}> ${escape(f.name||f.source_name)}${f.unit?' ('+escape(f.unit)+')':''}</label>`).join('')}</div>
      </details>
      ${z.error?`<div class="helper warning" role="alert">数据统计无法完成：${escape(z.error)}</div>`:''}
      ${!report?`<p class="muted">${z.loading?'正在从当前数据版本计算指标…':'暂无统计结果'}</p>`:
      `<div class="tp-dash-metrics"><article><small>筛选后记录</small><strong>${n(metric.records,0)}</strong><span>总计 ${n(metric.source_records,0)} 条</span></article><article><small>字段</small><strong>${n(metric.columns,0)}</strong><span>其中数值字段 ${n(metric.numeric_fields,0)}</span></article><article><small>已选字段缺失与无效数值率</small><strong>${n(metric.missing_percent)}%</strong><span>${n(metric.missing_cells,0)} 个 NULL，${n(metric.non_finite_numeric_cells,0)} 个非有限值，${n(metric.conversion_failed_cells,0)} 个转换失败（共 ${n(metric.invalid_numeric_cells,0)} 个无效数值）</span></article><article><small>当前覆盖比例</small><strong>${n(metric.coverage_percent)}%</strong><span>${z.filters.length} 项筛选</span></article></div>
      ${evidenceFindings(z)}
      <div class="tp-dash-section-head"><h3>可配置图表</h3><button type="button" class="button" data-dash-action="add-card" ${z.cards.length>=6?'disabled':''}>＋ 添加图表</button></div><div class="tp-dash-grid">${z.cards.map((card,i)=>{
        const id=giveCardId(card),data=z.chartData[id],bad=z.chartErrors[id];return `<article class="tp-dash-card" data-dash-card-id="${escape(id)}"><div class="tp-dash-card-head"><strong>${escape(kinds[card.kind])}</strong><button type="button" data-dash-action="remove-card" data-index="${i}" aria-label="删除图表">×</button></div>
          <div class="tp-dash-card-tools"><label>图表 <select data-dash="card-type" data-index="${i}">${Object.entries(kinds).map(([k,v])=>`<option value="${k}" ${card.kind===k?'selected':''} ${k==='trend'&&!metric.confirmed_time?'disabled':''}>${v}</option>`).join('')}</select></label><label>字段 <select data-dash="card-field" data-index="${i}">${option(simpleFields,card.field_id,card.kind==='category'?'all':'numeric')}</select></label>${card.kind==='scatter'?`<label>对比 <select data-dash="card-other" data-index="${i}">${option(simpleFields,card.other_field_id,'numeric')}</select></label>`:''}${card.kind==='histogram'?`<label>分桶 <select data-dash="card-bins" data-index="${i}">${[8,12,16,24,32].map(v=>`<option value="${v}" ${card.bins===v?'selected':''}>${v} 组</option>`).join('')}</select></label>`:''}${card.kind==='category'?`<label>显示类别 <select data-dash="card-limit" data-index="${i}">${[5,10,12,20].map(v=>`<option value="${v}" ${(card.limit||12)===v?'selected':''}>前 ${v} 类</option>`).join('')}</select></label>`:''}${card.kind==='trend'?`<label>时间粒度 <select data-dash="card-granularity" data-index="${i}">${[['hour','小时'],['day','每天'],['week','每周'],['month','每月']].map(([value,label])=>`<option value="${value}" ${(card.granularity||'day')===value?'selected':''}>${label}</option>`).join('')}</select></label>`:''}</div>
          <div data-dash-card-result>${cardResult(z,id)}</div></article>`;
      }).join('')}</div>
      <section class="tp-dash-wide"><div class="tp-dash-section-head"><h3>多字段箱线图</h3><span class="tp-dash-note">同图查看各字段的分位数与极端值</span></div>${boxCollection(z)}</section><section class="tp-dash-wide"><div class="tp-dash-section-head"><h3>字段相关性热力图</h3><span class="tp-dash-note">Pearson / Spearman · 可筛选指标</span></div>${correlations(z)}</section><details class="tp-dash-proof"><summary>字段统计明细及分位数</summary>${fieldStats(z)}${report.selection?.fields_truncated?'<p class="tp-dash-note">字段数量超过单次 24 列的扫描预算。</p>':''}</details>
      <details class="tp-dash-proof"><summary>数值来源和计算口径</summary><p>全部数据直接来自当前版本 __tp_effective DuckDB 视图；本页仅呈现数据画像与线性相关，不推断故障或模型收益。筛选条件以参数化 SQL 执行，结果与图表共享同一筛选指纹。</p><code>${escape(report.source_sha256)}</code><p>筛选 SHA256：${escape(report.scope_sha256)}</p><p>字段契约修订：${escape(report.contract_revision)}</p><p>缺失率分母：${n(metric.metric_scope?.denominator_cells,0)} 单元格（${n(metric.metric_scope?.rows,0)} 行 × ${n(metric.metric_scope?.field_ids?.length,0)} 个已选字段）；统计字段：${escape((metric.metric_scope?.field_ids||[]).map(id=>fieldLabel(z,id)).join("、"))}</p><p>NULL、非有限值和转换失败分开计数；未评估业务阈值异常。</p></details>`}
    </section>`;
    // A source/version switch can reuse the same host element. Remove event
    // handlers captured by the previous dashboard state before rebinding.
    if(z.host.__dashboardBound && z.host.__tpDashboardOwner!==z){
      for(const [type,fn] of Object.entries(z.host.__tpDashboardHandlers||{}))
        z.host.removeEventListener?.(type,fn);
      z.host.__dashboardBound=false;
    }
    if(!z.host.__dashboardBound){
      z.host.__dashboardBound=true;
      z.host.__tpDashboardOwner=z;
      const handlers=z.host.__tpDashboardHandlers={};
      const bind=(type,fn)=>{handlers[type]=fn;z.host.addEventListener(type,fn);};
      bind('input',ev=>{
        if(!state||state!==z)return;const k=ev.target?.dataset?.dash;
        if(k==='filter-value')z.newFilterValue=ev.target.value;
        if(k==='filter-upper')z.newFilterUpper=ev.target.value;
      });
      bind('change',ev=>{
        if(!state||state!==z)return;const k=ev.target?.dataset?.dash,i=Number(ev.target?.dataset?.index);
        if(k==='filter-field')z.newFilterField=ev.target.value;
        if(k==='association-method'){z.associationMethod=ev.target.value==='spearman'?'spearman':'pearson';if(z.associationMethod==='spearman'&&z.corrIds.length>6)z.corrIds=z.corrIds.slice(0,6);saveURL(z);load(z);return;}
        if(k==='stat-field'){
          const selected=new Set(z.statIds);
          if(ev.target.checked && selected.size>=24){z.error='单次最多统计 24 个字段，请先取消其他字段';paint(z);return;}
          if(!ev.target.checked && selected.size<=1){z.error='至少保留 1 个统计字段';paint(z);return;}
          if(ev.target.checked)selected.add(ev.target.value);else selected.delete(ev.target.value);
          z.statIds=[...selected];
          // Correlations and box plots require statistics from the same columns.
          z.corrIds=z.corrIds.filter(id=>selected.has(id));
          z.boxIds=z.boxIds.filter(id=>selected.has(id));
          z.error='';saveURL(z);load(z);return;
        }
        if(k==='filter-op')z.newFilterOp=ev.target.value;
        if(k==='corr-field'||k==='box-field'){
          const name=k==='corr-field'?'corrIds':'boxIds';const max=name==='corrIds'?10:16;
          const selected=new Set(z[name]||[]);
          const limit=name==='corrIds'&&z.associationMethod==='spearman'?6:max;
          if(ev.target.checked&&selected.size>=limit){z.error=`最多选择 ${limit} 个字段`;paint(z);return;}
          if(ev.target.checked)selected.add(ev.target.value);else selected.delete(ev.target.value);
          z[name]=[...selected];z.error='';saveURL(z);
          if(name==='corrIds')load(z);else paint(z);return;
        }
        if(k==='card-type'||k==='card-field'||k==='card-other'||k==='card-bins'||k==='card-limit'||k==='card-granularity'){
          const card=z.cards[i];if(!card)return;
          if(k==='card-type'){card.kind=ev.target.value;const subset=card.kind==='category'?simpleFields:simpleFields.filter(numeric);if(!subset.some(f=>f.field_id===card.field_id))card.field_id=subset[0]?.field_id;}
          if(k==='card-field')card.field_id=ev.target.value;
          if(k==='card-other')card.other_field_id=ev.target.value;
          if(k==='card-bins')card.bins=Number(ev.target.value);
          if(k==='card-limit')card.limit=Number(ev.target.value);
          if(k==='card-granularity')card.granularity=ev.target.value;
          normalizeCard(card,simpleFields,Boolean(metric.confirmed_time));
          saveURL(z);requestCharts(z,[i]);return;
        }
        paint(z);
      });
      bind('click',ev=>{
        const action=ev.target?.closest?.('[data-dash-action]');if(!action||state!==z)return;
        ev.preventDefault();const act=action.dataset.dashAction,i=Number(action.dataset.index);
        if(act==='add-filter'){
          if(z.filters.length>=5){z.error='最多使用 5 个筛选条件';paint(z);return;}
          const row={field_id:z.newFilterField,op:z.newFilterOp};
          if(!['is_null','not_null'].includes(row.op))row.value=z.newFilterValue;
          if(row.op==='between')row.upper=z.newFilterUpper;
          z.filters.push(row);saveURL(z);load(z);return;
        }
        if(act==='drop-filter'){z.filters.splice(i,1);saveURL(z);load(z);return;}
        if(act==='clear-filters'){z.filters=[];z.newFilterValue='';z.newFilterUpper='';saveURL(z);load(z);return;}
        if(act==='export-report'){
          exportReport(z).catch(error=>{if(state===z){z.error=String(error?.message||error);paint(z);}});
          return;
        }
        if(act==='refresh'){load(z);return;}
        if(act==='remove-card'){const removed=z.cards.splice(i,1)[0];if(removed){cancelOldCharts(z,removed._tpCardId);z.chartQueue=z.chartQueue.filter(job=>job.id!==removed._tpCardId);delete z.chartData[removed._tpCardId];delete z.chartErrors[removed._tpCardId];delete z.cardTokens[removed._tpCardId];}saveURL(z);paint(z);return;}
        if(act==='add-card'&&z.cards.length<6){
          const more=defaultCards(simpleFields)[0];if(!more)return;
          const card={...more};giveCardId(card);z.cards.push(card);saveURL(z);requestCharts(z,[z.cards.length-1]);
        }
      });
    }
  }
  function validate(z,response){
    if(response.asset_id!==z.aid||response.version_id!==z.vid||response.source_sha256!==z.sha||
        Number(response.contract_revision)!==Number(z.revision))throw Error('数据或字段契约已改变，已取消旧版本图表');
  }
  async function exportReport(z){
    if(!z.report||z.report?.evidence_analysis?.status!=='supported')throw Error('当前没有可核验的报告数值，无法导出');
    const payload={asset_id:z.aid,version_id:z.vid,expected_sha256:z.sha,
      expected_revision:z.revision,filters:z.filters,field_ids:z.statIds,
      association_ids:z.corrIds,association_method:z.associationMethod};
    const output=await z.ctx.req(`/projects/${u(z.pid)}/feature-lab/data-analysis/dashboard/report`,form(payload));
    if(state!==z||output?.source_sha256!==z.sha||output?.scope_sha256!==z.report.scope_sha256)
      throw Error('报告来源或筛选条件发生变化，已拒绝导出旧结果');
    if(!output.report_sha256||!output.markdown)throw Error('报告缺少可校验内容');
    // SHA-256 is a content guarantee, not merely an HTTP 200 response.
    if(!globalThis.crypto?.subtle)throw Error('当前浏览器无法验证报告 SHA256，已拒绝导出');
    const bytes=new TextEncoder().encode(output.markdown);
    const hash=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))).map(b=>b.toString(16).padStart(2,'0')).join('');
    if(hash!==output.report_sha256)throw Error('报告 SHA256 核验失败，已拒绝导出');
    const blob=new Blob([bytes],{type:'text/markdown;charset=utf-8'});
    const url=URL.createObjectURL(blob),link=document.createElement('a');
    link.href=url;link.download=`TimePilot_数据一览_${z.vid}_证据报告.md`;link.click();
    URL.revokeObjectURL(url);
  }
  function cancelOldCharts(z,cardId=null){
    if(!z.chartControllers)return;
    for(const [id,controller] of z.chartControllers){
      if(cardId===null||id===cardId){controller.abort();z.chartControllers.delete(id);}
    }
  }
  function drainCharts(z){
    while(state===z&&z.activeChartRequests<2&&z.chartQueue.length){
      const job=z.chartQueue.shift();
      if(job.epoch!==z.chartEpoch||job.token!==z.cardTokens[job.id])continue;
      const card=z.cards.find(c=>c._tpCardId===job.id);
      if(!card)continue;
      z.activeChartRequests++;
      const payload={asset_id:z.aid,version_id:z.vid,expected_sha256:z.sha,
        expected_revision:z.revision,filters:z.filters,...job.config};
      const controller=typeof AbortController!=='undefined'?new AbortController():null;
      if(controller)z.chartControllers.set(job.id,controller);
      const options=form(payload);if(controller)options.signal=controller.signal;
      Promise.resolve().then(()=>z.ctx.req(`/projects/${u(z.pid)}/feature-lab/data-analysis/dashboard/chart`,options))
        .then(response=>{
          if(state!==z||job.epoch!==z.chartEpoch||job.token!==z.cardTokens[job.id])return;
          validate(z,response);
          if(response.scope_sha256!==z.report?.scope_sha256)throw Error('图表筛选范围已过期，请重新统计');
          z.chartData[job.id]=response;delete z.chartErrors[job.id];
          refreshCard(z,job.id);
        }).catch(err=>{
          if(state!==z||job.epoch!==z.chartEpoch||job.token!==z.cardTokens[job.id])return;
          z.chartErrors[job.id]=String(err?.message||err);
          refreshCard(z,job.id);
        }).finally(()=>{
          if(controller&&z.chartControllers.get(job.id)===controller)z.chartControllers.delete(job.id);
          // A new filter/source epoch must never wait for an old slow query.
          // Ignore completions from superseded epochs when counting slots.
          if(job.epoch===z.chartEpoch){
            z.activeChartRequests=Math.max(0,z.activeChartRequests-1);
            if(state===z){drainCharts(z);if(!z.activeChartRequests&&!z.chartQueue.length)z.chartLoading=false;}
          }
        });
    }
  }
  function requestCharts(z,indices=null){
    const full=indices===null;
    if(full){
      cancelOldCharts(z);z.chartEpoch++;z.chartQueue=[];z.chartData={};z.chartErrors={};
      z.activeChartRequests=0; // old epoch requests no longer reserve new concurrency slots
    }
    if(!z.report){z.chartLoading=false;paint(z);return;}
    const jobs=full?z.cards.map((_,i)=>i):indices;
    for(const i of jobs){
      const card=z.cards[i];if(!card)continue;
      normalizeCard(card,z.sourceFields,Boolean(z.report?.metrics?.confirmed_time));
      const id=giveCardId(card);
      // Invalidating only this card prevents one fast edit from re-running all cards.
      cancelOldCharts(z,id);
      const token=z.cardTokens[id]=(z.cardTokens[id]||0)+1;
      delete z.chartData[id];delete z.chartErrors[id];
      z.chartQueue=z.chartQueue.filter(job=>job.id!==id);
      const {kind,field_id,other_field_id,bins,limit,granularity}=card;
      z.chartQueue.push({id,token,epoch:z.chartEpoch,config:{kind,field_id,other_field_id,bins,limit,granularity}});
    }
    z.chartLoading=Boolean(z.chartQueue.length||z.activeChartRequests);
    paint(z);
    drainCharts(z);
  }
  async function load(z){
    if(z.overviewController)z.overviewController.abort();
    cancelOldCharts(z);
    const epoch=++z.queryEpoch;z.chartEpoch++;z.chartQueue=[];z.activeChartRequests=0;z.cardTokens={};z.report=null;z.error='';z.loading=true;z.chartData={};z.chartErrors={};paint(z);
    try{
      const controller=typeof AbortController!=='undefined'?new AbortController():null;
      z.overviewController=controller;
      const options=form({asset_id:z.aid,version_id:z.vid,expected_sha256:z.sha,expected_revision:z.revision,filters:z.filters,field_ids:z.statIds,
        association_ids:z.corrIds,association_method:z.associationMethod});
      if(controller)options.signal=controller.signal;
      const response=await z.ctx.req(`/projects/${u(z.pid)}/feature-lab/data-analysis/dashboard`,options);
      if(state!==z||epoch!==z.queryEpoch)return;
      validate(z,response);
      const actual=response?.metrics?.metric_scope?.field_ids||response?.selection?.field_ids;
      if(Array.isArray(actual)&&JSON.stringify(actual)!==JSON.stringify(z.statIds))
        throw Error('统计字段范围与本次选择不一致，已拒绝显示旧版指标');
      z.report=response;z.loading=false;
      // The server is authoritative about normalized filter operands.
      z.filters=response.filters||[];saveURL(z);paint(z);
      requestCharts(z);
    }catch(err){if(state!==z||epoch!==z.queryEpoch)return;z.error=String(err?.message||err);z.loading=false;paint(z);}
  }
  function mount(host,context){
    if(!host||!context?.source_sha256)return;
    const key=[context.pid,context.aid,context.vid,context.source_sha256,context.contract_revision].join('|');
    if(!state||state.key!==key){
      if(state){if(state.overviewController)state.overviewController.abort();cancelOldCharts(state);}
      const fs=(context.fields||[]).map(f=>({...f,name:f.source_name||f.name}));
      const z={key,host,ctx:context.ctx,pid:context.pid,aid:context.aid,vid:context.vid,
        sha:context.source_sha256,revision:context.contract_revision,
        associationMethod:scopedParam({pid:context.pid,aid:context.aid,vid:context.vid,sha:context.source_sha256,revision:context.contract_revision},'da_corr_method')==='spearman'?'spearman':'pearson',
        sourceFields:fs,report:null,loading:false,error:'',queryEpoch:0,chartEpoch:0,
        chartData:{},chartErrors:{},chartQueue:[],cardTokens:{},chartControllers:new Map(),activeChartRequests:0,newFilterField:fs[0]?.field_id||'',newFilterOp:'eq',
        newFilterValue:'',newFilterUpper:''};
      z.statIds=restoreStatFields(z);
      const nums=fs.filter(f=>z.statIds.includes(f.field_id)&&numeric(f)).map(f=>f.field_id);
      const usable=fs.filter(f=>z.statIds.includes(f.field_id)&&numeric(f)&&!['record_id','order_index','entity_key','event_time','label','prediction_time','available_at'].includes(f.role)).map(f=>f.field_id);
      z.corrIds=restoreSelection(z,'da_corr',10,usable.slice(0,Math.min(8,usable.length))).filter(id=>usable.includes(id));
      z.boxIds=restoreSelection(z,'da_box',16,nums.filter(id=>!/(time|序号|index)$/i.test(fs.find(f=>f.field_id===id)?.name||'')).slice(0,8)).filter(id=>nums.includes(id));
      z.filters=restoreFilters(z);z.cards=restoreCards(z);
      z.chartLoading=false;
      state=z;paint(z);load(z);return;
    }
    // draw() in the surrounding workspace may replace the host DOM while the
    // SQL query is in flight; do not restart or duplicate the request.
    state.host=host;state.ctx=context.ctx;paint(state);
  }
  function dispose(){if(state){if(state.overviewController)state.overviewController.abort();cancelOldCharts(state);state.queryEpoch++;state.chartEpoch++;state.cardTokens={};state.chartQueue=[];}state=null;serial++;}
  window.TimePilotDataDashboard372={mount,dispose,_chartSVG:chartSVG};
})();
