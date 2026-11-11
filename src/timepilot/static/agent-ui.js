/* TimePilot unified agent control center: workflow state belongs to the server. */
(() => {
  'use strict';
  let ctx=null, timer=null, active=null, imports=[], assets=[], skills=[];
  const H=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
  const api=(path,opt={})=>ctx.req('/agent'+path,opt);
  const post=body=>({method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const goalNames={understand:'理解原始文件',quality:'检查数据质量',analyze:'BI 分析',features:'特征工程',forecast:'生成预测草稿',semantic_benchmark:'生成人工标注清单',autonomous_modeling:'自主预测实验',mechanism:'机理验证'};
  const statusNames={queued:'等待执行',running:'执行中',waiting_approval:'等待人工确认',succeeded:'已完成',failed:'执行失败',cancelled:'已取消',needs_reconciliation:'需要人工核对写入'};
  function message(text,danger=false){return `<div class="helper ${danger?'danger':'warning'}">${H(text)}</div>`;}
  async function loadViews(){const v=document.getElementById('agent-asset')?.value||'';
    const target=document.getElementById('agent-view');if(!target)return;
    target.innerHTML='<option value="">请选择已保存的视图</option>';
    if(!v)return;
    const [id,vid]=v.split('|');
    try{const values=await ctx.req(`/data-assets/${encodeURIComponent(id)}/versions/${encodeURIComponent(vid)}/views`);
      target.innerHTML='<option value="">请选择已保存的视图</option>'+(values||[]).map(x=>`<option value="${H(x.view_id)}">${H(x.name)}</option>`).join('');
    }catch{target.innerHTML='<option value="">视图加载失败，请先保存筛选视图</option>'}
  }
  function form(){return `<div class="panel"><h2>新建受控任务</h2><p class="muted">选择目标与数据。文件检查、分析和质量评估只读执行；导出特征和预测草稿需要单独确认。</p>
    <form id="agent-job-form" class="form"><div class="grid grid-2">
      <label class="field">任务目标<select id="agent-goal" required>${Object.entries(goalNames).map(([k,v])=>`<option value="${k}">${v}</option>`).join('')}</select></label>
      <label class="field" id="agent-import-label">原始导入<select id="agent-import"><option value="">请选择</option>${imports.map(x=>`<option value="${H(x.import_id)}">${H(x.filename)} · ${H(x.candidate_count)} 区域</option>`).join('')}</select></label>
      <label class="field" id="agent-asset-label" style="display:none">数据资产<select id="agent-asset"><option value="">请选择</option>${assets.map(x=>`<option value="${H(x.asset_id)}|${H(x.current_version)}">${H(x.name)} · ${H(x.current_version)}</option>`).join('')}</select></label>
      <label class="field" id="agent-view-label" style="display:none">预测视图<select id="agent-view"><option value="">请选择已保存的视图</option></select></label>
      <label class="field" id="agent-mechanism-label" style="display:none">机理证据编号<input id="agent-mechanism" maxlength="64" placeholder="先到自主建模页登记证据"></label>
      <label class="field" id="agent-auto-series-label" style="display:none">预测系列 ID<input id="agent-auto-series" maxlength="180" placeholder="从 BI 中选择一条系列"></label>
      <label class="field" id="agent-auto-history-label" style="display:none">最少历史月份<input id="agent-auto-history" type="number" min="20" max="120" value="24"></label>
      <label class="field" id="agent-lag-label" style="display:none">滞后阶数<select id="agent-lags"><option value="1,3,12">1、3、12 期</option><option value="1,3">1、3 期</option><option value="1">1 期</option></select></label>
      <label class="field">最大工具步骤<input id="agent-max-steps" type="number" min="1" max="24" value="10" required></label>
      <label class="field">工具计算预算<input id="agent-max-units" type="number" min="1" max="100" value="30" required></label>
      <label class="field">最大执行秒数<input id="agent-max-seconds" type="number" min="10" max="3600" value="300" required></label></div>
      <label class="field">任务备注（不会改变工具规划）<textarea id="agent-instruction" rows="2" maxlength="600" placeholder="例如：检查字段歧义、月份覆盖和来源冲突"></textarea></label>
      <label style="display:flex;align-items:center;gap:10px"><input type="checkbox" id="agent-use-model">允许已配置的大模型在限定工具中建议下一步（模型失败自动降级）</label>
      <label id="agent-export-label" style="display:none;align-items:center;gap:10px"><input type="checkbox" id="agent-export">预览完成后申请导出特征文件</label>
      <div class="action-row"><button class="button button-primary" type="submit">创建任务</button><span class="muted small">不会自动启动训练，也不自动执行写操作。</span></div>
    </form></div>`;}
  function jobView(j){const budget=j.budget||{};
    return `<div class="panel"><div class="section-row"><div><h2>任务 ${H(j.job_id.slice(0,8))} · ${H(statusNames[j.status]||j.status)}</h2><p class="muted">${H(goalNames[j.goal]||j.goal)} · 已使用 ${H(budget.used_units)} / ${H(budget.max_units)} 单位，${H(budget.used_seconds)} / ${H(budget.max_seconds)} 秒 · 模型 ${H(j.model_status)}</p></div>
     <div class="action-row"><button type="button" class="button" data-agent="refresh">刷新</button>${['queued','running','waiting_approval'].includes(j.status)?'<button type="button" class="button" data-agent="cancel">取消</button>':''}${j.status==='failed'?'<button type="button" class="button" data-agent="retry">重试</button>':''}</div></div>
     ${j.last_error?message(j.last_error,true):''}
     ${j.pending_approval?`<div class="helper warning"><strong>需要确认写入：${H(j.pending_approval.tool)}</strong><p>仅按下方已锁定的参数执行。请核对数据资产、版本和工具权限。</p><details><summary>查看受控参数</summary><pre style="white-space:pre-wrap;overflow-wrap:anywhere">${H(JSON.stringify(j.pending_approval.arguments,null,2))}</pre></details><button type="button" class="button button-primary" data-agent="approve">确认执行该写操作</button></div>`:''}
     <div class="smart-table-wrap"><table class="data-table"><thead><tr><th>执行工具</th><th>状态</th><th>耗时</th><th>证据 SHA-256</th></tr></thead><tbody>${j.steps.map(s=>`<tr><td>${H(s.name)}</td><td>${H(statusNames[s.status]||s.status)}</td><td>${H(s.elapsed_seconds)} 秒</td><td class="mono">${H((s.result_sha256||'—').slice(0,16))}</td></tr>`).join('')}</tbody></table></div>
     ${j.status==='succeeded'?message('任务已完成。只读分析不会修改原始数据；预测草稿需要进一步核对才能提交训练。'):''}
     ${j.status==='needs_reconciliation'?message('写入状态未知。请检查已有版本或草稿，再人工处理；系统不会自动重试。',true):''}</div>`;}
  async function fetchJob(){if(!active||!ctx||!location.pathname.startsWith('/agent'))return;
    try{const data=await api('/jobs/'+encodeURIComponent(active));
      const slot=document.getElementById('agent-job-detail');if(slot)slot.innerHTML=jobView(data);
      // Resume background polling after refresh, back/forward or opening a job.
      // Approvals re-queue an existing job; they must restart polling too.
      if(['queued','running'].includes(data.status)){
        if(!timer)timer=setInterval(fetchJob,1200);
      }else if(timer){clearInterval(timer);timer=null;}
    }catch(e){const slot=document.getElementById('agent-job-detail');if(slot)slot.innerHTML=message(e.message,true);}
  }
  async function refreshList(){const slot=document.getElementById('agent-recent');if(!slot)return;
    try{const jobs=await api('/jobs?limit=20');slot.innerHTML=jobs.length?jobs.map(j=>`<button type="button" class="button button-small" data-agent="open" data-id="${H(j.job_id)}">${H(goalNames[j.goal])} · ${H(statusNames[j.status]||j.status)} · ${H(j.job_id.slice(0,8))}</button>`).join(''):'<p class="muted">尚无历史 Agent 任务。</p>';
    }catch(e){slot.innerHTML=message(e.message,true)}
  }
  function toggle(){const g=document.getElementById('agent-goal')?.value;
    for(const [name,display] of [['import',['understand','semantic_benchmark'].includes(g)],['asset',!['understand','semantic_benchmark','mechanism'].includes(g)],['view',g==='forecast'],['lag',g==='features'],['mechanism',g==='mechanism'],['auto-series',g==='autonomous_modeling'],['auto-history',g==='autonomous_modeling']]){
      const el=document.getElementById('agent-'+name+'-label');if(el)el.style.display=display?'flex':'none';}
    const ex=document.getElementById('agent-export-label');if(ex)ex.style.display=g==='features'?'flex':'none';
  }
  async function render(args){ctx=args;if(timer)clearInterval(timer);timer=null;
    args.root.innerHTML='<div class="page-heading"><div><h1>Agent 任务中心</h1><p>统一 Skills、任务状态与受控执行记录。所有执行均在当前本地服务内进行。</p></div><a href="/data-assets" class="button">数据资产</a></div><div class="panel">正在读取工具与业务资产...</div>';
    try{[imports,assets,skills]=await Promise.all([
       args.req('/smart-import/imports'),args.req('/data-assets'),api('/skills')]);
      args.root.innerHTML='<div class="page-heading"><div><h1>Agent 任务中心</h1><p>确定性工具、有限模型建议、可恢复检查点与人工写入审批。</p></div><a href="/data-assets" class="button">返回数据资产</a></div>'+
       `<div class="panel"><h2>可用技能</h2><div class="smart-actions">${skills.skills.map(x=>`<span class="badge">${H(x.display_name||x.name)} · ${H(x.tools.length)} 个工具</span>`).join('')}</div><p class="muted small">模型建议只可在工具白名单内选择；工具执行权限不随模型回答改变。</p></div>`+
       form()+'<div id="agent-job-detail"></div><div class="panel"><h2>最近任务</h2><div class="smart-actions" id="agent-recent"></div></div>';
      toggle();await refreshList();
      const selected=location.pathname.split('/')[2];if(selected){active=selected;await fetchJob();}
    }catch(e){args.root.innerHTML=message('Agent 工作台加载失败：'+e.message,true)}
  }
  async function submit(event){if(event.target.id!=='agent-job-form')return false;
    event.preventDefault();const g=document.getElementById('agent-goal').value;
    const body={goal:g,instruction:document.getElementById('agent-instruction').value,
      use_model:document.getElementById('agent-use-model').checked,
      max_steps:Number(document.getElementById('agent-max-steps').value),
      max_units:Number(document.getElementById('agent-max-units').value),
      max_seconds:Number(document.getElementById('agent-max-seconds').value)};
    if(['understand','semantic_benchmark'].includes(g))body.import_id=document.getElementById('agent-import').value;
    else if(g==='mechanism')body.mechanism_evidence_id=document.getElementById('agent-mechanism').value;
    else{const selected=document.getElementById('agent-asset').value.split('|');body.asset_id=selected[0];body.version_id=selected[1];
      if(g==='forecast')body.view_id=document.getElementById('agent-view').value;
      if(g==='autonomous_modeling'){body.selection={filters:{series_id:[document.getElementById('agent-auto-series').value.trim()]}};body.min_history=Number(document.getElementById('agent-auto-history').value);body.max_units=100;}
      if(g==='features'){body.lags=document.getElementById('agent-lags').value.split(',').map(Number);body.export_features=document.getElementById('agent-export').checked;}}
    try{const job=await api('/jobs',post(body));active=job.job_id;ctx.navigate('/agent/'+encodeURIComponent(active));await refreshList();
      if(timer)clearInterval(timer);timer=setInterval(fetchJob,1200);await fetchJob();}
    catch(e){ctx.toast('创建任务失败：'+e.message)}
    return true;
  }
  async function click(event){const el=event.target.closest('[data-agent]');if(!el)return false;
    const action=el.dataset.agent;
    try{
      if(action==='open'){active=el.dataset.id;ctx.navigate('/agent/'+encodeURIComponent(active));await fetchJob();}
      else if(action==='refresh'){await fetchJob();}
      else if(active){const j=await api('/jobs/'+encodeURIComponent(active));
        if(action==='approve'&&j.pending_approval)await api(`/jobs/${encodeURIComponent(active)}/approve`,post({confirmed:true,pending_sha256:j.pending_approval.sha256}));
        else if(['cancel','retry'].includes(action))await api(`/jobs/${encodeURIComponent(active)}/${action}`,post({}));
        await fetchJob();await refreshList();}
    }catch(e){ctx.toast('操作失败：'+e.message)}
    return true;
  }
  function changed(event){if(event.target.id==='agent-goal'){toggle();return true;}
    if(event.target.id==='agent-asset'){void loadViews();return true;}
    return false;
  }
  window.TimePilotAgentCenter={render,submit,click,changed};
})();
