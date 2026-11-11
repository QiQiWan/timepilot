# TimePilot v0.5.11｜集成故障、发布完整性与回归审计

日期：2026-11-09

## 1. 这次日志具体说明了什么

用户提供的 `acceptance_v0510.log` 在 `[1/5] Python regression tests` 阶段停止，pytest 收集了 5 个模块错误。五次错误均来自同一个模块导入入口：

```text
src/timepilot/__init__.py:2
from timepilot.sdk import TimePilot
ModuleNotFoundError: No module named 'timepilot.sdk'
```

这是一次**源码安装完整性错误**，本轮根本没有执行 Python 集成测试、DuckDB 或 Chrome 验收。v0.5.10 完整源码包确实包含 `src/timepilot/sdk.py`；而之前增量补丁没有携带它，因此旧项目本身缺失该文件时直接覆盖补丁无法修复。日志尚不能区分旧项目原本就不完整、云盘同步遗漏、不同路径互相覆盖或编辑安装残留等因素。

之前已发现的两个独立问题仍有针对性代码：v0.5.8 的 Perspective 表名需使用 `memory.timepilot_active` 这类服务端公开的全名；v0.5.9 的 DuckDB VIEW 没有物理表伪列 `rowid`，Perspective 要配置 `row_id_expr='"__tp_rowid"'`。这些修复没有从本次 v0.5.10 日志获得新的真实浏览器通过记录。

## 2. 系统性问题与本版处理

| 故障类型 | 发现的原因 | v0.5.11 的措施 |
|---|---|---|
| 增量发布遗漏依赖 | 只携带本轮改动文件，无法保证接收方有完整基线 | 改为完整源码包，含所有 Python/JS/测试/脚本，拒绝不完整压缩包 |
| 不同版本混用 | 旧发布 manifest 仍指向 v0.5.8，部分版本号独立维护 | 统一项目/API/HTML/前端版本；manifest 由项目元数据生成，逐文件验证 SHA-256 |
| 测试前已缺源文件 | 原脚本先 pytest，单个缺失模块触发 5 个收集错误 | 测试与启动前先检查 manifest、SDK、文件、导入路径与版本 |
| 局部修复损伤工程 | 直接解压修改覆盖，没有完整升级与回退流程 | 所有输入 ZIP 文件先验证，备份旧文件、原子替换、失败回滚，旧残留代码隔离到备份 |
| 云盘/文件覆盖 | OneDrive 等同步中可能出现文件不同步、额外旧代码 | 按实际运行目录验证哈希；文件缺失或变化给出文件路径与修复步骤 |
| 前端缓存/旧依赖 | 只看 node_modules 是否存在，无法确认安装版本 | 逐项检查顶层 npm 依赖版本；源码哈希与构建哈希不同时重建 |
| 构建产物不完整 | npm 构建成功不等于可调用 JS、WASM 可加载 | 继续校验 ESM 导出契约、WASM 头与 SHA256，并在真实 Chrome 导入模块 |
| Perspective/DuckDB 接口不一致 | 表名、行序、隐式 `rowid`、JS/Python API 边界不统一 | 保留远程真实协议验收，先 Python Virtual Server，再 Chrome WebSocket/Datagrid |
| SQLite 旧库结构不匹配 | `metadata.create_all()` 不会 ALTER 旧表 | 初始化后检查所有已声明字段；缺字段时停止并提供表/列清单，数据保留 |
| 测试状态失真 | 跳过的真实 DuckDB/Chrome 用例可能被误认为测试通过 | 将纯单元、真实协议、真实浏览器三种结果分开报告，不将跳过标记为成功 |

## 3. 发布一致性规则

源码清单采用 `docs/RELEASE_MANIFEST.json` format 2，记录相对路径、字节数、SHA-256 及版本。发布脚本和安装修复脚本使用共同的 `release_integrity` 逻辑。独立源码包按压缩后的真实字节再次检查，禁止不在清单中的文件、重复 ZIP 项、路径穿越及恶意符号链接。

`frontend/native-bi/package-lock.json` 如目标机器已生成则保留为本机安装锁文件；本次无法访问 npm 仓库生成跨平台可信锁文件。正式生产发布应在有网络的可信 CI 环境执行 npm ci、生成并保留 lockfile，同时冻结 Python 解析后的依赖版本。当前发行物明确标记为**源码包**。

旧工程中的代码残留会移动到 `.timepilot-backup/`；用户 `runtime/`、`.env`、`node_modules/` 和生成的编译资源不变。完整性校验排除这些运行目录，避免遍历超大缓存或误改业务数据。

## 4. 分层验收及结论边界

- **S0 源码**：包内每个文件完整、SDK 存在、Python/HTML/JS/依赖版本一致。
- **S1 单元回归**：跨工程模块导入、FastAPI/SQLite 及 JS 工作区行为。
- **S2 真正的 Python 协议**：实际 DuckDB 连接、Parquet/Arrow、Perspective `new_session`、`open_table`、`view().to_json()`。
- **S3 构建验收**：npm 依赖版本、Perspective JS/WASM 哈希和公共导出。
- **S4 真实 Chrome**：已安装 Google Chrome、WebSocket、网格、透视、图表、编辑、版本恢复、实时追加。

当前容器缺 Python 3.12.12、duckdb、pyarrow 和 perspective-python，无法在这里宣称 S2 与 S4 完成；本地 Python 3.13 环境下的可执行 S0/S1 测试，以及系统已有 Chromium 的局部浏览器等待状态测试另行记录。最终 S2–S4 需要用户 Mac 在已安装环境执行 `TIMEPILOT.sh accept native`。

## 5. 继续存在的工程边界

1. 运行数据库目前以 SQLite + 文件系统管理，尚无正式的可回退 Alembic 数据迁移与跨进程写锁；本版增加阻断式结构检查，避免静默修改旧数据。
2. WebSocket SQL 执行时间限制采用进程内线程和 DuckDB interrupt，个别原生依赖无响应时无法保证操作系统层面的强制终止；生产化应使用独立进程/作业隔离。
3. 前端跨平台确定性安装依赖已生成 lockfile，Python 全量锁文件也尚未随源码交付；已校验顶层精确 Perspective 版本，仍需要在 CI 锁定传递依赖。
4. 原生 BI 暂限回环地址、本机单用户模式。任何向企业公网开放的版本均需补充身份认证、权限、并发配额、安全审计和真实数据安全验收。
5. 本次用户日志未提供 v0.5.10 通过 Python 收集之后的新浏览器记录，因此不能保证后续无未触及的运行路径错误；当前版本的闭环标准是取得 Mac 真实验收的全部 PASS 报告。
