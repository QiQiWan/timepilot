# TimePilot v0.38.7｜物理资产完整性、孤儿文件安全审阅与发布验收

审阅对象为 v0.38.6 源码候选包，按 `docs/V0386_NATIVE_SNAPSHOT_RECOVERY_AND_QA.md` 的遗留事项逐项推进。本版仍为 **source-only 候选**，不得把源代码逻辑测试或模拟 API 浏览器测试当成真实 DuckDB/Perspective 验收。

## 一、源码直接发现的问题

1. **孤儿文件判定缺口**：v0.38.6 的只读清点仅在 `--full-hash` 模式显示 `hash_matches=false`，但仍可能给出 `complete=true`，导致物理回执与实际文件冲突时清点结果过于乐观。同样，未启用全量哈希时也未对 `snapshot_ready.file_bytes` 与当前文件大小做必要的一致性检查。
2. **正式登记资产缺独立批量体检**：已有 `NativeBIService.verify(asset_id, force=True)` 能对单一资产哈希校验，但孤儿审计 CLI 没有同时校验已登记资产的只读模式；数据库指向已丢失或被同长度篡改的 Parquet 时，难以获得统一的现场清单。
3. **符号链接与维护路径**：`native_bi` 根目录、隔离归档根目录、Worker 租约库和元数据库的符号链接需要保守拒绝。否则停机维护过程中可能读取/移动受控目录之外的文件。
4. **无 I/O 预算的全哈希**：旧 `--full-hash` 只有目录项数上限，对单文件很大的情况缺少总读取字节预算。

## 二、逐项实施

| 文件 | 修改事项 | 失败时动作 |
|---|---|---|
| `src/timepilot/native_bi_recovery.py` | 孤儿回执长度逐项核验；要求全哈希时核验 SHA256 及扫描前后文件状态；已注册资产新增 `audit_registered_snapshots()` | 缺失、篡改、扫描过程变化、格式非法返回 `complete=false`，保留证据 |
| `scripts/native_snapshot_audit.py` | `--verify-registered` 独立读取 SQLite 资产目录；总 SHA 字节预算在孤儿与登记资产之间共享；输出已检查数、有效数、异常数、缺失数、哈希字节数 | 只读返回 `requires_review`，退出码 2 |
| `scripts/native_snapshot_quarantine.py` | 拒绝元数据库、租约库、源根及目标根符号链接；移动前复核文件状态 | 拒绝隔离，不删除 Parquet |
| `scripts/v0387_physical_integrity_acceptance.py` | 临时库下执行真实 CSV→Parquet→DuckDB 查询→SQLite 对账→同长度损坏检测 | 缺依赖退出码 2，物理错误退出码 1 |
| `tests/test_v0387_integrity_audit.py` | 固定异常反例：同长度篡改、长度错误、路径重定向、缺失、预算、真实 SQLite、全局共享预算等 | 任何非预期成功视为测试失败 |
| `scripts/release_gate_v0387.py` | 加入新增专项和真实物理资产校验阶段 | 分别记录 `passed`、`failed`、`blocked` |
| CI、CLI、说明、manifest | 统一活动版本 0.38.7，保留历史复测入口 | 源码清单变化直接拒绝安装校验 |

## 三、审计约定与独立运行

先备份原始 `TIMEPILOT_HOME`，本版只读审计可在服务运行中执行，但结果仅代表扫描期间的文件状态。如果资产仍在变化，应在停机后再次审计。`--verify-registered` 会读取已登记文件全部字节；`--full-hash` 会读取孤儿快照。两项同时开启时共用 `--max-hash-bytes`，默认 4 GiB，超过预算必须返回待复核，不能静默认定无损坏。

```bash
python scripts/native_snapshot_audit.py --home "$TIMEPILOT_HOME" \
  --verify-registered --full-hash --max-scan 100000 \
  --max-hash-bytes 4294967296 --out reports/v0387/native_integrity.json
```

输出中的 `complete`、`registered_integrity.safe`、`ambiguous_orphans`、`invalid_orphans`、`hash_budget_exhausted` 必须联合判断。`complete=true` 不保证跨主机原子一致，报告不能作为多实例共享目录的事务凭据。异常时不应自动恢复、不应删除 Parquet，也不应绕过人工发布对账。

## 四、与物理数据库验收的证据边界

- **源码/SQLite/文件系统证据**：验证已有快照真实路径、长度、SHA 与元数据记录的一致性；支持文件缺失和文件变更等失败反例。无法证明其数据列及数值可由 DuckDB 正确解析。
- **真实 DuckDB/Parquet 证据**：在目标 CPython 3.12.12 和已安装 DuckDB、PyArrow 的环境执行：

```bash
python scripts/v0387_physical_integrity_acceptance.py --require-target \
  --out reports/v0387/registered_integrity.json
python scripts/native_bi_physical_crash_acceptance.py --require-target \
  --out reports/v0387/native_crash.json
bash TIMEPILOT.sh release-gate --mode physical
```

前者验证真实 Parquet 中的前导零字符串、数值及注册哈希，随后在临时数据中制造**同长度损坏**，要求发现异常；后者复测文件落盘但元数据提交前、以及元数据提交后响应前的双崩溃窗口。两个脚本都使用独立临时 SQLite 和快照，不接触用户原有业务数据。

- **浏览器证据**：`featurelab380_chromium_dom_qa.py` 运行真实 Chromium 和项目 JS，但 API 使用固定模拟数据。它验证 30 次路由切换和图表选择交互；原生 Perspective/WebSocket/后端联合门禁仍需完整 JS/WASM、目标 Chrome 和真实后端。
- **离线交付证据**：必须存在可复现的 npm lock、编译的 Perspective JS/WASM、平台匹配的 Python wheelhouse。当前源码包不包含这些运行资源。

## 五、当前已知限制及下一步

1. 目标 Mac/CPython 3.12.12 环境不在本次执行环境；切勿把 Python 3.13.5 的源回归视为目标环境通过。
2. 只读哈希验证存在 TOCTOU 窗口。签名包含 inode、大小、mtime、ctime 并在读取前后检查，但不能为恶意并发写入提供跨进程、跨文件系统事务保证。人工隔离必须停机并备份。
3. 重复全量 SHA 对 1GB 或 100 万行资产有实际 I/O 成本。先记录 `hashed_bytes`、p50/p95 和 RSS/PSS，再确定缓存或增量验证策略；不能以牺牲正式发布完整性为代价优化。
4. 应在目标环境关闭 `physical`、`browser`、`offline` 三个门禁，再考虑生产发布。CI 的 GitHub Actions 配置虽增加真实物理验收任务，本地无法代表 CI 正式运行结果。

复测结果与操作系统、解释器、各项实测收据由根目录 `TimePilot_v0.38.7_ITERATION_QA_20261118.md` 的外部交付文件单独给出，避免把预计验收当作已经发生的结果。
