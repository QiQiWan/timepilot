# TimePilot v0.24.1—v0.24.5 源码迭代、接口与验收记录

日期：2026-11-11；基线：`TimePilot_v0.24.0_COMPLETE_SOURCE_ITERATED.zip`。

> **交付状态：源码实现 + 离线契约/HTTP/浏览器组件回归已验证；DuckDB/PyArrow + 已编译 Perspective 5.6.1 + Chrome 全链路尚未通过。** 不得将源码测试、使用模拟数据的 API 测试或原生组件测试计入生产 E2E。

## 1. 影响用户的两项缺陷

截图 A：「请将 A8 列与 A10 列相加，结果放在一个新的列」；截图 B：「对 A2 和 A4 字段计算移动平均，到新的列上」。旧对话规划器缺少对两种表达的受限多字段计算路径，返回「尚未匹配到可安全执行的操作」和空工具数组。前端又同时展示长历史、高级操作及一组缺少引导的工具按钮。

改造：`bi_nl_math.py` 在真实已授权字段白名单上解析运算、检测物理数值类型、生成受约束 JSON AST；算术通过 `bi.expression.create`，滚动多列经 `bi.window.compute_many` 转换为各自的因果窗口字段。无时间字段时默认源记录顺序 `__tp_rowid`，窗口默认最近 5 条；绝不冒充日历时间或推断时间单位。`bi_query_engine.py` 按固定语句模板编译 DuckDB SQL，排序时用源行号稳定破同值；窗口仅使用历史和当前行。原始资产不变；计算字段的查询预览明确有上限，需审核发布为新资产才能供训练使用。

- `src/timepilot/static/bi-conversation.js/.css`：只突出对话与随当前列名动态更新的建议操作（字段、前 20 行、两列相加、多列滚动平均、排序），工作区/建模/审核移入默认折叠区；删除默认下方大工具面板。
- `src/timepilot/static/native-bi.js`：计算结果进入独立的只读扩展预览，主 Perspective Viewer 保持挂载；不将预览的 1000 行宣称为全量新表；无真实查询结果时拒绝显示完成。
- `tests/test_bi_math_and_context_v0245.py`：两类用户原始请求、字段非法、非数值拒绝、跨 80 列字段检索、实际 HTTP 计划/状态变更、分组聚合防误判。

## 2. 分版本变更

### v0.24.1｜任务一致性、依赖门禁、迁移

- `import_summary.py`：摘要 Job 在 SQLite 中持久化执行游标、状态、尝试次数和两分钟租约，服务启动重新认领失效租约；后台线程心跳更新、失败可重试、可取消。被认领任务只跳过已经完成的 Region，不跳过正在处理的当前 Region；源哈希每次复查。
- `asset_references.py`、`asset_lifecycle.py`：回收前检查运行中摘要、发布、分析、特征配方等引用；不通过物理删除共享源文件来执行回收。
- `project_migrations.py`：保留原有结构、创建迁移前 SQLite backup（覆盖已提交 WAL），旧库 v4 可沿 v8—v17 增量升级；重复执行 DDL 对存在字段进行精确检查，禁止高版本库降级。
- `frontend/native-bi/src/workspace-bridge.mjs`：手工配置和 Agent 修改合并时保留未被当前动作触及的源、筛选及面板，锁住来源变更；状态 API 继续使用 `expected_revision`。

### v0.24.2｜DataContract 与可信摘要

- `import_data_contract.py`：对每张 Region 提供 `field_id/ordinal/technical_name/source_name/source_cell/physical_type/scan scope/quality/source_sha/fingerprint/semantic_status/review`；样本类型推断及全量数值解析比例独立标识。
- `project_publish_batches.py`：表头或 Region 边界待复核时要求用户确认，已排除 Region 不可发布；存在未分配的非空区域时须单独确认。不擅自更改源字段顺序、前导零和原文件；摘要失败不会因丢弃原源导致误发布。
- `projects_api.py`：Region 合同读取和人工确认路由，摘要 Job 的 retry/cancel；`project-ui.js` 导入页加入待核实标签和确认入口。
- `tests/test_import_contract_and_jobs_v0242.py`：验证真实 SQLite 的超时租约重新认领、Region 游标恢复、取消，以及合同的源单元格证据/人工确认/项目隔离。

### v0.24.3｜Perspective 状态与计算预览

- `frontend/native-bi/src/workspace-bridge.mjs`：新增 `mergeAgentPanelConfig()` 及单一 Viewer 状态的保留规则；`main.js` 应用前检查预期 token，禁止旧 Agent 响应静默覆盖新手工配置。
- `src/timepilot/static/native-bi.js`：展示相关矩阵/计算列时保留主 Viewer，将它们放在受管理的扩展面板；派生计算附上源版本和预览范围。
- `frontend/native-bi/tests/agent-manual-merge-v0243.test.mjs`：验证筛选合并、来源禁止切换、重复操作幂等。

**待补齐：** 当前构建环境没有真实 Perspective JS/WASM 和 Arrow；多面板拖拽、交叉筛选、刷新与回滚的真实 Chrome E2E 仍是发行门禁，扩展面板暂未等价于原生 Perspective Heatmap 插件。

### v0.24.4｜FeatureLab

- `feature_lab.py`：`FeatureRecipeInput` 包含固定来源资产/版本及最多 30 个步骤；支持加减乘除、Lag、差分、历史滚动均值/标准差、按设备分组、确定性排序。变量引用逐一检验真实技术字段，SQL 从固定模板编译，不接受用户 SQL 或路径。
- 预览时返回来源 SHA、配方指纹、实际行数和样本范围；`draft → pending_review → approved → publishing → published`；企业模式审核人与创建人分离；发布是新不可变 Parquet 数据资产并保留完整 lineage。每次创建、提交审核、批准和发布都有独立 `feature_recipe_events` 审计。
- `feature_lab_api.py`：新建、清单、详情、预览、提交审核、批准、发布。`static/feature-lab-ui.js/.css`：项目「特征工程」页面提供表、字段、方法、分组、排序和产出配置。
- `tests/test_feature_lab_v0244.py`：SQL 模板校验、禁止未来帧、无效字段拒绝、HTTP 配方生命周期、审计；真实 DuckDB 预览与新资产发布未在当前机器执行。

### v0.24.5｜动态 Schema 与受限推理规划

- `bi_context_builder.py`：在 200 列以上宽表仍检索用户明确提及的后半部分真实字段；仅向模型提供当前授权字段和可用工具 JSON Schema 的匹配子集。
- `bi_reasoning_planner.py`：先处理确定性的短命令、算术、滚动平均、列查看和样本查看；复杂命令根据运行时允许的工具做模型规划，失败或返回空数组回退本地规则/明确澄清；不展示或保存模型内部逐词推理，不信任模型的 SQL、项目 ID 或越权要求。
- `bi_execution_observer.py`：对多步计划依次生成状态摘要，使用真实已授权引擎执行有界预览检查，记录证据哈希及结果来源；仍通过已有并发修订、审计和 Viewer 回执进行最终验收。
- 修复 `scripts/evaluate_bi_nl_v0232.py` 旧评测脚本的孤立工作区缺少 `asset_id` 导致 99/99 `KeyError`：只在测试夹具中配置完整虚拟资产元数据，生产代码的权限与字段类型规则保持不变。
- 完成少量可解释中文匹配的扩展（含字段高于阈值、非零、空值排除、排序、分组求和），避免把带分组的“求和”误认作两字段逐行相加；无法确定的请求继续要求澄清。

## 3. 接口索引

| API 前缀均含 `/api/v1/projects/{pid}` | 方法 | 作用 |
|---|---|---|
| `/imports/{iid}/regions/{rid}/contract` | GET | 数据字段/来源坐标/证据/审核 |
| `/imports/{iid}/regions/{rid}/review` | POST | 确认或排除 Region，按指纹失效 |
| `/imports/{iid}/summary-jobs/{jid}/retry` | POST | 重新执行失败摘要任务 |
| `/imports/{iid}/summary-jobs/{jid}/cancel` | POST | 取消待执行摘要任务 |
| `/feature-lab/recipes` | GET/POST | 列出或建立有版本绑定的特征配方 |
| `/feature-lab/recipes/{rid}/preview` | POST | 真实有界计算预览 |
| `/feature-lab/recipes/{rid}/request-review` | POST | 发起审核 |
| `/feature-lab/recipes/{rid}/approve` | POST | 核验与确认 |
| `/feature-lab/recipes/{rid}/publish` | POST | 物化为不可变新资产 |
| `/bi-agent/workspaces/{wid}/turns` | POST | 旧接口兼容新运算命令 |
| `/bi-agent/workspaces/{wid}/reasoning-plans` | POST | 受限多步计划与审计 |

当前项目数据库目标 `project_schema = 17`。升级时旧数据与历史审计不会被清空；向旧版代码回退必须先还原备份，不能直接写入高版本项目库。

## 4. 测试记录与边界

- `frontend/native-bi`: `node --test tests/*.test.mjs` **33/33 通过**；包含 Viewer 状态合并的新测试。
- Python 联合回归（导入/摘要/资产/FeatureLab/规划/BI）：**45/45 通过**（2026-11-11 按文件选择性执行）；详见 `reports/v0245` 每阶段 JSON。
- 五阶段 `offline` 验收全部 `source_tests_passed`，按项目互相独立重跑；没有使用模拟结果来声称 SQL/Arrow 数值精度达标。
- 已安装 Chromium 的浏览器**组件级**画布尺寸、浮窗反复挂载、合成热力图测试通过；尚无真实 Perspective 的 JS/WASM + DuckDB/Arrow 数据流浏览器测试证据。
- 旧 99 条**开发者标注**固定指令，修复评测夹具与规划规则后离线 `exact_tool_sequence_accuracy = 84/99 (84.85%)`，`planner_error_rate = 0`，`unsafe_action_rate = 0`，`hallucinated_field_rate = 0`；**仍未达到 ≥90% 准确率门槛**，也未做双人真实业务标注，见 `reports/bi_nl_v0232.json`。
- `configured` 实际运行结果：`blocked_environment`；当前 Python 3.13 与目标 3.12.12 不同，缺少 DuckDB、PyArrow 和已编译的 Perspective 5.6.1 包。故未证明 100 万行性能、表达式/窗口 DuckDB 数值正确性、发布物化、真实 Chrome 单 Viewer 全链路。
- 全量 Python 套件曾在约 19% 被执行超时；本版仅声明按模块执行并存证的测试通过，**不能将全量套件视为通过**。

## 5. 操作和验收

```bash
conda activate server
cd /path/to/TimePilot_v0.24.5
bash TIMEPILOT.sh source-check
bash TIMEPILOT.sh
# 五阶段离线自检
bash TIMEPILOT.sh accept bi-recovery-v0241
bash TIMEPILOT.sh accept import-contract-v0242
bash TIMEPILOT.sh accept bi-workspace-v0243
bash TIMEPILOT.sh accept feature-lab-v0244
bash TIMEPILOT.sh accept bi-reasoning-v0245
# 目标机具备 DuckDB、PyArrow、Chrome、Perspective 构建后执行真实门禁
bash TIMEPILOT.sh accept bi-reasoning-v0245 --mode configured
```

用户工作流：项目 → 数据管理 → 上传文件 → 核实标为待审阅的子表 → 查看字段与质量摘要 → 批量发布 → 全屏 BI / 浮动分析助手；如需可复用的历史特征，在项目 → 特征工程建立配方、真实预览、提交审核、发布。自然语言计算结果先在 BI 附加区域显示，不会覆盖完整 Perspective Viewer，也不会自动成为训练数据。

**发行决议：** 当前包是 **v0.24.5 source-only iteration**。尚缺失可发行运行包所需的真实引擎验证和浏览器全链路；应在目标机执行上述 configured 验收、单独的 FeatureLab 数值/发布验收及真实业务语义评测后，再评估生产发行。
