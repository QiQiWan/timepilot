# TimePilot v0.35.2 逐项实施与验收审阅

审阅基线：v0.35.1 完整源码。实施范围：维护重置、工况连续段、量纲约束、跨设备/工况受控泛化，以及前后端评价证据呈现。保持源数据/数据库结构不变。

## 1. 已实现内容

| 模块 | 新增能力 | 实际入口/源码 |
|---|---|---|
| 机理约束 | 人工确认维护标记、设备键、时区/事件时间、来源证据、工程单位 | `feature_industrial.py`, `feature_semantics.py` 原契约 |
| 工业算子 | 维修/工况区段 `industrial.reset_mean` 和逐次增量 `industrial.reset_sum` | `feature_operator_registry.py`、`feature_compiler.py` |
| 候选策略 | 仅在已确认的事件依据与适用单位存在时，自动推荐维护段均值；累计和仍需人工逐次增量确认 | `feature_strategy.py`, `feature_candidates.py` |
| 工况切换 | 对同一设备的 A→B→A 按连续区段重新计算，不将两段 A 合并；首次维护前输出 NULL | `feature_industrial.py` 的两级窗口 SQL |
| 安全性 | 拒绝未知单位、瞬时压力/温度/功率累计和、缺失维修标记、未确认工况、重复时间/设备键、无来源依据 | `feature_industrial.py`, `feature_compiler.py`, `feature_dag.py` |
| 泛化评价 | 预先指定留出设备/工况；时序任务必须提供带时区的真实截止时刻 | `feature_industrial_transfer.py` |
| 数据隔离 | 所有外层训练/验证折仅使用未留出组；独立留出组只用于最后同协议对照 | `feature_evaluation.py`, `feature_industrial_transfer.py` |
| 用户界面 | 候选按“机理与维护重置”分组、详情显示规则证据；评价页可配置受控组留出并显示指标 | `feature-workbench-ui.js`, `feature-evaluation-ui.js`, `feature-shell.js` |
| 来源与兼容 | 保留 DataContract 修订、原始 Source SHA256 与 FeatureSpec 指纹；未启用留出时旧 EvaluationProtocol JSON 序列化保持兼容 | `feature_contracts.py`, `release_integrity.py` |

## 2. 计算规则

### 2.1 维护/工况区段

- 每台设备按已确认事件时间升序排列，数据层必须具备唯一设备-时间键。
- 每条记录的 BOOLEAN 维护标记为 TRUE 时开始新段；工况与前一条不同也开始新段。
- 同一工况再次出现时属于新段；连续历史不会因工况同名而跨期回填。
- 没有观察到维护事件的设备输出 NULL，禁止暗中把数据集第一行假设为维修起点。
- `reset_mean` 为**观测次数加权的均值**，并非真实时间积分；采样率变化时须结合时长权重另行分析。
- `reset_sum` 只允许物理意义上可累加的逐次增量信号，要求 `incremental_confirmed=true` 并绑定数据契约中已确认的依据。功率与压力等瞬时读数被阻断。
- 时间监督任务仍执行输入可用时间与决策时点的全量核验。较迟到达的过程记录会触发保守阻断，不会悄悄剔除。

### 2.2 量纲与证据

- 支持一组明确的工程单位与类型：压力、长度、速度、加速度、时间、温度、质量、力、功率、能量、电压、电流、频率、体积、数量等。未知单位**不自动猜测或换算**。
- 维护重置字段要求 `semantic_type=event`, `physical_type=BOOLEAN`, `confirmation=confirmed`，且 `evidence` 提供人工核验过的规则来源。
- 例如已确认“某设备更换滤芯标记”为维护事件，再以压力采样值形成维修后均值；不能仅凭列名中有 maintenance 一词就启用。

### 2.3 设备/工况泛化

使用既有 `/projects/{pid}/feature-lab/evaluations/plans` 接口。`EvaluationProtocol` 新增可选字段：

```json
{
  "industrial_transfer": {
    "kind": "unseen_device",
    "group_field_id": "fld_device",
    "holdout_values": ["设备D"],
    "time_cutoff": "2026-11-01T00:00:00+08:00",
    "min_train_rows": 40,
    "min_holdout_rows": 20
  }
}
```

以上对象是现有评价协议的新增字段，需和已物化、同源、受控的 `task`、`set_id`、`revision` 一起提交。静态分组验证可省略时间截止；时序任务必须提供明确时区。留出值由用户预先指定，不按标签或最佳收益自动挑选。

数据隔离发生在外层 K 折/时间滚动验证**之前**。留出组的样本和标签都不会用于外层特征筛选、超参数搜索与模型拟合。最终报告包含训练及留出行数、行集合 SHA256、单独的模型对照和受控限定说明。启用独立组留出时，禁用自动特征搜索以限制评价协议发生后验调整。

**证据边界**：该实现形成锁定分组的受控验证；如果自动候选在留出范围确定之前已经依据全量无监督画像生成，则还不能称为严格盲法最终测试。只有在候选生成前预注册设备与测试时间、冻结全部模型决策，并使用后来收集的数据完成一次正式验证，才适合形成外部生产性能报告。

## 3. 测试情况与必需的现场门禁

- `tests/test_featurelab352_industrial_mechanisms.py`：SQLite 对两级窗口 SQL 做数值核对，覆盖不同设备、A→B→A、维修前缺测、两类累计计算及拒绝路径。SQLite 无 DuckDB `TRY_CAST`，测试适配器只将该词法转换为 SQLite 的 `CAST`；其他窗口 SQL 不改动。
- `tests/test_featurelab352_transfer.py`：固定合成设备数据的组隔离、外层 CV 排除留出组、修改留出标签不影响训练选择、真实时间截断与标签可用时间检查。
- `frontend/native-bi/tests/featurelab352_industrial_ui.test.mjs`：评价方案、设备留出摘要及机理说明的 UI 集成测试。
- `tests/test_featurelab352_real_duckdb.py`：真实 DuckDB/Parquet 数值测试；如缺依赖，验收脚本标 `blocked`，不能算作通过。
- `scripts/featurelab352_acceptance.py`：使用当前运行环境执行源码校验、Python/Node 回归与物理计算门禁，生成带日志的 ZIP；不安装依赖，不下载 Chrome。

尚待目标机器验证：真实工业维修日志准确性、时钟同步和事件可用性、DuckDB/Parquet 数值、Perspective 浏览器、Worker 中断、多个设备新工况性能、百万行 RSS/耗时。任何专项测试因缺依赖跳过时，**不能对外宣称生产正式验收通过**。

## 4. 建议实际数据验证案例

优先使用有明确维护事件的气压/喷涂设备数据，明确设备编号、事件时间、过程数据可用时间、工况类别与维护日志。先验证维修前全部 NULL、维修后首条即重置、跨工况独立分段，再计算原始/新特征与基线的同协议对照。

其次采用贴片工艺的节点 A/B 不良标签，明确标签在节点之后何时可见，分别执行不平衡二分类与未见工况留出；现有代码不会推断未见设备能否产生真实收益，需要固定真实标签样本及业务误剔成本。

## 5. 交付与升级

提供 v0.35.2 完整源码 ZIP 与基于**未经本地修改的 v0.35.1 完整源码**的差异覆盖 ZIP。解压根目录即 `src/`, `scripts/`, `tests/`, `docs/` 等，直接复制覆盖，不包含安装器。无数据库迁移；覆盖前备份现有 `TIMEPILOT_HOME` 和源码。`source-check`、`db-check` 后运行现有启动脚本。

## 6. 本次源码级回归记录

- 实际复跑 v0.35.2 专项及相关历史回归：**61 passed / 1 skipped**；跳过项为真实 DuckDB/PyArrow 物理计算测试。
- 前端 Node 全量：**88 passed / 0 failed**。修复了三个历史测试因写死 v0.35.1 资源版本而在升级后错误失败的问题，相关断言现在根据 `frontend/native-bi/package.json` 的版本动态验证。
- 物理计算门禁：因当前环境未提供 `duckdb` 和 `pyarrow`，状态应为 **blocked**。通过 SQLite 数值用例只能验证公共窗口 SQL 及重置分段逻辑，不能替代 DuckDB/Parquet 验收。
- 工况泛化测试使用固定合成样本，覆盖设备隔离、留出集标签扰动和时间/标签可用性；真实产业数据的外部泛化改善 **未得到证明**。

运行 `python scripts/featurelab352_acceptance.py` 可获得带日志 ZIP；脚本不会自动安装环境或下载浏览器。
