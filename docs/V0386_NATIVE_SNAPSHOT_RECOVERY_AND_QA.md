# TimePilot v0.38.6｜原生 Parquet 崩溃恢复与物理发布门禁

版本日期：2026-11-18。基线：v0.38.5 源码及其 `ITERATION_QA`。变更范围：`NativeBIService` 物理快照写入、文件来源回执、孤儿清点、只读审计与停机人工隔离、物理故障注入及发布流水线。本版没有改变项目表、用户数据目录或现有 `native_bi_assets` 表结构。

## 1. 已核实的缺陷和改动

### 1.1 Parquet 已完成但元数据未提交

原实现顺序：DuckDB `COPY` → `data.building` → `os.replace(data.parquet)` → SQLite `native_bi_assets` / `native_bi_asset_heads` 事务。若此处强制退出，文件留在原生 BI 目录，但数据库没有资产记录。旧版库存清点仅识别“一个未登记的 Parquet”，无法确定所属项目；历史孤儿文件可能导致无关项目的发布也被阻断。

当前新增 `publication-intent.json`：

- 创建资产 UUID 目录后、COPY 之前，原子写入 `format/asset_id/lineage/state=preparing`。只保留项目、导入批次、候选区、版本和来源 SHA 等标识，无原始业务行或上传文件路径。
- 真实 Parquet 写入后执行 `os.replace()` 和 `fsync()`，计算文件 SHA256、大小、行数，再原子更新 `state=snapshot_ready`。
- SQLAlchemy 元数据仍保持父资产和 head 指针同事务提交。元数据事务失败后保留已完成的 Parquet 和意图文件供人工核对；未形成 `data.parquet` 的不完整目录仍按原逻辑清理。
- `.building` 文件在扫描中单独计数，不能据此声称已形成有效 Parquet。意图文件只是来源线索，**不作为自动注册、自动重试或强制覆盖资产的授权**。

### 1.2 来源分区的孤儿快照清点

`NativeBIService.find_published_candidates()` 先扫描全部权威 SQLite 元数据，再在有限预算内扫描物理目录。已登记资产在匹配来源和 SHA 复核后可以复用。未登记快照分为：

| 情况 | `complete` | 处理 |
|---|---|---|
| 完整意图且与当前发布区域相同 | true | `scoped_unregistered_snapshots > 0`，当前区域需人工对账 |
| 完整意图属于其他项目/区域或独立文件上传 | true | `unregistered_other_regions` 记录，当前区域可继续独立发布 |
| 遗留版本无意图、标记损坏、来源类型不明、Symlink | false | `ambiguous_orphans`，阻断“当前区域没有孤儿文件”的结论 |
| 超出元数据或目录扫描预算 | false | 禁止以不完整库存推断产物不存在 |
| 仅有 `.building` 临时文件 | true | `incomplete_builds` 记录，需维护清理，但不计为已完成 Parquet |

为兼容 v0.38.3 的调用契约，`unregistered_snapshots` 包含当前区域孤儿及来源不明项；新增 `scoped_unregistered_snapshots` 与 `unregistered_other_regions` 提供明确口径。对账接口增加 `blocked_unregistered_snapshot`，不赋予绕过校验的自动操作。

### 1.3 只读审计和人工隔离

`native_snapshot_audit.py` 用只读 SQLite 连接校验 metadata，枚举 UUID 文件及意图；`--full-hash` 可复算孤儿文件 SHA256。没有 DB 时明确 `BLOCKED`，不会自动初始化空库。可按项目/导入/候选/版本四个标识筛选，也可全局清点。

```bash
# 在维护机上；第一次建议只读清点
python scripts/native_snapshot_audit.py --home "$TIMEPILOT_HOME" --full-hash \
  --out reports/v0386/native_orphans.json
```

经工程师审查原始来源和数据库后，对于无法直接补齐元数据的孤儿文件，可执行**显式停机隔离**，保留完整文件、意图和隔离回执，不删除字节，不将可疑资产自动注册成用户数据。

```bash
# 先停止 API/五类 Worker，备份 HOME；从只读审计获取 UUID 和实际 SHA
python scripts/native_snapshot_quarantine.py \
  --home "$TIMEPILOT_HOME" \
  --asset-id '<孤儿资产UUID>' \
  --expected-sha256 '<审计得到的实际SHA256>' \
  --case-id '<核查案例UUID>' \
  --reason '已人工复核源文件、SQL元数据和快照完整性，隔离待进一步处理' \
  --confirm-services-stopped
```

隔离工具在文件系统操作前后检查本地 Worker 角色租约，以及目标 UUID 是否仍存在于 SQLite 注册表。遇到活动租约、校验不符、元数据已注册、路径 symlink 或无法证明停机时会拒绝移动。对于没有有效回执的历史文件，另需 `--allow-unknown-intent` 人工确认。隔离目录为 `TIMEPILOT_HOME/native_bi_quarantine/<case-id>/<asset-id>`，位于活跃库存扫描范围之外。**该操作不清除持久发布任务的 `needs_reconciliation` 状态**；之后还应使用已有对账接口重新获取证据并由项目管理员批准 `retry_verified_absence` 或关闭任务。

这里的“已停止”仍要求操作人实际停掉 API；角色租约检查能够阻断已登记 Worker 的在线活动，但无法证明没有绕过 Worker 注册机制的外部脚本正在使用同一文件。禁止对运行中的企业多主机系统直接使用该脚本。

## 2. 真实物理故障验收

新增 `scripts/native_bi_physical_crash_acceptance.py`；必须存在真实 DuckDB、PyArrow，正式发布时还要求 CPython **3.12.12**。它使用一次性临时 SQLite 和真实 DuckDB `COPY` 生成的 Parquet，不连接现有 `TIMEPILOT_HOME` 或任意外部数据库 URL。

| 故障点 | 子进程退出方式 | 期望可验证后果 |
|---|---|---|
| 正常 CSV → Parquet | 无 | `read_parquet` 行数及带前导零编码保真，SHA 验证通过 |
| 文件完成后、SQL 元数据前 | `os._exit(71)` | 发现一份带来源标记的未登记 Parquet；SQL 无对应行；其 SHA 和行数可独立复核；无关区域不被它阻断 |
| SQL 事务提交后、业务返回前 | `os._exit(72)` | 正式元数据可找到原生资产，原生物理哈希通过；此时上层项目关联仍由既有人工对账路径关闭 |

```bash
python scripts/native_bi_physical_crash_acceptance.py --require-target \
  --out reports/v0386/native_bi_crash.json
bash TIMEPILOT.sh release-gate --mode physical
```

运行条件不满足时报告 `status=blocked`、退出码 2；真实 SQL 或故障条件不满足返回 `failed`、退出码 1；不得把跳过或测试替身记录为正式物理验收通过。原先 12/506 行固定 Parquet 图表 SQL 数值、10k/100k/1M 行性能检查继续由正式 `physical` 门禁串行验证。**本轮提供的 source-only 包不内含 DuckDB、PyArrow、Perspective 编译 bundle 或目标平台 Python wheelhouse**。

## 3. CI 和离线运行包

`release_gate_v0386.py` 将来源意图/隔离单元测试纳入 `source` 门禁，将真实强杀测试置于物理图表及性能门禁之前；`browser` 门禁仍强制检查本机已装 Chrome、完整 JS/WASM 和 30 次浏览器往返。

CI 使用目标 Python 3.12.12、Node 24；离线发布的显式构建流程在在线构建机上使用 `--bootstrap-lock` 生成并核验 npm 锁，离线包需按目标 OS/CPU 提供 Python wheelhouse。没有这些依赖不得发布为完整离线运行包。CI 配置的存在不能替代实际 CI 运行记录。

## 4. 并发首次启动和发布门禁本轮修复

审阅复现了五个 Worker 共享全新 SQLite 元数据文件时偶发 `database is locked`。根因是单独的 `BEGIN IMMEDIATE` 仅覆盖建表事务，而旧代码的 `PRAGMA quick_check` 预检和 SQLAlchemy 连接时 `PRAGMA journal_mode=WAL` 发生在写事务之前。现在 `timepilot.db.Store` 对本地文件 SQLite 在只读预检、连接建立、建表和结构审阅的整个初始化阶段使用同一 OS 进程锁（`.bootstrap.lock`），原本对 SQLite DDL 的写事务仍保留。这样不会删除、替换或重建既有库；PostgreSQL 与内存 SQLite 不进入本地文件锁逻辑。验收使用真实五进程空库启动回归，而非仅检查函数存在。

`release_gate_v0386.py` 的各组测试使用独立进程组；发布专项进一步按测试文件拆分进程，规避历史 fixture 在同一 pytest 会话内共享后台资源时可能卡住的问题。测试输出统一写入临时文件，再通过 `wait()` 获取真实退出码；超时时终止进程组并保存阶段退出码，从根源上消除子进程继承 `stdout` 管道导致的 `communicate()` 阻塞。修复了旧测试启动 Worker 后继承输出管道导致的发布门禁卡死。各门禁应以本次生成的 JSON 文件和实际退出码为准。须区分 `source=PASS` 与 `physical/browser/offline=BLOCKED`。

## 5. 当前风险与下一步

1. 本机实测仅能验证 SQLite/文件系统/来源回执逻辑；由于缺目标 CPython、DuckDB/PyArrow 和 JS/WASM，物理及浏览器门禁仍应为 `BLOCKED`。请以生成的同轮 JSON/日志为证，不推断通过。
2. 针对 `SIGKILL` 后“项目资产关联未完成”已有元数据校验与人工对账；建议在目标 Mac 实机将持久项目任务、原生 BI 元数据和 UI 审批端到端串联复测。
3. `fsync` 与大文件 SHA256 的成本需在真实 1GB 工业文件下测量；目前不声明性能提升或生产收益。
4. 该方案面向单机 SQLite 与本地文件系统；多主机部署仍须采用外部强一致协调和分布式 fencing，不得将来源意图视为分布式事务。
5. 隔离是人工保全操作，不代替数据内容修复。正式入库前仍需要实际 DuckDB 读取、业务字段合同与源 SHA 复核。
