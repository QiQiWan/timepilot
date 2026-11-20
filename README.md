# TimePilot v0.40.6 — Windows 一键构建与启动

在已安装项目 Python 3.12.12 依赖的环境中，**首次联网，之后可复用本机已构建资源**。
PowerShell/CMD 进入解压后的源码目录后只需执行：

```powershell
.\TIMEPILOT.bat start
```

程序自动检查源码/数据库/当前 Python，检查 Perspective 的签名 JS/WASM 资源；首次缺失时
优先复用当前系统的 Node.js 24，其次复用 `TIMEPILOT_HOME/toolchains/node24` 缓存；
Windows 仍无 Node24 时，自动从官方 nodejs.org 下载 24 LTS ZIP 并校验 SHA256，
无需管理员权限或系统级 Node 安装。随后按项目固定依赖创建未签名 npm lock、执行
`npm ci`、构建 Vite/Perspective 并校验资源，然后才启动本地 Web 服务。
**成功后无需再执行单独的 `bi-setup` 命令。** 已构建且验签通过时不访问 Node/npm。

原有 SQLite/Parquet 数据目录和 Conda 环境不会删除、迁移或重建。用户日志如显示
`Release manifest: 0.40.4`，代表使用的是旧代码，须将本 v0.40.6 ZIP 解压至
**新目录**，保留原来的 `C:\Users\...\.timepilot` 数据目录。
首启需要访问 `nodejs.org` 和 npm registry；若企业网络阻断，错误会明确指出 URL 与步骤，
也可预先安装 Node 24，或在 PowerShell 中设置 `$env:TIMEPILOT_NODE_HOME` 指向其目录。
设置 `$env:TIMEPILOT_OFFLINE='1'` 可强制禁止任何首次下载（需要预先构建的 BI）。
命令若在已安装依赖的 Python 环境之外运行，仍需先激活现有 Python 3.12 环境，
本版本不会隐式进行 pip/Conda 安装。

更多测试与故障说明见 [v0.40.6 工程审阅](docs/V0406_ONE_COMMAND_WINDOWS_START.md)。

---

# TimePilot v0.40.5 — 工业合成数据与可信模型评价验收

## v0.40.5: Reproducible industrial synthetic acceptance

Use `python scripts/accept_synthetic_industrial_v0405.py --rows 5000 --repeats 3 --workers 2` to create deterministic industrial telemetry and **paired** simulated predictions for three candidate models, check point-in-time availability, and measure filtered queries. Use `--rows 333333` for 999,999 synthetic prediction records. See [v0.40.5 implementation review](docs/V0405_SYNTHETIC_INDUSTRIAL_ACCEPTANCE_REVIEW.md). Real DuckDB/PyArrow/Perspective/macOS offline gates remain independent.



## v0.40.4 历史基线：百万行验证预测、查询预算与 BI 行级联动

本次继续保留原有模型训练和两阶段资产提交。预测资产容量上限从 240,000 调整到
1,000,000 条；**这只是文件发布及查询的容量合同，未证明百万行训练任务在目标 Mac
具有足够内存**。在有 PyArrow 时采用 32,768 行一批、按模型/折/月/设备排序的
Parquet 行组写入；已有 CSV/Parquet 文件的 manifest schema 不变，旧运行仍可读取。

`/execution/predictions/query` 使用服务端指定的查询时间预算（默认 15 秒、可通过
`TIMEPILOT_PREDICTION_QUERY_TIMEOUT_SECONDS` 在 2—60 秒间调整），查询返回实际后端、耗时、
签名和范围内逐行聚合指标。无 DuckDB 时继续使用正确但较慢的 CSV 全量扫描降级。
数据库、任务修订和文件 SHA256 在读取前均进行校验，旧资产不会因升级被悄悄迁移。

原生预测 WebSocket 暴露 `prediction_row_id` 显式列，点击未分组记录可调用已有血缘查询；
源行面板的反向选择在 Perspective 打开独立的过滤明细面板。分组/透视聚合的点击
不可唯一对应原始行时会拒绝追溯。长连接每约 5 秒复核 session/项目权限、任务修订和资产签名。

量化压测：`python scripts/accept_prediction_scale_v0404.py --source-baseline`
会生成 10 万、24 万和 100 万行临时 CSV，验证筛选指标与分页，记录延迟与 RSS。
只有在真实 macOS/Python 3.12.12、DuckDB、PyArrow、Perspective 和 WASM 就绪后才能进入
原生物理门禁；Chrome WebSocket/点击联调由 `release-gate --mode browser` 继续验收。

**本包仍为源码候选版，不含平台依赖及构建产物，缺失的物理验收不能计为通过。**
详细见 [v0.40.5 工程审阅](docs/V0404_PREDICTION_SCALE_PERSPECTIVE_REVIEW.md)。

历史版本功能与限制详见 [v0.40.3 源码审阅](docs/V0403_PREDICTION_BI_WINDOW_LINEAGE_REVIEW.md) 和
[v0.40.2 血缘基础](docs/V0402_SOURCE_LINEAGE_AND_WORKER_REVIEW.md)。本版本保留此前的
签名预测、训练 Worker 与时序折级证据。原文件物理行号、分类器完整历史窗口
血缘和未见设备的零样本时序预测仍未认证。

---

# TimePilot v0.40.1 — 全时序任务预算、独立进程看门狗、设备外推折与崩溃步骤恢复

此源码版本基于 v0.40.0，新增对旧时序 `RuntimeEngine` 的完整任务进程监督和冻结实验预算；工业提前二分类支持 `leave_device_out` + 时间前向验证，并保留原有普通建模任务的实验控制与签名预测资产。

**适用边界**：零样本跨设备评价目前仅用于全局二分类器；连续时序预测仍按多窗口滚动回测。跨设备折与独立时间测试分别标注。历史运行若缺少冻结预算账本标为 `legacy_budget_unverified`；旧运行的时间约束不可追溯重建。

进入本项目的现有 Python 3.12 环境后验证：

```bash
bash TIMEPILOT.sh source-check
bash TIMEPILOT.sh release-gate --mode source
bash TIMEPILOT.sh release-gate --mode physical
bash TIMEPILOT.sh release-gate --mode browser
```

源码验收可在具备已安装测试依赖的环境运行；真实 DuckDB、PyArrow、Perspective、Chrome 以及 Mac 离线运行必须经过本机物理门禁。**本仓库为源码候选版，无预编译浏览器资产或 Mac 离线验证证明。**

详细改造、接口、失败注入、验证结果以及未解决的问题见 [v0.40.1 工程审阅](docs/V0401_TEMPORAL_BUDGET_DEVICE_OOD_REVIEW.md)。

## Mac 源码完整性修复（v0.38.8 热修复）

Finder 拷贝时可能遗漏 `.github` 隐藏目录，旧版 `source-repair` 只恢复 `ci.yml`，遗漏 `offline-release.yml`。本包已补齐两份工作流的可见 SHA256 恢复副本。

```bash
bash TIMEPILOT.sh source-repair --dry-run
bash TIMEPILOT.sh source-repair
bash TIMEPILOT.sh source-check
```

以上只恢复发行版模板隐藏文件；修改过的文件先在 `TIMEPILOT_HOME/source_repair_backups` 下备份，不修改 Python/Conda、`.env`、SQLite、Parquet。若现有源码已经损坏，亦可使用独立的 `TimePilot_v0388_Repair_Workflows.py` 对比发布清单恢复两个工作流文件。若 `source-check` 发现其他异常，请重新解压原始可信 ZIP 至**新的本地非同步目录**，避免 Finder 拖拽覆盖隐藏文件。

---

# TimePilot v0.38.8 | 发布验证与离线交付

本轮改进发布验收：完整离线包必须依次通过真实 DuckDB/Parquet、Chrome/Perspective、依赖闭包与 ZIP 解压复测；失败时不发布正式 ZIP。源码包可继续正常启动和开发，现有 `TIMEPILOT_HOME` 不受影响。

源码检查：`bash TIMEPILOT.sh source-check`。目标环境发布：`bash scripts/BUILD_OFFLINE_RELEASE.sh`。本源码候选包暂未附带 npm 锁、JS/WASM 与 Python 离线依赖，无法直接作为完整离线运行包。

实现、反例与剩余验收见 [v0.38.8 发布验证报告](docs/V0388_PHYSICAL_RELEASE_AND_QA.md)。

---

## 历史迭代记录（v0.38.7 及以前）

# TimePilot v0.38.7 — 数据资产物理完整性审计与发布安全门禁

本次迭代扩展 v0.38.6 崩溃恢复路径：正式登记的 Parquet 资产可以通过 `native_snapshot_audit.py --verify-registered` 使用 SQLite 目录和文件 SHA256 独立验证；对孤儿回执增加长度、哈希一致性检查和可配置扫描预算。发现文件被修改、目录符号链接、扫描失败或配额不足时，明确阻断安全结论。停机隔离还拒绝重定向的源目录、隔离目录、Worker 租约库和元数据库。

该审计可在当前无 DuckDB 的环境中工作，验证的是**物理文件字节及其 SQLite 登记关系**，不代表 Parquet 数值可解析。后者需在目标 Python 3.12.12 下运行 `v0387_physical_integrity_acceptance.py` 与 v0.38.6 既有强杀测试；无依赖时返回 `BLOCKED`。

```bash
bash TIMEPILOT.sh source-check
bash TIMEPILOT.sh release-gate --mode source
python scripts/native_snapshot_audit.py --home "$TIMEPILOT_HOME" --verify-registered --full-hash --max-hash-bytes 4294967296
python scripts/v0387_physical_integrity_acceptance.py --require-target --out reports/v0387/registered_integrity.json
bash TIMEPILOT.sh release-gate --mode physical
```

详细约束、受控测试、结果与风险见 [v0.38.7 物理资产审阅](docs/V0387_PHYSICAL_INTEGRITY_REVIEW.md)。勿对正在运行的生产目录执行隔离或修复。完整 Perspective/Chrome 和离线包仍依赖独立门禁。

---

# TimePilot v0.38.6 — 原生 Parquet 崩溃恢复、孤儿快照清点与实机验收

在 v0.38.5 持久任务与发布回执基础上，本轮为真实原生 BI 文件增加来源意图和 Parquet 完成回执，完善磁盘写入与 SQLite 提交之间的崩溃恢复。按项目/导入/候选版本清点未登记快照，未知来源继续保守阻断；提供只读审计、停机人工隔离与真实 DuckDB 强杀验收。原有同步/异步/批量发布、任务租约和人工对账继续保留。

当前交付为 **source-only 候选版**，不包含完整离线运行包。目标 Python 3.12.12、真实 DuckDB/PyArrow 和 Perspective/Chrome 仍是独立门禁。软件不自动创建 Python 环境，也不会清空原有 `TIMEPILOT_HOME`。详见 [v0.38.6 逐项实施与审阅](docs/V0386_NATIVE_SNAPSHOT_RECOVERY_AND_QA.md)。

```bash
bash TIMEPILOT.sh source-check
bash TIMEPILOT.sh release-gate --mode source
# 以下命令要求目标 Python 3.12.12 和真实相关依赖，缺失时明确返回 BLOCKED
bash TIMEPILOT.sh release-gate --mode physical
bash TIMEPILOT.sh release-gate --mode browser
bash TIMEPILOT.sh release-gate --mode offline
```

**v0.38.6 新增：** 原生 Parquet 写入恢复意图、来源分区孤儿文件清点、只读审计、停机人工隔离归档与真实 DuckDB 崩溃验收脚本。文件不会自动删除或强制注册。请在管理数据前先备份 `TIMEPILOT_HOME`。

```bash
python scripts/native_snapshot_audit.py --home "$TIMEPILOT_HOME" --full-hash
python scripts/native_bi_physical_crash_acceptance.py --require-target --out reports/v0386/native_bi_crash.json
bash TIMEPILOT.sh release-gate --mode source
# 物理与浏览器门禁在 Python 3.12.12 + 真实依赖和预装 Chrome 上执行：
bash TIMEPILOT.sh release-gate --mode physical
bash TIMEPILOT.sh release-gate --mode browser
```

文件隔离需人工停机确认并使用 `native_snapshot_quarantine.py` 的显式参数，详见 v0.38.6 文档。源码发布候选包不包含编译的 Perspective 或平台 wheelhouse。

---

# TimePilot v0.38.4 — 统一发布身份、跨进程互斥与资产完整性

本版将同步发布、持久队列、批量子任务及人工对账统一到按来源区域划分的 OS 写入锁。发布任务使用与名称无关的来源版本指纹和 SQLite 唯一登记。复用原生 BI 产物前强制校验行数、来源 lineage 和物理 SHA256，项目关联或统一目录出现冲突时明确阻断。详见 [v0.38.4 实施与审阅](docs/V0384_IDENTITY_CONCURRENCY_REVIEW.md)。

当前为 **source-only 源码候选版**。真实 DuckDB/PyArrow、Perspective/Chrome、CPython 3.12.12 和完整离线运行包仍需正式门禁，桩件回归不能替代生产验收。源码验证不修改用户原有 `TIMEPILOT_HOME`。

```bash
bash TIMEPILOT.sh source-check
python scripts/release_gate_v0384.py --mode source
python scripts/release_gate_v0384.py --mode physical
python scripts/release_gate_v0384.py --mode browser
python scripts/release_gate_v0384.py --mode offline
```

---

# TimePilot v0.38.3 — 发布对账、人工恢复与可审计追溯

本版继续保留 v0.38.2 的轻量 Worker Runtime，在数据发布进入原生 BI 写入阶段后发生异常时，阻止自动重试和跨入口重复提交。数据管理页新增待对账入口，可对来源 SHA256、正式 CSV、Parquet 快照及项目注册关系进行独立核对。具备充分证据后，项目管理员可以确认完成、补齐关联、人工批准无产物重试或保留原始产物关闭任务；决定以不可修改的哈希链追加到项目 SQLite 审计表。该哈希链不能替代独立数字签名或外部可信时间戳。

新增代码级异常与权限回归；真实 DuckDB、Python 3.12.12、Perspective/Chrome 和完整离线包仍是独立发布门禁，不把模拟 BI 回归视为生产通过。详细说明见 [v0.38.3 实施与 QA](docs/V0383_PUBLICATION_RECONCILIATION_AND_QA.md)。

```bash
bash TIMEPILOT.sh source-check
python scripts/release_gate_v0383.py --mode source
python scripts/release_gate_v0383.py --mode physical
python scripts/release_gate_v0383.py --mode browser
python scripts/release_gate_v0383.py --mode offline
```

---

# TimePilot v0.38.2 — 轻量 Worker Runtime 与任务级故障保护

本版本针对 v0.38.1 五个 Worker 全量初始化 Web 应用造成的空闲内存开销，引入按角色构建的后台运行时。预测、Agent、上传、发布和特征五类 Worker 通过各自的服务依赖执行**原有任务队列**，不再调用 `create_app()` 初始化 FastAPI、HTTP 路由或原生 BI 前端。保留 v0.38.1 已有的 SQLite 角色租约、任务心跳、世代栅栏、单机进程池和内置模式。

在当前 Linux/Python 3.13.5 同机测试：五进程空闲 RSS 合计从 v0.38.1 的 **833.5 MiB** 降至 v0.38.2 的 **662.0 MiB**，降低 **20.6%**；新进程 PSS 合计约 **571.3 MiB**，全部上线耗时约 **4.71 s**。测量是单次空闲状态；不包含 API 或实际计算负载，不能外推到目标 Mac/Python 3.12.12。

故障保护同步完善：上传任务的项目关联与完成状态同事务提交；过期租约不能续约、更新进度或提交产物。发布任务在写入/注册/质量检查阶段中断时进入 `failed + needs_reconciliation`，禁止无核对的自动重试；停留在 `verifying` 阶段的中断可重领。Agent 完成写入和续租要求尚未过期的任务租约，FeatureLab 预览回执要求有效任务租约。每次测试使用独立的临时 `TIMEPILOT_HOME`。



## 运行与验收

```bash
# 原有一键本地模式保持不变
bash TIMEPILOT.sh start
# API 与 Worker 分离（须在同一台主机、同一 TIMEPILOT_HOME）
bash TIMEPILOT.sh start --external-workers
bash TIMEPILOT.sh workers-start
# 单角色轻量入口及兼容的 CLI 入口
python -m timepilot.worker_entrypoint --role feature
bash TIMEPILOT.sh worker-service --role feature
# 本地仅读租约状态
bash TIMEPILOT.sh worker-status
# 独立发布检查
bash TIMEPILOT.sh source-check
bash TIMEPILOT.sh release-gate --mode source
python scripts/featurelab382_worker_acceptance.py --out reports/v0382/worker_subprocess.json
# 目标 Python 3.12.12 / DuckDB / PyArrow / Perspective / Chrome 上执行：
bash TIMEPILOT.sh release-gate --mode physical
bash TIMEPILOT.sh release-gate --mode browser
bash TIMEPILOT.sh release-gate --mode offline
```

仍为 **source-only 源码候选**。缺少 npm 冻结锁、编译后的 Perspective JS/WASM、目标环境 wheelhouse 以及真实物理数据库/Chrome 联调时，`physical/browser/offline` 必须显示 `BLOCKED`。不自动新建虚拟环境、不清空原 `TIMEPILOT_HOME`。详细修改和风险见 [v0.38.2 迭代审阅](docs/V0382_LIGHT_WORKER_AND_RECOVERY.md)。

---

# TimePilot v0.38.0 — 任务化工作台与同源统计审阅

本版对已有 **概况 → 质量检查 → 统计图表 → 自动特征 → 成果** 工作台进行按需加载与状态隔离。概况、质量、统计页不再自动提交特征预览；成果页独立查看已保存成果。统计图表允许在最多 24 列计算预算内，从实际数据的更多列中手动选择字段，总体指标、多字段箱线图、相关分析和报告共用本次选定的统计字段；独立图表卡片可选择其他合法字段，但共享当前数据版本及筛选条件。来源变化后按 `project_id / asset_id / version_id / source SHA256 / 字段契约版本` 核对 URL 状态，阻断旧筛选跨版本复用，后端拒绝异常字段及重复字段。

本版为源码候选版，**不包含** Perspective JS/WASM、npm 锁、离线 Python wheelhouse；真实 DuckDB/Parquet 数值、目标 Mac Python 3.12.12 和原生 Chrome BI 30 次切换均须另行验收。Chromium 离线 DOM 测试仅使用模拟 API 来测交互，不得用作物理数据库或生产结论。

```bash
bash TIMEPILOT.sh source-check
bash TIMEPILOT.sh release-gate --mode source
python scripts/featurelab380_chromium_dom_qa.py --out reports/v0380/dom_browser_qa.json
# 在目标 Python 3.12.12 且具备真实依赖的机器上执行：
bash TIMEPILOT.sh release-gate --mode physical
bash TIMEPILOT.sh release-gate --mode browser
```

详见 [v0.38.0 源码改造与审阅报告](docs/V0380_TASK_WORKSPACE_REVIEW.md)。

---

# TimePilot v0.37.9 — 安全恢复、离线交付门禁与 BI 状态修复

延续 v0.37.8 的指标契约与来源校验，本版新增可验证的独立目录恢复、完整离线 wheelhouse 构建、三类发布包严格区分、图表旧请求并发失效控制。在 v0.37.7 的全部功能上修复 `invalid_numeric_cells` 缺失、缺失率分母口径，新增有界 SHA256 验证缓存、统计请求性能聚合、就绪探针、实际 DuckDB 验收样例和多阶段 Docker 构建。新增 Pydantic / JSON Schema 指标契约，并更新相关前端口径。代码包保留项目、FeatureLab、模型、BI、报告与历史测试，不清空原 `TIMEPILOT_HOME`。


## v0.37.9 常用操作

```bash
# 验收源码，现有 Python 环境不变
bash TIMEPILOT.sh source-check
bash TIMEPILOT.sh release-gate --mode source

# 停止服务后备份现有数据库和 Parquet
bash TIMEPILOT.sh backup --output /safe/path/timepilot_backup.zip
# 全量 SHA256 验证后恢复到新的目录，绝不会覆盖原目录
bash TIMEPILOT.sh restore --backup-archive /safe/path/timepilot_backup.zip --destination /safe/path/recovered_home

# 完整离线发行版只在已安装 Node 24 / Python 3.12.12 的联网构建机器制作
bash scripts/BUILD_OFFLINE_RELEASE.sh
```

**交付标识必须如实区分：** `source-only`（不含原生 BI）、`native-bi-offline`（仅 JS/WASM 完整）、`complete-offline`（附已锁定并验证的目标平台 Python wheels）。当前源代码 ZIP 不含 npm 锁、编译后的 Perspective WASM 或 wheelhouse，不能作为完整离线包使用。应用正常启动不会在后台下载 npm 依赖。

**开始使用：** 使用原 Python 3.12.12 环境，先备份 `TIMEPILOT_HOME`，执行 `bash TIMEPILOT.sh source-check`、`bash TIMEPILOT.sh db-check` 和 `bash TIMEPILOT.sh`。当前交付为经过源码回归的**候选版**；当前 ZIP 缺真实 npm 锁和编译 JS/WASM，完整原生 BI 与物理发布需要继续执行验收。请参见 [v0.37.9 灾备和离线发布说明](docs/V0379_OFFLINE_RECOVERY_AND_QA.md)，以及 [v0.37.8 历史修改记录](docs/V0378_IMPLEMENTATION_AND_RELEASE_GATES.md)。

---

## v0.37.4 Mac startup hotfix (2026-11-16)

When `npm ci` reports `EUSAGE Missing: @rollup/rollup-... from lock file`, the
existing `package-lock.json` was generated without the cross-platform optional
Rollup records. The native BI setup now audits these entries, rebuilds the npm
lock in an isolated, empty temporary workspace and **runs real `npm ci` before
publishing**. Old lock files are SHA256-backed up under `TIMEPILOT_HOME` and a
verified existing Perspective JS/WASM bundle is retained on failure. It does
not create a Python environment or touch SQLite/Parquet user data.

For the independent hidden CI file mismatch run `bash TIMEPILOT.sh source-repair
--dry-run` followed by `bash TIMEPILOT.sh source-repair`. To build the native
Perspective resources online using the already installed Node 24 run
`bash TIMEPILOT.sh bi-setup --bootstrap-lock`. This source-only archive cannot
contain a verified compiled WASM bundle without completing the npm build.

## v0.37.3 证据驱动解释与特征价值对照

- 数据一览与特征图表：服务端数值证据、中文结论模板和来源/筛选 SHA256；无数据或统计冲突时显示“无法判断”。
- 有已确认标签且同源评价完整：在相同外层折比较原始字段、全部候选、筛选后特征，允许明确显示负收益；无标签不会显示预测收益。
- 数据一览支持只读证据报告导出，浏览器校验报告 SHA256；自动特征分析报告复用同折对照证据与产物哈希。
- 工程验收：`python scripts/featurelab373_evidence_acceptance.py --out reports/v0373/evidence_acceptance.json`，完整说明见 [v0.37.3 审阅与验收](docs/FEATURELAB_V0373_EVIDENCE_AND_QA.md)。
- 当前源码包不包含编译后的 Perspective JS/WASM；真实 DuckDB/Parquet SQL 复核与 Chrome 验收仍使用原有物理门禁，缺少依赖不能宣称通过。

## v0.37.2 数据一览仪表盘与正式验收

进入项目「分析工作台 → 概览 / 统计图表」可查看统一数据画像、多字段统计、分位数、Pearson 关联和可配置图表。筛选与配置随 URL 恢复；切换数据资产和版本将清除不兼容的字段筛选。所有统计基于当前 DuckDB 有效视图和参数化 SQL，图表必须匹配源 SHA256 与契约修订。

```bash
python scripts/featurelab372_dashboard_acceptance.py --out reports/v0372/dashboard_physical.json
python -m pytest -q -rs
node --test frontend/native-bi/tests/*.test.mjs
```

第一项是真实 DuckDB SQL/Parquet 数值对照；**缺少 DuckDB 返回 BLOCKED（退出码 2），不允许视为验收通过**。详见 [v0.37.2 数据仪表盘实现与验收](docs/FEATURELAB_V0372_DASHBOARD_QA.md)。

# 时骁 TimePilot v0.37.1

**本版修复**：统一 Perspective 的 ESM 加载与构建哈希校验，按连接追踪编号诊断原生 BI WebSocket，固定正式特征集到双表 Viewer 的绑定，修复 API 路径重复前缀，并改进基本 BI 回退的字段识别。已提供 30 次同一 Chrome 文档中的 BI 入口往返验收（仍需目标机器实测）。参见 [v0.37.1 修复与验收](docs/FEATURELAB_V0371_BI_REPAIR_AND_QA.md)。

面向工业数据的项目管理、自动数据分析、特征工程与可信建模工作台。提供 B/S 界面及 Python 服务端，运行依赖详见 `requirements.txt`，使用 DuckDB + Parquet 存储计算产物，Perspective 用于原生 BI 探索。

## v0.37.0 工业数据适配、标签时点与性能基准

真实运营数据接入与 PIT 监督数据契约详见 [工业数据验收与性能手册](docs/FEATURELAB_V0370_INDUSTRIAL_ACCEPTANCE.md)。NAB 多设备真实遥测的官方 CSV 与人工异常标注窗口通过已锁定 Git 文件哈希验证；MetroPT-3 单设备运行时序由 UCI 官方 CSV 适配。公开数据的标签入库时间未公开，只支持事后异常回顾性评分。企业工艺标签须提交每条数据的事件时点与标签可用时点后才执行 PIT 监督评价。固定条件下新增跨时间、未见设备评价，以及可复现 CSV/Parquet 数值与峰值 RSS 性能基准。

```bash
python scripts/featurelab370_fetch_public.py --out data/public/nab
python scripts/featurelab370_real_data.py --source nab --input data/public/nab --out reports/v0370/real_nab --require-physical
python scripts/featurelab370_performance.py --rows 10000 50000 200000 --repeats 3 --require-physical --out reports/v0370/performance.json
```

以上命令默认不下载浏览器或修改已有业务数据库；数据下载需要公网，若缺失 DuckDB 或 PyArrow，物理验收明确返回 `BLOCKED`。源码包不附带公开大数据集，运行前请按官方来源独立取得原始文件。

## v0.37.1 Perspective / WebSocket / 正式特征专项验收

```bash
python -m pytest -q tests/test_v0371_bi_repair.py
(cd frontend/native-bi && npm test)
python scripts/featurelab371_chrome_cycles.py --out reports/v0371/chrome_30_cycles.json
```

最后一条使用已安装的 **Google Chrome** 和实际 Perspective JS/WASM、DuckDB/Parquet；创建隔离的 QA 项目与正式物化特征，从原生 BI 与特征 BI 在**同一个浏览器文档内**往返 30 次。每次均检查源表与特征表的实际绑定、WebSocket、`regular-layout` 注册身份、刷新恢复和来源哈希。缺少依赖或真实 Google Chrome 明确返回 BLOCKED；此脚本不会安装浏览器或替换成 Chromium。

## 目前的默认操作

1. 在**项目 → 数据管理**上传文件，检查表结构并发布。源数据保留不变。
2. 发布成功后，已有后台 Worker 自动安排基础画像和**受控特征候选预览**。默认最多 16 项候选；未确认时间/分组/标签时，自动禁用不安全的算子。
3. 打开**分析与特征**，通过“概况 / 质量检查 / 统计图表 / 自动特征 / 成果”按任务切换，不再将所有模块堆叠在同一长页面。自动研判说明质量与统计价值；有已确认标签且完成同源监督评价时显示原始字段、全部特征、筛选后特征的同折模型对照。
4. 在分析与特征的“其他工具”菜单可选择 **手工构造 / 高级算法 / BI 数据 / 报告与成果**。手工算子和安全公式、高级 tsfresh/Featuretools 与 PIT 回接、原生 Perspective、不可变报告归档和特征集管理均延续同一个资产与版本。正式物化、模型验证和发布继续要求确认。

数据分析与特征预览无需配置大模型 API。若外部模型不可用，核心功能仍通过内置规则执行。大模型只能在现有白名单和权限范围内增强说明与建议。

## 部署与升级

本包是**完整源码直接复制版**。先停止旧服务，备份项目源码和 `TIMEPILOT_HOME`，将压缩包解压后复制到项目根目录覆盖同名源码；无需依次应用旧补丁。继续使用现有 Python 3.12 环境，**不需重新创建数据库**。本包为源码版，不附带 Perspective JS/WASM 预构建资源，原生 BI 仍须具备本机对应的构建环境与依赖。

```bash
bash TIMEPILOT.sh source-check
bash TIMEPILOT.sh db-check
python scripts/featurelab360_acceptance.py --mode smoke
bash TIMEPILOT.sh
```

验收脚本只使用已经安装的 Python/Node 环境，不自动安装依赖，也不下载浏览器。缺少 DuckDB、PyArrow 等物理计算依赖时明确标记 `blocked`，不能视为生产验收通过。

## v0.36.0 真实验收与发布门禁

本版将已经存在的物理 DuckDB、同源 PIT/模型、已安装 Chrome 和 Perspective 验收脚本整合成一次可审计执行。运行 `--mode smoke` 只验证源码、核心测试、迁移和进程租约；**不能据此声称生产验收通过**。完整验收使用真实业务 CSV 与字段映射，见 `docs/FEATURELAB_V0360_ACCEPTANCE.md`。

```bash
python scripts/featurelab360_acceptance.py --mode smoke
python scripts/featurelab360_acceptance.py --mode full --real-csv /path/to/production.csv --mapping-json /path/to/field_map.json --with-browser
```

第二条仅使用现有 Python、已安装 Chrome 和已构建的 Perspective；缺少条件时报告 BLOCKED，不会自动安装依赖或下载浏览器。对 1 万 / 10 万 / 100 万行的合成压力数据测量真实 DuckDB/Parquet 性能，但**压力数据不代表真实工业预测效果**。

## 主要文档

- [v0.37.1 Perspective / 正式特征 BI 修复与浏览器验收](docs/FEATURELAB_V0371_BI_REPAIR_AND_QA.md)
- [v0.37.0 真实工业数据与性能验收](docs/FEATURELAB_V0370_INDUSTRIAL_ACCEPTANCE.md)
- [v0.36.0 真实数据、图表与发布验收](docs/FEATURELAB_V0360_ACCEPTANCE.md)
- [当前功能与使用指南](docs/CURRENT_USER_GUIDE.md)
- [v0.35.6 手工工具、高级算法、原生 BI 和成果报告](docs/FEATURELAB_V0356_IMPLEMENTATION_REVIEW.md)
- [v0.35.5 自动研判与模型结果验收](docs/FEATURELAB_V0355_IMPLEMENTATION_REVIEW.md)
- [v0.35.3—v0.35.4 实施与验收审阅](docs/FEATURELAB_V0353_V0354_IMPLEMENTATION_REVIEW.md)
- [数据契约](docs/DATA_CONTRACT.md)
- [原生 BI 使用说明](docs/BI_USER_GUIDE_CN.md)
- [历史迭代文档目录](docs/DOCUMENT_INDEX.md)

工业维护重置、工况分段与跨设备留出评价继承 v0.35.2 的受控实现；没有真实现场数据、来源契约确认和独立最终测试集时，不应将模型验证结果作为生产性能证明。


### Frozen synthetic regression QA (v0.36.2)

Run `python scripts/featurelab362_acceptance.py --mode smoke` after every source change.
The immutable goldens live under `benchmarks/featurelab_v0362/` and intentionally
fail if fixture bytes, quality numbers, split manifests or supervised metrics
drift. For physical verification on the active Mac, run
`python scripts/featurelab362_acceptance.py --mode full --with-browser`.
Smoke/API tests use a NativeBI test double and never imply physical Parquet,
Chrome, production model accuracy or deployment readiness. Existing data in
`TIMEPILOT_HOME` is never touched by the regression harness.

## v0.36.3 / v0.36.4 最新校验

```bash
python -m pytest -q -rs
node --test frontend/native-bi/tests/*.test.mjs
python scripts/featurelab362_acceptance.py --mode smoke
python scripts/featurelab364_physical_acceptance.py
```

最后一条独立读取固定合成数据，在实际安装 DuckDB、PyArrow 的环境中进行
Parquet、时序聚合、逐行窗口特征与监督模型指标核验。缺少依赖将返回
`BLOCKED`（退出码 2）；不会自动安装任何依赖或更改已有数据库。

工作台的主导航包含系统日志，可按级别、模块、关键字、时间筛选并下载。
原生 MathJax 算子公式随源码包离线提供，在展开帮助后才加载。
源码包未含 npm 锁文件和预编译原生 BI；CI 联网构建时会生成锁文件并
上传锁与完整的 Perspective JS/WASM。若需要完全可重复离线编译，请将
CI 的经核验锁文件回写仓库。

参见 [v0.36.4 发布与物理验收](docs/FEATURELAB_V0364_RELEASE_AND_QA.md)。

## v0.36.5 Chrome 全流程验收（必须在已安装 Chrome 的目标机器运行）

```bash
python scripts/featurelab365_chrome_journey.py --out reports/v0365/chrome_journey.json
```

该脚本使用独立的临时运行目录与固定合成数据，覆盖项目创建、真实文件上传与扫描、发布中刷新后恢复、独立 DuckDB/Parquet 对账、自动画像与统计图、人工调整与刷新恢复、特征正式计算、原生 Perspective WebSocket、报告归档及下载 SHA256 校验、错误恢复。

`TIMEPILOT_CHROME_PATH` 可指向当前设备已安装的 Google Chrome；不下载浏览器。`TIMEPILOT_QA_SCREENSHOTS=1` 开启逐步截图。退出码 `0=通过`、`1=失败`、`2=物理依赖缺失导致阻断`。**未实际运行的浏览器步骤不能标记为通过**。本地开发可先执行 `python -m pytest -q tests/test_featurelab365_browser_gate.py` 及 `cd frontend/native-bi && npm test`。

详细实施、测试边界和注意事项见 [v0.36.5 发布与浏览器验收](docs/FEATURELAB_V0365_BROWSER_ACCEPTANCE.md)。

### v0.37.4 统计仪表盘修复

概况、质量检查和统计图表独立呈现；多字段箱线总览及 Pearson 热力矩阵，单图切换不重算其他图。系统日志支持异常聚合、代码调用位置和诊断编号；详见 `docs/FEATURELAB_V0374_DASHBOARD_AND_LOGGING_QA.md`。


## v0.40.3 新功能

- 普通回归与分类支持由验证预测行到当前审批数据版本的有序输入行追溯，并强制校验整个训练输入集签名。
- 连续时序预测可按照真实已签名训练折查询历史窗口所覆盖的筛选源记录。分类旧窗口、逐特征实际使用仍保守标注。
- 模型结果页支持模型/折/月/设备/误差过滤、筛选范围微平均指标、逐样本追溯。
- 查询优先使用 DuckDB + Parquet；缺少依赖时退化为带 SHA256 校验的有限 CSV 扫描。240,000 行上限未提升，Mac 原生 BI 尚待物理验收。
