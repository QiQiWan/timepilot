# TimePilot v0.39.1 — 统一监督模型评价、运行归档与系统审阅

## 1. 本版本的实际能力

v0.39.0 的 `ModelTaskSpec` 负责四类任务的 Y/X、版本和审批。v0.39.1 在此基础上为 `tabular_regression`、`tabular_classification` 增加**真实运行**，保留 `continuous_forecast` 和 `classification` 的原时序执行器。

```mermaid
flowchart LR
A[业务任务和人工确认 Y/X] --> B[版本化 ModelTaskSpec]
B --> C[预检查和审批]
C --> D[SQLite 持久模型运行队列]
D --> E[独立 Feature Worker / 内置 Worker]
E --> F[相同分折: 常数基线 / 线性 / 随机森林]
F --> G[有来源 SHA 和折哈希的评价记录]
G --> H[项目工作台查看 / 进入 FeatureLab 深入分析]
```

**评价与训练边界**：训练在真实完整的已发布数据上执行，不使用仪表盘抽样值。算法固定种子 42，折间使用同一数据切分和 train-only 插补/标准化。结果保留折大小、折索引 SHA256、模型与基线指标、训练任务与数据版本哈希、结果 SHA256；不存储原始样本、逐行预测或可部署的 pickle/权重。`best_observed_model` 是在当前验证集上的观察结果，无独立业务效益证据。

## 2. 任务类型与执行范围

| 类型 | 本版可直接运行 | 验证方式 | 指标 |
|---|---|---|---|
| 普通回归 | 是 | K 折、设备/批次分组 K 折、按时间留出 | MAE、RMSE、R²、WAPE |
| 普通二分类 | 是（正负标签需人工映射） | 分层 K 折、分组 K 折、按时间留出 | Average Precision、Balanced Accuracy、Recall、Precision |
| 时间序列预测 | 保留既有建模流程 | 滚动验证与 PIT | 既有建模工作流的指标 |
| 工序提前分类 | 保留既有建模流程 | 时间前推、标签成熟时间 | 既有分类工作流的指标 |

运行预算：每个普通模型任务最多 20,000 行、32 个 X。大表引导先生成审阅后的数据资产/降维；不静默截断样本。非法 Y/X、非有限数值、单类折、时间重叠及非数字原始 X 将被阻断。目标评估只基于已批准版本；任务修订使历史结果变为 `superseded`。

## 3. 代码级改造

| 文件 | 作用 |
|---|---|
| `src/timepilot/model_task_execution.py` | SQLite 持久队列、幂等提交、过期租约接管、训练折与固定模型比较、原子结果写入 |
| `src/timepilot/model_task_api.py` | 新增运行创建、列表、详情三个项目级接口 |
| `src/timepilot/api.py` | 创建执行服务、整合内置 Worker，保持已有路由兼容 |
| `src/timepilot/worker_runtime.py` | 独立 Feature Worker 也处理统一监督建模队列，保留角色租约 |
| `src/timepilot/static/modeling-ui.js` | 新增标签映射、确认并批准、模型运行、结果查看及 FeatureLab 深入分析入口 |
| `tests/test_model_task_execution_v0391.py` | 实际 sklearn、真实 SQLite、真实应用 API 与源 CSV 联调，含租约/修订/跨项目反例 |
| `scripts/release_gate_v0391.py` | 本版本独立源码门禁及运行环境阻断结果 |

新建 `model_task_runs` 表，**只做增量初始化**，不删除或修改原有数据和版本。`run_key` 将任务修订、审批摘要、模型执行版本绑定为唯一标识；同一个有效请求多次点击只创建一个运行。Worker 对结果提交必须匹配持有者、代数与未过期租约。

新增 API（相对于 `/api/v1/projects/{pid}/model-tasks/{tid}`）：

```text
POST /runs                  -> 202, queued or existing run
GET  /runs                  -> historical runs
GET  /runs/{run_id}         -> current state, source identity, result and hash
```

## 4. 可靠性与已知限制

- 跨项目任务访问拒绝；任务修订、审批或源 SHA 变化后禁止新运行，历史结果显示为 `superseded`。
- SQLite 租约过期后可以重新执行纯计算，但旧执行者不得再次提交结果。
- 全部计算使用固定超参数的 Ridge/LogisticRegression/RandomForest 基线，不宣称算法在真实 MES 数据中带来收益。
- 本版还未提供统一可部署模型包、模型注册/推理服务、独立盲测或完整自动超参数搜索。时序模型执行器、UI 与 FeatureLab 仍保留各自适配流程。
- 非基础版本使用已存在的 DuckDB 版本化覆盖视图；该物理路径尚待 DuckDB 环境验收。
- 真实 Mac Python 3.12.12、Perspective/Chrome、完整离线运行包门禁继续按 `BLOCKED` 独立保留。源码测试不能替代运行环境验收。

## 5. 操作与验收

```bash
bash TIMEPILOT.sh source-check
bash TIMEPILOT.sh release-gate --mode source
bash TIMEPILOT.sh release-gate --mode physical
bash TIMEPILOT.sh release-gate --mode browser
```

进入项目模型管理，点击新建任务，选择一般回归或一般分类，确认 Y、数值 X、分类标签映射及分折规则，保存并预检查，通过人工批准后点击“开始模型对照”，在 Worker 处理后点击“刷新运行结果”。任务保持只读结果和审计记录。使用 `TIMEPILOT_HOME` 指向原有工作目录，不重置 SQLite 或原始资产。

## 6. 后续优先级

1. **P0：真实物理门禁**：真实 Mac 的 DuckDB/Parquet、时间留出/分组数据全链、Perspective WebSocket 以及完整离线安装/回滚。
2. **P0：训练产物正式冻结**：保存最优候选模型在独立测试集之前的训练折参数与模型签名，支持安全模型注册和复现实验。
3. **P0：时序适配统一**：将原有时序草稿与 `ModelTaskSpec` 的审批、来源、训练运行编号彻底统一，避免双份审批状态。
4. **P1：真实产业样本**：销售预测 WAPE、设备预测 MAE 与标签成熟的质量分类 PR-AUC，跨设备/时间/工况保留集与 PIT 逐样本审计。

## 7. Verified evidence and release disposition (2026-11-20)

| Check | Verified result | Scope |
|---|---|---|
| `source-check` | PASS | SHA256-signed source files; Python 3.13.5 test container |
| Model execution + contract/API tests | 21 passed | Real SQLite, actual scikit-learn fit/score, published CSV, task revision/fencing; including result SHA tamper rejection |
| Worker + model combination | 42 passed | Focused Python regression (before extra integrity test) |
| Frontend Node tests | 160 passed | Full available Node suite |
| Mac source-repair compatibility | 7 passed | Existing v0.38.8/v0.38.10 fixtures |
| Publication reconciliation | 12 passed | Isolated legacy test |
| ZIP independently extracted | Source check PASS; Python 9 and Node 8 PASS | Recorded after final archive generation |
| Full `release-gate --mode source` | INCOMPLETE (timeout) | Four initial stages passed; no consolidated PASS receipt; do not infer full-suite success |
| `physical`, `browser`, `offline` | BLOCKED (exit 2) | Missing exact target Python 3.12.12, DuckDB/PyArrow and compiled Perspective/wheelhouse |

User-facing model comparison is an evaluated retrospective model run. There is no deployment-ready predictor artifact or production prediction benefit claim. Time-aware tasks retain the original specialized workflow and have not been merged into the tabular executor.
