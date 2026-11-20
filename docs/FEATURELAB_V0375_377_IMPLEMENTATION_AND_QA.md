# v0.37.5–v0.37.7 数据分析模块实施与验收

## 实施范围

**v0.37.5 故障收敛**

- `feature_dashboard_sql.category_sql()` 统一旧 GET `/data-analysis/chart?mode=category` 与新 POST `/data-analysis/dashboard/chart` 的 DuckDB 类别 SQL。别名使用 `category_value` / `category_count`；仅允许已授权字段及 `__tp_effective`。参数化筛选由服务端编译。
- `GET /data-analysis/chart-capabilities` 给出可使用的字段及时间序列能力；未确认真实日历时间不可启用日期分桶。
- 每张图表使用稳定 ID 和独立请求令牌，快速连续切换只查询该卡；整页最多两条查询并发，过时结果不回写。
- 500 返回诊断编号；每次真实失败有一条简短事件，首次错误含脱敏代码栈。根因指纹增加图表类型和 SQL 模板 ID，次数按真实事件累加。

**v0.37.6 页面与统计**

- 概况、质量检查、统计图表三个任务按实际 DOM 内容呈现；统计页才加载多维查询，自动特征页面由用户激活时加载。
- 统一多字段箱线图采用 Tukey 定义，Q1、Q3、IQR、1.5 IQR 内的真实须端及离群点计数；不同单位独立刻度。未计算 KDE 的小提琴图继续禁用。
- 数值字段分别统计 SQL NULL、有限值与 NaN/Inf；缺失与无效不能混同。字段关联提供有预算约束的 Pearson 与 Spearman；Spearman 按有效配对计算平均秩，默认排除 ID、顺序和时间角色。

**v0.37.7 性能与交付**

- 仪表盘概览增加最多 32 份、TTL 75 秒的缓存，命中前重新验证项目和数据 SHA256、契约版本及过滤条件。
- 支持下载最多 500 条安全日志的 ZIP，内含 JSONL、根因组摘要和 JSONL SHA256。长期历史日志受轮转限制；归档不能推断生产故障已解决。
- 加入 `scripts/featurelab377_dashboard_acceptance.py` 对固定 12 行 CSV→真实 Parquet→DuckDB 图表及独立数值进行强制核验。缺少 DuckDB 返回 `BLOCKED` 退出码 2。
- 加入 `scripts/featurelab377_dashboard_performance.py` 分别在 506、1 万、10 万、100 万行生成真实 Parquet，记录 DuckDB 精确聚合耗时、进程峰值 RSS 和预算判定。只反映当前机器，未经同机旧版基准不声明加速率。

## 验收

```bash
python -m pytest -q tests/test_featurelab377_dashboard_regression.py
node --test frontend/native-bi/tests/featurelab377_chart_race.test.mjs
python scripts/featurelab377_dashboard_acceptance.py --out reports/v0377/physical_dashboard.json
python scripts/featurelab377_dashboard_performance.py --out reports/v0377/dashboard_perf.json
```

Mac Chrome 物理验收应进入现有 `housing.csv` 统计页，分别切换类别、直方图、箱线、散点、分位数、时间趋势（仅确认日期后启用），至少连续 30 次，核对页面/接口无 500、同源 SHA 和筛选范围，以及 `TP-` 诊断日志。无法连接目标 Chrome 或未执行时须标记 `BLOCKED`。

## 不能推断的事项

源代码和 mock UI 测试不证明当前 Mac 的真实 SQL/浏览器已通过；在线数据来源、实际业务异常、模型预测收益和大规模复杂分组性能，需要现场额外证据。
