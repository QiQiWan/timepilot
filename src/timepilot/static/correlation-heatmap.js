/* The authorized DuckDB result is displayed locally, including n_pair and nulls. */
(() => {
  'use strict';
  function render(container,source){
    if(!source||!Array.isArray(source.field_ids)||!Array.isArray(source.matrix))
      throw new Error('相关矩阵响应结构无效');
    const fields=source.field_ids,N=fields.length;
    if(N<2||N>40||source.matrix.length!==N*N)throw new Error('矩阵维度不一致');
    const index=new Map(source.matrix.map(c=>[c.field_x+'\u0000'+c.field_y,c]));
    if(index.size!==N*N)throw new Error('矩阵单元存在重复或缺失');
    const wrapper=document.createElement('section');wrapper.className='tp-heatmap';
    const title=document.createElement('div');title.className='tp-heatmap-title';
    title.textContent=`Pearson 相关性 · ${N} 个字段 · 全量筛选行集`;wrapper.append(title);
    const scroll=document.createElement('div');scroll.className='tp-heatmap-scroll';
    const table=document.createElement('table');table.className='tp-heatmap-table';
    table.setAttribute('aria-label','字段相关系数与成对有效样本数');
    const head=table.createTHead(),hr=head.insertRow();hr.append(document.createElement('th'));
    for(const name of fields){const th=document.createElement('th');th.textContent=name;hr.append(th)}
    const body=table.createTBody();
    for(const x of fields){
      const tr=body.insertRow(),label=document.createElement('th');label.textContent=x;tr.append(label);
      for(const y of fields){
        const c=index.get(x+'\u0000'+y);if(!c)throw new Error('缺失矩阵单元');
        const cell=tr.insertCell();cell.className='tp-heatmap-cell';
        const v=c.coefficient;
        cell.textContent=v==null?'—':Number(v).toFixed(2);
        cell.title=`${x} × ${y}\nr = ${v==null?'无定义':Number(v).toFixed(6)}\n成对有效样本 n = ${c.n_pair}\n${c.status}`;
        if(v==null){cell.classList.add('is-undefined');}
        else{const hue=v>=0?215:12;cell.style.backgroundColor=`hsla(${hue},75%,55%,${Math.min(.82,Math.abs(v)*.67+.12)})`;}
        cell.setAttribute('aria-label',cell.title.replaceAll('\n','，'));
      }
    }
    scroll.append(table);wrapper.append(scroll);
    const legend=document.createElement('p');legend.className='tp-heatmap-legend';
    legend.textContent='蓝色为正相关，橙色为负相关；颜色深浅表示强度。悬停查看 r、n 和状态。无定义单元格不显示系数。';wrapper.append(legend);
    const csv=document.createElement('button');csv.className='button button-small';csv.type='button';csv.textContent='导出矩阵 CSV';
    csv.addEventListener('click',()=>{
      const lines=['field_x,field_y,coefficient,n_pair,status',...source.matrix.map(r=>
        [r.field_x,r.field_y,r.coefficient??'',r.n_pair,r.status].map(v=>'"'+String(v).replaceAll('"','""')+'"').join(','))];
      const link=document.createElement('a');const url=URL.createObjectURL(new Blob(['\ufeff'+lines.join('\n')],{type:'text/csv'}));
      link.href=url;link.download='timepilot_correlation.csv';link.click();URL.revokeObjectURL(url);
    });wrapper.append(csv);container.replaceChildren(wrapper);
    return {rendered:true,cells:N*N,field_ids:fields};
  }
  window.TimePilotCorrelationHeatmap={render};
})();
