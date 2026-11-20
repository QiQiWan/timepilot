# TimePilot v0.38.5：持久发布事务与系统审阅

版本日期：2026-11-18。基线：`TimePilot_v0.38.4_SOURCE_CANDIDATE_20261117.zip`。本报告的代码位置均对应 v0.38.5 源码。所有程序化测试使用独立的 `tmp_path` 或临时 `TIMEPILOT_HOME`，不读写用户既有运行目录。

## 1. 本轮关闭的问题

1. **同步 API 无持久记录（P0）。** 旧 `projects_api.py` 的 `publish-general` 直接调用 `ProjectPublication.publish()`，外部 Parquet 写入完成但 HTTP 响应丢失时，无法从后台任务页面找到对应工单。现在该 HTTP 入口进入 `ProjectPublicationJobs.publish_sync()`，先创建/复用持久来源版本登记和 SQLite 任务，再通过同一 `_claim`、`work_once`、区域文件锁与租约机制执行；既有同步成功仍返回 HTTP 201 和 `status=ready|prepared`、`meta`、`native_asset_id` 等字段。执行权被其他 Worker 占用时返回 HTTP 202 与真实任务 ID，绝不启动第二个写入执行者。
2. **缺少写入边界的阶段回执（P0）。** 新增 `publication_journal.py`：在 SQLite 中持久保存 `project_publication_events`，事件与任务状态在同一事务提交。记录的阶段包括 queued、verifying、publishing、csv_prepared、registering_bi、native_registered、project_linked、checking_quality、ready_to_commit、complete、failed 和 needs_reconciliation。仅保存 SHA、版本、行数、资产 ID、错误类型及人工处理决定，拒绝源行/任意键值进入回执。
3. **来源漂移（P0）。** Worker 领取新版本队列项后，重新计算源文件与发布配方版本 ID。若提交后文件/配方变化，正式物化和 BI 写入前失败；既有无版本身份的历史任务沿用原有来源校验逻辑。
4. **人工对账审计割裂（P1）。** 既有 `project_publication_reviews` 审计机制保留。人工审批的任务状态变更和阶段回执写入同一 SQLite 事务。重复 `decision_id` 幂等返回，不重复追加阶段事件。
5. **执行记录入口（P1）。** `project-ui.js` 的发布任务行新增“执行记录”。用户主动展开后，才调用项目级 `/publish-jobs/{job_id}/journal` 查看阶段与链校验；无需占用仪表盘空间，也不显示原始业务行。

## 2. 关键数据与 API 契约

**SQLite 新增表（增量，原任务和源数据不变）：**

`project_publication_events(sequence, job_id, project_id, event_json, previous_hash, event_hash, created_at)`。按项目和任务建索引，UPDATE/DELETE 触发器禁止修改，单任务 SHA256 哈希链标记追加顺序。该哈希链可检测普通删改；拥有 SQLite 文件完整写权限的攻击者仍可能重构整条链，故不宣称独立时间戳或外部数字签名效力。

**发布操作：**

- `POST /api/v1/projects/{project_id}/imports/{import_id}/regions/{region_id}/publish-general`：沿用即时成功/预备响应；竞争中的任务返回 202 和 job_id；错误返回明确的 4xx，数据写入仍通过持久队列。
- `POST .../publish-jobs`：异步提交，稳定 SHA256 版本登记，重试不产生同源并发写入。
- `GET /api/v1/projects/{project_id}/publish-jobs/{job_id}/journal`：项目级只读回执链和校验结果。没有历史回执的旧任务显示空记录，不伪造阶段。
- `GET .../reconciliation` + `POST .../reconciliation/decide`：完整外部资产核对和人工决定仍为独立受控接口；不确定的外部写入不得靠点击“重试”绕过。

**持久阶段顺序：**

```text
提交/重入（SQLite）
  -> 已登记来源版本指纹 + job_id
  -> 已领取租约 / verifying
  -> publishing（可恢复 CSV 物化）
  -> csv_prepared（CSV SHA256/行数回执）
  -> registering_bi（外部写入之前提交写前回执）
  -> native_registered（原生资产 ID 回执）
  -> project_linked（项目目录关联已建立）
  -> checking_quality
  -> ready_to_commit（工作已完成，待最终事务提交）
  -> complete/succeeded（任务状态与最终回执同事务）

任一写入边界之后进程终止 / 租约丢失 / 异常
  -> failed/needs_reconciliation -> 来源及 BI 清点
  -> 确认完成、已验证孤儿资产补关联、确认无产物后重试、人工关闭
```

**恢复边界：** SQLite 回执与 `project_publication_jobs` 的同事务一致性已验证，但原生 Parquet 写入和项目目录 SQLite 无法共用单一数据库事务。实际断电可能留下未登记的物理文件，现阶段通过 v0.38.3–v0.38.4 建立的真实目录清点与人工审批进行补偿，不会自动删除不确定资产；多主机分布式事务尚未实现。

## 3. 回归矩阵与证据等级

- **真实 SQLite + FastAPI + 文件系统（不使用 DuckDB）：** 同步接口先有持久记录、返回兼容、重复请求复用、写入后响应丢失转人工对账、哈希验证后补齐关联、来源版本变化拒绝执行、租约过期防自动重试、跨项目读取隔离、审计条目无法 UPDATE/DELETE。
- **业务兼容：** 原有泛化数据发布、项目数据治理、预测建模引用、数据资产、原生 BI 模拟协定、v0.38.3 人工对账、v0.38.4 真实双进程互斥与崩溃锁释放。
- **前端静态回归：** 执行记录按需展开、回执安全转义、数据管理原人工核对按钮保留。
- **真实 DuckDB/Parquet：** 需要 CPython 3.12.12、DuckDB、PyArrow，当前测试环境不具备。测试替身明确采用 CSV 物理文件，不声称 Parquet 发布、SQL 精度或高并发性能通过。
- **原生 Perspective/Chrome：** 缺签名后的 npm lock、编译 JS/WASM 和目标 Chrome 联调。不能根据 DOM 或静态源码测试给出生产可用结论。
- **离线包：** 本交付为 source-only。必须在目标 OS/CPU、Python 3.12.12 和 Node 24 下冻结 Python wheelhouse、npm lock 和完整 JS/WASM，并通过 source/physical/browser/offline 各门禁，才能标记为生产候选。

各项实测数字及具体阻断原因见同轮 `TimePilot_v0.38.5_ITERATION_QA_20261118.md`。

## 4. 系统审阅：仍需关闭的事项

| 优先级 | 风险及现状 | 下一步可执行验收 |
|---|---|---|
| P0 | 多版累计真实物理门禁持续 BLOCKED | 在目标 Mac Python 3.12.12、Node 24、DuckDB/PyArrow 和 Chromium/Chrome 下先执行四种门禁；保存来源 SHA、SQL 口径和 30 次浏览器运行日志 |
| P0 | 持久日志不能原子控制外部 Parquet 物理写入 | 实机执行在 Parquet 完成后、原生元数据入库前 `SIGKILL`；检验孤儿文件可发现、不得自动重写、人工对账可恢复 |
| P1 | 单机文件锁不保证多主机互斥 | 当前明确限制单机；企业多节点先建立统一幂等键、共享强一致租约/栅栏和事务外盒再开放 |
| P1 | 未做长期数据恢复压测 | 真实 10k/100k/1M 行、1GB 文件，注入磁盘满、SQLite busy、临时文件残留，测重试率、恢复时间、峰值 RSS/PSS 与 hash I/O |
| P1 | 任务日志本地 hash chain 不是独立审计 | 对关键审批写外部 append-only / 签名锚点；增加日志存储保留期和导出校验 |
| P2 | 历史发布门禁和旧版脚本较多 | 待 CI 对历史回归完成后统一路由与文档；目前只切换最新 CLI 入口并保留旧用例追溯 |

## 5. 复测命令

```bash
bash TIMEPILOT.sh source-check
bash TIMEPILOT.sh release-gate --mode source
python -m pytest -q tests/test_v0385_durable_publication.py tests/test_project_publication_jobs_v020.py tests/test_v0383_publication_reconciliation.py tests/test_v0384_publication_identity.py
node --test frontend/native-bi/tests/featurelab385_publication_journal.test.mjs
# 以下必须在目标 Mac / Python 3.12.12 具备真实依赖的运行机执行：
bash TIMEPILOT.sh release-gate --mode physical
bash TIMEPILOT.sh release-gate --mode browser
bash TIMEPILOT.sh release-gate --mode offline
```

此轮不自动更改 `TIMEPILOT_HOME`、不下载浏览器、不创建 Python 环境，不根据缺失的物理证据虚报生产性能。
