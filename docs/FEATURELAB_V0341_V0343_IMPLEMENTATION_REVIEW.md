# TimePilot v0.34.1—v0.34.3：FeatureLab 三阶段实施与审阅

## 本轮任务与可核实交付

**v0.34.1 · 自适应候选规划。** 自动分析在完成字段画像后使用 `feature_strategy.plan_candidates()`。按目标、数据结构、字段契约和来源数据的无标签描述统计构建候选。原有 `basic/temporal/industrial/quality/advanced` 模板继续可用，但自动入口固定调用 `adaptive`，不再默认逐列穷举“移动均值+差值”。使用受控算子、兼容字段、已确认排序/设备键，按算子族轮换，且在同一族内交错字段与算子。维持预算约束，记录策略摘要、候选来源及禁用原因。生成候选的质量通过不等于预测能力改善。

**v0.34.2 · 算子目录与页面统一。** 新工作台提供基础变换、历史变化、窗口统计、异常波动、变量交互、类别指标等候选分组、数量与筛选。每行正文点击查看详情；复选框独立控制选中状态。详情包含来源字段、计算参数、统计质量、已有模型选择证据和原有真实图表。按 `family` 过滤、焦点及图表模式保存在页面 URL。任务恢复继续复用已有 FeatureLab 分析会话及本地工作台状态。

**v0.34.3 · 高级算法统一候选管理。** 利用已有 tsfresh/Featuretools 高级任务与受控执行器，不引入新的 SQL 或 Python 动态执行路径。当前页面可以展开已有高级算法配置表单、按依赖状态显示可用性、提交预算/契约检查及人工确认。完成的高级候选通过 `/analysis/advanced-batches` 只读接口并入同一候选列表，并标明计算引擎、原始来源、SHA256、物化资产及 PIT 回接要求。点击高级候选显示独立详情；**高级候选不会被直接加入逐行 FeatureSet 或自动发布**。只有在既有 PIT 时间/实例核验、人工选择及同源模型评价通过后才能作为模型候选使用。

## 关键代码文件

| 模块 | 代码 | 工作内容 |
|---|---|---|
| 自动规划 | `src/timepilot/feature_strategy.py` | 安全字段筛选，目标驱动的多算子族候选，禁止越权时间算子，轮换字段和方法 |
| 候选持久化 | `src/timepilot/feature_candidates.py` | `adaptive` 模板，策略摘要及候选类别回执，原有模板兼容 |
| 自动分析衔接 | `src/timepilot/feature_auto_analysis.py` | 数据画像 → 自适应候选 → 实际预览，兼容大模型建议但只运行白名单 |
| 数据画像 | `src/timepilot/feature_profiler.py` | 新增来源绑定的零值频率与 10/50/90 分位数（可选聚合异常会显式标记不可用） |
| 高级候选桥接 | `src/timepilot/feature_candidate_bridge.py` | 项目/用户/版本范围、来源与原始字节检查、只读元数据规范化 |
| API | `src/timepilot/feature_lab_v2_api.py` | 已鉴权的高级候选查询接口 |
| 页面 | `src/timepilot/static/feature-workbench-ui.js`、`feature-shell.js`、`feature-advanced-ui.js`、`feature-lab2.css` | 类别筛选、详情图表、完整高级任务表单、焦点与 URL 状态 |
| 测试 | `tests/test_featurelab341_343_integration.py`、`frontend/native-bi/tests/featurelab343_unified_workbench.test.mjs` | 候选多样性、禁止泄漏、安全来源、前端审阅与交互 |

## 防泄漏及计算限制

1. 自动策略仅使用源文件统计质量、字段语义及已有的可执行算子白名单，禁止目标列、标识列、受限字段作为数值特征；未经确认的时间和设备分组不能用于历史/窗口计算。
2. 类别特征仅对已确认的允许类别字段生成；单条候选最多 2 个类别值。不为没有可信顺序的数据构造滞后/滚动算子。
3. 保留每个候选的来源、算子、参数和指纹，不将大模型建议当作计算指令。
4. 高级计算仍受原有行数、特征数、运行时间、资源预算、数据可用时间和人工确认约束；依赖缺失时基础功能可用，高级入口显示不可用。
5. 高级候选与普通特征可能不在同一数据粒度，单独存储，必须先通过逐实例 PIT 回接、来源/契约指纹复核和同折模型评价。候选列表可见不代表已可生产使用。

## 测试与验收

- 新增 Python 测试：自适应候选类型与字段覆盖、无时间排序时禁用窗口、受限字段隔离、高级元数据只读及来源失效检测。
- 新增 Node 测试：分组/筛选、普通候选与高级候选隔离、详情可见、可选高级表单、HTML 注入转义和 URL 交互契约。
- 保留以前的候选勾选与点击行为，已选项在筛选后不丢失。
- 发布文件由 `scripts/release_integrity.py` 生成完整 SHA256 清单；`scripts/verify_installation.py --source-only --strict-source` 验证版本一致性。

**当前环境限制：** 所运行的测试均属于源码、策略与接口契约测试；容器未具备 DuckDB/PyArrow/tsfresh/Featuretools 和目标 macOS + Perspective 环境，故真实 Parquet 高级计算、PIT 连接数值与 BI 浏览器图表未在此证明通过。不能将这些待验收项计为完成。

## 在原机进行现场验收

原有服务停止后整体复制源码覆盖（无需安装脚本），保留 `TIMEPILOT_HOME` 数据目录，启动前备份数据库。在现有 Python 3.12 环境中执行：

```bash
bash TIMEPILOT.sh source-check
python -m pytest -q tests/test_featurelab341_343_integration.py tests/test_featurelab332_real_duckdb.py tests/test_featurelab335_real_duckdb_charts.py
node --test frontend/native-bi/tests/*.test.mjs
bash TIMEPILOT.sh
```

现场分别用：无时间字段表格、已确认设备和顺序的工业时序、含类别及两个数值字段的静态表、已满足 PIT 契约的高级特征数据四种情形检查。确认候选族和字段覆盖、图表来源与数值、数据版本切换、任务重启、独立勾选、PIT 受控回接以及实际模型评价。高级依赖未安装时，应显示未就绪且不阻断基础分析。必要时按 `requirements-featurelab-advanced.txt` 在既有环境中补齐；程序本身不会自行安装。
