/* Shared DataAsset workbench: one source revision, one selection, many read views.
 * A linked Perspective view never copies raw bytes or exposes edit endpoints.
 */
(() => {
  'use strict';
  let ctx, current, asset, version, selected={filters:{},date_from:null,date_to:null};
  let generation=0, nativeModule=null, nativeMounted=false, allViews=[];
  const E=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const U=x=>encodeURIComponent(String(x));
  const fmt=x=>Number(x||0).toLocaleString('zh-CN');
  const post=data=>({method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
  const api=(path,opts={})=>ctx.req('/data-assets'+path,opts);
  const link=(a,v,tail='')=>`/data-assets/${U(a)}/versions/${U(v)}${tail}`;
  function note(t){return `<div class="helper warning">${E(t)}</div>`}
  async function release(){if(nativeMounted&&nativeModule){try{await nativeModule.disposePerspective()}catch{}}nativeMounted=false;nativeModule=null;}
  function renderList(list){ctx.root.innerHTML=`<div class="page-heading"><div><h1>统一数据资产</h1><p>一次导入，版本共用。筛选、趋势、原生 BI 和预测草稿引用同一份数据。</p></div><a class="button button-primary" href="/imports">导入数据</a></div><div class="panel"><h2>可用数据资产</h2>${list.length?list.map(x=>`<a class="bi-dataset-item" href="${link(x.asset_id,x.current_version)}"><b>${E(x.name)}</b><span>${fmt(x.version_count)} 个版本 · 引用原始文件 · ${E(x.source_kind)}</span></a>`).join(''):'<p class="muted">尚无已转换的数据版本，请先导入并确认。</p>'}</div>`;}
  function getSelection(){const filters={};for(const n of ['country','brand','vehicle','source_name','statistical_scope']){
    const v=document.getElementById('asset-filter-'+n)?.value;if(v&&v!=='__all__')filters[n]=[v];
  }return {filters,date_from:null,date_to:null};}
  function opts(values,sel){return `<option value="__all__">全部</option>`+(values||[]).map(x=>`<option value="${E(x.value)}" ${sel===x.value?'selected':''}>${E(x.value)} (${fmt(x.count)})</option>`).join('')}
  function dropdowns(facets){const labels={country:'国家/地区',brand:'品牌',vehicle:'车型',source_name:'数据来源',statistical_scope:'统计口径'};
    return Object.keys(labels).map(x=>`<label class="field"><span>${labels[x]}</span><select id="asset-filter-${x}" data-asset-change="filter">${opts(facets[x]?.items,selected.filters[x]?.[0])}</select></label>`).join('');}
  function rowsGrid(items){if(!items?.length)return '<p class="muted">当前条件下没有数据行。</p>';
    const columns=['timestamp','country','brand','vehicle','target','available_at','statistical_scope','source_name','quality_flags'];
    const labels={timestamp:'月份',country:'国家/地区',brand:'品牌',vehicle:'车型',target:'目标值',available_at:'发布时间',statistical_scope:'统计口径',source_name:'来源',quality_flags:'检查标记'};
    return `<div class="smart-table-wrap"><table class="data-table"><thead><tr>${columns.map(x=>`<th>${labels[x]}</th>`).join('')}</tr></thead><tbody>${items.map(r=>`<tr>${columns.map(c=>`<td>${E(r[c])}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
  }
  function chart(data){if(!data?.length)return '<p class="muted">请进一步筛选数据以展示数值趋势。</p>';
    const arr=data.filter(x=>x.value!=null).slice(0,48);if(!arr.length)return '<p class="muted">没有有效数值。</p>';
    const max=Math.max(1,...arr.map(x=>Math.abs(Number(x.value)||0)));
    const width=850,height=190,step=width/arr.length;
    const bars=arr.map((r,i)=>{const h=Math.max(1,Number(r.value)/max*145);return `<rect x="${(i*step+2).toFixed(1)}" y="${(168-h).toFixed(1)}" width="${Math.max(1,step-4).toFixed(1)}" height="${h.toFixed(1)}" fill="#2563c9"><title>${E(r.dimension)}：${E(r.value)}</title></rect>`}).join('');
    return `<svg viewBox="0 0 ${width} ${height}" style="max-width:100%;height:auto" role="img" aria-label="当前筛选的数据趋势图">${bars}<line x1="0" y1="168" x2="${width}" y2="168" stroke="#9daac0"/></svg><p class="muted small">只显示前 ${arr.length} 个分组，鼠标指向可查看数值。</p>`;
  }
  async function updateData(){const requestId=++generation;const slot=document.getElementById('asset-data');if(!slot)return;
    slot.innerHTML='<p class="muted">正在查询相同版本的数据...</p>';
    try{
      const analysis=await api(`/${U(asset.asset_id)}/versions/${U(version.version_id)}/analyze`,post(selected));
      const grid=await api(`/${U(asset.asset_id)}/versions/${U(version.version_id)}/grid`,post({selection:selected,limit:40}));
      if(requestId!==generation)return;
      const s=analysis.summary;
      document.getElementById('asset-filters').innerHTML=dropdowns(analysis.facets);
      document.getElementById('asset-metrics').innerHTML=`<div class="grid grid-4">${[['记录',s.rows],['系列',s.series],['有效覆盖率',(Number(s.coverage)*100).toFixed(1)+'%'],['缺失',s.missing],['冲突',s.conflict],['发布时间缺失',s.unpublished]].map(([n,v])=>`<div class="panel"><small class="muted">${E(n)}</small><h3>${E(v)}</h3></div>`).join('')}</div>`;
      const warnings=analysis.warnings||[];
      const show=(s.distinct_scopes<=1&&s.distinct_sources<=1);
      let group=[];if(show){const g=await api(`/${U(asset.asset_id)}/versions/${U(version.version_id)}/group`,post({selection:selected,group_by:'timestamp',operation:'sum',limit:240}));group=g.rows}
      if(requestId!==generation)return;
      slot.innerHTML=`${warnings.map(note).join('')}<div class="panel"><h2>按月份趋势</h2>${show?chart(group):note('存在多个来源或统计口径，请先筛选单一来源和口径后查看数值合计。')}</div><div class="panel"><h2>数据明细（前 40 行）</h2>${rowsGrid(grid.rows)}</div>`;
    }catch(e){if(requestId===generation)slot.innerHTML=note(e.message);}
  }
  async function ensureIndex(){const id=asset.import_id,ver=version.version_id;
    const path=`/smart-import/imports/${U(id)}/versions/${U(ver)}/workbench`;
    let result=await ctx.req(path+'/index-status');if(result.status==='unbuilt'||result.status==='failed')result=await ctx.req(path+'/build-index',{method:'POST'});
    for(let i=0;i<200&&['queued','running'].includes(result.status);i++){
      const el=document.getElementById('asset-data');if(el)el.innerHTML=`<p class="muted">首次建立只读分析索引：${fmt(result.indexed_rows)} / ${fmt(result.expected_rows)}</p>`;
      await new Promise(ok=>setTimeout(ok,350));result=await ctx.req(path+'/index-status');
    }if(result.status!=='ready')throw new Error(result.error||'分析索引未就绪，请稍后重试');
  }
  async function detail(id,vid){asset=await api('/'+U(id));version=await api('/'+U(id)+'/versions/'+U(vid));
    ctx.root.innerHTML=`<div class="page-heading"><div><h1>${E(version.name)}</h1><p>统一资产 ${E(id)} · 源版本 ${E(vid)} · ${fmt(version.row_count)} 行 · 只读原始数据</p></div><a class="button" href="/data-assets">所有资产</a></div>
      <div class="panel"><div class="section-row"><h2>业务筛选</h2><div class="smart-actions"><a class="button button-primary" href="/imports/${U(asset.import_id)}/versions/${U(vid)}/workbench">用于预测 / 保存视图</a><button type="button" class="button" data-asset="open-native">原生 BI（引用）</button><button type="button" class="button" data-asset="generic-bi">通用 BI（引用）</button><a class="button" href="/research?tab=modeling">受控自主建模</a></div></div><div id="asset-filters" class="grid grid-3"></div><div id="asset-metrics"></div></div>
      <div class="panel"><h2>分析视图与字段语义</h2><div class="smart-actions"><button class="button button-primary" type="button" data-asset="save-view">保存当前筛选视图</button></div><div id="asset-views" class="muted">正在加载保存的分析视图...</div><p class="muted small">字段语义及原始数据哈希独立版本化，所有 BI 查询复用此资产编号。</p></div><div id="asset-data"></div>`;
    await ensureIndex();const views=await api(`/${U(id)}/versions/${U(vid)}/views`);allViews=views;
    document.getElementById('asset-views').innerHTML=views.length?views.map(v=>`<button type="button" class="button button-small" data-asset="load-view" data-view-id="${E(v.view_id)}">${E(v.name)} · ${fmt(v.summary_at_save?.rows)} 行</button><a class="button button-small" href="${link(id,vid,'/native')}?view_id=${U(v.view_id)}">原生 BI</a><a class="button button-small" href="/imports/${U(asset.import_id)}/versions/${U(vid)}/workbench?view_id=${U(v.view_id)}">用于预测</a>`).join(''):'暂无保存视图。可在预测工作台中保存当前选择。';
    await updateData();
  }
  async function nativePage(id,vid){await release();asset=await api('/'+U(id));version=await api('/'+U(id)+'/versions/'+U(vid));
    const query=new URLSearchParams(location.search);
    const viewId=query.get('view_id');
    if(!viewId&&query.has('selection')){
      const incoming=JSON.parse(query.get('selection'));
      if(!incoming||typeof incoming!=='object')throw new Error('筛选条件格式不正确');
      selected={filters:incoming.filters||{},date_from:incoming.date_from||null,date_to:incoming.date_to||null};
    }
    if(viewId){const saved=await api(`/${U(id)}/versions/${U(vid)}/views/${U(viewId)}`);
      selected={filters:{...saved.selection.filters},date_from:saved.selection.date_from||null,date_to:saved.selection.date_to||null};}
    ctx.root.innerHTML=`<div class="page-heading"><div><h1>原生 BI · ${E(version.name)}</h1><p>直接引用现有不可变数据版本。拖拽透视、图表和字段筛选由 Perspective 执行。</p></div><div class="action-row"><a class="button" href="${ctx.projectId?'/projects/'+U(ctx.projectId)+'/data/'+U(id):link(id,vid)}">返回数据资产</a>${ctx.projectId?'<button class="button button-primary" data-project="make-editable">生成可编辑工作副本</button>':''}</div></div><div class="panel"><div class="smart-actions"><button type="button" class="button" data-asset="pivot">透视</button><button type="button" class="button" data-asset="bar">柱状图</button><button type="button" class="button" data-asset="grid">明细网格</button><button type="button" class="button" data-asset="reconnect">重新连接</button></div><div id="asset-native-root" style="min-height:500px"><p class="muted">正在加载已编译的 Perspective...</p></div></div>`;
    const el=document.getElementById('asset-native-root');try{
      await ctx.req('/ready');
      nativeModule=await import('/assets/native-bi/perspective-entry.js');
      const proto=location.protocol==='https:'?'wss:':'ws:';
      const url=`${proto}//${location.host}/api/v1/native-bi/linked/${U(id)}/versions/${U(vid)}/ws?selection=${encodeURIComponent(JSON.stringify(selected))}`;
      await nativeModule.mountPerspectiveRemote(el,url);nativeMounted=true;
    }catch(e){el.innerHTML=note(`正式 BI 未就绪：${e.message}。请使用统一启动入口检查编译和运行依赖。`)}
  }
  async function render(context){ctx=context;generation++;await release();
    const parts=(ctx.route||location.pathname).split('/').filter(Boolean);
    if(parts.length===1){renderList(await api(''));return}
    if(parts[0]!=='data-assets'||parts[2]!=='versions'){ctx.root.innerHTML=note('资产页面路径无效');return}
    const newAssetVersion=parts[1]+':'+parts[3];
    if(current!==newAssetVersion){selected={filters:{},date_from:null,date_to:null};current=newAssetVersion}
    try{if(parts[4]==='native')await nativePage(parts[1],parts[3]);else await detail(parts[1],parts[3]);}
    catch(e){ctx.root.innerHTML=note(e.message)}
  }
  function changed(event){if(!event.target.matches('[data-asset-change]'))return false;
    selected=getSelection();void updateData();return true;}
  async function click(event){const button=event.target.closest('[data-asset]');if(!button)return false;
    event.preventDefault();try{
      if(button.dataset.asset==='reconnect')await nativePage(asset.asset_id,version.version_id);
      else if(button.dataset.asset==='open-native'){
        ctx.navigate(link(asset.asset_id,version.version_id,'/native')+'?selection='+U(JSON.stringify(selected)));
      }
      else if(button.dataset.asset==='generic-bi'){
        const result=await api(`/${U(asset.asset_id)}/versions/${U(version.version_id)}/legacy-bi-link`,post({selection:selected}));
        ctx.navigate('/bi/table/'+U(result.table_id));
      }
      else if(button.dataset.asset==='save-view'){
        const name=window.prompt('请输入筛选视图名称');if(!name?.trim())return true;
        const saved=await api(`/${U(asset.asset_id)}/versions/${U(version.version_id)}/views`,post({name:name.trim(),selection:selected}));
        ctx.toast('已保存筛选视图：'+saved.name);
        await detail(asset.asset_id,version.version_id);
      }
      else if(button.dataset.asset==='load-view'){
        const saved=await api(`/${U(asset.asset_id)}/versions/${U(version.version_id)}/views/${U(button.dataset.viewId)}`);
        selected={filters:{...saved.selection.filters},date_from:saved.selection.date_from||null,date_to:saved.selection.date_to||null};
        await updateData();ctx.toast('已载入筛选视图：'+saved.name);
      }
      else if(nativeModule&&nativeMounted)await nativeModule.setPerspectivePreset(button.dataset.asset);
    }catch(e){ctx.toast(e.message)}return true;
  }
  window.TimePilotDataAssets={render,changed,click};
})();
