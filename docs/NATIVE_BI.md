# 原生 BI 架构与数据版本

## 计算及交互

Python 3.12.12 + FastAPI 承载 HTTP 和同源 WebSocket。历史数据使用 Parquet 快照、DuckDB 查询与 FINOS Perspective `DuckDBVirtualServer`。浏览器通过 `<perspective-viewer>` 调用 Datagrid、透视和图表；服务端执行全量筛选、分组与聚合。有限 Arrow 样本仅用于浏览器降级预览，页面必须标注样本范围。

实时数据由独立 Perspective Python Server 托管，借助 `Table.update()` 推送增量。此通道属于进程内有界监测流，不能代替企业持久化消息队列。

## 资产版本

原始 Parquet 作为只读快照保留。单元格修改写入独立补丁，并创建数据修订；查询时从基础快照叠加生效修订。撤销和回滚创建新历史版本。视图保存必须记录资产 ID、数据版本、QuerySpec/来源模式和完整 Perspective Workspace，打开历史视图不得自动漂移到最新数据。

## 浏览器运行与构建

前端静态产物包含 `perspective-entry.js`、`perspective-entry.css`、独立 Perspective WASM 及 SHA256 构建清单。它们由发布构建一次，FastAPI 后续直接分发；终端用户启动时不调用 npm。构建失败保留前一套通过校验的资源，未通过清单检查时拒绝标记为发布就绪。

## 大数据资源边界

DuckDB 查询通过受控查询模型生成。WebSocket 限制同源、活动会话数及存活期限；每条查询尝试通过连接中断设置执行上限。这些机制尚无法代替多租户 RBAC、独立进程资源隔离或企业生产级查询治理。

## Perspective 5.x 的表与工作区绑定

前端使用客户端引用（`perspective.worker()` 或 `perspective.websocket()`）承载数据表，表通过 `Table.get_name()` 固定名称。浏览器执行 `viewer.load(client)` 后，必须 `viewer.restore({table: tableName, plugin: 'Datagrid', ...})`。切换透视、图表时，同样传入 `table` 字段；保存多面板布局使用 `saveWorkspace()` 和 `restoreWorkspace()`。

构建清单包含前端源码 SHA256。源码变化后开发验收会自动重建，普通双击启动不需要 Node.js。

## v0.5.8 ESM 发布契约

普通 Vite 应用构建默认不保留入口模块导出。为保证 TimePilot 的静态页面可使用原生 BI，采用 `preserveEntrySignatures: 'strict'`，在 Rollup `generateBundle()` 期校验七个对外 API，并输出 `perspective-contract.json`。Python SHA256 发布清单将这个合同也作为必需资源；浏览器独立导入编译 JS，再按 SHA256 校验后加载。任何合同错误均拒绝发布。首次更新至 v0.5.8 需要重新构建前端。


## Perspective 5.6.1 与 DuckDB VIEW 的自然行序

`DuckDBVirtualServer` 原始处理器默认创建 `GenericSQLVirtualServerModel({})`，其自然行标识为 `rowid`，仅在 DuckDB 物理表有效。TimePilot 的 `timepilot_active` 是基于 Parquet 和版本编辑的只读 VIEW。平台每个文件行已有永久保存的 `__tp_rowid`。`create_timepilot_virtual_server()` 继承上游协议服务端/会话/处理器，为每个会话设置 `row_id_expr='"__tp_rowid"'`。透视、图表、窗口函数及缺省排序都继续由 DuckDB 执行 SQL，既能使用稳定行序，也无需物化复制底层数据。
