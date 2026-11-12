# TimePilot FeatureLab 2.0 v0.25.3—v0.25.6 实施说明与验收

基线：v0.25.2 完整源码。目标：修复审阅报告指出的数据可信性、作业生命周期、预览与原生可视化，以及复杂 Agent 任务闭环问题。

## v0.25.3｜数据契约、监督门禁与任务一致性

- `feature_contracts.py`、`feature_semantics.py`、`feature_safety.py`：确认时间角色时校验字段物理类型和时区，避免数值 ID 误认时间。
- `feature_compiler.py`、`feature_evaluation.py`：使用源哈希、任务摘要、契约修订、PIT 证据校验监督任务的配方与已物化产物，计划、提交及运行多处门禁；检查窗口内历史数据晚到风险。
- `feature_execution.py`：取消排队作业同时更新 job/run 状态；运行心跳、租约回收、幂等请求参数验证；企业环境限制状态读取。
- `project_migrations.py`：v23→v24（PIT 证据迁移），旧项目不删审计。
- 验收：数值字段时间角色非法、监督跨任务绑定、重复请求参数冲突、取消状态一致。

## v0.25.4｜独立预览与真实时长窗口

- `feature_execution.py`：`create_preview` 发起受控独立作业，有 `max_rows`、`scan_budget`，不会生成 `feature_artifacts` 或正式 `feature_runs`；全量作业仍独立产生不可变产物。
- `feature_operator_registry.py`、`feature_compiler.py`：注册真实 duration window，确认 event_time 后使用 DuckDB `RANGE INTERVAL`；保留实体分组和业务时间可用性校验。
- `feature_lab_v2_api.py`：新增 `POST /sets/{sid}/preview-jobs` 并提供 status/result 查询。
- `project_migrations.py`：v24→v25。验收：预览独立性、SQL 参数白名单、未确认时点拒绝、真实 DuckDB 数值测试另行执行。

## v0.25.5｜配方历史与 Perspective 工作区

- `feature_registry.py`：不可变 `feature_set_revisions`，修改产生历史记录；支持读取/对比；仅回填可复核的当前版本。
- `feature_viewer_state.py`、`feature_lab_v2_api.py`：按用户/项目/特征集保存真实 Perspective token，使用 `expected_revision` 与源 SHA 阻断过期覆盖。
- `native_bi_api.py`：有权限的原始版本与成功计算的 Feature Artifact 通过同一 Virtual Server 连接展示。
- `frontend/native-bi/src/main.js`、`static/feature-perspective-bridge.js`、`feature-builder-ui.js`、`feature-shell.js`：同工作区特征对照、保存、组件状态保持；不会因表单刷新而卸载 Viewer 容器。
- `project_migrations.py`：v25→v26。验收：前端模拟组件 DOM 连续性、工作区修订冲突与用户隔离；真实 Perspective 插件/保存恢复尚需本机 Chrome 联测。

## v0.25.6｜实验结果与可恢复 Agent

- `feature_tool_manifest.py`：根据当前意图、授权字段与实际可用算子检索工具集合，不再机械截取前 12 个。
- `feature_agent.py`：依赖步骤执行、运行状态观测与落库、等待异步任务、有限次数重规划；成功的有副作用步骤不重复执行。
- `feature_evaluation.py`：明确相同验证折才可做模型指标配对比较；无标签报告只陈列统计与物化证据，不虚构预测效果。
- `feature-evaluation-ui.js`、`feature-shell.js`：显示可读指标、状态、原因与后续动作，详细技术 JSON 默认折叠。
- `project_migrations.py`：v26→v27。验收：动态工具排序、模拟多步执行、状态恢复、折级结果对照及交互组件。

## 兼容性与真实运行边界

- 保持旧 `FeatureRecipe`、原始文件、资产标识、历史模型与审核入口可读取。
- `--mode offline` 仅可表明具体 Python HTTP/SQLite、Node 与 mock Viewer Chromium 组件通过；不得据此声称真实 DuckDB/Arrow/Perspective 联调完成。
- `--mode configured` 要求 Python 3.12.12、DuckDB、PyArrow、已编译的 Perspective 5.6.1 JS/WASM 与系统 Chrome。缺少条件返回 `blocked_environment`，数值测试另置真实计算证据。
- 即使原生 BI 的通用浏览器测试通过，仍需新增 FeatureLab 真实对照/保存/刷新/撤销端到端测试；未完成前明确记录 `pending_feature_viewer_real_e2e`。
- 必须为真实百万行数据、设备分组窗口、晚到记录和双用户权限建立独立性能/安全证据后才能作为生产正式版。

## 一键命令

```bash
conda activate server
bash TIMEPILOT.sh source-check
bash TIMEPILOT.sh
bash TIMEPILOT.sh accept feature-safety-v0253
bash TIMEPILOT.sh accept feature-preview-v0254
bash TIMEPILOT.sh accept feature-perspective-v0255
bash TIMEPILOT.sh accept feature-agent-v0256
bash TIMEPILOT.sh accept feature-agent-v0256 --mode configured
```

所有新阶段均保存机器可复查的 JSON 报告：`reports/featurelab/v0256/{stage}/acceptance.json`。
