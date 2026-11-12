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
import {gridConfig, presetConfig, loadNamedClient, analyticalChartConfig} from './viewer-contract.mjs';
import {openHostedTable} from './hosted-table.mjs';
import {releasePerspectiveResources} from './session-lifecycle.mjs';
import {compileAgentViewerState,ensureSameTable,mergeAgentPanelConfig} from './workspace-bridge.mjs';
import {observeViewer} from './viewer-events.mjs';
import {createSeriesPreserver} from './series-preservation.mjs';

let initialized, worker, viewer, table, socket, localTable = false, resizeObserver;
let changeListener=null,stopObserving=null,seriesKeeper=null;

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
  elem.style.cssText = 'display:block;width:100%;height:100%;min-height:0';
  container.appendChild(elem);
  if (typeof ResizeObserver !== 'undefined') {
    resizeObserver = new ResizeObserver(() => {
      if (typeof elem.notifyResize === 'function') Promise.resolve(elem.notifyResize()).catch(() => {});
    });
    resizeObserver.observe(container);
  }
  if(stopObserving){stopObserving();stopObserving=null;}
  if(changeListener)stopObserving=observeViewer(elem,changeListener);
  viewer = elem; // Retain it early so a failed mount is also correctly disposed.
  // Perspective v5: load the hosting Client, then choose a named Table via restore().
  // A Table-only load may render in compatibility mode but has no stable workspace
  // root, and restore({plugin}) without a table can leave the workspace blank.
  const bound=await loadNamedClient(elem, client, tableRef, bounded);
  seriesKeeper=createSeriesPreserver(elem,bound.schema,{onNotice:message=>{
    const status=document.getElementById('tp-bi-shell-status');if(status)status.textContent=message;
  }});
  await seriesKeeper.initialize();
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

/** Mount source and an immutable feature result as two panels of ONE Viewer.
 * Both tables are hosted by the same server-side DuckDB Virtual Server.
 */
export async function mountFeatureComparison(container, url, previousWorkspace = null) {
  await mountPerspectiveRemote(container,url);
  if (previousWorkspace) {
    try { await restorePerspectiveState(previousWorkspace); }
    catch (error) {
      // An incompatible saved token must not replace the live immutable data.
      console.warn('旧图表配置无法在当前特征结果中恢复：',error);
    }
  }
  try {
    const output=await openHostedTable(socket,'timepilot_feature_artifact',bounded);
    const resultName=await output.table.get_name();
    const schema=await output.table.schema();
    const existing=await bounded(viewer.saveWorkspace(),'检查当前图表布局',6000);
    const config=gridConfig(schema,resultName);
    await bounded(viewer.restore(config,{panel:'tp-feature-result'}),'显示特征结果面板',12000);
    return {source_table:await table.get_name(),result_table:resultName,
      panel_count:Object.keys((await viewer.saveWorkspace()).panels||{}).length};
  } catch(error) {
    // Keep the original source Viewer available on any derived-panel failure.
    throw new Error('原始数据已加载，附加特征面板失败：'+error.message);
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
  const token=await bounded(viewer.save(), '读取当前数据系列',6000);
  const patch=presetConfig(preset,schema,name);
  if(token?.columns?.length&&patch.plugin!==token.plugin){
    const numeric=token.columns.filter(col=>['integer','float','number','double','decimal'].includes(String(schema[col]).toLowerCase()));
    if(numeric.length)patch.columns=numeric;
  }
  return bounded(viewer.restore(patch), 'Perspective 原生视图切换', 12000);
}

/** Apply server-validated chart panels to the currently mounted Arrow table. */
export async function configurePerspectiveDashboard(panels) {
  if (!viewer || !table) throw new Error('Perspective 数据尚未加载');
  if (!Array.isArray(panels) || !panels.length || panels.length > 8) {
    throw new Error('一个工作区需要 1–8 个有效图表');
  }
  const schema=await bounded(table.schema(), '读取分析表字段', 10000);
  const name=await bounded(table.get_name(), '查询表名', 8000);
  const configs=panels.map(panel=>analyticalChartConfig(panel,schema,name));
  for(let i=0;i<configs.length;i++){
    const options=i===0?{}:{panel:`tp-agent-${i}`};
    await bounded(viewer.restore(configs[i],options), '更新 BI 分析面板', 12000);
  }
  return viewer;
}

/** Apply an Agent presentation patch to the LIVE, fully bound source table.
 * No Arrow preview request, viewer.delete(), websocket close or table remount.
 */
export async function applyAgentWorkspaceState(state, options={}) {
  if (!viewer || !table || localTable) {
    throw new Error('请先加载完整的 Perspective 远端数据版本；有限样本不可替代全量分析');
  }
  const name=await bounded(table.get_name(),'读取当前来源表',8000);
  const schema=await bounded(table.schema(),'读取当前来源字段',8000);
  const before=await bounded(viewer.saveWorkspace(),'读取当前布局',7000);
  if(options.expectedToken && JSON.stringify(before)!==JSON.stringify(options.expectedToken))
    throw new Error('用户手动调整了 Perspective 配置，请重新确认后应用 Agent 操作');

  // dashboard already includes the main chart. Applying both the main
  // chart and the first dashboard entry would silently duplicate a panel.
  const changes=(state.dashboard?.length?state.dashboard:[state.chart]).filter(Boolean);
  const root=Object.keys(before.panels||{})[0];
  if(!root)throw new Error('当前 Perspective 工作区未创建主面板');
  let result;
  for(let i=0;i<changes.length;i++){
    const config=compileAgentViewerState({...state,chart:changes[i]},schema,name);
    const theme=state.viewer?.theme;
    if(theme==='Pro Light'||theme==='Pro Dark')viewer.setAttribute('theme',theme);
    const panel=i===0?root:`tp-agent-${i}`;
    const active=before.panels?.[panel];
    const toolNames=(options.actions||[]).map(a=>a.tool||a);
    const patch=active?mergeAgentPanelConfig(active,config,toolNames):config;
    await bounded(viewer.restore(patch,{panel}),`更新分析面板 ${panel}`,12000);
    result={panel,plugin:patch.plugin,table:name};
  }
  const after=await bounded(viewer.saveWorkspace(),'核验工作区状态',7000);
  ensureSameTable(before,after,name);
  return {status:'rendered',panel_count:Object.keys(after.panels||{}).length,
    active_table:name,receipt:result,workspace:after};
}

/** Genuine runtime plugin discovery, not optimistic hard-coded features. */
export async function inspectPerspectiveCapabilities() {
  if(!viewer || !table)throw new Error('Perspective 尚未就绪');
  const plugins=typeof viewer.getAllPlugins==='function'?await viewer.getAllPlugins():[];
  const schema=await table.schema();
  return {viewer_version:'5.6.1',schema,plugins,
    remote_full_data:!localTable,active_table:await table.get_name()};
}

export function setPerspectiveChangeListener(listener){
  if(listener!==null && typeof listener!=='function')throw new Error('无效的工作区变化监听器');
  changeListener=listener;
  if(stopObserving){stopObserving();stopObserving=null;}
  if(viewer && changeListener)stopObserving=observeViewer(viewer,changeListener);
}

export async function getPerspectiveSelectedSeries(){
  if(!viewer||!table)return {fields:[],all:[]};
  const token=await bounded(viewer.saveWorkspace(),'读取当前图表字段',6000);
  const panel=token.panels?.[token.active]||Object.values(token.panels||{})[0]||{};
  const schema=await table.schema();
  const all=Object.keys(schema).filter(x=>x!=='__tp_rowid');
  return {fields:(panel.columns||[]).filter(x=>all.includes(x)),all,plugin:panel.plugin||'Datagrid'};
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
  if (stopObserving){stopObserving();stopObserving=null;}
  if(seriesKeeper){seriesKeeper.dispose();seriesKeeper=null;}
  if (resizeObserver) {resizeObserver.disconnect();resizeObserver = null;}
  const previous = {viewer, table, socket, localTable};
  viewer = null;
  table = null;
  socket = null;
  localTable = false;
  await releasePerspectiveResources(previous, bounded);
}
