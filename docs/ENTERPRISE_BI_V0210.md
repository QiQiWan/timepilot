# TimePilot v0.21.0｜企业数据平台、工业 BI 与生产验收

## 1. 交付范围与验收口径

v0.21.0 在 v0.20.1 项目制数据管理基础上增加企业模式身份管理、项目角色权限、租户隔离、物理 Parquet 去重、工业数据分析模板、生产规模恢复验收与真实 DuckDB/Perspective/Chrome 集成验收入口。

**运行环境区分：**

- **本地模式**（默认）：延续原工作流，无需创建企业账户；不因新权限规则破坏历史项目、BI、预测与分类插件。
- **企业模式**（显式启用）：要求已有管理员、HTTPS 对外来源、租户及项目权限；旧全局 API 在未完成租户化前被拒绝；旧项目归属需要离线确认。
- **服务侧通过**：源码测试、FastAPI 真实 HTTPS HTTP、SQLite 身份/RBAC/CSRF、多租户逻辑隔离、1,000,000 行真实子进程中断恢复。
- **尚需目标机验收**：真正安装并运行 DuckDB、pyarrow、Perspective Python 与编译后的 JS/WASM，在 macOS 已安装 Chrome 中完成实际 BI 页面与 WebSocket、保存视图和工业叠加图验证，以及 10,000,000 行真实 DuckDB 查询性能测试。

本次源码包**没有**已编译的 Perspective JS/WASM 或 npm 锁文件。`bash TIMEPILOT.sh` 会按现有逻辑自动检查、必要时首次安装 npm 前端依赖并构建。完全离线的正式部署应由发布机生成并使用已校验的 `FULL_OFFLINE` 包。

## 2. 代码变更及接口

| 领域 | 主要文件 | 机制及验收对象 |
|---|---|---|
| 身份与授权 | `src/timepilot/enterprise_security.py` | 租户及本地账号、scrypt 密码验证、8 小时会话、HttpOnly/Secure/SameSite=Strict Cookie、双提交 CSRF、登录失败锁定、撤销会话、审计 |
| 项目所有权 | `src/timepilot/projects.py` | `tenant_id`；项目列表受租户和成员过滤；历史项目默认不可见直到明确划归 |
| API / WebSocket 隔离 | `src/timepilot/api.py`、`native_bi_api.py`、`enterprise_security.py` | 企业 ASGI 网关、项目成员校验、资产所属项目二次校验；未授权 WS/旧全局流接口禁止访问 |
| 企业 UI | `static/login.html`、`identity.html`、`security-client.js`、`identity.js` | 登录、用户创建/停用、项目授权、租户操作审计和退出账户 |
| 工业 BI | `src/timepilot/industrial_bi.py`、`projects_api.py`、`static/native-bi.js` | 5 种工业分析模板、时间聚合、维护事件标记、工况混合预警、版本冻结、Arrow 输出和 Perspective 图表 |
| 不可变物理去重 | `src/timepilot/physical_store.py`、`scripts/dedup_native_bi.py` | SHA-256 内容寻址、跨资产相同 Parquet 硬链接、校验失败拒绝、仅显式 `--apply` 执行 |
| 大数据恢复 | `scripts/accept_production_recovery.py` | 真正 `os._exit(73)` 杀死独立发布子进程；续跑、哈希对比、损坏分块重建、源指纹变更阻断 |
| 性能 | `scripts/benchmark_native_bi.py` | 真实 Parquet 文件、DuckDB 100k/1m/10m 行，查询 p50/p95、并发、文件大小、进程峰值 RSS |
| 浏览器验收 | `scripts/accept_native_bi_live.py`、`accept_enterprise_browser.py` | 本机 Chrome、真实 FastAPI/DuckDB/Perspective WebSocket、TLS HTTPS、会话/CSRF/租户隔离、视图保存恢复 |

重要接口：

- `POST /api/v1/auth/login`、`GET /api/v1/auth/me`、`POST /api/v1/auth/logout`
- `GET/POST /api/v1/identity/users`、`PATCH /api/v1/identity/users/{uid}`
- `GET/POST /api/v1/identity/projects/{pid}/members`、`GET /api/v1/identity/audit`
- `GET /api/v1/projects/{pid}/industrial/assets/{aid}/suggestions`
- `POST /api/v1/projects/{pid}/industrial/preview`、`POST /api/v1/projects/{pid}/industrial/arrow`
- `GET/POST /api/v1/projects/{pid}/industrial/views`、`GET /api/v1/projects/{pid}/industrial/views/{vid}`

## 3. 身份认证、角色与数据隔离

企业身份数据存放在 `TIMEPILOT_HOME/security/identity.sqlite3`。项目目录保留在 `TIMEPILOT_HOME/projects/catalog.sqlite3`。身份与项目数据之间以 `tenant_id` 和项目 UUID 关联。企业模式使用一个部署中的共享 SQLAlchemy/SQLite 和文件空间，实行**服务端逻辑隔离**；当前不提供每租户独立数据库、独立加密密钥或独立 Worker 的物理隔离。

| 权限 | 读取项目 | BI 数据分析 | 数据/任务写入 | 管理项目权限 |
|---|---:|---:|---:|---:|
| viewer | 是 | 只读 | 否 | 否 |
| analyst | 是 | 只读 | 否（当前按最小权限处理） | 否 |
| editor | 是 | 是 | 是 | 否 |
| project_admin | 是 | 是 | 是 | 是 |
| tenant_admin | 所属租户全部项目 | 是 | 是 | 是，且管理账户 |

未完成租户化的全局数据接口（包括原有跨项目数据列表与 Agent 设置）在企业模式下 fail-closed；不得在 UI 上提供绕过接口。原生 BI WebSocket 只接受同源 `https`、有效会话、有权项目及资产绑定，并拒绝旧全局实时数据流。账户停用会撤销现有会话。

**适用边界：** 此版本为自托管本地身份体系，尚未提供企业 OIDC/SAML 单点登录、MFA、SCIM、集中化会话撤销服务、跨进程速率限制、完整安全渗透测试、独立租户密钥、分布式任务/数据库隔离。不可因“企业模式可登录”即判定符合公网生产安全基线；公网或多组织强隔离部署前需外部专业审计与配套基础设施。

## 4. 历史项目迁移与回滚

**先停止全部 TimePilot 服务**，将 OneDrive 同步目录中的活动 `runtime` 迁至 Mac 本地非同步目录，备份时按 SQLite WAL 一致性规则操作。升级前使用 v0.20.1 的备份脚本及系统快照，保留完整原目录。

```bash
conda activate server
export TIMEPILOT_HOME="$HOME/TimePilot-runtime"
bash TIMEPILOT.sh backup --output "$HOME/TimePilot-before-v021.zip"

# 离线创建租户；密码经 TTY 输入，命令行与日志不存储密码
bash TIMEPILOT.sh identity bootstrap --tenant factory-a --name '工厂 A' --user admin

# 只列待迁移项目；默认不修改任何历史项目
bash TIMEPILOT.sh identity claim-legacy --tenant-id <tenant_uuid>

# 仅将明确选择的历史项目归属租户；还会要求人工输入 CLAIM
bash TIMEPILOT.sh identity claim-legacy --tenant-id <tenant_uuid> --project-id <project_uuid> --apply
```

确认配置可信的 HTTPS 终止反向代理、正确转发 Host/Origin/Cookie、进程与文件系统权限后：

```bash
export TIMEPILOT_ENTERPRISE_MODE=1
export TIMEPILOT_LOCAL_MODE=0
export TIMEPILOT_PUBLIC_BASE_URL="https://timepilot.example.internal"
bash TIMEPILOT.sh
```

登录页 `/login`；租户管理员访问 `/identity` 管理账户和项目成员。历史数据未被强制迁移，不会因升级自动划归某个租户。需要回滚时停止新版服务，从完整备份恢复独立的数据目录和旧版程序；不允许在有旧 Worker 仍运行时对同一 SQLite 文件混合读写。

## 5. 物理去重

```bash
bash TIMEPILOT.sh dedup scan                   # 只读预演
bash TIMEPILOT.sh dedup scan --apply           # 确认后执行
bash TIMEPILOT.sh dedup gc                     # 仅列出无引用的 blob
bash TIMEPILOT.sh dedup gc --apply             # 删除确认无引用的 blob 名称
```

去重目标是 `runtime/native_bi/{asset_uuid}/data.parquet` 中**SHA-256 完全相同的不可变物理文件**。同一文件系统中，`runtime/native_bi/_blobs/sha256/{前缀}/{SHA256}.parquet` 持有内容寻址对象；原路径、资产 ID、版本与数据哈希保持稳定。原路径与对象共享 inode。迁移按 SHA 和数据库登记逐一核对，先创建新链接再原子替换目标路径，损坏源、未登记源、符号链接拒绝迁移。

**边界：** CSV 原始文件与 Parquet 的字节编码不同，不能安全地硬链接到同一对象；旧 BI 版本编辑增量尚未全部纳入跨格式物理去重。不同文件系统、OneDrive、NFS 或不支持硬链接的存储上保留原文件且报告不支持。文件级 SHA-256 去重不等同于全平台跨格式语义去重。`dedup gc --apply` 只删除 link count=1 的孤立对象名，不删除逻辑资产路径。

## 6. 工业可视化的可复现性

已接入 5 类模板：设备温度趋势、维护事件叠加、工况切换、工艺质量过程、设备运行对比。用户确认时间、设备、指标、维护事件、工况与采样桶，并明确固定数据版本。数据经 DuckDB 服务端**全量聚合**后，以 Arrow IPC 传送 Perspective，避免误把页面抽样作为全量统计。结果附来源 SHA256、定义 SHA256、混合工况桶数、事件列表和受限图表点数；超出 `max_points` 不静默截断。保存的工业视图在重新读取时复核源资产版本及哈希；跨项目视图不可见。

维护事件是已确认源字段标记；工况切换只显示观测上的变化，尚不意味着已开展真实机理推断。图表使用 JSON/Arrow 且服务端字段名白名单和参数化 SQL；不能从浏览器传入自由 SQL。并发与大规模查询采用时间上限，但仍应通过生产性能验收决定合理查询限额。

## 7. 验收指令、记录与阈值

### A. 完整源码、单元和兼容性（当前环境执行）

```bash
bash TIMEPILOT.sh source-check
bash TIMEPILOT.sh test
```

### B. 真实发布进程崩溃与重启（当前环境已通过）

```bash
bash TIMEPILOT.sh accept production-recovery
# 或自定义实际行数/报告位置
python scripts/accept_production_recovery.py --rows 1000000 --out reports/recovery_1m.json
```

验收项：20,000 行一个检查点；写完两块后真实强制退出 73；新进程复用已验证两块；续跑与不中断结果 SHA-256 一致；故意损坏块会重建；源指纹不同拒绝复用。1,000,000 行合成 CSV 的测试记录在本轮容器约 17.153 秒；这只度量**数据发布恢复**，未包含 DuckDB 聚合、真实 BI 浏览器或完整用户级负载。

### C. 真实 DuckDB / Parquet 查询容量（目标 Python 3.12 环境待运行）

```bash
bash TIMEPILOT.sh accept production
# 可只先测 10 万和 100 万，再扩展千万行
python scripts/benchmark_native_bi.py --rows 100000 1000000 10000000 --repeats 5 --concurrency 4
```

记录导入吞吐、聚合/筛选及并发查询 p50/p95、Parquet 大小、峰值 RSS 和哈希，并可用 `--max-query-p95-ms` 设置目标机 SLA。缺少真实 DuckDB 时返回 `blocked`、退出码 2，**不会以模拟数据服务填补**。性能阈值需要按目标机器内存、并发和业务查询设计制定。

### D. 企业 HTTPS 浏览器（目标机已安装 Chrome 时待运行）

```bash
bash TIMEPILOT.sh accept enterprise
```

脚本启动临时真实 HTTPS FastAPI/TLS + SQLite 双租户服务，验证真实 Cookie/CSRF/权限/跨租户隔离，随后用本机 Chrome 完成页面登录、项目创建、租户隔离和伪造请求阻断。不下载浏览器；若 Chrome 因策略禁止本地 HTTPS 会标注 `blocked` 并返回 2。

### E. DuckDB → Perspective WebSocket → Chrome（目标机待运行）

```bash
bash TIMEPILOT.sh accept native
```

严格检查 Python 3.12.12、真实 DuckDB/pyarrow/Perspective Python、当前源码匹配的 JS/CSS/WASM 及已安装 Chrome，执行原生 BI 导入、筛选、Perspective 模块/WS、保存视图恢复。扩充测试检查实际工业数据 180 条分钟记录 → 3 个小时桶、维护事件、工况混合、Arrow 输出、保存视图及版本一致性。

`reports/*.json` 为逐阶段事实记录；测试失败/阻断时检查 `status` 和 `stages`，不要仅凭启动服务或存在报告文件认定成功。

## 8. 尚需企业与业务实机补证

1. Mac Python 3.12.12 + 已安装 Node 24 / Chrome 的完整原生 Perspective JS/WASM 首次编译、WebSocket、跨页刷新与历史数据恢复。
2. DuckDB 10,000,000 行生产规模真实扫描、筛选、并发、缓存影响、时限和内存测量；至少两种真实工艺表及一次生产硬件压测。
3. 真实用户负载下的 HTTPS 反向代理配置、访问日志、认证绕过、文件权限、租户资源配额和数据泄漏专项安全审查。
4. 历史 CSV 与 BI Parquet 的跨格式物理重用、旧编辑增量版本的存储回收；确保清理任何文件前均有血缘/引用计数。
5. 数据发布 → BI 冻结选择 → 预测/分类训练 → 结果复现的目标机整链路测试，证明可审计行集与模型输入一致。

上述条目缺少实测证据前，应将发行状态定义为**候选发布/需目标机签收**，不得标记为正式生产部署验收通过。
