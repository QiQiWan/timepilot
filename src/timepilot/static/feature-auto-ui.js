/* Guided automatic feature analysis: evidence first, manual tools optional. */
window.TimePilotFeatureViews=window.TimePilotFeatureViews||{};
window.TimePilotFeatureViews.analysis=function(s,h){
  const t={
    title:'\u667a\u80fd\u7279\u5f81\u5206\u6790',
    sub:'\u9009\u62e9\u6570\u636e\u548c\u5206\u6790\u76ee\u6807\uff0c\u7cfb\u7edf\u81ea\u52a8\u5b8c\u6210\u6570\u636e\u7406\u89e3\u3001\u7279\u5f81\u751f\u6210\u4e0e\u8d28\u91cf\u7b5b\u9009\u3002',
    goal:'\u4f60\u60f3\u4ece\u6570\u636e\u4e2d\u4e86\u89e3\u4ec0\u4e48\uff1f',
    defaultGoal:'\u81ea\u52a8\u5206\u6790\u6570\u636e\u7684\u5206\u5e03\u3001\u53d8\u5316\u548c\u5f02\u5e38\uff0c\u63a8\u8350\u53ef\u7528\u7684\u7279\u5f81',
    start:'\u5f00\u59cb\u81ea\u52a8\u5206\u6790',
    rerun:'\u91cd\u65b0\u5206\u6790',
    resume:'\u7ee7\u7eed\u4e0a\u6b21\u5206\u6790',
    report:'\u5206\u6790\u7ed3\u679c',
    data:'\u6570\u636e\u8ba4\u8bc6',
    screening:'\u7279\u5f81\u8d28\u91cf',
    next:'\u4e0b\u4e00\u6b65',
    limitation:'\u9002\u7528\u9650\u5236',
    save:'\u4fdd\u5b58\u4e3a\u7279\u5f81\u96c6\u8349\u7a3f',
    materialize:'\u6b63\u5f0f\u8ba1\u7b97\u5e76\u751f\u6210\u8d44\u4ea7',
    interpret:'\u67e5\u770b\u89e3\u91ca\u4f9d\u636e',
    download:'\u5bfc\u51fa\u5206\u6790\u62a5\u544a',
    manual:'\u624b\u5de5\u8c03\u6574\u7279\u5f81',
    noBenefit:'\u5c1a\u672a\u8bc4\u4ef7\u9884\u6d4b\u6536\u76ca\u3002\u9700\u8981\u5df2\u786e\u8ba4\u7684\u6807\u7b7e\u548c\u65f6\u95f4\u53ef\u7528\u6027\u5951\u7ea6\u3002',
    none:'\u5206\u6790\u5b8c\u6210\u540e\u5c06\u663e\u793a\u7279\u5f81\u3001\u8d28\u91cf\u6307\u6807\u548c\u9009\u62e9\u5efa\u8bae\u3002',
    resetNote:'\u65b0\u6570\u636e\u6216\u5b57\u6bb5\u5951\u7ea6\u53d8\u5316\u540e\u9700\u8981\u91cd\u65b0\u8ba1\u7b97\u3002',
    evidence:'\u7edf\u8ba1\u8bc1\u636e',
    accept:'\u786e\u8ba4\u4e3a\u8bb0\u5f55\u987a\u5e8f',
    runOnly:'\u53ea\u652f\u6301\u6309\u8bb0\u5f55\u6761\u6570\u8ba1\u7b97\u7a97\u53e3\uff0c\u5c1a\u672a\u786e\u8ba4\u771f\u5b9e\u65e5\u5386\u65f6\u95f4\u3002'
  };
  const fmt=v=>v==null||!Number.isFinite(+v)?'\u2014':Number(v).toLocaleString('zh-CN',{maximumFractionDigits:4});
  const pct=v=>v==null?'\u2014':(100*Number(v)).toFixed(1)+'%';
  const sess=s.autoSession, r=sess?.report, choices=r?.quality_rows||[], selected=sess?.selection||[];
  const reviewOnly=sess?.status==='ready'&&sess?.stage==='review';
  const agent=sess?.agent||{};
  const explanation=s.autoExplanation||sess?.explanation;
  const agentLabels={
    'feature.contract.inspect':'数据契约核验',
    'feature.profile.compute':'数据画像',
    'feature.candidate.propose':'候选生成',
    'feature.candidate.preview':'候选实测',
    'feature.quality.screen':'特征质量筛选',
    'feature.report.explain':'结果证据解释'
  };
  const providerLabel=agent.mode==='llm'?'模型辅助 · 受控执行':'规则模式 · 可无模型使用';
  const traceRows=(agent.trace||[]).map(item=>`<li><span>${h.e(agentLabels[item.step]||item.step)}</span><small>${h.e({queued:'已提交',succeeded:'已完成',failed:'失败',blocked:'已阻断'}[item.status]||item.status)}</small></li>`).join('');
  const agentPanel=sess?`<section class="tp-auto-card tp-auto-agent"><div class="tp-auto-agent-heading"><div><strong>智能分析执行记录</strong><span>${h.e(providerLabel)}</span></div><small>${agent.mode==='llm'?'模型仅提供建议；计算和权限校验由系统执行':'字段与特征基于系统规则自动处理'}</small></div>${agent.candidate_adjustment?`<p class="muted small">候选调整：${h.e(agent.candidate_adjustment.provider==='llm'?'模型辅助排序':'依据质量规则')} · ${h.e(agent.candidate_adjustment.reason||'')}</p>`:''}${agent.rejected_proposals?`<p class="tp-auto-agent-warning">${fmt(agent.rejected_proposals)} 次模型建议未通过白名单校验，已退回安全规则。</p>`:''}<details><summary>查看各步骤与工具回执</summary><ol>${traceRows}</ol><p class="muted small">自动流程只能调用数据读取、画像、候选计算、质量筛选和解释工具。保存、物化与发布必须由用户确认。</p><p class="muted small">启用模型时会发送目标文本、允许的字段元信息和聚合统计，不发送原始数据行；受限字段停用外部模型。</p></details></section>`:'';

  const stage={profiling:'\u68c0\u67e5\u6570\u636e',previewing:'\u8ba1\u7b97\u5019\u9009\u7279\u5f81',materializing:'\u751f\u6210\u6b63\u5f0f\u8d44\u4ea7',evaluating:'\u8bc4\u4ef7\u5206\u6790\u7ed3\u679c',ready:'\u5206\u6790\u5b8c\u6210',failed:'\u5206\u6790\u672a\u6210\u529f',needs_input:'\u9700\u8981\u8865\u5145\u6570\u636e'};
  const statusLabel={recommended:'\u5efa\u8bae\u4fdd\u7559',review:'\u9700\u8981\u590d\u6838',exclude:'\u5efa\u8bae\u6392\u9664',unverified:'\u672a\u8ba1\u7b97'};
  const timeExplain={
    sequence_order_candidate:'数字序号可能表示记录先后；不能据此推断真实时间间隔',
    elapsed_time_candidate:'可能是相对运行时长；请先确认数值单位',
    calendar_time:'已识别为日期时间类型；请核实时区和业务含义',
    epoch_candidate:'可能是 Unix 时间戳；请先确认秒/毫秒及时区'
  };
  const anyTime=(s.timeBasis?.fields||[]).filter(x=>x.kind!=='not_time');
  const timePanel=anyTime.length?`<div class="tp-auto-time">${anyTime.slice(0,3).map(x=>`<div><strong>${h.e(x.name)}</strong><span>${h.e({sequence_order_candidate:'\u8bb0\u5f55\u987a\u5e8f\u5019\u9009',elapsed_time_candidate:'\u76f8\u5bf9\u65f6\u95f4\u5019\u9009',calendar_time:'\u65e5\u5386\u65f6\u95f4\u5019\u9009',epoch_candidate:'Epoch \u7f16\u7801\u5019\u9009'}[x.kind]||x.kind)}</span><small>${h.e(timeExplain[x.kind]||x.evidence?.[0]||'')}</small>${x.kind==='sequence_order_candidate'&&!x.confirmed?`<button class="button button-small" data-feature-v2="accept-time-order" data-field="${h.e(x.field_id)}">${t.accept}</button>`:''}</div>`).join('')}</div>`:'';
  const objective=sess?.objective||'explore';
  const source=s.assets?.find(x=>x.native_asset_id===s.asset);
  const sourceName=source?.asset?.name||s.asset||'\u672a\u9009\u62e9';
  const intro=`<section class="tp-auto-card tp-auto-intro"><div><h2>${t.title}</h2><p>${t.sub}</p></div><div class="tp-auto-source"><strong>${h.e(sourceName)}</strong><span>${h.e(s.contract?.fields?.length||0)} \u5217 \u00b7 SHA256 ${h.e((s.contract?.source_sha256||'').slice(0,12))}</span></div>${timePanel}</section>`;
  const hintTopo=sess?.topology||r?.topology;
  const timeHint=hintTopo?.safe_for_windows?'已核实顺序和实体键，窗口将按已确认字段计算；真实时长另需日历时间和时区。':'尚未核实安全顺序或设备分组；系统将优先生成逐行特征。';
  const starter=`<form id="tp-fl2-auto-form" class="tp-auto-card tp-auto-start"><label class="field"><span>${t.goal}</span><textarea name="goal" rows="2" maxlength="1000" placeholder="${t.defaultGoal}">${h.e(s.autoGoal||sess?.goal||'')}</textarea></label><div class="tp-auto-chips">${[['explore','\u627e\u6570\u636e\u89c4\u5f8b'],['anomaly','\u8bc6\u522b\u5f02\u5e38'],['quality','\u8fc7\u7a0b\u8d28\u91cf'],['classification','\u8d28\u91cf\u5206\u7c7b'],['forecast','\u9884\u6d4b\u4efb\u52a1']].map(([k,v])=>`<button type="button" data-feature-v2="auto-preset" data-objective="${k}" class="tp-auto-chip ${s.autoObjective===k?'active':''}">${v}</button>`).join('')}</div><input type="hidden" name="task_hint" value="${h.e(s.autoObjective||'auto')}"><div class="tp-auto-start-footer"><span>${h.e(timeHint)}</span><button type="submit" class="button button-primary">${sess?t.rerun:t.start}</button></div></form>`;
  const steps=[['profile','\u7406\u89e3\u6570\u636e'],['preview','\u751f\u6210\u7279\u5f81'],['review','\u8d28\u91cf\u7b5b\u9009'],['materializing','\u751f\u6210\u8d44\u4ea7'],['evaluation','\u6a21\u578b\u6216\u63a2\u7d22\u8bc4\u4ef7']];
  const stageLine=sess?`<section class="tp-auto-card tp-auto-progress"><div class="tp-auto-progress-header"><strong>${h.e(sess.status==='ready' ? (sess.stage==='review'?'候选特征已分析，待人工审核':sess.stage==='materialized'?'特征资产已建立，探索评价待完成':stage[sess.status]):stage[sess.status]||sess.status)}</strong><span>${fmt(Math.round(100*(sess.progress||0)))}%</span><button type="button" class="button button-small" data-feature-v2="auto-refresh">\u5237\u65b0</button></div><div class="tp-auto-track"><div style="width:${Math.max(0,Math.min(100,100*(sess.progress||0)))}%"></div></div><details><summary>\u67e5\u770b\u8be6\u7ec6\u8fdb\u5ea6</summary><ol>${steps.map(([key,label])=>`<li>${label}${(sess.stage==='evaluated'?steps.length-1:steps.findIndex(z=>z[0]===sess.stage))>=steps.findIndex(z=>z[0]===key)?' \u2713':''}</li>`).join('')}</ol></details>${sess.status==='failed'?`<p class="tp-auto-error">${h.e(sess.error||'')}</p>`:''}${sess.strategy_source?`<p class="tp-auto-strategy muted small">${h.e(sess.strategy_source==='llm'?'\u6a21\u578b\u53c2\u4e0e\u5019\u9009\u63a8\u8350':'\u7edf\u8ba1\u89c4\u5219\u63a8\u8350')}\uff1a${h.e(sess.strategy_reason||'')}</p>`:''}${(sess.warnings||[]).map(x=>`<p class="muted small">${h.e(x)}</p>`).join('')}</section>`:'';
  const action=sess?.next_action;
  const guidedAction=action?`<section class="tp-auto-card tp-auto-action-summary" role="status"><strong>当前建议：${h.e(action.title)}</strong><p>${h.e(action.detail)}</p><small>下一步：${h.e(action.action)}。系统不会自动审批或发布。</small></section>`:'';
  const kpi=r?`<div class="tp-auto-kpis">${[[r.row_count,'\u6570\u636e\u884c\u6570'],[r.candidate_count,'\u5019\u9009\u7279\u5f81'],[r.evaluated_count,'\u5df2\u5b9e\u9645\u8ba1\u7b97'],[r.recommended_count,'\u5efa\u8bae\u4fdd\u7559']].map(([n,label])=>`<div><strong>${fmt(n)}</strong><small>${label}</small></div>`).join('')}</div>`:'';
  const table=choices.length?`<form id="tp-fl2-auto-select" class="tp-auto-card"><div class="tp-auto-heading"><div><h3>${t.screening}</h3><p>\u7ed3\u679c\u6839\u636e\u5168\u91cf\u8ba1\u7b97\u7684\u6709\u6548\u7387\u4e0e\u53d8\u5f02\u60c5\u51b5\uff0c\u6682\u4e0d\u4ee3\u8868\u9884\u6d4b\u91cd\u8981\u6027\u3002</p></div><button type="submit" class="button button-small" ${reviewOnly?'':'disabled'}>\u4fdd\u5b58\u9009\u62e9</button></div><div class="tp-auto-candidate-list">${choices.map(x=>`<label class="tp-auto-candidate ${x.status}"><input type="checkbox" name="candidate_id" value="${h.e(x.candidate_id)}" ${selected.includes(x.candidate_id)?'checked':''} ${x.status==='unverified'||!reviewOnly?'disabled':''}><span class="tp-auto-candidate-title">${h.e(x.display_name||x.output_name)}<small>${h.e(x.output_name)}</small></span><span class="tp-auto-status">${h.e(statusLabel[x.status]||x.status)}</span><span class="tp-auto-metric">\u7f3a\u5931 ${pct(x.quality?.null_rate)}</span><details><summary>${t.evidence}</summary><small>${h.e(x.reason)}. ${h.e(x.operator_id||'')} · rows=${h.e(x.quality?.rows_scanned||0)}, std=${fmt(x.quality?.std)}</small></details></label>`).join('')}</div></form>`:'';
  const topology=r?.topology||sess?.topology;
  const topoBox=r?`<div class="tp-auto-topology"><strong>数据结构识别</strong><p>${h.e(topology?.safe_for_windows?'时间/设备分组通过全量验证，可按确认键计算时序特征。':'尚缺可靠时间或设备分组证据：本次优先计算与记录顺序无关的特征。')}</p>${(topology?.reasons||[]).slice(0,3).map(x=>`<small>${h.e(x)}</small>`).join('')}</div>`:'';
  const intentBox=r?.intent_advice?.requires_confirmation?`<p class="tp-auto-intent muted small">任务理解需要确认：${h.e(r.intent_advice.reason||'')}。系统选择了保守分析路径；如目标不同，可在上方重新指定任务。</p>`:'';
  const modelFeedback=(s.autoDelivery?.same_artifact_evaluation && s.autoDelivery?.evaluation?.mode==='supervised' && s.autoDelivery?.artifact?.sha256===sess?.model_feedback?.artifact_sha256)?sess.model_feedback:null;
  const modelBox=modelFeedback?`<section class="tp-auto-card"><h3>模型贡献证据</h3><p>以下仅来自与当前物化产物相同 SHA256 的外层训练折；仅供审阅，未自动修改特征集，仍需独立生产验证。</p><p>${h.e(modelFeedback.practical_gain_candidate?'筛选后特征有值得人工复核的潜在收益':'尚无充分证据证明筛选后特征具有实际收益')}</p><details><summary>各特征入选训练折次数</summary><ul>${(modelFeedback.selection_frequency||[]).slice(0,24).map(x=>`<li>${h.e(x.feature)}：${fmt(x.selected_in_folds)} / ${fmt(x.total_folds)}</li>`).join('')}</ul></details></section>`:'';
  const advancedBox=r?.advanced_options?.length?`<details class="tp-auto-card"><summary>高级特征算法建议（需单独确认计算预算）</summary>${r.advanced_options.map(x=>`<p><strong>${h.e(x.engine)}</strong>：${h.e(x.why)}。${h.e(x.guard)}</p>`).join('')}</details>`:'';
  const missing=(r?.high_missing_fields||[]).slice(0,6).map(x=>`<span class="tp-auto-pill">${h.e(x.name)}: ${pct(x.null_rate)}</span>`).join('');
  const fieldRows=(sess?.profile?.fields||[]).slice(0,16);
  const fieldTable=fieldRows.length?`<details class="tp-auto-field-table"><summary>查看字段统计（全量画像）</summary><div class="table-wrap"><table class="data-table"><thead><tr><th>字段</th><th>缺失率</th><th>平均值</th><th>标准差</th><th>最小</th><th>最大</th></tr></thead><tbody>${fieldRows.map(f=>`<tr><td>${h.e(f.source_name)}</td><td>${pct(f.null_rate)}</td><td>${fmt(f.mean)}</td><td>${fmt(f.std)}</td><td>${fmt(f.min)}</td><td>${fmt(f.max)}</td></tr>`).join('')}</tbody></table></div></details>`:'';
  const scored=choices.filter(x=>x.quality?.null_rate!=null).slice(0,8);
  const chart=scored.length?`<div class="tp-auto-coverage"><strong>候选有效值覆盖率</strong>${scored.map(x=>{const cover=Math.max(0,Math.min(100,100*(1-Number(x.quality.null_rate))));return `<div class="tp-auto-cover-row"><span title="${h.e(x.output_name)}">${h.e(x.display_name||x.output_name)}</span><div class="tp-auto-cover-track"><div style="width:${cover.toFixed(2)}%"></div></div><small>${cover.toFixed(1)}%</small></div>`}).join('')}<small>该图仅表示有效值覆盖情况，不表示模型贡献。</small></div>`:'';
  const summary=r?`<section class="tp-auto-card"><h3>${t.data}</h3><p>\u5df2\u5bf9 ${fmt(r.row_count)} \u884c\u3001${fmt(r.field_count)} \u5217\u751f\u6210\u6570\u636e\u753b\u50cf\uff1b\u7edf\u8ba1\u53e3\u5f84\uff1a${h.e(r.profile_scope||'')}. ${r.high_missing_fields?.length?'\u90e8\u5206\u5b57\u6bb5\u9700\u8981\u590d\u6838\u7f3a\u5931\u60c5\u51b5\u3002':'\u672a\u53d1\u73b0\u7f3a\u5931\u7387\u8d85\u8fc7 10% \u7684\u5b57\u6bb5\u3002'}</p><div class="tp-auto-pills">${missing}</div>${fieldTable}${chart}</section>`:'';
  const supervisedGoal=['classification','forecast','regression'].includes(objective);
  const sup=s.autoSupervision;
  const selectFields=(name,fields)=>`<label class="field"><span>${h.e(name)}</span><select name="${h.e(name)}" ${fields.length?'':'disabled'}>${fields.map(f=>`<option value="${h.e(f.field_id)}" ${name==='label_field_id'&&f.name===s.autoTargetHint?'selected':''}>${h.e(f.name)} · ${h.e(f.role)}</option>`).join('')}</select></label>`;
  const staticOk=!!sup?.static_eligible;
  const temporalOk=!!(sup?.temporal_eligible??sup?.eligible);
  const staticPreferred=staticOk&&!temporalOk;
  const splitOptions=[
    ...(temporalOk?[["time","按时间滚动验证"],["grouped_time","按设备隔离并按时间验证"]]:[]),
    ...(staticOk&&!sup?.entity_field_ids?.length?[["kfold","静态回归 K 折"],["stratified","静态分类分层 K 折"]]:[]),
    ...(staticOk&&sup?.static_group_fields?.length?[["group","静态设备/批次分组 K 折"]]:[])
  ].filter(([key])=>(key!=='grouped_time'||sup?.entity_field_ids?.length) &&
                     (key!=='stratified'||objective==='classification') &&
                     (key!=='kfold'||objective!=='classification'));
  const defaultSplit=splitOptions.some(([key])=>key===s.autoSplitHint)?s.autoSplitHint:
    (staticPreferred?(sup?.static_default_split||splitOptions[0]?.[0]):'time');
  const supervisedBox=reviewOnly&&supervisedGoal&&!sess.feature_set_id?`<details class="tp-auto-supervision" ${s.autoTargetHint?'open':''}><summary>启用模型对照评价（可选）</summary>
    ${s.autoTargetHint?`<p class="muted small">建模向导建议 Y：${h.e(s.autoTargetHint)}；建议 X：${h.e((s.autoFeatureHints||[]).join('、')||'请手动确认')}。仅为待核对的字段意向，仍须在此确认标签、特征契约和验证切分。</p>`:''}
    ${(temporalOk||staticOk)?`<p class="muted small">有真实预测时间的任务按时间验证；独立静态样本可选择 K 折或分组验证。模型评价前需确认标签与样本独立性。</p>
    <label class="tp-auto-checkbox"><input type="checkbox" name="enable_supervised" value="yes"> 我已核实监督任务标签并希望对比原始字段与新特征</label>
    ${selectFields('label_field_id',sup.label_fields||[])}
    <label class="field"><span>验证方法</span><select name="split_kind">${splitOptions.map(([key,title])=>`<option value="${h.e(key)}" ${key===defaultSplit?'selected':''}>${h.e(title)}</option>`).join('')}</select></label>
    ${temporalOk?`${selectFields('prediction_time_field_id',sup.prediction_time_fields||[])}${selectFields('label_available_at_field_id',sup.label_available_fields||[])}`:''}
    ${staticOk?`<label class="tp-auto-checkbox"><input type="checkbox" name="static_independence_confirmed" value="yes"> 我已核实静态数据的样本独立性，且所有输入字段在实际预测决策时均已可用（否则应改用时间/分组验证）</label>`:''}
    ${staticOk&&sup.static_group_fields?.length?`<label class="field"><span>设备/批次分组字段（分组验证适用）</span><select name="static_group_field"><option value="">使用已确认的实体键</option>${sup.static_group_fields.map(f=>`<option value="${h.e(f.field_id)}">${h.e(f.name)}</option>`).join('')}</select></label>`:''}`:
    `<p class="muted small">${h.e((sup?.issues||[]).concat(sup?.static_issues||[]).filter(Boolean).join('；')||'请先核实标签和字段契约')}。可先保存为探索特征。</p>`}
    </details>`:'';
  const proof=s.autoDelivery;
  const proofBox=sess?.artifact?`<div class="tp-auto-delivery"><strong>结果来源核对：${h.e(proof?.status||'核验中')}</strong><small>物化 SHA256 ${h.e(proof?.artifact?.sha256?.slice(0,20)||'待核实')} · 模型评价 ${proof?.same_artifact_evaluation?'同源校验通过':'待校验'} · BI ${proof?.same_artifact_bi_receipt?'客户端双面板回执已记录':'待浏览器验证'}</small><small>客户端回执不能替代独立浏览器验收。${h.e((proof?.limitations||[]).slice(0,1).join('；'))}</small>${(proof?.evaluation?.summary||[]).slice(0,3).map(x=>`<small>${h.e(x.variant)}：${h.e(x.verdict)}；同折平均改善 ${fmt(x.mean_improvement)}，不作为自动发布依据。</small>`).join('')}</div>`:'';
  const deliver=sess?.status==='ready'&&r?`<section class="tp-auto-card"><h3>${t.next}</h3><p>${proof?.same_artifact_evaluation&&proof?.evaluation?.mode==='supervised'?'已完成绑定同一 Parquet 产物的监督模型评价；具体对照见下方和导出报告。':t.noBenefit}</p>${sess.feature_set_id?`<p>\u7279\u5f81\u96c6\u8349\u7a3f ID: <code>${h.e(sess.feature_set_id)}</code> ${sess.artifact?'\u00b7 \u6b63\u5f0f\u4ea7\u7269 SHA256 '+h.e(sess.artifact.sha256?.slice(0,16)):'\u00b7 \u5c1a\u672a\u7269\u5316'}</p>`:''}<div class="tp-auto-actions"><form id="tp-fl2-auto-save"><input name="name" maxlength="120" required value="${h.e(s.autoSetName||sourceName+' feature analysis')}" aria-label="FeatureSet name">${supervisedBox}<label class="tp-auto-checkbox"><input type="checkbox" name="confirm_materialize" value="yes" required> 我已核对候选，确认保存并开始正式计算（不自动发布）</label><button type="submit" class="button button-primary" ${reviewOnly && selected.length&&!sess.feature_set_id?'':'disabled'}>确认采用并计算</button></form>${proofBox}${sess.feature_set_id&&!sess.artifact?`<button type="button" class="button" data-feature-v2="auto-materialize">${t.materialize}</button>`:''}${sess.evaluation?`<p class="tp-auto-evaluation-status">${proof?.same_artifact_evaluation&&proof?.evaluation?.mode==='supervised'?'已完成同源监督评价，结果需结合各验证折与业务风险审阅。':'探索评价已完成；尚无可靠的预测性能证据。'}</p>`:''}${sess.artifact?`<button type="button" class="button" data-feature-v2="auto-open-bi">\u5728 BI \u67e5\u770b\u7279\u5f81</button>`:''}${sess.artifact?`<button type="button" class="button" data-feature-v2="auto-export-evidence">导出同源核验 JSON</button>`:''}<button type="button" class="button" data-feature-v2="auto-report">${t.download}</button><button type="button" class="button" data-feature-v2="auto-explain">${t.interpret}</button></div>${explanation?`<blockquote class="tp-auto-explanation"><strong>${h.e(explanation.headline)}</strong><p>${h.e(explanation.summary)}</p><small>${h.e(explanation.provider==='llm_curated'?'模型辅助选择的实测证据':'系统根据实测数据整理')}</small>${(explanation.claims||[]).length?`<details><summary>逐条查看依据</summary><ul>${explanation.claims.map(item=>`<li>${h.e(item.text)} <code>${h.e(item.id)}</code></li>`).join('')}</ul></details>`:''}</blockquote>`:''}</section>`:'';
  return `<div class="tp-auto-workbench">${intro}${starter}${stageLine}${guidedAction}${agentPanel}${kpi}${topoBox}${intentBox}${summary}${table}${modelBox}${advancedBox}${deliver}${!r?`<section class="tp-auto-card tp-auto-empty"><h3>${t.report}</h3><p>${t.none}</p><div class="tp-auto-outputs"><span>\u2460 \u6570\u636e\u8ba4\u8bc6</span><span>\u2461 \u7279\u5f81\u5019\u9009</span><span>\u2462 \u8d28\u91cf\u8bc1\u636e</span><span>\u2463 \u8d44\u4ea7\u4e0e\u62a5\u544a</span></div></section>`:''}${r?.limitations?.length?`<details class="tp-auto-card"><summary>${t.limitation}</summary>${r.limitations.map(x=>`<p class="muted small">${h.e(x)}</p>`).join('')}</details>`:''}<details class="tp-auto-advanced"><summary>\u9ad8\u7ea7\u5de5\u5177\u4e0e\u624b\u5de5\u8bbe\u7f6e</summary><p>\u5df2\u6709\u7b97\u5b50\u3001\u624b\u5de5\u516c\u5f0f\u3001tsfresh\u3001Featuretools \u548c\u6a21\u578b\u8bc4\u4ef7\u529f\u80fd\u53ef\u7ee7\u7eed\u4f7f\u7528\u3002</p><a href="/projects/${h.u(s.pid)}/features/build?asset=${h.u(s.asset)}">${t.manual}</a></details></div>`;
};
