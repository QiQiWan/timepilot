/* Analysis plans show real feasibility and steps, never fabricated completions. */
(() => {
  'use strict';
  const H=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function render(plan,edit=false){
    if(!plan)return '';
    const p=plan.plan||{},f=p.feasibility||{},fields=f.defaults?.field_ids||[];
    const status=plan.status,canApply=['ready','awaiting_confirmation'].includes(status);
    const all=p.intent?.numeric_fields||[];
    return `<section class="tp-plan-card" aria-label="分析执行计划">
      <h3>分析计划 <small>${H(status)}</small></h3>
      <div><b>系统理解</b><p>${H((p.intent?.intent_candidates||[]).join('、')||'尚不能识别')}</p></div>
      <div><b>真实数据依据</b><p>${H(fields.join('、')||p.intent?.mentioned_fields?.join('、')||'尚未选择字段')}</p></div>
      <div><b>可行性</b><p>${H(f.status||status)} ${H((f.reasons||[]).join('；'))}</p></div>
      <div><b>执行步骤</b><p>${H((p.steps||[]).map(x=>x.tool).join(' → ')||'无可执行步骤')}</p></div>
      ${f.index_suggestions?.length?`<p class="tp-plan-warning">可能的索引列：${H(f.index_suggestions.join('、'))}。可自行决定是否纳入相关矩阵。</p>`:''}
      ${edit?`<div class="tp-plan-field-pick" id="tp-plan-field-pick">${all.map(n=>`<label><input type="checkbox" value="${H(n)}" ${fields.includes(n)?'checked':''}>${H(n)}</label>`).join('')}</div>`:''}
      <div class="tp-plan-actions">${canApply?`<button class="button button-primary button-small" data-bi-agent="execute-plan">确认执行相关分析</button>`:''}
      ${all.length?`<button class="button button-small" data-bi-agent="${edit?'save-plan-fields':'edit-plan-fields'}">${edit?'确认字段':'调整字段'}</button>`:''}</div>
      <small>数值统计由当前数据源在 DuckDB 中完成；图形必须完成渲染回执。</small></section>`;
  }
  window.TimePilotBIPlanCard={render};
})();
