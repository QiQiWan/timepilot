# TimePilot v0.38.1 — Worker 进程隔离、失败恢复与系统审阅

审阅基线：用户提供的 `TimePilot_v0.38.0_SOURCE_CANDIDATE_20261117.zip`，于 2026-11-17 独立解压后修改。所有测试在隔离的临时 `TIMEPILOT_HOME` 中执行；**未修改用户原有数据库或业务数据**。

## 一、本轮结论

现有 FeatureLab、Agent、预测、导入和发布任务各有自己的租约与恢复逻辑，但 v0.38.0 的 FastAPI 启动仍会同时创建五类后台线程。即使使用 `serve --no-worker`，API `/ready` 也没有独立 Worker 的健康证据；旧 `release_gate_v0380.py` 的报告版本字段曾沿用 `0.37.9`。

v0.38.1 将五类任务的执行入口提取为共用动作，支持各自独立的 OS 进程。API 的内置模式也通过同一 SQLite 角色租约互斥。增加管理接口和系统日志状态表，记录 Worker 心跳、任务队列聚合、执行轮次的耗时分位数及错误轮次。增加真实子进程强制终止/租约接管回归和真实 DuckDB/Parquet 规模基准脚本（仅在物理依赖存在时执行）。新版发布门禁明确区分源码验收与物理运行、浏览器、离线交付。

**发布状态：源码候选版。** 本地 Python 3.13.5、Node 22.16.0、Chromium 可用；项目目标 Python 3.12.12、Node 24、DuckDB/PyArrow、Perspective JS/WASM、本机 Chrome 原生联调和完整 wheelhouse 缺失，物理与离线门禁不得记为通过。

## 二、主要源码修改及直接效果

| 文件 | 修改内容 | 验收方法 |
|---|---|---|
| `src/timepilot/worker_supervisor.py` | 新增五角色 SQLite 独占租约、generation fencing、30 秒 TTL、后台心跳、停止释放与 200 次/角色的有界耗时样本；按角色 p50/p95 与只含任务状态计数的聚合监控 | 双 Worker 抢占、旧 epoch 无法续租、进程 kill、角色释放、API 集成 |
| `src/timepilot/api.py` | 内置线程统一接入同一 WorkerSupervisor；新增共享发布后画像和特征协调器动作，外部 API-only 模式在缺心跳时 `/ready` 返回 503；新增 `/api/v1/system/workers` | TestClient 真实启动与停止五角色；检查 503 与无重复执行 |
| `src/timepilot/enterprise_security.py` | Worker 诊断路由仅 tenant_admin 可读（与日志访问权限一致） | 安全回归、路由审阅 |
| `src/timepilot/cli.py`, `scripts/launcher.py` | `worker-service --role`、`worker-status`、`start --external-workers`、`workers-start`；沿用现有 Python 和 `TIMEPILOT_HOME` | 在临时 home 中启动 `--once`，无新环境/隐式安装 |
| `scripts/worker_pool.py` | 五个独立子进程，任一异常退出则通知并停止其余进程；避免一个类任务失败后长时间半失效 | 进程/信号路径审阅 |
| `scripts/featurelab381_performance.py` | 使用真实 DuckDB 生成不可变 Parquet，按 10k/100k/1M 行，重复计算 profile / categories / distribution / correlation，记录 Parquet SHA、p50/p95、RSS 和预算 | DuckDB/PyArrow 不具备则退出 2 并写入 BLOCKED JSON |
| `src/timepilot/static/system-logs-ui.js` | 只在系统日志页显示五类 Worker、队列状态、异常计数和执行轮次 p95，支持刷新且不展示个人任务内容 | 新增 Node 模拟 API DOM 测试 |
| `scripts/release_gate_v0381.py`, CI, 源码清单 | 修复版本报告，加入进程可靠性回归及物理性能门禁，保留源码/物理/浏览器/完整离线四类状态 | 独立 source gate / 明确 BLOCKED |

## 三、实施与启动

普通单机版维持原操作：`bash TIMEPILOT.sh start`，由 API 生命周期管理五个本机线程。需要真正隔离操作系统进程时，在两个终端执行：

```bash
# 终端 A：禁用 API 内置 Worker
bash TIMEPILOT.sh start --external-workers
# 终端 B：启动预测、Agent、上传、发布、特征/评价五个独立进程
bash TIMEPILOT.sh workers-start
# 只读诊断
bash TIMEPILOT.sh worker-status
```

也可以由现有服务管理器分别部署 `bash TIMEPILOT.sh worker-service --role feature` 等五个独立命令，均须共享同一本机 `TIMEPILOT_HOME`，同类角色只能有一个存活。切换部署模式前**停止原 API 与所有 Worker**，避免抢占时启动失败。Worker 的业务租约决定作业重试；对于外部写入的状态不确定任务，保留人工核对，不强制盲目重试。

可配置 `--max-rss-mib 4096` 作为任务间准入暂停门槛。它无法终止已经开始、且超预算的计算。SQLite 角色租约仅保证本机相同数据目录下进程协调，不提供跨主机一致性；需要真正分布式时应更换专用协调器和队列存储，并重新做多机争用验证。

### 当前接口语义

- `/api/v1/health`：活性。API 还活着，不代表后台任务或 BI 资源可用。
- `/api/v1/ready`：就绪。在外部模式要求五类角色的有效心跳且未暂停准入，同时检查现有 DuckDB、Perspective、BI bundle、数据目录和数据库；缺任一项返回 503。
- `/api/v1/system/workers`：运维摘要。角色是否在线、空闲/执行中/预算暂停、领取任务的轮次、异常轮次、耗时分位数、队列状态计数。日志 UI 标注 **执行轮次耗时不等于完成任务耗时**；企业模式仅租户管理员可读。

## 三之一、并发空库启动与企业权限修复（新增审阅结果）

五个独立 Worker 在全新、空白 `TIMEPILOT_HOME` 中同时调用 `Store(...)`，实际触发了 `sqlite3.OperationalError: table datasets already exists`。问题位于 SQLAlchemy `create_all(checkfirst=True)` 的跨进程检查/建表竞争。`src/timepilot/db.py` 已改为在同一 SQLite 连接显式执行 `BEGIN IMMEDIATE`，并在事务内完成全部元数据建表；非 SQLite 数据库仍沿用原路径，老表结构检查和不自动修改旧字段的安全规则保持不变。新增五个真实独立子进程同时创建全新元数据的回归，确保五进程并发首次启动不会重复建表。完整五 Worker 启动进一步复现了 `ProjectImportJobs` 并发 `ALTER TABLE` 的第二处竞争，因此 `create_app()` 现在统一通过 `startup_guard.py` 使用独立 SQLite `BEGIN IMMEDIATE` 启动锁，串行执行元数据、项目目录及任务迁移；操作完成后立即释放，进程异常退出也不会留下永久锁。

`python scripts/featurelab381_worker_acceptance.py --out reports/v0381/worker_subprocess.json` 使用隔离的临时数据目录实际启动五个 Worker，确认全部角色在线，再向进程池父进程发送 SIGKILL，检查子进程自动退出及五个租约释放。测试生成 JSON 和日志，不能用源代码静态检查代替。此流程明确限定单机；进程被 SIGKILL 时已提交外部系统的副作用仍需按业务队列对账。

企业模式审阅发现：`/api/v1/system/workers` 和日志接口返回全局运行状态，可能包括其他租户的活动计数和故障文本。现将全局诊断限定为**单租户部署中的租户管理员**；多租户环境全部返回 403，由有主机权限的运维人员使用本机 `worker-status` 和日志文件。未来若需要多租户管理控制台，应建立单独的平台运维角色和审计链路，且按租户隔离可见数据；不能简单放开当前全局端点。

## 三之二、实际启动资源观察

在当前 Linux x86_64 / Python 3.13.5（**非官方目标 Python 3.12.12**）环境中，五个进程全部空闲上线后通过 `psutil` 测得 RSS 分别为约 `165.2 / 165.0 / 164.9 / 165.0 / 175.7 MiB`，合计约 `835.8 MiB`。RSS 求和会重复计入共享库内存，也不包含 API、浏览器和真实分析任务；应视为启动内存风险提示，不能当作生产内存峰值或 Mac 性能指标。

审阅发现每一个独立 Worker 都通过 `create_app()` 建立全套 Web 路由与各类服务对象，造成启动成本和常驻内存重复。建议 v0.38.2 优先建设按角色最小依赖注入的 Worker Runtime，在相同任务和租约语义下与当前实现对照启动耗时、PSS/RSS 和数据库连接数，并确保真正的导入/发布/特征处理无回归。由于该重构会涉及业务服务依赖拓扑，本版保留成熟的同源执行入口并明确资源限制。

## 四、测试与缺失证据

本地能够完成：模块单元回归、真实 SQLite 并发抢占、真实子进程 kill、应用 TestClient 启停、前端 Node 测试、源码完整性和解压校验。具体测试计数以本轮独立生成的 QA JSON 和测试日志为准。

物理运行缺少：Python 3.12.12、真实 DuckDB/PyArrow、Perspective 虚拟服务器、npm frozen lock 与已编译 JS/WASM。新性能脚本因此返回 `BLOCKED`，没有 10k/100k/1M 行的 p95/RSS 数字。Node 22 的前端源代码测试不等于 Node 24 的发布构建；Chromium 的模拟 API DOM 验收也不等于真实 Chrome + DuckDB + WebSocket。

## 五、本次审阅的遗留问题及版本优先级

1. **P0：发布前必须关闭运行环境门禁。** 在目标 Mac Python 3.12.12 和 Node 24 上完成 npm 锁、Perspective JS/WASM、DuckDB 独立 SQL 数值对账、Chrome 30 次同文档切换与离线包断网安装。源码通过不代替此步骤。
2. **P1：现有业务队列的最终写入要分别审阅。** 进程角色独占锁减少重复执行，实际产物保护仍取决于 FeatureLab、上传、发布、预测及 Agent 自身的租约；对可能产生外部副作用的写操作必须坚持人工对账。对故障注入中的数据库断电、租约续期饥饿、对象存储原子提交尚无全部正式证据。
3. **P1：真实负载/性能尚不能证明。** 新脚本为单机 DuckDB SQL 微基准，仅代表确定性合成列上的聚合查询；必须补齐真实 FastAPI 并发 6 卡图表、Worker 并发和实际设备数据 10k/100k/1M 的 p95/RSS/锁等待，并设定同比基线。不能拿微基准替代完整工作流吞吐与延迟。
4. **P1：容量隔离仍需 OS 限制。** 本轮支持独立 OS 进程和任务间内存准入，未提供 Linux cgroup、macOS memory pressure 的强制隔离与抢占。进程池遇到子进程退出会整体停止并提示，由外部服务管理器按既定恢复策略重启。
5. **P1：状态 UI 继续精简。** 将后台状态放在系统日志页，避免复制到概况、质量检查和统计图表中；后续可将失败任务的安全重试与人工确认接入同一管理入口，但企业权限、审计与幂等语义必须先确定。
6. **P2：跨主机正式高可用**，须在专用任务队列、数据库协调和资源配额完成后实施；目前 SQLite 单主机限定必须保留。

## 六、发布门禁与复现命令

```bash
bash TIMEPILOT.sh source-check
bash TIMEPILOT.sh release-gate --mode source
python -m pytest -q tests/test_v0381_worker_supervisor.py
node --test frontend/native-bi/tests/featurelab381_worker_diagnostics.test.mjs
python scripts/featurelab381_worker_acceptance.py --out reports/v0381/worker_subprocess.json
# 在安装了真正 DuckDB、PyArrow 和 Python 3.12.12 的目标环境
python scripts/featurelab381_performance.py --require-target --out reports/v0381/performance.json
bash TIMEPILOT.sh release-gate --mode physical
bash TIMEPILOT.sh release-gate --mode browser
bash TIMEPILOT.sh release-gate --mode offline
```

所有运行/版本检查都不得修改原始 `TIMEPILOT_HOME` 内的业务数据。升级前通过 v0.37.9 备份功能保存并校验后，先在单独目录复核，再升级生产目录。源代码候选包的 `RELEASE_MANIFEST.json` 将逐项校验全部源码 SHA256。
