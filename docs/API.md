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

JSON 结构和边界见 [`BI_WORKFLOW_V040.md`](BI_WORKFLOW_V040.md)。完整自动生成接口文档在 `/docs`。所有本地管理和写入接口暂未建设正式权限体系，请仅在可信研发环境使用。

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
