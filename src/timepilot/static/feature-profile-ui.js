/* FeatureLab 2.0: Profile view (no duplicated chart state or browser prompt). */
window.TimePilotFeatureViews=window.TimePilotFeatureViews||{};
window.TimePilotFeatureViews.profile=function(s,h){
  const profile=s.profile;
  const data=profile?`<div class="tp-fl2-counters"><div><b>${h.e(profile.rows_scanned)}</b><small>实际扫描记录</small></div><div><b>${h.e(profile.fields?.length)}</b><small>字段数</small></div><div><b>${h.e(profile.scope)}</b><small>统计范围</small></div><div><b>${h.e(profile.time?.status==='verified'?'时间已确认':'时间待核实')}</b><small>时间语义</small></div></div>
   <div class="table-wrap"><table class="data-table"><thead><tr><th>字段</th><th>缺失率</th><th>最小值</th><th>最大值</th><th>平均值</th></tr></thead><tbody>
   ${profile.fields.map(f=>`<tr><td>${h.e(f.source_name)}</td><td>${f.null_rate===null?'—':(100*f.null_rate).toFixed(2)+'%'}</td><td>${h.e(f.min??'—')}</td><td>${h.e(f.max??'—')}</td><td>${h.e(f.mean??'—')}</td></tr>`).join('')}</tbody></table></div>
   <p class="muted small">统计依据：${h.e(profile.source_sha256?.slice(0,16))} · ${h.e(profile.scope==='full'?'完整已授权数据':'局部数据')}</p>`:
   `<div class="tp-fl2-empty"><b>尚未生成数据画像</b><p>可检查每个字段的真实缺失率、数值范围与时间结构。分析在后台运行，重新进入页面后可查看任务。</p></div>`;
  return `<section class="tp-fl2-work"><header><div><h2>数据画像</h2><p>先了解数据结构、缺失情况和变化范围，再决定需要计算哪些特征。</p></div>
    <div class="action-row"><button class="button" data-feature-v2="edit-contract">检查字段含义</button><button class="button button-primary" data-feature-v2="run-profile">生成完整画像</button></div></header>
    ${s.job?`<div class="tp-fl2-job">任务：${h.e(s.job.status)} · ${h.e(s.job.error_text||'')} <button class="button button-small" data-feature-v2="refresh-job">刷新状态</button></div>`:''}
    ${data}<div class="tp-fl2-hints"><strong>下一步</strong><p>如果字段时间或设备编号尚未明确，点击“检查字段含义”进行确认。完成后进入“构造特征”。</p></div></section>`;
};
