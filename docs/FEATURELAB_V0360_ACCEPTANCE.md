# TimePilot v0.36.0 — 真实数据与产品级验收

版本基线：v0.35.6 完整源码。发布日期：2026-11-14。

## 1. 版本目标与范围

本轮将历史分散的质量评估整合成统一的、可复现的发布门禁。具体包括：

1. 真实 DuckDB + PyArrow 的 Parquet 计算、特征物化、分桶图表及独立 SQL 数值比对。
2. 独立外部工业 CSV（业务人员确认字段语义）→ 类型化数据导入 → PIT 特征计算 → 同折监督模型评价 → 真实物化产物的 SHA256 关联。
3. 本机已安装的 Chrome → FastAPI → DuckDB → Perspective Python/JS/WASM → WebSocket → 原生 Viewer 双资产展示、刷新复原。
4. Worker 操作系统进程异常退出、租约自然失效、重领和旧 Worker 提交阻断；多 Worker 争抢、来源失效检查。
5. SQLite 项目数据库向前迁移及较新版本数据库禁止回退、原有数据不覆盖。
6. 1 万 / 10 万 / 100 万行真实 Parquet 压力计算（确定性合成压力数据），记录耗时、资源占用与峰值保留。

**重要：** 当前代码包包含验收实现和执行入口；是否达到完整生产验收取决于目标 Mac/生产环境真实执行后生成的日志。`passed`、`blocked`、`failed` 必须分别处理。任何 `skipped`、缺少浏览器或跨来源 SHA256 都不能计为通过。

## 2. 如何运行

在现有 TimePilot 项目根目录与已安装的 Python 3.12 环境中使用，不创建新的 Conda/venv 环境、不下载浏览器、不修改业务原始文件。

### 2.1 源码级快速回归

```bash
bash TIMEPILOT.sh source-check
python scripts/featurelab360_acceptance.py --mode smoke
```

`--mode smoke` 包括源码校验、Python/Node 回归、SQLite 跨版本和 OS 子进程崩溃恢复。**即使该命令退出码为 0，也不代表生产验收已通过。** 它的 JSON 报告会明确将物理数据、性能及浏览器门禁标为 `blocked`。

### 2.2 完整实机验收

准备一份外部工业 CSV（至少 150 行，两台以上设备与两种以上工况），并根据实际业务语义填写字段映射，示例：`examples/featurelab360_field_map.example.json`。

```bash
python scripts/featurelab360_acceptance.py \
  --mode full \
  --real-csv /path/to/industrial.csv \
  --mapping-json /path/to/field_map.json \
  --with-browser
```

字段映射中的 `event_time`、`available_at`、`prediction_time`、`label_available` 分别代表事件发生时间、特征可获取时间、预测决策时间、真实标签可获得时间。它们应是不同的字段名；不允许将后续工序的信息提前用作预测特征。`provenance` 为业务人员声明的来源说明，仍需业务部门独立核实。

这一命令运行现有的 `scripts/featurelab30_e2e.py` 和 `scripts/featurelab31_browser_acceptance.py`，而非重新实现不受控的模型评价。整个流程使用隔离的 QA 数据目录；不会将原始 CSV 纳入验收 ZIP。**原有生产数据目录不能作为 `--out` 或 QA 目录使用。**

### 2.3 仅运行物理性能基准

```bash
python scripts/featurelab360_performance.py \
  --sizes 10000,100000,1000000 \
  --max-rss-mb 2048 \
  --max-seconds-per-size 180
```

数据在临时目录中创建、校验并自动清理；为合成压力数据，不具备业务预测标签，也不能证明模型性能。默认阈值为性能测试的初始配置，验收时应根据 Mac/生产服务器设备情况确认实际要求。必须对每个规模分别检查实际运行时间、峰值内存、数据哈希、分桶计数、均值与最大/最小值。

### 2.4 验收输出

`reports/featurelab360_acceptance/ACCEPTANCE.json`：各门禁的状态、耗时、原因与来源证据。

`reports/featurelab360_acceptance/ACCEPTANCE.md`：可向研发及业务部门交付的验收摘要。

`reports/featurelab360_acceptance.zip`：测试输出、统计 JSON 和日志，不含原始业务数据。

`reports/featurelab360_acceptance/isolated_qa_*`：为真实后端与浏览器连续验收保留的隔离 QA 应用目录；在人工审阅完结果后可手动清理。不要在生产数据目录运行。

## 3. 发布门禁及判定标准

| 门禁 | 通过条件 | 常见阻断原因 |
|---|---|---|
| `strict_source` | 当前源码 SHA256 清单严格匹配 | 混合源码版本、复制不完整 |
| `python_regression` | 有实际执行的 Python 用例且全部成功 | 测试失败、缺失证据 |
| `frontend_node` | Node 测试全部执行成功且零跳过 | 缺少 Node、组件失败 |
| `migration_compatibility` | 真实 SQLite 迁移代码通过兼容测试 | 版本高于支持范围、丢失记录 |
| `worker_crash_recovery` | 子进程崩溃后按真实租约到期恢复，旧 Worker 无法提交 | 租约失效后仍无法重领、重复提交 |
| `physical_duckdb` | Parquet/图表/机理计算真实执行，零跳过 | DuckDB、PyArrow 缺失或数值不一致 |
| `performance` | 10k/100k/1m 行分别通过独立 SQL 对照与资源预算 | 性能超时、RSS 过量、极值丢失 |
| `real_source_pipeline` | 真实 CSV 的源码 SHA256、PIT 产物、模型同折证据一致 | 无真实 CSV、字段契约不成立、来源失效 |
| `native_browser` | Mac 实机 Chrome/Perspective、WebSocket、刷新与两份资产同源 | 未构建 Perspective、未安装 Chrome、缺证据 |

`featurelab360_gate.py` 对缺少门禁、测试零执行、`skipped`、来源/物化 SHA256 不一致、不同 FeatureSet/产物、未观察到真实 WebSocket 等情况实行失败封闭。仅所有必需门禁实际 `passed`，才能设置 `production_ready=true`。

## 4. 当前代码审阅及剩余边界

- 已有 FeatureLab 候选生成、特征评价、不可变报告、权限与状态恢复继续复用；v0.36.0 无数据库 Schema 变更。
- 加入真正的子进程退出与自然时间租约过期测试，但正式生产还需要长时间 Worker/大模型 API 故障场景的持续运行验收。
- 完整源码包为 source-only：不包含编译后的 Perspective JS/WASM。**不能把源代码具备构建脚本等同于目标浏览器已经能够运行。**
- 物理压力测试使用临时合成数据，只能衡量计算一致性与性能，不能推断跨设备或真实工况的预测收益。
- 真实工业数据中的单位、异常含义、维护事件与时间字段必须经过业务人员确认。完整生产级验收还需要实际数据质量与模型评价指标的负责人签核。
- 现有历史入口、文档与旧验收脚本保留用于回归；正式 v0.36.0 验收统一通过 `featurelab360_acceptance.py`，防止多个脚本各自宣称“已通过”。
