/* Research evaluation and reviewed modeling workbench; all actions are explicit. */
(() => {
  'use strict';
  let ctx=null, imports=[],assets=[],cases=[],activeCase=null,activeTrial=null,plan=null,lastSpec=null,trialProtocol=null;
  const ESC=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const API=(path,init={})=>ctx.req('/research'+path,init);
  const POST=value=>({method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(value)});
  const roles=['timestamp','year','series_id','target','available_at','statistical_scope','source_name','country','brand','vehicle'];
  const names={timestamp:'时间',year:'年份',series_id:'实体/序列标识',target:'预测值',available_at:'真实发布时间',statistical_scope:'统计口径',source_name:'数据来源',country:'国家',brand:'品牌',vehicle:'车型'};
  let page='semantic';
  function notice(message,warning=false){return `<div class="helper ${warning?'warning':''}">${ESC(message)}</div>`;}
  function choices(items, selected=''){return items.map(x=>`<option value="${ESC(x)}" ${x===selected?'selected':''}>${ESC(x)}</option>`).join('');}
  function caseForm(item){if(!item)return notice('请先选择一个未完成审核的真实工作表。');
    const columns=['',...item.schema.columns];if(item.schema.kind==='wide_monthly')columns.push('__monthly_value__');
    const saved=item.annotation||item.baseline;
    return `<form id="research-label-form" class="form">
     <div class="helper warning">人工标注前请查看原始工作表。规则建议只作参考，确认操作会记录你的审核声明。</div>
     <p><b>${ESC(item.source_name)} / ${ESC(item.sheet)}</b> · ${ESC(item.case_id.slice(0,12))} · ${ESC(item.status)}</p>
     <div class="grid grid-2"><label class="field">表格结构<select id="research-layout">${choices(['wide_monthly','long_monthly','ordinary_timeseries','ordinary_table','metadata'],saved.layout)}</select></label>
     <label class="field">审核人员标识<input id="research-reviewer" maxlength="100" placeholder="工艺工程师或数据负责人" required></label></div>
     <div class="grid grid-3">${roles.map(r=>`<label class="field">${ESC(names[r])}<select data-role="${r}">${choices(columns,saved.roles?.[r]||'')}</select></label>`).join('')}</div>
     <label class="field">人工确认备注<input id="research-label-note" maxlength="500" placeholder="字段依据、统计口径和疑难点"></label>
     <button class="button button-primary" type="submit">确认并保存人工标注</button></form>`;}
  function trialEditor(t){const available=['',...t.schema.columns];if(t.schema.kind==='wide_monthly')available.push('__monthly_value__');
    return `<form id="research-trial-form" class="form"><div class="helper warning">这是另一位审核人员的实际修正会话。请独立核对原始文件；系统记录提交字段变更和耗时（含空闲时间），不展示人工真值。</div>
    <p><b>${ESC(t.arm==='model'?'模型建议':'规则建议')}</b> · ${ESC(t.reviewer)}</p>
    <label class="field">修正后的结构<select id="research-trial-layout">${choices(['wide_monthly','long_monthly','ordinary_timeseries','ordinary_table','metadata'],t.suggestion.layout)}</select></label>
    <div class="grid grid-3">${roles.map(r=>`<label class="field">${ESC(names[r])}<select data-trial-role="${r}">${choices(available,t.suggestion.roles?.[r]||'')}</select></label>`).join('')}</div>
    <button type="submit" class="button button-primary">提交实际人工修正并计时</button></form>`;}
  function semantic(){return `<div class="page-heading"><div><h1>真实数据语义评估</h1><p>真实工作表建立审核样本；规则和模型建议不会自动成为人工真值。</p></div><a class="button" href="/imports">返回数据导入</a></div>
    <div class="panel"><h2>从已有文件建立待标注清单</h2><div class="action-row"><select id="research-import"><option value="">选择一批已上传文件</option>${imports.map(x=>`<option value="${ESC(x.import_id)}">${ESC(x.filename)} · ${ESC(x.candidate_count)} 区域</option>`).join('')}</select><button data-research="seed" class="button button-primary" type="button">生成标注清单</button></div><p class="muted">创建清单只读取表结构，不做语义结论或模型调用。</p></div>
    <div class="panel"><h2>批量人工标注</h2>
      <p>可下载真实源结构的待审核 CSV，分发给业务专家填写 reviewer、layout、roles_json 并显式勾选 confirmed_by_human=true。系统只接受经人工填写且源文件哈希一致的标注，不把规则建议当作真值。</p>
      <div class="action-row"><a class="button" href="/api/v1/research/semantic/review-template.csv" download>下载待审核模板 CSV</a>
        <input type="file" id="research-review-csv" accept=".csv,text/csv">
        <button class="button button-primary" data-research="review-import" type="button">校验并导入人工标注</button></div></div>
    <div class="panel"><div class="section-row"><h2>待核实工作表</h2><button class="button" data-research="refresh" type="button">刷新</button></div><div id="research-case-list" class="smart-actions"></div><div id="research-case-detail"></div></div>
    <div class="panel"><h2>双方案对照评估</h2><p>准确率只计算已经由人工审核确认的样本，修正量表示与真值的字段差异，并非实测操作时间。</p>
       <label style="display:flex;gap:8px;align-items:center;margin:12px 0"><input type="checkbox" id="research-model-opt-in">允许已配置的大模型读取字段结构（不发送数据行和人工标签），按真实调用记录比较</label>
       <div class="grid grid-3"><label class="field">最多评估案例<input type="number" id="research-cases" min="1" max="200" value="30"></label><label class="field">输入单价（USD/百万 Token）<input type="number" step="0.01" min="0" id="research-in-price" placeholder="需要填写实际报价"></label><label class="field">输出单价（USD/百万 Token）<input type="number" step="0.01" min="0" id="research-out-price" placeholder="需要填写实际报价"></label></div>
       <button class="button button-primary" data-research="evaluate" type="button">执行有真值的对照评估</button><div id="research-evaluation"></div></div>
    <div class="panel"><h2>配对 A/B 评估方案</h2><p>由独立业务人员先确认人工真值，再输入两名不同的复核者。系统对每个案例安排规则组和模型组各一次；两名复核者的分组顺序交叉平衡。模型组只有点击分配任务时才调用已配置的 API。未完成配对前不报告收益。</p>
      <div class="grid grid-3"><label class="field">复核人员甲<input id="research-protocol-a" placeholder="复核人员甲" maxlength="100"></label>
       <label class="field">复核人员乙<input id="research-protocol-b" placeholder="复核人员乙" maxlength="100"></label>
       <label class="field">随机种子<input id="research-protocol-seed" type="number" value="42" min="0" max="2147483647"></label></div>
      <div class="action-row"><button type="button" class="button button-primary" data-research="protocol-create">生成可复现实验分配</button>
        <button type="button" class="button" data-research="protocol-results">查看配对结果</button></div>
      <div id="research-protocol-result"></div></div>
    <div class="panel"><h2>实际人工修改量 A/B 会话</h2><p>先选取已经由独立业务人员审核的工作表，再由复核人员进行真实修正。规则和模型两组分别记录提交变更数量、耗时和最终正确率；不会用推断值冒充点击次数。</p>
      <div class="grid grid-2"><label class="field">复核人员标识<input id="research-trial-reviewer" placeholder="第二位独立复核人员" maxlength="100"></label>
      <label class="field">建议来源<select id="research-trial-arm"><option value="rules">规则基线</option><option value="model">模型建议（会调用已配置模型）</option></select></label></div>
      <div class="action-row"><button class="button button-primary" data-research="trial-start" type="button">开始记录本次复核</button><button class="button" data-research="trial-summary" type="button">查看已完成会话统计</button></div><div id="research-trial-editor"></div><div id="research-trial-summary"></div></div>`;}
  function modeling(){return `<div class="page-heading"><div><h1>受控自主建模</h1><p>按同一滚动窗口对比基线与滞后特征模型。所有实验需要确认，不自动进入生产。</p></div><a class="button" href="/data-assets">统一数据资产</a></div>
    <div class="panel"><h2>选择数据资产与时间序列</h2><form id="research-model-form" class="form"><div class="grid grid-2"><label class="field">资产版本<select id="research-asset" required>${assets.map(x=>`<option value="${ESC(x.asset_id)}|${ESC(x.current_version)}">${ESC(x.name)} / ${ESC(x.current_version.slice(0,10))}</option>`).join('')}</select></label><label class="field">序列标识（必须明确）<input id="research-series" placeholder="填写或粘贴 BI 中的 series_id" required maxlength="180"></label>
       <label class="field">预测跨度（月）<input id="research-horizon" type="number" min="1" max="6" value="1"></label><label class="field">滚动窗口数<input id="research-folds" type="number" min="2" max="6" value="3"></label><label class="field">最少历史月份<input id="research-history" type="number" min="20" max="120" value="24"></label><label class="field">最大秒数<input id="research-seconds" type="number" min="10" max="1800" value="180"></label></div>
       <div class="helper warning">同一选择须限定单一来源与统计口径。所有数据需包含真实可用时间；未核实的历史销售数据会在预检时被拒绝。</div>
       <button type="submit" class="button button-primary">检查数据并生成实验计划</button></form>
       <div id="research-model-plan"></div></div>
    <div class="panel"><h2>已完成实验</h2><button class="button" data-research="list-experiments">刷新实验</button><div id="research-experiment-list"></div></div>`;}
  function mechanisms(){return `<div class="page-heading"><div><h1>机理学习验证</h1><p>使用明确的状态、控制、采样间隔及维修事件进行一阶机理辨识。</p></div></div>
    <div class="panel"><h2>上传机理证据 JSON</h2><p>请提供 JSON 字段：kind、reference、sample_interval_seconds、state_unit、control_unit、state、control、maintenance_indices。真实设备数据须有来源及工程审核。</p>
       <textarea id="research-mechanism-json" rows="9" style="width:100%" placeholder='{"kind":"measured_equipment","reference":"设备试验编号","sample_interval_seconds":60,"state_unit":"MPa","control_unit":"A","state":[...],"control":[...],"maintenance_indices":[]}'></textarea>
       <button class="button button-primary" data-research="mechanism">登记并进行受控验证</button><div id="research-mechanism-result"></div></div>`;}
  async function refreshCases(){cases=await API('/semantic/cases?status=all&limit=200');const list=document.getElementById('research-case-list');if(!list)return;
    list.innerHTML=cases.length?cases.map((c,i)=>`<button type="button" class="button button-small" data-research="case" data-index="${i}">${ESC(c.source_name)} · ${ESC(c.sheet)} · ${c.status==='human_confirmed'?'已确认':'待人工复核'}</button>`).join(''):'<p class="muted">尚无样本；先上传数据再建立待标注清单。</p>';
    if(activeCase){const chosen=cases.find(x=>x.case_id===activeCase);document.getElementById('research-case-detail').innerHTML=caseForm(chosen);}}
  async function refreshExperiments(){const e=await API('/experiments');const dest=document.getElementById('research-experiment-list');if(dest)dest.innerHTML=e.length?`<div class="smart-table-wrap"><table class="data-table"><thead><tr><th>实验</th><th>最佳模型</th><th>状态</th></tr></thead><tbody>${e.map(x=>`<tr><td>${ESC(x.experiment_id.slice(0,16))}</td><td>${ESC(x.selected_model)}</td><td>${ESC(x.status)} <button type="button" class="button button-small" data-research="next-experiment" data-experiment="${ESC(x.experiment_id)}">下一轮建议</button></td></tr>`).join('')}</tbody></table></div>`:'<p class="muted">尚无实验结果。</p>';}
  async function render(args){ctx=args;imports=await ctx.req('/smart-import/imports');assets=await ctx.req('/data-assets');
    const route=new URLSearchParams(location.search);page=route.get('tab')||'semantic';
    ctx.root.innerHTML=`<div class="panel smart-actions"><a class="button ${page==='semantic'?'button-primary':''}" href="/research?tab=semantic">人工语义评估</a><a class="button ${page==='modeling'?'button-primary':''}" href="/research?tab=modeling">自主预测实验</a><a class="button ${page==='mechanisms'?'button-primary':''}" href="/research?tab=mechanisms">机理学习验证</a></div>`+
      (page==='modeling'?modeling():page==='mechanisms'?mechanisms():semantic());
    if(page==='semantic')await refreshCases();if(page==='modeling')await refreshExperiments();}
  async function submit(event){if(event.target.id==='research-label-form'){
    event.preventDefault();const caseId=activeCase;
    const rolesOut={};document.querySelectorAll('[data-role]').forEach(x=>{if(x.value)rolesOut[x.dataset.role]=x.value});
    try{await API('/semantic/cases/'+encodeURIComponent(caseId)+'/review',POST({reviewer:document.getElementById('research-reviewer').value.trim(),
      confirmed_by_human:true,layout:document.getElementById('research-layout').value,
      roles:rolesOut,notes:document.getElementById('research-label-note').value}));await refreshCases();ctx.toast('已记录人工审核，保留来源及修订历史');}
    catch(e){ctx.toast('保存审核失败：'+e.message)}return true;}
    if(event.target.id==='research-trial-form'){event.preventDefault();
      if(!activeTrial)return true;
      const corrected={layout:document.getElementById('research-trial-layout').value,roles:{}};
      document.querySelectorAll('[data-trial-role]').forEach(x=>{if(x.value)corrected.roles[x.dataset.trialRole]=x.value;});
      try{const result=await API('/semantic/trials/'+encodeURIComponent(activeTrial.trial_id)+'/finish',POST({reviewer:activeTrial.reviewer,corrected}));
        activeTrial=null;document.getElementById('research-trial-editor').innerHTML=notice('实际人工提交：'+result.submitted_field_changes+' 项字段变更；耗时 '+result.observed_elapsed_seconds+' 秒（含空闲时间）。');
        ctx.toast('已记录实际复核会话');}
      catch(e){ctx.toast('保存会话失败：'+e.message)}return true;}
    if(event.target.id==='research-model-form'){event.preventDefault();
      try{const [asset_id,version_id]=document.getElementById('research-asset').value.split('|');
        const sid=document.getElementById('research-series').value.trim();if(!sid)throw new Error('必须明确选择一个系列');
        lastSpec={asset_id,version_id,selection:{filters:{series_id:[sid]}},
          horizon:Number(document.getElementById('research-horizon').value),folds:Number(document.getElementById('research-folds').value),
          min_history:Number(document.getElementById('research-history').value),max_seconds:Number(document.getElementById('research-seconds').value)};
        plan=await API('/experiments/plan',POST(lastSpec));document.getElementById('research-model-plan').innerHTML=`<div class="helper"><p>预检通过：${ESC(plan.cohort.rows)} 行、${ESC(plan.cohort.series)} 个系列；${ESC(plan.expected_fit_calls)} 次模型拟合。来源哈希：${ESC(plan.source_sha256.slice(0,16))}</p>
          <p>候选模型：${ESC(plan.models.join('、'))}。机理学习必须另行提供物理证据。</p>
          <button type="button" class="button button-primary" data-research="execute-model">确认执行有限预算模型比较</button></div>`;
      }catch(e){plan=null;document.getElementById('research-model-plan').innerHTML=notice('预检失败：'+e.message,true)}return true;}
    return false;}
  async function click(event){const button=event.target.closest('[data-research]');if(!button)return false;
    try{const action=button.dataset.research;
      if(action==='seed'){const id=document.getElementById('research-import').value;if(!id)throw new Error('请先选择上传记录');
        const result=await API('/semantic/seed/'+encodeURIComponent(id),POST({}));ctx.toast('已生成 '+result.candidate_count+' 个待审核案例');await refreshCases();}
      if(action==='refresh')await refreshCases();
      if(action==='review-import'){
        const file=document.getElementById('research-review-csv')?.files?.[0];
        if(!file)throw new Error('请选择专家填写的 UTF-8 CSV');
        const data=new FormData();data.append('file',file,file.name);
        const response=await fetch('/api/v1/research/semantic/review-import',{method:'POST',body:data});
        let result=await response.json();if(!response.ok)throw new Error(JSON.stringify(result.detail||result));
        ctx.toast('已记录 '+result.imported_human_labels+' 条人工确认；从未自动补全真值');
        await refreshCases();
      }
      if(action==='trial-start'){const selected=cases.find(x=>x.case_id===activeCase);
        if(!selected||selected.status!=='human_confirmed')throw new Error('先选择一份已经独立审核确认的工作表');
        const reviewer=document.getElementById('research-trial-reviewer').value.trim();
        if(!reviewer)throw new Error('请输入复核人员标识');
        const arm=document.getElementById('research-trial-arm').value;
        activeTrial=await API('/semantic/cases/'+encodeURIComponent(activeCase)+'/trial',POST({reviewer,arm}));
        document.getElementById('research-trial-editor').innerHTML=trialEditor(activeTrial);
        ctx.toast('复核计时已开始');}
      if(action==='trial-summary'){const result=await API('/semantic/trials/summary');
        document.getElementById('research-trial-summary').innerHTML=`<pre style="overflow:auto;max-height:380px">${ESC(JSON.stringify(result,null,2))}</pre>`;}

      if(action==='protocol-create'){
        const reviewers=[document.getElementById('research-protocol-a').value.trim(),document.getElementById('research-protocol-b').value.trim()];
        const seed=Number(document.getElementById('research-protocol-seed').value);
        trialProtocol=await API('/semantic/protocols',POST({reviewers,seed,max_cases:50}));
        document.getElementById('research-protocol-result').innerHTML=`<p>分组方案 ${ESC(trialProtocol.protocol_id.slice(0,16))} · ${trialProtocol.cases} 个案例；待实际复核。</p><div class="smart-actions">${trialProtocol.assignments.map((a,i)=>`<button type="button" class="button button-small" data-research="protocol-trial" data-index="${i}">${ESC(a.arm==='model'?'模型':'规则')} · ${ESC(a.reviewer)} · ${ESC(a.case_id.slice(0,9))}</button>`).join('')}</div>`;
      }
      if(action==='protocol-results'){
        if(!trialProtocol)throw new Error('先生成配对试验方案');
        const query=new URLSearchParams();
        const inPrice=document.getElementById('research-in-price')?.value;
        const outPrice=document.getElementById('research-out-price')?.value;
        if(inPrice)query.set('input_usd_per_million',inPrice);
        if(outPrice)query.set('output_usd_per_million',outPrice);
        const result=await API('/semantic/protocols/'+trialProtocol.protocol_id+'/results'+(query.size?'?'+query.toString():''));
        document.getElementById('research-protocol-result').innerHTML+=`<pre style="max-height:320px;overflow:auto">${ESC(JSON.stringify(result,null,2))}</pre>`;
      }
      if(action==='protocol-trial'){
        if(!trialProtocol)throw new Error('缺少配对试验方案');
        const assignment=trialProtocol.assignments[Number(button.dataset.index)];
        if(!assignment)throw new Error('无效的试验分配');
        activeCase=assignment.case_id;
        const detail=document.getElementById('research-case-detail');if(detail)detail.innerHTML='<p class="muted">配对复核期间隐藏此处的参考标签；请独立查看原始文件。</p>';
        activeTrial=await API('/semantic/cases/'+encodeURIComponent(assignment.case_id)+'/trial',POST({
          reviewer:assignment.reviewer,arm:assignment.arm,protocol_id:trialProtocol.protocol_id}));
        document.getElementById('research-trial-editor').innerHTML=trialEditor(activeTrial);
        ctx.toast('已开始配对试验，真值不会显示在复核编辑器');
      }
      if(action==='case'){activeCase=cases[Number(button.dataset.index)].case_id;document.getElementById('research-case-detail').innerHTML=caseForm(cases[Number(button.dataset.index)]);}
      if(action==='evaluate'){const use_model=document.getElementById('research-model-opt-in').checked;
        const p1=document.getElementById('research-in-price').value,p2=document.getElementById('research-out-price').value;
        const value={use_model,max_cases:Number(document.getElementById('research-cases').value)};
        if(p1)value.input_usd_per_million=Number(p1);if(p2)value.output_usd_per_million=Number(p2);
        button.disabled=true;try{const outcome=await API('/semantic/evaluate',POST(value));
          document.getElementById('research-evaluation').innerHTML=`<pre style="overflow:auto;max-height:320px">${ESC(JSON.stringify(outcome,null,2))}</pre>`;
        }finally{button.disabled=false;}}
      if(action==='execute-model'){if(!plan||!lastSpec)throw new Error('实验计划已失效，请重新预检');button.disabled=true;
        try{const outcome=await API('/experiments/execute',POST({spec:lastSpec,plan_sha256:plan.plan_sha256,confirmed:true}));
          document.getElementById('research-model-plan').innerHTML=`<div class="helper">实验完成，待复核。最佳模型：${ESC(outcome.selected_model)}；未部署、未提交生产训练。
          <pre style="max-height:380px;overflow:auto">${ESC(JSON.stringify(outcome.leaderboard,null,2))}</pre></div>`;await refreshExperiments();}finally{button.disabled=false;}}
      if(action==='list-experiments')await refreshExperiments();
      if(action==='next-experiment'){
        const id=button.dataset.experiment;
        const proposal=await API('/experiments/'+encodeURIComponent(id)+'/next-plan');
        const dest=document.getElementById('research-model-plan');
        if(dest)dest.innerHTML='<div class="helper"><strong>下一轮研究建议（不会自动执行）</strong><pre style="overflow:auto;max-height:340px">'+ESC(JSON.stringify(proposal,null,2))+'</pre></div>';
        ctx.toast('下一轮实验建议已生成；执行仍需人工确认');
      }
      if(action==='mechanism'){const raw=JSON.parse(document.getElementById('research-mechanism-json').value);
        const e=await API('/mechanisms/evidence',POST(raw));const r=await API('/mechanisms/'+encodeURIComponent(e.evidence_id)+'/assess',POST({}));
        document.getElementById('research-mechanism-result').innerHTML=`<pre style="overflow:auto">${ESC(JSON.stringify(r,null,2))}</pre>`;}
    }catch(e){ctx.toast('操作失败：'+e.message)}return true;}
  window.TimePilotResearch={render,submit,click};
})();
