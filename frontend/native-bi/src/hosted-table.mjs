/** Resolve only names actually advertised by a Perspective server.
 * DuckDB Virtual Server exposes catalog-qualified names (e.g. memory.data),
 * while the streaming Perspective Server exposes unqualified names.
 */
export function resolveHostedTableName(names, requestedName) {
  if (!Array.isArray(names) || names.some(name => typeof name !== 'string') ||
      !requestedName || typeof requestedName !== 'string') {
    throw new Error('Perspective 服务端数据表列表格式错误');
  }
  if (names.includes(requestedName)) return requestedName;
  // A fully-qualified request is exact; never silently switch catalogs.
  const candidates = requestedName.includes('.') ? [] :
    names.filter(name => name.endsWith(`.${requestedName}`));
  if (candidates.length === 1) return candidates[0];
  if (candidates.length > 1) {
    throw new Error(`Perspective 数据表 ${requestedName} 在多个数据库中重名：${candidates.join('、')}`);
  }
  throw new Error(`Perspective 服务端没有公布数据表 ${requestedName}。` +
    `可访问的数据表：${names.slice(0, 12).join('、') || '（空）'}。` +
    '请核对 DuckDB 视图创建和数据版本，避免重复安装浏览器或前端依赖。');
}

/** Query the live Perspective registry before calling open_table(). */
export async function openHostedTable(client, requestedName, step = async value => value) {
  const names = await step(client.get_hosted_table_names(), 'Perspective 服务端数据表发现', 10000);
  const exposedName = resolveHostedTableName(names, requestedName);
  const table = await step(client.open_table(exposedName), `Perspective 数据表 ${exposedName} 打开`, 12000);
  return {table, exposedName};
}
