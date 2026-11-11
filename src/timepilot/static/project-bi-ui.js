/* v0.15.0 Project BI: one immutable source revision, one business filter,
 * one official Perspective workspace and one project-owned forecast lineage. */
(() => {
  'use strict';
  let ctx=null, aid=null, pid=null, base=null, asset=null, version=null;
  let selection={filters:{},date_from:null,date_to:null}, module=null;
  let views=[], drafts=[], snapshotAssets=[], ready=false, activeView=null;
  let generation=0;
  const H=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const U=v=>encodeURIComponent(String(v));
  const request=(path,opt)=>ctx.req(path,opt);
  const post=data=>({method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
  const url=(suffix='')=>'/projects/'+U(pid)+'/bi/'+U(aid)+suffix;
  const dataUrl=()=>'/projects/'+U(pid)+'/assets/'+U(aid);
  const fieldNames={country:'国家或地区',brand:'品牌',vehicle:'产品或车型',source_name:'数据来源',statistical_scope:'统计口径'};
  const note=(text,kind='warning')=>`<div class="helper ${kind}">${H(text)}</div>`;
  async function dispose(){generation++;ready=false;if(module){try{await module.disposePerspective()}catch{}}module=null;}
  function controls(){
    return `<div class="project-bi-filters">${Object.entries(fieldNames).map(([f,label])=>`<label class="field"><span>${label}</span><input data-project-bi-filter="${f}" value="${H((selection.filters[f]||[]).join(', '))}" placeholder="全部；多个值用逗号分隔"></label>`).join('')}
      <label class="field"><span>起始月份</span><input id="project-bi-from" type="month" value="${H(selection.date_from||'')}"></label>
      <label class="field"><span>截止月份</span><input id="project-bi-to" type="month" value="${H(selection.date_to||'')}"></label></div>
      <div class="action-row" style="margin-top:12px"><button class="button button-primary" type="button" data-project-bi="apply">应用筛选</button><button class="button" type="button" data-project-bi="clear">清空条件</button><span class="muted small">筛选只改变当前分析范围；原始数据保持不变</span></div>`;
  }
  function viewsHtml(){
    return views.length?views.map(v=>`<div class="project-bi-item"><div><b>${H(v.name||v.view_id)}</b><small>版本 ${H((v.version_id||'').slice(0,12))} · ${H(v.summary_at_save?.rows??'—')} 行 · ${H(v.created_at||'')}${v.missing?' · 文件缺失':''}</small></div><div class="action-row"><a class="button button-small" href="${url('?view_id='+U(v.view_id))}">打开视图</a><button type="button" class="button button-small" data-project-bi="draft" data-view="${H(v.view_id)}" ${v.missing?'disabled':''}>创建预测草稿</button></div></div>`).join(''):'<p class="muted">暂无保存视图。设置筛选条件、调整 Perspective 图表后保存。</p>';
  }
  function draftsHtml(){
    return drafts.length?drafts.map(d=>`<div class="project-bi-item"><div><b>${H(d.name||d.draft_id)}</b><small>${H(d.status==='ready'?'通过建模预检查':d.status==='needs_review'?'需要检查数据':'记录不可用')} · 版本 ${H(d.version_id?.slice(0,12))} · 视图 ${H(d.view_id?.slice(0,8))}</small>${d.preflight?.blockers?.length?`<small class="project-bi-warning">${d.preflight.blockers.map(H).join('；')}</small>`:''}</div><div class="action-row">${d.dataset_id?`<a class="button button-small" href="/new?project=${U(pid)}&dataset=${U(d.dataset_id)}&draft=${U(d.draft_id)}&horizon=${U(d.options?.forecast_horizon||3)}">创建训练任务</a>`:`<button class="button button-small" data-project-bi="materialize" data-draft="${H(d.draft_id)}" ${d.status!=='ready'?'disabled':''}>生成训练数据</button>`}</div></div>`).join(''):'<p class="muted">从保存的 BI 视图创建预测草稿，系统会先检查时间、实体、历史长度和数据可用时间。</p>';
  }
  function redrawLists(){
    const v=document.getElementById('project-bi-views');if(v)v.innerHTML=viewsHtml();
    const d=document.getElementById('project-bi-drafts');if(d)d.innerHTML=draftsHtml();
  }
  async function refreshLists(){
    [views,drafts]=await Promise.all([request(dataUrl()+'/views'),request('/projects/'+U(pid)+'/forecast-drafts')]);
    redrawLists();
  }
  function getSelection(){
    const filters={};
    document.querySelectorAll('[data-project-bi-filter]').forEach(input=>{
      const values=input.value.split(',').map(v=>v.trim()).filter(Boolean);
      if(values.length)filters[input.dataset.projectBiFilter]=[...new Set(values)];
    });
    return {filters,date_from:document.getElementById('project-bi-from')?.value||null,
            date_to:document.getElementById('project-bi-to')?.value||null};
  }
  function status(text,kind=''){const el=document.getElementById('project-bi-status');if(el)el.textContent=text;const bar=document.getElementById('project-bi-status-bar');if(bar)bar.dataset.status=kind;}
  async function mount(g){
    const slot=document.getElementById('project-bi-perspective');if(!slot)return;
    status('正在连接正式 BI...');ready=false;
    try{
      const info=await request('/ready');if(info.ready!==true)throw new Error('分析依赖尚未就绪');
      module=await import('/assets/native-bi/perspective-entry.js');
      const scheme=location.protocol==='https:'?'wss':'ws';
      const ws=`${scheme}://${location.host}/api/v1/native-bi/linked/${U(aid)}/versions/${U(version.version_id)}/ws?selection=${U(JSON.stringify(selection))}`;
      await module.mountPerspectiveRemote(slot,ws);
      if(g!==generation)return;
      ready=true;status('正式 BI 已连接 · 只读标准版本','ready');
      if(activeView?.perspective_workspace){
        try{await module.restorePerspectiveState(activeView.perspective_workspace,activeView.base_table_name||'timepilot_active')}
        catch(e){ctx.toast('已恢复数据筛选，图表布局恢复失败：'+e.message)}
      }
    }catch(e){if(g===generation){ready=false;slot.innerHTML=note('正式 BI 尚不可用：'+e.message+'。请检查 Perspective 离线构建资源与 DuckDB 服务。');status('无法连接正式 BI','error');}}
  }
  async function load(id,context){
    await dispose();ctx=context;pid=context.projectId;aid=id;const g=generation;
    base=await request(dataUrl());
    asset=await request(dataUrl()+'/versions');
    const query=new URLSearchParams(location.search),viewId=query.get('view_id');
    activeView=viewId?await request(dataUrl()+'/views/'+U(viewId)):null;
    // Historical views always restore their pinned source version, even after
    // a project changes its current version pointer.
    const vid=activeView?.version_id||base.version_id;
    version=await request('/data-assets/'+U(aid)+'/versions/'+U(vid));
    selection=activeView?.selection||{filters:{},date_from:null,date_to:null};
    if(!activeView&&query.has('selection')){
      try{selection=JSON.parse(query.get('selection'))}catch{throw new Error('筛选链接无效')}
    }
    snapshotAssets=await request('/projects/'+U(pid)+'/native-assets');
    const options=asset.versions||[];
    const currentVersion=base.version_id;
    ctx.root.innerHTML=`<div class="project-breadcrumb"><a href="/projects">全部项目</a><span>/</span><a href="/projects/${U(pid)}">项目</a><span>/</span><a href="/projects/${U(pid)}/data">数据资产</a><span>/</span><b>BI 分析</b></div>
      <div class="page-heading"><div><h1>${H(version.name)}</h1><p>正式 BI · ${Number(version.row_count||0).toLocaleString('zh-CN')} 行 · 版本和分析条件可追溯</p></div><div class="action-row"><a class="button" href="/projects/${U(pid)}/data">返回数据资产</a><button class="button" data-project="make-editable" data-asset-id="${H(aid)}">建立可编辑工作副本</button></div></div>
      <div class="panel"><div class="section-row" style="margin-top:0"><h2>标准数据版本</h2><span class="muted tiny">只读主资产 · 手动切换版本，不改动历史视图</span></div><div class="project-bi-version"><label class="field"><span>分析版本</span><select id="project-bi-version">${options.map(v=>`<option value="${H(v.version_id)}" ${v.version_id===vid?'selected':''}>${H(v.version_id.slice(0,14))}${v.version_id===currentVersion?' · 项目当前版本':''}</option>`).join('')}</select></label><span class="muted small">源校验：${H(version.data_sha256?.slice(0,16)||'—')} · ${H(version.created_at||'')}</span></div>${vid!==currentVersion?note('当前打开的是历史保存视图。修改分析版本需要通过版本选择器更新项目当前版本。'):''}</div>
      <div class="panel" style="margin-top:16px"><div class="section-row" style="margin-top:0"><h2>业务筛选</h2><span class="muted tiny">同一筛选条件同时用于图表与预测草稿</span></div>${controls()}</div>
      <div class="panel" style="margin-top:16px"><div class="section-row" style="margin-top:0"><h2>Perspective 分析工作台</h2><div class="action-row"><button class="button button-small" data-project-bi="grid">网格</button><button class="button button-small" data-project-bi="pivot">透视</button><button class="button button-small" data-project-bi="bar">柱状图</button><button class="button button-small" data-project-bi="line">折线图</button><button class="button button-primary button-small" data-project-bi="save" ${vid!==currentVersion?'disabled':''}>保存分析视图</button></div></div><p id="project-bi-status" class="muted small" role="status">准备连接...</p><div id="project-bi-status-bar" data-status="loading"></div><div class="project-bi-perspective" id="project-bi-perspective"><p>正在加载正式 Perspective 分析模块...</p></div></div>
      <div class="grid grid-2 project-bi-bottom"><div class="panel"><h2>已保存的 BI 视图</h2><div id="project-bi-views"><p class="muted">正在读取...</p></div></div><div class="panel"><h2>预测草稿</h2><div id="project-bi-drafts"><p class="muted">正在读取...</p></div></div></div>
      ${snapshotAssets.length?`<div class="panel" style="margin-top:16px"><h2>可编辑数据版本</h2><p>编辑形成独立 Parquet 工作副本，其版本和撤销记录由原生 BI 管理。</p>${snapshotAssets.map(v=>`<a class="project-pending" href="/projects/${U(pid)}/bi/native/${U(v.native_asset_id)}">${H(v.asset?.name||v.native_asset_id)}<span>进入可编辑版本 →</span></a>`).join('')}</div>`:''}`;
    await Promise.allSettled([refreshLists(),mount(g)]);
  }
  async function waitIndex(){
    const iid=asset.import_id,vid=version.version_id;
    const prefix='/smart-import/imports/'+U(iid)+'/versions/'+U(vid)+'/workbench';
    let job=await request(prefix+'/index-status');
    if(['unbuilt','failed'].includes(job.status))job=await request(prefix+'/build-index',{method:'POST'});
    for(let t=0;t<360&&['running','queued'].includes(job.status);t++){
      status('正在建立可复用的分析索引 · '+(job.indexed_rows||0)+' / '+(job.expected_rows||0));
      await new Promise(resolve=>setTimeout(resolve,1000));
      job=await request(prefix+'/index-status');
    }
    if(job.status!=='ready')throw new Error(job.error||'分析索引尚未完成，请稍后再保存视图');
  }
  async function click(event){
    const el=event.target.closest('[data-project-bi]');if(!el)return false;
    event.preventDefault();const action=el.dataset.projectBi;
    try{
      if(action==='apply'||action==='clear'){
        selection=action==='apply'?getSelection():{filters:{},date_from:null,date_to:null};
        if (Object.values(selection.filters).some(v=>v.length>30 || v.some(x=>x.length>180)) ||
            JSON.stringify(selection).length>3500) throw new Error('筛选值过多，请缩小筛选范围');
        activeView=null;
        // A bookmarked URL identifies the immutable source revision and a saved
        // view; ad hoc changes explicitly remove the stale view reference.
        const state=JSON.stringify(selection);ctx.navigate(url('?selection='+U(state)));
        return true;
      }
      if(['grid','pivot','bar','line'].includes(action)){
        if(!ready)throw new Error('Perspective 尚未连接');
        await module.setPerspectivePreset(action);return true;
      }
      if(action==='save'){
        if(base.version_id!==version.version_id)throw new Error('请先将当前历史版本设为项目工作版本');
        if(!ready)throw new Error('请先连接正式 Perspective BI，再保存完整分析视图');
        const name=prompt('视图名称', '业务分析视图');if(!name?.trim())return true;
        const elStatus=document.getElementById('project-bi-status');if(elStatus)elStatus.textContent='正在检查筛选范围并保存...';
        await waitIndex();
        const workspace=await module.savePerspectiveState();
        const root=await module.getActiveTableName();
        const view=await request(dataUrl()+'/views',post({name:name.trim(),selection,
          perspective_workspace:workspace,base_table_name:root||'timepilot_active'}));
        ctx.toast('视图已保存并绑定当前数据版本');
        await refreshLists();ctx.navigate(url('?view_id='+U(view.view_id)));return true;
      }
      if(action==='draft'){
        const vid=el.dataset.view;
        const existing=views.find(v=>v.view_id===vid);
        if(!existing||existing.missing)throw new Error('视图记录不可用');
        const name=prompt('预测草稿名称',existing.name+' · 预测');if(!name?.trim())return true;
        const raw=prompt('预测未来多少个月（1-24）','3');if(raw===null)return true;
        const horizon=Number(raw);
        if(!Number.isInteger(horizon)||horizon<1||horizon>24)throw new Error('预测跨度需为 1—24 个月');
        status('正在按保存的筛选视图检查预测适用性...');
        const draft=await request(dataUrl()+'/views/'+U(vid)+'/forecast-drafts',post({
          name:name.trim(),forecast_horizon:horizon,validation_windows:3,min_history:12,
          model_candidates:['seasonal_naive','moving_average'],series_policy:'all'}));
        await refreshLists();status('预测检查完成：'+(draft.status==='ready'?'可以生成训练数据':'存在需要确认的问题'));
        ctx.toast('预测草稿已关联项目和保存视图');return true;
      }
      if(action==='materialize'){
        status('正在基于已保存的 BI 筛选生成训练数据...');
        const draft=await request('/projects/'+U(pid)+'/forecast-drafts/'+U(el.dataset.draft)+'/materialize',{method:'POST'});
        await refreshLists();ctx.toast('训练数据已创建，前往建模任务配置');
        ctx.navigate('/new?project='+U(pid)+'&dataset='+U(draft.dataset_id)+'&draft='+U(draft.draft_id)+
          '&horizon='+U(draft.options?.forecast_horizon||3));return true;
      }
    }catch(error){ctx.toast('操作未完成：'+error.message);status(error.message,'error')}
    return true;
  }
  async function change(event){
    if(event.target?.id!=='project-bi-version')return false;
    const vid=event.target.value;
    try{
      await request('/projects/'+U(pid)+'/assets',post({asset_id:aid,version_id:vid}));
      ctx.navigate(url());
    }catch(e){ctx.toast('切换版本失败：'+e.message)}
    return true;
  }
  window.TimePilotProjectBI={render:load,click,change,dispose};
})();
