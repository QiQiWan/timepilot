# TimePilot v0.40.1：时序执行全局预算、跨设备评价与恢复审阅

## 结论与交付范围

本版针对 v0.40.0 源码进一步完善旧 `RuntimeEngine` 的执行可靠性。普通回归/分类的既有冻结预算、取消、预测资产两阶段提交保持兼容。对连续时序预测、工序质量提前分类引入**持久化冻结总预算、外层完整任务进程看门狗、续租和尝试次数栅栏、逐阶段运行回执**。对工序提前二分类增加时间前向的设备 OOD 验证折。两个实验执行器通过项目级统一运行入口展示任务来源、分折、实验预算及证据。

**有意未做的内容**：零样本跨设备连续时序预测（旧插件按单设备独立拟合）；原始行级重采样血缘；时间序列插件所用特征的验证集置换贡献；旧时序执行器的严格资产“准备—提交”跨数据库事务；原生 Perspective 浏览器与 Mac 离线实机门禁。这些不应在 UI 或发布信息中写为完成。

## 1. 实施清单（文件级）

| 文件 | 变更 | 校验要点 |
|---|---|---|
| `src/timepilot/temporal_experiment_control.py`（新增） | SQLAlchemy 侧建立冻结预算和尝试级 `temporal_run_steps`；保留历史任务 `legacy_budget_unverified` | 同次运行首次领取后固定截止时间，崩溃重试不重置 |
| `src/timepilot/temporal_worker_child.py`（新增） | 仅执行已领取运行 ID 的子进程入口；监控父进程生存状态 | 外层停止时不产生失控写入 |
| `src/timepilot/runtime.py` | `queue/claim/execute` 分离；租约续期；外层完整任务 watchdog，控制进程树；基于租约/尝试检查更新；分类设备 OOD 折 | 强制终止包含数据读取/预处理/报告；设备互斥且标签成熟 |
| `src/timepilot/modeling_workflow.py` | `ForecastPlan.validation_strategy`；跨设备预检查；传递冻结 `budget_profile` | 可用设备数不足时阻断，原有时序预测保留滚动验证 |
| `src/timepilot/model_task_spec.py` | 允许时间序列二分类 `leave_device_out` | 连续预测不接受无法证明的零样本设备迁移 |
| `src/timepilot/model_task_temporal_bridge.py` | 统一任务与旧草稿验证策略、折数严格一致 | 阻止跨来源/跨分折伪链接 |
| `src/timepilot/model_task_unified_runs.py` | `preview / steps / start / summary` 读取时序冻结预算、设备互斥哈希、测试集范围声明 | 证据 SHA 排除每次读取变化的剩余秒数 |
| `src/timepilot/static/modeling-ui.js` | 工序二分类选择时间前向或设备留出，设折数并提示最终独立测试范围 | 表单同时下传两个合同，不允许无设备标识的 OOD 请求 |
| `tests/test_temporal_experiment_v0401.py`（新增） | 真实时序预测/分类、超时、取消、租约恢复、反例 | 测试使用真实 Python 子进程和 SQLite，不用伪造指标 |
| `frontend/native-bi/tests/temporal_experiment_v0401.test.mjs`（新增） | 前端历史配置恢复、模式选项及表单合同断言 | 保证用户所选协议不被默认模式覆盖 |
| `scripts/release_gate_v0401.py`（新增） | 引入新增 Python 与 Node 测试，维护历史真实门禁 | CI 退出码决定 PASS/FAIL/BLOCKED |
| `scripts/release_integrity.py`、`README.md`、`pyproject.toml` 等 | 版本号、发布必须文件与完整签名 | ZIP 解压后文件数、SHA256、无混版 |

## 2. 训练预算合同

由 `TemporalExperimentControl.plan(profile)` 产生不可变实验计划，仅允许 `5m / 15m / 30m`：

```json
{
  "schema": "temporal-experiment-plan-v1",
  "budget_profile": "15m",
  "run_wall_seconds_total": 900,
  "execution_scope": "entire_legacy_temporal_run_including_io_and_reporting",
  "enforcement": "external_process_tree_watchdog",
  "retry_policy": "same_first_start_and_deadline",
  "fold_resource_limit": "isolated_plugin_wall_cpu_rss"
}
```

- **提交时冻结**：队列记录与计划在同一 SQLAlchemy 事务内写入；返回的计划 SHA256 参与版本审计。
- **首次领取开始计时**：记录 `started_epoch`、`deadline_epoch`；任务重领保留原截止时间。
- **完整任务监督**：外部父进程每约 100 毫秒检查截止、取消标记、租约所有者与尝试数；违规终止完整进程树，更新明确的 `EXPERIMENT_BUDGET_EXHAUSTED`、`RUN_CANCELLED` 或 `TEMPORAL_LEASE_LOST` 等错误码。单模型拟合依旧受 CPU、内存和墙钟限制。
- **回收孤儿步骤**：重领任务时把之前尝试仍处于 `running` 的步骤改为 `interrupted`；不会把中断伪标为成功。
- **历史兼容**：缺少冻结计划的老运行标 `legacy_budget_unverified`，不追认 5/15/30 分钟约束。
- **限制**：进程内不同阶段的 CPU/RSS 总和并未提供统一的跨模型累计预算；OS 进程组的行为还需 Windows/macOS 实机验收。启动后主进程崩溃的再领取由租约到期实现。

## 3. 设备 OOD 评价定义与数据泄漏核查

本版将 `leave_device_out` 作为**工业工序提前二分类**的可选验证协议；需要至少与所选折数同数量的独立设备、每折同时包含正负标签。

以整段数据的验证时段起点 `cutoff` 为共同时间界，设备集合划分为互斥验证组 `H_k`：

- 训练集 `Tr_k`：设备不属于 `H_k`，记录时间早于 `cutoff`，且训练标签成熟时间不晚于 `cutoff`。
- 验证集 `Va_k`：设备属于 `H_k`，记录来自冻结的历史验证时段。
- `Tr_k` 与 `Va_k` **设备 ID 交集为空**；同时检测跨批次重叠、标签成熟与历史特征可用时点。
- 每个候选模型使用相同的折与签名索引；报告 `train_index_sha256 / validation_index_sha256 / heldout_device_sha256 / train_device_sha256`。
- **最终独立测试**：保持当前已有的后续时间测试（可能含已参与其他折训练的设备），明确标为 `chronological_mixed_devices_not_device_ood`；与设备 OOD 验证指标分别呈现。

设备 OOD 不等于全新产线长期稳定运行效果。当前策略针对训练不含留出设备、推理读取该设备决策时刻可用的现场传感器快照；动态适应/少样本微调未纳入本版。

## 4. 用户界面

工序提前分类表单新增“验证协议”：时间前向或跨设备留出；后一模式须明确设备标识、验证折数、Y 正负标签与标签成熟时间。结果页将 `validation_protocol`、折证据、`independent_test_scope`、任务预算与步骤运行历史放在同一任务版本下展示。超时、取消、源数据失效的历史结果仍按当前来源审计规则阻断展示。

普通表格模型界面和 Perspective 入口保持兼容，仍需在目标环境完成实际 WS/WASM 联调；本版本不声称已完成原生 Perspective 实机门禁。

## 5. 版本、API 与向后兼容

- 项目级统一入口继续使用 `/projects/{project_id}/model-tasks/{task_id}/execution` 的预览、启动、状态、取消及步骤查询；时序任务复用既有 API，不新造二级重复路由。
- 时序任务提交预算默认 15 分钟，用户可选择 5 或 30 分钟；同一已提交修订的运行回执不可被新执行引擎覆盖。
- 使用增量建表，不删除或重建原数据库；历史检查点保留，旧运行缺少新回执则表明“未验证”。
- 目标 Python 为项目当前约定的 3.12.12；本次验收环境缺失的组件在物理门禁中显示阻断。

## 6. 本次测试与发布门禁

专项测试：真实预测/分类、进程树超时、取消、租约失效及旧结果兼容。前端包括分类 OOD 模式持久化恢复。正式退出码与数量请以交付的 `SOURCE_TEST_RECEIPT.json` 为准，不能凭测试已启动就宣称通过。

```bash
bash TIMEPILOT.sh source-check
bash TIMEPILOT.sh release-gate --mode source
bash TIMEPILOT.sh release-gate --mode physical
bash TIMEPILOT.sh release-gate --mode browser
```

源码门禁包含源码 SHA256/版本检查、完整建模 Python 回归及完整 Node 前端测试。物理门禁验证本机 Python/DuckDB/PyArrow/Perspective/Chrome/目标平台安装状态；未通过则归类 BLOCKED。

## 7. 下一步优先级（v0.40.2—v0.40.4）

**P0 v0.40.2：逐样本血缘**。保留数据采集行 ID；重采样桶、窗口与原始记录建立受控多对多 Parquet 边表；完整预测可以反查原始输入和业务决策时点；同时对原始行权限和撤销进行约束。

**P0 v0.40.3：原生 BI 物理联调**。使用真实 DuckDB/Perspective/Chrome，在至少 10 万/100 万行两档合成数据上测试分页、过滤、聚合一致性，避免逐行扫描 CSV，输出读取内存及耗时。

**P1 v0.40.4：完整实验一致性与真实工况**。构造跨设备、跨批次、跨月份及失效发布时间反例，封闭回归与分类主指标；再关闭 Mac 本机浏览器、Python 3.12、离线运行物理门禁。连续时序预测的设备 OOD 需先建设可跨设备拟合的全局时序模型，再开放给用户。

### 增量审阅修复：历史滚动折兼容性

全量回归曾检出 `test_temporal_one_entry_executes_and_reuses_legacy_real_run` 失败，根因为旧折的 `comparison_protocol` 未重复记录 `split_kind`，新入口仅检查该摘要，误报“Temporal fold validation protocol differs from approved task”。修复后：新时序预测阶段在摘要中明确设置 `split_kind=rolling_origin`；历史阶段若缺少此字段，只有在**全部已签名分折均确认为 `rolling_origin`** 时才允许推断。设备 OOD 和时间分类折仍要求声明的摘要协议与各签名折一致。该修复已使用真实时序预测和跨设备分类单项复测通过。

初轮含旧断言失败的回归记录独立保留，不将其算作成功；最终发布门禁以修复后完整运行结果为准。
