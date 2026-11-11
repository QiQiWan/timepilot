# TimePilot BI 与模型辅助工具设计（v0.4.0）

## 1. 开发目标与范围

BI 系统以可见、可复算、可追踪的表格操作为中心，兼容文件、时序数据集和管理员允许的 SQL 数据源。当前适合研发与中小规模数据分析，单个工作表最大 250,000 行，上传上限默认 25MB。BI 的数据处理使用 Pandas，DuckDB 已列入统一依赖清单以支持后续大表查询优化；当前不要将 DuckDB 描述为已启用的实际执行引擎。

## 2. 表结构与持久化

`BITableRow` 保存表标识、名称、字段元数据、行数、SHA256 和来源。快照位于 `TIMEPILOT_HOME/bi/{table_id}/data.csv`，用户改字段、过滤、去重、转换和关联均产生新的表 ID，原始快照不会被修改。`BIReportRow` 保存报表配置，包括源表 ID、分组、聚合指标和图表类型；再次查看报表时按不可变源表重算汇总数据。

原始表不强制有 `series_id/timestamp/target/available_at`。通过 `/bi/tables/{id}/to-timeseries` 明确字段映射后生成标准预测数据集。此阶段执行 TimePilot 原有时间可用性和主键校验，确保后续滚动回测不引入未来信息。

## 3. API 清单（`/api/v1` 前缀）

| 方法 | 路径 | 功能 |
|---|---|---|
| GET | `/bi/tables` | 列出 BI 表与来源 |
| POST | `/bi/tables/upload` | 导入 CSV/XLSX/Parquet |
| POST | `/bi/tables/import-timeseries` | 导入训练数据集快照 |
| GET | `/bi/sources` | 查看 SQL 源启用状态和允许访问的表名 |
| POST | `/bi/sources/snapshot` | 将白名单数据库表读取为 BI 快照 |
| GET | `/bi/tables/{id}/profile` | 字段类型、缺失率、唯一值、样例与数值范围 |
| GET | `/bi/tables/{id}/preview` | 支持分页、整表搜索、列筛选、升降序排序与来源行索引 |
| POST | `/bi/tables/{id}/transform/preview` | 试算清洗配方，返回行列差异，不创建新快照 |
| POST | `/bi/tables/{id}/transform` | 执行清洗配方，输出新表 |
| POST | `/bi/tables/join` | 按同名键执行 LEFT/INNER 关联 |
| POST | `/bi/tables/{id}/aggregate` | 分组聚合与排序 |
| GET | `/bi/tables/{id}/export.csv` | 带表格公式注入保护的 CSV 导出 |
| POST | `/bi/tables/{id}/to-timeseries` | 转换为标准时序训练数据 |
| GET / POST | `/bi/reports` | 查询或创建报表 |
| GET | `/bi/reports/{id}` | 重新运行保存的报表配置 |
| GET | `/bi/tools` | 暴露 BI 工具 JSON Schema 与执行权限 |
| POST | `/bi/tools/execute` | 通过白名单调度 BI 工具 |
| GET / PUT | `/settings/llm` | 获取与保存模型服务设置（不回显 Key） |
| GET | `/settings/llm/providers` | 获取国内外服务商接口地址、协议与模型示例 |
| POST | `/settings/llm/test` | 真实连接测试（主动调用） |
| GET / PUT | `/settings/general` | 主题、建议策略和预算 |
| GET | `/settings/runtime` | 运行环境、插件和存储信息 |
| POST | `/assistant/bi/plan` | 使用 LLM 生成 BI 操作提议，不自动执行 |
| POST | `/assistant/task/draft` | 使用 LLM 起草结构化预测任务 |

### 示例：分组统计

`POST /api/v1/bi/tables/{table_id}/aggregate`

```json
{"group_by":["month"],"metric":{"column":"sales","operation":"sum"},"sort_by":"group","order":"asc","limit":100}
```

### 示例：清洗配方

`POST /api/v1/bi/tables/{table_id}/transform`

```json
{"name":"sales_cleaned","steps":[{"type":"rename","column":"Sales","new_name":"sales"},{"type":"filter","column":"sales","operator":"gte","value":0},{"type":"deduplicate","columns":["product_id","month"]}]}
```

支持的操作：`rename`、`drop_column`、`drop_nulls`、`fill_null`、`cast`（number/date/text）、`filter`（eq/ne/gt/gte/lt/lte/contains）、`deduplicate`、`select`、`derive_date`（year/month/day/hour）。每条配方最多 20 步，每步由后端验证。数据表无法被原地修改。

关联右表必须在关联键上唯一，避免无意中的多对多扩张；如有重复，需要先在 BI 中按明确的业务规则处理。禁止从 UI/API 执行用户任意 SQL 或 Python。统计只允许指定字段和 count/sum/mean/min/max/nunique；每次输出限制为 500 条汇总记录。

## 4. AI 提议与执行边界

配置 OpenAI Chat Completions 兼容服务后，大模型可读取当前 BI 表的 ID、名称、行数、字段名和推断类型，根据用户需求输出 `tool + arguments + reason`。工具需出现在 `GET /bi/tools` 的白名单，并按 JSON Schema 检查。用户在前端查看提议并确认操作；只读工具可直接调度，写操作传入 `confirmed: true` 才能执行。模型建议不能自动执行代码或原始 SQL。

默认不上传业务原始行，但字段名称本身可能含有敏感信息，使用外部服务前仍应获得企业授权。HTTP 模型服务地址仅支持本机；其他地址必须使用 HTTPS，禁用自动重定向和环境代理。API Key 通过 Fernet 在本地加密，密钥文件属于当前本地操作系统账户；不等同于正式企业 KMS。

## 5. 研发与产品边界

本阶段实现的是嵌入 TimePilot 的轻量 BI 工作台，包含表管理、清洗、关联、聚合、报表和工具接口。企业级 BI 未来可扩展多表数据仓库、细粒度权限、跨用户共享、定时调度、可视化仪表盘和专业 SQL 引擎。TimePilot 的原有时序预测接口继续独立，通过受控数据映射连接 BI 结果。


## 6. v0.3.0 表格操作及服务商预设

表详情 `/bi/table/{id}` 默认提供可滚动表格、分页、排序、筛选和字段侧栏。点击单元格创建位置明确的编辑草稿；编辑可以通过 `set_cell` 这一受控变换步骤进入预览与保存流程。表字段与清洗、报表、AI 助手、预测转换分别有独立子路由。

模型服务商增加 OpenAI、DeepSeek、阿里云百炼（北京/新加坡）、智谱、Kimi、火山方舟、Gemini、Claude、OpenRouter、Groq 和 Ollama，外加自定义兼容服务。Claude 使用 Messages API；其他供应商走各自的 OpenAI 兼容端点。预设不代表账号已授权，模型 ID 仍由用户确认；当前只保存一个活动连接。详见 [BI_GRID_V030.md](BI_GRID_V030.md)。

## v0.4.0 补充：从单步建议扩展到受控多步骤 BI

目前支持 `bi.transform`、`bi.aggregate`、`bi.pivot`、`bi.profile` 四类多步骤工作流节点，最多 6 步。模型只给出结构化计划，独立预览工具在内存中验证全部步骤，写入操作必须提交与计划和数据快照绑定的限时一次性凭证。用户先看预览，再生成一个新版本；原始数据保留。真实第三方模型的计划正确率和工程稳定性仍需单独评估。技术细节见 [`BI_WORKFLOW_V040.md`](BI_WORKFLOW_V040.md)。
