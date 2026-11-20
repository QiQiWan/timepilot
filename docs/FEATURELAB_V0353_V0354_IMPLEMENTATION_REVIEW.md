# TimePilot v0.35.3—v0.35.4 实施与验收审阅

## 版本目标

- v0.35.3：图表能说明统计范围、坐标意义和观察结论；数据质量、数据分析、特征工程不再出现反复的“继续”跨页跳转；刷新保持来源和当前位置。
- v0.35.4：导入数据发布后，以同源版本默认进行受控基础画像和特征候选预览，统一展示质量、图表、特征和成果；默认不依赖大模型 API；高级手动入口保留。

## 具体实现

1. `feature_chart_insights.py` 根据可核验的图表聚合值生成结构化说明（横纵轴、分组、时间范围、最高/最低桶均值、桶内极差、类别数量和限制）。与新特征量纲不同的原字段不再绘制在同一个坐标轴。
2. `feature_data_analysis.py` 和 `feature_workbench.py` 将解释挂载在原有同源图表响应上，维持来源 SHA256 与契约修订号检查。数值结论只来自聚合数据，不自动推测故障成因或预测改善。
3. `data-analysis-ui.js` 复用字段画像绘制前 8 项字段缺失比例，提供多字段质量概览，无须额外数据库扫描；修正时间图取值（原先可能使用记录数代替均值），显示时间桶开始、中间、结束的实际时间刻度；展示“画了什么 / 主要观察 / 注意事项 / 下一步”。技术证据折叠。
4. `analysis-workspace.js` / `analysis-workspace.css` 创建一页五阶段导航；`project-ui.js` 将历史 `/features/analysis` 映射至统一 `/analysis?section=features`；删除“继续”按钮导致的任意跨页跳转。资产、版本、图表和步骤使用 URL 状态，数据版本变更清空旧来源字段和候选定位。
5. `feature_analysis_journey.py` 直接复用 `FeatureDataAnalysis`、`FeatureAutoAnalysis`、持久任务 Worker 和安全源码核验。导入成功后在原有发布 Worker 内触发一次 16 项探索性候选；上限 200 万行及 256 列。未经用户确认，不进行正式特征物化、模型评价或发布。
6. `feature_lab_v2_api.py` 增加 `GET /workspace`、`POST /workspace/start`（均在项目 FeatureLab API 前缀下）；根据来源版本和 SHA256 复用会话，不重复建立预览任务。
7. 旧高级算子、tsfresh、Featuretools、手动特征工作区和模型评价接口仍在；自动模式只使用低成本安全策略，未配置模型 API 时可运行。
8. 修复正式入口 `index.html` 遗漏注册新工作台 JS/CSS 的问题，增加资源注册测试，避免源码存在但前端运行找不到控制器。

## 自动化测试

`tests/test_featurelab353_chart_insights.py`：分布/类别解释、实际时间桶范围、未分设备警告、不同量纲隔离。

`tests/test_featurelab354_journey.py`：无模型幂等、来源指纹阻断、HTTP 状态读取不产生新会话。

`frontend/native-bi/tests/featurelab354_unified_workspace.test.mjs`：旧路由兼容、URL 参数、图表和来源切换、静态资源注册。

`python scripts/featurelab354_acceptance.py`：当前环境自动运行上述及相关旧回归、全部 Node UI 测试；缺 DuckDB/PyArrow 的真实计算标记 blocked，非零退出，不安装依赖。

## 验收边界

- 本次容器的 Python/Node 与 Chromium DOM 模拟只证明功能和前端组件控制流，不代表 Mac 真实服务、Perspective WebAssembly 图表和工业数据完全验收。
- 必须在已有 Python 3.12.12 + DuckDB/PyArrow 环境重跑物理 SQL/Parquet 验收；Chrome 直接使用已安装浏览器，无需下载。
- 模型 API 开启时可能提供额外建议；核心质量、图表和低成本候选在模型关闭及 API 故障下仍需要保持可用。
- 若来源契约需要人工确认，时间窗口和其他受约束算子仍然禁止越权执行；自动预览不会强行绕过。
