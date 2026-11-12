## v0.31.0 Mac 安装修复（2026-11-12）

若 `source-check` 报 `.env.example` / `.github/workflows/ci.yml` 哈希不一致，执行
`bash TIMEPILOT.sh source-repair`，脚本先备份本地变更，再按发布清单恢复；
然后在联网构建机（Node 24）运行 `bash TIMEPILOT.sh bi-setup --bootstrap-lock`
完成 Perspective JS/WASM 构建。`db-check` 中的 `schema 30 -> 34` 是待迁移提示，
正常启动时会先备份再升级，**不要删除原项目数据库**。
详细说明见 `docs/MAC_SOURCE_AND_BI_REPAIR_V0310.md`。

# TimePilot v0.31.0 — 特征评价与发布可靠性收束（源码版）

本次涵盖 v0.30.1—v0.31.0：发布契约、CI 与离线构建门禁；tsfresh/Featuretools 的时序有效性、计算预算、超时与内存监控；高级候选经逐预测时点 PIT 核对物化、同产物哈希模型评价与 BI；项目级五步工作流和真实业务 Agent 复核工具。

**重要限制：当前提供的是 SOURCE ONLY。** 未提供真实 JS/WASM 与审查后的 npm 锁文件，不能声称开箱即用的完整离线发行包；缺少企业产线数据、真实质量标签或业务人工审阅时，正式验收必须 `blocked`。在目标机执行 `bash TIMEPILOT.sh source-check` 和 `bash TIMEPILOT.sh accept feature-lab-v031` 进行源级测试；配置真实业务字段与数据后运行 `bash TIMEPILOT.sh accept feature-lab-v031 --mode configured` 进行全链路验收。详见 `docs/FEATURELAB_V031_RELEASE_AND_REVIEW.md`。

---

# TimePilot v0.30.0 — 高成本工业特征发现与同源 PIT 验收

新增可选 tsfresh/Featuretools、多表按实例 PIT 聚合、受控 Agent 工具及高级发现工作台；所有高级结果是待审核候选，禁止自动发布。正式监督评估验收脚本在隔离项目内将同一外部 CSV 贯通至 Parquet 物化、特征选择、模型对照、设备/工况切片和 BI DuckDB 视图。

启动：`bash TIMEPILOT.sh source-check`；源码回归：`bash TIMEPILOT.sh accept feature-lab-v030`；选择安装 `python -m pip install -r requirements-featurelab-advanced.txt`；实际数据验收：`bash TIMEPILOT.sh accept feature-lab-v030 --mode configured`（需配置 `TIMEPILOT_FEATURELAB_QA_*` 来源与 9 个字段映射）。完整参数、运行与审阅结果见 `docs/FEATURELAB_V030_RELEASE_AND_REVIEW.md`。

**验收边界**：本轮没有可核验的真实产线测量/质量数据；容器缺 DuckDB/Arrow、两项可选包与 Perspective 打包资源，因此真实端到端/Perspective 浏览器回执在部署机继续验收。独立测试的生成数据不计为真实产线证据。

---

# TimePilot v0.29.0 — 训练折特征筛选、模型贡献对照与稳定性评价

新增三组模型对照（原始 / 全派生 / 训练折筛选）、线性与随机森林基线、时间前推评估、设备/工况切片、PSI 漂移和默认关闭的预算受控自动公式搜索；可以审阅特征并生成独立未批准草稿，数据库 schema 31→32 加性迁移。

启动和验收：`bash TIMEPILOT.sh source-check`、`bash TIMEPILOT.sh accept feature-lab-v029`。真实文件验收需人工核实时间、标签可用时间和预计算派生列，通过 `TIMEPILOT_FEATURELAB_QA_CSV` 等变量设置；详情见 `docs/FEATURELAB_V029_RELEASE_AND_REVIEW.md`。

**验收边界：**本轮没有真实来源可确认的产线 CSV；当前环境缺失 DuckDB/Arrow/Perspective 原生运行条件。已完成源码/模拟数据数值与浏览器组件检查，真实产线 PIT/BI 端到端验证待具备数据和依赖的目标环境执行。

---

# TimePilot v0.28.0 — 高级特征构造、自定义公式与可视化分析

在 v0.27.0 半自动候选工作台基础上，增加以下功能（需在目标环境完成 DuckDB / Arrow / Perspective 真实全链路验收）：

- **高级算子**：滚动求和、中位数、90% 分位数、极差、变异系数、标准化偏差；相对变化率、数值绝对值、平方根和 `log1p`；保留按实体/时间契约计算的限制。
- **自定义公式**：通过可视化输入字段 `x0…x7` 和白名单函数构造特征；后端验证字段、单位和表达式 AST，编译安全 DuckDB SQL。不可调用任意 SQL / Python。
- **多种可视化**：趋势、分布直方图、原始值—派生值散点图；图表标明样本范围，质量统计标明实际扫描范围。
- **版本与工作流**：推荐 → 后台预览 → 选择 → FeatureSet 草稿 → Parquet 物化 → 最新产物查询 → BI；保留来源哈希、契约修订、生成算法版本和工作会话。
- **验收命令**：`bash TIMEPILOT.sh accept feature-lab-v028` 运行源码/单元/Node 检查；真正测量文件运行 `TIMEPILOT_FEATURELAB_QA_CSV=/absolute/path/data.csv bash TIMEPILOT.sh accept feature-lab-v028 --mode configured`。也可单独运行 `python scripts/featurelab28_acceptance.py --csv /path/to/data.csv --report /path/to/report.json`。所有验收使用隔离数据目录，不改写原件。

**实际验收范围**：当前开发容器无 DuckDB、PyArrow、Perspective 可安装资源，真实数值计算/Parquet 与 BI 浏览器面板在此环境未运行。所附工业结构样例为生成数据，不能作为实际产线数据证据。参见 `docs/FEATURELAB_V028_RELEASE_AND_REVIEW.md`。

---

# TimePilot v0.27.0 — 半自动特征工作台

在 v0.26.1 数据目录启动恢复基础上，完成 v0.26.2 动态算子表单和 v0.27.0 半自动特征工程工作台。

- **默认快速生成**：字段语义提示、基础/时序/工业/质量四种模板、批量候选、勾选筛选和保存到现有 FeatureSet。
- **手工模式**：按算子元数据动态显示适用字段与参数，自动创建不重复的技术名；支持已确认 BOOLEAN 维护事件。
- **候选预览**：通过现有后台租约任务执行只读样本计算，展示原始值/派生值、曲线与样本缺失信息；不将样本统计当成模型提升证据。
- **会话恢复**：SQLite 项目目录 schema 31 保存候选、选择、模板及工作台布局；源 SHA 或契约修订改变时旧会话失效。
- **保留原有链路**：DuckDB + Parquet、Perspective、FeatureSpecV2、审批、审计、版本化发布均保持兼容，无需 npm 或模型 API。

详细使用步骤、接口契约、验收记录与已知限制：`docs/FEATURELAB_V027_RELEASE_AND_REVIEW.md`。

本次源码测试按模块 65 项通过。**真实 DuckDB 特征计算、Perspective 加载及 Windows 实机效果尚待目标 Python 3.12.12 环境验收**。启动前可运行 `bash TIMEPILOT.sh db-check` 和 `bash TIMEPILOT.sh source-check`。

---

# TimePilot v0.26.1: database initialization and startup recovery

The startup error that reported an unknown project catalog version for an
already-upgraded v30 database has been fixed. New catalogs are created
automatically and valid older catalogs are backed up before migration. The
new `db-check` command reports the exact runtime data paths and refuses to
reset unknown or corrupt files. Fresh installations default to `~/.timepilot`
so source code can be safely replaced; an existing source-local `runtime`
continues to be used unless you explicitly relocate it.

```bash
conda activate server
bash TIMEPILOT.sh db-check
bash TIMEPILOT.sh source-check
bash TIMEPILOT.sh
bash TIMEPILOT.sh accept catalog-bootstrap-v0261
```

Full details: [Catalog and startup guide](docs/CATALOG_STARTUP_V0261.md).

---

# 时骁 TimePilot v0.26.0（FeatureLab 2.0 审阅修复与实验集成源码版）

## v0.25.7—v0.26.0 本轮改造

- **v0.25.7**：修复七项独立审阅缺陷：成功产物门禁、监督 TaskSpec 绑定、二分类非法标签、资产切换清理旧状态、Viewer 快照失效、幂等任务指纹及 IANA 时区校验。
- **v0.25.8**：统一发布、查看、原生 WebSocket 的物化资产资格校验；加入取消/物化提交的租约栅栏、跨版本保护和真实 DuckDB 数值测试（目标机执行）。
- **v0.25.9**：严格二分类标签域和验证折类别报告；支持按设备与真实事件时间计算“距最近维护时间”的安全算子；特征构造时可绑定监督任务，评价沿用固定 TaskSpec。
- **v0.26.0**：Agent 多步计划支持计算 → Perspective 双面板 → 渲染回执。缺少真实计算或界面回执时停留在等待/失败状态，不提前宣称成功。

**交付边界**：完整源码与离线契约/组件测试；真实 DuckDB、PyArrow 与 Perspective 5.6.1 JS/WASM 的 FeatureLab 全链路验收仍需在 Python 3.12.12 的目标机器完成。历史资产、审批和 FeatureRecipe 保持兼容。

```bash
bash TIMEPILOT.sh source-check
bash TIMEPILOT.sh accept feature-audit-v0257
bash TIMEPILOT.sh accept feature-engine-v0258
bash TIMEPILOT.sh accept feature-industrial-v0259
bash TIMEPILOT.sh accept feature-agent-e2e-v0260
bash TIMEPILOT.sh accept feature-agent-e2e-v0260 --mode configured
```

详见 [`docs/featurelab/ITERATION_V0257_V0260_QA.md`](docs/featurelab/ITERATION_V0257_V0260_QA.md)。

---

# 时骁 TimePilot v0.25.6（FeatureLab 2.0 正确性与交互集成源码版）

## v0.25.3—v0.25.6 修复与增强

- **v0.25.3**：字段时间语义类型门禁、监督任务与物化产物 PIT 证据绑定、任务取消与运行状态同步、任务租约与失败恢复。
- **v0.25.4**：受预算限制的独立特征预览（不创建正式产物）、按真实时长的安全窗口算子和源可用时间检查。
- **v0.25.5**：不可变配方修订快照、版本差异、按用户/项目隔离的 Perspective 工作区 token 保存，以及受控特征表与原始表对照连接。
- **v0.25.6**：按意图筛选工具、草稿→预览的分步工具执行、持久化执行观测、等待作业完成与有限重规划、可读实验对照。

**已验证**：新增 Python/HTTP 安全与服务契约测试、Node 前端回归、已安装 Chromium 的模拟 Viewer 组件检查。**尚未验证**：真实 DuckDB/Arrow 数值、真实 Perspective 5.6.1 JS/WASM 的完整 FeatureLab 特征对照和保存/刷新/撤销。源码包为 source-only，不含预编译 Perspective 二进制资源。不能将其宣称为生产环境完整验收通过。

执行：`bash TIMEPILOT.sh`；逐版验收：`bash TIMEPILOT.sh accept feature-safety-v0253`、`feature-preview-v0254`、`feature-perspective-v0255`、`feature-agent-v0256`；`--mode configured` 将严格检测目标依赖，缺失时标记环境阻断。

详细说明：[v0.25.3—v0.25.6 实施与验收](docs/featurelab/ITERATION_V0253_V0256_QA.md)。

---

# 时骁 TimePilot v0.25.2（FeatureLab 2.0 源码验证版）

## FeatureLab 2.0 | v0.24.7—v0.25.2

本轮新增：字段/时间/实体数据契约、可恢复画像作业、FeatureSpec v2 和安全算子 DAG、工业时序点时可用性检查、探索及监督对照评价、可审核发布的特征集、基线特征质量/漂移对比、动态 Agent 工具目录以及四个特征工作台页面。保留 v0.24.6 以前的特征配方和 BI 入口。

**验收边界：**源码/HTTP/模拟接口、原生 Node 与本机 Chromium 组件可测试。仅在目标 Python 3.12.12 且安装 DuckDB、Arrow、已构建 Perspective 5.6.1 JS/WASM 后才能完成真实计算与全页面图表验收。本源码包不包含 JS/WASM 离线发布产物，不应标记为生产级 E2E 已通过。

执行命令：`bash TIMEPILOT.sh`（仍沿用现有环境和自动前端构建）。独立验收：`bash TIMEPILOT.sh accept feature-agent-v0252`；真实验收门禁：`bash TIMEPILOT.sh accept feature-agent-v0252 --mode configured`。其他阶段见 `docs/featurelab/USER_GUIDE.md`。

---

# 历史版本：TimePilot v0.24.6（源码迭代版）

工业时序数据整理、分析、研究评估和受控建模工作台。依赖以根目录 `requirements.txt` 和 `pyproject.toml` 为准。本地模式使用现有 Python 3.12 环境，提供数据导入、版本化 BI、人工语义标注、Agent 任务及滚动预测实验。


## v0.24.6：图表系列保留与 BI 易用性

- 在 Perspective 5.6.1 内切换图表类型时，优先保留当前面板手动拖入的兼容数据系列，不影响其它面板、筛选和原始表。
- 「计算字段」「移动平均」「按时间汇总」「关联数据表」改为应用内字段选择表单，支持从当前图表已选系列填入；旧版 `prompt/confirm` 已从主要 BI 操作路径清除。
- BI 分析助手缩放尺寸与用户/资产绑定，标题栏只保留关闭按钮；右上角新增带分类检索的「使用手册」。
- 高级操作按任务分类，审核和建模等低频操作收纳；旧数据及审批链保持兼容。

使用说明：[BI 使用手册](docs/BI_USER_GUIDE_CN.md)。改动范围、回归及真实引擎验收边界：[v0.24.6 质量报告](docs/ITERATION_V0246_QA.md)。

## v0.24.1—v0.24.5：数据契约、FeatureLab、工作区一致性与语义计算

本轮从 v0.24.0 源码增量推进五个阶段：摘要任务 SQLite 租约与断点恢复、依赖检查、按源单元格证据确认 Region 的 DataContract、原生 Perspective Viewer 单实例状态保留、可审核/发布的工业时序 FeatureLab、动态工具字段检索与多步受限规划。

**对话助手**默认只显示简洁提问框和根据当前真实数值字段生成的建议操作；高级版本、派生审核、建模和工业工具移到折叠区。自然语言「A8+A10 生成新列」「A2、A4 移动平均」有确定性、安全校验的工具路径；默认窗口为 5 条源记录，并明确注明不推断时间单位。计算结果先作为只读预览，原件不变；训练使用须经特征配方审核或派生数据审批。

```bash
bash TIMEPILOT.sh source-check
bash TIMEPILOT.sh accept bi-recovery-v0241
bash TIMEPILOT.sh accept import-contract-v0242
bash TIMEPILOT.sh accept bi-workspace-v0243
bash TIMEPILOT.sh accept feature-lab-v0244
bash TIMEPILOT.sh accept bi-reasoning-v0245
bash TIMEPILOT.sh accept bi-reasoning-v0245 --mode configured
```

**请注意：** 交付为 source-only，未预编译本机 Perspective JS/WASM；当前容器缺 DuckDB、PyArrow，尚未完成真实数据引擎与 Chrome 的完整端到端验收。旧 99 条规则评测准确率 84.85%，仍低于 90% 目标；单元测试通过不意味着工业数据计算质量已经达标。详见 [`docs/ITERATION_V0241_V0245_QA.md`](docs/ITERATION_V0241_V0245_QA.md)。

## v0.23.6—v0.24.0：资产管理、统一 Perspective 工作区与受限 Agent

本轮围绕项目内数据目录、全屏工作区、Perspective 5.6.1 能力清单和 Agent 执行链路做增量重构。

- **资产管理**：项目内 SQLite 分页搜索、打开资产详情和真实字段、逻辑移入回收站、恢复、引用影响预检与审计；原始文件、已发布版本及历史模型不删除。物理彻底清理仍禁用。
- **BI 工作台**：在项目路由默认使用独立全视口布局；原生 Perspective Viewer 占据主体，历史查询/编辑/版本操作保留在高级抽屉中。手工与 Agent 图表更新使用同一 Viewer；不再将有限 `query.arrow?limit=1000` 结果当作全量源表。
- **状态一致性**：保存已授权原生 Viewer token、来源哈希、工作区修订与渲染回执；浏览器事件和 Agent 更新共用状态通道，并用修订冲突阻止覆盖。
- **能力与 Tools**：用锁定的 5.6.1 版本及实际数据引擎声明功能，注册的可用工具有明确参数 Schema 和执行接口。窗口、跨表关联、ExprTK 等未完成原生对齐时会返回不可用原因。
- **Agent**：“查看数据字段”“查看前20行”“资产概览”等直接走只读工具。复杂任务可使用有上限的工具规划器；可在 OpenAI 推理模型配置中选择推理档位；模型建议经过服务端工具、字段、权限和数据版本校验。

完整源码、迁移说明、验收状态和未完成的真实端到端条件参见 [`docs/ITERATION_V0236_V0240_QA.md`](docs/ITERATION_V0236_V0240_QA.md)。

```bash
bash TIMEPILOT.sh source-check
bash TIMEPILOT.sh accept assets-v0236
bash TIMEPILOT.sh accept bi-fullscreen-v0237
bash TIMEPILOT.sh accept perspective-parity-v0238
bash TIMEPILOT.sh accept bi-tools-v0239
bash TIMEPILOT.sh accept bi-agent-v0240
# 在目标 Python 3.12.12、DuckDB、PyArrow 和离线 Perspective 均就绪的机器上：
bash TIMEPILOT.sh accept bi-agent-v0240 --mode configured
```

**发行边界**：本 ZIP 为 SHA256 校验的完整源代码包，不包含未实际编译的 Perspective JS/WASM。离线单元/DOM/合成浏览器测试通过不代表真实 DuckDB→Perspective→Agent 链路已通过。`configured` 验收缺少运行时依赖时明确阻断；不得改写为“已通过”。


## v0.22.0—v0.22.1：通用数据解析与多表导入

进入「项目管理 → 数据 → 上传新数据」即可上传一个或多个 CSV、XLSX、Parquet 或 ZIP 文件。后台自动扫描文件、工作表与独立子表；每个区域显示真实字段名、单元格坐标和原始数据样本。导入阶段不再要求逐列选择时间、目标、实体或指标业务角色，也不会将 `A1` 等来源字段重命名。默认选中全部确定的区域，一次提交批量后台发布，每张表形成独立不可变数据版本并注册正式 BI。

无模型 API 也能完成确定性扫描和发布；开启已配置模型后，可调用受控的 `import.*` 只读结构工具辅助解释。批次登记支持中断后修复缺失的子任务关联；真实 BI 导入还会在全量列扫描中保护晚到的前导零编码。企业权限、原始文件哈希、逐工作表恢复和发布分块检查点继续保留。旧数据/模型 API 保持兼容，但预测专用转换不再出现在通用导入主流程。完整接口、限制及验收见 [通用导入说明](docs/UNIVERSAL_IMPORT_BATCH_V0221.md)。后续 v0.23 将单独开发对话式 BI 工具库。


## v0.23.3—v0.23.5：原生工作台、结构摘要与模糊分析（源码迭代版）

本次版本在 v0.23.2 的现有项目/原生 BI/Agent 契约上增加：原生大画布与浮动分析助手、按资产和用户作用域持久化 UI 偏好、源文件哈希校验的多表 Region 统计摘要、可恢复的分析计划记录与受限 DuckDB Pearson 相关矩阵、原生工作区矩阵热力图呈现。原有 99 条指令评测集保留，单独新增 56 条开发者合成模糊指令集。

**验收声明：** 当前交付为源代码包，未提供预编译 Perspective JS/WASM，且目标机真实数据引擎/Chrome 全链路尚未验收；开发者合成指标不能当作模型/业务准确率。详细任务逐条实现状况、可运行命令、受限项见 [`docs/ITERATION_V0233_V0235_QA.md`](docs/ITERATION_V0233_V0235_QA.md)。

```bash
bash TIMEPILOT.sh source-check
bash TIMEPILOT.sh accept bi-ui-v0233
bash TIMEPILOT.sh accept import-summary-v0234
bash TIMEPILOT.sh accept bi-fuzzy-v0235
bash TIMEPILOT.sh accept bi-fuzzy-v0235 --mode configured
```

## v0.23.2：经审核派生数据、可量化 BI Agent、工业验收

- BI 的关联、计算、时间聚合、维护事件标记可申请冻结为新的不可变表；审批后发布，并在项目中创建模型草稿。图表标记不会自动成为训练标签。
- 固定 99 条中文 BI 工具调用基准，区分离线规则评估与已配置模型的真实评估，报告准确率、越权、歧义拒绝和恢复率。
- `bash TIMEPILOT.sh accept derived-real` 在安装 DuckDB/PyArrow 的环境执行真实物化；`accept bi-nl` 运行离线基线；`accept industrial-bi` 执行真实 DuckDB/Arrow 长时测试。企业 HTTPS 和 Chrome 浏览器仍分别通过原有验收工具。
- 详细契约与验收见 `docs/APPROVED_DERIVATIONS_AND_BI_EVAL_V0232.md`。

## v0.23.0-v0.23.1: Conversational BI and project modeling

The project BI uses a project-bound agent workbench with a validated tool catalog, bounded DuckDB queries, actual Arrow-backed Perspective charts, a persisted per-user conversation transcript and reversible workspace revisions. Core actions include filters, sorting, projection, aggregates, numeric calculation expressions and native chart panel creation. Users can preview tool plans, apply them, save dashboard configurations, reopen saved views and undo/redo changes. Real free-form language depends on an optional configured model; the no-model planner only supports explicitly known Chinese patterns.

The second stage adds guarded cross-table virtual JOIN (exact grouped-count preview, 250k row output and 5x fanout ceilings), timestamp bucketing, rolling averages and industrial maintenance/regime overlays. Generic published tables can be discovered directly as association sources. From a pure row-level filtered workspace the user can freeze a full-row DataSelection and create an existing review-required model draft. Virtual JOINs/calculations/aggregates cannot silently become training data; materialization needs a separate reviewed stage.

**Run:** `bash TIMEPILOT.sh accept conversational-bi` (real DuckDB/Arrow; fails with a BLOCKED result if missing dependencies), `bash TIMEPILOT.sh accept conversational-bi-ui` (existing browser, DOM stub), and `bash TIMEPILOT.sh accept native` (real built Perspective WASM and browser). All three must be distinguished in formal acceptance. [Implementation, API and audit](docs/CONVERSATIONAL_BI_WORKBENCH_V0231.md).

## 启动与诊断

在已有 Conda 环境中执行。**正常启动不运行 pip、不创建环境、不下载浏览器。首次启动或 BI 源码发生变化时，如缺少已编译 Perspective 资源，会自动使用本机 Node/npm 安装锁定的前端依赖、构建 JS/WASM、验签并启动；此后启动复用本地已校验资源。** 首次构建可能需要访问 npm Registry。macOS 已通过 nvm 安装 Node 24 时，启动入口会自动切换到该版本，无需手动 `nvm use 24`。强制离线可设置 `TIMEPILOT_OFFLINE=1`，此时只尝试现有 node_modules 或已编译离线包，缺失则明确中止。

```bash
conda activate server
cd /path/to/TimePilot_v0.24.0
bash TIMEPILOT.sh
```

打开 <http://127.0.0.1:8000>。Windows 使用 `TIMEPILOT.bat`（在已激活 Python 3.12 的终端中执行）。保留原业务数据时，请先设置 `TIMEPILOT_HOME` 指向既有数据目录；不要把真实数据复制进源码发布包。

根目录仅保留两份可执行入口：`TIMEPILOT.sh`（Mac/Linux）和 `TIMEPILOT.bat`（Windows）。其余构建、诊断、验收和恢复均由这些入口调用 `scripts/` 下的内部实现。

| 操作 | Mac/Linux 命令 |
| --- | --- |
| 启动服务 | `bash TIMEPILOT.sh` |
| Conversational BI real DuckDB/Arrow acceptance | `bash TIMEPILOT.sh accept conversational-bi` |
| Conversational BI existing Chrome DOM smoke | `bash TIMEPILOT.sh accept conversational-bi-ui` |
| 环境诊断 | `bash TIMEPILOT.sh check` |
| 查看当前状态 | `bash TIMEPILOT.sh status` |
| 校验完整源码 | `bash TIMEPILOT.sh source-check` |
| 恢复缺失的隐藏文件 | `bash TIMEPILOT.sh restore-hidden` |
| 全量测试 | `bash TIMEPILOT.sh test` |
| 研究阶段验收 | `bash TIMEPILOT.sh accept research` |
| Agent/统一资产验收 | `bash TIMEPILOT.sh accept agent` / `bash TIMEPILOT.sh accept unified` |
| 项目管理与导入专项验收 | `bash TIMEPILOT.sh accept projects` |
| 多表发布与真实 DuckDB/Arrow 端到端验收 | `bash TIMEPILOT.sh accept universal-import` |
| 原生 BI 真实浏览器验收 | `bash TIMEPILOT.sh accept native` |
| 研究页面真实浏览器验收 | `bash TIMEPILOT.sh accept research-browser` |
| 手动构建完整离线发行包（可选） | `bash TIMEPILOT.sh build` |
| 备份运行数据（先停止服务） | `bash TIMEPILOT.sh backup --output /path/to/timepilot-runtime-backup.zip` |
| 启动时使用严格离线模式 | `TIMEPILOT_OFFLINE=1 bash TIMEPILOT.sh` |
| 完整源码打包 | `bash TIMEPILOT.sh package --output /path/to/archive.zip` |

注意：源码包不含已编译 Perspective JS/WASM 或 npm 锁文件。**运行 `bash TIMEPILOT.sh` 即可自动完成首次前端构建与校验；无需预先运行 `build` 命令。** 首次需要可访问 npm Registry 的网络或已有完整缓存。自动构建失败会保留旧有效资源并拒绝发布不完整 BI；不会偷偷替换到简易页面。生产使用应将 `TIMEPILOT_HOME` 放在本地磁盘，避免 OneDrive/Dropbox 对 SQLite 活动文件同步。

## 本版项目化功能（v0.13.0）

- 首页 `/projects`：项目列表、新建、编辑、归档、恢复与软删除；旧数据自动关联至默认项目，不复制原始文件。
- 项目内数据页：显示已登记的数据资产和导入记录，上传后展示工作表及样本结构诊断，选择候选表并可选择性请求大模型辅助，审批后生成不可变数据版本。
- 数据记录只在项目 BI 页面中打开；标准化版本走只读 Perspective 入口，显式创建可编辑工作副本才会产生新资产。
- 项目模型和任务页先接入现有运行记录与插件，尚不包含跨项目共享认证和企业多用户权限管理。
- BI 启动门禁会校验 JS/CSS/WASM、源码指纹、DuckDB/Arrow/Perspective Python 导入；缺少离线资源或构建依赖就阻断启动，不静默改用基础 BI。

进一步的复杂表自动切分、LLM 语义标注量化、原生 BI Chrome 编辑/回滚/重连和生产级完整离线发行仍需专项验收。详见 `docs/PROJECT_WORKFLOW_V0130.md`。

## 既有研究功能（第四阶段）

在 `/research` 使用：

1. 从已导入的真实 Excel/ZIP 工作表建立待审核记录；业务专家可逐条审核，或下载 CSV 模板后批量回填。规则建议不算人工真值。
2. 业务专家确认后，建立固定种子的配对 A/B 方案。每个案例分配给两名独立复核者，规则与模型组的顺序交叉；模型组需要真实 API 才能执行。系统记录实际提交字段更改、完成率、耗时和模型 Token；缺少配对实测时不计算收益。
3. 在统一数据资产中选择具有真实 `available_at`、唯一来源和口径的连续月度序列，进行有限预算的滚动回测、滞后特征回归与基线比较，并输出逐窗口诊断和完整性哈希。
4. 如提供同资产版本的状态—控制—维修机理证据，可关联一阶动力学辨识报告；未通过设备验证的机理结果不会作为预测模型特征或自动部署。

当前上传的年度销量数据可以建立**待人工复核**的语义样本池；如业务方未提交人工确认和真实模型 API 调用，不应报告真实语义准确率、成本或人工效率增益。

## 源码目录

- `src/timepilot/`：FastAPI、数据管理、BI、Agent、预测和机理算法。
- `frontend/native-bi/`：Perspective 前端源代码及其 JS 测试。
- `skills/`：工程内的领域工具执行说明（受控工具在 Python 注册表中实现）。
- `scripts/`：内部构建、验收、版本核验及源文件修复。
- `tests/`：Python 与 API 回归。
- `docs/`：详细数据契约、交付说明及实验约束。
- `bootstrap/`：隐藏配置文件的可见校验副本。

版本与验收说明见 `docs/RESEARCH_STAGE4_V0120.md`，入口迁移表见 `docs/ROOT_ENTRY_MIGRATION.md`。历史研究功能的默认本地模式未启用身份鉴别；v0.21.0 现支持显式企业模式和项目级租户授权，详见本文件末尾的安全与验收边界。公网部署仍需专门安全审查。

## v0.14.0：项目异步智能导入

项目主页 `/projects` → 数据管理 → 上传 ZIP 或多工作表 Excel。文件接收后立即得到可恢复的任务 ID；后台执行结构发现。完成后可查看业务字段画像、人工审核语义解释、生成不可变版本，并使用项目内的正式 BI 工作台查看记录。已保存的语义方案可重新打开。

运行仍使用现有 Python 环境，根目录只保留 `TIMEPILOT.sh` / `.bat`。单入口 `accept projects` 增加了异步上传、字段画像和语义建议校验。源码包不含 Perspective 编译产物；完整 BI 仍以离线构建及真实 Chrome 验收为前提。

详见 `docs/PROJECT_WORKFLOW_V0140.md`。

## v0.15.0：统一项目 BI 与模型运行

项目 → 数据管理 → 上传 → 结构审核 → 标准数据资产 → 正式 Perspective BI → 保存视图 → 预测草稿 → 训练数据 → 模型运行。旧数据页面已退出主工作流，兼容 API 保留。

保存视图保留当前数据版本、筛选条件、Perspective 布局和源数据摘要；历史视图不会随项目当前版本变化。预测草稿记录数据适用性检查，建模提交接口校验原视图与训练数据、算法和回测参数一致。独立后台心跳及逐工作表检查点用于恢复异常中断的 XLSX 结构扫描；任务异常最多重试三次，未完成的单张工作表需要重新扫描。

从 v0.14.0 升级无需移动原始资产；请先备份 `TIMEPILOT_HOME`。项目数据库增量迁移，另有旧视图归属安全回填。测试与 API、操作步骤详见 `docs/PROJECT_BI_WORKBENCH_V0150.md`。本包仍为源码包，正式 Perspective 离线资源需要构建并通过目标 Chrome 验收。


## v0.16.0：通用字段识别、数据资产与 Agent 只读目录

项目导入支持根据原始文件发现任意列名、数据类型、数值指标候选、时间/标识/事件字段，字段画像使用可搜索表格展示，代码字段不虚构业务名称。用户可以修正工作表表头并人工确认关键角色；通用发布保留全部源列与秒级时间，在完整数据上计算缺失率，生成不可变版本后直接注册 Native BI。原预测专用数据链路继续兼容。

新增 `/api/v1/projects/{pid}/data-catalog` 和字段结构检查、通用版本发布/查询接口；Agent 可通过白名单只读工具发现项目数据和查看 schema，不返回原始样本行。对格式上限和无法识别的业务含义明确提示，不静默截断字段。

**已知边界：** 通用资产原生 BI 视图尚未直接接通预测专用的视图→草稿→模型流程；发布阶段尚无逐块断点恢复；当前本地开发环境缺少完整原生 BI 的真实浏览器验收。详见 `docs/UNIVERSAL_DATA_WORKFLOW_V0160.md`。

## v0.17.0：通用资产到预测引擎的可复现闭环

项目正式 BI 增加“从当前数据建立模型”：从已发布通用资产和保存视图中的**明细行级筛选**创建项目建模草稿，自动建议时间、设备、目标和采样频率，人工确认数据可用时间与单目标限制后执行全量预检查；通过后生成不可变训练数据并入队现有滚动回测引擎。模型管理统一展示新旧草稿和运行来源。已完成方案可克隆为独立复现实验，保持源版本和筛选摘要一致，重新审核、运行与比较结果。Agent 新增通用建模画像、任务建议、草稿检查与受控创建工具。

支持现有引擎的月/日/小时数据；分钟/秒数据、不规则间隔、没有时间单位的整数索引暂不能直接运行，且不会自动填补、重采样或静默丢弃记录。训练一次最多 20 万条筛选后明细；温度预测仍为单目标历史建模。完整操作、字段映射、时间泄漏保护、评估协议、API 与验收边界见 `docs/DEVICE_TEMPERATURE_MODELING_V0170.md`。


## v0.18.0：工业多变量、分钟重采样与质量分类

同一项目数据资产可继续在正式 BI 内保存不同设备/工况筛选。新增从分钟/秒到小时/日的显式可审计聚合，每个桶保留原始记录量、覆盖率、源数据可用时刻；缺失周期、重复时间、工况混杂时阻断，避免对齐误差与未来泄漏。新增 `historical_exog_ridge` 真正使用目标和其他传感器的历史特征；维护事件、工况类别及明确提供的物理上下界/步长约束接入统一合同，并进行滚动测试和未来预测检查。

新增 `binary_logistic` 贴片二分类插件，明确决策时刻特征与标签成熟时间，依时间切分训练/验证/测试集，验证集选择满足最小召回率的阈值，测试输出混淆矩阵、召回率、误剔率等；项目模型页提供审计/评估 API 与受控的人工复核建议。异常检测及可学习机理模型继续保留兼容任务契约，**尚未实现真实执行插件**；不含产线自动剔除接口。详见 `docs/INDUSTRIAL_MODELING_V0180.md`。当前交付仍为源码包，正式 BI 的编译资源和目标机浏览器验收需另行完成。

## v0.21.0：统一资产、数据治理与自动构建

- 正式启动 `bash TIMEPILOT.sh` 集成 Perspective 依赖检查、首次自动安装前端依赖、源码指纹校验、构建以及异常安全退出，不修改已有 Conda 环境。启动前对 SQLite 元数据库执行完整性检测与幂等快照。
- 项目数据目录新增稳定资产 ID、字段结构、版本比较、可修改显示名称与源文件哈希。相同源文件/字段确认方案改名后复用已有不可变数据版本；BI 内部 Parquet 仍按需物化，不直接修改原始 CSV。
- 新增服务端数据范围预览及冻结 `DataSelection`；模型任务携带冻结选择，准备阶段复核数据版本、来源哈希、筛选行数和质量阻断项。
- 新增项目数据质量规则（必填、唯一键、值域、时间重复/时间逆序、采样缺口）、逐行来源位置、人工处理原因、质量检查历史和只读审计；Agent 工具只能按项目范围受控访问。
- 通用发布进入后台工作队列，按 2 万行一块计算 SHA-256 并写入可恢复检查点；BI 注册及质量检查失败时允许重试。浏览器关闭不影响已提交任务。
- 项目数据管理页显示版本、质量、任务状态和治理入口；正式 BI 推荐合适的时间趋势/设备对比预设，收束开发型编辑工具，区分图表临时筛选与真正参与建模的行级筛选。
- `bash TIMEPILOT.sh backup --output /absolute/path/backup.zip` 创建并校验 SQLite 一致性快照和文件数据压缩包，备份恢复仍需运维人工确认，**不会自动覆盖源数据**。

接口、审阅结论、验收边界和回滚建议见 `docs/DATA_PLATFORM_UNIFICATION_V0201.md`。本地单用户模式尚未提供企业级统一身份认证、分角色授权和多租户安全隔离；不要直接部署至公网。

## v0.21.0：企业项目隔离、工业 BI 与生产级验收

企业模式需离线创建租户管理员，显式启用 HTTPS 对外来源。受控身份、租户角色、项目成员、操作审计和未授权 HTTP/WebSocket 阻断参见 `docs/ENTERPRISE_BI_V0210.md`；旧版本文档中的“仅本地研发配置”是历史说明。

- `/login`：企业账号登录；`/identity`：管理员创建/停用用户、授权项目成员、查阅审计。
- 项目工业 BI：设备温度趋势、维护事件、工况切换、工艺质量及设备对比，绑定版本哈希保存视图。
- `bash TIMEPILOT.sh dedup scan`：检查完全相同 Parquet 不可变快照；`--apply` 才使用内容寻址硬链接执行去重。
- `bash TIMEPILOT.sh accept production-recovery`：100 万行 CSV，真正杀进程、续跑及 SHA-256 一致性复核。
- `bash TIMEPILOT.sh accept production`：真实 DuckDB 10 万/100 万/1000 万行筛选、聚合和并发压测。
- `bash TIMEPILOT.sh accept enterprise`：真实 HTTPS 及本机 Chrome 多租户验收；`accept native`：真实 Perspective WebSocket/Chrome 验收。

**交付边界：** 开发环境能够测试真实百万行发布恢复和 HTTP 身份隔离，但缺少完整 DuckDB/Perspective 与可访问本地 HTTPS 的 Chrome 时，会将集成验收记录为 `blocked`。企业模式仍是单实例本地身份体系，不包含 SSO/MFA 或物理租户隔离；公网生产前应完成专门安全评估。源代码 ZIP 不含预编译 JS/WASM，首次启动自动构建，或者在发布机器生成完整离线发行包。

### v0.24.6 BI 交互验收

```bash
bash TIMEPILOT.sh accept bi-ux-v0246
bash TIMEPILOT.sh accept bi-ux-v0246 --mode configured
```

第一条验证源码、图表系列保留逻辑、表单、浮窗和帮助弹层；第二条还要求目标机本地真实 Perspective JS/WASM、DuckDB、PyArrow 和系统 Chrome，环境不齐全时明确阻断。
