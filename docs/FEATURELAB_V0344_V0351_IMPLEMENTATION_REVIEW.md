# TimePilot FeatureLab v0.34.4—v0.35.1 源码迭代与验收审阅

审阅基线：v0.34.3 完整源码。交付方式：完整源码 ZIP + 可直接覆盖的差异 ZIP；保留原目录与现有运行数据。

## 1. 范围、实现结果与边界

| 阶段 | 实际修改 | 当前状态及证据 |
|---|---|---|
| v0.34.4 可信图表 | 新增 `feature_chart_semantics.py` 全范围等记录数分桶统计、min/max/mean/count、设备隔离、时间范围和分辨率筛选，统一返回聚合范围；改造数据分析与特征详情 API、UI；缺失数据不画成零值 | SQLite 独立数值聚合测试通过；真实 DuckDB/Parquet 的专项门禁已编写，当前容器缺依赖，待 Mac 执行 |
| v0.34.5 搜索规划 | 16/32/64 项预算，按轮次记录候选类型、输入字段覆盖、质量状态、来源指纹、完整性和停止原因；候选数量上限从 24 扩展至 64；修复缺少候选质量回执仍误称预算用完 | 固定候选与来源指纹复现测试通过；完整的**按反馈追加新一轮候选、冗余聚类和边际收益自动停机**尚未实现 |
| v0.34.6 模型对照 | 保留已有同折 raw/with/selected 模型协议；新增训练折拟合、外层验证折评分的特征组再拟合消融，并汇总组级敏感性 | 固定回归和静态/时间切分测试通过；**独立最终留出数据集锁定与一次性正式评价**尚未落到生产接口；不能声称生产收益 |
| v0.34.7 高级算法 | 读取高级任务产物并按项目/用户/来源/数据指纹核验；结合已核验 PIT join/evaluation，统一输出“待关联—已关联—已评价”；同页定位目标高级任务 | 接口/模拟持久化测试通过；由于缺少 tsfresh、Featuretools、DuckDB，没有完成真实高级 PIT 计算性能/数值验收 |
| v0.35.0 统一工作台 | 已有数据发布后自动基础画像，新增 FeatureLab 顶部同源数据质量诊断和完整分析入口，模型同源证据下显示组消融；支持设备/分组、时间/精度筛选和 URL 恢复 | 前端 Node 集成回归通过；独立 Perspective 浏览器 E2E 待本机确认 |
| v0.35.1 验收 | 新增 SQLite/固定合成信号精确测试、DuckDB+Parquet 物理测试、本机一键验收脚本；明确区分 passed/failed/blocked | 容器依赖缺失时标 blocked，不计为通过；Mac 实际运行/百万行性能需现场测量 |

## 2. 正确性和安全设计

### 2.1 时间图表

- 设备和时间字段必须属于已确认、非受限的字段契约。若存在多个设备，默认仅统计记录最多的**一个设备**，并显示该选择；用户可选设备值及设备字段。不会将不同设备轨迹连接成连续曲线。
- `series_aggregate()` 对选定设备及时间范围的**全部有效排序记录**分桶，返回桶开始/结束时间、均值、最小值、最大值和计数；突出后段极值，防止早期 120 条截断。
- 原始 `source_sha256`、契约修订、数据版本与候选指纹由既有服务校验。图表读接口没有生成、物化、发布等副作用。
- 数值 NULL 被保留为缺测，前端不把 NULL 当作零；根据来源返回的统计范围做 SVG 绘图。
- 待加强：时间桶的实际横轴使用桶序号，时间不均匀数据在屏幕上是等间距显示；有需要时改为连续时间坐标。极值保留不等于保留尖峰的精确发生时间，需放大时间窗口查询原始记录。

### 2.2 候选搜索

- 规划只依据字段角色、元信息、无监督描述统计、白名单算子；模型标签和验证集收益不进入全局候选排名。
- 质量摘要分轮保存 `quality_verified`, `search_evidence_sha256` 和 `stopping_reason`。预算为快速 16、标准 32、深入 64。
- 旧版 `basic` 等模板及已存在候选任务保留，新数据可重新分析选择新预算。64 项计算在源数据较大时可能耗时明显增加，建议先用 16 项验证数据契约和计算稳定性。

### 2.3 特征组消融

- 在既有外层训练/验证折上，先用训练折确定所选特征，再分别移除算子组，使用同一模型、同一折重新拟合/评分，最多复算 3 个组。
- 报告 `mean_loss_when_removed`, `positive_loss_folds`, `evaluated_folds`。组选择采用固定规格确定的排序，不根据验证集标签选择展示对象；该证据是**预测敏感性**，不能提供因果解释或对外部署承诺。
- 独立最终测试集与完整嵌套验证尚未上线，仍属于下一步的核心算法闭环。

### 2.4 高级计算

- 高级候选和普通逐行特征有不同数据粒度。显示为同一候选池，但不勾选混入普通 FeatureSet。
- 已通过 PIT Join、来源文件哈希及模型评价校验的候选才显示相应后续状态。高级引擎不在无依赖时自动执行或绕过算子/计算预算。

## 3. 关键源文件

| 文件 | 用途 |
|---|---|
| `src/timepilot/feature_chart_semantics.py` | 时间图表共同 SQL 统计与真实范围说明 |
| `src/timepilot/feature_data_analysis.py`, `feature_workbench.py`, `feature_lab_v2_api.py` | 图表来源、服务及查询参数 |
| `src/timepilot/feature_strategy.py`, `feature_candidates.py`, `feature_search_orchestrator.py`, `feature_auto_analysis.py` | 候选规划、预算、轮次、质量证据 |
| `src/timepilot/feature_ablation.py`, `feature_evaluation.py` | 模型组消融与报告 |
| `src/timepilot/feature_candidate_bridge.py` | 高级特征受控模型评价状态回接 |
| `src/timepilot/static/data-analysis-ui.js`, `feature-workbench-ui.js`, `feature-shell.js`, `feature-advanced-ui.js` | UI 及分析任务连接 |
| `tests/test_featurelab344_351_integration.py`, `tests/test_featurelab344_real_duckdb.py` | 固定数据和真实物理聚合测试 |
| `frontend/native-bi/tests/featurelab351_integrated_ui.test.mjs` | 页面同源、来源失效、消融隐藏与过滤状态 |
| `scripts/featurelab351_acceptance.py` | 现有环境中执行源码、计算、UI 验收并生成日志压缩包 |

## 4. 环境、测试与未完成项

在当前容器运行了 Python 定向回归、Node 前端全量回归、SQLite 独立时间图表计算、静态模型对照以及源码清单校验。SQLite 结果不能替代 DuckDB 实机数值。真实 DuckDB/Parquet 及 Perspective 需要在目标 Mac 已有环境中执行。

执行：

```bash
bash TIMEPILOT.sh source-check
bash TIMEPILOT.sh db-check
python scripts/featurelab351_acceptance.py
bash TIMEPILOT.sh
```

日志：`reports/featurelab351_acceptance/ACCEPTANCE.md`、`ACCEPTANCE.json` 及 `reports/featurelab351_acceptance.zip`。任何关键测试缺失或 `skipped` 均显示 `blocked`，不能当作生产验收通过。

需要补跑：真实 DuckDB/Parquet 数值与图表逐桶比较；Perspective 本机 Chrome 的选择、刷新、后退；Worker kill/restart；百万行峰值 RSS 和耗时；tsfresh/Featuretools 高级 PIT 与结果；最终独立测试集的方案冻结与正式收益结论。

## 5. 后续优先事项

1. 搜索 S2/S4 的**真正增量扩展**：计算第一轮质量后，计算候选间冗余，再按实际质量和预算选择下一批，并保留全部状态与 SHA；此功能不能仅靠轮次标签替代。
2. 将独立最终留出集、未见设备/工况、完整嵌套选择与最终一次性评分纳入正式模型评价 API，严禁根据最终测试反馈反向调整参数。
3. 根据 Mac 的 DuckDB、Perspective、Worker 和真实工业数据性能报告消除现场缺陷，再考虑稳定基线。

**当前交付定位**：v0.35.1 源码集成与可执行验收候选版本，生产级验证仍有明确门禁未闭合。
