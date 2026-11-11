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
