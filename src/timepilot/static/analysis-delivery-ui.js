/* v0.35.6: source-pinned feature assets and immutable reports, without another route. */
(() => {
  'use strict';
  const enc=encodeURIComponent;
  const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const shaOk=value=>/^[0-9a-f]{64}$/i.test(String(value||''));
  let current=null,sequence=0;
  function isActive(state,token){return current===state && sequence===token && state.host?.isConnected;}
  async function checksum(body,expected){
    if(!shaOk(expected))throw Error('报告缺少有效的 SHA256，已阻止下载');
    if(!globalThis.crypto?.subtle?.digest)throw Error('当前浏览器不支持 SHA256 校验，已阻止下载');
    const bytes=new TextEncoder().encode(body);
    const hash=await globalThis.crypto.subtle.digest('SHA-256',bytes);
    const actual=Array.from(new Uint8Array(hash),b=>b.toString(16).padStart(2,'0')).join('');
    if(actual.toLowerCase()!==String(expected).toLowerCase())throw Error('报告文件 SHA256 不一致，禁止下载');
  }
  function download(text,name){
    const blob=new Blob([text],{type:'text/markdown;charset=utf-8'});
    const uri=URL.createObjectURL(blob),a=document.createElement('a');
    a.href=uri;a.download=name;a.click();
    setTimeout(()=>URL.revokeObjectURL(uri),2500);
  }
  function chooseSource(state){
    const available=state.assets||[];
    const matched=available.find(x=>x.native_asset_id===state.aid);
    if(matched)return matched;
    // Explicit asset identifiers must never silently fall back to a different asset.
    if(state.aid)return null;
    return available[0]||null;
  }
  function markdownPreview(markdown){
    const lines=String(markdown||'').slice(0,22000).split(/\r?\n/);
    let html='',list=[];
    const flush=()=>{if(list.length){html+='<ul>'+list.map(x=>'<li>'+esc(x)+'</li>').join('')+'</ul>';list=[];}};
    for(const raw of lines){
      const text=raw.trim();
      if(!text){flush();continue;}
      const heading=/^(#{1,4})\s+(.+)$/.exec(text);
      if(heading){flush();const level=Math.min(4,heading[1].length+1);
        html+='<h'+level+'>'+esc(heading[2])+'</h'+level+'>';continue;}
      if(/^[-*]\s+/.test(text)){list.push(text.slice(2));continue;}
      flush();html+='<p>'+esc(text)+'</p>';
    }
    flush();return html;
  }
  function renderPage(state){
    const session=state.session,delivery=state.delivery||{},versions=state.reports||[];
    const valid=!!(session && !session.stale && delivery.status!=='blocked'&&delivery.status!=='stale' &&
      delivery.source?.asset_id===state.aid && delivery.source?.version_id===state.vid &&
      delivery.source?.sha256===session.source_sha256 &&
      delivery.source?.contract_revision===session.contract_revision);
    const art=valid?delivery.artifact:null;
    const sets=(state.sets||[]).filter(x=>x.asset_id===state.aid&&x.version_id===state.vid);
    const active=state.preview?`<section class="tp-delivery-preview"><header><h3>${esc(state.previewTitle||'报告内容')}</h3><button type="button" data-delivery="close-preview" class="button button-small">收起预览</button></header><div class="tp-delivery-markdown">${markdownPreview(state.preview)}</div>${state.preview.length>22000?'<p class="muted">此处仅展示前 22000 字符，下载可查看完整报告。</p>':''}</section>`:'';
    return `<section class="tp-delivery-workbench" data-testid="unified-delivery">
      <header class="tp-delivery-header"><div><h2>成果与分析报告</h2><p>当前数据：${esc(state.assetName||state.aid)} · ${esc(state.vid)}。只显示属于该数据版本的特征集与报告。</p></div>
        <button type="button" class="button button-small" data-delivery="refresh">刷新成果</button></header>
      ${state.error?`<div class="helper warning" role="alert">${esc(state.error)}</div>`:''}
      <div class="tp-delivery-stats">
        <div><small>分析任务</small><strong>${esc(!session?'尚未运行':session.stale?'来源已失效':session.stage||session.status)}</strong></div>
        <div><small>当前版本特征集</small><strong>${sets.length}</strong></div>
        <div><small>正式物化</small><strong>${art?esc(art.row_count)+' 行':'待计算'}</strong></div>
        <div><small>归档报告</small><strong>${versions.length} 个版本</strong></div>
      </div>
      ${session?.stale||delivery.status==='blocked'||delivery.status==='stale'?'<p class="tp-delivery-warning">数据来源或计算证据已变化。历史记录可查看，但禁止作为当前可用成果归档或发布。</p>':''}
      <section class="tp-delivery-section"><div class="tp-delivery-section-head"><h3>本次分析报告</h3><span class="muted">${session?'Session '+esc(session.session_id.slice(0,10)):'尚无分析任务'}</span></div>
        ${session?`<p>${valid?'已核对数据版本与分析来源，可生成和保存报告。':'分析结果正在准备或来源尚待核对，保存前需确保同源。'}</p>
        <div class="tp-delivery-actions">
          <button type="button" class="button button-small" data-delivery="preview-live" ${valid?'':'disabled'}>预览报告</button>
          <button type="button" class="button button-small" data-delivery="download-live" ${valid?'':'disabled'}>导出 Markdown</button>
          <button type="button" class="button button-primary button-small" data-delivery="archive" ${valid?'':'disabled'}>保存不可变报告</button>
          ${art?'<button type="button" class="button button-small" data-delivery="bi">在 BI 查看正式成果</button>':''}
        </div>`:'<p>请先在自动仪表盘完成数据分析与候选生成。</p>'}
      </section>
      <section class="tp-delivery-section"><div class="tp-delivery-section-head"><h3>已保存的特征集</h3><button class="button button-small" type="button" data-delivery="manual">手工编辑或新建特征集</button></div>
        ${sets.length?`<div class="tp-delivery-sets">${sets.slice(0,30).map(x=>`<div class="tp-delivery-set"><div><strong>${esc(x.name||x.set_id)}</strong><small>修订 ${esc(x.revision)} · ${esc(x.state||'草稿')}</small></div><button type="button" class="button button-small" data-delivery="edit-set" data-set="${esc(x.set_id)}">继续管理</button></div>`).join('')}</div>`:
          '<p>当前数据版本还没有保存的特征集。可以先审阅自动候选，再保存；也可以手动添加算子。</p>'}
        ${art?`<p class="muted">正式产物 SHA256：${esc(art.sha256)}。仅在服务端同源校验通过后提供 BI 对照。</p>`:''}
      </section>
      <section class="tp-delivery-section"><div class="tp-delivery-section-head"><h3>历史报告版本</h3><span class="muted">保存后原文件不可覆盖；每次打开重新核验 SHA256</span></div>
        ${versions.length?`<div class="tp-delivery-reports">${versions.map((x,i)=>`<div class="tp-delivery-report"><div><strong>修订 ${esc(x.revision)}</strong><small>${esc(x.created_at||'')} · SHA256 ${esc((x.report_sha256||'').slice(0,16))}</small></div><div class="tp-delivery-actions"><button class="button button-small" type="button" data-delivery="preview-archive" data-id="${esc(x.report_id)}">查看</button><button class="button button-small" type="button" data-delivery="download-archive" data-id="${esc(x.report_id)}">下载</button></div></div>`).join('')}</div>${versions.length>1?'<button type="button" class="button button-small" data-delivery="diff">比较最近两版</button>':''}`:'<p class="muted">尚未归档报告。确认分析结果后，可保存第一份不可变版本。</p>'}
      </section>
      ${active}
      <details class="tp-delivery-evidence"><summary>成果来源与校验详情</summary><p>源数据 SHA256：${esc(session?.source_sha256||'—')}</p><p>数据契约修订：${esc(session?.contract_revision||'—')}</p><p>交付状态：${esc(delivery.status||'—')}</p><p>报告归档会同时记录特征物化和模型评价引用。未经核实的模型收益不会被写为已验证结论。</p></details>
    </section>`;
  }
  async function load(ctx,pid,host,aid,vid){
    const state={ctx,pid,host,aid,vid,session:null,delivery:null,reports:[],sets:[],preview:'',previewTitle:'',assets:[],error:'',assetName:''};
    const token=++sequence;current=state;
    host.innerHTML='<p class="muted">正在核对特征资产与报告…</p>';
    try{
      const assets=await ctx.req('/projects/'+enc(pid)+'/native-assets');
      if(!isActive(state,token))return;
      state.assets=(assets||[]).filter(a=>a.asset?.columns?.length);
      const selected=chooseSource(state);
      if(!selected){host.innerHTML=state.aid?'<div class="helper warning" role="alert">当前数据资产已不存在或无权限访问。<a href="/projects/'+enc(pid)+'/data">返回数据管理重新选择</a>。</div>':'<div class="empty">还没有数据资产。请先上传并发布数据。</div>';return;}
      state.aid=selected.native_asset_id;state.assetName=selected.asset?.name||state.aid;
      const prefix='/projects/'+enc(pid)+'/feature-lab';
      const [latest,sets]=await Promise.all([
        ctx.req(prefix+'/analysis/latest?asset_id='+enc(state.aid)+'&version_id='+enc(vid)),
        ctx.req(prefix+'/sets?asset_id='+enc(state.aid))]);
      if(!isActive(state,token))return;
      state.session=latest?.session||null;
      state.sets=Array.isArray(sets)?sets:sets?.items||[];
      if(state.session){
        const sid=enc(state.session.session_id);
        const results=await Promise.allSettled([
          ctx.req(prefix+'/analysis/sessions/'+sid+'/delivery'),
          ctx.req(prefix+'/analysis/sessions/'+sid+'/reports')]);
        if(!isActive(state,token))return;
        state.delivery=results[0].status==='fulfilled'?results[0].value:{status:'blocked'};
        state.reports=results[1].status==='fulfilled'?results[1].value.reports||[]:[];
        if(results.some(x=>x.status==='rejected'))state.error='部分历史成果暂不可核验；请查看任务日志。';
      }
      if(isActive(state,token))host.innerHTML=renderPage(state);
    }catch(error){if(isActive(state,token))host.innerHTML='<div class="helper warning" role="alert">成果读取失败：'+esc(error.message||error)+'。请检查系统日志，再点击浏览器刷新重试。<a href="/logs">查看系统日志</a></div>';}
  }
  async function getReport(state,archivedId=null){
    const prefix='/projects/'+enc(state.pid)+'/feature-lab';
    if(!state.session)throw Error('尚无可用的分析任务');
    const result=await state.ctx.req(archivedId?
      prefix+'/reports/'+enc(archivedId):
      prefix+'/analysis/sessions/'+enc(state.session.session_id)+'/report');
    if(result.source_sha256 && result.source_sha256!==state.session.source_sha256)
      throw Error('报告与当前源文件不一致');
    if(archivedId){
      if(!result.verified||!result.immutable||result.session_id!==state.session.session_id)
        throw Error('历史报告未通过不可变归档校验');
      if(result.manifest?.source_sha256!==state.session.source_sha256)
        throw Error('历史报告与当前分析源不同');
    }
    await checksum(result.markdown,result.report_sha256);
    return result;
  }
  async function action(event){
    const button=event.target.closest('[data-delivery]');
    const state=current,token=sequence;
    if(!button||!state||!state.host.contains(button))return;
    event.preventDefault();
    const key=button.dataset.delivery;
    if(key==='refresh'){await load(state.ctx,state.pid,state.host,state.aid,state.vid);return;}
    if(key==='manual'||key==='edit-set'){
      if(key==='edit-set' && !state.sets.some(x=>x.set_id===button.dataset.set&&x.asset_id===state.aid&&x.version_id===state.vid))
        throw Error('特征集与当前数据版本不一致');
      window.TimePilotAnalysisWorkspace?.openTool('manual',
        key==='edit-set'?{setId:button.dataset.set}:{});return;
    }
    if(key==='bi'){window.TimePilotAnalysisWorkspace?.openTool('bi');return;}
    if(key==='close-preview'){state.preview='';state.host.innerHTML=renderPage(state);return;}
    button.disabled=true;
    try{
      if(key==='archive'){
        if(state.session?.stale||['blocked','stale'].includes(state.delivery?.status))throw Error('过期或校验失败的结果不能归档');
        await state.ctx.req('/projects/'+enc(state.pid)+'/feature-lab/analysis/sessions/'+enc(state.session.session_id)+'/reports',{
          method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({expected_revision:state.session.revision})});
        if(!isActive(state,token))return;
        await load(state.ctx,state.pid,state.host,state.aid,state.vid);return;
      }
      if(key==='preview-live'||key==='download-live'||key==='preview-archive'||key==='download-archive'){
        const id=key.includes('archive')?button.dataset.id:null;
        if(id&&!state.reports.some(x=>x.report_id===id))throw Error('该归档报告不属于当前会话');
        if(!id&&(state.session?.stale||['blocked','stale'].includes(state.delivery?.status)))throw Error('当前报告来源无效');
        const report=await getReport(state,id);
        if(!isActive(state,token))return;
        if(key.startsWith('download'))download(report.markdown,'TimePilot_特征分析_'+state.session.session_id.slice(0,8)+(id?'_'+report.revision:'')+'.md');
        else{state.preview=report.markdown;state.previewTitle=id?'已归档报告（字节已核验）':'当前分析报告（字节已核验）';state.host.innerHTML=renderPage(state);}
        return;
      }
      if(key==='diff'){
        if(state.reports.length<2)throw Error('至少需要两个已归档的报告版本');
        const [left,right]=state.reports;
        const prefix='/projects/'+enc(state.pid)+'/feature-lab/reports/';
        const response=await state.ctx.req(prefix+enc(left.report_id)+'/diff?other_id='+enc(right.report_id));
        if(!isActive(state,token))return;
        state.preview=response.diff||'报告内容没有差异';state.previewTitle='最近两版报告差异';state.host.innerHTML=renderPage(state);
        return;
      }
    }catch(error){if(isActive(state,token)){state.error='操作未完成：'+String(error.message||error);state.host.innerHTML=renderPage(state);}}
    finally{button.disabled=false;}
  }
  async function render(ctx,pid,host,assetId='',versionId='base'){
    if(!host)return;
    host.addEventListener('click',event=>{action(event).catch(error=>{
      if(current?.host===host){current.error=String(error.message||error);host.innerHTML=renderPage(current);}
    });});
    await load(ctx,pid,host,assetId,versionId||'base');
  }
  window.TimePilotAnalysisDeliveryUI={render,renderPage,checksum};
})();
