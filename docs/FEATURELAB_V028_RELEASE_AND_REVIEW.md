# TimePilot FeatureLab v0.28.0｜迭代实施与质量审阅

日期：2026-11-12。以用户提供的 v0.27.0 完整源码为基础，延续 v0.26.2 动态表单及 v0.27.0 半自动工作台。

## 1. 本轮范围和结论

本轮完成面向通用数值与工业时序的高级特征算子、安全自定义公式、三类图形预览、全计算视图的质量统计和隔离式端到端验收工具。**目前仅证明源码接口、编译器、回归组件和模拟数据界面符合检查条件。当前执行机器缺少 DuckDB、PyArrow 和 Perspective Python / 已构建 JS/WASM，无法证明正式工业数据在该机器上完成过计算、Parquet 物化或真实 Perspective 面板渲染。**

可获得的数据范围：项目目录有设备、工艺及维修事件的结构化示例；此次没有真实来源可核实的产线测量文件。`scripts/featurelab28_acceptance.py` 无 `--csv` 时会自动生成 **216 行确定性测试样例**，其 `source_kind=generated_test_fixture`；只有通过 `--csv` 显式提供外部文件，才记录 `source_kind=external_user_supplied_provenance_unverified`，仍需业务方核对数据来源。程序将测试数据放入隔离的数据库目录，不修改正式业务库和原 CSV。

## 2. 已完成代码改造

| 改造项 | 实现位置 | 行为与边界 |
| --- | --- | --- |
| 高级算子 | `feature_operator_registry.py`, `feature_compiler.py` | `rolling.sum`, `rolling.median`, `rolling.p90`, `rolling.range`, `rolling.cv`, `rolling.zscore`, `relative.change`, `transform.log1p`, `transform.abs`, `transform.sqrt`；继续按原字段契约、实体/事件时间和可用性约束编译 |
| 可编译的自定义规则 | `feature_expression.py`, `feature_custom.py` | AST 白名单验证 `x0…x7`、四则运算与安全函数。限制长度、节点数、函数集、字段数、数值类型和单位。禁止运行任意 Python、SQL 或文件/网络操作 |
| 自定义公式 API | `feature_lab_v2_api.py` | `POST /expressions/validate` 返回已验证的 FeatureDefinition；正式保存走原 FeatureSet 修订流程 |
| 自动候选 | `feature_candidates.py` | 加入 `advanced` 模板，按已允许的数值字段生成确定性的高级特征候选，预算上限 60；固定源 SHA、版本、契约修订 |
| 预览统计 | `feature_visualization.py`, `feature_candidates.py` | 全计算视图上的缺失率/非有限值、均值、极值、标准差及近似分位数；图像样本数据与统计扫描数据分开报告，不输出“准确率提升” |
| 预览与产物恢复 | `feature_lab_v2_api.py`, `feature-shell.js` | 最近一次预览任务查询、当前 FeatureSet 最新经校验的物化产物查询；禁止误用过期来源 |
| 可视化交互 | `static/feature-builder-ui.js`, `feature-shell.js`, `feature-lab2.css` | 快速、手工、自定义三种模式；趋势图、样本直方图、样本原值—派生值散点图；自动隐藏非适用字段与参数 |
| 独立验收 | `scripts/featurelab28_acceptance.py`, `scripts/launcher.py` | `accept feature-lab-v028`；真实 CSV 模式执行隔离数据导入、候选计算、物化、SHA256 与 DuckDB BI 视图检查，并输出可复核 JSON |
| 版本和启动一致性 | `pyproject.toml`, `frontend/native-bi/package.json`, `index.html`, `api.py`, `verify_installation.py`, 发布清单 | 统一 0.28.0；源码检查支持 FastAPI 动态绑定 `__version__`；保留原有缓存校验及发布 SHA 清单 |

v0.28.0 不改变数据库 schema（仍为 **31**）。既有原始数据、数据版本、FeatureSpec V2、FeatureSet、工单、审批发布与 Perspective 工作区结构保持兼容。

### 2.1 算子与数值约束

- `rolling.sum`：窗口内求和；`rolling.median`：窗口中位数；`rolling.p90`：连续 0.9 分位数；`rolling.range`：极差。
- `rolling.cv`：滚动标准差与滚动平均的比值，均值为零时返回 NULL；`rolling.zscore`：当前值相对滚动均值和标准差的偏离，标准差为零时返回 NULL。
- `relative.change`：`(x_t-x_{t-k})/abs(x_{t-k})`；历史值为零时返回 NULL。
- `transform.log1p`：只对大于 -1 的数据计算；`transform.sqrt`：只对非负数计算；无效区间返回 NULL 并在质量统计中体现。
- 自定义 `x0`、`x1` 等变量由真实字段标识符绑定；`safe_div` 将零分母转为 NULL；加减运算检查两个已声明单位的一致性。不推断未确认单位。
- 原有窗口按输入记录顺序或已确认真实时间字段排序；**存在乱序设备时间而又未绑定有效事件时间时，不应把结果解释为时序预测特征。**

### 2.2 交互与可视化

- 工作台 `快速生成` 在字段推荐、模板、候选之间保持单一上下文；`手工构造` 根据算子注册表展示参数；`自定义公式` 显示字段别名、支持的数学函数和校验反馈。
- 候选预览新增趋势、直方图、散点图。趋势涉及双系列不同量纲时必须提醒不能直接以相同纵轴解释；直方图和散点图当前使用预览样本，不能代表全量统计分布。
- 数据分布、趋势及相关性的可视化没有自动写入正式特征资产；必须保存草稿并执行物化任务。
- 不含 tsfresh、Featuretools 全量依赖，这类多表和高维统计搜索保留为后续按需插件化能力；本轮以可检查、可重现 SQL 原生算子为主。

## 3. 质量检查证据

| 检查项 | 实际结果 | 结论 |
| --- | --- | --- |
| Python v0.28 专项、v0.27 回归及工业字段测试 | `42 passed, 1 skipped`，10.51 秒 | 通过；skipped 为真实 DuckDB 测试模块 |
| Node 原生 BI 构建/Hosted Table/工作区接口测试 | `14 pass, 0 fail` | 通过；仅契约与状态逻辑 |
| 浏览器页面（Chromium + 模拟 API） | 6 张截图，快速候选 18 条、字段 6 个、预览行 27 行；手工动态参数及自定义公式入口展示正常 | 组件交互通过；不构成真实 API / BI 渲染证据 |
| Python/JS 语法检查 | `compileall`，`node --check` 通过 | 通过 |
| 真实依赖检查 | DuckDB=false，PyArrow=false，Perspective=false | 阻断 |
| 实际工业 CSV | 没有已确认来源的实际产线数据文件 | 未执行 |
| 端到端真实计算与物化 | QA 脚本输出 `overall=blocked`、缺失 `duckdb/pyarrow` | 未执行 |
| Perspective 实际双面板 | Python / JS/WASM 原生资源缺失，当前截图使用模拟 API | 未执行 |
| 历史全量 Python 测试 | 多次合并运行触发时限，单独模块有若干通过记录；完整运行无最终报告 | 尚未闭合 |
| Windows + Python 3.12.12 | 当前开发容器为 Python 3.13.5 | 未执行 |

**验收规则：**不可将没有完成的步骤计入通过，不可将 216 行生成样例标注为真实工业数据；`passed_except_interactive_perspective` 也仅表示数据和 SQL 视图完成，浏览器面板仍需人工/自动化实际渲染回执。

记录：`/mnt/data/TimePilot_v028_python_qa.log`、`/mnt/data/TimePilot_v028_node_qa.log`、`/mnt/data/timepilot28_ui_qa.log` 和 `/mnt/data/timepilot28_acceptance_current_environment.json`（这些测试日志不包含在交付的源码 ZIP 中）。

## 4. 目标机器：真实工业数据验收步骤

### 4.1 准备

- 使用既有 Python **3.12.12** 环境。不得重建环境或重置 `TIMEPILOT_HOME`。在已联网或离线准备的依赖环境中确认 `requirements.txt` 的 DuckDB、PyArrow、Perspective 5.6.1 和 Playwright 可用。
- 实际数据应为 CSV，建议 UTF-8；有唯一时间/设备标识及至少两列真实数值传感器字段，每张文件至少 12 行，不含未经授权的个人或商业敏感信息。业务方应核实时间单位、排序、字段单位、设备/批次键和测量值的含义。
- 确认本机已有 Chrome，构建 `frontend/native-bi` 的 Perspective 5.6.1 前端资源（`bash TIMEPILOT.sh build` 或默认启动的受控构建流程；所需本地 npm lock 与依赖须齐备）。本源码包不包含预编译 JS/WASM，不得离线无依赖即宣称可用。

### 4.2 执行

Linux/macOS (bash)：

```bash
bash TIMEPILOT.sh source-check
bash TIMEPILOT.sh accept feature-lab-v028
TIMEPILOT_FEATURELAB_QA_CSV="/absolute/path/plant_measurements.csv" \
  bash TIMEPILOT.sh accept feature-lab-v028 --mode configured
```

Windows PowerShell（使用现有 Python 环境）：

```powershell
$env:TIMEPILOT_FEATURELAB_QA_CSV = 'D:\data\plant_measurements.csv'
.\TIMEPILOT.bat accept feature-lab-v028 --mode configured
```

只验证隔离端到端数据链路，也可以执行：

```bash
python scripts/featurelab28_acceptance.py \
  --csv "/absolute/path/plant_measurements.csv" \
  --report "/absolute/path/featurelab28_actual_acceptance.json"
```

不带 `--csv` 将使用系统自动生成的 **216 行样例**，并在报告中标记为生成数据。建议首先跑生成数据以验证环境，再换实际文件跑完整链路。实际报表中的 `input.sha256`、`source_rows`、`artifact.sha256`、`statistics_rows` 可以复核所用数据及产物一致性。

### 4.3 最终 BI 门禁

1. 运行 `bash TIMEPILOT.sh accept native --mode configured` 先检查真实 Chrome 与 Perspective 全局原生组件。
2. 通过用户实际生产数据的 FeatureSet 入口，在原生 BI 加载原始表和物化特征表两个面板，核实行数、字段、版本与数据源 ID。验证图表交互、刷新、保存视图、回退和无控制台错误。
3. 两个面板真实完成加载后，记录浏览器 DOM 或 Viewer 保存状态的结构化回执；如果仅完成 SQL 视图查询，保持 `perspective_browser_render=not_executed`。

上述双面板实际渲染仍需在具备全部依赖的目标机器完成。本轮脚本自动检查的是导入、后台特征计算、Parquet 文件签名和供 Perspective 加载的 DuckDB 视图，不提供已经成功渲染的虚假证明。

## 5. 后续缺陷与下一步

- **P0**：获取一份正式可使用的设备运行测量数据，落实字段字典、已确认事件时间和业务来源；运行 `configured` 真实验收并保留 JSON 证据。
- **P0**：构建与锁定 Perspective 前端 JS/WASM，补充真实 FeatureSet 双面板浏览器端自动验收，将 Viewer 回执与产物 SHA 关联。
- **P1**：对多设备乱序、采样间隔不一致、时间区间重置和维护事件窗口计算进行 DuckDB 真实数值对照。
- **P1**：对百万行、几十列的预览与物化做内存、响应时间和取消恢复验收，并以实测成本调整预算。
- **P2**：补充特征筛选与模型贡献评估，严格按训练/验证切分执行，避免标签泄漏。可按需集成 tsfresh/Featuretools，先确认许可证、依赖和成本。

## 6. 审阅结论

v0.28.0 的 UI 与源代码能力比 v0.27.0 扩展到高级统计、可控自定义公式和多种特征图形，旧有 FeatureSet 与 BI 保持兼容；实时计算、生产性能和真正的工业数据 BI 显示仍需实机证据才能宣告闭环。发布状态：**source validated / runtime E2E blocked**。
