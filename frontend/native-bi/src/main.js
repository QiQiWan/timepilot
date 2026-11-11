/** TimePilot Perspective 5.6.1: official native datagrid, pivot, chart and workspace.
 *  Online full-data mode uses Perspective's official DuckDB Virtual Server.
 *  Arrow local mode is explicitly a bounded preview/fallback.
 */
import perspective from '@perspective-dev/client';
import viewerModule from '@perspective-dev/viewer';
import '@perspective-dev/viewer-datagrid';
import '@perspective-dev/viewer-charts';
import '@perspective-dev/viewer/dist/css/themes.css';
import SERVER_WASM from '@perspective-dev/server/dist/wasm/perspective-server.wasm?url';
import VIEWER_WASM from '@perspective-dev/viewer/dist/wasm/perspective-viewer.wasm?url';
import {ROOT_TABLE, rebindWorkspaceBase} from './workspace-state.mjs';
import {gridConfig, presetConfig, loadNamedClient} from './viewer-contract.mjs';
import {openHostedTable} from './hosted-table.mjs';
import {releasePerspectiveResources} from './session-lifecycle.mjs';

let initialized, worker, viewer, table, socket, localTable = false;

// A WebSocket that never answers open_table(), or WASM that never initializes,
// must not leave the whole TimePilot page permanently in "loading" state.
function bounded(promise, label, ms = 15000) {
  let timer;
  return Promise.race([
    Promise.resolve(promise),
    new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error(`${label} 超过 ${ms / 1000} 秒未完成，请检查浏览器控制台、WebSocket 与 Perspective 运行状态`)), ms);
    }),
  ]).finally(() => clearTimeout(timer));
}

async function initialize() {
  if (!initialized) initialized = Promise.all([
    perspective.init_server(fetch(SERVER_WASM)),
    viewerModule.init_client(fetch(VIEWER_WASM)),
  ]);
  try {
    await bounded(initialized, "Perspective WASM 初始化", 15000);
  } catch (err) {
    // A transient initialization failure must not poison subsequent retries.
    initialized = null;
    throw err;
  }
}

async function attachViewer(container, client, tableRef, onCell = null) {
  container.replaceChildren();
  const elem = document.createElement('perspective-viewer');
  elem.setAttribute('theme', 'Pro Light');
  elem.style.cssText = 'display:block;width:100%;height:640px;min-height:480px';
  container.appendChild(elem);
  viewer = elem; // Retain it early so a failed mount is also correctly disposed.
  // Perspective v5: load the hosting Client, then choose a named Table via restore().
  // A Table-only load may render in compatibility mode but has no stable workspace
  // root, and restore({plugin}) without a table can leave the workspace blank.
  await loadNamedClient(elem, client, tableRef, bounded);
  if (onCell) elem.addEventListener('perspective-click', async (event) => {
    const detail = event.detail || {};
    const row = detail.row || {};
    const raw = row.__tp_rowid;
    const rowId = Array.isArray(raw) ? raw[0] : raw;
    if (rowId == null || rowId === '') return;
    if (!Number.isSafeInteger(Number(rowId)) || Number(rowId) < 0) return;
    const col = (detail.column_names || []).find(name => name !== '__tp_rowid' &&
      Object.prototype.hasOwnProperty.call(row, name));
    if (!col) return;
    try {
      const workspace = await elem.saveWorkspace();
      const panel = workspace.panels?.[detail.panel] || Object.values(workspace.panels || {})[0];
      if ((panel?.group_by || []).length || (panel?.split_by || []).length) return;
      onCell({row_id: Number(rowId), column: col, value: row[col]});
    } catch { /* a chart or derived aggregation is not an editable cell */ }
  });
  return viewer;
}

export async function mountPerspective(container, arrowBuffer) {
  await disposePerspective();
  await initialize();
  if (!worker) worker = await bounded(perspective.worker(), 'Perspective 本地 Worker', 12000);
  // Stable root name is essential for saveWorkspace()/restoreWorkspace().
  table = await bounded(worker.table(new Uint8Array(arrowBuffer), {name: ROOT_TABLE}),
    'Arrow 数据表创建', 12000);
  localTable = true;
  return bounded(attachViewer(container, worker, table), 'Perspective 工作区就绪', 15000);
}

export async function mountPerspectiveRemote(container, url, options = {}) {
  await disposePerspective();
  await initialize();
  // Official Perspective Python DuckDB Virtual Server protocol over WebSocket.
  socket = await bounded(perspective.websocket(url), 'Perspective WebSocket 握手', 10000);
  try {
    // Perspective's DuckDB Virtual Server advertises catalog-qualified table IDs;
    // the local streaming Server keeps unqualified names. Discover the real ID
    // rather than guessing "memory." or changing the immutable snapshot.
    const opened = await openHostedTable(socket, options.tableName || ROOT_TABLE, bounded);
    table = opened.table;
    localTable = false;
    return await bounded(attachViewer(container, socket, table, options.onCell),
      '远程 Perspective 工作区就绪', 15000);
  } catch (error) {
    await disposePerspective(); // Also release sockets on viewer.restore() failures.
    throw error;
  }
}

export async function getActiveTableName() {
  if (!table) throw new Error('Perspective 数据表尚未加载');
  return await table.get_name();
}

export async function setPerspectivePreset(preset) {
  if (!viewer || !table) throw new Error('请先加载数据');
  const schema = await bounded(table.schema(), 'Perspective 字段读取', 10000);
  const name = await bounded(table.get_name(), 'Perspective 数据表名称', 8000);
  if (preset === 'dashboard') {
    // In Perspective v5 every viewer is a workspace. Restoring to named panels
    // creates them via the official upsert API. Let its native layout UI place
    // and resize panels; v4 split-layout tokens are no longer compatible.
    const chart = presetConfig('bar', schema, name);
    const grid = gridConfig(schema, name);
    const snapshot = await bounded(viewer.saveWorkspace(), '读取仪表盘布局', 6000);
    const existing = Object.keys(snapshot.panels || {})[0];
    if (existing) await bounded(viewer.restore(chart, {panel: existing}), '仪表盘图表', 12000);
    else await bounded(viewer.restore(chart), '仪表盘图表', 12000);
    await bounded(viewer.restore(grid, {panel: 'tp-grid'}), '仪表盘明细', 12000);
    return viewer;
  }
  return bounded(viewer.restore(presetConfig(preset, schema, name)),
    'Perspective 原生视图切换', 12000);
}

export async function savePerspectiveState() {
  if (!viewer) throw new Error('尚未打开 Perspective 工作区');
  return bounded(viewer.saveWorkspace(), '保存 Perspective 工作区', 6000);
}

export async function restorePerspectiveState(workspace, previousRootName = ROOT_TABLE) {
  if (!viewer || !table) throw new Error('尚未打开 Perspective 工作区');
  const actual = await getActiveTableName();
  return bounded(viewer.restoreWorkspace(rebindWorkspaceBase(workspace, previousRootName, actual)),
    '恢复 Perspective 工作区', 12000);
}

export async function disposePerspective() {
  // Retire references immediately: an in-flight UI action must never reuse a
  // session that is about to be closed. Remote WebSocket is closed FIRST, so
  // viewer.delete() cannot send late schema requests to a deleted Rust view.
  const previous = {viewer, table, socket, localTable};
  viewer = null;
  table = null;
  socket = null;
  localTable = false;
  await releasePerspectiveResources(previous, bounded);
}
