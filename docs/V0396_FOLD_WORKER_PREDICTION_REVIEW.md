# TimePilot v0.39.6：逐折模型证据、强制终止 Worker、版本化验证预测与原生 BI

更新时间：2026-11-20；基础源码：v0.39.5。交付状态：可复核的源代码候选版（source-only）。

## 1. 设计与适用边界

四类任务统一由已经审批的 `ModelTaskSpec`、原数据版本 `source_sha256`、任务 `revision` 和执行 `run_id` 索引。维持原来两套实际执行器：普通回归/分类使用 `ModelTaskRunService`；连续时序预测/工序提前分类使用 `RuntimeEngine`。本轮在实际训练和评价路径上补充同源逐折证据，而不根据旧聚合指标伪造逐折记录。

**统一评价合同**：每个模型在自己的任务中使用相同来源数据、同一冻结折划分、同类明确预算；输出折 ID、训练/验证样本指纹、训练标签成熟时点、每折模型评分、验证预测明细、运行预算、运行结果/资产 SHA256。模型的实际运行时间、计算量和特征算法仍可能不同；跨分类与回归的指标没有直接排序意义。连续时序预测的折级基本单位为“设备 × 滚动窗口”；工序提前分类为经过标签成熟检查的前向时间块，独立测试期保留原有隔离。

| 家族 | 训练器 | 验证方式 | 折级可核验字段 | 保留原测试集 |
| --- | --- | --- | --- | --- |
| 普通回归 | 基线、线性、随机森林 | 既有审批的滚动/月份/设备/常规划分 | `folds`, `fold_scores`, `validation_predictions`, `train_index_sha256`, `validation_index_sha256` | 原协议决定 |
| 普通二分类 | 基线、逻辑类线性模型、随机森林 | 同上，标签成熟审计 | 同上 + 混淆计数 | 原协议决定 |
| 连续时序预测 | `RuntimeEngine` 内置模型插件 | 多设备滚动窗口 | 各设备折哈希、训练标签成熟、候选评分、样本预测 | 是 |
| 工序提前分类 | `RuntimeEngine` 二分类插件 | 时间前推多折、实体/批次与成熟标签防泄漏 | 前向折、候选模型验证概率与混淆计数 | 是 |

关键文件：`src/timepilot/runtime.py`、`src/timepilot/model_temporal_evidence.py`、`src/timepilot/model_task_execution.py`、`src/timepilot/model_task_unified_runs.py`。只有具备完整 `stages/backtest.json` 或 `stages/classification.json` 校验和、正确模型名单及预测索引证据的运行才能展示完整折级指标。v0.39.5 以及更早的历史时序运行缺少新材料时仍显示“仅聚合证据”。

## 2. 独立训练进程与真实预算

`src/timepilot/model_fit_isolation.py` 与 `src/timepilot/model_legacy_isolation.py` 对既有内置训练器提供隔离子进程；父进程使用 `psutil` 检查整棵子进程树并在超过以下条件时终止：

- 普通 sklearn 模型：单次 fit **45 s 墙钟、45 s CPU、1536 MiB RSS**，每模型每折独立执行。常数基线无拟合耗时。
- 时序内置插件：单次 fit 最多 **180 s 墙钟、180 s CPU、2048 MiB RSS**，在批准任务的预算允许范围内收紧。可配置预算取任务配置与管理员上限的较小者；最短 10 s。
- 超限代码分别为 `MODEL_WALL_TIMEOUT`、`MODEL_CPU_LIMIT`、`MODEL_RSS_LIMIT`；子进程其他异常经受限 `failure.json` 回传异常类别及最多 300 字消息。父进程不反序列化来自子进程的任意 pickle；进程结束后必须检查完整、有长度上限的数值预测结果。

**预算核验注意事项**：这里的独立上限按每候选/每折执行，尚未提供整个实验矩阵的原子性总墙钟截止时间。CPU 与 RSS 由父进程采样检查，并非硬件或操作系统级隔离，极短时资源峰值可能被跳过；未来可在 Linux 加入 cgroup、在 Windows 加入 Job Objects，对 Autogluon 内部进程另行验证。对比应记录各候选实际 `fit_seconds_per_fold`，不能把同上限解释为同计算量。

## 3. 验证预测明细资产与版本合同

`ModelPredictionAssets` 在 `<TIMEPILOT_HOME>/model_prediction_assets` 内保存只读验证样本；与原始数据表分离，不改写 DuckDB/Parquet 原资产。SQLite `model_prediction_assets` 保存 `project_id`、`task_id`、`revision`、`run_id`、`source_sha256`、`spec_sha256`、`approval_sha256`、`result_sha256`，并持久保存 CSV SHA256、可选 Parquet SHA256 与完整资产清单 SHA256。

逐行字段包括 `__tp_rowid`、`model`、`fold`、`source_row_sha256`、`source_row_index`、`series_id`、`event_time_utc`、`decision_time_utc`、`label_mature_utc`、`month`、`actual`、`prediction`、`residual`、`absolute_error`、`predicted_label`、`actual_label`。时序训练器的 `source_row_sha256` 锚定 **实际进入训练器的规范化记录**（含时间、目标和真实可用时刻），当上游执行重采样或聚合时，`source_row_index` 对应派生记录位置；若需要定位原始设备日志行，还须另建“重采样桶—原始行集合”映射。禁止将此哈希当作原始数据源主键。

`publish` 加了跨进程文件锁和 `INSERT OR IGNORE`，防止同一运行的并发查询交叉覆盖已经签名的 CSV。每次读取再次校验审批、修订、数据源和文件字节哈希；超 240000 条预测明细将阻断。无 PyArrow 时保留经 SHA 校验的标准 CSV，安装 PyArrow 时可同时生成压缩 Parquet；如果用户需要大数据量下的高效游标式筛选，下一步应优化为分区 Parquet 和 DuckDB 谓词下推。

## 4. 原生 Perspective 授权与界面

- `GET /api/v1/projects/{pid}/model-tasks/{tid}/execution`：同源运行概要、折级证据、候选对照、版本化资产元信息。
- `GET /api/v1/projects/{pid}/model-tasks/{tid}/execution/diagnostics?model=...&dimension=month|device&value=...`：针对同一验证集样本的可追溯分组评价；请求未提供 `model` 时优先使用当前任务第一个实际运行候选模型。
- `GET /api/v1/projects/{pid}/model-tasks/{tid}/execution/predictions`：已批准且未失效的预测资产元数据。
- `GET /api/v1/projects/{pid}/model-tasks/{tid}/execution/predictions/rows?model=...&fold=...&offset=...&limit=...`：带签名元数据、有限分页的核对明细。
- `WS /api/v1/native-bi/model-predictions/{pid}/{tid}/{prediction_id}/ws?project_id={pid}`：原生 Perspective 只读表 `timepilot_active`。原生组件通过 `TimePilotPerspectiveLoader` 加载，同一个结果工作台内保留模型/折/设备/月度切片，并能检查预测偏差。连接时再校验来源/任务修订、资产哈希、WebSocket Origin 与项目身份授权。

相关文件：`src/timepilot/native_bi_api.py`、`src/timepilot/enterprise_security.py`、`src/timepilot/model_task_api.py`、`src/timepilot/static/modeling-ui.js`。Native BI 的真实 WS/DuckDB/Perspective 依赖需单独完成物理/浏览器门禁，本轮的 Node 测试仅验证前端路由/参数/失效隐藏逻辑。

## 5. 验收矩阵与审阅结论

| 门禁 | 验证点 | 本轮验收方式 / 边界 |
| --- | --- | --- |
| 子进程强杀 | 真正超过墙钟预算、RSS 预算，子进程退出 | `tests/test_model_worker_predictions_v0396.py` 真实 subprocess |
| 同折时序证据 | 多设备 × 多折、源数据与标签成熟、同候选折记录 | 真实 `RuntimeEngine.work_once` + ASGI HTTP/SQLite |
| 工序提前分类 | 多折前向评价、二分类混淆计数及独立测试隔离 | 真实旧执行器回归 |
| 资产证据链 | 来源—任务—运行—模型—折、分页 SHA、篡改/跨项目/修订变更阻断 | JSON/CSV/SQLite 回归 |
| 原生 BI 页面 | 同源 WS 地址、已签名资产按钮、默认时序模型、过期隐藏 | Node VM 前端测试 |
| 真 DuckDB/Perspective/Mac | DuckDB 可解析 CSV/Parquet；浏览器真实 WS；Mac Python 3.12.12 | **需目标机复测** |

审阅中发现并修复：①沿用 `forest` 作为时序默认诊断模型会报错，现按实际候选自动选择；②子进程异常原先被折叠为无法定位的泛化报错，现传递受限错误类别与描述；③并发资产发布可能互相覆盖签名底稿，现使用同一资产 ID 的跨进程文件锁；④旧时序失败原因以前只显示统一消息，现优先读取原执行器持久化的 `error_code` 与 `error_message`。

**仍需推进的 P0/P1**：

1. 在目标 Mac 的 Python 3.12.12 环境，安装锁定依赖并验收真实 DuckDB 的 CSV/Parquet 解析、Perspective WebSocket、跨设备字段筛选以及 Chrome 页面行为；通过后再考虑完整离线包。
2. 为独立训练的**整个任务运行**增加总 CPU/GPU/内存配额、取消和恢复策略；在不同操作系统使用 OS 级强限制，增加 Autogluon 有子进程时的压力测试。
3. 加入上游重采样桶到**原始行 ID**的完整血缘，完善时序模型之间的受控同协议比较；对设备/月度 OOD 验证另建独立正式评价集。
4. 增加物理 Parquet/DuckDB 批量读取、分页与 `Perspective` 浏览器集成用例，明确在线高并发服务与单机资源消耗上限。

## 6. 复现实验与升级

已有运行的 SHA256 可继续核验，新增派生数据在第一次读取新的已成功且仍获批准的运行时按签名阶段产物发布；没有新折级产物的旧任务维持历史状态。不要删除项目数据库和历史原数据。源码解压后执行：

```bash
bash TIMEPILOT.sh source-check
bash TIMEPILOT.sh release-gate --mode source
# Only in the correct target environment:
bash TIMEPILOT.sh release-gate --mode physical
bash TIMEPILOT.sh release-gate --mode browser
```

发布记录与源码验收以本次打包输出的 `docs/RELEASE_MANIFEST.json` 和独立接收文件为准。源码门禁通过不等于实际部署完成。

## 7. 本轮最终回归记录（2026-11-20）

1. Python 建模专项完整回归：**60 passed**，运行约 273.92 秒。实际覆盖原有任务合同、数据泄漏审计、旧时序执行器、时序分类、Worker 超时及 RSS 强杀、预测资产 SHA256 与项目访问隔离。
2. 前端 Node 原生 BI 相关完整回归：**169 passed**；原生 Perspective 路由与来源失效显示通过自动化测试。
3. 首次专项回归曾出现 **59 passed、1 failed**：普通建模诊断的未知模型名返回 404 而旧合同要求 422；已在诊断入口增加候选白名单，失败用例复测和完整回归均通过。单独保存首次失败记录，供比对。
4. 源码发布完整性与发布 ZIP 内 785 个签名文件逐一比对通过。发布门禁的回执版本字段沿用旧值的问题已修复；此次复跑验证的是同一业务代码，随后仅修正发布脚本的版本字符串和本说明文本，重新签名后再次做了源码检查。
5. **物理验收仍阻断**：本环境为 Linux / Python 3.13.5，未具备 DuckDB、PyArrow、Perspective 和目标 Mac Python 3.12.12 运行环境。尚未验证 DuckDB/Parquet 的实际物理链路、Perspective 浏览器连接、目标机离线启动和全仓库长周期测试。
