# TimePilot v0.40.3 — 预测资产查询与多层血缘技术审阅

2026-11-20 · 基线 v0.40.2 · 交付形式：源码候选版（不等于 Mac 离线发行版）

## 1. 修订范围与结论

本次直接修改四类建模运行的公共查询层，保留原有模型选择、训练、分折、预算和 SQLite 资产提交合同。

| 改造项 | 具体实现 | 证据边界 |
|---|---|---|
| 普通回归/分类的来源行 | 训练结果签名中增加 `source_lineage_schema` 与完整有序输入行集 `source_rowset_sha256`；通过验证预测 `source_row_index + source_row_sha256` 回查审批版本的真实记录 | 认证冻结输入数据行及内容；原始上传文件物理行号不做保证 |
| 连续时序的历史窗口 | 根据已签名的 `train_index_sha256`、series、fold、验证开始时间，从原模型实际使用的规范化数据重建历史窗口并核验；分页返回每条历史观测到所有筛选源行的关系 | 认证输入观测记录集合；不证明插件内部每个 X 的实际权重、使用路径或因果贡献 |
| 验证预测的筛选查询 | `GET .../predictions/query` 允许模型、分折、月、设备、绝对误差阈值、offset/limit；严格参数白名单；按选择后的逐行预测重新计算微平均 MAE/RMSE | 该范围是筛选后预测记录的聚合，禁止直接与“折等权平均”的正式选模分数混淆 |
| DuckDB 查询路径 | 安装 DuckDB 时使用只读签名 Parquet 和 DuckDB 条件过滤/排序；CSV 环境使用逐行扫描降级，并返回实际查询后端 | 当前环境没有 DuckDB/PyArrow，不能宣称 DuckDB 实机性能和 Perspective 浏览器完成验收 |
| 页面交互 | 结果页筛选、分页、逐样本选择、来源核验及历史窗口查看；复用原生 Perspective 挂载节点而不销毁 | 侧栏点击可选中预测行；Perspective 图表内部原生数据点点击事件仍待浏览器端证实 |

### 1.1 普通表格输入行的正确性

普通模型训练使用冻结的有序输入表。`tabular-selected-row-v1` 记录完整输入 DataFrame 的确定性 CSV 字节哈希，并将其写入 `result_json`；既有两阶段提交同时对结果 JSON 与预测资产签名。追溯时重新经过项目与任务授权，读取同源版本，检查当前数据 SHA、行数、行顺序及结果 JSON 签名，最后核对源行的 `source_row_index` 和 `source_row_sha256`。查询仅返回对应冻结行的实际列值，不引用其他任务的源表。

非 base 编辑版本通过 `ORDER BY "__tp_rowid"` 固定物化输入顺序，减少 DuckDB 任意行顺序导致的误关联。如果历史旧运行没有行集签名，则返回 `legacy_lineage_not_recorded`；绝不补造旧证明。

目前使用“签名的有序源行集合 + 签名的预测行索引”提供逻辑边表；尚未额外写出每个训练输入行的独立 Parquet 物理边表，避免在崩溃恢复前引入第二套不一致的事务。建议在 v0.40.4 接入完整二阶段边表发布。

### 1.2 时序历史窗口的范围

`GET .../predictions/window-lineage?row_id=<int>&offset=0&limit=100`

首先执行原有目标观测的完整源数据/审批/血缘校验；接着使用已签名折的 `fold,series_id,first_validation_event_utc,cutoff_utc,train_index_sha256` 重建输入记录，核对所有训练历史观测哈希及可用时间，再从 v0.40.2 的不可变 `selected_row_lineage_v1.json.gz` 返回映射。`source_edge_total` 是完整历史窗口的关联数量，`source_edges` 每页不超过 100 条，不泄露未授权项目源字段。

对旧分类器的训练窗口没有完整、唯一的历史输入逐行索引，返回 `classification_history_membership_not_certified`。同一设备训练历史不是“新设备零样本泛化”的证明。多变量插件的逐特征使用证据仍需插件接口扩展，当前只记录实际传入的历史输入观测成员。

### 1.3 分析查询语义

`GET .../predictions/query?model=forest&fold=1&month=2025-01&device=A&min_abs_error=0&offset=0&limit=100`

所有输入值经类型/正则/有限性检查，SQL 只使用服务端预先列名和参数，不接受自定义查询语句或路径。完整资产获取每次先核验 CSV/Parquet 和任务审批的 SHA256。`metrics.metric_scope = selected_validation_prediction_rows_micro_average`，若无匹配行，MAE/RMSE 返回 null。结果包含签名、所选过滤器、后端类型及稳定 `__tp_rowid`。

**性能边界**：目前公开预测资产上限仍是 240,000 行；DuckDB 有 Parquet 条件下推与服务端聚合，未建立物理二级索引，也未验证 100 万行端到端资产上传、发布与 Perspective 性能。CSV 降级后端每次扫描已签名文件，适合兼容性，不适合百万行生产查询。

## 2. 文件级改造

| 文件 | 改造范围 |
|---|---|
| `src/timepilot/model_tabular_lineage.py` | 新增有序源行集合签名与逐样本行身份校验 |
| `src/timepilot/model_prediction_query.py` | 新增类型安全的条件查询与筛选后 MAE/RMSE |
| `src/timepilot/model_source_lineage.py` | 新增已签名历史窗口的分页源行关联读取 |
| `src/timepilot/model_task_execution.py` | 在模型结果签名中冻结源行集；版本视图按行键排序 |
| `src/timepilot/model_task_unified_runs.py` | 源行回查、历史窗口与筛选查询统一入口 |
| `src/timepilot/model_task_api.py` | 新增两个只读项目级查询 API |
| `src/timepilot/static/modeling-ui.js` | 查询面板、选中样本、保留 Perspective DOM、历史源行面板、失效结果清理 |
| `tests/test_model_prediction_query_lineage_v0403.py` | 真实 SQLite/sklearn、真实时序、篡改、跨项目、签名排序及过滤反例 |
| `frontend/native-bi/tests/model_prediction_query_v0403.test.mjs` | UI 合同、数据范围与来源链接检查 |
| `scripts/release_gate_v0403.py` | 本轮新增测试纳入全模型发布门禁 |
| `scripts/release_integrity.py` | 新增文件列为必须签名内容 |
| `scripts/launcher.py` | 发布门禁统一入口升级至 0.40.3 |

## 3. 验收与风险审阅

- **P0：仍未关闭的部署物理门禁**：目标 Mac Python 3.12.12、DuckDB/PyArrow、Perspective 实际 WebSocket 和 Chrome 原生交互。
- **P0：大规模资产**：当前最多 240,000 条，整文件 SHA 每次重新检查、CSV 流式降级会增加延迟；尚未开展真实百万行压力测试。
- **P1：历史分类训练窗口**：旧分类器各折唯一输入索引不足，目前安全拒绝认证。
- **P1：交互一致性**：侧栏筛选与行选择已联动来源面板，Perspective 内部图表点击与侧栏双向同步仍需前端原生组件事件验证。
- **P1：性能与日志**：真正的多用户并发查询预算、Query Cancel、长连接权限撤销尚未进行硬门禁验收。

工程使用前执行：

```bash
bash TIMEPILOT.sh source-check
bash TIMEPILOT.sh release-gate --mode source
bash TIMEPILOT.sh release-gate --mode physical
bash TIMEPILOT.sh release-gate --mode browser
```

源码检查通过不意味着真实物理门禁通过。测试回执保留独立执行的退出码；未安装的 DuckDB/Perspective 不做假环境替代。
