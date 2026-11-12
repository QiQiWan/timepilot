# TimePilot v0.30.1—v0.31.0 源码迭代与发布审阅

版本：v0.31.0；基线：v0.30.0；日期：2026-11-12。

## 一、验收结果与适用边界

本次完成四阶段源码迭代，原有项目目录、DuckDB/Parquet、Perspective 资产与 FeatureSet 保持兼容。数据库版本以加性迁移从 33 升级到 34，旧 SQLite 项目数据库应由迁移工具先备份再升级。

**交付状态：可验证源码。当前不满足生产离线发行条件。** 当前执行环境 Python 3.13.5，缺少 DuckDB、PyArrow、tsfresh、Featuretools、Perspective 的已编译 JS/CSS/WASM 及受审查的 `package-lock.json`；也无可核实的企业产线测量、质量标签或真实业务 Agent 人工复核记录。涉及这些依赖的真包/真实产线/浏览器验收返回 `blocked` 或跳过，不得算作通过。实际可启动的离线发行包只能在具备锁文件与完整依赖的构建机器完成；本交付的 ZIP **为 SOURCE ONLY**。

## 二、逐版本实施情况

### v0.30.1：发布与 CI

- 将 Python 包、HTML 静态资源、前端 package.json、测试版本断言统一至 0.31.0。修正模型工作流、原生 BI、发布检查中的旧版本硬编码。
- `scripts/launcher.py start` 仅调用 `ensure_native_bi.py --status-only`，禁止启动时自动 npm 安装或构建；启用的完整 BI 运行前检查 Python 环境与 JS/WASM 发布校验。
- `scripts/BUILD_OFFLINE_RELEASE.sh` 在构建机运行，必须存在经过审核的 `frontend/native-bi/package-lock.json`，使用 `npm ci`；校验 JS/CSS/WASM 才允许执行 `--with-native-bundle`。`--bootstrap-lock` 仅为有网络构建机器创建锁文件的显式维护动作，锁文件需审查并纳入源码版本控制。
- CI：Python 3.12.12 完整测试、可选真包任务、Node 24 固定锁文件构建、真实浏览器与 WS 检查分层；缺少锁文件时 CI 明确失败。**CI 配置已修，完整线上 CI 尚未成功运行。**
- `featurelab30_e2e.py` 新增 `--backend-only` 与 `--audit-home`。后端模型评价完毕、浏览器未验收时，默认返回非零；仅显式后端阶段验收可报告部分完成。

### v0.30.2：高级算法可靠性

- `feature_advanced.py` 对 tsfresh 每个实例要求至少 2 次观测、至少 2 个不同时间戳、跨度至少 1 秒、每个输入数值测点有效覆盖率不低于 70%；仍按 `event_time` 和 `available_at` 同时执行 PIT。
- 特征预算基于预设计算器及输入字段估算，按算法特性区别上限；Featuretools 在 JOIN 前计算关联扇出；计算时间、资源与候选量超限时拒绝。
- `feature_advanced_isolation.py` 在独立进程中进行超时与进程 RSS 内存监控；高级服务限制并发任务与同用户提交，独立任务上下文不再使用共享项目 ID/预算状态。
- 可选真包测试可在目标环境执行 `PYTHONPATH=src python -m pytest -q tests/test_featurelab30_optional_engines.py`；如果因找不到依赖而 skipped，则**真包验证没有通过**。

### v0.30.3：高级特征 PIT 建模闭环

- 新增 `feature_advanced_join.py`：高级候选需要明确来自现有持久计算任务，经人工选择列并确认后，生成新关联任务。`AdvancedJoinPlan` 保存真实预测实例来源 SHA256、已确认字段 ID、契约修订和候选产物 SHA256。工作任务启动时重复校验全部指纹。
- `pit_join` 要求预测实例键唯一、候选键唯一、每条候选的 UTC cutoff **严格等于**相应预测实例时间，不得晚到、不允许缺失候选、默默扩行或覆盖原始列；无穷值拒绝，特征重命名并保留来源映射。
- 新 Parquet 产物绑定内容 SHA256、BI 项目原生数据资产和评价任务；模型结果记录**实际读取**产物 ID 与 SHA256，防止只靠文件名称误判一致性。训练折内质量筛选和特征贡献对照沿用 v0.29 评价器。
- 数据库新增 `feature_advanced_joins` 与 `feature_advanced_join_evaluations` 两个表（Schema 34）；API 增加验证、任务、结果和模型评价入口；UI 可选择预测实例、候选列、人工确认并进入同源模型对照。
- 该链路的字段/权限/PIT/幂等契约已经通过模拟和纯函数测试；实际大数据 Parquet + BI + 浏览器尚无可确认的运行证据。

### v0.31.0：业务流程及正式验收

- 新增 `feature_workflow.py` 的 5 步工作区状态概览：确认数据、生成候选、正式计算、实验评价、审核发布，状态来自 SQLite 的项目资产证据；刷新后仍可恢复，源哈希与项目权限隔离。
- 前端高级发现新增 `选择高级候选→校验关联→人工确认 PIT 物化→模型评价→BI 资产` 的入口与任务状态。
- `scripts/featurelab31_agent_business_acceptance.py` 读取真实 Agent 任务、步骤日志和业务人员逐项复核 CSV，计算完成率、业务接受率、人工修正数量与实测节省工时；无实际记录拒绝通过，不填充模拟统计。
- `scripts/featurelab31_release_gate.py` 同时需要真实产线同源后端、真实浏览器双面板、tsfresh 和 Featuretools 真包执行、经人工审核的 Agent 业务任务。严格模式缺少任一回执返回退出码 2；可选显式 `--allow-advanced-pending`、`--allow-business-pending` 仅供**限定范围的集成检查**，报告标记 partial，不能用于正式发布。

## 三、用户端完整使用步骤

1. 进入项目，导入原始设备/批次/质量数据，在数据契约中确认设备编号、业务事件时间、数据可用时间、预测时点、目标标签与标签可用时间。
2. 打开“特征工程→快速生成”，生成普通候选、预览质量指标；必要时使用“高级发现”生成 tsfresh 或 Featuretools 候选。
3. 对高级候选选择预测实例资产及真实主键/预测时点字段，先验证，检查 PIT 结果与估算资源消耗，再手工确认。
4. 等待 Parquet 物化成功，检查来源 SHA256、产物 SHA256，进入模型实验比较原始/全部/筛选三组，查看设备、工况切片和跨时间漂移。
5. 在 BI 中打开原始和物化两个同源资产；确认真实图表、WebSocket、刷新恢复；结果需经人工业务审阅后发布。技术上的成功不代表业务收益已证明。

## 四、复现命令

在目标 Python 3.12.12 已有环境中（无需创建新虚拟环境）：

```bash
bash TIMEPILOT.sh db-check
bash TIMEPILOT.sh source-check
bash TIMEPILOT.sh accept feature-lab-v031 --mode offline
```

`offline` 只做源码/单测/Node 检查，不会创建假的业务证据。

完整真实验收须配置并确认以下环境变量：

```text
TIMEPILOT_FEATURELAB_QA_CSV                      /path/to/exported_measurements_and_labels.csv
TIMEPILOT_FEATURELAB_QA_PROVENANCE               业务方导出说明，需另附 MES/质检导出审计记录
TIMEPILOT_FEATURELAB_QA_EVENT_TIME               event_time
TIMEPILOT_FEATURELAB_QA_AVAILABLE_AT             measurement_available_at
TIMEPILOT_FEATURELAB_QA_PREDICTION_TIME          prediction_time
TIMEPILOT_FEATURELAB_QA_LABEL_AVAILABLE          label_available_at
TIMEPILOT_FEATURELAB_QA_LABEL                    quality_flag
TIMEPILOT_FEATURELAB_QA_DEVICE                   equipment_id
TIMEPILOT_FEATURELAB_QA_REGIME                   operating_regime
TIMEPILOT_FEATURELAB_QA_MEASURE_A                pressure
TIMEPILOT_FEATURELAB_QA_MEASURE_B                thickness
TIMEPILOT_FEATURELAB_QA_TIMEZONE                 Asia/Shanghai
TIMEPILOT_FEATURELAB_QA_OUTPUT                   /private/path/to/new/qa_workspace
TIMEPILOT_FEATURELAB_QA_AGENT_CATALOG            /private/path/to/real/project.sqlite3
TIMEPILOT_FEATURELAB_QA_AGENT_PROJECT            actual_project_id
TIMEPILOT_FEATURELAB_QA_AGENT_ANNOTATIONS        /private/path/to/agent_review.csv
```

创建**空白业务复核模板**：

```bash
python scripts/featurelab31_agent_business_acceptance.py --template /private/path/to/agent_review.csv
```

进行完整端到端验收：

```bash
bash TIMEPILOT.sh accept feature-lab-v031 --mode configured
```

验收采用隔离且初始为空的目录，不删除正式业务资产；会输出 `backend.json`、`advanced.json`、`browser.json`、`business.json`、`release_gate.json`。若只想复核部分环节，分别调用 `scripts/featurelab30_e2e.py --backend-only --audit-home ...`、`scripts/featurelab31_real_advanced_e2e.py`、`scripts/featurelab31_browser_acceptance.py` 与 `scripts/featurelab31_release_gate.py`。低于 10 个真实 Agent 任务、缺少 Perspective JS/WASM、缺少可核实的质量标签时，发布门禁为 `blocked`。

前端构建机器（Node 24，要求已审查 npm 锁文件）：

```bash
bash TIMEPILOT.sh build
```

只有 `package-lock.json` 和真实 Perspective JS/CSS/WASM 校验成功之后，才能创建可运行的 `TimePilot_v0.31.0_FULL_OFFLINE.zip`。未通过时只允许交付独立标记的 source-only ZIP，不应称其为“可直接离线双击运行”。

## 五、未闭合项及风险审阅

| 级别 | 未闭合问题 | 当前证据 | 交付要求 |
|---|---|---|---|
| P0 | 真正可启动的离线运行包 | 本机无经审查 `package-lock.json`、原生 BI 编译资源和真包依赖 | 有网络构建机产生锁文件并审查，固定 Node 24/Python 3.12.12，完成编译与洁净机安装 |
| P0 | 真实产线 PIT/监督对照/设备工况泛化 | 无真实可核实产线测量与质量标签 | 业务导出与来源证明、时点字段、至少 2 设备/工况及独立人工复核 |
| P0 | 原生 Perspective 双面板实际浏览器回执 | 当前无 JS/WASM、DuckDB/PyArrow/Chrome 实机验收 | 真实 WebSocket/双面板/刷新后回执、检查产物 SHA256 一致 |
| P0 | 真实 tsfresh/Featuretools 算法通过 | 本环境可选包缺失，专项真包跳过 | 在构建和运行环境执行真包计算、记录时间/内存/产物哈希 |
| P1 | 受控 Agent 真实业务完成率和节省工时 | 当前缺少审核后业务流程样本 | 至少 10 个真实任务、人工逐项确认、计算接受率与错改量并满足业务约定门槛 |
| P1 | 完整模型泛化 | 既有数值单测不构成产线泛化结论 | 跨时间、设备、工况及新批次独立保持集比较，报告置信区间和类别支持度 |

**结论：**v0.31.0 已实现源级版本收束、PIT 回接和可审计发布门禁。当前环境能完成源码、模拟接口及数值单测，完整离线与生产真实数据验收应保持 `blocked`，待目标机器补齐依赖和业务输入后才能标记正式完成。
