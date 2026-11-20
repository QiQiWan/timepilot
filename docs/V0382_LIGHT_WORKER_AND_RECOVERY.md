# TimePilot v0.38.2 角色级轻量运行时、租约保护与系统复审

审阅基线：用户上传的 `TimePilot_v0.38.1_SOURCE_CANDIDATE_20261117.zip`，逐项对照此前审阅报告。日期：2026-11-17。交付类型：源码候选，目标 Python 3.12.12/Mac/原生 Perspective 生产环境尚待验收。

## 1. 实际修改（文件与行为）

| 范围 | 关键文件 | 修改和约束 |
| --- | --- | --- |
| 角色级 Runtime | `worker_runtime.py`, `worker_entrypoint.py` | 创建预测、上传、发布、特征、Agent 所需依赖图；API 路由不再随独立 Worker 启动，仍使用原有队列，不更改任务语义；企业模式保留环境和身份管理检查 |
| 进程池与 CLI | `scripts/worker_pool.py`, `cli.py` | 默认进程池改用轻量入口，原 `worker-service` 仍受支持；SIGINT/SIGTERM、角色租约和父进程退出语义保持 |
| 执行入口复用 | `api.py`, `worker_runtime.py` | Embedded/External 共用 `feature_cycle`、`publication_cycle` 和模型工具选择规则，避免双实现逐渐分歧 |
| 上传事务保护 | `project_import_jobs.py` | 有效租约、状态和项目可写性在 `BEGIN IMMEDIATE` 内核验；项目关联与任务成功同事务提交；过期执行者不得续租、更新进度或宣称成功 |
| 发布中断控制 | `project_publication.py` | 对进入发布/BI 注册/质量检查阶段后超期的任务设置 `needs_reconciliation` 阶段，明确阻止自动重试；验证阶段可重新领取；提交状态要求有效租约 |
| Agent/Feature 工件保护 | `agent_executor.py`, `feature_execution.py` | Agent 最终状态写入、续租、步骤归档要求当前有效租约；FeatureLab 预览回执写入前复核租约 |
| QA/发布门禁 | `tests/test_v0382_light_runtime_and_fencing.py`, `scripts/featurelab382_worker_acceptance.py`, `scripts/release_gate_v0382.py` | 真实 CSV 上传/Agent 工作、过期租约注入、五进程父进程强杀、RSS/PSS、来源完整性、目标环境阻断 |

## 2. 资源基线（单次，同机，不作性能承诺）

- 基线 v0.38.1：五个空闲 Worker 的 RSS 合计 833.5 MiB，五个角色全部在线且父进程被强制终止后释放租约。
- 本版 v0.38.2：五个空闲 Worker 的 RSS 合计 662.0 MiB，较基线减少 171.5 MiB（20.6%）；PSS 合计 571.3 MiB；上线耗时 4.71 s；五角色均在线，强制终止父进程后子进程退出、角色租约释放。
- 测试机器 Linux x86_64、Python 3.13.5，与要求的目标 Mac / Python 3.12.12 不同。RSS 可能重复计算共享内存，PSS 仅当前 Linux 提供。两版均未执行真实业务或压力负载；不代表 p95/QPS、峰值 RSS 或生产部署水平。

## 3. 失效规则与可复核证据

1. **上传**：原 `attach_import` 与 `_finish` 分为两个数据库事务，且 `_finish/_heartbeat` 未验证 `lease_until`。新实现把关联项目和成功状态封入 SQLite 写事务；过期 owner 无权写成功或失败，新 owner 在校验不可变 manifest 后可完成提交。
2. **发布**：原实现会自动重领所有租约超期的 running 任务，即使之前已写入 Parquet 或注册 BI。现在 `publishing/registering_bi/checking_quality/complete` 等阶段的超期工作转为 `failed, stage=needs_reconciliation` 并拒绝常规重试。仅能证实处于 `queued/verifying` 的阶段允许重领；人工确认后才应由业务方重新发布（当前未提供自动判断外部副作用的功能）。
3. **Agent**：原续租和最终结果更新只比较 owner 与 running 状态。增加租约尚未过期的判断，阻止旧执行者写成功；中断写操作仍沿用旧版 `needs_reconciliation` 机制。
4. **FeatureLab**：已有正式物化的文件 SHA、版本和世代验证维持不变；新增预览回执写入前的有效租约校验，避免无效执行者的审计记录。

## 4. 仍有缺口（按优先级）

- **P0 交付**：必须在实际 CPython 3.12.12、DuckDB、PyArrow、Node 24、Chrome 及 Perspective JS/WASM 上运行真实数值、连接和安装验收。缺失时不得宣称物理/浏览器/完整离线就绪。
- **P1 外部写入人工对账**：发布任务 `needs_reconciliation` 目前通过状态字段和错误说明提示，缺少独立运维工作台、可审计的人工签名记录及重新发布决策。不要自动清除或绕过核对标记。
- **P1 大任务资源控制**：`--max-rss-mib` 仍是执行下一轮任务前的暂停阈值，没有 OS 级强制终止；真实 10k/100k/1M DuckDB p50/p95/RSS 尚未完成。
- **P1/安全**：任务级角色租约与跨进程协调仅保证单机；部署于多个主机仍需强一致协调与外部副作用幂等键。企业多租户后台任务的最小权限/来源身份仍须真实数据安全复核。
- **P2 性能/质量**：五个角色中的 Agent 需要完整业务工具依赖，仍占较多内存。应按工具任务再延迟初始化，使用 PSS/RSS/启动时间对照确认收益。

## 5. 可复测命令和证据分类

```bash
python scripts/verify_installation.py --source-only
python scripts/release_gate_v0382.py --mode source
python -m pytest -q tests/test_v0382_light_runtime_and_fencing.py
python -m pytest -q tests/test_project_async_import_v014.py tests/test_project_publication_jobs_v020.py
node --test frontend/native-bi/tests/*.test.mjs
python scripts/featurelab382_worker_acceptance.py --out reports/v0382/worker_subprocess.json
# 以下需要目标真实依赖，缺失应返回 2/BLOCKED：
python scripts/release_gate_v0382.py --mode physical
python scripts/release_gate_v0382.py --mode browser
python scripts/release_gate_v0382.py --mode offline
```

以上只覆盖已执行测试范围。正式发布前应确保所有关键验收项既有独立来源 SHA，又有可复现的物理数字与浏览器行为记录。
