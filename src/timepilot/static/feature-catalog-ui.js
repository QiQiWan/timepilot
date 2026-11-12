window.TimePilotFeatureViews=window.TimePilotFeatureViews||{};
window.TimePilotFeatureViews.catalog=function(s,h){
 const tag={draft:'草稿',pending_review:'待审核',approved:'已审核',published:'已发布'};
 return `<section class="tp-fl2-work"><header><div><h2>特征资产</h2><p>查看定义、版本、来源与审核状态。发布后的资产可以进入 BI 和建模流程。</p></div><button class="button" data-feature-v2="refresh-sets">刷新列表</button></header>
 ${s.sets.length?s.sets.map(r=>`<div class="tp-fl2-catalog-row"><div><strong>${h.e(r.name)}</strong><p class="muted small">${h.e(tag[r.state]||r.state)} · 修订 ${h.e(r.revision)} · ${h.e(r.spec.features.length)} 项特征 · 来源 ${h.e(r.source_sha256.slice(0,12))}</p></div><div class="action-row">
  <button class="button button-small" data-feature-v2="show-lineage" data-sid="${h.e(r.set_id)}">查看定义</button>
  ${r.state==='draft'?`<button class="button button-small" data-feature-v2="request-review" data-sid="${h.e(r.set_id)}">提交审核</button>`:''}
  ${r.state==='pending_review'?`<button class="button button-small" data-feature-v2="approve" data-sid="${h.e(r.set_id)}">审核</button>`:''}
  ${r.state==='approved'?`<button class="button button-primary button-small" data-feature-v2="publish" data-sid="${h.e(r.set_id)}">发布特征资产</button>`:''}
  ${r.state==='published'?`<a class="button button-small" href="/projects/${h.u(s.pid)}/bi/native/${h.u(r.published_asset_id)}">在 BI 中打开</a>`:''}
 </div></div>`).join(''):'<p class="tp-fl2-empty">当前项目还没有 v2 特征集。可以先在“构造特征”中创建草稿。</p>'}
 ${s.lineage?`<div class="tp-fl2-hints"><h3>特征依赖</h3><pre class="tp-fl2-json">${h.e(JSON.stringify(s.lineage,null,2))}</pre></div>`:''}
 </section>`;
};
