/* v0.35.5 Evidence-linked judgement brief. Server owns every calculation and claim. */
(() => {
  'use strict';
  const enc=encodeURIComponent;
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const n=v=>typeof v==='number'&&Number.isFinite(v)?v.toLocaleString('zh-CN',{maximumFractionDigits:5}):'未评价';
  let current=null,seq=0,timer=null,listener=null;
  const labels={quality:'数据质量',statistics:'统计图表',features:'自动特征',outputs:'成果与评价',review:'自动特征'};
  function cleanup(){seq++;clearTimeout(timer);timer=null;current=null;
    if(listener){document.removeEventListener('timepilot:feature-preview-ready',listener);listener=null;}}
  function proofText(terms){return `<details class="tp-ai-evidence"><summary>查看依据（${terms?.length||0}）</summary>${(terms||[]).map(x=>`<p>${esc(x.kind)} · ${esc(x.path)}${x.field_id?' · '+esc(x.field_id):''}${x.evaluation_id?' · 评价 '+esc(x.evaluation_id):''}<br><small>来源 SHA256 ${esc(x.source_sha256)}${x.artifact_sha256?' · 特征产物 SHA256 '+esc(x.artifact_sha256):''}</small></p>`).join('')||'<p>无可用证据</p>'}</details>`;}
  function findings(rows){return (rows||[]).map(x=>`<article class="tp-ai-finding"><div><b>${esc(x.title)}</b><p>${esc(x.observation)}</p>${proofText(x.evidence)}</div><button type="button" class="button button-small" data-ai-section="${esc(labels[x.action]?x.action:'features')}">查看${esc(labels[x.action]||'特征')}</button></article>`).join('');}
  function renderContent(data){
    if(data?.status==='stale')return `<div class="tp-ai-blocked" data-testid="insights-stale">来源已变更。请重新分析当前数据，旧版结论暂不显示。</div>`;
    const layers=data?.layers||{};
    const quality=layers.data_quality||{},feature=layers.feature_quality||{},information=layers.information_value||{};
    const model=data?.model||{status:'not_evaluated',message:'模型尚未评价。',comparisons:[]};
    const stat=quality.metrics||{},feat=feature.metrics||{};
    const table=model.status&&Array.isArray(model.comparisons)&&model.comparisons.length&&
      !['awaiting_verified_evaluation','no_confirmed_label','blocked','not_evaluated'].includes(model.status)
      ?`<table class="tp-ai-model-table" data-testid="insights-model-comparison"><thead><tr><th>方案</th><th>${esc(model.metric_label||'评价指标')}</th><th>有效折数</th></tr></thead><tbody>${model.comparisons.map(x=>`<tr><td>${esc(x.label)}</td><td>${n(x.mean)}</td><td>${esc(x.valid_folds)}</td></tr>`).join('')}</tbody></table>`:'';
    const deltas=Array.isArray(model.variant_deltas)&&model.variant_deltas.length?
      `<section class="tp-ai-comparison-373" data-testid="verified-feature-comparison"><h4>特征方案同折比较</h4>${model.variant_deltas.map(x=>`<p><b>${esc(x.variant==='with_features'?'全部候选特征':'筛选后特征')}</b>：相对原始字段，平均改善 <strong>${n(x.mean_delta)}</strong>；${esc(x.positive_folds)}/${esc(x.verified_fold_count)} 折改善。${x.verdict==='validation_regression'?'当前验证表现退化，建议保留原始字段基线。':x.verdict==='validation_gain'?'验证折显示改善，仍需独立测试集确认。':'不同折表现不稳定，尚无稳定收益证据。'}</p>`).join('')}<p class="tp-ai-limit">同源物化产物、相同验证折及指标已经核验；没有独立测试集或生产收益保证。</p></section>`:'';
    const support=model.supporting_metrics?.length?`<details class="tp-ai-optional"><summary>其他同折评价指标</summary>${model.supporting_metrics.map(x=>`<p>${esc(x.metric)}（${esc(x.fold_count)} 折）：原始字段 ${n(x.values[0])} · 全部候选 ${n(x.values[1])} · 筛选后 ${n(x.values[2])}</p>`).join('')}</details>`:'';
    const evidence=model.evidence?.length?proofText(model.evidence):'';
    const stability=model.selection_stability?`<p class="tp-ai-secondary">训练折选择一致性 Jaccard：${n(model.selection_stability.mean_jaccard)}（${esc(model.selection_stability.compared_pairs)} 对折；仅描述稳定性）</p>`:'';
    const frequency=model.selection_frequency?.features?.length?`<details class="tp-ai-optional"><summary>单特征训练折入选次数</summary>${model.selection_frequency.features.slice(0,8).map(f=>`<p>${esc(f.feature)}：${esc(f.selected_in_folds)} / ${esc(f.total_folds)} 折入选</p>`).join('')}<small>${esc(model.selection_frequency.interpretation)}</small>${proofText(model.selection_frequency.evidence)}</details>`:'';
    const ablation=model.ablation?.groups?.length?`<details class="tp-ai-optional"><summary>特征组消融与限制</summary>${model.ablation.groups.map(g=>`<p>${esc(g.group)}：删除后指标损失 ${n(g.metric_loss_when_removed)}；${esc(g.positive_loss_folds)}/${esc(g.evaluated_folds)} 折变差。</p>`).join('')}<small>${esc(model.ablation.interpretation)}</small>${proofText(model.ablation.evidence)}</details>`:'';
    return `<section class="tp-ai-panel" data-testid="evidence-judgement-panel"><header><div><h2>自动研判</h2><p>基于真实数据、候选计算与已核验的模型实验。质量评价和预测收益分别报告。</p></div><button type="button" class="button button-small" data-ai-refresh="1">更新研判</button></header>
      <div class="tp-ai-grid"><article class="tp-ai-card"><h3>数据质量</h3><strong>${quality.status==='measured'?'检查完成':'待分析'}</strong><p>${quality.status==='measured'?`检查 ${esc(stat.scanned_fields??0)} 个字段，${esc(stat.high_missing_fields??0)} 个字段缺失率不低于 10%。`:'数据画像完成后显示实际质量结论。'}</p>${findings(quality.findings)}</article>
      <article class="tp-ai-card"><h3>特征质量</h3><strong>${feature.status==='measured'?`${esc(feat.quality_passed??0)} 项建议保留`:'待计算'}</strong><p>${feature.status==='measured'?`${esc(feat.evaluated_candidates??0)} 项候选有质量记录，${esc(feat.needs_review??0)} 项待复核，${esc(feat.unverified??0)} 项尚未完成验证。`:'候选质量检查完成后显示结果。'}</p>${findings(feature.findings)}</article>
      <article class="tp-ai-card"><h3>特征信息</h3><strong>${information.status==='descriptive_only'?'统计变化已检查':'待评价'}</strong><p>统计波动反映候选的变化情况。特征冗余、预测信息价值仍需有针对性的分析。</p>${findings(information.findings)}</article></div>
      <article class="tp-ai-model" data-testid="insights-model-status" data-model-state="${esc(model.status)}"><div><h3>模型价值</h3><p class="tp-ai-verdict">${esc(model.message)}</p>${model.fold_count?`<p class="tp-ai-secondary">验证协议：${esc(model.split_kind||'已确认')} · ${esc(model.fold_count)} 折 · ${esc(model.paired_folds||0)} 折参与同折对照</p>`:''}</div>${table}${deltas}${support}${stability}${frequency}${ablation}${evidence}${(model.limitations||[]).length?`<p class="tp-ai-limit">${esc(model.limitations.join('；'))}</p>`:''}</article>
      <p class="tp-ai-footer">统计特征通过质量检查仅证明可计算性；没有已确认标签和同源模型评价时，不提供预测准确率或收益数值。<details><summary>本次研判来源</summary><small>来源 SHA256：${esc(data.source_sha256)} · 字段契约修订 ${esc(data.contract_revision)}。研判规则不依赖模型 API。</small></details></p></section>`;
  }
  async function refresh(){
    clearTimeout(timer);timer=null;
    const s=current;if(!s||!s.host?.isConnected)return;
    const id=++seq;
    const url=`/projects/${enc(s.pid)}/feature-lab/workspace/insights?asset_id=${enc(s.asset)}&version_id=${enc(s.version)}`;
    try{
      const data=await s.ctx.req(url);
      if(id!==seq||s!==current||!s.host.isConnected)return;
      const params=new URLSearchParams(location.search);
      const currentSource=window.TimePilotDataAnalysisUI?.getContext?.();
      if(currentSource&&(currentSource.aid!==s.asset||currentSource.vid!==s.version))return;
      if(params.get('asset')&&params.get('asset')!==s.asset)return;
      if(data.source_sha256!==s.sourceHash&&s.sourceHash)throw Error('数据来源已变化，请刷新页面');
      s.sourceHash=data.source_sha256;
      s.host.innerHTML=renderContent(data);
      s.host.querySelectorAll('[data-ai-section]').forEach(button=>button.addEventListener('click',()=>window.TimePilotAnalysisWorkspace?.activate(button.dataset.aiSection,true)));
      s.host.querySelector('[data-ai-refresh]')?.addEventListener('click',()=>refresh());
      // Read-only polling, used only for in-flight analysis; manual evaluation
      // can be refreshed with one click without restarting any computation.
      if(['profiling','previewing','materializing','evaluating'].includes(data.task_status)||
         (data.layers?.feature_quality?.status==='pending'&&data.task_status!=='ready'))
        timer=setTimeout(()=>refresh(),5500);
    }catch(error){if(id===seq&&s===current){s.host.innerHTML=`<div class="tp-ai-blocked">研判暂不可用：${esc(error?.message||error)}。数据分析和特征工程仍可分别使用。<button type="button" class="button button-small" data-ai-refresh="1">重试</button></div>`;
      s.host.querySelector('[data-ai-refresh]')?.addEventListener('click',()=>refresh());}}
  }
  function mount(ctx,pid,host,assetOverride=null,versionOverride=null){
    cleanup();if(!host)return;
    const params=new URLSearchParams(location.search),asset=assetOverride||params.get('asset');
    if(!asset){host.innerHTML='<p class="muted">选择数据后，自动呈现有证据的研判结果。</p>';return;}
    current={ctx,pid,host,asset,version:versionOverride||params.get('version')||'base',sourceHash:null};
    host.innerHTML='<p class="muted">正在核对数据与模型证据…</p>';
    listener=event=>{if(event.detail?.pid===pid&&event.detail?.asset_id===current?.asset&&
      event.detail?.version_id===current?.version)refresh();};
    document.addEventListener('timepilot:feature-preview-ready',listener);
    refresh();
  }
  window.TimePilotAnalysisInsightsUI={mount,refresh,cleanup,renderContent};
})();
