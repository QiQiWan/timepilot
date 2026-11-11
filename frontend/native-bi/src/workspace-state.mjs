/** Versioned workspace base-table rebinding. Derived tables MUST keep their names. */
export const ROOT_TABLE = 'timepilot_active';

export function rebindWorkspaceBase(workspace, previousName, nextName) {
  if (!workspace || typeof workspace !== 'object' || !workspace.panels ||
      typeof workspace.panels !== 'object' || !Object.keys(workspace.panels).length) {
    throw new Error('保存的 Perspective 工作区缺少面板配置');
  }
  if (!previousName || !nextName || typeof previousName !== 'string' || typeof nextName !== 'string') {
    throw new Error('工作区基础表名称无效');
  }
  const result = structuredClone(workspace);
  const derived = result.derived_tables || {};
  const allowed = new Set([previousName, nextName, ...Object.keys(derived)]);
  for (const [name, table] of Object.entries(derived)) {
    if (!table || typeof table !== 'object' || typeof table.table !== 'string') {
      throw new Error(`派生表 ${name} 的来源无效`);
    }
    if (!allowed.has(table.table)) throw new Error(`派生表 ${name} 引用了未知数据源 ${table.table}`);
    if (table.table === previousName) table.table = nextName;
  }
  for (const [panel, config] of Object.entries(result.panels)) {
    if (!config || typeof config.table !== 'string' || !allowed.has(config.table)) {
      throw new Error(`面板 ${panel} 引用了未知数据源`);
    }
    if (config.table === previousName) config.table = nextName;
  }
  return result;
}
