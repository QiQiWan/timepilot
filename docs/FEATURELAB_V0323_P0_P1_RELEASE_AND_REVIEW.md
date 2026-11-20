# TimePilot v0.32.3｜特征工程 P0/P1 实施与独立审阅

> 2026-11-12。本报告区分源码实测、SQLite ANSI 窗口验证、待运行的 DuckDB 真包测试与企业现场验收。发布包是 source-only，不含伪造的 Perspective 编译产物。

## 1. P0：时间序列/设备计算正确性

- 新增 `feature_topology.py::infer_topology`，仅根据已确认 `order_index`（数值）或已确认 `event_time`（带有效时区）设置排序字段。检测未确认的设备/批次键，拒绝隐式跨设备滚动。
- `feature_temporal_profile.py::temporal_diagnostics` 按全量来源检测排序空值、设备分组键空值、设备内重复排序键、来源乱序行数，持久化证据哈希。
- `feature_candidates.py` 对时序候选显式写入 `order_field_id`、`partition_field_ids`；缺少安全拓扑时仅提出逐行变换和同单位字段差值，不借用导入行号。
- `feature_compiler.py` 独立验证排序键已确认且分组键与契约一致；实际计算前 SQL 检查空值/重复键，失败就报错。旧版手工创建的未指定顺序配方可被读取，其按导入行号计算的语义不作为已验收工业时间。
- 新增乱序导入/交错双设备/重复键/空键的 SQLite ANSI 窗口测试；DuckDB 真包测试需要目标环境安装 duckdb 后运行。

## 2. P1：自动化、交互与证据

- 目标识别区分比例回归、故障分类、分类字段探索；高置信规则与模型冲突时使用可解释规则，低置信提示人工确认。
- 20 万行不再直接停止；不超过 200 万行可进入受控后台全量画像和分批候选预览，更多数据要求明确分区方案；不会将预览样本冒充全量结果。
- 仅根据全量统计质量推荐初始字段，缺失过高、常量、低波动字段不会进入自动候选。合规监督评价完成后保留训练折特征入选频次与配对模型对照，反馈不自动改变已物化特征集。
- 对满足拓扑的来源给出可选 tsfresh 提示；Featuretools 需要关联表及逐表 `available_at` 确认后才具备运行资格；均不自动调用高成本算法。
- 大模型保持目标、字段/模板建议、合格候选重排和证据解释的权限边界，内部字段对外脱敏；可无模型运行，越权请求安全降级。
- 默认工作台显示数据拓扑、推荐候选证据和可追踪执行状态；用户勾选确认后一次保存草稿并启动正式物化，保留人工发布审批。只有核验同一 Parquet SHA256 后才显示监督模型贡献反馈。

## 3. 代码改造清单

| 文件 | 变化 |
|---|---|
| `src/timepilot/feature_topology.py` | 新增安全数据拓扑推断和排序门禁 |
| `src/timepilot/feature_temporal_profile.py`、`feature_profiler.py` | 全量时序键/分区统计与证据记录 |
| `src/timepilot/feature_candidates.py`、`feature_compiler.py` | 显式分组排序，执行前二次验证 |
| `src/timepilot/feature_auto_analysis.py`、`feature_auto_agent.py` | 规则/LLM 目标判断、候选质量、预算与监督反馈 |
| `src/timepilot/feature_execution.py` | 受限后台候选扫描预算 |
| `src/timepilot/feature_lab_v2_api.py` | 新增人工确认并正式计算接口 |
| `src/timepilot/static/feature-auto-ui.js`、`feature-shell.js` | 同源反馈、单次确认、拓扑与结果提示 |
| `tests/test_featurelab323_topology_and_p1.py` | 新的 API/安全/窗口/语义回归（真 DuckDB 可选） |
| `frontend/native-bi/tests/featurelab323_safe_auto_ui.test.mjs` | 新的 UI 安全与交互测试 |
| `scripts/launcher.py`、`scripts/release_integrity.py` | v0.32.3 源码验收命令和发布清单 |

## 4. 验收边界

**已验证的范围：** Python API/契约和数据拓扑回归、SQLite ANSI 窗口数值计算、Node 前端合约；完整数字以当次运行测试汇总为准。

**未取得的证据：** 企业真实产线时间/设备/标签数据、DuckDB+PyArrow 真正物化、tsfresh/Featuretools 真包、Perspective 浏览器渲染、Node 24 与 Mac 实机完整离线发行。源代码检查与模拟任务回执不得被认定为生产成功。

**发布建议：** 生产门禁保持 blocked。通过下列独立检查后方能审阅发布：

1. 目标 Mac Python 3.12.12 与 Node 24；源码、数据库增量迁移和 BI 真实构建。
2. 真实设备交错/乱序/重复键数据，独立比较 DuckDB 计算与参考结果。
3. 有独立真实标签与时间可用性的 PIT 物化、训练折内模型评价、跨设备/工况切片。
4. 同一 SHA256 Parquet 的 Perspective 显示、导出报告及资产复用；真实 Chrome 运行回执。
5. 用户独立完成任务的操作次数、耗时、人工修正和模型增益实测。

## 5. 源码验收命令

```bash
bash TIMEPILOT.sh db-check
bash TIMEPILOT.sh source-check
bash TIMEPILOT.sh accept feature-lab-v0323 --mode offline
bash TIMEPILOT.sh
```

本阶段不修改数据库 Schema 35，不需要删除或重建原始项目库。
