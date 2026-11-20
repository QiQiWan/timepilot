# TimePilot v0.33.0—v0.33.1 特征工程工作台：实施与审阅

## 一、交付范围

基线：v0.32.4 StartupFix1 完整源码；本次提升为 `v0.33.1`，保持原数据库 Schema、现有 DuckDB + Parquet 数据、Perspective 资源与特征审查/物化/发布链路。本版没有修改数据库表，没有新增 Python 第三方依赖。仍需原运行环境 Python 3.12.12 和现有依赖；源码版不包含编译后的 Perspective JS/WASM。

本版分别处理：

- **v0.33.0**：工作台结构调整、数据和版本切换、任务目标选择、空/计算中/待确认/待审阅/完成/失败/来源变化等状态，URL 中保存 `asset`、`version`、`candidate`、`filter`、`chart`。支持刷新重新加载、返回历史路由、切换版本时隔离旧图表与选择结果。
- **v0.33.1**：真实特征详情图表、类别指示算子、数值/分类/时序/设备分组图表；候选选择的完整状态保存；实际图表仅由 DuckDB 编译过的特征视图聚合生成，严格校验源 SHA256、特征候选、数据版本和契约修订。

## 二、页面结构及交互

1. 上部数据资产及版本下拉框，显示当前字段数。选择新资产或版本时清理当前候选、预览、运行和图表状态，然后按新上下文重新读取后端状态。
2. 自动分析的目标下拉框默认显示“自动发现特征”，可选异常/过程质量/分类/回归/时间预测；自由文本补充要求位于可展开区域。
3. 运行状态内联呈现。保留后台作业的 ID 和来源；后端 `needs_input`、`failed`、`stale` 对应不同的可操作说明；页内不再默认显示大量技术 JSON。
4. 分析完成后显示候选总量、质量通过数量和需复核数量。候选列表支持全部、通过、待复核、建议排除、已选。右侧显示业务名称、质量指标、算子定义、证据及图表。
5. 采用独立的“保存选择”及“保存并生成特征”两步。正式计算仍要求勾选确认，不自动发布；未完成可靠监督评价时明确标注“尚未验证模型贡献”。
6. URL 的候选、筛选和图表变化使用历史记录；重新打开页面按 URL 恢复，切换不同版本不复用旧结果。
7. 已修复一个重要交互缺陷：筛选列表后保存候选时，完整选择清单必须来自控制器状态，不能只读取当前可见的复选框。

## 三、四类计算与图表的实际口径

| 类型 | 计算 | 绘图数据 | 边界 |
|---|---|---|---|
| 数值 | 使用原有数值算子，编译真实 DuckDB 特征视图 | 全量有限值统计、最多 20 桶直方分布 | 空值、NaN、无限值不计入有限值样本 |
| 分类 | 新增 `category.is_value`，将某个已确认类别转换为 0/1（缺失保留 NULL） | 经确认的类别字段分组数量与新特征均值 | 禁止标签、restricted、未确认的类别字段参与自动候选 |
| 时序 | 复用 `rolling.mean`、`lag.rows` 等实际窗口算子 | 同一来源的前 120 条有序记录与原字段对照 | 仅在已确认时间/顺序且满足安全条件时使用，曲线明确标注有限预览 |
| 设备分组 | 确认设备键后按设备分组统计 | 按设备聚合记录数、有效数及特征均值 | 时序特征的窗口使用设备分区，禁止跨设备混合 |

图表返回 `source_sha256`、`version_id`、`contract_revision`、`candidate_id`、`candidate_fingerprint`、`scope`、`quality`、`points`。前端在源指纹、版本或契约不符时拒绝展示旧图。图表只代表特征计算与统计质量，不能单凭此判断预测效果提升。

**资源控制**：单个图表完整聚合最多 200 万行；超限直接返回预算提示。时序明细限定 120 行，其余图表由后端聚合查询，禁止把大批明细传入浏览器。

## 四、文件级变更

- `src/timepilot/static/feature-workbench-ui.js`：新工作台 UI 和 SVG 图表。
- `src/timepilot/static/feature-shell.js`：资产/版本选择、URL 焦点/筛选/图表恢复、异步图表请求防串线、完整候选选择保存。
- `src/timepilot/static/feature-lab2.css`：工作台双栏、状态条、详情和移动端样式。
- `src/timepilot/static/index.html`、`src/timepilot/static/app.js`：脚本接入与包含查询参数的路由处理。
- `src/timepilot/feature_workbench.py`：从真实 DuckDB 特征视图生成四类证据图表，前置检查来源与预算。
- `src/timepilot/feature_lab_v2_api.py`：新增 `GET /projects/{pid}/feature-lab/analysis/sessions/{sid}/candidates/{cid}/chart?mode=...`。
- `src/timepilot/feature_profiler.py`、`src/timepilot/feature_candidates.py`、`src/timepilot/feature_auto_analysis.py`：按已确认的类别取值发现类别指示候选。
- `src/timepilot/feature_operator_registry.py`、`src/timepilot/feature_compiler.py`、`src/timepilot/static/feature-builder-ui.js`：安全类别取值算子及手工编辑入口。
- `pyproject.toml`、`src/timepilot/__init__.py`、`frontend/native-bi/package.json`：统一发布版本号。
- `scripts/release_integrity.py`：发布清单中强制保留本次代码、测试、审阅文档。

## 五、验收结果与证据边界

| 需求 | 已实现与验证 | 验收情况 |
|---|---|---|
| 空状态 / 已完成 | 新工作台在控制器测试和浏览器 DOM 测试中呈现 | 通过（测试桩） |
| 运行中 / 待确认 / 失败 / 来源变化 | 前端状态模拟测试覆盖状态及错误说明 | 通过（状态测试） |
| 目标选择 | 六类目标 UI 测试、请求契约沿用旧接口 | 通过（前端单测） |
| 数据与版本切换 | 控制器回归模拟了跨数据和跨版本清空旧上下文 | 通过（控制器测试） |
| 刷新、浏览器后退 | URL 参数序列化、重新挂载及历史恢复经过控制器测试 | 通过（控制器测试）；真实浏览器地址栏导航待现场复验 |
| 候选筛选与完整选择 | 筛选下保留不可见但已选中的候选，提交后端验证 | 通过（控制器测试） |
| 数值计算 | SQL 生成与安全规则通过；全量图表与数据一致性测试已编写 | SQL/契约通过；真实 DuckDB 集成待运行 |
| 类别计算 | 类别算子、SQL 字面量转义、受控来源候选检验通过 | SQL/契约通过；真实 DuckDB 集成待运行 |
| 时序计算 | 排序字段与窗口编译以及跨设备分区检查通过 | SQL/契约通过；真实 DuckDB 集成待运行 |
| 设备分组图表 | 按已确认设备键聚合的接口和 UI 绘图已接入 | 接口契约通过；真实 DuckDB 集成待运行 |
| 候选图表与真实结果一致 | 统计摘要、桶频数、有限值计数联立校验的实测测试已编写 | 缺少 DuckDB 依赖时不能宣称通过 |
| 图表刷新后焦点与筛选恢复 | Node 控制器测试验证同页重新挂载及历史恢复 | 通过（控制器测试） |
| 来源与越权防护 | SHA256、版本、修订号、候选 ID 及图表模式校验 | 契约测试通过 |

在当前隔离环境执行：后端定向回归 **46 passed、5 skipped**（其中三项新增真实 DuckDB 测试因当前环境未安装 DuckDB 被显式跳过）；前端 Node 全量测试 **59 passed**；容器内 Chromium 使用内存 DOM 和模拟请求验证工作台渲染，没有 JavaScript 异常。当前隔离环境的 Chromium 无法访问普通 HTTP/file 导航，因此没有完成浏览器原生刷新/后退的现场验收。请不要将前端模拟数据的显示结果当成实际工厂数据验证。

## 六、安装及本机正式验收

在 Mac 现有项目目录执行（保持原 `TIMEPILOT_HOME`，不清数据库）：

```bash
python scripts/repair_installation.py \
  --archive "$HOME/Downloads/TimePilot_v0.33.1_FEATURE_WORKBENCH_FULL_SOURCE.zip" \
  --target . --apply
bash TIMEPILOT.sh source-check
bash TIMEPILOT.sh check
bash TIMEPILOT.sh db-check
bash TIMEPILOT.sh
```

运行本次专项测试：

```bash
python -m pytest -q tests/test_feature_workbench_contract_v033.py tests/test_feature_workbench_v033.py
node --test frontend/native-bi/tests/featurelab331_workbench.test.mjs frontend/native-bi/tests/featurelab331_interaction.test.mjs
```

要完成正式 v0.33.1 验收，需在已配置 DuckDB 的实际运行环境中确认 `tests/test_feature_workbench_v033.py` 三项用例**确实执行并通过，而非 skipped**，并使用安装好的 Chrome 对真实项目完成“刷新—切换数据版本—后退恢复—同源数值核对”的页面验收。

**当前已知限制**：自动分析的阶段衔接仍沿用原 `POST .../advance` 的浏览器轮询机制，网页关闭后可能暂停阶段切换；重新进入会按 Session 恢复。服务端全自动阶段协调器为后续独立工作。分类和分组图目前默认选取首个适用维度，完整多维选择器与可复用 BI 视图属下一阶段。静态无时间字段的完整监督评价扩展不在本次 v0.33.0—v0.33.1 范围内。
