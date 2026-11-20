/* FeatureLab v0.29: model comparison, training-only selection, fold stability and explicit review. */
window.TimePilotFeatureViews=window.TimePilotFeatureViews||{};
window.TimePilotFeatureViews.evaluate=function(s,h){
  const esc=h.e,sets=s.sets||[],report=s.evalResult?.report||null;
  const fmt=(v,unit='')=>v===null||v===undefined||!Number.isFinite(Number(v))?'—':Number(v).toFixed(4)+unit;
  const variants=[['raw_only','原始字段'],['with_features','全部派生'],['selected_features','筛选后'],['auto_search','自动搜索']];
  const metrics=report?.task_type==='regression'?['mae','rmse']:['average_precision','recall','precision','false_reject_rate','balanced_accuracy'];
  const metricTitle={average_precision:'AP（平均精度）',recall:'缺陷召回率',precision:'查准率',false_reject_rate:'误剔率',balanced_accuracy:'平衡准确率',mae:'MAE',rmse:'RMSE'};
  const firstModel=report?.model_families?.[0]||'linear';
  const modelComparison=(report?.model_families||['linear']).map(model=>{
    const stats=report?.stability_summary?.models?.[model]||{};
    const rows=metrics.map(m=>`<tr><th>${esc(metricTitle[m]||m)}</th>${variants.map(([v])=>`<td>${fmt(stats[v]?.[m]?.mean)}${stats[v]?.[m]?.std!=null?` <small>±${fmt(stats[v][m].std)}</small>`:''}${stats[v]?.[m]?.folds?` <small>(${stats[v][m].folds}折)</small>`:''}</td>`).join('')}</tr>`).join('');
    return `<h4>${esc(model==='linear'?'线性模型':'随机森林')}</h4>
      <div class="table-wrap"><table class="data-table tp-fl2-eval-table"><thead><tr><th>指标</th>${variants.map(([v,label])=>`<th>${esc(label)}</th>`).join('')}</tr></thead><tbody>${rows}</tbody></table></div>`;
  }).join('');
  const screenings=(report?.screening_recommendations||[]).map(item=>{
    const words={review_candidate:'达到最小收益门槛',negligible_gain:'改善幅度较小',unstable_or_no_gain:'收益不稳定',insufficient_evidence:'证据不足'};
    return `<span class="tp-fl2-eval-assessment">${esc(variants.find(v=>v[0]===item.variant)?.[1]||item.variant)}：${esc(words[item.verdict]||item.verdict)}（${fmt(item.mean_improvement)} / ${esc(item.positive_folds)} 折改善）</span>`;
  }).join('');
  const selectStats=report?.selection_frequency||[];
  const selectedReview=s.evalSelection?.selection||null;
  const numFolds=report?.folds?.length||0;
  const selectionItems=report?.mode==='supervised'?(s.activeEvalSet?.spec?.features||s.sets.find(x=>x.set_id===s.evalPlan?.set_id)?.spec?.features||[]).map(f=>{
    const freq=selectStats.find(x=>x.feature===f.output_name)?.selected_in_folds||0;
    const checked=selectedReview?selectedReview.feature_ids.includes(f.feature_id):freq>=Math.ceil(numFolds/2);
    return `<label class="tp-fl2-eval-check"><input type="checkbox" name="review_feature" value="${esc(f.feature_id)}" ${checked?'checked':''}>
      <span><strong>${esc(f.display_name||f.output_name)}</strong><small>${esc(f.output_name)} · ${freq}/${numFolds} 折保留</small></span></label>`;
  }).join(''):'';
  const searchItems=report?.searched_candidate_recipes||[];
  const searchChoice=searchItems.map(r=>`<label class="tp-fl2-eval-check"><input type="checkbox" name="review_search" value="${esc(r.candidate_id)}" ${selectedReview?.search_candidate_ids.includes(r.candidate_id)?'checked':''}>
      <span><strong>${esc(r.label)}</strong><small>${esc(r.expression)} · ${esc(r.folds)} 折入选 · 需要审阅并重新物化</small></span></label>`).join('');
  const warnings=[...(report?.limitations||[])];
  const splitLabel={time:'时间滚动验证',grouped_time:'设备隔离时间验证',
    kfold:'静态随机 K 折',stratified:'静态分层 K 折',group:'静态设备/批次分组 K 折'};
  const selectionStability=report?.selection_stability;
  const industrialHoldout=report?.industrial_transfer_holdout?.status==='measured'?report.industrial_transfer_holdout:null;
  const holdoutMetrics=industrialHoldout?.model_metrics?.[firstModel]||{};
  const holdoutScore=report?.task_type==='regression'?'mae':'average_precision';
  const holdoutEvidence=industrialHoldout?`<div class="tp-fl2-eval-card" data-testid="industrial-transfer-holdout"><h4>独立留出设备 / 工况测试</h4>
     <p>留出类型：${esc(industrialHoldout.kind==='unseen_device'?'未见设备':'未见工况')} · 分组 ${esc(industrialHoldout.holdout_values.join('、'))} · 训练 ${esc(industrialHoldout.train_rows)} 条 · 留出 ${esc(industrialHoldout.holdout_rows)} 条</p>
     <p>${esc(metricTitle[holdoutScore]||holdoutScore)}：原始字段 ${fmt(holdoutMetrics.raw_only?.[holdoutScore])} / 全部特征 ${fmt(holdoutMetrics.with_features?.[holdoutScore])} / 筛选后 ${fmt(holdoutMetrics.selected_features?.[holdoutScore])}。</p>
     <p class="muted small">${esc(industrialHoldout.interpretation)} 上游候选可能使用全源无监督画像，只有在候选生成前冻结留出组才能形成严格的盲法评价。 ${industrialHoldout.scope==='group_disjoint_only'?'该结果没有验证未来时间外推。':''}</p>
     <details><summary>留出来源与审计指纹</summary><code>${esc(industrialHoldout.manifest_sha256)}</code></details></div>`:'';
  const featureSlices=(report?.folds||[]).flatMap(f=>(f.group_stability||[]).flatMap(g=>
    (g.groups||[]).flatMap(by=>(by.slices||[]).map(group=>({fold:f.fold,variant:g.variant,field:by.field,...group})))));
  const drift=(report?.folds||[]).flatMap(f=>(f.feature_stability?.fields||[]).slice(0,5).map(d=>({fold:f.fold,...d})));
  const result=report?`<section class="tp-fl2-eval-results">
    <div class="tp-fl2-eval-heading"><div><h3>评价结果</h3><p class="muted">${esc(report.mode==='supervised'?'训练折内筛选 · '+(splitLabel[report.split_kind]||'受控验证')+' · 来源指纹已记录':'无监督探索画像')}</p></div>
      <span class="tp-fl2-eval-status">${esc(report.mode==='supervised'?'已计算':'探索性')}</span></div>
    ${report.mode==='exploration'?`<p>已有特征定义 ${esc(report.feature_count)} 项。${report.feature_artifact?'已找到完整物化产物。':'暂未找到完整物化产物。'} 无监督任务不生成预测性能或模型贡献评价。</p>`:`
      <div class="tp-fl2-eval-kpis"><div><small>验证折数</small><strong>${numFolds}</strong></div><div><small>模型方案</small><strong>${esc(report.model_families?.join('、')||'linear')}</strong></div><div><small>筛选特征</small><strong>${selectStats.filter(x=>x.selected_in_folds>0).length}</strong></div><div><small>自动搜索推荐</small><strong>${searchItems.length}</strong></div></div>
      <div class="tp-fl2-eval-card"><header><h4>模型贡献对照</h4><small>同一折、同一模型、相同训练样本；均值 ± 折间标准差，括号为有效折数</small></header>
        ${modelComparison}
        <div class="tp-fl2-eval-assessments">${screenings}</div>
        <small class="muted">AP 为 Average Precision；分类阈值暂固定 0.5。提高召回率通常需要同时考察误剔率。结果不构成因果推断或统计显著性证明。</small></div>
      ${holdoutEvidence}
      <div class="tp-fl2-eval-grid"><div class="tp-fl2-eval-card"><h4>训练折筛选结果</h4>
         <p class="muted small">每个训练折独立确定保留字段。新配方保存后仍需重新计算、复核和审批。</p>
         ${selectionStability?`<p class="muted small">训练折间筛选一致性：${selectionStability.mean_jaccard==null?'不可评价':fmt(selectionStability.mean_jaccard)}（Jaccard，${selectionStability.compared_pairs} 对折；仅描述稳定性）</p>`:''}
         <form id="tp-fl2-review-form"><div class="tp-fl2-eval-selection-list">${selectionItems||'<p>没有可保留的派生特征。</p>'}</div>
         ${searchChoice?`<h4>自动搜索候选</h4><div class="tp-fl2-eval-selection-list">${searchChoice}</div>`:''}
         <label class="field"><span>审阅意见（可选）</span><input name="review_note" maxlength="1000" value="${esc(selectedReview?.note||'')}" placeholder="为什么保留这些特征"></label>
         <div class="action-row"><button class="button button-primary" type="submit">保存筛选决策</button>
           <button class="button" type="button" data-feature-v2="derive-evaluation-set" ${s.evalSelection?.revision?'':'disabled'}>生成新特征集草稿</button></div>
         </form></div>
       <div class="tp-fl2-eval-card"><h4>跨折稳定性</h4>
         <p class="muted small">PSI 按训练折分位点计算，用验证数据诊断分布变化，不参与筛选。组别只显示达到最小样本要求的切片。</p>
         <div class="table-wrap"><table class="data-table"><thead><tr><th>折</th><th>字段</th><th>PSI</th><th>验证缺失率</th></tr></thead><tbody>
           ${drift.slice(0,12).map(x=>`<tr><td>${esc(x.fold+1)}</td><td>${esc(x.feature)}</td><td>${fmt(x.psi)}</td><td>${fmt(x.valid_missing)}</td></tr>`).join('')||'<tr><td colspan="4">数据不足</td></tr>'}</tbody></table></div>
         <details><summary>设备/工况切片（${featureSlices.length} 项）</summary><div class="table-wrap"><table class="data-table"><thead><tr><th>折</th><th>方案</th><th>字段</th><th>分组</th><th>样本</th><th>AP/MAE</th></tr></thead><tbody>
           ${featureSlices.slice(0,90).map(x=>`<tr><td>${esc(x.fold+1)}</td><td>${esc(x.variant)}</td><td>${esc(x.field)}</td><td>${esc(x.group)}</td><td>${esc(x.rows)}</td><td>${fmt(report.task_type==='regression'?x.metrics?.mae:x.metrics?.average_precision)}</td></tr>`).join('')||'<tr><td colspan="6">未设置已确认的分组字段</td></tr>'}</tbody></table></div></details>
       </div></div>`}
    <details class="tp-fl2-eval-evidence"><summary>实验协议、逐折结果与完整审计数据</summary>
      <pre class="tp-fl2-json">${esc(JSON.stringify(report,null,2))}</pre></details>
    ${warnings.length?`<details><summary>适用边界与限制（${warnings.length}）</summary>${warnings.map(x=>`<p class="muted small">${esc(x)}</p>`).join('')}</details>`:''}
  </section>`:'';
  const sliceFields=(s.contract?.fields||[]).filter(f=>f.confirmation==='confirmed'&&['entity_key','batch_key','regime'].includes(f.role));
  const transferFields=sliceFields.filter(f=>f.classification!=='restricted'&&
      (f.role==='regime'&&f.semantic_type==='category'||f.role==='entity_key'&&s.contract?.entity_field_ids?.includes(f.field_id)));
  return `<section class="tp-fl2-work tp-fl2-eval-work"><header><div><h2>实验评价</h2><p>固定特征集 → 训练折筛选 → 模型对照 → 稳定性诊断 → 审阅保留</p></div></header>
    <form id="tp-fl2-evaluation-form" class="tp-fl2-eval-settings"><div class="tp-fl2-eval-setting-grid">
      <label class="field"><span>特征集</span><select name="set_id" data-feature-v2-change="evaluation-set">${sets.map(r=>`<option value="${esc(r.set_id)}" ${s.activeEvalSet?.set_id===r.set_id?'selected':''}>${esc(r.name)} · 修订 ${r.revision} · ${r.spec?.task?.mode==='supervised'?'监督':'探索'}</option>`).join('')}</select></label>
      <label class="field"><span>模型对照</span><select name="models"><option value="linear">线性模型（默认）</option><option value="both">线性模型 + 随机森林</option><option value="forest">随机森林</option></select></label>
      <label class="field"><span>训练折最多保留特征</span><input name="max_selected" type="number" value="24" min="1" max="100" required></label>
      <label class="field"><span>最高允许缺失率</span><input name="max_missing" type="number" value="0.60" min="0" max="0.99" step="0.05" required></label>
      <label class="field"><span>冗余相关阈值</span><input name="max_corr" type="number" value="0.985" min="0.5" max="1" step="0.005" required></label>
      <label class="field"><span>筛选排序</span><select name="rank_method"><option value="univariate">训练折标签相关性</option><option value="quality_only">仅做质量筛选</option></select></label>
    </div>
    <details class="tp-fl2-eval-advanced"><summary>高级设置：内层搜索、设备/工况泛化验证</summary>
      <label class="tp-fl2-eval-check"><input name="transfer_enabled" type="checkbox"><span><strong>独立留出设备 / 工况评价（可选）</strong><small>必须提前指定留出组；只使用其余数据筛选与训练，不能依据留出结果再调参</small></span></label>
      <div class="tp-fl2-eval-setting-grid">
        <label class="field"><span>泛化类型</span><select name="transfer_kind"><option value="unseen_device">未见设备</option><option value="unseen_regime">未见工况</option></select></label>
        <label class="field"><span>留出分组字段</span><select name="transfer_field"><option value="">请选择已确认设备 / 工况字段</option>${transferFields.map(f=>`<option value="${esc(f.field_id)}">${esc(f.display_name||f.source_name)} · ${esc(f.role)}</option>`).join('')}</select></label>
        <label class="field"><span>留出组值</span><input name="transfer_values" maxlength="500" placeholder="如 设备C；多个值使用英文逗号分隔"></label>
        <label class="field"><span>时间截断（时序任务必填）</span><input name="transfer_cutoff" maxlength="40" placeholder="2026-01-01T00:00:00Z"></label>
      </div>
      <label class="tp-fl2-eval-check"><input name="search_enabled" type="checkbox"><span><strong>启用预算受控的自动特征搜索</strong><small>高计算开销；候选只由内层训练/验证选择，默认关闭</small></span></label>
      <div class="tp-fl2-eval-setting-grid">
      <label class="field"><span>最大候选数</span><input name="search_candidates" type="number" value="32" min="1" max="96"></label>
      <label class="field"><span>每折最多模型拟合次数</span><input name="search_fits" type="number" value="8" min="1" max="24"></label>
      <label class="field"><span>每折搜索时间上限（秒）</span><input name="search_seconds" type="number" value="25" min="2" max="120"></label>
      <label class="field"><span>稳定性分组字段</span><select name="slice_field"><option value="">仅比较各验证折</option>${sliceFields.map(f=>`<option value="${esc(f.field_id)}">${esc(f.display_name||f.source_name)}</option>`).join('')}</select></label></div>
    </details>
    <div class="action-row"><button type="submit" class="button button-primary" ${sets.length?'':'disabled'}>生成评价计划</button>
      ${s.evalPlan?`<button type="button" class="button" data-feature-v2="run-eval">开始评价</button>`:''}</div>
    <p class="muted small">监督评价必须绑定已核验的完整物化产物：时序任务要求 PIT 证据，静态任务要求独立性确认及逐行计算证明。探索任务不展示预测收益。</p></form>
    ${s.job&&s.job.kind==='evaluation'?`<p class="tp-fl2-eval-status">评价任务 ${esc(s.job.status)} <button class="button button-small" data-feature-v2="refresh-job">刷新</button></p>`:''}
    ${result}
  </section>`;
};
