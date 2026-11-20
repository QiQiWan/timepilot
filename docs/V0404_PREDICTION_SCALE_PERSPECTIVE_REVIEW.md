# TimePilot v0.40.4｜预测资产容量、受控查询与 Perspective 逐行追溯

日期：2026-11-20；基线：v0.40.3；交付类型：源码候选版（物理验收单独判定）

## 1. 本轮具体交付

| 改造 | 文件 | 验收的真实含义 |
|---|---|---|
| 验证预测最多 1,000,000 行 | `model_prediction_assets.py`、`model_task_execution.py`、`model_task_unified_runs.py` | 增加单资产行数合同上限；现有训练数据输入限制保持原样，未证明百万行训练可在目标机完成 |
| Parquet 分批构建和行组过滤 | `model_prediction_assets.write_parquet_blocks()`、`model_prediction_commit.py` | 使用 32,768 行块避免整表 Arrow 分配；Parquet 行按模型/折/月/设备排序并保留签名 `__tp_rowid`；原有 v1 manifest 和 CSV 可追溯身份不变 |
| 服务端查询预算 | `model_prediction_query.py` | 环境变量 `TIMEPILOT_PREDICTION_QUERY_TIMEOUT_SECONDS`（2—60 秒，默认 15）；CSV 每 1,024 行检查时间；DuckDB 调用 `connection.interrupt()`；查询实际后端、耗时和指标范围会返回 |
| 原生 Perspective 稳定行键 | `native_bi_api.py` | 服务器在原有 `__tp_rowid` 上增加可见 `prediction_row_id`，不修改签名源文件 |
| 双向选择与拒绝聚合伪追溯 | `frontend/native-bi/src/prediction-selection.mjs`、`main.js`、`modeling-ui.js` | 表格/明细点击使用确定行键；分组/透视结果不可映射为单一源行；从模型结果点击则打开独立明细面板，保留原工作台图表 |
| 长连接撤销 | `native_bi_api.py` | 原生预测 WebSocket 约每五秒检查同源 session、项目授权、当前修订、模型运行与 SHA；撤销后关闭 1008 |
| 真实可重复负载脚本 | `scripts/accept_prediction_scale_v0404.py` | 临时生成 10 万/24 万/100 万行 CSV，核对过滤计数、分页和行级微平均；只有实际安装 DuckDB/PyArrow 时才额外运行 Parquet 查询一致性测试 |

## 2. 查询与资产合同

- 原有 `validation-predictions-v1` manifest 不变；v0.40.3 已归档资产不强制重建、不重新签名。
- 新 Parquet 行组物理顺序可能不同于 CSV，所有外部行标识均使用原始 `__tp_rowid`，查询必须显式 `ORDER BY __tp_rowid`。不能用 Parquet 文件第 N 行冒充训练记录 N。
- 授权路径：项目 → 已批准任务当前版本 → 运行状态 → 预测元数据 → 源文件和资产 SHA256。传入的筛选值只走参数化 SQL，前端不可提交原始 SQL 或文件路径。
- 查询范围的 MAE/RMSE 是*匹配验证预测行的样本加权统计*；同一个样本在多个折出现时可能重复计数，不能与分折等权的正式选模分数直接比较。
- 超时错误为 `PREDICTION_QUERY_TIMEOUT`。CSV 检查发生在扫描阶段；读取前的全文件 SHA256 本身仍未被独立进程级强制中止。单个查询的内存和多用户并发仍需目标机压测。
- 受控源码发布不改变 `TIMEPILOT_HOME` 和现有 SQLite/DuckDB/Parquet 数据，不创建临时 Python 环境。

## 3. Perspective 工作台交互

浏览器侧 `perspective-click` 只在未分组、未拆分、具有可见稳定 `prediction_row_id` 的表格/记录上执行来源查询。统计图表聚合点和无 ID 的组合记录不会被假定为一条源行。来源面板选择后通过单独 `tp-prediction-detail` 原生 Datagrid 面板定位到所选行，原始图表和过滤条件保留。

整个操作对比运行 ID、预测资产 SHA256 和任务 ID；切换任务、运行失效或源版本变化后自动拒绝旧操作。上述为**源码交互合同**；Perspective 5.6.1 的事件 payload 和 Chrome 的视觉效果仍需在目标机安装依赖后进行人工及浏览器自动验收，不能仅凭 Node 测试认定通过。

## 4. 安全与风险审阅

P0：当前执行环境没有 DuckDB、PyArrow、Perspective Python，也不属于 Python 3.12.12 的 macOS；因此 Parquet 查询实机压测、真实 WebSocket、Chrome 交互、目标 Mac 离线发行均为 BLOCKED。

P0：100 万行上限已经写入发布和查询代码，但普通建模的 `MAX_ROWS=20_000` 以及时序数据物化规模限制尚未提升。大模型训练峰值 RSS、多个模型多折的中间预测内存和多用户吞吐须另做实验。

P1：资产每次读取仍独立验证完整 CSV/Parquet SHA256。这样更安全，但百万行反复读取产生额外 IO。后续可引入 inode+size+mtime 之外的周期性校验与短期授权凭证；不能简单以 mtime 缓存代替 SHA256。

P1：Parquet 写入仍依赖内存中的 `records` 列表；本次优化的是 Arrow 转换内存，不等于真正的流式训练输出/流式事务发布。全面实现大批次增量结果发布需要版本化二阶段事务合同。

P1：已有原始数据血缘能追溯规范化观测与冻结源行；最初上传文件物理行号和分类插件完整历史输入窗口仍缺乏认证。

## 5. 复测与发布命令

```bash
bash TIMEPILOT.sh source-check
bash TIMEPILOT.sh release-gate --mode source
python scripts/accept_prediction_scale_v0404.py --source-baseline --out reports/v0404/scale.json
# 仅在目标 Mac Python 3.12.12、真实依赖和原生资产就绪后：
bash TIMEPILOT.sh release-gate --mode physical
bash TIMEPILOT.sh release-gate --mode browser
```

真实浏览器验收检查：项目 A/Predict A → WebSocket 正常打开 → Perspective 单行点击 → 左侧展示相同 `prediction_row_id` 的来源行 → 回到结果列表反选 → 原图不丢失 → 撤销项目权限后约 5 秒内 WS 被关闭 → 跨项目连接拒绝。

## 6. 与既有版本对比

本次保留四类训练任务、独立训练 Worker、冻结实验预算、折级证据与相同数据/预算对照。升级不触及已有训练器算法、不改变旧数据版本字段与 SQLite 迁移合同。建议下一阶段优先补齐真实 Mac 浏览器门禁，随后测量索引/分区方案的 p50/p95、峰值 RSS、100 万行并发吞吐，并完善分类历史窗口血缘。
