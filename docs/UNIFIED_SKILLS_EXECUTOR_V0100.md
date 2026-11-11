# TimePilot v0.10.0：统一 Skills、可靠执行器与单入口

## 运行

仅启动以下一个入口。Mac/Linux：`bash TIMEPILOT.sh`；Windows：`TIMEPILOT.bat`。沿用当前 Python 3.12 环境，**不会创建环境，也不会在普通启动时执行 pip/npm 安装或下载浏览器**。已有数据应通过 `TIMEPILOT_HOME` 指向原数据目录。

| 命令 | 作用 | 行为边界 |
|---|---|---|
| `bash TIMEPILOT.sh` | 预检并启动本地 Web 服务 | 只读源码/环境检查，使用当前 Python |
| `bash TIMEPILOT.sh check` | 当前环境与系统诊断 | 不安装依赖 |
| `bash TIMEPILOT.sh source-check` | 完整源码哈希检查 | 检查隐藏文件和发布清单 |
| `bash TIMEPILOT.sh test` | Python/JS 回归 | 既有环境，不下载浏览器 |
| `bash TIMEPILOT.sh accept agent` | Agent 的 API 与回归验收 | 仅用已有依赖 |
| `bash TIMEPILOT.sh accept native` | 原生 Perspective 实机验收 | 需要本机现有 Chrome、DuckDB、Perspective、已编译 JS/WASM |
| `bash TIMEPILOT.sh package --output /path/to/file.zip` | 生成完整源码发布包 | 不运行 npm |
| `bash TIMEPILOT.sh package --with-native` | 打包已构建且验证的 JS/WASM | 不下载、不自动构建 |
| `bash TIMEPILOT.sh build --bootstrap-lock` | 发布机器主动安装锁定 npm 并构建原生 BI | **仅显式调用此命令时**使用 npm |

Windows 的同类命令使用 `TIMEPILOT.bat`。历史 `TIMEPILOT.sh` 等脚本为兼容入口，新用户统一使用 `TIMEPILOT.sh`。

## Tool Registry 和 Skills

`src/timepilot/agent_tools.py` 注册 16 个工具，分为数据理解、质量、BI、特征工程、预测准备五组。每个工具包含名称、用途、严格参数 JSON Schema、权限和估算计算单位。关联 Skill 说明位于 `skills/*/SKILL.md`，可以增补脚本和操作约束，但**不得任意导入或执行 Skill 文本中的代码**。

`GET /api/v1/agent/skills` 返回统一注册表及参数契约。模型只能在服务端允许的候选工具里提出下一步建议；模型无法创建新的工具、修改权限、传入未经验证的 SQL/Python/Shell 或路径，也无法自动启动训练。

## 持久化任务与状态

数据位置：`${TIMEPILOT_HOME}/agent_workflows/agent_jobs.sqlite3`（SQLite WAL）。创建任务：`POST /api/v1/agent/jobs`，查询：`GET /api/v1/agent/jobs/{job_id}`。前端入口：`/agent`。

状态：`queued → running → succeeded`。写入前必须转为 `waiting_approval`，由 `POST /api/v1/agent/jobs/{job_id}/approve` 通过**审批参数的 SHA-256**确认。取消：`/cancel`；失败重试：`/retry`。`needs_reconciliation` 代表写入状态未知，必须人工核对，不执行自动重试。

每步保留工具、输入哈希、输出 SHA-256、耗时、尝试编号、结果和错误。任务创建支持幂等键；跨进程通过 SQLite 原子租约认领，只读任务在租约过期后恢复，写入期间崩溃只标记待核对。任务暂停和进程重启不会清除已完成的检查点。

### 执行预算

`max_steps` 1—24，`max_units` 1—100，`max_seconds` 10—3600，`max_model_calls` 0—2，`max_attempts` 1—4。一次模型选择扣除 3 个相对计算单位。工具运行中通过租约心跳防止其他 Worker 重复认领。**max_seconds 是累积执行预算，无法强制抢占正在运行的第三方同步函数。** 取消能立即终止排队任务，对正在运行的工具在安全检查点生效；特征导出逐行轮询取消，其他旧工具主要在步骤边界生效。

### 五类任务

- `understand`: 上传文件结构、布局、语义、质量与系列预览；按证据条件调整检查次序。
- `quality`: 分析已存资产的质量及覆盖。
- `analyze`: 根据是否混合来源/口径，选择数值按月统计或仅展示样本网格；通用 BI 表通过画像工具分析。
- `features`: 计算按实体和可用时间门控的历史滞后特征；确认后导出新的内容寻址 CSV，不修改源文件、不默认作为训练数据。
- `forecast`: 先进行质量分析，再等待用户确认，调用已有预测草稿工具；不会自动训练。

可选模型仅接收目标、白名单候选工具和汇总统计，不接收原始行、文件名或来源网址。网络/模型错误会降级为确定性选择，并保存 `model_status`。旧版数据理解入口同样已改为在模型失败时保留检查结果与方案。

## 主要限制和验收

1. 原生 Perspective 的 JS/WASM 没有放入源码包；需在具备资源的 Mac 上完成编辑、回滚、重连的真实 Chrome 验收。新增 Agent 页面和基础 BI 不依赖 Perspective。
2. 本地服务只绑定回环地址，没有实现企业统一登录/RBAC，不应对外开放。
3. 已完成 SQLite/工具 API 级的崩溃恢复与审批测试。单步内部长时间阻塞无法硬杀线程；生产版建议引入独立 Worker 进程，按任务类型设置资源隔离。
4. Model 使用效果需要真实人工标注数据集，比较模型选路与确定性规则的任务成功率、人工修正量和实际费用。
