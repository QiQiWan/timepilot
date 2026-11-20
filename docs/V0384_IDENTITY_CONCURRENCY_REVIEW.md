# TimePilot v0.38.4 | 统一发布身份与资产一致性审阅

日期：2026-11-17。基线：v0.38.3 源码 ZIP 和迭代 QA。全部修改均在独立副本上执行，未读写用户现有 TIMEPILOT_HOME。

## 1. 源码直接确认的问题

1. `ProjectPublication.publish()` 原先没有跨进程互斥，HTTP 同步入口、异步 Worker、批量子任务可并行竞争同一外部 Parquet；一个进程内的 `threading.RLock` 不能保护另一个 Python 进程。
2. `ProjectPublicationJobs.submit()` 根据完整 `request_json` 识别重复任务。只修改展示名称也可能再次创建排队任务。
3. 已关联原生资产时，旧逻辑使用 `native.asset()` 读取元信息并直接复用，缺少强制 SHA256 校验；统一目录和项目来源链接使用 `INSERT OR IGNORE`，可能隐藏冲突。
4. 人工核对在正式落库前可能调用跨库/文件操作，两位管理员的审批可并发执行，需要与正常发布使用同一个互斥域。
5. 正式 `physical/browser/offline` 运行门禁缺失真实依赖，不能把 FakeNative 或 DOM-only 测试作为 DuckDB/Parquet/Perspective 通过证据。

## 2. 实际实现

| 区域 | 修改内容 | 约束 / 风险控制 |
|---|---|---|
| 区域互斥 | 新增 `publication_identity.py`，用项目/导入/区域稳定哈希生成 OS advisory lock 路径 | 三个入口及人工核对共享锁；超时有明确提示；进程崩溃 OS 释放锁 |
| 物化互斥 | `GenericAssetStore.publish()` 对完整 recipe SHA 使用单独的跨进程锁，锁内校验 CSV + manifest | 避免并发覆盖 chunk/manifest，已有 CSV 被篡改时拒绝“直接成功” |
| 发布身份 | 发布任务增量增加 `source_version_id` / `publication_key` 和唯一键登记表 `project_publication_identities` | 由来源 SHA、区域、字段/表头 recipe 和项目确定；展示名称排除在物理身份之外 |
| 防重复调度 | 提交时在 `BEGIN IMMEDIATE` 内查询同区域在途及未对账记录，版本身份在独立唯一键表登记 | 并发同请求复用已存在任务；不同请求的活动冲突阻断；成功后允许改名但复用同一物理版本 |
| 强制完整性 | `ProjectPublication` 在复用已关联 Parquet 时要求 lineage、行数和 `native.verify(force=True)`；导入记录 lineage 增加 `publication_key` | 发现已关联资产损坏，拒绝生成第二份替换文件并提示人工核对 |
| 冲突拒绝 | `ProjectCatalog.attach_generic()` 和 `DataPlatform.register()` 使用 SQLite `BEGIN IMMEDIATE` 查重核对 | 同项目来源版本与物理 ID/SHA/行数不一致时拒绝提交，不静默忽略 |
| 对账串行 | `PublicationReconciliation.decide()` 在读证据与外部补关联前获取同一区域锁 | 两人并行审批只有一次终态记录，其余拒绝或返回已提交的原决定 |
| 兼容迁移 | 新字段通过事务内 `ALTER TABLE` 增量升级，旧作业和已存在关联保留 | 旧状态保留读取；旧在途任务在新提交时仍按区域检查 |
| 一键入口 | `scripts/launcher.py` 的 `release-gate` 从历史 v0.38.2 门禁更新为 v0.38.4 | 增加调用路由测试，确保双击脚本和命令行使用当版门禁 |

## 3. 验收设计

- 同一来源、同一配方 12 个并发提交：任务 ID 相同、SQLite 唯一登记只有一行。
- 两个独立 `spawn` Python 进程同时发布同一区域，磁盘假原生存储持久记录实际写入数，预期仅 1 个物理产物，两个请求读取同一个资产 ID。
- 子进程在持有写入锁时使用 `os._exit()` 模拟进程突然结束，预期新进程可以重新取得 OS 锁，但是否允许外部写入仍要通过孤儿快照盘点。
- 已关联文件改字节、已物化 CSV 改字节、SQLite 统一版本物理 ID 被修改、第二个 Native ID 尝试关联同一版本：全部 fail-closed。
- 两名管理员提交同一证据的不同审批 UUID，最终只能形成一个有效审批结果；历史审计保留。
- 原有 API、队列、企业项目权限、数据治理和前端脚本执行回归；不修改用户生产数据。

## 4. 尚未完成和适用边界

- OS advisory lock 只对共享同一物理文件系统的单机/共享目录部署有效；**不支持互不共享锁目录的多主机写入**。跨主机必须采用数据库唯一约束 + 分布式事务/outbox 或具有强一致 CAS 的外部元数据服务。
- 各阶段仍涉及不同数据库与文件系统，无法由一个 SQLite 事务实现跨系统原子提交。进程在原生写入后异常退出，仍依赖完整元数据/孤儿文件盘点及人工对账闭环，不能把单个锁当成跨系统事务。
- 物理全文件 SHA256 可占用大量 IO，发布接口采用强校验优先；BI 普通图表读取仍使用独立的短时内容校验缓存。下一阶段应测量真实大文件性能。
- 实际 DuckDB、PyArrow、Perspective、冻结 npm 锁、Python 3.12.12、Mac/Windows/Chrome 的环境测试均为独立发布阻断条件。无实机资源时须返回 BLOCKED，不将桩件回归标为生产通过。

## 5. 本版复测命令

```bash
python scripts/verify_installation.py --source-only
python scripts/release_gate_v0384.py --mode source
python -m pytest -q tests/test_v0384_publication_identity.py tests/test_v0383_publication_reconciliation.py tests/test_project_publication_jobs_v020.py
node --test frontend/native-bi/tests/*.test.mjs
python scripts/release_gate_v0384.py --mode physical
python scripts/release_gate_v0384.py --mode browser
python scripts/release_gate_v0384.py --mode offline
```

正式回归运行结果、失败项及可交付版本见外部 `TimePilot_v0.38.4_ITERATION_QA_20261117.md`。验收证据同时保存每个命令的退出码和运行环境；本页仅描述代码改造和测试计划。
