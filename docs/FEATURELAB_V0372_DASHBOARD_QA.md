# v0.37.2 数据一览仪表盘：实现与验收

## 本轮目标

在原有 v0.37.1 项目工作台中增加「数据一览」，覆盖多字段质量与统计、参数化过滤、Pearson 字段关联、可配置图表、数据版本切换和可恢复的 URL 状态。原有 FeatureLab 模型、单字段图和 BI 入口继续保留。

## 数据、权限与计算约定

- 固定基线：`benchmarks/featurelab_v0372/FIXED_DASHBOARD.csv`，12 条 **合成**记录。`MANIFEST.json` 固定 SHA256；不得宣称为产线实测。
- 通过 `FeatureDataAnalysis._source()` 验证数据源哈希与字段契约；项目读权限在服务端校验；受限字段不进入统计、筛选或关联列表。
- 读取版本化 `__tp_effective` DuckDB 视图；按白名单字段 ID 解析列名，过滤运算符严格枚举，比较值使用 DuckDB 参数占位符。
- 过滤条件最多 5 项，自动扫描最多 200 万行，字段统计至多 24 列，相关矩阵至多 6 个数值字段，散点最多 400 对确定性抽样点。
- 统计口径为当前版本的筛选全量。缺失率按已选字段计算：缺失单元数 / (筛选行数 × 所选字段数)。相关性使用 `CORR` 与两列同时有有限值的配对数。
- 数值均值、标准差、min/max、p10、p25、median、p75、p90，全部在 DuckDB 中计算。类别 TOP N 之外按剩余非空记录合并，不静默丢失频数。
- 趋势图要求明确确认 TIMESTAMP/DATE 的真实时间字段；序号不能自动冒充日历时间。每张图附结果版本和筛选 SHA256，不匹配即拒绝显示。
- 结果只代表统计事实；不自动推断异常、因果关系或模型预测收益。

## 后端接口

`POST /api/v1/projects/{project_id}/feature-lab/data-analysis/dashboard`

```json
{
  "asset_id": "source-id",
  "version_id": "base",
  "expected_sha256": "当前数据源 sha256",
  "expected_revision": 2,
  "filters": [{"field_id":"field1","op":"eq","value":"A"}],
  "field_ids": ["field2","field3"],
  "association_ids": ["field2","field3"]
}
```

返回 `rows_total`、`rows_filtered`、`metrics`、`fields`、`correlations`、`scope_sha256` 与来源版本。所有数值由当前有效数据版本实际计算。

`POST /api/v1/projects/{project_id}/feature-lab/data-analysis/dashboard/chart` 沿用以上来源与筛选字段，另外支持 `kind`（`histogram` / `box` / `quantiles` / `category` / `scatter` / `trend`）、`field_id`、`other_field_id`、`bins`、`limit`、`granularity`。数值图可调整直方图组数；类别图可调整 Top N；趋势图支持 hour/day/week/month。

## 页面交互

- `概览`：数据一览，四项总体指标、筛选行数、卡片图表、字段质量、字段关联和技术口径。
- `统计图表`：同一个数据一览及原有单字段统计，可展开详细记录。最多增加 6 张图，调整图型和字段；配置与筛选保存在 `da_cards`、`da_filters` URL 参数中，刷新可恢复。
- `质量检查`：保留专门质量检查区域。版本或数据资产变更时，清除不适用的字段、候选、图表配置及筛选信息，再根据新来源重新计算。
- 请求附 `expected_sha256` 和 `expected_revision`；旧请求返回时再次核对源 SHA、字段契约、筛选指纹，避免异步响应覆盖新版本。

## 正式物理验收

```bash
python scripts/featurelab372_dashboard_acceptance.py --out reports/v0372/dashboard_physical.json
python -m pytest -q tests/test_featurelab372_dashboard_contract.py tests/test_featurelab372_real_duckdb.py
node --test frontend/native-bi/tests/featurelab372_dashboard_ui.test.mjs
```

验收使用 DuckDB 将固定 CSV 实际写入 Parquet，并独立执行 `COUNT`、`AVG`、`MEDIAN`、`STDDEV_POP`、`COUNT(DISTINCT)`、`CORR` 和条件聚合，逐项对照仪表盘 API；还核查筛选后的各图频数、版本切换均值变化、源哈希拒绝和权限拒绝。**缺少 DuckDB 时脚本退出码 2 = BLOCKED，不得据此宣布物理验收通过**。

浏览器端的静态/状态测试测试了动态筛选、配置增删、URL 恢复和异步响应来源检查；真实 Chrome 视觉及筛选/版本往返另需实际浏览器验收，不能用模拟测试替代。
