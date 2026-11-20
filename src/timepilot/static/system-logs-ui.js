/* v0.38.3: worker leases, queues and server log console; filters are sent to bounded and RBAC-protected API. */
(() => {
  'use strict';
  const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let current=null;
  async function fetchData(){
    if(!current)return;
    const mine=current;
    const params=new URLSearchParams({level:mine.level,source:mine.source,q:mine.q,since:mine.since,offset:String(mine.offset),limit:'100'});
    try {
      const result=await mine.ctx.req('/system/logs?'+params);
      if(mine!==current || !mine.host.isConnected)return;
      mine.data=result;
      renderTable();
    } catch(error){if(mine===current)mine.host.querySelector('#tp-log-rows').innerHTML='<p class="helper warning">'+esc(error.message)+'</p>';}
  }
  function renderTable(){
    const s=current;if(!s)return;
    const table=s.host.querySelector('#tp-log-rows');if(!table)return;
    const items=s.data?.items||[];
    const groups=(s.data?.error_groups||[]).slice(0,8);
    const summary=groups.length?`<section class="tp-log-errors"><h3>近期异常归并</h3>${groups.map(g=>`<button type="button" class="button" data-log-diagnostic="${esc(g.diagnostic_id)}" title="筛选该诊断">${esc(g.diagnostic_id)} · ${esc(g.error_type||g.operation)} · 同类 ${esc(g.count)} 次</button>`).join('')}</section>`:'';
    table.innerHTML=summary+`<div class="tp-log-meta">找到 ${esc(s.data?.matched||0)} 条（当前第 ${s.offset+1} 条起）；日志最多保存 6 个轮转文件，每份 5 MB。${s.data?.truncated?'日志记录过多，结果为部分扫描。':''}</div>
      <div class="tp-log-table-wrap"><table class="data-table tp-log-table"><thead><tr><th>时间</th><th>级别</th><th>模块</th><th>内容</th></tr></thead><tbody>${items.map(x=>`<tr><td>${esc(x.time)}</td><td><span class="tp-log-level tp-log-${esc(x.level.toLowerCase())}">${esc(x.level)}</span></td><td>${esc(x.source)}</td><td><span class="tp-log-message">${esc(x.message)}</span>${x.request_id?`<small>请求 ${esc(x.request_id)}</small>`:''}${x.diagnostic_id?`<small>诊断 ${esc(x.diagnostic_id)} · 阶段 ${esc(x.operation||'')} · 当前累计 ${esc(x.repeat_count||1)} 次</small>`:''}${Array.isArray(x.stack)&&x.stack.length?`<details><summary>查看安全调用栈</summary><pre>${esc(x.stack.map(f=>`${f.file}:${f.line} ${f.function}`).join('\n'))}</pre></details>`:''}</td></tr>`).join('')||'<tr><td colspan="4">没有匹配的日志。</td></tr>'}</tbody></table></div>
      <div class="tp-log-pager"><button type="button" data-log="prev" ${s.offset<=0?'disabled':''}>上一页</button><button type="button" data-log="next" ${s.offset+items.length>=Number(s.data?.matched||0)?'disabled':''}>下一页</button></div>`;
  }
  const roleNames={forecast:'预测任务',agent:'科研 Agent',upload:'数据导入',publication:'数据发布',feature:'特征与评价'};
  async function fetchWorkers(){
    const mine=current;
    if(!mine)return;
    const host=mine.host;
    const section=host.querySelector('#tp-worker-rows');
    if(!section)return;
    try {
      const report=await mine.ctx.req('/system/workers');
      if(current!==mine||!host.isConnected)return;
      const roles=report.roles||{};
      const queue=report.queues||{};
      const queueInfo=Object.entries(queue).filter(([,x])=>x.available).map(([name,x])=>{
        const label={'feature_jobs':'特征计算','project_import_jobs':'数据导入','project_publication_jobs':'数据发布'}[name]||name;
        const states=Object.entries(x.counts||{}).map(([state,num])=>`${esc(state)} ${esc(num)}`).join(' · ')||'暂无任务';
        return `<span>${esc(label)}：${states}</span>`;
      }).join('　');
      section.innerHTML=`<div class="tp-log-meta">部署：${report.deployment==='external'?'独立进程':'内置线程'} · 在线 ${esc(report.online_count)}/${esc(report.required_count)} · 本机 SQLite 租约</div>
        <div class="tp-log-table-wrap"><table class="data-table tp-log-table"><thead><tr><th>服务</th><th>状态</th><th>已领任务轮次</th><th>异常轮次</th><th>轮次 p95</th></tr></thead><tbody>
        ${Object.entries(roleNames).map(([key,name])=>{const r=roles[key]||{};
          const label=!r.online?'离线':r.status==='memory_paused'?'内存预算暂停':r.status==='busy'?'执行中':'空闲';
          return `<tr><td>${esc(name)}</td><td>${esc(label)}</td><td>${esc(r.claimed_cycles??0)}</td><td>${esc(r.error_cycles??0)}</td><td>${r.p95_ms==null?'—':esc(r.p95_ms)+' ms'}</td></tr>`;
        }).join('')}</tbody></table></div><p class="muted small">${queueInfo||'无队列状态可读取'}。轮次耗时不代表任务完成时间；Worker 离线后，租约过期的任务按各队列规则接管。</p>`;
    } catch(error){
      if(current===mine&&host.isConnected)section.innerHTML='<p class="helper warning">Worker 状态获取失败：'+esc(error.message)+'</p>';
    }
  }
  async function render(ctx){
    const host=ctx.root;
    current={ctx,host,level:'',source:'',q:'',since:'',offset:0,data:null};
    host.innerHTML=`<div class="tp-log-screen"><div class="section-row"><div><h1>系统日志</h1><p class="muted">查看系统请求、后台任务与异常。可按级别、模块及关键字筛选，也可导出当前筛选结果。</p></div><button type="button" class="button" data-log="refresh">刷新</button></div>
    <section class="panel" aria-label="后台 Worker 状态"><h2>后台任务状态</h2><div id="tp-worker-rows" aria-live="polite">正在读取 Worker 心跳…</div></section>
    <div class="panel tp-log-filter"><label>级别<select id="tp-log-level"><option value="">全部</option><option>INFO</option><option>WARNING</option><option>ERROR</option><option>CRITICAL</option></select></label><label>模块<input id="tp-log-source" placeholder="如 feature 或 http"></label><label>关键字<input id="tp-log-q" placeholder="如 failed、timeout"></label><label>开始时间（ISO 8601）<input id="tp-log-since" placeholder="2026-11-16T08:00:00+00:00"></label><button type="button" class="button button-primary" data-log="apply">筛选</button><button type="button" class="button" data-log="download-jsonl">导出 JSONL</button><button type="button" class="button" data-log="download-txt">导出文本</button><button type="button" class="button" data-log="download-zip">下载诊断证据包</button></div>
    <div id="tp-log-rows" class="panel" aria-live="polite">正在读取日志…</div><p class="muted small">服务器自动隐藏常见凭据字段；日志中不记录 HTTP 请求体与 URL 查询参数。企业模式仅租户管理员可访问。单次导出最多 500 条匹配记录，超出时请缩小筛选范围。</p></div>`;
    if(host.__tpLogClick)host.removeEventListener('click',host.__tpLogClick);
    host.__tpLogClick=async event=>{
      const key=event.target.closest('[data-log]')?.dataset.log;
      const diagnostic=event.target.closest('[data-log-diagnostic]')?.dataset.logDiagnostic;
      if(diagnostic&&current?.host===host){current.q=diagnostic;current.offset=0;host.querySelector('#tp-log-q').value=diagnostic;await fetchData();return;}
      if(!key||current?.host!==host)return;
      if(key==='apply'){
        for(const [k,id] of [['level','tp-log-level'],['source','tp-log-source'],['q','tp-log-q'],['since','tp-log-since']])current[k]=host.querySelector('#'+id).value.trim();
        current.offset=0;await fetchData();
      } else if(key==='refresh')await Promise.all([fetchData(),fetchWorkers()]);
      else if(key==='prev'){current.offset=Math.max(0,current.offset-100);await fetchData();}
      else if(key==='next'){current.offset+=100;await fetchData();}
      else if(key.startsWith('download-')){
        const params=new URLSearchParams({level:current.level,source:current.source,q:current.q,since:current.since,format:key.slice(9)});
        // A direct same-origin download inherits the admin session cookie.
        const a=document.createElement('a');a.href='/api/v1/system/logs/download?'+params;a.download='timepilot-logs.'+key.slice(9);document.body.appendChild(a);a.click();a.remove();
      }
    };
    host.addEventListener('click',host.__tpLogClick);
    await Promise.all([fetchData(),fetchWorkers()]);
  }
  window.TimePilotSystemLogs={render};
})();
