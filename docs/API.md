# v0.39.5 模型评价与结果诊断接口

在现有 `GET/POST /api/v1/projects/{pid}/model-tasks/{task_id}/execution` 上，新增 `GET .../execution/diagnostics?model=forest&dimension=month&value=2024-03`：返回审批/来源/结果 SHA 核验后的当前验证分折指标、回归误差或混淆矩阵、验证集分组切片与最佳模型的末折置换贡献。参数限定 `model=baseline|linear|forest` 和 `dimension=month|device`。`GET .../execution/report` 同步增加逐 X 时间账本、预算、逐折明细、切片、修订/数据签名及不可验证边界。无可信折级产物的旧时序执行器返回 `legacy_aggregate_only`；不得补造折数据。

使用细节及验收记录见 [v0.39.4—v0.39.5 说明](V0394_V0395_TIME_AUDIT_AND_MODEL_WORKBENCH.md)。

---

# TimePilot v0.37.7 当前 API 概要

v0.37.5–v0.37.7 新增 `GET /feature-lab/data-analysis/chart-capabilities`、支持 Pearson/Spearman 的数据概览以及统一类别统计；非法图表参数返回 422，运行异常返回带 `TP-` 诊断编号的 500。

v0.37.1 增加仅本地开发模式可访问的 `GET /api/v1/native-bi/ws-status?tp_trace=...`，返回连接数和本追踪编号的 WS 关闭阶段与代码；企业模式下返回 403。原始与特征表共享同一来源资产的连接；详见 `FEATURELAB_V0371_BI_REPAIR_AND_QA.md`。

v0.37.0 的工业数据来源/时点字段契约和真实数据验收通过 `scripts/featurelab370_real_data.py` 提供命令行接口，当前**未新增**工业验收专用 HTTP API。标准化 CSV 可使用现有项目数据导入 API，禁止对未核实的标签回执时间自动补全。具体见 `docs/FEATURELAB_V0370_INDUSTRIAL_ACCEPTANCE.md`。

主入口 `/api/v1/projects`，分析 `/api/v1/projects/{pid}/feature-lab`；新增加 `GET /api/v1/system/logs` 和 `/download?format=jsonl|txt` 只读日志接口，本地模式允许访问，企业模式仅租户管理员可访问；查询/导出有限流与日志脱敏。完整签名以运行时 `/docs` 为准。

## v0.33.2 / v0.33.1 特征工程实测图表接口

`GET /api/v1/projects/{pid}/feature-lab/analysis/sessions/{sid}/candidates/{cid}/chart?mode=distribution|series|category|group|auto`

返回来自实际 DuckDB 特征计算视图的聚合点位。`mode=auto` 依据候选和已确认字段自动选择。响应含 `source_sha256`、`version_id`、`contract_revision`、`candidate_fingerprint`、`quality`、`points` 和采样范围；旧版本、来源失效及不适用的字段组合会被阻断或返回 `not_applicable`。新算子 `category.is_value` 仅接受已确认的普通类别字段和标量类别值。正式使用请按 `/docs` OpenAPI 的实际定义调用。

---

## v0.32.4 旧配方审计 API

- `GET /api/v1/projects/{pid}/feature-lab/sets/temporal-audits?asset_id={aid}`：当前项目数据资产的历史窗口只读审计汇总。
- `GET /api/v1/projects/{pid}/feature-lab/sets/{sid}/temporal-audit`：单份特征集的风险证据，`risk=high` 时禁止正式发布。
- 自动会话响应新增只读 `next_action`，按实际状态提示复核、物化、评价等后续操作。

---


# TimePilot v0.33.3｜FeatureLab API 概要（保留原会话和交付接口）

以服务启动后的 `/docs` OpenAPI 为准。接口以项目 ID 做隔离，实际读写权限由当前本地/企业安全模式决定；历史 v0.9.0 文档关于“完全无认证”的说明**不适用于所有当前部署模式**，不得据此开放公网服务。

接口前缀：`/api/v1/projects/{pid}/feature-lab`。

| 方法 | 端点后缀 | 作用/约束 |
|---|---|---|
| POST | `/analysis/sessions` | 创建自动分析，输入 `asset_id`、目标、预算、请求 ID；固定受控工具流程 |
| GET / POST | `/analysis/sessions/{sid}` / `/analysis/sessions/{sid}/advance` | GET reads persisted state; POST is legacy compatibility. Embedded worker now continues stages independently. |
| POST | `/analysis/sessions/{sid}/retry` | Explicit owner/editor-authorized retry: `{ "expected_revision": integer }`; failed jobs only; stale sources and corrupted successes are blocked. |
| PUT | `/analysis/sessions/{sid}/selection` | 审核候选选择；乐观锁 `expected_revision` |
| GET | `/analysis/sessions/{sid}/agent` | 有界模型规划、工具回执与证据解释；不包含原始密钥 |
| GET | `/analysis/sessions/{sid}/supervision-options` | 已确认标签、预测时点、独立标签可用时间、静态任务可用性 `static_eligible` 与分组切分建议 |
| POST | `/analysis/sessions/{sid}/save-set` | 明确保存 FeatureSet；可传 `supervision` 结构但必须 `confirm=true` |
| POST | `/analysis/sessions/{sid}/accept-and-run` | 人工确认后保存 FeatureSet 草稿并排队物化；重试幂等，禁止模型自动触发 |
| POST | `/analysis/sessions/{sid}/materialize` | 人工创建正式物化任务；真实产物校验后自动提交评价 |
| GET | `/analysis/sessions/{sid}/delivery` | 来源、配方、物化 Parquet、模型评价、BI 同源状态及 SHA256 |
| POST | `/analysis/sessions/{sid}/viewer-receipt` | 仅保存客户端双面板回执；不替代独立浏览器验收 |
| GET | `/analysis/sessions/{sid}/report` | 含 Markdown、UTF-8 字节 SHA256、同源交付清单的报告 |

**新增 P0 顺序门禁：**仅已确认的 `order_index` 或真实事件时间可用于自动时序窗口；多设备按已确认实体键分组，真实数据排序键缺失/重复将拒绝编译执行；独立样本使用逐行数值算子。

**字段契约：**静态 K 折需有已确认数值标签以及用户的 `static_independence_confirmed=true` 明示确认；同一设备/批次多条记录必须使用 `split_kind=group` 且明确全部实体键。时间预测继续要求已确认设备键、事件时间、过程数据 `available_at`、预测时点、标签及独立标签 `label_available_at`；禁止把整数记录序号 `Time` 冒充真实日历时间。

**v0.33.2 评价协议：** `TaskSpecV2.split_kind` 支持 `time / grouped_time / kfold / stratified / group`。静态配方禁止所有窗口算子、标签/编号/时间输入与未确认的输入字段。`EvaluationProtocol.search.enabled` 暂只在非分组时间任务允许；静态和分组时间搜索必须待实现折内约束验证后才能开启。完整结果增加 `split_manifest_sha256`、`paired_comparison`（仅同折差值）、`selection_stability`（仅训练折集合）和按真实可用标签过滤后的 `train_rows_sha256`。

**安全边界：**模型只能建议目标、字段、模板、合格候选排序和已存在证据 ID，不能请求执行任意 SQL/Python、跨项目读取、自动保存或发布；失败/无 API 时使用规则引擎。`delivery.production_verified` 在源码工作流内始终为 `false`，必须通过独立现场发布门禁。


---

## 历史版本说明（保留供追溯；操作请以上文为准）

# TimePilot REST API v0.9.0（包含历史接口）

API 包含基础模型任务及 BI/AI 能力。启动服务后浏览器打开 `http://127.0.0.1:8000/docs` 查看 Swagger/OpenAPI。所有示例均为本地运行；当前 API 尚未认证，严禁映射公网。

| 方法 | 路径 | 功能 |
|---|---|---|
| GET | `/api/v1/health` | 运行状态 |
| GET | `/api/v1/plugins` | 可用模型插件及能力 |
| GET | `/api/v1/datasets` | 列出数据快照 |
| POST | `/api/v1/datasets/upload` | multipart 上传、字段映射、数据标准化 |
| GET | `/api/v1/datasets/{dataset_id}/preview` | 标准化前 20 行 |
| POST | `/api/v1/tasks/validate` | 独立检查任务与数据是否匹配 |
| POST | `/api/v1/runs` | 创建模型训练任务 |
| GET | `/api/v1/runs` | 最近运行列表 |
| GET | `/api/v1/runs/{run_id}` | 进度、错误、当前阶段 |
| GET | `/api/v1/runs/{run_id}/events` | 结构化运行日志 |
| GET | `/api/v1/runs/{run_id}/quality` | 独立测试质量报告 |
| GET | `/api/v1/runs/{run_id}/forecasts` | 验证/未来预测对象 |
| GET | `/api/v1/runs/{run_id}/forecasts.csv` | 下载未来预测 CSV |
| GET | `/api/v1/runs/{run_id}/predict/{series_id}` | 重新加载保存模型进行预测 |
| POST | `/api/v1/runs/{run_id}/cancel` | 请求取消（模型内部训练不保证立即中断） |
| POST | `/api/v1/runs/{run_id}/retry` | 仅失败任务可重新排队 |
| POST | `/api/v1/demo/bootstrap` | 创建合成月度时序示例并入队 |

## 导入数据

```bash
curl -X POST http://127.0.0.1:8000/api/v1/datasets/upload \
  -F file=@sales.csv -F name=sales_monthly -F frequency=MS -F unit=vehicles
```

上传 CSV 默认必须包含 `series_id,timestamp,target,available_at`。也支持 `timestamp_col`、`target_col`、`entity_col`、`available_at_col` 这几项字段映射参数。缺少发布时间时，可以声明 `publication_lag_days`，但会产生明显警告。

## 创建并查询任务

```json
{
  "dataset": "sales_monthly",
  "task": {
    "task_id": "TP-001",
    "name": "月度销量预测",
    "task_family": "demand_forecast",
    "frequency": "MS",
    "forecast_horizon": 1,
    "model_candidates": ["seasonal_naive", "moving_average"],
    "evaluation": {"validation_windows": 4, "test_horizon": 1, "min_history": 12},
    "approved": true
  }
}
```

先提交 `/api/v1/tasks/validate`，验证通过后提交 `/api/v1/runs`。Worker 自动拾取队列。正式版本仍需添加认证、授权、限流、租户隔离、审计事件和用户审批。

## BI 与模型设置 API

新增完整接口清单及示例请查看 [BI_AND_AGENT.md](BI_AND_AGENT.md)，包括 `/api/v1/bi/tables`、`/api/v1/bi/reports`、`/api/v1/bi/tools`、`/api/v1/settings/llm`、`/api/v1/settings/general`、`/api/v1/assistant/bi/plan` 及 `/api/v1/assistant/task/draft`。写入新数据快照及创建报表等操作在工具执行端需要显式确认。


## v0.3.0 表格预览与服务商目录

- `GET /api/v1/bi/tables/{table_id}/preview`：支持 `page`, `page_size`, `search`, `sort_by`, `sort_dir`, `filter_column`, `filter_operator`, `filter_value`，返回预览行、原始行索引、总行数及页码。
- `POST /api/v1/bi/tables/{table_id}/transform/preview`：请求 `{"name": "...", "steps": [...]}`；只返回处理前后概要和样例，不创建数据快照。
- `POST /api/v1/bi/tables/{table_id}/transform`：经确认后执行同一配方，生成新的不可变数据快照。
- `GET /api/v1/settings/llm/providers`：可公开的服务商、协议、接口地址和模型 ID 示例，绝不包括任何 API 密钥。

页面路由新增 `/bi/table/{table_id}/fields`、`/prepare`、`/reports`、`/ai`、`/forecast`，主入口 `/bi/table/{table_id}` 默认显示完整表格式数据。

## v0.4.0 新增 API（研发版）

| 方法 | 路由 | 作用 |
| --- | --- | --- |
| `POST` | `/api/v1/bi/tables/{id}/transform/preview` | 预演白名单数据处理操作（含计算列、批量编辑、文本清洗） |
| `POST` | `/api/v1/bi/tables/{id}/transform` | 生成一个新的不可变数据快照 |
| `POST` | `/api/v1/bi/tables/{id}/pivot` | 运行受控透视交叉统计 |
| `POST` | `/api/v1/assistant/bi/workflow` | 基于字段元数据返回多步骤 BI 工具草稿，默认不执行 |
| `POST` | `/api/v1/bi/tables/{id}/workflow/preview` | 逐步预演最多 6 项白名单操作，返回限时预览凭证 |
| `POST` | `/api/v1/bi/tables/{id}/workflow/execute?confirmed=true&preview_token=TOKEN` | 凭有效预览凭证一次性创建最终数据快照 |
| `GET` | `/api/v1/bi/tables/{id}/export.xlsx` | 导出数据表为 Excel |
| `GET` | `/api/v1/bi/reports/{id}/export.xlsx` | 导出已保存分析报表为 Excel |

JSON 结构和边界见 历史 BI 工作流说明（原 `BI_WORKFLOW_V040.md` 未纳入当前发行包；请以 `/docs` 为准）。完整自动生成接口文档在 `/docs`。所有本地管理和写入接口暂未建设正式权限体系，请仅在可信研发环境使用。

## v0.5.0 原生 BI（DuckDB / Parquet）

新 BI 与旧 `/api/v1/bi` 并存。所有端点仅限可信本地研发环境；无生产级访问控制。完整接口契约、样例及规模限制见 [NATIVE_BI.md](NATIVE_BI.md)。

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/v1/native-bi/capabilities` | DuckDB 和 Perspective 构建状态 |
| GET | `/api/v1/native-bi/assets` | Parquet 数据资产目录 |
| POST | `/api/v1/native-bi/assets/upload` | 文件导入，限制浏览器上传大小 |
| POST | `/api/v1/native-bi/assets/from-legacy/{table_id}` | 校验旧 CSV 后迁移，不覆盖旧资产 |
| GET | `/api/v1/native-bi/assets/{asset_id}` | 数据快照及字段元数据 |
| GET | `/api/v1/native-bi/assets/{asset_id}/verify` | SHA256 完整性检查 |
| GET | `/api/v1/native-bi/assets/{asset_id}/profile` | 最多抽样 10,000 行字段画像 |
| POST | `/api/v1/native-bi/query` | QuerySpec 参数绑定、服务端筛选/聚合/分页 |
| GET | `/api/v1/native-bi/assets/{asset_id}/sample.arrow` | Perspective 限量 Arrow 样本 |
| POST | `/api/v1/native-bi/views` | 保存视图配置 |
| GET | `/api/v1/native-bi/views/{view_id}` | 读取已保存视图 |
| POST | `/api/v1/native-bi/assistant/query-draft` | LLM 生成只读查询草案 |
| POST | `/api/v1/native-bi/assets/{asset_id}/to-timeseries` | BI 资产转标准预测任务数据集，最大 20 万行 |

查询示例（将 `<UUID>` 替换为真实资产编号）：

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/native-bi/query \
  -H "Content-Type: application/json" \
  -d '{"asset_id":"<UUID>","group_by":["区域"],"metrics":[{"operation":"sum","column":"销售额","alias":"revenue"}],"limit":100}'
```

`QuerySpec` 只允许已注册字段和列出的操作，不接收用户 SQL。返回内容为明确标记的当前页或汇总结果，**不代表所有数据已进入浏览器**。

## Native BI v0.5.1/v0.5.2 新增 API

所有接口前缀为 `/api/v1/native-bi`。输入输出 Schema 以 `src/timepilot/native_bi.py`、`native_bi_edit.py` 为准。

| 方法 | 路由 | 功能 |
|---|---|---|
| POST | `/query.arrow` | 使用受控 QuerySpec 对完整数据执行 DuckDB 查询后输出最多 1,000 行 Arrow IPC |
| GET | `/assets/{asset_id}/sample.arrow?version_id=base&limit=5000` | 对指定历史版本输出至多 10,000 行样本 |
| POST | `/views` | 保存 `{asset_id, name, config}`，config 可包含 Perspective workspace 和版本来源 |
| GET | `/assets/{asset_id}/views` | 查询该资产的保存视图 |
| GET | `/views/{view_id}` | 读取保存的视图配置 |
| GET | `/assets/{asset_id}/versions` | 读取当前头指针及历史修订 |
| GET | `/assets/{asset_id}/versions/verify` | 验证基表及增量文件的 SHA256 |
| POST | `/edits/preview` | 校验 `EditBatch`、返回差异预览和 10 分钟单次令牌 |
| POST | `/edits/commit` | `EditCommit`：提交与令牌严格绑定的批次，生成稀疏修改版本 |
| POST | `/assets/{asset_id}/undo` | `UndoRequest`：用户确认后创建新撤销修订 |
| POST | `/assets/{asset_id}/rollback` | `RollbackRequest`：用户确认后创建新回滚修订 |

样例 `EditBatch`：

```json
{
  "asset_id": "<UUID>",
  "expected_version": "base",
  "comment": "修订销售额",
  "edits": [
    {"row_id": 10, "column": "销售额", "value": "1000.00"},
    {"row_id": 12, "column": "备注", "value": null}
  ]
}
```

先调用 `/edits/preview` 获得 `preview_token`，再以 `{ "batch": EditBatch, "preview_token": "..." }` 调用 `/edits/commit`。每次最多 5,000 个单元格，令牌过期、重复提交、头版本改变或字段值无法转换均被拒绝。原始 Parquet 不覆盖。真实 DuckDB/Arrow 集成验收说明见 `docs/ACCEPTANCE.md`。


## v0.6.1 智能数据接收与可追溯转换

基础 URL：`/api/v1/smart-import`，仅限本机访问。

| 请求 | 地址 | 用途 |
|---|---|---|
| POST multipart(file) | `/uploads` | ZIP/XLSX/CSV/Parquet 单文件上传 |
| POST multipart(files[]) | `/uploads/batch` | 多文件直接上传、自动合并为安全内部归档 |
| GET | `/imports` | 上传记录 |
| GET | `/imports/{import_id}` | 原始文件、工作表、候选区域、推荐分组 |
| GET | `/imports/{import_id}/regions/{candidate_id}/preview` | 指定数据区域样例 |
| POST JSON | `/imports/{import_id}/transform-jobs` | 开始后台结构转换，立即返回 job_id |
| GET | `/imports/{import_id}/transform-jobs/{job_id}` | 查询进度、状态、结果版本与错误信息 |
| POST JSON | `/imports/{import_id}/transform` | 同步转换，兼容脚本/小样本调用 |
| GET | `/imports/{import_id}/versions` | 按配方不可变版本 |
| GET | `/imports/{import_id}/versions/{version_id}/preview` | 样本、来源、质量标记；支持 scopes/sources/series_id 参数 |
| GET | `/imports/{import_id}/versions/{version_id}/pivot` | streaming 基础聚合；支持 group/scopes/sources/series_id/limit |
| GET | `/imports/{import_id}/versions/{version_id}/export.csv` | 完整规范化 CSV |
| POST | `/imports/{import_id}/versions/{version_id}/publish-bi` | 发布为 DuckDB 原生 BI 资产（需相应依赖） |

示例转换请求（来自上传结果推荐的 candidate_ids）：

```json
{"candidate_ids":["<candidate_id>"],"name":"年度车型明细","duplicate_policy":"keep_and_flag"}
```

返回任务状态为 `queued/running/completed/failed`；转换任务可通过 `completed_candidates/total_candidates/output_rows` 查询进度。无明确数据发布时间时 `available_at` 为空，不自动伪造。


## v0.7.0 · 智能数据 BI 与预测任务

完整字段、十个接口和发布前验收步骤见 [WORKBENCH_V070.md](WORKBENCH_V070.md)。数据导入后打开 `/imports/{import_id}/versions/{version_id}/workbench`，依次筛选、保存视图、生成预测草稿，完成预检后再创建标准数据集。


## v0.9.0 统一数据资产

主要端点：`GET /api/v1/data-assets`，`GET /api/v1/data-assets/{asset_id}`，`GET /api/v1/data-assets/{asset_id}/versions/{version_id}`；`POST .../analyze`、`.../grid`、`.../group`、`.../pivot` 共享 `ViewSelection`。`GET|POST .../views` 直接使用时序工作台的视图记录和 `view_id`。字段角色版本通过 `GET|POST .../semantics` 维护，用户确认后保存来源 SHA-256。

`POST .../legacy-bi-link` 将筛选后的不可变源数据版本接入原通用 BI，无需二次上传；最多 250,000 行，拒绝隐式采样。

`WS /api/v1/native-bi/linked/{asset_id}/versions/{version_id}/ws` 是同源的、只读 Perspective 关联连接，需预编译前端和 DuckDB/Perspective 环境。不能从此入口修改原始转换数据。

预测入口：`POST .../views/{view_id}/forecast-drafts` 生成草稿，训练数据准备前检查实体身份、重复月份、来源/口径及发布时间，用户需最终确认。

## v0.14.0：项目异步上传与字段画像

- `POST /api/v1/projects/{pid}/imports/jobs` — Multipart 文件，返回 202 `job_id,status,files`。
- `GET /api/v1/projects/{pid}/imports/jobs/{job_id}` — 查询持久化进度，扫描成功后 `import_id=job_id`。
- `POST .../cancel` — 只允许取消待执行的上传；`POST .../retry` — 对失败状态有限重试。
- `GET /api/v1/projects/{pid}/imports/{iid}/regions/{candidate_id}/profile?max_rows=100000` — 精确扫描界限、字段画像、来源哈希。
- `GET /api/v1/projects/{pid}/imports/{iid}/plans` — 列出可恢复的语义确认方案。

以上端点均需现有项目关联；完整数据表和可视化仍需访问正式 BI。模型列角色建议只在人工批准后用于确定性转换。


## FeatureLab v0.32.0 automatic analysis

Root: `/api/v1/projects/{project_id}/feature-lab`. All routes use existing project ACLs and actor scope.

- `GET /time-basis/infer?asset_id=&version_id=base` suggests calendar/index/elapsed roles, with evidence and confidence.
- `POST /time-basis/accept` confirms a numerical `order_index` or `elapsed_time` with contract revision precondition; no direct datetime casting.
- `GET /analysis/latest?asset_id=&version_id=base` returns the last actor-owned analysis.
- `POST /analysis/sessions` creates an idempotent analysis using `asset_id, version_id, goal, task_hint, budget, request_id`.
- `GET /analysis/sessions/{id}` loads durable, source-fingerprint-pinned state.
- `POST /analysis/sessions/{id}/advance` is compatibility-only; the embedded server worker now automatically advances completed jobs. Poll read-only `GET` for progress.
- `PUT /analysis/sessions/{id}/selection` requires expected revision and verified candidate IDs.
- `POST /analysis/sessions/{id}/save-set` saves only the current reviewed selection into a new FeatureSet draft.
- `POST /analysis/sessions/{id}/materialize` starts the existing full materialization job after draft creation.
- `GET /analysis/sessions/{id}/report` returns source-pinned Markdown quality findings.
- `POST /analysis/sessions/{id}/explain` returns rule evidence by default, or optional aggregate-only model explanation.

No `analysis` endpoint auto-approves publication or claims supervised predictive improvement without verified labels and PIT task contracts.


### v0.32.1 自动特征 Agent 证据接口

`GET /api/v1/projects/{pid}/feature-lab/analysis/sessions/{sid}/agent`
读取当前操作人、项目范围内的智能规划、固定工具序列和安全回执。
响应包含 `source_sha256`、`contract_revision`、`stale`、`status`、`agent.plan`、`agent.trace`、
`agent.candidate_adjustment`、`explanation`；不包含原始数据行、模型 API 凭证及完整提示词。
沿用原 `/analysis/sessions` 创建、推进、筛选、保存与物化接口；这些写操作仍由用户显式触发。
模型建议仅能控制经契约校验的任务目标、允许字段和算子模板，并且不能指向越权工具。


### v0.37.2 多维数据仪表盘

`POST /api/v1/projects/{pid}/feature-lab/data-analysis/dashboard` 与 `/data-analysis/dashboard/chart`。字段 ID、版本、源 SHA256、契约修订和参数化筛选均必需按实际权限校验。请求示例与数值定义见 `FEATURELAB_V0372_DASHBOARD_QA.md`。


### v0.37.3 服务端证据与导出

`POST /api/v1/projects/{pid}/feature-lab/data-analysis/dashboard` 的返回增加 `evidence_analysis`（每个数字绑定范围、来源、证据 ID；无证据为 `insufficient_evidence`）。

`POST /api/v1/projects/{pid}/feature-lab/data-analysis/dashboard/report` 与上述 API 使用完全相同的 `DashboardQuery`，服务端对当前来源重新统计并只读返回 `markdown`、`report_sha256`、`source_sha256`、`scope_sha256` 和 `claim_ids`。不会接受客户端自行构造的统计结论，若来源失效或证据冲突则拒绝导出。

`GET .../analysis/sessions/{sid}/candidates/{cid}/chart` 的 `explanation.evidence_analysis` 含分布、最大桶内极差、可比量纲的原始字段与派生特征桶均值比较、分组比较。

`GET .../workspace/insights` 的 `model.variant_deltas` 仅在标签、同源产物及同折验证均可信时出现；保留负改善，不等同最终测试集或生产收益。
