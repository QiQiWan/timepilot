# TimePilot v0.37.8｜P0 指标契约、图表回归和发布门禁

日期：2026-11-17。基线为用户提供的 v0.37.7 源码 ZIP 和《v0.37.7 系统级审阅与迭代方案》。本次为**直接修改后的完整源码候选版**；不涉及用户数据目录、模型成果和数据库迁移。

## 1. 修改清单与证据等级

| 审阅项 | 本次源码修改 | 当前完成状态 | 剩余门禁 |
|---|---|---|---|
| P0-03 指标返回缺字段 | `dashboard_metrics_contract.py`，Pydantic 模型及生成 JSON Schema；`overview` 返回缺失/非有限值/转换失败各项计数、`metric_scope` 明确分母；同步更新 JS 显示 | 代码修改完成、专项回归可执行 | DuckDB 12/506 行 + 31 列真实 SQL 对账 |
| P0-02 图表 500 错误 | 沿用已统一的 `category_sql`；新增真实 Parquet 31 列反例、NULL/NaN/Inf 测试和物理验收入口；非法字段强制报错，原有 500 诊断编号保留 | 代码回归完成；物理环境未验收 | DuckDB/PyArrow、目标 Chrome 30 次切换 |
| P0-01 Docker/BI 构建断点 | Docker 多阶段：Node 24 `npm ci` 编译 JS/WASM → Python 3.12.12 加载并核对 ESM 导出、WASM SHA 和运行依赖；CI 阻止无锁或损坏的 npm 清单 | 构建流程已改；缺锁时显式阻断 | 联网构建机生成并审核完整 `package-lock.json`，实际构建容器 |
| P1-01 源文件反复 SHA | `native_bi.py` 30s 有界成功校验缓存；包括 `dev/inode/size/mtime_ns/ctime_ns` 和预期来源 SHA；改变立即失效，支持 `force=True`；公开读量统计 | 单元测试可验证文件篡改失败关闭 | 大型 Parquet 实际重复加载性能比对 |
| P1-04 诊断 SLO | 新增仪表盘请求耗时记录；`/api/v1/system/logs/summary` 近轮转窗口 p50/p95、异常比例、时间区间，企业模式下仅租户管理员可看 | 源码与专项测试完成 | 真实 10k/100k/1M 并发日志验收 |
| P0 readiness | `/health` 轻量存活；`/ready` 检查 JS/WASM、DuckDB、Virtual Server、元数据库、项目库、磁盘剩余空间、嵌入式 Worker；API-only 外部 Worker 缺心跳时阻断 | 专项测试完成 | 目标机器完整服务启动、可读实际 Parquet 和所有 Worker |
| P1 数据交互、Worker 隔离、真实 MES/PIT | 保留既有完整功能，未强制重构未验证链路 | 留待后续版本 | 现场数据、真实负载、服务/Worker 分进程测试 |

## 2. 数值契约和统计口径

返回的 `metrics` 包含：

- `records`：当前筛选记录行数。`source_records`：当前数据版本总记录数。
- `columns`：当前访问权限下的字段总数。`selected_fields`：当前参与完整统计的字段数，最多 24。
- `missing_cells`：选中字段内 SQL NULL 数量。
- `non_finite_numeric_cells`：选中数值字段内转换为 DOUBLE 后的 NaN / 正负无穷数量。
- `conversion_failed_cells`：非 NULL 数值字段内 `TRY_CAST` 为 DOUBLE 失败的数量。
- `invalid_numeric_cells`：上述两类数值无效单元格总数，绝不包含 NULL。
- `metric_scope.denominator_cells`：`metrics.records × metrics.selected_fields`。
- `missing_percent`：`100 × (missing_cells + invalid_numeric_cells) / metric_scope.denominator_cells`，没有行或字段时为 `null`。
- `metric_scope.field_ids`：实际参与统计的字段 ID 列表，`missing_policy` 记录计算边界。

这与 **所有可见字段总列数** 的缺失率具有不同的分母，前端现在把已选字段和分母直接标明。业务阈值异常尚未计入这些指标；它需要另外的领域规则。`fields[].effective_missing_count`、`fields[].invalid_numeric_count` 与总数仍可相互对账。

生成模型：`src/timepilot/dashboard_metrics_contract.py`；只读 JSON Schema：`contracts/featurelab/dashboard_metrics.schema.json`。源码测试验证两者逐项一致。

## 3. 有界来源哈希加速的安全边界

`NativeBIService.verify()` 在同一进程内复用 30 秒的上一次**成功**校验。缓存项同时绑定资产 ID、预期 SHA、文件长度、设备号、inode、修改时间、状态变化时间；任何一项改变立即重新执行 SHA256。长度变化、首次哈希不匹配、读过程中改变都返回失败。失败结果不缓存。`verify(force=True)` 用于人为审计或正式复核。对能同时控制底层文件系统元数据且拥有高权限的攻击者，stat 缓存不能代替每次都完整哈希；受信任不可变快照的部署模型仍是前提。

读取量可由 `native.verification_metrics()` 查看：`hashes` / `hits` / `hash_bytes_read`。

## 4. 从源码到发布的命令

**① 先检查源码，不创建数据库或新 Python 环境：**

```bash
bash TIMEPILOT.sh source-check
python scripts/release_gate_v0378.py --mode source
```

**② 在已有 Python 3.12.12、真实 DuckDB/PyArrow 的目标 Mac 上：**

```bash
python scripts/release_gate_v0378.py --mode physical
```

其中 `tests/test_featurelab378_physical.py` 包含 12×5 与 506×31 两组实际 Parquet，以及更宽字段范围下的无效值、筛选、分母对账。原 `featurelab377_dashboard_acceptance.py` 现同时调用新测试。依赖缺失返回 `BLOCKED`（退出码 2），不得改写为成功。

**③ Node 24 且联网的构建机器（允许下载 npm 库，不会下载浏览器）：**

```bash
bash TIMEPILOT.sh bi-setup --bootstrap-lock
python scripts/npm_lock_health.py
python scripts/build_native_bi.py
python scripts/verify_native_bi_release.py --require-build --require-source-current --require-virtual-server
```

将通过实际 `npm ci` 检查的 `frontend/native-bi/package-lock.json` 与编译后原生 BI 资源交付，再执行容器构建。CI 现在要求预先审核的锁文件，避免在每次发布时隐式重新求解依赖。**当前源码 ZIP 没有 npm 锁，无法直接在离线机器构建完整 Perspective。**

**④ 在安装有 Google Chrome 的目标环境执行（不自动下载安装浏览器）：**

```bash
python scripts/release_gate_v0378.py --mode browser
```

此阶段重用真实 Chrome 30 次原生 BI/正式特征集往返脚本；缺少浏览器、JS/WASM、Python 或 DuckDB 会直接阻断发布。`--mode browser` 包含前两个阶段。

## 5. 当前证据与未完成部分

本容器 Python 3.13.5，Node 22.16.0，缺少 DuckDB/PyArrow 和编译后的原生 Perspective 资源，无法访问联网包源。这里执行的 Python/Node 源码测试可以排除部分接口、校验及前端回归错误，**不能证明目标 Mac、真实浏览器、DB 或真实工厂数据的行为**。报告 JSON 由当前运行直接生成，记录 `passed/failed/blocked`，不附带人为伪造的物理验收结果。

下一迭代按审阅报告推进：v0.37.9 冻结 OS/CPU 对应 Python wheelhouse、Node lock 和离线运行包，在全新 Mac 安装/升级/回滚；v0.38.0 合并三任务页面的状态管理与字段范围选择；v0.38.1 分 Worker 进程及资源配额；v0.39.0 增加真实 MES 标签时点与业务收益对照。
