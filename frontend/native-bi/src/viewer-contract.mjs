/** Perspective v5 view-contract: every panel explicitly binds a named Table. */
export function gridConfig(schema, tableName) {
  if (!tableName || !schema || !Object.keys(schema).length) {
    throw new Error('Perspective 数据表名称或字段为空');
  }
  return {table: tableName, plugin: 'Datagrid', columns: Object.keys(schema),
    group_by: [], split_by: [], settings: true};
}

export function presetConfig(preset, schema, tableName) {
  if (preset === 'grid') return gridConfig(schema, tableName);
  if (!tableName || !schema || !Object.keys(schema).length) {
    throw new Error('Perspective 数据表名称或字段为空');
  }
  const columns = Object.keys(schema).filter(name => name !== '__tp_rowid');
  const timeDimension = columns.find(key => ['datetime', 'date'].includes(schema[key])) || null;
  const dimension = (preset === 'line' && timeDimension) ||
    columns.find(key => ['string', 'date', 'datetime'].includes(schema[key])) || columns[0];
  const metric = columns.find(key => ['integer', 'float', 'number'].includes(schema[key]));
  if (preset === 'pivot') {
    if (!dimension) throw new Error('找不到可分组字段');
    return {table: tableName, plugin: 'Datagrid', group_by: [dimension],
      columns: metric ? [metric] : columns.filter(col => col !== dimension), settings: true};
  }
  if (preset === 'bar' || preset === 'line') {
    if (!metric) throw new Error('没有可绘图的数值字段，请检查数据类型或创建聚合查询');
    return {table: tableName, plugin: preset === 'bar' ? 'Y Bar' : 'Y Line',
      group_by: dimension !== metric ? [dimension] : [], columns: [metric], settings: true};
  }
  throw new Error(`不支持的视图类型：${preset}`);
}

/** Bind a v5 Workspace to its Client and explicitly select a stable Table.
 *  The injected step function makes every async operation separately bounded.
 */
export async function loadNamedClient(viewer, client, table, step = async promise => promise) {
  const tableName = await step(table.get_name(), 'Perspective 数据表名称', 8000);
  const schema = await step(table.schema(), 'Perspective 字段读取', 10000);
  await step(viewer.load(client), 'Perspective Client 挂载', 12000);
  await step(viewer.restore(gridConfig(schema, tableName)), 'Perspective 数据网格渲染', 12000);
  return {tableName, schema};
}

/** Explicit columns from a reviewed BI tool plan (never guess fields or units). */
export function analyticalChartConfig(chart, schema, tableName) {
  if (!chart || !schema || !tableName) throw new Error('缺少图表数据或版本');
  const available = new Set(Object.keys(schema).filter(x => x !== '__tp_rowid'));
  if ((chart.x && !available.has(chart.x)) || (chart.color && !available.has(chart.color)) ||
    !(chart.y || []).every(x => available.has(x))) {
    throw new Error('图表配置引用的数据字段不在 DuckDB 结果中');
  }
  const types = {grid:'Datagrid',pivot:'Datagrid',bar:'Y Bar',line:'Y Line',
    area:'Y Area',scatter:'X/Y Scatter',heatmap:'Heatmap',histogram:'Y Bar'};
  const plugin=types[chart.type];
  if(!plugin)throw new Error(`不支持的图表类型：${chart.type}`);
  if(chart.type==='grid')return gridConfig(schema,tableName);
  const y=(chart.y||[]).filter(v=>v!==chart.x);
  if(chart.type==='pivot')return {table:tableName,plugin,group_by:chart.x?[chart.x]:[],
    columns:y.length?y:[...available].filter(x=>x!==chart.x).slice(0,8),settings:true};
  if(!y.length)throw new Error('图表需要明确的 Y 轴数据列');
  if(chart.type==='scatter'){
    if(!chart.x)throw new Error('散点图需要明确的 X 轴字段');
    return {table:tableName,plugin,columns:[chart.x,...y],
      split_by:chart.color?[chart.color]:[],settings:true};
  }
  if(chart.type==='histogram'){
    throw new Error('直方图必须先创建可审计的数值分箱，不能把类别柱状图冒充直方图');
  }
  return {table:tableName,plugin,group_by:chart.x?[chart.x]:[],columns:y,
    split_by:chart.color?[chart.color]:[],settings:true};
}
