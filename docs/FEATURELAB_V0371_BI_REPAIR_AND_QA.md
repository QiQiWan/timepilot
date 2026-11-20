# TimePilot v0.37.1 · Perspective、正式特征 BI 与真实 Chrome 往返验收

## 修复范围与原因

v0.37.0 在同一个页面中使用 `/assets/native-bi/perspective-entry.js` 以及带不同 `?v=` 的 ESM URL，多次执行同一构建会触发 `customElements.define('regular-layout', ...)` 重复注册。另有 `/api/v1/api/v1/projects/...` 路径重复、正式特征链接失去物化产物 ID、WebSocket 只报告 `Generic Websocket Error`、回退透视把序号 `Time` 同时当作分组/汇总指标等问题。

1. **单一加载源**：`src/timepilot/static/perspective-loader.js` 将入口固定为唯一 ESM URL，校验 `perspective-build.json` 内构建文件 SHA256，共享加载 Promise。首页四个 BI 入口均调用同一加载器。若活动页面中检测到新的构建 SHA256，要求整个页面刷新，拒绝重新执行自定义元素注册代码。
2. **旧连接顺序释放**：`frontend/native-bi/src/main.js` 对路由切换引发的 WebSocket 关闭建立串行等待链，避免后续 Viewer 在旧会话尚未释放时重复占用连接槽位。
3. **WebSocket 可追踪**：`frontend/native-bi/src/connection-diagnostics.mjs` 生成独立 `tp_trace`。`src/timepilot/native_bi_api.py` 记录握手拒绝、会话容量、版本/权限、特征文件校验、协议异常和断开时的阶段及关闭码。仅本地开发模式开放 `/api/v1/native-bi/ws-status?tp_trace=...`，保留 180 秒、最多 128 条且不包含 SQL、数据内容或任意请求参数。
4. **正式特征双面板**：前端 `feature-shell.js` 生成 `asset / version / artifact / FeatureSet / analysis session` 完整链接。`feature-perspective-bridge.js` 需要源表与 `timepilot_feature_artifact` 双面板绑定的实际 Viewer 回执，未就绪就保留失败原因。后端从受权注册记录解析 Parquet，校验物化状态及来源；不信任客户端传递的文件路径。`main.js` 检查两个实际表绑定和非系统列；错误时保留已加载的源表。
5. **路由契约**：`bi-conversation.js` 的 `ctx.req()` 统一使用 `/projects/...`，公共请求层只添加一次 `/api/v1`，并拒绝双重前缀。
6. **基本 BI 回退**：数值字段选择跳过常见时间/序号/ID 列，缺少数值字段时允许按组计数；正负汇总不再把负值钳成 0；汇总与源表保持固定数据版本。

## 已执行的代码验收与限制

- 全部前端 Node 用例应使用 `cd frontend/native-bi && npm test` 重跑；v0.37.1 新增测试检查单一 ESM Promise、SHA256 切换拒绝及诊断错误。
- Python 新增 `tests/test_v0371_bi_repair.py`，检查 WebSocket 跨域拒绝、返回的阶段/关闭码、双面板源码契约与正式特征链接的完整 ID。
- 源码模式不能证明 Perspective JS/WASM 与 DuckDB 的物理连接。正式验收必须安装 Google Chrome，并构建与同一源包匹配的 Perspective 前端；不得通过 Chromium 或 DOM 模拟替代。
- 此处的完整执行结果应以附带 QA 报告和日志为准，缺少相应依赖时记录为 BLOCKED。

## 30 次真实浏览器往返的独立门禁

```bash
python scripts/featurelab371_chrome_cycles.py \
  --out reports/v0371/chrome_30_cycles.json --timeout 240
```

该脚本复用隔离的固定合成数据全用户工作流，在创建正式物化产物后，以**同一个 Google Chrome 标签页、同一 CustomElementRegistry** 的 SPA 导航从原生源表进入正式特征页面，再返回原生源表，往返 30 次，共计 60 次路径切换。每次均校验实际 Viewer 布局；特征页面要求两张不同表（源表+特征结果）的 schema 和绑定均有效。额外检查 `regular-layout` 的原始构造器身份、浏览器 PageError、WebSocket 建立、刷新恢复、源数据与特征产物 SHA256，以及报告归档回执。

使用 `TIMEPILOT_CHROME_PATH` 指向**已安装的 Google Chrome**。设置 `TIMEPILOT_QA_SCREENSHOTS=1` 保存页面截图。每次切换的步骤回执写入同一 JSON；即使失败也保留失败阶段、异常摘要和此前通过的步骤。只有 `status=passed` 且 `completed_cycles=30`、`steps` 数组共 60 个有效路径检查、`no_duplicate_custom_elements=true`，才算通过。CI `native-bi-real-browser` 为该门禁设置阻断条件。

## 复现和恢复注意

- 从之前版本升级，应先停止服务，备份 `TIMEPILOT_HOME`，用完整源码包覆盖同名代码，重建原生 Perspective JS/WASM，然后**完整刷新 Chrome 页面**。仅刷新部分 DOM 无法清除浏览器已注册的 CustomElement。
- 当发生错误，先在系统日志中按 `tp_trace` 过滤，并请求 `/api/v1/native-bi/ws-status?tp_trace=...` 检查是否被 Origin、连接槽位、版本或特征产物拒绝。状态接口在企业模式返回 403。
- 当正式特征失败但源表正常，先核对特征集已正式物化、`artifact_id` 有对应记录、文件 SHA256 与生成时一致、查询 URL 携带项目 ID，随后重试特征面板。
- 代码审阅已修复确认的导入路径和 API 契约；物理 WebSocket 故障的最终闭环仍须以上述真实浏览器证据为准。无数据就绪和浏览器执行证据时不能认定 30 次验收通过。

## 后续拆分

v0.37.2 建立独立的多图数据画像仪表盘，v0.37.3 细化基于统计与监督评价证据的自然语言解释。这两项不计入 v0.37.1 的发布通过条件。
