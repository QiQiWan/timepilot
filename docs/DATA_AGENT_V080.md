# 时骁 v0.8.0｜数据理解 Agent 与受控工具

## 1. 作用与适用范围

用户上传 ZIP、Excel、CSV 或 Parquet 后，在“智能数据导入”中选择候选工作表，点击“自动检查与生成方案”。本机执行文件完整性、布局、语义、清洗规则、抽样质量及时间序列构建检查，形成具备哈希和审计步骤的待确认方案。用户确认统计口径、来源及字段映射后，才允许调用已有的确定性转换引擎写出不可变数据版本。此步骤不会创建训练任务或启动模型训练。转换后应在 BI 中检查趋势、覆盖率、来源重叠与发布时间，再决定是否生成预测任务草稿。

此工作流复用 v0.6.1 的原始文件及版本引擎、v0.7.0 的 BI 与预测草稿；无额外 Python 环境，运行不调用 npm。

## 2. 工具清单与权限

| 工具 | 权限 | 输入 | 输出与说明 |
| --- | --- | --- | --- |
| `file.inspect` | 只读 | import_id，candidate_ids | 上传文件数、文件 SHA-256、工作表数量；仅显示部分文件，执行前全量核对选中源文件 |
| `layout.detect` | 只读 | 同上 | 候选表头、月度宽表/长表、年份/月份列、置信标记 |
| `semantics.map` | 只读 | 同上 | 时间、数值、系列、国家、品牌、统计口径、来源及来源网址字段建议；代码列强制人工核对 |
| `data.clean_preview` | 只读 | 同上 | 空白清理、数值转换、缺失与冲突处理规则（预览） |
| `quality.evaluate` | 只读 | 同上，可指定 version_id | 方案阶段仅样本检查；指定不可变版本后查询全量计数与质量率 |
| `series.preview` | 只读 | 同上 | 预计时间粒度、序列数量/单元格上界，不代替转换后全量结果 |
| `series.build` | **用户确认后写入** | 完整 Approval + 方案 SHA-256 | 委托 SmartImportService 后台生成不可变数据版本，记录来源与决策哈希 |

工具名称白名单固定，不能传入任意代码、SQL、Shell、路径、URL 或自定义工具。只读工具不能间接触发 `series.build`。同一方案的执行具有幂等性；已提交的决策不能更改，需另建方案。失败或进程中断可按原决策最多重试 2 次（总共最多 3 次），每次均核对原始文件哈希。

## 3. 人工确认规则

- **统计口径：** 对年度月度宽表，即使样本只出现一种口径也要求确认。用户可以选择单一口径，也可以明确“保留全部口径并分别标记”；不会自动混合销量、产量及工厂出货量。
- **来源：** 来源名称、来源网址都是来源线索。发现相关字段时要求确认来源保留政策。系统保留来源和冲突标记，去重/合并默认禁用。
- **汇总层级：** “源表汇总”与“车型月度明细”禁止在同一方案中处理；单独处理汇总表需额外确认，避免不同汇总层级互相相加。
- **代码字段：** `A2`、`A14` 等无法可靠推断目标与序列含义；非标准表必须人工指定数值列，系列 ID 映射由业务人员确认。
- **重复记录：** 默认全保留并标注。选择删除完全相同的观测，需要独立 `duplicate_review` 确认；原始数据仍保留。
- **发布时间：** `available_at` 不根据“采集时间 UTC”或时间列臆造。可由用户登记发布时间滞后假定并标注；正式防泄漏回测仍应以真实发布时间核实。

## 4. 大模型协作与隐私

默认关闭外部模型。仅用户主动勾选“使用已配置的大模型”时，才调用现有 `ModelGateway`。发送内容包含字段名、表结构类型、月度列数量、字段建议、用户输入的分析目标（该文本可能包含用户自行提供的信息）。不主动发送数据行、文件名、原始路径、来源网址值或密钥。接口传输至用户在“模型设置”选择的服务商，仍须遵守企业的数据外发政策。

大模型只返回 `summary`、`recommended_analysis`、`cautions`，由结构化白名单提取、长度限制后供页面展示。忽略额外的执行字段与权限请求；JSON/字段校验失败则报错，用户可关闭模型继续确定性处理。外部模型不具备工具执行权，不可自主写入、清洗数据、创建预测任务或启动训练。

## 5. 可复现性与数据流

```
文件上传与来源哈希
      → 候选区域发现 → 6 个只读工具 → 方案 JSON + SHA-256
      → 人工逐项确认（决策哈希）
      → 原始文件再次校验 → 受控 series.build
      → 不可变 normalized.csv + 来源文件/工作表/行/单元格
      → 全量质量统计 → BI 分析任务草稿 → 保存视图 → 人工准备训练数据
```

存储位置：`TIMEPILOT_HOME` 对应导入数据目录下的 `understanding/plans/<uuid>.json` 和 `understanding/executions/<uuid>.json`。训练任务不在 Agent 转换步骤中创建。保留旧版本，数据版本的 SHA-256 与数据源、配方一致。

## 6. API

所有路径以 `/api/v1/smart-import` 开头。

- `GET /agent/capabilities`：工具合同和是否允许自动写入。
- `POST /imports/{id}/agent/tools/{tool}/preview`：单个受控只读工具。
- `POST /imports/{id}/agent/plans`：生成可审阅方案。请求可包含 `candidate_ids`, `name`, `request`, `use_model`。
- `GET /imports/{id}/agent/plans`：当前导入的历史方案。
- `GET /imports/{id}/agent/plans/{plan_id}`：查看计划、步骤、样本依据与审批事项。
- `POST /imports/{id}/agent/plans/{plan_id}/execute`：需 `plan_sha256`, `confirm: true`, `acknowledged_issues` 和适用的 `scope_selection_mode` / `allowed_scopes` / `metric_column` / `field_mapping`。
- `GET /imports/{id}/agent/plans/{plan_id}/execution`：轮询执行进度、完整版本元信息和 BI 下一步链接。
- `POST /imports/{id}/agent/plans/{plan_id}/retry`：仅失败/中断任务，用同一决策恢复。

所有写接口遵循已有本地服务的访问控制边界，未来部署多用户公网版本前仍需增加独立认证、细粒度授权、配额与任务隔离。本版本用于现有本地单用户工作流。

## 7. 验收方法

```bash
conda activate server
bash TIMEPILOT.sh accept agent

# 在已安装 Google Chrome 的 Mac 上运行 HTTP + 浏览器端到端；不下载浏览器
TIMEPILOT_AGENT_BROWSER_TEST=1 bash TIMEPILOT.sh accept agent
```

还需执行项目原有 `python -m pytest -q`、前端 JS 测试、源码完整性校验。真实 Chrome 联动结果由 Mac 现场输出判断，不以合成 DOM 截图替代。

## 8. 现存限制

1. 复杂多表数据只支持当前确定性识别器可识别的候选时序结构；任意业务 Excel 布局、跨表 Join 与复杂公式需要扩展解析器，不执行模型生成的转换代码。
2. 大型源文件发现/转换会消耗 CPU、磁盘空间和时间；并发转换由现有任务引擎限制；不会自动下载/安装浏览器或 npm 依赖。
3. 模型建议是辅助说明，无法保证业务语义正确；来源、口径和字段含义仍由业务人员确认。
4. 当前环境无法验证目标 Mac 上 Perspective 的 Rust `View not found` 完整闭环；该问题与基础 BI 的 Agent 分析流程隔离。
