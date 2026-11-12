/** Pure, security-first Perspective 5.6.1 config adapter.
 * Preserve the bound immutable asset table; NEVER switch to a 1000-row Arrow preview.
 */
const OP={eq:'==',ne:'!=',gt:'>',gte:'>=',lt:'<',lte:'<=',contains:'contains',
  starts_with:'begins with',in:'in',is_null:'is null',not_null:'is not null'};
const PLUGIN={grid:'Datagrid',pivot:'Datagrid',bar:'Y Bar',line:'Y Line',
  area:'Y Area',scatter:'X/Y Scatter',heatmap:'Heatmap'};
const AGG={sum:'sum',avg:'avg',min:'min',max:'max',count:'count'};

export function compileAgentViewerState(state,schema,tableName){
  if(!state || !state.query || !tableName || !schema)throw new Error('缺少可验证的工作区来源');
  // These computations require a named, version-pinned derived table. A base
  // Table cannot silently emulate join/expression/window/resample semantics.
  for(const key of ['expressions','window','windows','bucket','join','industrial']){
    if(state[key] && (Array.isArray(state[key])?state[key].length>0:true))
      throw new Error(`${key} 需要受权限控制的完整派生表，当前不能直接应用在原生源表`);
  }
  const q=state.query,fields=Object.keys(schema).filter(x=>x!=='__tp_rowid');
  const available=new Set(fields);
  const check=(field)=>{if(!available.has(field))throw new Error(`字段 ${field} 不属于当前数据版本`);};
  if(q.offset || (q.limit && q.limit!==300)){
    // Paging is for query previews; Perspective's complete virtual source
    // remains unbounded. The user's paging request is not silently imposed.
    if(q.offset)throw new Error('有偏移量的分页结果无法直接作为全量 Perspective 视图');
  }
  const filters=(q.filters||[]).map(f=>{
    check(f.column);
    if(!Object.prototype.hasOwnProperty.call(OP,f.op))throw new Error('不支持的筛选运算符');
    return f.value===undefined?[f.column,OP[f.op]]:[f.column,OP[f.op],f.value];
  });
  const sort=(q.order_by||[]).map(s=>{check(s.field);return [s.field,s.descending?'desc':'asc'];});
  const group=(q.group_by||[]);group.forEach(check);
  const metrics=(q.metrics||[]);
  const aggregates={};
  for(const m of metrics){
    if(!m.column || !AGG[m.operation])throw new Error('统计别名或统计方式需要先创建可审计派生表');
    check(m.column);aggregates[m.column]=AGG[m.operation];
  }
  const ui=state.viewer||{};
  const split_by=ui.split_by||[];
  if(split_by.length>5||new Set(split_by).size!==split_by.length)throw new Error('列透视维度不合法');
  split_by.forEach(check);
  const config={table:tableName,plugin:'Datagrid',settings:ui.settings!==false,
    filter:filters,sort,group_by:group,split_by:[...split_by],aggregates};
  const chart=state.chart||{type:'grid'};
  if(!PLUGIN[chart.type])throw new Error('未安装或未核实的图表类型');
  config.plugin=PLUGIN[chart.type];
  const x=chart.x||null,y=chart.y||[];if(x)check(x);
  for(const field of y){
    if(!available.has(field)){
      const metric=metrics.find(m=>m.alias===field);
      if(!metric?.column)throw new Error(`图表指标 ${field} 需要派生统计结果`);
    }
  }
  if(chart.type==='scatter'){
    if(!x||!y.length)throw new Error('散点图需要明确横纵轴');
    config.columns=[x,...y.map(v=>metrics.find(m=>m.alias===v)?.column||v)];
  }else if(chart.type==='grid'){
    config.columns=q.columns?.length?q.columns:fields;
  }else if(chart.type==='pivot'){
    config.group_by=x?[x]:group;
    config.columns=q.columns?.length?q.columns:metrics.map(m=>m.column).filter(Boolean).length?
      metrics.map(m=>m.column):fields.filter(f=>!config.group_by.includes(f));
  }else{
    if(!y.length)throw new Error('图表缺少已确认的数值指标');
    config.group_by=x?[x]:group;
    config.columns=y.map(v=>metrics.find(m=>m.alias===v)?.column||v);
  }
  config.columns.forEach(check);
  if(chart.color){check(chart.color);
    if(!config.split_by.includes(chart.color))config.split_by=[...config.split_by,chart.color];}
  return config;
}

export function ensureSameTable(before,after,tableName){
  const panels=Object.values(after?.panels||{});
  if(!panels.length || panels.some(p=>p?.table!==tableName))
    throw new Error('工作区配置尝试改写已绑定数据源');
  for(const [name,item] of Object.entries(after.derived_tables||{})){
    if(item?.table!==tableName && !before?.derived_tables?.[name])
      throw new Error('未获授权的新派生表');
  }
  return true;
}

/** Apply only the operations requested by the Agent, preserving manual Viewer
 * settings not mentioned in those operations. The current saved panel is the
 * authority; the old server query state must not clear manual selections.
 */
export function mergeAgentPanelConfig(live,desired,tools=[]){
  if(!live || live.table!==desired.table)throw new Error('原生工作区来源不一致');
  const changes=tools.map(x=>typeof x==='string'?x:x.tool).filter(Boolean);
  const next={...live};
  const set=(...keys)=>{for(const key of keys)if(Object.prototype.hasOwnProperty.call(desired,key))next[key]=structuredClone(desired[key]);};
  for(const tool of changes){
    if(tool==='bi.filter.set'){
      const result=[...(next.filter||[])];
      for(const f of desired.filter||[])if(!result.some(x=>JSON.stringify(x)===JSON.stringify(f)))result.push(f);
      next.filter=result;
    }
    else if(tool==='bi.filter.clear'||tool==='bi.filter.remove')set('filter');
    else if(tool==='bi.sort.set'||tool==='bi.sort.clear')set('sort');
    else if(tool==='bi.columns.set')set('columns');
    else if(tool.startsWith('bi.aggregate.'))set('group_by','split_by','aggregates','columns','sort');
    else if(tool.startsWith('bi.pivot.split.'))set('split_by');
    else if(tool==='bi.chart.plugin.set'||tool==='bi.chart.type.set')set('plugin');
    else if(tool==='bi.chart.style.set')set('plugin_config','columns_config');
    else if(tool.startsWith('bi.chart.'))set('plugin','columns','group_by','split_by','aggregates');
    else if(tool==='bi.viewer.settings.toggle')set('settings');
  }
  return next;
}
