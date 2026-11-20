# TimePilot v0.39.3｜四类建模任务统一执行与证据审阅

## 1. 审阅结论

本版在 v0.39.2 的 ModelTaskSpec + TemporalTaskBridge 上增加统一 ModelRun 入口，保留两套经过测试的训练执行器：普通回归与普通二分类使用 `ModelTaskRunService`，连续时序预测与工序提前分类使用 `ModelingWorkbench`/原 PIT-aware 执行器。用户通过同一模型工作台提交、刷新和下载评价摘要；受控桥接的时序任务可一次操作完成旧执行器的审批、物化与入队，但必须在前端再次明确确认执行。

**当前仍不具备**：统一的物理训练内核、可部署模型权重、跨所有执行器统一的折级预测明细、真实生产增益、目标 Mac + DuckDB + Perspective 离线发布的完整 PASS 证据。时序任务有真实聚合指标和验证协议，其折级 SHA 仍需由原执行器产物补齐，不伪造为空的折明细。

## 2. 逐条问题、代码修改及验收

| 审阅问题 | 代码修改 | 验收要求 | 残余风险 |
|---|---|---|---|
| 普通/时序执行入口不统一 | `model_task_unified_runs.py`，`model_task_api.py` | 统一的 `GET/POST .../execution` 可作用于四类任务 | 保留旧 API 兼容调用 |
| 双重审批造成复杂操作 | 统一确认后，受控传播批准到 legacy 计划，执行前重验来源、PIT 合同及草稿预检查 | 未确认调用返回 409，不创建训练数据；有确认才批准、物化、提交 | 旧 API 仍能独立操作自己的合法草稿 |
| 同时操作可能重复提交 | 使用任务 ID 生成跨进程 OS advisory 锁，底层入队维持原幂等语义 | 并发提交复用单一运行；强杀进程后锁释放 | 多主机共享文件系统锁语义未验收 |
| 外部提交完成、回执写入前崩溃 | SQLite 新增 `model_task_execution_receipts`，按任务修订和执行器唯一；重试复用底层运行 ID | 补写唯一引用、校验 SHA，不重新训练或悄悄覆盖记录 | 旧执行器内部孤儿资产仍须独立物理审计 |
| 数据变化后旧指标仍可能可见 | 读取时核验来源 SHA、Spec revision、approval SHA、绑定、结果 SHA | 修改修订后统一页面清空指标与导出入口，旧回执可审计并标 `current=false` | 来源文件校验的 I/O 负担待真实性能测量 |
| 指标/折来源结构不一致 | 统一 `model-run-v1`（`metric_sets`、`folds`、`validation_protocol`、hash） | 两个执行器各自真实结果返回，时序折记录未提供时为空 | 同源同折模型直接比较尚未实现 |
| 报告缺少统一追溯 | 只读 `/execution/report` 生成 Markdown，包含任务、数据版本、训练/执行器及数值哈希 | 仅成功且当前的运行可以导出；禁止输出原始预测、模型权重 | 后续增加持久报告资产及可信签名 |
| 工作台冗余操作 | `modeling-ui.js` | 同一页面运行/刷新、展示验证协议和指标、导出 MD；高级 FeatureLab 入口保留 | 目标 Chrome/Perspective 完整交互尚未验收 |

## 3. 数据库与运行状态

新增**增量** SQLite 表 `model_task_execution_receipts`，字段：`task_id`、`project_id`、`revision`、`executor`、`run_id`、`spec_sha256`、`approval_sha256`、`source_sha256`、`submission_sha256`、`created_at`。约束 `PRIMARY KEY(task_id,revision,executor)`。这是执行引用账本，训练结果仍存于既有引擎，避免复制数据与制造新的“双结果事实源”。现有 `TIMEPILOT_HOME` 无须清空或重建。执行入口在同一项目中复用签名，出现冲突时拒绝并要求对账。

统一状态：`needs_review`、`needs_approval`、`needs_link`、`ready_to_run`、`queued`、`running`、`succeeded`、`failed`、`stale`。只有通过来源、审批和结果哈希复核的 `succeeded` 才能展示 `metric_sets` 和下载报告。

## 4. API 合同

```text
GET  /api/v1/projects/{pid}/model-tasks/{id}/execution
POST /api/v1/projects/{pid}/model-tasks/{id}/execution
GET  /api/v1/projects/{pid}/model-tasks/{id}/execution/history
GET  /api/v1/projects/{pid}/model-tasks/{id}/execution/report
```

POST JSON：`{"expected_revision": 1, "confirm_temporal_execution": true}`。第二个布尔值在尚需桥接旧执行器审批时必须明确提供；普通回归/分类不需要该标志。预检查与统一任务审批为前置条件。所有入口均验证项目权限，服务端决定来源身份与数据路径；无自定义代码、无任意 SQL、无跨项目数据读取。

统一 GET 的关键字段：`schema_version: model-run-v1`、`state`、`executor`、`run_id`、`revision`、`source_sha256`、`spec_sha256`、`validation_protocol`、`declared_folds`、`folds`、`metric_sets`、`result_sha256`、`evidence_sha256`、`report_available`、`business_benefit_proven: false`。`folds` 是真实来源折信息，不会凭设置折数合成折级证据。

## 5. 自动化测试与证据等级

- 新增 Python：真实 SQLite/CSV/sklearn，同源重复提交、并发、改版失效、污染回执、安全隔离、旧时序实际预检查/训练/报告。
- 新增 Node：模型工作台统一操作、显式时序确认、失效后拒绝展示或执行。
- 兼容性：保留原 `/{id}/runs`、`/{id}/evidence`、`/modeling/drafts` API，历史数据不迁移/清空。
- 源码包验收：`bash TIMEPILOT.sh source-check`、专项测试、Node 全量测试、ZIP 独立解压及逐文件 SHA。
- 实机 P0：Python 3.12.12 / DuckDB+PyArrow / 原生 Perspective+Chrome / 完整离线依赖 **仅在目标环境真实运行取得 PASS 后关闭**。当前若缺依赖返回 `BLOCKED=2`。

## 6. 下一轮建议

**v0.39.4**：逐 X 可用时间账本、训练标签成熟时间审计、滚动多折和跨设备/月份的外部留出测试；确保不同模型的比较满足“同样本、同折、同预算”，需要真实业务数据才能报告收益。**v0.39.5**：把 ModelRun 结果与 BI 筛选、误差/混淆矩阵、保存视图和报告版本相互绑定，补齐折级证据以及明确的运行错误解释。**发布 P0**：目标 Mac 实测 DuckDB、Perspective、升级/回滚/离线包，生成正式验收收据。
