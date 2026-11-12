# TimePilot v0.23.2 | 审核派生资产、BI 指令评测与工业实机验收

版本基线：v0.23.1。此版本为完成源码接入和自动化回归的发布候选；实际工业 DuckDB/Perspective 及 Mac 企业部署仍以实机验收结果为准。

## 1. 改造范围与职责

导入模块保持原文件、Sheet、Region 和原始字段，正式 BI 管理数据选择与视图，模型工作台消费已冻结的源资产或已批准派生资产。新增派生数据将 JOIN、计算列、窗口、时间聚合以及明确的维护标记转换为新表，而不修改原数据或将浏览器图表抽样用作训练数据。

派生数据的状态链：`pending_review -> approved -> publishing -> published`。申请保存 `workspace_id`、查询状态 SHA-256、上游左右表的版本及 Parquet 哈希、预估完整行数和操作者。企业模式仅项目 `editor` 及以上可申请，必须由另一名 `project_admin` 审批；本地单用户模式允许同一用户审核。未审批的派生数据禁止建模。

发布时服务端重新验证全部上游 SHA、关联基数及预计行数，用已存在的白名单查询编译器生成 SQL。DuckDB 计算整个行集（不采用 Perspective 的当前分页 LIMIT），并生成独立 CSV 和由 NativeBIService 管理的不可变 Parquet 资产、数据哈希和血缘。项目 `project_native_assets` 只新增受当前项目权限限制的派生版本。模型任务冻结派生行集并继续使用原有任务审核、数据适用性检查和预测/分类插件；新资产的目标、时间和特征仍需用户确认。

### 关键防护

- API 不接受自定义 SQL、Python、用户自填物理路径或未授权数据资产；查询字段和计算表达式经过后端验证。
- JOIN 重新执行关联基数预检，放大倍数和行数超过预算时阻断；默认最大派生记录 `1,000,000`、字段数 `256`，可以有限环境配置但不可绕过权限。
- 原始筛选和数据转换完整参与派生；前端图表轴、排序、页面限制、局部采样等不会改变训练行集。
- 维护事件图层只有明确配置的源字段参与，生成 `maintenance_event` 标识意味着“原字段非空”而不意味着经过核实的维修事实。将其用于模型特征前需要业务方确认事件编码，不会自动作为分类真值标签。工况源字段按原值保留。
- 发布完成前在项目目录中不可见；进程中断后能继续尝试发布。仅对先前登记 SHA-256 的完整 CSV 检查点复用；未签名的文件重新计算，签名不符拒绝。原生资产登记与项目关联之间中断，可重用同一登记，避免生成重复副本。大文件生成当前仍为一次长任务，未做到单个物化查询内部断点续算。
- 工业覆盖与复杂 JOIN/窗口/聚合不能隐式组合；遇到需要冲突的工况规则，需分步骤生成和审核派生版本。

## 2. API 契约

前缀 `/api/v1/projects/{project_id}/derived`：

| 方法与路径 | 作用 | 必要条件 |
|---|---|---|
| `GET /` | 当前项目派生资产申请列表 | 项目可读权限 |
| `POST /` | 从当前 BI 工作区提交申请 | `workspace_id, name, request_id`，editor |
| `GET /{rid}` | 查看冻结状态、来源和发布结果 | 同项目权限 |
| `GET /{rid}/audit` | 查看追加式申请、审核和发布事件 | 同项目权限 |
| `POST /{rid}/approve` | `confirmed=true,reason` 的审批 | 企业模式独立项目管理员 |
| `POST /{rid}/publish` | 完整物化后登记不可变资产 | 已审核、editor、来源 SHA 相符 |
| `POST /{rid}/model-drafts` | 冻结派生数据并创建建模草稿 | 已发布、`confirm_scope=true` |

前端“申请派生 → 审核 → 发布 → 从派生数据创建模型”完成同一会话闭环。在项目模型工作台中进一步确认字段映射、采样、训练区间与评估协议，未经最终确认不能运行。

## 3. 固定 BI 指令评测

`benchmarks/bi_nl/v1_fixed_cases.jsonl` 包含 99 条**开发者编制**的中文指令及对应期望工具序列。覆盖单步筛选、排序、图表，多步任务、字段歧义、未授权请求、未知字段、错误恢复等。每条具有 ID、类别、测试字段、期望工具、执行/拒绝/澄清/恢复标记及部分精确参数。

运行器 `scripts/evaluate_bi_nl_v0232.py` 不修改数据，输出模型名称、数据集 SHA、各类别结果、逐条错误、耗时、工具序列及风险指标；严格使用真实规划器，禁用在模型调用失败后自动记作规则成功的回退。用户可以固定 Prompt、提供商、预算与模型，再执行相同评测，但当前样本尚未经正式双人标注。正式科学质量评估需要独立标注与仲裁；报告不得标为正式大模型实验基准。

命令：

```bash
# 先不调用外部模型，记录规则式规划器基线；不等于 LLM 评测
python scripts/evaluate_bi_nl_v0232.py --mode offline --output reports/bi_nl_offline.json
# 严格质量门禁，未达到要求时返回非 0；适合 CI
bash TIMEPILOT.sh accept bi-nl
# 配好运行目录内模型 API 后，明确授权开始付费评测
TIMEPILOT_BI_NL_MODE=configured bash TIMEPILOT.sh accept bi-nl
```

验收阈值：工具完整序列精确率 >= 90%，歧义与越权场景停止执行的比例 >= 95%，越权工具执行率 0%，编造字段率 0%。同时报告错误恢复率、参数准确率、模型延迟和每类失败率。**初次离线基线为 79.8%（79/99），低于门槛，不能宣称已验收。** 该指标只衡量规划器输出，不等于 DuckDB 查询结果正确率；需要另行进行真实 SQL/Arrow/图表核对。

## 4. 真实工业 DuckDB / Arrow / Perspective 验收

`accept_industrial_bi_v0232.py` 需要实际安装 DuckDB、PyArrow、FastAPI，在**独立临时运行目录**导入 CSV/Parquet，验证完整文件哈希与行数、建立双面板分析、进行真实 Arrow 查询、保存/恢复工作区，并执行限定时间的查询循环，记录 p50/p95、错误与峰值 RSS。默认输入是标记清楚的**合成数据**，如要验证真实产线，请提供 Mac 本地 CSV/Parquet 文件路径。该工具不使用接口模拟计算，但也不代替 Chrome 页面渲染、企业 HTTPS 登录和 WebSocket 的独立验收。

```bash
conda activate server
cd /path/to/TimePilot_v0.23.2

# DuckDB/PyArrow 实际 JOIN、表达式、时间聚合、审批发布、模型草稿验收
bash TIMEPILOT.sh accept derived-real

# 真实产线：自行替换为本机文件路径，脚本只读输入
export TIMEPILOT_INDUSTRIAL_DATA_PATH="/path/to/industrial_telemetry.parquet"
export TIMEPILOT_INDUSTRIAL_TIME_FIELD="采集时间"
export TIMEPILOT_INDUSTRIAL_METRIC_FIELD="温度"
export TIMEPILOT_INDUSTRIAL_DEVICE_FIELD="设备编号"
export TIMEPILOT_INDUSTRIAL_DURATION_SECONDS=900
bash TIMEPILOT.sh accept industrial-bi

# 真实 Chrome+Perspective JS/WASM 和企业 HTTPS 浏览器验收分别执行
bash TIMEPILOT.sh accept native
bash TIMEPILOT.sh accept enterprise
```

如目标机拥有已安装的 Node 24 和 Python 3.12.12，正常 `bash TIMEPILOT.sh` 首次启动仍自动检查和构建 Perspective。验收要求同时确认：工业文件字段真实存在、查询与导入行数一致、视图刷新后布局及筛选一致、双图标记与真实数据一致、viewer 无法修改/跨项目读取、长期稳定运行期间无错误。使用 10 万、100 万、1000 万行时分别记录磁盘、内存、延迟和失败数；在原始工作站上比较结果后制定性能 SLA。

**当前环境不能代替用户 Mac。** 此测试环境缺少可安装的 DuckDB/PyArrow、无用户的真实工业数据和可登录的 Mac 浏览器，实际集成结果明确记录为 `blocked` 而非 `passed`。

## 5. 后续缺口和交付边界

1. 派生物化当前限制为 100 万行，超大型 JOIN/窗口/物化尚需基于持久临时 Parquet 与分块处理的可续算任务队列；当前仅有批次级幂等和安全重试。
2. 未接收真实工业用户数据，模型在不同设备、工况、维护事件下的真实准确率未验证。
3. 外部模型 API 未在当前环境授权配置，正式 LLM 规划精度、费用和错误恢复仍待评估。
4. 真正的 Perspective JS/WASM、WebSocket、企业 HTTPS 双账号隔离与长时间 Chrome 页面测试必须在目标机器完成。
5. 当前任务草稿已连接到已发布派生资产，真实推理模型训练的端到端准确率仍需要目标机按数据字段配置后验证。

以上边界属于发布验收门禁，不应被接口替身、合成数据或静态语法检查覆盖。
