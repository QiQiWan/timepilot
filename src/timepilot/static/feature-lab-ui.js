/* Data-versioned feature engineering; all SQL and publishing happen on server. */
(() => {
  'use strict';
  const esc = x => String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const U=encodeURIComponent;
  const post=data=>({method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
  let ctx=null,projectId='',assets=[],draft=[],list=[],preview=null,selected='',recipeName='';
  const base=()=>'/api/v1/projects/'+U(projectId)+'/feature-lab';
  const api=(path,options)=>ctx.req(base()+path,options);
  const fields=()=>assets.find(a=>a.native_asset_id===selected)?.asset?.columns||[];
  const options=cols=>cols.map(c=>`<option value="${esc(c.name)}">${esc(c.name)} (${esc(c.physical_type)})</option>`).join('');
  function draw(){
    const root=document.getElementById('tp-feature-lab');if(!root)return;
    const numeric=fields().filter(c=>/(INT|DOUBLE|FLOAT|DECIMAL|REAL)/i.test(c.physical_type||''));
    root.innerHTML=`<div class="panel tp-feat-main"><div class="section-row"><div><h2>建立特征配方</h2><p class="muted small">原数据和列名保持不变；窗口特征使用当前及历史记录，不读取未来数据。</p></div></div>
      <form id="tp-feature-form" class="form"><div class="grid grid-2">
      <label class="field"><span>数据资产</span><select name="asset_id" data-feature="select-asset">${assets.map(a=>`<option value="${esc(a.native_asset_id)}" ${selected===a.native_asset_id?'selected':''}>${esc(a.asset?.name||a.native_asset_id)}</option>`).join('')}</select></label>
      <label class="field"><span>配方名称</span><input name="name" required maxlength="120" value="${esc(recipeName)}" placeholder="例如：喷涂设备运行状态特征"></label></div>
      <div class="tp-feature-builder"><div class="grid grid-3">
      <label class="field"><span>计算类型</span><select name="kind" data-feature="choose-kind"><option value="rolling_mean">移动平均</option><option value="lag">历史滞后</option><option value="difference">相邻变化量</option><option value="rolling_std">滚动标准差</option><option value="arithmetic">两列算术运算</option></select></label>
      <label class="field"><span>数值字段</span><select name="field">${options(numeric)}</select></label>
      <label class="field"><span>第二字段（仅算术运算）</span><select name="other_field"><option value="">无</option>${options(numeric)}</select></label>
      <label class="field"><span>排序依据</span><select name="order_by"><option value="__tp_rowid">原记录顺序（非日期）</option>${options(fields())}</select></label>
      <label class="field"><span>设备/工况分组</span><select name="partition_by"><option value="">不分组</option>${options(fields())}</select></label>
      <label class="field"><span>窗口/滞后条数</span><input type="number" name="window" min="1" max="240" value="5"></label>
      <label class="field"><span>运算符（仅算术运算）</span><select name="operator"><option value="+">+</option><option value="-">−</option><option value="*">×</option><option value="/">÷</option></select></label>
      <label class="field"><span>新特征技术名</span><input name="output" required maxlength="64" pattern="[A-Za-z_][A-Za-z0-9_]*" placeholder="A1_ma5"></label>
      </div><button type="button" data-feature="add-step" class="button">添加特征步骤</button></div>
      <div class="tp-feature-steps">${draft.map((step,i)=>`<div class="tp-feature-step"><b>${i+1}. ${esc(step.name)}</b> <small>${esc(step.kind)} · ${esc(step.field)} · 按 ${esc(step.order_by)}</small><button type="button" class="button button-small" data-feature="remove-step" data-index="${i}">移除</button></div>`).join('')||'<p class="muted small">当前尚未添加特征步骤。</p>'}</div>
      <button class="button button-primary" type="submit" ${!draft.length?'disabled':''}>保存配方</button></form></div>
      <div class="panel tp-feature-saved"><div class="section-row"><h2>已有特征配方</h2><button class="button button-small" type="button" data-feature="refresh">刷新</button></div>
      ${list.length?list.map(r=>`<div class="tp-feature-item"><div><strong>${esc(r.name)}</strong><small>${esc(r.state)} · ${esc(r.spec.steps.map(x=>x.name).join('、'))}</small><small>来源：${esc(r.source_sha256.slice(0,12))} · ${esc(r.version_id)}</small></div><div class="action-row"><button type="button" class="button button-small" data-feature="preview" data-id="${esc(r.recipe_id)}">预览</button>${r.state==='draft'?`<button type="button" class="button button-small" data-feature="request-review" data-id="${esc(r.recipe_id)}">提交审核</button>`:''}${r.state==='pending_review'?`<button type="button" class="button button-small" data-feature="approve" data-id="${esc(r.recipe_id)}">审核</button>`:''}${r.state==='approved'?`<button type="button" class="button button-primary button-small" data-feature="publish" data-id="${esc(r.recipe_id)}">发布新数据资产</button>`:''}${r.state==='published'?`<a class="button button-small" href="/projects/${U(projectId)}/bi/native/${U(r.derived_id)}">打开特征资产</a>`:''}</div></div>`).join(''):'<p class="muted">还没有保存的配方。</p>'}
      <div id="tp-feature-preview">${preview?`<h3>真实数据预览 · ${esc(preview.returned)} 条</h3><p class="muted small">${esc(preview.total_rows)} 行来源，预览仅显示部分记录。训练前必须经过发布审核。</p><div class="table-wrap"><table class="data-table"><thead><tr>${preview.columns.map(x=>`<th>${esc(x)}</th>`).join('')}</tr></thead><tbody>${preview.preview_rows.map(row=>`<tr>${preview.columns.map(c=>`<td>${esc(row[c])}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`:''}</div></div>`;
  }
  async function render(context,pid){
    ctx=context;projectId=pid;draft=[];preview=null;recipeName='';
    const [candidate,recipes]=await Promise.all([
      ctx.req('/projects/'+U(pid)+'/native-assets'),api('/recipes')]);
    assets=candidate.filter(a=>a.asset?.columns?.length);
    list=recipes;
    selected=assets.find(a=>a.native_asset_id===new URLSearchParams(location.search).get('asset_id'))?.native_asset_id || assets[0]?.native_asset_id || '';
    draw();
  }
  async function click(event){
    const el=event.target.closest('[data-feature]');if(!el||!ctx)return false;
    const type=el.dataset.feature;
    if(type==='select-asset'||type==='choose-kind')return false;
    const nameInput=document.querySelector('#tp-feature-form [name=name]');
    if(nameInput)recipeName=nameInput.value;
    event.preventDefault();
    try{
      if(type==='add-step'){
        const form=document.getElementById('tp-feature-form');if(!form.reportValidity())return true;
        const d=new FormData(form);
        const name=String(d.get('output')||'').trim();
        const field=String(d.get('field')||'');const kind=String(d.get('kind'));
        if(!name||!field)throw Error('请填写数值字段和输出技术名');
        const step={kind,field,name,operator:String(d.get('operator')||'+'),window:Number(d.get('window')||5),
                    lag:Math.max(1,Number(d.get('window')||1)),order_by:String(d.get('order_by')||'__tp_rowid'),
                    partition_by:String(d.get('partition_by')||'')||null,other_field:String(d.get('other_field')||'')||null};
        if(kind==='arithmetic'&&!step.other_field)throw Error('算术运算需要选择第二个字段');
        if(draft.some(x=>x.name===name))throw Error('输出特征名已存在');
        draft.push(step);draw();
      }else if(type==='remove-step'){draft.splice(Number(el.dataset.index),1);draw();}
      else if(type==='refresh'){list=await api('/recipes');preview=null;draw();}
      else if(type==='preview'){preview=await api('/recipes/'+U(el.dataset.id)+'/preview?limit=20',{method:'POST'});draw();}
      else if(type==='request-review'){
        if(!confirm('将特征定义提交审核？原始数据仍不改变。'))return true;
        await api('/recipes/'+U(el.dataset.id)+'/request-review',{method:'POST'});list=await api('/recipes');draw();
      }else if(type==='approve'){
        const reason=prompt('填写核验意见（检查特征时序和数据用途）');if(!reason?.trim())return true;
        await api('/recipes/'+U(el.dataset.id)+'/approve',post({reason,confirm:true}));list=await api('/recipes');draw();
      }else if(type==='publish'){
        if(!confirm('确认生成新的不可变特征数据资产？完整行集将被扫描，历史数据不会覆盖。'))return true;
        await api('/recipes/'+U(el.dataset.id)+'/publish',{method:'POST'});list=await api('/recipes');draw();
      }
    }catch(error){ctx.toast('特征工程操作失败：'+error.message)}
    return true;
  }
  async function submit(event){
    if(event.target?.id!=='tp-feature-form')return false;
    event.preventDefault();
    try{
      const d=new FormData(event.target);
      const payload={asset_id:String(d.get('asset_id')),version_id:'base',name:String(d.get('name')).trim(),steps:draft};
      await api('/recipes',post(payload));
      list=await api('/recipes');draft=[];preview=null;recipeName='';draw();ctx.toast('特征配方已保存');
    }catch(error){ctx.toast('保存失败：'+error.message)}
    return true;
  }
  function change(event){
    if(event.target?.dataset.feature==='choose-kind'){
      const field=document.querySelector('#tp-feature-form [name=window]');
      if(field){const lag=['lag','difference'].includes(event.target.value);field.min=lag?'1':'2';field.value=lag?'1':'5';}
      return true;
    }
    if(event.target?.dataset.feature!=='select-asset')return false;
    selected=event.target.value;draft=[];recipeName='';draw();return true;
  }
  window.TimePilotFeatureLabUI={render,click,submit,change};
})();
