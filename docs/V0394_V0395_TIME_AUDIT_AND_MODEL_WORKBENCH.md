# TimePilot v0.39.4—v0.39.5｜时间可用性、泛化评价与模型结果工作台

> 2026-11-20 源码迭代与审阅记录。基于用户提供的 v0.39.3 `UNIFIED_MODEL_RUN_SOURCE` 实际代码推进。此交付为 **source-only**；未取得目标 Mac 上 DuckDB、Perspective、Chrome 和离线依赖闭包的物理验收证据。

## 1. 实际完成的范围

| 版本 | 实施内容 | 代码/接口 | 证据及边界 |
|---|---|---|---|
| v0.39.4 | 每个 X 的时间账本；独立预测决策时刻、标签成熟时刻；有实际发布时间列时逐行审查 | `model_task_time_audit.py`、`model_task_spec.py`、`model_task_store.py` | 真正对来源版本的完整训练行做时点审计，发现迟到 X 阻止训练；延迟秒数/天数是业务假设，报告单独标记 |
| v0.39.4 | 滚动前向扩张多折、跨月份前向留出、跨设备同时跨时间留出 | `model_task_execution.py` | 训练和验证行集合、日期边界、训练标签成熟、设备不重叠各有运行期断言及 SHA256 |
| v0.39.4 | 常数/线性/随机森林三候选同来源、同折、同预算上限 | `model_task_execution.py`、`feature_evaluation.py` | 保存每模型每折分数、拟合时间和硬化资源参数；仅同 **预算上限**，不虚称相等 FLOPs |
| v0.39.5 | 回归残差、分类混淆矩阵、置换重要性、月度/设备切片 | `model_task_diagnostics.py`、`model_task_unified_runs.py` | 使用原验证集预测计算汇总，发布切片至少 15 行；单次末折置换非因果解释 |
| v0.39.5 | 统一模型结果面板 + 版本追溯 + 模型/切片筛选 + 原生 BI 来源导航 + MD 报告 | `static/modeling-ui.js`、`model_task_api.py` | 失败显示错误代码/原因、来源过期清除指标；BI 目前联通**数据来源导航与验证切片**，未持久化把预测值传回 Perspective |
| v0.39.5 | 时间账本的签名持久化、审计报告、源码及专项发布门禁 | `model_task_unified_runs.py`、`release_gate_v0395.py` | 自动增量建表、不清除旧任务；原 v0.39.3 执行回执签名不可无声覆盖 |

## 2. 时间可用性合同：一个真实案例

每一行训练样本包含如下信息。此处以贴片设备的入站不良预测为例；列名是示意，实际由业务数据决定。

| 字段 | 示例 | 含义/约束 |
|---|---|---|
| `batch` | LOT-302 | 分组设备、批次或业务实体，用于跨设备泛化验证 |
| `event_at` | 2024-03-07 14:00 | 被预测过程的事件时刻 |
| `decide_at` | 2024-03-07 13:50 | 正式预测决策时刻，默认等于事件时刻（默认值需审查） |
| `temperature` | 247.5 | 自变量 X1，不得直接使用标签、未来测量或时间字段 |
| `temperature_pub_at` | 2024-03-07 13:48 | X1 真正可用于建模决策的发布时间，不等于事后 ETL 时间的假设 |
| `pressure`, `pressure_pub_at` | 8.1, 13:49 | X2 和 X2 独立发布时间；每个 X 可选单独列或使用经确认的公共列 |
| `defect_flag` | NG | 目标 Y，分类需明确正/负标签映射 |
| `defect_known_at` | 2024-03-08 09:00 | 标签成熟时刻，不能让尚未回传的质量结果进入此前训练 |

执行期至少检查：

1. 事件时间/决策时刻/发布时间列必须可解析；无时区偏移的字符串使用合同的 IANA `timezone` 本地化再转换 UTC；混合带偏移与不带偏移、DST 歧义时间、缺失行拒绝。
2. 对每个 X 检查 `available_at(X,i) <= decide_at(i)`；对未提供真实列、仅提供 `publication_lag_days` 的 X 明确标记为 `declared_lag_assumption`，不可声称真实发布时间已得到外部验证。
3. 对每个时间验证折，训练样本满足 `event_at < cutoff` 且 `label_known_at <= cutoff`，验证样本不得混入训练集合；可配置的 `gap` 使用**不同事件时刻数量**而非天数。
4. 使用 `ModelTaskSpec` 锁定源 `source_sha256`、原生版本 `native_sha256`、`spec_sha256`；生成 `ledger_sha256` 和每个 X 的发布时间序列 SHA，报告不泄露原始行。

**重要区别：** X 发布时间的原始列、人工提供的列含义仍需业务数据治理核实。审计能够发现“声称已发布但实际上晚于决策”的记录，不能自行证实业务部门登记的发布时间真实无误。源哈希只证明字节未被悄悄改写。

## 3. 三种泛化测试与公平比较

| 验证协议（`validation.kind`） | 训练范围 | 验证范围 | 推荐场景 |
|---|---|---|---|
| `rolling_origin` | 从早期扩张到当前折的 cutoff，剔除还未成熟的 Y | 顺序向后、由事件时间分块，3～8 折按 UI 配置 | 预测性能随时间的稳定性 |
| `leave_month_out` | 每一待测月份之前的完整历史，Y 在该月起点已成熟 | 依次持出最近 N 个不同日历月份 | 季节切换、月份数据分布漂移 |
| `leave_device_out` | 不含持出设备，且早于全局时间 cutoff 的历史；Y 已成熟 | 与训练设备完全不交叠、日期不早于 cutoff 的设备数据 | 新设备/新工况迁移及部署前外推 |

其他原协议 `kfold` / `stratified_kfold` / `group_kfold` 继续保留，必须正确选择任务场景。`group_kfold` 本身不提供时间前向保证，不能替代第三种验证。

比较共同点：同 `source_sha256`、同 `spec_sha256`、同 `train/validation index SHA`、同随机种子 42、固定模型配置、单线程，最多 2 万行和 32 个 X；每个候选每折拟合 45 秒**软上限**。所有模型共享验证索引，结果的 `comparison_protocol` 保存预算指纹和分折指纹。

**比较边界：** 45 秒为拟合结束后的超时判定，尚非抢占式硬超时；模型训练耗时与 FLOPs 仍不同。该结果提供可复现的同折比较，尚未构成成本归一化的算法排行榜。`average_precision`/MAE 作为比较主指标，分类阈值固定 0.5。汇总指标是逐折指标的**无权算术平均**，与全样本加权指标有区别。

## 4. v0.39.5 用户操作与数据流

按现有项目页面进入：项目 → 数据资产 → 模型建模 → 选择一般回归/一般分类 → 确定 Y/X → 指定滚动/月份/设备切分、折数、可用时间、标签成熟时间 → 保存预检查 → 人工审批 → 开始模型对照 → 查看模型结果。

模型结果页包括：来源版本和修订、审批/运行状态、真实指标来源、逐 X 发布时间账本、三候选同折指标、错误分类的混淆矩阵、回归绝对误差分位数、末折置换贡献、验证集月份/设备筛选、到同一数据资产的原生 BI 导航及 Markdown 报告下载。

BI 切片的筛选参数是 `model=baseline|linear|forest`、`dimension=month|device`、`value=...`。它仅对**训练期间已计算的验证预测**做聚合筛选，不重新训练，也不会在用户改筛选时偷偷变更数据来源。每个小组的行数不满 15 时不予输出；最多公布每类 20 组。当前通过 asset ID 导航原生 BI，未实现把独立验证预测列联动为可编辑数据资产或在 Perspective 里动态应用同一筛选器。后者需要新的持久预测版本和权限/泄漏审核。

运行失败时页面显示服务器给出的 `failure_code` 和有限制长度的 `failure_reason`；完整 traceback 留在服务日志。来源 SHA/任务修订发生变化，结果接口隐藏全部旧指标和报告链接，不会拿旧模型解释新数据。

### API

```text
GET  /api/v1/projects/{pid}/model-tasks/{task_id}/execution
POST /api/v1/projects/{pid}/model-tasks/{task_id}/execution
GET  /api/v1/projects/{pid}/model-tasks/{task_id}/execution/diagnostics?model=forest&dimension=month&value=2024-03
GET  /api/v1/projects/{pid}/model-tasks/{task_id}/execution/report
GET  /api/v1/projects/{pid}/model-tasks/{task_id}/execution/history
```

POST 仍使用 `{ "expected_revision": 1, "confirm_temporal_execution": false }`；时序草稿首次受控执行须明确确认。新增诊断接口仅对当前审批、来源有效、结果 SHA 一致的成功任务开放，参数非法返回 422，失效/未经执行返回 409；跨项目隔离返回 404。报告由当前校验后的证据即时生成，不提供不可信的历史下载别名。

### 已有时序预测/工序提前分类的过渡策略

连续时序任务与原工序提前分类仍由 `ModelingWorkbench` 的既有训练器负责。新执行前读取其真实来源，对每个 X 做可用性账本（<= 20 万行的有界读取），但原执行器未提供可信折级样本索引、成熟 Y 各折记录或逐 X 贡献；因此标注 `fold_training_maturity_certified = false`，不拼接虚假的同折对比、设备切片或重要性。已经落库的测试集聚合混淆计数可以标注来源展示。旧版执行回执保留，不能无声用新训练器覆盖；想获得新诊断须经过合法的新修订重新执行（原执行器仍只生成其支持的证据）。

## 5. 审阅及测试

### 源码级已实测

| 测试范围 | 结果 | 备注 |
|---|---|---|
| Python 建模 v0.38.9—v0.39.5 合同/真实 SQLite/CSV/sklearn | **55 passed** | 包括负例、三种泛化分折、无效时点和审批后变更 |
| 前端 Node 全量测试 | **167 passed** | 包括新增的模型结果 DOM、BI 参数转义、失效证据隐藏 |
| 原 v0.39.3 旧时序/审批回归 | **纳入上述 Python 和 Node** | 继续保留历史执行审计，不伪造结果 |
| 全仓库 Python `pytest -q` | **未完成** | 历史集成测试长时间运行；结果不能作 PASS |
| Python 3.12.12 / DuckDB + Parquet / Chrome + Perspective / Mac 离线闭包 | **BLOCKED / 未取得现场实测** | 本环境 Python 3.13，交付为 source-only |

运行：

```bash
bash TIMEPILOT.sh source-check
bash TIMEPILOT.sh release-gate --mode source
# 现场真正关闭 P0 前还应单独运行：
bash TIMEPILOT.sh release-gate --mode physical
bash TIMEPILOT.sh release-gate --mode browser
bash TIMEPILOT.sh release-gate --mode offline
```

`release_gate_v0395.py` 对源码签名、55 项建模测试和 167 项 Node 测试进行独立验收，记录阶段 PASS/BLOCKED/FAIL。非源码门禁延续 v0.39.3 的实际目标依赖检测，不会通过跳过测试来报告 PASS。

## 6. 仍需推进的问题（下一轮 P0—P1）

1. **P0：原时序训练器统一折级合同。** 将真正的训练/验证/测试索引签名、每折截止时点的 X/Y 成熟记录、设备隔离、模型级预算从原执行器保存到受控结果；升级前不要拿不同执行器的聚合指标直接排名。
2. **P0：修复软预算难以硬性中断问题。** 采用独立限额子进程及可回收模型 Worker；规定 wall-clock、RSS、CPU、并行线程和复现环境的计量合同。
3. **P0：现场真实业务数据核验。** 对温度/贴片异常/设备维护至少三套数据由生产/MES 校核时间列与标签回传，固定设备交叉、月份外推、标签成熟边界与业务阈值；报告按批次、设备及月份对比人工规则基线。
4. **P1：BI 深度联动。** 设计验证预测的版本化受控 Parquet/SQLite 派生资产，绑定训练版本、可追溯 row hash 和权限，再通过 Perspective 按筛选查询；目前不把原始预测持久化以降低泄漏与隐私风险。
5. **P1：特征解释稳定性。** 增加多随机种子、多折置换置信区间、共线 X 的分组置换和解释性校准，避免将单次置换值误写为稳定机制效应。
6. **发布门禁：** 在目标 Mac 上关闭真实 DuckDB/Parquet、原生 Perspective/Chrome、离线依赖与一键双击启动验收，再发布独立的正式 offline ZIP。
