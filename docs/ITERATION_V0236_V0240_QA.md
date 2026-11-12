# TimePilot v0.23.6—v0.24.0 源码实施与阶段验收报告

**编制日期**：2026-11-11  
**基线**：`TimePilot_v0.23.5_COMPLETE_SOURCE_ITERATED.zip`  
**最终源码版本**：`0.24.0`  
**交付类型**：包含所有源代码、测试和发行哈希的源码包。**尚未达到生产级的 Perspective/DuckDB/Arrow/Chrome 真实端到端验收门禁**。

## 1. 执行结论

按照《TimePilot v0.23.6—v0.24.1 完整优化迭代计划》连续实施了资产生命周期、BI 工作台、Perspective 能力发现、Agent Tools 与多步规划五个阶段。项目中既有原始数据、发布版本及历史工作区通过增量迁移保留，既有 `/turns` 和建模链路保留兼容。

| 版本 | 源码改造 | 已验证 | 明确未完成/限制 |
|---|---|---|---|
| **v0.23.6** | 项目资产服务端分页搜索、详情及前 20 行预览；回收站/恢复/影响检查/审计；“查看数据字段”等确定性命令 | HTTP/SQLite 测试；项目隔离、重复请求、字段实际返回；不改变工作区 revision | 物理彻底清理默认禁用；预览真实 DuckDB 链路需要目标依赖 |
| **v0.23.7** | 项目路由全视口 BI Shell、唯一原生 Perspective 画布、高级操作收纳、浮动助手和状态事件桥；停止 Agent 使用前 1000 行 Arrow 覆盖原表 | 静态 DOM 浏览器组件测试（5 种屏幕尺寸、20 次挂载）、JS 状态桥单测、工作区保存接口 | 尚未完成真实 Perspective 5.6.1 JS/WASM + WebSocket 运行时浏览器联动测试 |
| **v0.23.8** | 5.6.1 锁定版能力目录、原生插件/Virtual Server 依赖检测、工作区快照和渲染回执 API | 版本/能力 Manifest 和无依赖时安全拒绝的单测 | Window、ExprTK、Join、Crossfilter 和原生 Heatmap 仍按 `unsupported` 展示；未宣称完整覆盖官方能力 |
| **v0.23.9** | 单一受限 Tool Manifest，把已有真实注册工具映射到支持的能力；Viewer 操作状态桥及前端工具检查 | Tool Manifest 测试，注册/字段/权限前置条件测试 | 没有逐项完成全部可选插件的人工/Agent 同等配置验证；Tool parity 生产门禁未通过 |
| **v0.24.0** | 持久化受限任务计划和步骤、规则优先/可选模型规划、执行观测和渲染确认；OpenAI 可选推理档位配置 | 计划 HTTP/SQLite 生命周期及回归、前端桥接测试，模型参数构造单测 | 未使用真实第三方模型密钥进行质量评估；缺少独立人工标注的 100 条业务语义集；真实高复杂度任务成功率仍待测 |

**状态解释**：本表“源码改造”表示代码已加入发布包，“已验证”具体标明单元/API/组件级证据。“真实引擎 + 浏览器 E2E”和“正式可发布”仍属于 v0.24.1 工作，不可视作本轮通过。

## 2. v0.23.6：数据资产目录与安全回收

### 用户界面

- 项目 → 数据管理：服务端按资产名称、源文件、工作表和字段名查询，提供排序、每页 30 项、分页与状态筛选；不遍历真实单元格执行不受限的全文查找。
- 行动作：**打开资产详情 / 进入 BI / 删除（移入回收站）**；回收站资产可恢复；删除前弹窗显示真实依赖和活跃任务阻断原因。
- 资产详情：列结构、版本、质量、引用、文件完整性和真实前 20 行数据预览；BI 入口使用原资产版本。
- 危险操作：删除只改**当前项目的资产可见性**。已发布内容、共享源文件和模型快照不执行磁盘删除；物理 `purge` 明确返回阻断，不存在暗中清理行为。

### 核心改造文件

- `src/timepilot/asset_lifecycle.py`：稳定逻辑资产 ID、`search/impact/status/change`、并发修订、请求幂等、历史与活跃依赖门禁、项目权限检查。
- `src/timepilot/data_platform.py`、`projects_api.py`：目录状态过滤、分页路由、详情/预览/回收/恢复 API。
- `src/timepilot/static/asset-catalog-ui.js`、`project-ui.js`、`project-ui.css`：目录、搜索、确认弹层、回收站和详情。
- `src/timepilot/bi_intent_resolver.py`、`bi_agent_service.py`、`bi-conversation.js`：将 `inspect_schema` 等只读请求映射到已注册工具，并把字段列表渲染为可读结果。LLM 返回空 `actions` 时重新尝试合法确定性路径。

### 接口（前缀 `/api/v1/projects/{pid}`）

| 方法 | 路径 | 核心约定 |
|---|---|---|
| GET | `/catalog/assets?q=&state=&sort=&page=&page_size=` | 项目内检索及准确分页总数 |
| GET | `/catalog/asset-trash` | 仅已回收的资产 |
| GET | `/catalog/assets/{aid}` | 项目内详细元数据和版本索引 |
| GET | `/catalog/assets/{aid}/preview?limit=20` | 正式数据引擎中的有界只读样本；不冒充全量 |
| GET | `/catalog/assets/{aid}/impact` | 下游视图/模型/运行依赖及阻断信息 |
| POST | `/catalog/assets/{aid}/trash` | `request_id`、`expected_revision`、`confirm=true`，只更新目录状态 |
| POST | `/catalog/assets/{aid}/restore` | 相同稳定 asset ID 恢复；幂等审计 |
| POST | `/catalog/assets/{aid}/purge` | 暂时全部阻断，禁止删除物理原件 |

**典型回归**：“查看数据字段”→`bi.schema.inspect({})`→实际字段名、类型和源列序号，`changed=false`，工作区 revision 不变。未配置模型 API 时仍可执行。

## 3. v0.23.7：唯一 Perspective 工作区

- BI 路由 `/projects/{pid}/bi/native/{aid}` 在应用内部默认铺满视口，普通项目 Shell 的宽度限制不再包裹 BI 画布。浏览器系统全屏只由用户主动点击触发。
- 原有查询、版本编辑、历史、工业分析工具保留在**可折叠高级操作抽屉**；主画布和浮动 Agent 不需要滚动到底部查看。
- `frontend/native-bi/src/workspace-bridge.mjs`、`viewer-events.mjs` 统一处理 Viewer 配置、变化事件、渲染和图表操作；`frontend/native-bi/src/main.js` 把 Agent 图表改动应用于现有 Viewer，而非重新加载 `query.arrow?limit=1000` 作为默认数据源。
- `src/timepilot/bi_perspective_workspace.py` 在用户/项目/工作区作用域保存原生 token、`source_sha256`、双重 revision 和审计；若 token 指向非授权 `timepilot_active` 表，拒绝保存。渲染回执要求和保存面板、插件、revision、来源一致。回执只能证明展示状态，不能替代数值计算证据。
- 相关性矩阵仍保留扩展面板能力，**禁止替换和销毁已配置的主 Perspective Viewer**。原生 Heatmap 插件后端和浏览器的真实兼容仍待验证。

## 4. v0.23.8：5.6.1 能力核查与数据合同

- `src/timepilot/perspective_capabilities.py` 为锁定的 `@perspective-dev/* = 5.6.1` 提供受限功能清单，按 DuckDB、Virtual Server 和浏览器插件核查。未找到对应能力时返回原因。
- `benchmarks/perspective_capability_matrix.json` 将声明功能与工具映射存档；它是**源码能力映射**，不代表 5.6.1 的所有功能通过浏览器实测。
- 已有真实 SQL/Arrow 查询、相关性分析、数据编辑审批等继续通过后端原有权限和不可变数据合同，不允许运行自由 SQL/Python。
- 下列能力保持禁用或受限：原生交叉筛选、ExprTK 表达式翻译、Native Window、Native Join、原生热力图插件、网格高级条件格式、实时流式更新、原件直接编辑。不要把后台存在的近似功能当作 Perspective 插件功能已完全对齐。
- 完整 Native 数据引擎能力及百万行性能测试仍以 `--mode configured` 的真实依赖验证为前提。

## 5. v0.23.9：Tool Manifest 与原生 Viewer 桥接

- `src/timepilot/bi_tool_manifest.py` 从 `bi_tool_registry.py` 的真实注册工具生成可用性，避免模型凭空虚构工具名称。
- 能力清单显示 `effect_class`（read/presentation/compute/commit）及当前状态。高风险写入和管理员动作不进入自动无确认执行集。
- 既有 `bi.schema.inspect`、`bi.rows.peek`、`bi.filter.set/clear`、`bi.sort.set/clear`、`bi.columns.set`、`bi.chart.create`、`bi.stats.correlation`、撤销重做等保留兼容；新增 Viewer 能力/主题/插件配置适配中仅开放拥有真实执行器的部分。
- Agent 修改图表后，前端读取最终 Viewer 状态并发送渲染回执；旧后端审计与原有 `/turns` 接口保留。
- **未达到的指标**：运行时支持的所有 5.6.1 官方插件/配置项逐项手工与 Agent 等价性。现阶段 Manifest 严格区分已注册、后端支持、浏览器待确认与明确不支持。

## 6. v0.24.0：受限多步规划和可选推理模型

- `src/timepilot/bi_reasoning_planner.py`、`bi_agent_api.py`：字段和当前工作区上下文构建、确定性命令优先、受限模型计划、单计划最多 8 步、服务端逐动作校验、SQLite 持久化审计、可重试状态和渲染回执门禁。
- 可选模型只得到当前已授权字段结构、受限工具 Schema、必要 Viewer 状态；不提供 API Key、其他项目源数据、任意 SQL 或 Python 执行权限。模型不能代替后端声称执行成功。
- 增加 `ModelConfig.reasoning_effort=off/low/medium/high`，仅对已适配的 OpenAI API 请求追加推理档位；其他厂商保持既有协议。未用真实 API 验证推理质量。
- 模型无密钥、超时、JSON 错误或给出空动作时，基础只读命令可由确定性解析器完成；复杂需求返回结构化原因和最小澄清。模型的内部推理文本不保留和不展示。
- 新增工作区 API（前缀 `/api/v1/projects/{pid}/bi-agent/workspaces/{wid}`）：`GET /perspective/capabilities`、`GET /perspective/tools`、`GET/PATCH /snapshot`、`POST /render-receipts`、`POST /reasoning-plans`、`GET /reasoning-plans/{run_id}`、`POST /reasoning-plans/{run_id}/apply`、`POST /reasoning-plans/{run_id}/rendered`。

## 7. 数据库迁移及源数据保护

`src/timepilot/project_migrations.py` 将 `project_schema` 由 v7 幂等升级至 **v12**：

| 版本 | 新增对象 | 作用 |
|---|---|---|
| v8 | `asset_lifecycle`, `asset_lifecycle_events`, `asset_dependency_cache` | 资产回收/恢复、引用及审计 |
| v9 | `bi_perspective_snapshots`, `bi_perspective_snapshot_events` | 原生 Viewer token 和并发 revision |
| v10 | `perspective_capabilities_cache` | 能力声明缓存 |
| v11 | `bi_perspective_tool_receipts` | 工具/渲染关联证据 |
| v12 | `bi_reasoning_runs`, `bi_reasoning_steps` | 受限规划、执行步骤与观测 |

迁移前执行 SQLite 完整性检查，按数据库在线 backup API 创建含 WAL 已提交事务的一致性备份。不得在新结构启动后用旧版进程写入项目库；回退需按数据库备份与旧包共同执行。新增表不会修改已有数据资产文件、训练数据快照及原有审计表。

## 8. 已执行测试与证据等级

### 当前容器环境

- Python：**3.13.5**（目标部署为 Python 3.12.12）。
- DuckDB、PyArrow：当前验收环境未安装，无法在这里完成真实数值和 Arrow E2E。
- Perspective：5.6.1 源码依赖在 `frontend/native-bi/package.json` 固定；当前没有编译完毕的真实 JS/WASM 资源和 node_modules。
- 浏览器：现有 Chromium，用于本地组件页面与状态桥的静态 DOM 检查；**不等于正式 Perspective 会话**。

### 实际测试记录

| 测试批次 | 执行结果 | 证据性质 |
|---|---|---|
| 项目管理、资产治理、通用导入、旧 BI Agent、分析计划等相关 Python 回归 | **72 通过、1 跳过**（缺 DuckDB） | Python/API/SQLite；与大模型、真实原生 BI 严格分开 |
| `frontend/native-bi && npm test` | **30 通过、0 失败** | Node 模块测试，使用本地已有 node；未下载浏览器 |
| `accept assets-v0236` | **3 项 Python 测试通过** | 本地资产 API 和指令用例 |
| `accept bi-fullscreen-v0237` | **1 项 Python + 3 项 Node + Chromium 组件检查通过** | 静态 UI，不加载真实 WASM |
| `accept perspective-parity-v0238` | **4 项 Python + 3 项 Node 通过** | 契约与能力阻断；不等于 Perspective 全功能对齐 |
| `accept bi-tools-v0239` | **13 项 Python + 3 项 Node 通过** | 注册工具与执行契约 |
| `accept bi-agent-v0240` | **17 项 Python + 3 项 Node 通过** | 计划持久化和状态；无真实配置模型调用 |
| 原有 99 条固定 BI 指令的离线评估 | **79/99（79.8%）** | 历史旧规则基线；复杂表达泛化仍不足，不代表新模型任务成功率 |

同一个 Python/Node 测试在不同分阶段命令中重复运行，不得将分批计数简单累加。上述 72/30 是扩大覆盖的总体非重复回归结果。

**旧 99 条基线说明**：为保存可比性，`benchmarks/bi_nl/v1_fixed_cases.jsonl` 未被改写；原有规则评测器仍是独立口径。v0.23.5 的 56 条为开发者合成案例，不构成人工双人标注数据。v0.24.0 的真实 LLM 多步规划仍需新案例、独立人工标注及真实执行评测。

## 9. 一键启动与分阶段验收

将源码解压至本地已有 Conda 环境，确保所有操作使用同一套 Python 依赖和本机 Chrome：

```bash
conda activate server
cd /path/to/TimePilot_v0.24.0
bash TIMEPILOT.sh source-check
bash TIMEPILOT.sh

# 新终端、同一个 Python 环境：
bash TIMEPILOT.sh accept assets-v0236
bash TIMEPILOT.sh accept bi-fullscreen-v0237
bash TIMEPILOT.sh accept perspective-parity-v0238
bash TIMEPILOT.sh accept bi-tools-v0239
bash TIMEPILOT.sh accept bi-agent-v0240

# 真实生产门禁：缺依赖时输出 blocked_environment 而不会冒充通过
bash TIMEPILOT.sh accept bi-agent-v0240 --mode configured
```

阶段记录自动输出 `reports/v0240/<stage>_<mode>.json`，其中 `real_native_e2e` 只有跑通目标原生链路才可能是 `true`。根目录启动流程保留原有 Perspective 首次构建/源指纹机制，不要求手动 npm build，不下载 Playwright 浏览器，不创建临时 Python 环境。

## 10. 目前拒绝将源码标记“生产完成”的事项

1. **目标环境 E2E**：Python 3.12.12、真实 DuckDB/Arrow、Perspective 5.6.1 JS/WASM、系统 Chrome 中，执行 `schema → query → Agent → Viewer → Save/Restore → Undo` 的完整闭环。
2. **官方功能 parity**：以本机 `getAllPlugins()`、Virtual Server `get_features()`、原生 Viewer token 的真实结构为准，对 filter/sort/group/split/aggregates/expressions/Window/Join/heatmap 等逐项核对手工与 Agent 结果；不能把“接口可见”代替真实结果。
3. **复杂场景**：跨表派生表、40 列相关矩阵、1M×20、同时多个 Panel、断线重连、来源哈希变化、浏览器回退及覆盖冲突。
4. **企业权限与安全**：Editor/Viewer/Admin、跨租户 HTTP/WebSocket、模型与回收站的组合权限，真实双用户并发验收。
5. **真实语义评估**：收集至少 100 条真实工作指令，独立双人标注与仲裁；分别报告规则和真实配置推理模型的工具、字段、数值、渲染、全链路成功率与预算。
6. **正式发行**：源码完整性通过后，仍须执行完整离线静态资源构建、源指纹一致性核查及 v0.24.1 安全/性能/故障恢复验收。

以上事项应作为下一版本 v0.24.1 的发行门禁。**本次源码包可在目标机继续部署和验收，不能据此断言已经实现 Perspective 所有官方功能或已达到生产级 Agent 能力。**
