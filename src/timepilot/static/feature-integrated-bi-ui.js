/* v0.35.6: real Perspective viewer inside the analysis workspace. */
(() => {
  'use strict';
  const V=window.TimePilotFeatureViews=window.TimePilotFeatureViews||{};
  V.bi=(s,h)=>{
    const proof=s.biProof,artifact=proof?.artifact;
    const source=s.assets?.find(x=>x.native_asset_id===s.asset);
    const eligible=!!(artifact && proof?.feature_set && proof?.source?.asset_id===s.asset &&
      proof?.source?.version_id===(s.version||'base') &&
      proof?.source?.sha256===s.contract?.source_sha256 &&
      proof?.status!=='blocked' && proof?.status!=='stale');
    return `<section class="tp-fl2-work tp-fl2-integrated-bi" data-testid="integrated-bi">
      <header><div><h2>BI 数据工作区</h2><p>${h.e(source?.asset?.name||s.asset||'请选择数据')} · 版本 ${h.e(s.version||'base')}。使用原生 Perspective 查看当前数据及正式特征成果。</p></div></header>
      ${s.biError?`<p class="helper warning" role="alert">${h.e(s.biError)}</p>`:''}
      <div class="tp-fl2-viewer-section is-open" aria-label="Perspective 数据和特征工作区">
        <div class="tp-fl2-viewer-toolbar"><strong>数据与特征对照</strong><div class="action-row">
          <button type="button" class="button button-small" data-feature-v2="open-viewer">加载源数据</button>
          <button type="button" class="button button-primary button-small" data-feature-v2="bi-verified-compare" ${eligible?'':'disabled'}>查看正式特征</button>
          <button type="button" class="button button-small" data-feature-v2="save-viewer" ${eligible?'':'disabled'}>保存视图</button>
          <button type="button" class="button button-small" data-feature-v2="bi-receipt" ${eligible?'':'disabled'}>核对图表回执</button>
        </div></div>
        <p class="muted small" id="tp-fl2-viewer-status" role="status">${eligible?'已找到同源正式特征；正在加载原生图表。':'可先查看源数据。正式特征须完成物化及同源校验才可对照。'}</p>
        <div id="tp-fl2-perspective" class="tp-fl2-perspective"><p class="muted">正在准备 Perspective 工作区…</p></div>
      </div>
      <p class="muted small">${eligible?`正式产物 ${h.e(artifact.row_count)} 行，SHA256 ${h.e(artifact.sha256.slice(0,16))}。视图保存绑定当前特征集和源版本。`:'预览候选不等于正式特征结果；可在自动特征页面确认并物化后再返回这里。'}</p>
      <details class="tp-wb-proof"><summary>来源与权限证据</summary><p>源文件 SHA256：${h.e(s.contract?.source_sha256||'—')}</p>
        ${eligible?`<p>FeatureSet ${h.e(proof.feature_set.set_id)} · 物化 SHA256 ${h.e(artifact.sha256)}</p>`:''}
        <p>图表来自当前项目的授权数据。客户端渲染回执不能单独证明生产数据正确性。</p></details>
    </section>`;
  };
})();
