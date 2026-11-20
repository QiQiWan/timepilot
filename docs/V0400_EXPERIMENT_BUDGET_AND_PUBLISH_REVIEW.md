# TimePilot v0.40.0 可信实验调度与预测资产发布：工程改造及审阅

> 基线：v0.39.6。交付类型：source-only 候选版。以下完成情况以实际源码、自动化测试及单机 SQLite/sklearn 运行核对；物理依赖与 Mac/Chrome 浏览器门禁独立核验。无需清空原有项目数据库。

## 一、范围与设计边界

这次实现 **v0.40.0 实验级预算与调度可靠性**。原有回归和普通二分类模型使用独立隔离 Worker、统一验证折和版本化预测资产；本轮将这些能力连成可监督、可取消、可恢复的执行路径。

- 适用：`tabular_regression`、`tabular_classification`，三个固定候选为 baseline / linear / forest，继续使用同一冻结的时间/设备验证折与 train-only 预处理。
- 时序预测和工序提前分类：原时序 RuntimeEngine 的取消、租约与插件监督继续可用，新增统一取消/步骤读取入口，但尚未采用 v0.40.0 **整个实验**的固定 5/15/30 分钟总墙钟协议，也暂未接入新的准备-提交事务。页面与回执必须明确这种限制。
- 计算预算目前只保证父进程对整个普通建模实验的 **墙钟约束**，以及每个隔离拟合进程的墙钟/CPU/RSS 上限。实际 FLOPs、耗能、不同模型相同工作量等并未证明。模型输入 CSV 读取、特征处理和发布同样占用实验墙钟时间。
- 长期规划的跨设备 OOD、原始行多对多血缘以及真实 Perspective 的目标机验收分别进入 v0.40.1—v0.40.4；当前没有以尚未产生的数据宣称关闭这些目标。

## 二、模块及实际文件变更

| 文件/模块 | v0.40.0 改造 | 风险控制 |
|---|---|---|
| `src/timepilot/model_experiment_control.py` | 新增不可变实验计划、总预算、取消标志、心跳租约、折级步骤收据 | 同项目鉴权、SQLite 增量迁移、过时租约栅栏 |
| `src/timepilot/model_prediction_commit.py` | 新增预测资产私有准备、签名、可恢复目录搬移与同事务发布 | 来源/审批 SHA256、原子事务、文件完整性核验 |
| `src/timepilot/model_task_execution.py` | 在正常执行与崩溃接管路径接入预算、步骤、准备及提交 | 同任务修订防重复提交、失败时隔离未发布资产 |
| `src/timepilot/model_fit_isolation.py` | 增加 `stop_check`，训练子进程进行周期性取消/租约检查 | 父进程主动终止模型进程树 |
| `src/timepilot/model_legacy_isolation.py`、`runtime.py` | 兼容原时序 RuntimeEngine 的取消探针 | 原时序其他预算合同不变 |
| `src/timepilot/model_task_unified_runs.py`、`model_task_api.py` | 提供统一预算预览、取消与逐步结果接口 | 请求修订和项目权限校验 |
| `src/timepilot/static/modeling-ui.js` | 预算选项、执行中取消按钮、折级状态/用量展示 | 失败或取消后需明确修订任务再运行 |
| `tests/test_model_experiment_control_v0400.py` | SQLite/sklearn 实际执行、异常注入、重复取消、鉴权与资产恢复 | 失败点可复现实验 |
| `frontend/native-bi/tests/model_experiment_control_v0400.test.mjs` | JS 页面预算控制及任务步骤合同 | 防回归 UI |
| `scripts/release_gate_v0400.py` | 注册新增测试，继承历史物理门禁 | source / physical / browser / offline 分级回执 |

## 三、实验计划与冻结语义

`ExperimentControls.plan(profile)` 输出冻结的 `experiment-plan-v1`，包含 `budget_profile`、`run_wall_seconds_total`、`max_fit_seconds_per_fold`、`max_parallel_steps`、`cleanup_grace_seconds`、`lease_heartbeat_seconds` 与 `enforcement`；`canonical_sha` 形成独立计划哈希。

默认预算为 15 分钟，另支持 5 分钟和 30 分钟。**计时从 Worker 首次领取开始**，不计排队；同一任务修订已提交运行后不得悄悄调整预算。如需要更换配置，更新并审批任务修订，重新创建运行。Worker 崩溃重领不会重置首次开始时间或总预算截止时间。

在任务运行期间：

1. 已冻结的来源数据、任务修订及审批 SHA256 匹配，Worker 获得租约代次 `lease_epoch`；启动实验级倒计时。
2. 父进程用守护线程监控请求取消、总截止时刻、租约归属，持续为活动任务续租（租约 60 秒、心跳续租间隔 5 秒）；异常退出后可在租约失效后重新领取，且不会重置预算。
3. 每个模型/折在单独记录 `model_run_steps` 后启动子进程；每 250ms 左右调用 `stop_check`，超出剩余总预算时不再启动新折。
4. 超时、取消、失去租约触发不同错误码：`EXPERIMENT_BUDGET_EXHAUSTED`、`RUN_CANCELLED`、`RUN_LEASE_LOST`。资源违规沿用 `MODEL_WALL_TIMEOUT`、`MODEL_CPU_LIMIT`、`MODEL_RSS_LIMIT`。
5. 同一运行的已批准数据和验证折可被审阅，旧运行不被重写；过期预算或取消任务不会产生可读取的新预测资产。

新增 SQLite 表：`model_experiment_controls(run_id PK, project_id, task_id, revision, plan_json, plan_sha256, wall_limit_seconds, started_at_epoch, deadline_epoch, cancel_requested_at, ...)` 与 `model_run_steps(step_id PK, run_id, model_id, fold, lease_epoch, attempt, state, started_at, finished_at, wall_seconds, cpu_seconds, peak_rss_bytes, error_code)`。

新表使用增量创建，启动时为历史运行补入 `legacy_budget_origin=migration_unverified`，仅表明可查询；历史结果、原 SHA 与执行状态不更改。

## 四、预测资产的两阶段提交

`model_prediction_preparations` 以 run_id 为主键，登记 `prepared / committed / aborted`，固定 `result_json` SHA、资产 manifest SHA、训练租约 owner/epoch、stage/final 相对路径。私有 stage 中的 CSV 固定字段与哈希；安装 PyArrow 时额外生成 Parquet。通过原有资产管理模块的 `_verify_active` 核实来源与审批仍有效。

准备阶段完成后，预测文件只能通过私有路径读取，公共接口查询不到资产。提交阶段受跨进程发布锁保护，先核验文件 SHA，随后在 **一个 SQLite 事务** 中同时写入 `model_prediction_assets`、成功状态 `model_task_runs` 和 `model_prediction_preparations=committed`。每次提交需要通过 lease_epoch、owner 和未过期租约校验。

目录 rename 与 SQLite 无法共享一个物理 ACID 事务。针对这一边界，采用不可变的签名准备回执和幂等目录移动：若崩溃发生在 rename 之后、SQLite 提交之前，新合法 Worker 可校验已有 final 目录并继续完成同一个结果的事务；不能覆盖不同哈希的文件。超出预算的准备回执标为 aborted，并保留受限文件供人工排查，不向 BI 发布。

## 五、统一接口与前端操作

接口前缀：`/api/v1/projects/{pid}/model-tasks/{task_id}`。

| HTTP | 路径 | 请求/返回 |
|---|---|---|
| POST | `/execution/plan/preview` | `{"budget_profile":"15m"}`；返回合同 SHA、任务修订、源数据 SHA、候选/分折信息 |
| POST | `/execution` | `{ "expected_revision":1, "budget_profile":"15m"}`；保留时序旧执行器显式确认开关 |
| POST | `/execution/cancel` | `{ "expected_revision":1, "run_id":"<uuid>"}`；重复取消幂等，不得越权取消 |
| GET | `/execution/steps` | 返回折级模型状态和计费/资源回执；时序旧执行器显示独立事件来源及限制 |
| GET | `/execution` | 原有运行聚合及新预算回执，来源过期标记 superseded |

页面操作路径：项目 → 数据资产 → 模型任务 → 审批 → 选择总预算并运行 → 在结果工作台刷新步骤、检查折级指标或取消。原生 Perspective 的预测行筛选入口保持 v0.39.6 路径不变。

## 六、已覆盖的故障反例与未关闭问题

本轮自动化用例覆盖：预算冻结后不可变更、跨项目取消阻断、排队取消不启动训练、实际子进程取消、总预算到期不可重领、租约心跳抵御短租约抢占、准备阶段崩溃恢复、rename→DB 事务之间崩溃恢复、签名文件篡改阻断、HTTP 请求参数校验/项目隔离、历史运行增量迁移。最终测试计数以独立发布门禁回执为准。

**仍存在的系统边界：**

- 目前实验级总预算只适用于普通模型。旧时序 RuntimeEngine 需要独立的统一计划/冻结步骤和插件预算回执，并再进行全链路故障注入。
- 后端恢复只能重用成功写入的 `prepared` 资产。崩溃发生在中间折或预测准备之前时，当前整任务需要重新计算；后续应引入逐折可恢复工件。
- 统一模型比较仍需独立固定测试集与严格跨设备 OOD；不能将不同预测任务的不同指标直接排序。
- 预测资产物理格式和原生 Perspective 浏览器运行需目标机真实测试；当前未取得目标平台可用性证据。
- SQLite 与文件系统使用可恢复最终一致性，需要未来补充隔离的 orphan staging 清理工具及审计回执，不能自动误删已有资产。

## 七、建议下一版本具体工作

1. **v0.40.1（P0）：统一 `FoldManifest` 与时序 OOD**。引入完全未见设备 vs warm-start 两种不同协议，禁止验证设备标签泄漏，采用统一源版本和折签名；完成真实设备 × 时间联合折。
2. **v0.40.2（P0）：原始行多对多血缘**。窗口/重采样输出显式 `raw_row_id` 和变换 SHA，预测资产一行映射多个原始输入行，受控查询无越权。
3. **v0.40.3（P0）：DuckDB/Parquet 预测查询和 Perspective 真实联调**。统一 FilterSpec，避免对大 CSV 每次全扫描，核对 BI/报告同筛选的指标一致性。
4. **v0.40.4（P0）：目标 Mac 闭环**。Python 3.12.12、真实 DuckDB/PyArrow/Perspective、预装 Chrome、离线包签名与物理回归全部通过后，方标记正式发布。

## 八、验收命令

```bash
bash TIMEPILOT.sh source-check
bash TIMEPILOT.sh release-gate --mode source
# 以下需要真实目标环境，失败时保留 BLOCKED 回执
bash TIMEPILOT.sh release-gate --mode physical
bash TIMEPILOT.sh release-gate --mode browser
bash TIMEPILOT.sh release-gate --mode offline
```

直接运行新增用例：

```bash
PYTHONPATH=src:tests python -m pytest -q tests/test_model_experiment_control_v0400.py
node --test frontend/native-bi/tests/model_experiment_control_v0400.test.mjs
```
