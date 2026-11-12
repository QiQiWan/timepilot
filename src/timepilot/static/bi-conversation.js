/* Project-scoped TimePilot v0.23.2 BI conversation: server-executed tools and history. */
(() => {
  'use strict';
  const H=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const U=encodeURIComponent;
  const post=x=>({method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(x)});
  const reqId=()=>('bi-'+(globalThis.crypto?.randomUUID?.()||String(Date.now())+'-'+Math.random().toString(36).slice(2)));
  let ctx,asset,version,workspace=null,busy=false,epoch=0,messages=[],views=[],pendingJoin=null,derivatives=[],planInfo=null,editPlanFields=false,draft="",pendingReasoning=null;
  const prefix=()=>'/projects/'+U(ctx.projectId)+'/bi-agent';
  function reset(){window.TimePilotBIDialogs?.close?.();window.TimePilotBIFloating?.leave();draft="";planInfo=null;pendingReasoning=null;editPlanFields=false;epoch++;workspace=null;ctx=null;asset=null;version=null;busy=false;messages=[];views=[];pendingJoin=null;derivatives=[];}
  function readTable(payload){
    if(!payload)return '';
    const data=payload.data||{};
    if(payload.tool==='bi.schema.inspect'){
      const fields=(data.fields||[]).slice(0,256);
      return `<details class="tp-bi-read-detail" open><summary>真实字段 ${data.fields?.length||0} 个 · 原始顺序</summary><div class="table-wrap"><table class="data-table"><thead><tr><th>序号</th><th>列名</th><th>类型</th></tr></thead><tbody>${fields.map((f,i)=>`<tr><td>${i+1}</td><td>${H(f.name)}</td><td>${H(f.physical_type||'未确定')}</td></tr>`).join('')}</tbody></table></div></details>`;
    }
    if(payload.tool==='bi.rows.peek'){
      const fields=data.columns||Object.keys((data.rows||[])[0]||{});
      return `<details class="tp-bi-read-detail" open><summary>真实记录预览 ${data.rows?.length||0} 条 · 只读</summary><div class="table-wrap"><table class="data-table"><thead><tr>${fields.map(x=>`<th>${H(x)}</th>`).join('')}</tr></thead><tbody>${(data.rows||[]).map(row=>`<tr>${fields.map(x=>`<td>${H(row[x])}</td>`).join('')}</tr>`).join('')}</tbody></table></div></details>`;
    }
    if(payload.tool==='bi.asset.describe'){
      const a=data.asset||{};return `<div class="tp-bi-read-detail"><b>${H(a.name||'当前数据资产')}</b><p>${H(a.row_count)} 行 × ${H(a.columns?.length)} 列 · 源哈希 ${H((a.sha256||'').slice(0,12))}</p></div>`;
    }
    return `<details class="tp-bi-read-detail"><summary>查看只读结果</summary><pre>${H(JSON.stringify(data,null,2).slice(0,7000))}</pre></details>`;
  }
  function draw(){
    const root=document.getElementById('tp-bi-agent-panel');if(!root||!ctx||!asset)return;
    const previousInput=root.querySelector('#tp-bi-agent-form textarea[name=message]');
    if(previousInput)draft=previousInput.value;
    const previousLog=root.querySelector('.tp-bi-agent-messages');
    const previousScroll=previousLog?.scrollTop||0;
    const atBottom=!previousLog||previousLog.scrollHeight-previousLog.scrollTop-previousLog.clientHeight<35;
    const state=workspace?.state,rev=workspace?.revision;
    const filters=state?.query?.filters||[];
    const canFreeze=!!workspace&&!state?.join&&!state?.bucket&&!state?.industrial&&!state?.expressions?.length&&!state?.window&&!state?.windows?.length;
    const tags=filters.map((f,i)=>`<span class="tp-bi-agent-tag">${i+1}. ${H(f.column)} ${H(f.op)} ${H(f.value??'')}</span>`).join('')||'<span class="muted">完整源数据（未筛选）</span>';
    const sourceFields=(asset.columns||[]).map(c=>c.name).filter(Boolean);
    const numerics=(asset.columns||[]).filter(c=>/(INT|FLOAT|DOUBLE|DECIMAL|REAL)/i.test(c.physical_type||'')).map(c=>c.name);
    const examples=['查看数据字段','查看前20行',
      ...(numerics.length>=2?[`请将 ${numerics[0]} 列与 ${numerics[1]} 列相加，结果放在一个新的列`,
          `对${numerics[0]}和${numerics[1]}计算最近5条记录的移动平均`]:[]),
      ...(sourceFields.length?[`按 ${sourceFields[0]} 升序排序`]:[])];
    root.innerHTML=`<div class="bi-card-title"><div><h2>数据分析</h2><p class="muted small">可直接提问、计算字段或生成图表 · 版本 ${H(version?.slice(0,14))}</p></div><span class="subtle-tag">${workspace?'修订 '+rev:'正在准备'}</span></div>
      <div class="tp-bi-agent-layout"><section class="tp-bi-agent-chat"><div class="tp-bi-agent-messages" role="log" aria-label="分析对话">${messages.length?messages.map(m=>`<div class="tp-bi-agent-msg ${m.who==='you'?'is-user':'is-engine'}"><b>${m.who==='you'?'你的要求':'执行结果'}</b><p>${H(m.text)}</p>${m.meta?`<small>${H(m.meta)}</small>`:''}${m.read?readTable(m.read):''}</div>`).join(''):'<div class="tp-bi-agent-placeholder">可直接输入“查看数据字段”“前20行”或“计算A1和A2之和”。系统只使用当前数据的真实字段。</div>'}</div>
      <div class="tp-bi-agent-examples" aria-label="推荐操作"><span class="muted tiny">试试这些操作</span>${examples.map(x=>`<button class="button button-small" type="button" data-bi-agent="example" data-text="${H(x)}">${H(x)}</button>`).join('')}</div>
      <form id="tp-bi-agent-form" class="tp-bi-agent-form"><label class="field"><span class="sr-only">分析指令</span><textarea name="message" rows="2" required maxlength="1200" placeholder="例如：把 A8 和 A10 相加并生成一列"></textarea></label><div class="action-row"><button class="button button-primary" type="submit" ${busy||!workspace?'disabled':''}>${busy?'执行中…':'执行'}</button><button class="button button-small" type="button" data-bi-agent="preview" ${busy||!workspace?'disabled':''}>查看计划</button></div></form>
      ${window.TimePilotBIPlanCard?.render(planInfo,editPlanFields)||''}</section>
      <details class="tp-bi-agent-side"><summary>其他操作 <span class="muted tiny">保存 · 数据处理 · 发布建模</span></summary>
      <div class="tp-bi-agent-side-inner">
      <div class="tp-bi-tools-group"><h3>当前数据</h3><p class="tp-bi-agent-data-note">${H(asset.name)} · ${H(asset.row_count)} 行 · 版本 ${H(String(version).slice(0,10))}</p>
      <details class="tp-bi-subdetails"><summary>查看当前筛选条件</summary><div class="tp-bi-agent-tags">${tags}</div></details></div>
      <div class="tp-bi-tools-group"><h3>视图与历史</h3><p class="muted tiny">保存当前图表，或撤销上一次操作。</p>
      <div class="tp-bi-agent-buttons"><button type="button" class="button button-small" data-bi-agent="save" ${busy||!workspace?'disabled':''}>保存当前图表</button>
      <button type="button" class="button button-small" data-bi-agent="undo" ${busy||!workspace?'disabled':''}>撤销</button>
      <button type="button" class="button button-small" data-bi-agent="redo" ${busy||!workspace?'disabled':''}>重做</button></div>
      <div class="tp-bi-agent-views">${views.length?views.map(v=>`<button type="button" class="button button-small" data-bi-agent="restore" data-view="${H(v.view_id)}">${H(v.name)}</button>`).join(''):'<span class="muted tiny">暂无已保存图表</span>'}</div></div>
      <div class="tp-bi-tools-group"><h3>计算和数据整理</h3><p class="muted tiny">选择真实字段后生成只读结果，源文件不变。</p>
      <div class="tp-bi-agent-buttons">${[['compute','计算新列'],['window','移动平均'],['bucket','按时间汇总'],['join','合并两张表'],['overlay','维护事件对照'],['clear','清除分析条件']].map(([id,label])=>`<button type="button" class="button button-small" data-bi-agent="${id}" ${busy||!workspace?'disabled':''}>${label}</button>`).join('')}</div></div>
      <details class="tp-bi-subdetails tp-bi-governance"><summary>发布数据和建立模型</summary>
      <p class="muted tiny">只在需要把分析结果用于正式建模时使用。系统会检查来源和数据范围。</p>
      <div class="tp-bi-agent-buttons"><button type="button" class="button button-small" data-bi-agent="derive" ${busy||!workspace?'disabled':''}>申请发布分析结果</button>
      <button type="button" class="button button-small" data-bi-agent="refresh-derived" ${busy?'disabled':''}>查看审核进度</button>
      <button type="button" class="button button-small" data-bi-agent="freeze" ${busy||!canFreeze?'disabled':''}>冻结当前筛选</button>
      <button type="button" class="button button-small" data-bi-agent="model" ${busy||!canFreeze?'disabled':''}>创建建模草稿</button></div>
      <div class="tp-bi-agent-derivations">${derivatives.slice(0,8).map(d=>`<div class="tp-bi-agent-derivation"><strong>${H(d.name)}</strong><span class="muted tiny">${H(d.status)}</span>${d.status==='pending_review'?`<button type="button" class="button button-small" data-bi-agent="approve-derived" data-derived="${H(d.derivation_id)}">审核</button>`:''}${d.status==='approved'||d.status==='publishing'?`<button type="button" class="button button-small" data-bi-agent="publish-derived" data-derived="${H(d.derivation_id)}">发布</button>`:''}${d.status==='published'?`<button type="button" class="button button-small" data-bi-agent="derived-model" data-derived="${H(d.derivation_id)}">建模</button>`:''}</div>`).join('')||'<p class="muted tiny">暂无待发布数据</p>'}</div></details>
      ${pendingJoin?`<div class="tp-bi-agent-confirm-join"><p>关联行数检查已完成。</p><button type="button" class="button button-small" data-bi-agent="confirm-join" ${busy?'disabled':''}>确认关联</button></div>`:''}
      <div id="tp-bi-agent-status" class="muted tiny" role="status">${workspace?'已连接数据工作区':'正在载入当前数据…'}</div>
      </div></details></div>`;
    const input=root.querySelector('#tp-bi-agent-form textarea[name=message]');if(input)input.value=draft;
    const log=root.querySelector('.tp-bi-agent-messages');if(log)log.scrollTop=atBottom?log.scrollHeight:previousScroll;
  }
  async function loadViews(){views=await ctx.req(prefix()+'/views?asset_id='+U(asset.asset_id));}
  async function loadDerived(){
    try{derivatives=(await ctx.req('/api/v1/projects/'+U(ctx.projectId)+'/derived')).filter(d=>d.workspace_id===workspace?.workspace_id);}
    catch(err){console.warn('派生数据状态读取失败',err);derivatives=[];}
  }
  async function mount(context,meta,dataVersion){
    reset();ctx=context;asset=meta;version=dataVersion;
    const currentEpoch=epoch;
    if(window.TimePilotBIFloating)await window.TimePilotBIFloating.mount(context,meta);
    if(epoch!==currentEpoch)return;draw();
    try {
      let candidates=await ctx.req(prefix()+'/workspaces?asset_id='+U(meta.asset_id));
      const prior=candidates.find(w=>w.version_id===dataVersion);
      const doc=prior?await ctx.req(prefix()+'/workspaces/'+U(prior.workspace_id)):
        await ctx.req(prefix()+'/workspaces',post({asset_id:meta.asset_id,version_id:dataVersion}));
      if(epoch!==currentEpoch)return;
      workspace=doc;
      try{await window.TimePilotNativeBI?.activateAgentWorkspace?.(doc)}
      catch(err){console.warn('Perspective 布局恢复失败',err)}
      await loadViews();await loadDerived();if(epoch!==currentEpoch)return;
      try{
        const transcript=await ctx.req(prefix()+'/workspaces/'+U(doc.workspace_id)+'/turns');
        if(epoch!==currentEpoch)return;
        messages=transcript.map(t=>({who:'you',text:t.message,meta:'修订 '+t.revision+' · '+t.status})).slice(-30);
      }catch(e){console.warn('BI 历史对话加载失败',e)}
      draw();
    }catch(error){if(epoch===currentEpoch){const root=document.getElementById('tp-bi-agent-panel');
      if(root)root.innerHTML=`<div class="helper warning">对话 BI 工作区不可用：${H(error.message)}。原有 Perspective 手动分析仍可使用。</div>`;}}
  }
  function addMessage(who,text,meta='',read=null){messages.push({who,text,meta,read});messages=messages.slice(-35);draw();}
  async function renderExecution(response){
    if(response.workspace)workspace=response.workspace;
    if(response.read_results?.length){
      for(const item of response.read_results){
        const desc={'bi.schema.inspect':`当前表共有 ${item.data?.fields?.length||0} 个字段，已按原始顺序列出。`,
                    'bi.asset.describe':'当前数据资产的元信息已读取，数据版本没有变化。',
                    'bi.rows.peek':`已读取 ${item.data?.rows?.length||0} 条真实记录（限定预览），未修改工作区。`,
                    'bi.catalog.list':'已查询当前项目授权的数据资产目录。'};
        addMessage('engine',desc[item.tool]||'只读查询已完成。',item.tool+' · 工作区版本不变',item);
      }
      return null;
    }
    const joinPlan=(response.actions||[]).find(a=>a.tool==='bi.join.preview');
    const joinEvidence=(response.steps||[]).find(s=>typeof s.estimated_rows==='number');
    if(joinPlan&&joinEvidence){
      pendingJoin=joinPlan.arguments;
      addMessage('engine',`关联预检查完成：左表 ${joinEvidence.left_rows} 行，`+
        `右表 ${joinEvidence.right_rows} 行，预计关联后 ${joinEvidence.estimated_rows} 行。`+
        '请确认后建立只读关联。');
    }
    if((response.steps||[]).some(s=>s.tool==='bi.join.apply_virtual'))pendingJoin=null;
    let browserReceipt=null;
    if(response.result){
      const data=response.result;
      addMessage('engine',`服务端执行完成：${data.returned??data.rows?.length??'—'} 条预览记录，数据版本保持不变。原生 Viewer 需要独立渲染确认。`+
        (response.planner?` 规划器：${response.planner}。`:''),
        `${(response.actions||response.steps||[]).map(a=>a.tool||a.effect||'').join(' → ')} · 修订 ${workspace?.revision}`);
      if(response.workspace?.state){
        try{
          const rendered=await window.TimePilotNativeBI?.renderAgentResult?.(
            workspace,data,response.actions||response.steps||[]);
          if(rendered?.status==='extension_preview'){
            addMessage('engine','计算列已在工作区附加区域展示真实结果预览，原生 Perspective 配置保持不变。发布为新数据资产需经过派生审核。');
          }else{
            browserReceipt=await window.TimePilotNativeBI?.acknowledgeAgentRender?.(rendered,workspace);
          }
        }catch(err){addMessage('engine','后端查询已执行，但原生图表加载失败：'+err.message);
          const root=document.getElementById('tp-bi-agent-status');if(root)root.textContent='请检查 Perspective JS/WASM 及 Arrow 构建资源';}
      }
    }else if(response.executed===false)addMessage('engine',response.message||'已生成计划，尚未更改工作区',
      response.actions?.length?response.actions.map(a=>a.tool).join(' → '):'无可执行工具；请补充必要的信息');
    else addMessage('engine','工作区修订已保存。');
    draw();
    return browserReceipt;
  }
  async function send(message,execute){
    if(busy||!workspace)return;busy=true;
    addMessage('you',message);draw();
    try{
      // The established correlation service handles numeric matrices. Other
      // requests use the general bounded reasoning planner or direct read.
      if(/相关|热力图/.test(message)){
        planInfo=await ctx.req(prefix()+'/workspaces/'+U(workspace.workspace_id)+'/analysis-plans',post({
          message,expected_revision:workspace.revision,request_id:reqId()}));
        editPlanFields=false;
        addMessage('engine',`相关分析计划：${planInfo.status}。${(planInfo.plan?.feasibility?.reasons||[]).join('；')||'核实数值列后执行。'}`);
      }else if(/趋势|波动|走势|比较|对比|统计|分布|分组|先.+再|仪表盘|多个图/.test(message)){
        const combined=pendingReasoning?pendingReasoning+'；用户补充：'+message:message;
        const planned=await ctx.req(prefix()+'/workspaces/'+U(workspace.workspace_id)+'/reasoning-plans',post({
          message:combined,expected_revision:workspace.revision,request_id:reqId()}));
        const tools=(planned.plan?.tool_calls||[]).map(x=>x.tool);
        const reason=planned.plan?.reason||'';
        addMessage('engine',`分析计划：${planned.status}。${reason||tools.join(' → ')||'需要核实字段或操作范围。'}`,
                   `规划：${planned.plan?.planner||'本地'} · 数据版本固定`);
        if(planned.status==='needs_clarification')pendingReasoning=combined;
        else pendingReasoning=null;
        if(planned.status==='ready' && execute && tools.length){
          const applied=await ctx.req(prefix()+'/workspaces/'+U(workspace.workspace_id)+
            '/reasoning-plans/'+U(planned.run_id)+'/apply',post({request_id:reqId(),confirm:false}));
          const receipt=await renderExecution(applied.execution||{});
          if(applied.render_required){
            if(receipt){
              const confirmed=await ctx.req(prefix()+'/workspaces/'+U(workspace.workspace_id)+
                '/reasoning-plans/'+U(planned.run_id)+'/rendered',post({success:true,viewer_receipt:receipt}));
              addMessage('engine',`已完成 Perspective 实际渲染，计划状态：${confirmed.status}。`);
            }else addMessage('engine','计算已完成，等待 Perspective 视图渲染和回执；计划尚未标记成功。');
          }
        }
      }else{
        pendingReasoning=null;
        const result=await ctx.req(prefix()+'/workspaces/'+U(workspace.workspace_id)+'/turns',post({
          message,execute,expected_revision:workspace.revision,request_id:reqId()}));
        await renderExecution(result);
      }
    }catch(error){addMessage('engine','操作未完成：'+error.message);}
    finally{busy=false;draw()}
  }
  const dialog=()=>window.TimePilotBIDialogs;
  const sourceColumns=(kind='all')=>(asset?.columns||[]).filter(c=>{
    const type=String(c.physical_type||'').toUpperCase();
    return kind==='numeric'?/(INT|FLOAT|DOUBLE|DECIMAL|REAL)/.test(type):
      kind==='time'?/(DATE|TIME)/.test(type):true;
  }).map(c=>c.name);
  function fieldOptions(names){return names.map(name=>({value:name,label:name}));}
  async function choose(title,kind='all',selected=''){
    const names=sourceColumns(kind);
    if(!names.length)throw new Error(kind==='time'?'没有已确认的日期时间字段，请先核实数据类型':
      '没有符合条件的字段，请检查源数据字段类型');
    const defaults=await window.TimePilotNativeBI?.getSelectedSeries?.().catch(()=>null);
    const value=names.includes(selected)?selected:(defaults?.fields||[]).find(f=>names.includes(f))||names[0];
    const result=await dialog().open({title,description:'选择当前数据表的真实字段。可直接使用 Perspective 中已拖入的系列。',
      fields:[{name:'field',label:'数据字段',type:'select',options:fieldOptions(names),value}]});
    return result?.field||null;
  }
  async function applyTool(tool,arguments_,confirmed=false){
    const url=prefix()+'/workspaces/'+U(workspace.workspace_id);
    const result=await ctx.req(url+'/apply',post({expected_revision:workspace.revision,
      actions:[{tool,arguments:arguments_}],request_id:reqId(),confirm:confirmed}));
    await renderExecution(result);
  }
  async function guidedAction(action){
    if(action==='clear'){
      const state=workspace.state;
      const what=state.join?'bi.join.clear':state.bucket?'bi.timeseries.clear':state.industrial?'bi.chart.clear_overlay':
        state.expressions.length?'bi.expression.clear':state.window?'bi.window.clear':'bi.filter.remove';
      if(!await dialog().confirm('恢复明细视图','将清除当前选中的分析条件。源数据和已保存版本保持不变。'))return;
      await applyTool(what,{});return;
    }
    const numeric=sourceColumns('numeric');
    if(action==='compute'){
      if(numeric.length<2)throw new Error('当前表至少需要 2 个数值字段');
      const fields=await dialog().open({title:'创建计算字段',
        description:'在当前数据上创建只读计算列；确认发布之前不会改变原始表。',
        fields:[
          {name:'left',label:'第一个字段',type:'select',options:fieldOptions(numeric),value:numeric[0]},
          {name:'op',label:'运算方式',type:'select',useViewer:false,options:[{value:'+',label:'相加（+）'},{value:'-',label:'相减（−）'},{value:'*',label:'相乘（×）'},{value:'/',label:'相除（÷）'}],value:'+'},
          {name:'right',label:'第二个字段',type:'select',options:fieldOptions(numeric),value:numeric[1]},
          {name:'name',label:'新列名称（字母、数字、下划线）',value:'computed_result',maxlength:64}],
        confirmText:'预览计算结果'});
      if(!fields)return;
      if(!/^[A-Za-z_][A-Za-z0-9_]{0,63}$/.test(fields.name))throw new Error('新列名称只能使用英文字母、数字和下划线');
      await applyTool('bi.expression.create',{name:fields.name,expression:{kind:'binary',op:fields.op,
        left:{kind:'column',value:fields.left},right:{kind:'column',value:fields.right}}});return;
    }
    if(action==='window'){
      if(!numeric.length)throw new Error('当前数据没有可用的数值字段');
      const orders=['__tp_rowid',...sourceColumns('all')];
      const data=await dialog().open({title:'计算移动平均',
        description:'默认按源记录顺序滚动计算；有明确时间字段时可改为按时间排序。结果写入新的只读列。',
        fields:[
          {name:'field',label:'数值字段',type:'select',options:fieldOptions(numeric),value:numeric[0]},
          {name:'order',label:'排序依据',type:'select',useViewer:false,options:[{value:'__tp_rowid',label:'源数据记录顺序'},...fieldOptions(orders.slice(1))],value:'__tp_rowid'},
          {name:'window',label:'窗口点数（2—240）',type:'number',value:5,min:2,max:240},
          {name:'name',label:'新列名称',value:numeric[0]+'_ma5',maxlength:64}],
        confirmText:'生成移动平均'});
      if(!data)return;
      if(!/^[A-Za-z_][A-Za-z0-9_]{0,63}$/.test(data.name))throw new Error('新列名称无效');
      await applyTool('bi.window.compute_many',{columns:[data.field],time_field:data.order,
        window:data.window,output_names:[data.name]});return;
    }
    if(action==='bucket'){
      const dates=sourceColumns('time');if(!dates.length)throw new Error('没有已确认的时间字段，请在数据管理中核实类型');
      if(!numeric.length)throw new Error('没有可聚合的数值指标');
      const data=await dialog().open({title:'按时间汇总',description:'以真实时间字段将记录分组，再计算所选数值指标。',fields:[
        {name:'time_field',label:'时间字段',type:'select',options:fieldOptions(dates)},
        {name:'metric_field',label:'聚合指标',type:'select',options:fieldOptions(numeric)},
        {name:'interval',label:'统计间隔',type:'select',useViewer:false,options:[{value:'1m',label:'1 分钟'},{value:'5m',label:'5 分钟'},{value:'15m',label:'15 分钟'},{value:'1h',label:'1 小时'},{value:'1d',label:'1 天'}],value:'1h'},
        {name:'operation',label:'统计方式',type:'select',useViewer:false,options:[{value:'avg',label:'平均值'},{value:'sum',label:'合计'},{value:'min',label:'最小值'},{value:'max',label:'最大值'},{value:'count',label:'记录数'}],value:'avg'}]});
      if(data)await applyTool('bi.timeseries.bucket',data);return;
    }
    if(action==='overlay'){
      const dates=sourceColumns('time');if(!dates.length)throw new Error('需要经过类型核实的时间字段');
      const others=sourceColumns('all');
      const data=await dialog().open({title:'维护事件与工况对照',description:'选择数据中的时间、指标和真实事件字段，生成设备趋势与事件标记。',fields:[
        {name:'time_field',label:'时间字段',type:'select',options:fieldOptions(dates)},
        {name:'metric_field',label:'数值指标',type:'select',options:fieldOptions(numeric)},
        {name:'event_field',label:'维护事件字段',type:'select',options:fieldOptions(others)},
        {name:'regime_field',label:'工况字段',type:'select',options:fieldOptions(others)}]});
      if(data)await applyTool('bi.chart.overlay',{template:'maintenance_overlay',...data,interval:'1h'});return;
    }
    if(action==='join'){
      const options=await ctx.req('/projects/'+U(ctx.projectId)+'/native-assets');
      const available=options.filter(v=>v.native_asset_id!==asset.asset_id);
      if(!available.length)throw new Error('当前项目没有可关联的第二张表');
      const rightPick=await dialog().open({title:'关联两张数据表',description:'先选择第二张表及关联键。系统会计算关联后行数，确认后创建只读视图。',fields:[
        {name:'right_asset_id',label:'关联表',type:'select',useViewer:false,options:available.map(v=>({value:v.native_asset_id,label:v.asset?.name||v.native_asset_id}))}]});
      if(!rightPick)return;
      const right=available.find(v=>v.native_asset_id===rightPick.right_asset_id);if(!right)throw new Error('关联表无效');
      const rightFields=(right.asset?.columns||[]).map(c=>c.name);if(!rightFields.length)throw new Error('关联表没有字段');
      const keys=await dialog().open({title:'选择关联键',description:'两张表应使用对应的设备、批次或记录标识；执行前会展示行数膨胀风险。',fields:[
        {name:'left_key',label:'当前表关联字段',type:'select',options:fieldOptions(sourceColumns())},
        {name:'right_key',label:'另一张表关联字段',type:'select',useViewer:false,options:fieldOptions(rightFields)}]});
      if(!keys)return;
      const join={right_asset_id:right.native_asset_id,right_version_id:right.asset?.head_version||'base',...keys,how:'left'};
      const base=prefix()+'/workspaces/'+U(workspace.workspace_id);
      const preview=await ctx.req(base+'/preview',post({expected_revision:workspace.revision,
        request_id:reqId(),actions:[{tool:'bi.join.preview',arguments:join}]}));
      const info=preview.steps?.[0]||{};
      if(!await dialog().confirm('确认关联数据',
        `左表 ${info.left_rows} 行；右表 ${info.right_rows} 行；预计输出 ${info.estimated_rows} 行，放大倍数 ${info.fanout}。原始数据不会修改。`,{confirmText:'创建只读关联'}))return;
      await applyTool('bi.join.apply_virtual',join,true);return;
    }
  }
  async function perform(action,details={}){
    if(busy||!workspace)return;busy=true;draw();
    const url=prefix()+'/workspaces/'+U(workspace.workspace_id);
    try{
      if(action==='edit-plan-fields'){
        editPlanFields=true;
      }else if(action==='save-plan-fields'){
        const field_ids=[...document.querySelectorAll('#tp-plan-field-pick input:checked')].map(e=>e.value);
        if(field_ids.length<2)throw new Error('请至少选取 2 个数值字段');
        const path=prefix()+'/workspaces/'+U(workspace.workspace_id)+'/analysis-plans/'+U(planInfo.plan_id);
        planInfo=await ctx.req(path,{method:'PATCH',headers:{'Content-Type':'application/json'},
          body:JSON.stringify({plan_revision:planInfo.plan_revision,field_ids,method:'pearson'})});
        editPlanFields=false;
      }else if(action==='execute-plan'){
        if(!planInfo)throw new Error('尚无可执行计划');
        const path=prefix()+'/workspaces/'+U(workspace.workspace_id)+'/analysis-plans/'+U(planInfo.plan_id);
        const run=await ctx.req(path+'/apply',post({request_id:reqId(),plan_revision:planInfo.plan_revision,
          confirm:true}));
        if(run.result?.workspace)workspace=run.result.workspace;
        const id=run.result?.analysis_id;
        if(!id)throw new Error('后端尚未生成真实矩阵，无法标记绘图完成');
        try{
          await window.TimePilotNativeBI.renderAgentResult(workspace);
          await ctx.req(path+'/execution/'+U(run.execution_id)+'/rendered',post({success:true}));
          planInfo=await ctx.req(path);
          addMessage('engine',`相关矩阵已完成真实计算和渲染：${run.result.matrix_shape.join('×')}；来源校验通过。`);
        }catch(error){
          await ctx.req(path+'/execution/'+U(run.execution_id)+'/rendered',post({success:false})).catch(()=>{});
          planInfo=await ctx.req(path).catch(()=>planInfo);
          throw error;
        }
      }else if(action==='confirm-join'){
        if(!pendingJoin)throw new Error('没有待确认的关联计划');
        if(!await dialog().confirm('创建关联视图','已通过关联行数预检查。创建只读关联视图，保留原始数据。'))return;
        await applyTool('bi.join.apply_virtual',pendingJoin,true);
        pendingJoin=null;
      }else if(['compute','window','bucket','overlay','join','clear'].includes(action)){
        await guidedAction(action);
      }else if(action==='undo'||action==='redo'){
        const result=await ctx.req(url+'/'+action,post({expected_revision:workspace.revision,request_id:reqId()}));
        await renderExecution(result);
      }else if(action==='save'){
        const name=await dialog().text('保存分析看板','工业数据分析看板','将保存当前 Perspective 图表布局、字段选择和来源版本。');if(!name)return;
        const perspective=await window.TimePilotNativeBI.saveAgentPerspective?.();
        await ctx.req(url+'/views',post({name:name.trim(),perspective}));
        await loadViews();addMessage('engine','分析视图已保存，包含当前版本与可恢复的工作区配置。');
      }else if(action==='restore'){
        const saved=await ctx.req(prefix()+'/views/'+U(details.view));
        const result=await ctx.req(url+'/open-view',post({view_id:saved.view_id,
          expected_revision:workspace.revision,request_id:reqId()}));
        await renderExecution(result);
        if(result.perspective)try{await window.TimePilotNativeBI.restoreAgentPerspective(result.perspective)}catch(err){ctx.toast('图表布局恢复失败：'+err.message)}
      }else if(action==='derive'){
        const name=await dialog().text('创建派生数据',asset.name+' · 派生版本','从当前完整数据计算结果生成可审核的数据资产。');
        if(!name?.trim())return;
        if(!await dialog().confirm('提交派生数据审核','将记录数据来源、字段及计算范围；正式发布前需要审批。'))return;
        const created=await ctx.req('/api/v1/projects/'+U(ctx.projectId)+'/derived',post({
          workspace_id:workspace.workspace_id,name:name.trim(),request_id:reqId()}));
        await loadDerived();addMessage('engine','已提交派生资产审核：'+created.estimated_rows+' 行。发布前需要审批，训练暂不可使用。');
      }else if(action==='refresh-derived'){
        await loadDerived();addMessage('engine','已更新派生数据审批状态。');
      }else if(action==='approve-derived'){
        const rid=details.derived;
        const decision=await dialog().text('审核意见','已核对来源、字段、范围及数据用途','企业模式需要具有相应权限的管理员进行审批。');
        if(!decision?.trim())return;
        const approved=await ctx.req('/api/v1/projects/'+U(ctx.projectId)+'/derived/'+U(rid)+'/approve',
          post({confirmed:true,reason:decision.trim()}));
        await loadDerived();addMessage('engine','审批结果：'+approved.status+'。');
      }else if(action==='publish-derived'){
        const rid=details.derived;
        if(!await dialog().confirm('发布派生数据','将扫描完整记录并生成新的不可变数据资产。', {confirmText:'确认发布'}))return;
        const published=await ctx.req('/api/v1/projects/'+U(ctx.projectId)+'/derived/'+U(rid)+'/publish',post({}));
        await loadDerived();addMessage('engine','派生数据已发布 '+published.published_rows+' 行，原生资产编号：'+published.native_asset_id);
      }else if(action==='derived-model'){
        const rid=details.derived;
        const name=await dialog().text('创建建模任务',asset.name+' · 派生建模');if(!name)return;
        if(!await dialog().confirm('创建建模草稿','使用已审核发布的完整派生数据；随后需确认目标、时间字段与特征。'))return;
        const created=await ctx.req('/api/v1/projects/'+U(ctx.projectId)+'/derived/'+U(rid)+'/model-drafts',
          post({name:name.trim(),confirm_scope:true}));
        addMessage('engine','派生数据已冻结并创建待审核模型草稿。');
        if(window.TimePilotModeling?.openDraft)await window.TimePilotModeling.openDraft({ctx,projectId:ctx.projectId,
          assetId:created.draft.asset_id,draftId:created.draft.draft_id});
      }else if(action==='freeze'){
        if(!await dialog().confirm('冻结训练范围','仅冻结当前源版本与完整行级筛选，图表预览样本不进入训练。'))return;
        const selection=await ctx.req(url+'/freeze',post({confirmed:true}));
        addMessage('engine','已冻结 '+selection.row_count+' 条源记录。选择 ID：'+selection.selection_id);
      }else if(action==='model'){
        const name=await dialog().text('建模草稿名称',asset.name+' · 数据建模');if(!name)return;
        if(!await dialog().confirm('确认建模范围','使用当前源版本和行级筛选创建待审核草稿。'))return;
        const result=await ctx.req(url+'/model-drafts',post({name:name.trim(),confirm_scope:true}));
        addMessage('engine','已创建待审核建模草稿，需进一步确认预测目标、时间字段和特征。');
        if(window.TimePilotModeling?.openDraft)await window.TimePilotModeling.openDraft({ctx,projectId:ctx.projectId,
          assetId:asset.asset_id,draftId:result.draft.draft_id});
      }
    }catch(error){addMessage('engine','操作未完成：'+error.message);}
    finally{busy=false;draw()}
  }
  function click(event){const element=event.target.closest('[data-bi-agent]');if(!element)return false;
    event.preventDefault();const action=element.dataset.biAgent;
    if(action==='example'){
      const input=document.querySelector('#tp-bi-agent-form textarea[name=message]');
      if(input){input.value=element.dataset.text;draft=input.value;input.focus();}return true;
    }
    if(['execute-plan','edit-plan-fields','save-plan-fields'].includes(action)){perform(action);return true;}
    if(action==='preview'){
      const input=document.querySelector('#tp-bi-agent-form textarea[name=message]');
      if(input?.value.trim())send(input.value.trim(),false);return true;
    }
    perform(action,{view:element.dataset.view,derived:element.dataset.derived});return true;
  }
  function submit(event){if(event.target?.id!=='tp-bi-agent-form')return false;
    event.preventDefault();const input=event.target.querySelector('textarea[name=message]');
    if(input?.value.trim()){const message=input.value.trim();input.value='';draft='';send(message,true);}return true;
  }
  window.TimePilotBIConversations={mount,reset,click,submit};
})();
