# TimePilot v0.30.0：高级特征算法、受控 Agent 与同源监督验收

版本：v0.30.0；源码基线：v0.29.0。2026-11-12。

## 1. 本次实际完成内容与验收边界

**已实现**：可选 tsfresh/Featuretools 高成本算子；两表预测实例—历史观测 PIT 聚合；服务端字段契约、哈希与预算核验；受控工具调用；持久化任务、隔离子进程超时、生成候选入 BI 资产；单个外部 CSV 从导入到正式物化、内置监督评价器和 BI DuckDB 视图的一键验收程序；新增 UI 页签与审计结果。

**尚无生产验收证据**：当前开发环境没有可以确认来源的产线测量值+质量标签，也没有 DuckDB、PyArrow、tsfresh、Featuretools、Perspective Python/JS/WASM 可用环境。因此，本次代码测试不能证明企业产线真实效果；`scripts/featurelab30_e2e.py` 已实现，但当前环境无法成功执行其真正的 SQL/Parquet/Perspective 后端步骤。可选算法仅完成 API/算法适配层测试和依赖缺失的可解释处理，真实算法测试标记为 skipped。独立的 Perspective 浏览器/WS 双面板回执仍须在构建资源齐备后实机执行。

真实企业数据与公开工业数据要严格区分。UCI SECOM 是有制造测量与合格/不合格标签的公开数据，但无法从其公开字段推导可靠的设备及工况编码；UCI Hydraulic System 数据来自试验台，有设备状态标签，不能当作企业车间实际产线数据。若选用公开数据开展复现，报告中必须标注来源和分组可用性，不得凭空填充设备/工况字段。

## 2. 功能和代码清单

| 模块 | 主要文件 | 已实现行为 |
|---|---|---|
| tsfresh 算子 | `feature_advanced.py` | minimal/efficient 两种预设，至多 8 个真实数值传感器，逐样本真实时间与可用时间校验；输出测点统计特征 |
| 多表 Featuretools | `feature_advanced.py` | 两源验证、实例-设备-预测时点与事件-设备-事件/可用时间关联；mean/max/min/std/count/sum 白名单聚合；每个预测实例 PIT 过滤 |
| 后台任务与权限 | `feature_advanced_service.py` | 项目编辑权限、字段 ID、不变来源 SHA256、契约 revision、绑定任务 request_id、结果审批前置 |
| 运行隔离 | `feature_advanced_isolation.py` | spawn 子进程、硬时间上限、单线程算法、结果 Parquet 原子保存、失败不接受残留文件 |
| 任务集成 | `feature_execution.py` | 使用既有租约/心跳/任务队列；失败标记可重试/终止，无静默成功 |
| HTTP & Agent | `feature_lab_v2_api.py`、`feature_tool_manifest.py`、`feature_agent.py` | `/advanced/*`，能力检查/结构化配方验证/显式确认后提交；禁止 Agent 任意 SQL、Python、主机路径及自动批准发布 |
| UI | `static/feature-advanced-ui.js`、`feature-shell.js`、`feature-lab2.css` | “高级发现”页签、按算法字段角色动态选择、来源/预算预检查、执行确认、任务及 BI 路由恢复、算法未安装提示 |
| 数据库 | `project_migrations.py` | Schema 32 → 33；`feature_advanced_artifacts` 审计表；保存候选与计算来源，不覆盖既有 FeatureSet |
| 同源工业验收 | `scripts/featurelab30_e2e.py` | 外部 CSV → 数据 PIT 预检 → 原始文件导入 → typed Parquet → 确认契约 → FeatureSet → 真实 Parquet 物化 → 真实 FeatureEvaluator 三折模型对照与设备/工况切片 → DuckDB 双视图校验 |

### 2.1 高成本算法的数据安全约束

1. `event_time <= prediction_time` 且 `available_at <= prediction_time`；tsfresh 若检测到违例则直接拒绝执行。多表聚合逐实例剔除不可用历史记录，并输出候选关联数与排除数，不允许按整表最大时点代替单实例计算。
2. 只能引用已核验、已确认的字段 ID 和数据角色。label、restricted、未经确认的时间、物理类型错误列一律拒绝进入算法输入。
3. tsfresh 按每个样本/循环键分组，要求每个键对应唯一预测时点。Featuretools 的关联键至少包含每条预测实例的唯一 ID、实体 ID 和预测时间。重复主键、类型规范化后重复主键、空实体被拒绝。
4. 默认上限：扫描 30,000 行、候选 JOIN 50,000 对、输出 80 列、最长 90 秒；绝对上限：200,000 行、200,000 对、256 列、240 秒。先计算关联扇出上界再 JOIN，超限时不执行。
5. 外部工具运行在独立进程，单线程，超时终止；原有 Worker 的租约/失败恢复仍有效。它没有获得任意代码执行权和批准发布权。
6. 高级结果为**待审核候选资产**。算法生成不等于有效预测特征，必须与标签/预测实例在 PIT 契约下确认关系并重新开展训练折内的评价。本版保留这一人工审核边界，未实现通用自动回接训练表的跨资产 Merge 及发布。

### 2.2 API

- `GET /api/v1/projects/{pid}/feature-lab/advanced/capabilities` — 实际运行环境算法是否安装；返回预设与安全约束。
- `POST /api/v1/projects/{pid}/feature-lab/advanced/validate` — 按已确认字段 ID 提交 tsfresh/Featuretools 配方，仅校验，不执行。
- `POST /api/v1/projects/{pid}/feature-lab/advanced/tool-call` — 工具 `feature.advanced.capabilities / .validate / .submit`；`.submit` 必须携带 `confirm:true` 和唯一 request_id。
- `GET /api/v1/projects/{pid}/feature-lab/advanced/jobs/latest?asset_id=...`、`GET .../jobs/{jid}` — 进度/异常/断点恢复。
- `GET /api/v1/projects/{pid}/feature-lab/advanced/artifacts/{jid}` — 核验后的候选资产、行列数、PIT 证据、来源/配方哈希和 BI ID。

### 2.3 依赖

TimePilot 原有 `requirements.txt` 不额外引入高成本算法。选择在现有 Python 环境中安装：

```bash
python -m pip install -r requirements-featurelab-advanced.txt
```

文件包含 `tsfresh>=0.21,<0.22` 和 `featuretools>=1.31,<2`。安装后运行真实集成用例：

```bash
PYTHONPATH=src python -m pytest -v tests/test_featurelab30_optional_engines.py
```

如果报告 `skipped`，说明算法未实际运行；不能将该结果计为计算成功。原生 BI 还需要在部署机器上完成 Perspective JS/WASM 资源构建以及浏览器连接验收。

## 3. 同一来源实际产线验收标准

`featurelab30_e2e.py` 要求一个**已由业务方确认来源**的实际 CSV，具备以下 9 列（可通过参数映射真实列名）：事件时间、测量可用时间、预测时点、标签可用时间、质量标签、设备 ID、工况、两个数值测量列。

最少 150 条、至少 2 台设备和 2 类工况；CSV 只用作输入，不覆写原件；每次使用 `TemporaryDirectory` 新建隔离项目和 SQLite/Parquet 目录。时间统一转换到 UTC；原始 CSV SHA256 与转换后 typed Parquet SHA256 分开存证。接受外部来源说明但暂时无法独立认证其真实性，报告明确记录这一点。

**命令示例**（替换为业务方实际列名和绝对路径）：

```bash
PYTHONPATH=src python scripts/featurelab30_e2e.py \
  --csv /data/production/measurements_quality.csv \
  --provenance 'MES 质检系统 2026年10-11月设备过程数据 业务人员确认导出' \
  --event-time event_time \
  --available-at measurement_available_at \
  --prediction-time prediction_time \
  --label-available label_available_at \
  --target quality_flag \
  --device equipment_id \
  --regime operating_regime \
  --measure-a process_pressure \
  --measure-b paste_thickness \
  --timezone Asia/Shanghai \
  --report reports/v030/same_source_e2e.json
```

注意：“业务已确认数据来源”的说明仍需结合文件导出日志/源系统记录复核。没有来源证明，不得升级为认证完成。工具不会自动下载、伪造或补齐缺失的生产记录。

验收结果须包含以下串联证据：`raw_csv_sha256` → `native_typed_source_sha256` → `FeatureSpecV2 task/PIT` → `feature_artifact_sha256` → 同一个 FeatureEvaluator 三个时间前推折及两个 slice 字段 → 同一物化 Parquet 的 DuckDB SQL BI 视图。模型会比较原始、全部派生、训练折选择三组输入，线性/随机森林可以选用；训练折内预处理，避免标签泄漏。

**完工门禁**：须同时看到 `external_source_pit_precheck`、`native_source_ingestion`、`confirmed_supervised_contract`、`pit_materialized_verified`、`same_artifact_supervised_evaluation`、`perspective_hosted_duckdb_views` 全部 `passed`。返回 `passed_backend_same_source_browser_pending` 表示服务器部分通过，仍须原生 Perspective 浏览器打开两个面板、筛选/图表操作、刷新恢复和 WS 渲染回执。这些证据不能由 Python SQL 视图测试替代。

在目标机通过统一入口：

```bash
export TIMEPILOT_FEATURELAB_QA_CSV='/data/production/measurements_quality.csv'
export TIMEPILOT_FEATURELAB_QA_PROVENANCE='MES系统产线实测数据，工艺组确认业务导出'
export TIMEPILOT_FEATURELAB_QA_EVENT_TIME='event_time'
export TIMEPILOT_FEATURELAB_QA_AVAILABLE_AT='measurement_available_at'
export TIMEPILOT_FEATURELAB_QA_PREDICTION_TIME='prediction_time'
export TIMEPILOT_FEATURELAB_QA_LABEL_AVAILABLE='label_available_at'
export TIMEPILOT_FEATURELAB_QA_LABEL='quality_flag'
export TIMEPILOT_FEATURELAB_QA_DEVICE='equipment_id'
export TIMEPILOT_FEATURELAB_QA_REGIME='operating_regime'
export TIMEPILOT_FEATURELAB_QA_MEASURE_A='process_pressure'
export TIMEPILOT_FEATURELAB_QA_MEASURE_B='paste_thickness'
bash TIMEPILOT.sh accept feature-lab-v030 --mode configured
```

## 4. 现阶段实际测试报告（运行环境的证据）

本轮运行环境：Python 3.13.5（目标生产环境应复测 Python 3.12.12）、Node 22.16.0、Chromium；sklearn、NumPy、pandas、pytest、Playwright 可用；DuckDB、PyArrow、Perspective、tsfresh、Featuretools 均不可用，PyPI/DNS 下载不可用。

执行分模块 Python 测试时，新增/历史回归 **40 passed，2 skipped**（后续发布流程如有更新，以独立发布 QA 文档最终计数为准）。`test_featurelab30_optional_engines.py` 中的两个库真实计算用例已准备，当前被明确跳过。前端使用本机 Chromium 和模拟 API 审阅了两个主要状态：多表字段配置、tsfresh 产物与 BI 链接，截图单独保存于交付目录外，**不作为真实计算证明**。

审阅发现并修复：

- 原回归测试将旧 Schema32 固定为最新值，迁移至33后已更新预期；增加从32到33的数据库保留记录测试。
- 多表预测实例主键可能在转字符串后碰撞，现对规范化后主键再次检查，拒绝混用数字/字符串编码的风险。
- 表单校验后刷新时可能丢失已选测点/实例字段，新增草稿状态保持。
- 实际 BI DuckDB 视图建表时使用参数化 DDL 可能不兼容，改为仅从内部可信路径生成转义字面量。
- 真实产线验收脚本只接受显式 CSV + 来源说明，不自动使用生成样例作为产线数据。

## 5. 未闭合项与后续验收次序

1. **G1 实际产线数据来源**：获取业务方导出的测量与质量标签，核查测点时间、数据实际到达时间、标签可用时间，以及 device/regime 的真实业务编码。不允许补造分组；如果数据没有工况，只能验收已具备切片维度。
2. **G2 原生 DuckDB/PyArrow/Perspective 实际运行**：在部署机执行 `--mode configured`，逐阶段检查 SHA/PIT/Parquet/监督折。若任一阶段失败，报告为 failed/blocked，不能签收。
3. **G3 tsfresh/Featuretools 真包集成**：安装可选依赖，运行两个真实集成测试，在实际业务字段上审阅列数量/执行时间、PIT 候选对数和缺失率。单次资源预算先采用默认上限，避免生产机资源争用。
4. **G4 原生 Perspective UI/WS**：在现有浏览器（不新增下载）实机打开原始与派生面板，实际筛选与绘图；保存并刷新恢复，验证回执，才可标记整体 E2E 通过。
5. **G5 高级候选回接监督模型**：目前高级引擎输出可查看、可审计的项目原生候选资产。还需开发专门按预测实例键对齐的跨资产合并、PIT 标签可用时间证明及外层训练折选择，避免高级时序聚合结果未经核验直接进入模型或发布。

**产品判断**：v0.30.0 已形成可选算法与受控执行的源码骨架，能让用户查看候选并计划完整工业验证。算法真实效果、产线模型收益和 BI 全链路暂不能作生产签收结论。

## 6. 发布前复审增补（2026-11-12）

- **并发隔离修复**：`AdvancedService._load()` 显式接收 `project_id` 与 `max_rows`；删除共享服务对象上可被其他任务覆盖的 `_pid`、`_limits` 临时状态。新增项目 A / 项目 B 连续调用回归用例，验证项目上下文不串用。
- **时间语义修复**：多表 Featuretools 的决策表与事件表分别使用各自已确认的 IANA 时区。无时区 DuckDB 时间戳先按字段契约定位到本地时区，再转换成 UTC；带时区时间直接转换成 UTC。夏令时歧义拒绝执行。新增 UTC 与 Asia/Shanghai 时区差异导致的晚到事件过滤用例。
- **同源物化审计**：监督模型报告写入 `evaluated_artifact_id`、`evaluated_artifact_sha256`、`evaluated_contract_revision`；验收器要求与正式物化产物完全相等且 3 个时间前推折存在，避免源文件相同但训练输入不同的误判。
- **回归计数更新**：最终以发布包外的 QA 记录为准。旧文档中“40 passed”是中间测试记录，未计入本次增加的并发与跨时区用例。

剩余限制不变：实际产线来源、tsfresh/Featuretools 真包执行、DuckDB/Parquet 数值闭环和 Perspective 浏览器渲染都未在当前容器完成，不得按生产验收已通过对外报告。
