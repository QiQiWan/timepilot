# FeatureLab v0.25.7—v0.26.0：独立审阅整改与阶段验收

## 目标与源码范围

基线为 TimePilot v0.25.6。采用向前兼容的项目目录迁移 `project_schema` v27 → v30，保留所有历史资产、配方修订、作业和视图；源文件哈希、模型与审批数据不做原地覆盖。

## v0.25.7：七项实际缺陷逐项修复

| 问题 | 对应修复 | 自动化证据 |
|---|---|---|
| 失败、取消或孤儿任务的产物可通过发布预检 | `feature_artifact_policy.py` 按真实 run/job 状态、固定配方、源哈希和磁盘内容校验；`feature_registry.py` 预检/发布复用 | `test_featurelab4_security_v0257.py` |
| 监督 TaskSpec 未传递至 Agent 配方 | 规划保留 TaskSpec、任务哈希，监督输入生成带固定任务的 FeatureSpec；未满足时拒绝 | 同上 |
| 非法二分类标签归为正类 | `feature_label_contract.py` 及 `feature_evaluation.py` 检查严格 0/1，展示每折标签质量 | 同上、`test_featurelab4_industrial_v0259.py` |
| 切换资产保留旧活动特征集 | `static/feature-shell.js` 资产上下文切换清理状态、丢弃迟到请求并保持合法路由上下文 | Chromium 组件与源码回归 |
| FeatureSet 修订后历史 Viewer 快照未失效 | `feature_viewer_state.py` 保存与恢复时绑定 `set_revision/spec_sha256`、最新来源 | `test_featurelab4_security_v0257.py` |
| request_id 不包含 TaskSpec | 计划持久指纹包括完整请求、源哈希与契约版本；冲突请求 409 | 同上 |
| 任意无效时区可确认 | `feature_contracts.py` 基于 IANA ZoneInfo 核实真实时区 | 同上 |

## v0.25.8：计算结果真实性与任务状态

- 引入**统一产物资格检查**。发布预检、正式发布、监督实验、HTTP 物化结果读取与原生 BI 特征 WebSocket，均核对当前项目与资产、成功的物化 job/run、版本修订、来源与配方哈希、文件路径和磁盘 SHA。正式监督任务额外要求全量 PIT 审计证据。禁止以 SQLite 产物行存在来判定数据可用。
- `feature_execution.py` 在物化元数据提交前检查实际租约持有者、取消请求、源哈希及配方修订；过期或取消中的执行不能提交成功产物。保留原先排队任务取消与运行状态同步。
- 真实算子测试：`test_featurelab2_real_duckdb_v0249.py`、`test_featurelab3_real_duckdb_v0254.py`、`test_featurelab4_real_duckdb_v0258.py`。**运行时缺少 DuckDB 时测试跳过，不能折算为真实验收通过。**

## v0.25.9：评价和维护事件

- 二分类标签域为真实 `0/1`；未知类别、缺失或非数值标签拒绝计算。报告展示每个验证折的样本数、类别分布、缺类警示、同折对照差值；固定时间切分，训练折内拟合。
- `event.since`：需已有**同一设备的布尔维护标记、真实事件时间、时区**。生成安全的设备分区窗口 SQL，计算距最近维护事件的秒数。源缺失事件或跨设备记录不被借用。
- 特征构造页新增**监督任务绑定表单**，设置预测目标类型、预测时刻、标签与标签可用时刻、时间/设备切分。评价页只读取既有 FeatureSpec 的 TaskSpec，不允许事后套用另一个任务。
- 该算子仍属于基础事件特征，尚不包含跨表维护事件关联、维修前后因果效应估计、完整工况状态机。

## v0.26.0：Agent 与 Perspective 回执

- 工具清单新增可验证的 `feature.viewer.open`，在完整物化结果可用后签发渲染请求。
- 对自然语言“生成特征并在 BI 显示”，按 `create draft → materialize → viewer.open` 分阶段执行。等待真实计算任务、等待 Viewer 图表回执分别进入 `awaiting_job` / `awaiting_viewer` 状态；后者由前端成功挂载原始表和完整特征结果后调用 `/render-receipt`。
- 服务端验证回执 token 的面板结构、实际锁定的 Viewer 版本、nonce、资产来源和最新产物哈希；重复、错误或过期回执拒绝。回执仅证明浏览器声称完成图表呈现，实际数值由 DuckDB 与 Artifact 审计独立核验。
- 计算失败、来源变化或窗口 PIT 检查失败不会强行进入任务成功态；保留现有有限重新规划、日志和人工审批分界。

## 数据库与文件改动

- v28：`feature_viewer_state` 增加配方修订和哈希绑定。
- v29：Feature Artifact 及 Job 租约检索索引。
- v30：`feature_execution_receipts`，记录独立的 Agent 图表回执。
- 核心新增：`feature_artifact_policy.py`、`feature_label_contract.py`、`tests/test_featurelab4_*.py`、`scripts/accept_featurelab4_stages.py`、`scripts/accept_featurelab4_ui_v0260.py`。
- 核心改造：`feature_agent.py`、`feature_execution.py`、`feature_registry.py`、`feature_evaluation.py`、`feature_compiler.py`、`feature_viewer_state.py`、`feature_operator_registry.py`、`feature_contracts.py`、`feature_lab_v2_api.py`、`native_bi_api.py`、`static/feature-*`。

## 验收等级与发布边界

- `source_component_only`：Python/SQLite/API、Node、模拟 HTTP + 已安装 Chromium 浮窗和表单检查。可作为持续集成与代码审阅基线。
- `real_duckdb_numeric`：安装目标 DuckDB/PyArrow 后用真实数据重复基础窗口、维护事件和晚到数据的数值测试。
- `real_feature_viewer_e2e`：在目标 Python 3.12.12、已编译 Perspective 5.6.1 JS/WASM、系统 Chrome 条件下运行真实 FeatureLab 物化 → Arrow/Virtual Server → 多面板 → 保存 → 刷新/撤销。该专项浏览器脚本仍待目标机建立，当前代码包不得标记生产级 E2E 通过。

Stage 命令及独立证据见 README；测试结果保存在 `reports/featurelab/v0260/<stage>/offline.json` 或 `configured.json`，运行环境门禁未满足时报告 `blocked_environment`。
