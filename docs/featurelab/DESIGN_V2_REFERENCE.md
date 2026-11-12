# TimePilot FeatureLab 2.0｜数据契约、交互工作流、智能规划与工具接口实施方案

**文档版本：**2.0 详细设计稿  
**日期：**2026-11-11  
**源码核查基线：**TimePilot v0.24.6  
**与上一版关系：**细化《TimePilot FeatureLab 2.0：工业时序特征工程系统研究与实施方案》，保留其六服务、四页面、DataContract/TaskSpec/FeatureSpec/FeatureSet/FeatureRun/EvaluationProtocol 及 v0.24.7—v0.25.2 路线。  
**文档状态：**以下新增对象、API、数据库表、页面及命令均为**待实施设计**，未经声明不得视为已进入当前源码。

## 0. 先确定的关键实施决策

**目标产品：**在 TimePilot 项目内完成 `数据资产 → 标准化确认 → 数据画像 → 特征设计 → 计算预览 → 实验评价 → 审核发布 → BI/模型复用`；每一步可由用户手动操作，也可由 Agent 在授权范围内通过同一批业务 API 实施。

**优先顺序：**先统一数据契约与基础工具能力；再完成画像和特征编辑可视化；然后补齐算子、可恢复计算、可靠实验；最后做长期特征资产、复杂 Agent 自主实验与可选研究库。

**六条硬约束：**

1. 不覆盖原始文件、不重命名真实源列、不改变已有版本及血缘；用户可见的中文业务名称是附加语义标签。
2. 原始字段、标准化字段、特征定义、物化特征与模型输入快照是不同对象，保持稳定 ID 和明确的来源。
3. 正式预测任务必须约束实体、决策时点、数据发生时间和业务可用时间；无可信时间信息时仅允许探索模式和按记录顺序分析。
4. 所有统计结果附带 `scope=full/sample/estimated`、样本量、数据版本、查询/执行指纹；前 1000 行预览不能自动升级为全量分析。
5. 大模型可以提出业务语义、算子和实验计划，不能直接执行自由 SQL/Python、越权选数据、绕过质量门禁或自行批准发布。
6. 所有手动功能与 Agent 工具复用同一服务层；使用 Perspective 原生图表与工作区配置，不另造重复图表系统。

**现有源码锚点（已核查）：**

- `src/timepilot/feature_lab.py`：`FeatureStep` 支持 `arithmetic/rolling_mean/rolling_std/lag/difference`；`FeatureRecipeInput` 保存源资产、版本、名称及步骤；通过白名单 DuckDB 模板编译，发布新的 Parquet 资产。
- `src/timepilot/feature_lab_api.py`：已有 `/recipes`、`/recipes/{id}/preview`、`request-review`、`approve`、`publish` 等接口；保持兼容。
- `src/timepilot/static/feature-lab-ui.js`：当前为一个长表单，仍调用原生 `prompt/confirm`，需要改为四页工作台及应用内表单。
- `src/timepilot/project_migrations.py`：`TARGET_SCHEMA = 18`；新增表从 v19 开始增量迁移，并保留旧 `feature_recipes` 及审计记录。
- `feature_lab.py::compile_step()` 目前统一将数值转 DOUBLE，窗口可按 `__tp_rowid` 排序；正式工业时序场景需要类型精度、实体和时间契约补强。

## 1. 数据标准：从外部文件到可发布特征的五层契约

### 1.1 数据对象层级与不可变范围

| 层 | 对象 | 存储建议 | 可修改范围 | 主键/版本 | 消费方 |
|---|---|---|---|---|---|
| L0 | RawAsset | 原始 CSV/XLSX/Parquet/JSONL 等 + SHA256 | 文件字节不可修改 | `asset_id + source_sha256` | 数据导入、审计、重解析 |
| L1 | DataContract / NormalizedAsset | JSON 契约 + Parquet 转换结果（可选） | 新增语义映射/生成新版本，不改 L0 | `contract_id + revision`、`normalized_asset_id + version` | BI、FeatureLab、模型 |
| L2 | FeatureSpec / FeatureSet | JSON Schema + SQLite + DAG 指纹 | 草稿可改；发布版本冻结 | `feature_id + version`、`set_id + revision` | 预览、计算、实验 |
| L3 | FeatureRun / FeatureArtifact | SQLite 执行状态 + Parquet + 统计报告 | 运行中追加进度；成功结果不可变 | `run_id`、`artifact_id + sha256` | BI 可视化、实验、审核 |
| L4 | PublishedFeatureAsset / ModelInputSnapshot | 不可变 Parquet + manifest.json | 仅派生新版本 | `published_id + version + sha256` | 模型管理、部署、回测 |

保留逻辑数据与物理文件的映射：统一 `logical_asset_id`，当前原生资产的 `native_asset_id` 在兼容层转换。文件去重和清理由独立存储引用计数控制，资产回收站仅修改可见性。

### 1.2 支持的外部数据格式与导入规则

| 类型 | 首期支持格式 | 必须具备的契约 | 兼容策略 |
|---|---|---|---|
| 设备测点宽表 | CSV、XLSX、Parquet | 一行一观测；可选设备 ID、事件时间、多个测点列 | 时间/设备缺失仍可进入探索模式 |
| 测点长表 | CSV、Parquet | `entity_id,event_time,metric_name,value`；同一时刻重复规则 | 可在 Normalized 层透视为宽表，但留原件 |
| 工艺/批次表 | CSV、XLSX、Parquet | 批次/产品/设备键、工艺版本和状态、可用时点 | 字符串 ID 不自动转数字 |
| 维护事件表 | CSV、XLSX、Parquet | `entity_id,event_id,event_type,start_time,available_at` | `end_time` 可空；缺失可用时点需核实 |
| 标签/决策表 | CSV、Parquet | `sample_id,entity_id,prediction_time,target,label_time` | 只有监督任务要求；在画像环节可缺省 |
| 时间序列事件流 | JSONL、Parquet（后期可加流接口） | schema/version、消息唯一 ID、事件时间、入库时间 | 实时接入需单独具备一致性/重放能力 |

**编码与值规则：**CSV 默认导出 UTF-8，导入支持已识别的 GB18030 等编码并记录；保留原字段名/列顺序/空表头来源位置；Excel 公式原文和缓存数值分别记录；产品 SN、批次号、邮编及 `00123` 一律优先按字符串保真；区分空字符串、缺失、`NaN`、转换失败、无穷值；有损转换以异常表列出原值和位置，不能静默替换。

**文件建议：**新项目默认提供五种 CSV 模板（设备宽表、测点长表、事件表、标签表、字段字典），每种配对应 `contract.json`。已经导入的老文件允许通过 UI 补充契约，不强制重新上传。

### 1.3 可直接发给业务方的最简数据模板

**设备过程数据（示例；所有值均为说明性数据）**

```csv
sample_id,device_id,event_time,available_at,batch_id,negative_pressure_kpa,air_pressure_kpa,run_state
S00001,EQ-A,2026-11-09T10:00:00+08:00,2026-11-09T10:00:03+08:00,B0008,-15.2,420.0,RUN
S00002,EQ-A,2026-11-09T10:01:00+08:00,2026-11-09T10:01:03+08:00,B0008,-15.0,421.0,RUN
S00003,EQ-B,2026-11-09T10:00:00+08:00,2026-11-09T10:00:05+08:00,B0011,-14.8,419.5,IDLE
```

**维护事件数据**

```csv
event_id,device_id,event_type,start_time,end_time,available_at,source_record_id
M00001,EQ-A,filter_replacement,2026-11-09T09:30:00+08:00,2026-11-09T09:45:00+08:00,2026-11-09T09:46:00+08:00,MAINT-884
```

**质量预测样本/决策表**

```csv
sample_id,device_id,batch_id,prediction_time,target,label_time,stage
S00001,EQ-A,B0008,2026-11-09T10:05:00+08:00,0,2026-11-09T16:00:00+08:00,node_A
```

注意：`label_time` 为标签发生或产生时间，另应支持 `label_available_at` 用来表示标签最终可用于训练/回放的时间。`prediction_time` 是业务决策时刻，来源必须由业务流程核实，不能用数据行的最大时间替代。

### 1.4 标准化后的列契约（FieldContract）

| 字段 | 类型/要求 | 示例 | 校验方式 |
|---|---|---|---|
| `field_id` | 稳定技术 ID，跨列重命名不变 | `fld_f281...` | 同一资产版本内唯一 |
| `source_name` | 原样保存列名 | `A2` | 与源表列位置和 SHA 对照 |
| `display_name` | 人工/模型候选中文名 | `负压` | 候选状态与确认人分离 |
| `physical_type` | DuckDB/Arrow 类型 | `DECIMAL(10,2)` | 与源样本、完整转换统计一致 |
| `semantic_type` | `numeric/string/datetime/category/id/boolean/event/target` | `numeric` | 语义候选可人工修正 |
| `role` | `entity_key/event_time/available_at/value/regime/label/...` | `value` | 与 TaskSpec 组合验证 |
| `unit` | 推荐 UCUM/组织单位字典里的标准写法 | `kPa` | 必须有换算与原单位 |
| `nullable` | boolean | `true` | 统计缺失与必要业务约束 |
| `precision/scale` | 数值精度 | `10/2` | 不强制统一 DOUBLE |
| `source_column_index` | 原始物理列序 | `7` | 列顺序保真 |
| `source_location` | sheet/region/原始单元格坐标 | `Sheet1!H1` | 可定位、可审计 |
| `classification` | `public/internal/restricted` 等 | `internal` | 模型上下文/导出保护 |
| `confidence/confirmation` | 候选、规则、人工确认来源 | `confirmed` | 正式建模仅用已满足条件的语义 |

**关键实现：**内部 API 始终使用 `field_id` 定位源列；UI 展示原名与业务名，如 `A2（负压）`。业务名、来源字段与特征技术名不互相覆盖，重复列名通过源列序号和稳定 ID 消歧。可以保留技术上必要的原始列编号与 Region 坐标。

### 1.5 时间、实体与重复记录标准

- **内部时间语义：**采用 ISO 8601 且带时区偏移；查询与持久化以 UTC 时间戳为统一计算时间轴，UI 使用用户确认的原时区展示。未知时区必须询问，禁止默认猜测。
- **四种时间：**`event_time`（事实发生）、`available_at`（业务系统确实可用）、`ingested_at`（TimePilot 接收）、`prediction_time`（决策时刻）。不能以 `ingested_at` 自动填 `available_at` 作为已证事实。
- **实体键：**支持复合键，如 `device_id + product_type` 或 `batch_id + product_serial`；窗口算法只能在经确认的实体/工况分区内执行。
- **唯一观测键：**建议 `source_record_id` 或 `(entity_keys,event_time,source_sequence)`；相同实体/时间的多行必须指定保留、聚合、排序或拒绝规则，绝不随机选一条。
- **不规则采样：**明确 `row_count` 窗口和 `duration` 窗口的区别；缺测和维护重置需要单独标记，插值只能在允许的时间/工况边界执行。
- **源数据没有时间信息：**允许普通行运算、按 `__tp_rowid` 的“过去 N 条记录”探索；禁止显示为“过去 N 分钟”，也不得进入正式时间泄漏安全宣称。
- **可用时点不足：**预测任务状态为 `needs_review`，只提供探索性结果或经过人工审批的研究假设；不能将潜在泄漏的特征发布为正式训练输入。

### 1.6 数据质量约束和发布门禁

| 等级 | 检查 | 发生问题时 |
|---|---|---|
| `fatal` | 文件 SHA 不匹配；源版本/项目无权限；字段找不到；输出 schema 冲突；DAG 成环；PIT 明确越界 | 拒绝执行/发布 |
| `block_review` | 预测任务缺少可用时间证据；事件关联歧义；必要单位未确认；重复观测键规则未知 | 仅探索，要求人工确认 |
| `warn` | 缺失较高、常量列、采样不齐、窗口有效样本不足、潜在分布漂移 | 可预览，警示并记报告 |
| `info` | 可选业务别名未补充，推荐算子没有标签信息 | 不阻断基础使用 |

质量报告统一格式 `check_id,severity,field_ids,observed,expected,scope,evidence_ref,review_status`。其中数值统计包含 `computed_at,statistic_scope,rows_scanned,rows_total,method_version,source_sha256`；采样指标不可默认为全量指标。

### 1.7 业务任务契约 TaskSpec

`mode` 有两种：`exploration` 与 `supervised`。探索模式只要有合法资产便能运行画像、分布、算术、按行窗口等。监督模式必须指定决策对象、预测时刻、目标、标签时间语义及时间/实体切分协议。

```json
{
  "task_spec_version": 1,
  "task_id": "task_quality_node_a",
  "mode": "supervised",
  "task_type": "binary_classification",
  "entity_keys": ["product_sn"],
  "decision": {"stage": "node_A", "prediction_time_field_id": "fld_decision_at"},
  "label": {"field_id": "fld_final_defect", "event_time_field_id": "fld_label_at", "available_at_field_id": "fld_label_available_at"},
  "data_policy": {"require_event_time": true, "require_available_at": true, "allow_future_features": false},
  "split": {"kind": "purged_grouped_time", "group_fields": ["batch_id"], "gap_minutes": 30},
  "metric_priority": ["recall", "false_reject_rate", "pr_auc"]
}
```

`purged_grouped_time` 是拟新增评估协议名，具体组隔离、时间分段和间隔必须由校验器完整实现，不能只靠字符串声明“防泄漏”。

### 1.8 FeatureSpec v2 / FeatureSet 契约

```json
{
  "schema_version": 2,
  "feature_set_id": "fs_eq_pressure_v1",
  "revision": 1,
  "source": {
    "asset_id": "asset-123",
    "version_id": "base",
    "source_sha256": "sha256-of-existing-source"
  },
  "semantic_binding_id": "binding-002",
  "task_id": null,
  "entity_keys": ["fld_device_id"],
  "time_context": {
    "event_time_field_id": "fld_sample_time",
    "available_at_field_id": "fld_available_at",
    "timezone": "Asia/Shanghai"
  },
  "features": [
    {
      "feature_id": "feat_pressure_ma_30min",
      "output_name": "pressure_ma_30min",
      "display_name": "负压过去30分钟平均值",
      "operator_id": "rolling.mean",
      "operator_version": "2.0",
      "input_field_ids": ["fld_negative_pressure"],
      "parameters": {
        "window_kind": "duration",
        "window_value": 30,
        "window_unit": "minute",
        "min_periods": 10,
        "include_current": true,
        "reset_on_event_types": ["filter_replacement"]
      },
      "output": {"physical_type": "DOUBLE", "unit": "kPa", "null_policy": "preserve_with_coverage"}
    }
  ],
  "preview_policy": {"max_rows": 100, "method": "time_stratified"},
  "state": "draft"
}
```

**字段引用一律以已授权 `field_id` 为准。** 配方中的 `source_sha256` 和 `operator_version` 固定；配方草稿修改会增加 revision。`preview_policy` 仅影响展示，不得影响正式物化计算。

### 1.9 FeatureRun、Artifact 与发布 Manifest 的最小结果

- FeatureRun：`run_id,project_id,set_id,set_revision,source_sha,operator_manifest_sha,engine_version,selection_sha,status,started_at,finished_at,progress,cost,errors`。
- Artifact：`artifact_id,run_id,storage_uri,sha256,row_count,column_schema,statistics_scope,query_fingerprint,retention,permissions`。存储 URI 由服务端签发，不由模型构造路径。
- Evaluation：`evaluation_id,task_id,feature_set_id,baseline_id,split_manifest_sha,metrics_by_fold,aggregates,latency,cost,leakage_report`。
- Published Manifest：`feature_set_id,version,approved_by,approved_at,source_refs,feature_graph_sha,artifact_sha,quality_report_id,usage_policy,model_references`。
- 缓存 Key：SHA256(`source_sha + FeatureSpec_graph_sha + operator_versions + engine_version + selection_sha + cutoff_policy`)；允许版本更新导致新结果，禁止覆盖同 Key 历史成功产物。

**推荐新文件目录（逻辑设计，须兼容现有 TIMEPILOT_HOME）：**

```text
TIMEPILOT_HOME/
  projects/catalog.sqlite3
  feature_lab/
    <project_id>/
      runs/<run_id>/run.json
      runs/<run_id>/staging/...
      artifacts/<artifact_id>/data.parquet
      artifacts/<artifact_id>/manifest.json
      profiles/<profile_id>/profile.json
      evaluations/<evaluation_id>/report.json
```

文件与目录的最终访问必须经过项目授权和存储服务，绝不把绝对路径放进 Agent 的工具参数。

## 2. 产品操作流：四个页面、一个任务上下文、一份工作区状态

### 2.1 路由和页面划分

沿用项目主导航 `概览 / 数据管理 / 特征工程 / 模型管理 / 任务记录`。特征工程内部设四个主 Tab：**数据画像、构造特征、实验评价、特征资产**；顶部固定同一 `ProjectAssetContext`，包含 `project_id,asset_id,version_id,contract_revision,task_id,selection_fingerprint,workspace_revision`。

建议使用以下路由，刷新、返回、分享时能恢复核心上下文：

```text
/projects/{pid}/features/profile?asset={aid}&version={vid}
/projects/{pid}/features/build?set={sid}&revision={rev}
/projects/{pid}/features/evaluate?task={tid}&set={sid}
/projects/{pid}/features/catalog?state=published
```

**工作区边界：**Perspective 是唯一的数据表/统计图/交叉筛选主画布；FeatureLab 负责外围的字段列表、特征配方 DAG、评估报告、审核和智能推荐。FeatureLab 只调用统一 Viewer Bridge 的 `openAsset / addPanel / applyConfigPatch / saveWorkspace`，不得再复制一套图表配置栏或替换已经加载的主 Viewer。

### 2.2 总体 UI 线框

```text
┌ TimePilot / 项目 A / 特征工程 ─────────────────── 使用手册 任务中心 智能助手 ┐
│ 数据画像  |  构造特征  |  实验评价  |  特征资产                         │
├────────────────────────────────────────────────────────────────────────────┤
│ 数据资产 [设备过程数据 v3 ▼] 设备范围 [全部 ▼]  模式 [探索 ▼]   [保存设置]    │
│ 数据状态：已核实  时间：待确认  单位：3项待核实  原始记录：506条             │
├─────────────┬─────────────────────────────────────┬────────────────────────┤
│ 字段 / 候选 │     Perspective 画布 / 原始—特征对照  │ 本次操作/参数抽屉       │
│ 搜索字段    │  Grid / Line / Histogram / Heatmap   │ 选择算子 / 窗口 / 分组   │
│ 分组筛选    │  图表选区驱动当前分析上下文           │ 说明 + 立即验证         │
│ A1、A2...   │  保存原有面板布局和用户选中系列       │ 证据 / 预计成本         │
├─────────────┴─────────────────────────────────────┴────────────────────────┤
│ 已选特征 7  | 质量告警 2  | 预计计算量       [预览] [评价] [提交审核]      │
│                                                         (圆形助手入口)      │
└────────────────────────────────────────────────────────────────────────────┘
```

所有关键控件根据当前状态启用；未选资产时引导回数据管理，契约未确认时显示待处理项但不阻断合法只读操作。技术说明默认收起放到“为什么这样推荐/高级参数”。

### 2.3 页面一：数据画像（Profile）

**页面目标：**回答“这是什么数据、质量怎样、时间/设备结构是什么、有什么可能有用的规律”，给特征构造提供证据。

| 区域 | 显示内容 | 可操作事项 | API/工具 | 输出对象 |
|---|---|---|---|---|
| 顶部摘要 | 记录数、字段数、实体数、跨度、采样质量、未确认项 | 刷新画像、选择范围 | `profile.get/create` | DatasetProfile |
| 左侧字段树 | 原列名、业务名、类型、单位、角色、质量标记 | 搜索、选列、批量标注 | `schema.inspect`, `contract.patch` | FieldContract revision |
| 主画布 | 原始曲线、缺失、箱线、分布、分组、工况和事件影响 | 缩放/框选/联动筛选、保存图 | `viewer.*`, `profile.query` | Workspace token |
| 右侧详情 | 字段来源、类型证据、范围、缺失位置、建议用途 | 确认时间/单位/实体/工况 | `contract.validate/confirm` | SemanticBinding |
| 底部建议 | “查看异常”“比较设备”“查看维护变化”“试做滞后” | 点击进入带参数的候选卡 | `candidate.suggest` | CandidateSet |

**页面状态**：`not_profiled → profiling → ready / needs_review / failed_retryable`。统计服务对全量行数、精确 NULL 数采用确定性聚合；费时的分位数/频域估计可采样，但 UI 须清晰显示“抽样”和具体范围。

**新手一次完整操作**：进入资产 → 看到 506 行、15 列 → 点击 A2 → 显示分布/变化和真实字段统计 → 将 `Time` 标为“序号”或选择真正时间字段 → 查看推荐候选 → 一键跳转构造特征，当前 A2 选择保留。

### 2.4 页面二：构造特征（Build）

**页面目标：**用户选字段及算子，看到即时可检查的前后差异，并形成稳定的 FeatureSet。

布局：左边 `字段/算子库`，中央 `原始与特征对照 Perspective`，右侧 `参数编辑器`，下方 `特征步骤条/DAG/错误与成本`。采用“选字段 → 选算子 → 参数设置 → 预览 → 加入特征集”五步；常用操作可减少为三次点击（如选列→滚动平均→预览）。

**推荐操作卡依据数据实际条件生成，避免把不可用工具直接置为主按钮**：

- 数值字段：两列相加、变化量、标准化、滚动均值、相关性、分布。
- 已确认时间序列：滞后、时长窗口、周期/趋势、采样缺口、变点。
- 多设备：按设备比较、设备内标准化、同工况偏差、跨设备稳定性。
- 有事件数据：维护前后对比、距最近维护、维护后重置。
- 已定义监督任务：目标关联、候选特征筛选、基线对照、时间泄漏检查。

**示例：A2/A4 过去 30 分钟滚动均值**

1. 图表或字段列表中多选 `A2` 与 `A4`，点击“移动平均”。
2. 系统发现需要 `duration=30min`，检查真实时间字段、设备键、时区、是否有分钟级采样；缺少关键字段只弹出一个字段选择内嵌面板，用户可直接在 Perspective 表头/字段侧栏点选标记。
3. 右侧参数采用自然语言控件：`窗口 [30] [分钟] | 每个 [设备] 分别计算 | 最少有效数据 [10] | 维护后 [重置]`。
4. 点击“预览”，中间双图叠加原始与计算值，虚线标记缺失和重置边界，显示真实样本数与全量范围；错误时精确定位相关字段。
5. 点击“加入特征集”，形成两项 FeatureDefinition，显示输出列名及单位。正式生成资产必须走审核链。

若只有 `Time=1..506` 的顺序数字，必须提出“目前识别为记录序号，按过去 5 条记录计算，还是补充真实采样时间？”；不得自动解释为分钟。

**编排交互：**上方快速模式默认列表步骤，点击“查看依赖关系”才进入 DAG 图；拖动调整没有依赖冲突的步骤。修改参数时只使受影响节点的缓存失效；支持撤销/重做和“与上次预览比较”。

### 2.5 页面三：实验评价（Evaluate）

**两条路径**：

- 探索/无标签：质量、冗余、分布、稳定性、跨设备/工况差异、计算成本、PIT 可验证程度。不显示未经训练的“模型增益”。
- 预测/分类任务：以 `TaskSpec` 限定标签、决策时点和数据切分；比较 Raw-only、Raw+Basic、Raw+Auto、Raw+Mechanism 等共用的折与模型基线，展示按折指标、置信区间和推理成本。

**操作向导**：①确认任务和标签 → ②选择分组/时间切分 → ③选择候选特征集与基线 → ④检查泄漏与资源预算 → ⑤运行/查看结果。每步均可保存为 `EvaluationProtocol`，重开时恢复。

主区域：顶部关键指标卡 + 差异图 + 按折箱线/趋势 + 重要性/冗余视图；右侧“这个结果说明什么”仅根据后端证据生成说明，不允许大模型虚构 Accuracy 或增益。

**比较样例**：当前特征集召回率从 A 到 B 的改变量必须同时展示验证折、测试集样本量、误剔除率和时间/设备留出表现。不同数据折/不同窗口的结果不可直接混在同一图表做“增益”结论。

### 2.6 页面四：特征资产（Catalog）

列表字段：`特征集名称/版本/任务/来源资产/字段数/数据时间范围/质量状态/审核状态/引用模型/最新计算时间/操作者`。

单项详情：血缘 DAG、特征定义、物化数据统计、评价记录、所属模型、复算历史、更新策略和回收依赖。支持搜索、复制特征集版本、版本差异、查看发布前检查、提交审核、打开 BI、模型引用、弃用。

**审核弹窗**：显示原文件 SHA、FeatureSpec 指纹、来源字段、PIT 检查、质量报告、预计数据量、引用状态、审核意见和“发布后不可原地改动”。审核与发布是显式人的操作；不能让 Agent 自动点击替代。

### 2.7 分层辅助 UI 与可视化规则

| 交互 | 默认样式 | 避免的问题 |
|---|---|---|
| 字段选择 | 可搜索多选列表 + 在 Perspective 列头点选同步 | 浏览器 `prompt()`、用户重复键入 A1/A2 |
| 算子参数 | 每个算子的右侧抽屉，条件字段按需出现 | 一次性堆出几十个参数 |
| 风险确认 | 内嵌字段卡或应用内 Modal | 原生 `confirm`、丢失上下文 |
| 智能建议 | 3—5 个针对当前任务的操作卡 | 固定摆满工具按钮 |
| 作业进度 | 全局任务中心显示状态、完成阶段、可恢复点 | 单页面等待、刷新后任务失踪 |
| 结果图 | 原生 Perspective Viewer / 受控扩展 Panel | Agent 与手动图表互相覆盖 |
| 帮助 | 右上“使用手册”，当前页面锚点、操作截图占位 | 高级术语说明堆在正文 |

**Vue/React 非强制：**沿用当前 B/S 静态模块和 Perspective Bridge，建议将 FeatureLab UI 拆成 `feature-shell.js`, `feature-profile-ui.js`, `feature-builder-ui.js`, `feature-evaluation-ui.js`, `feature-catalog-ui.js`, `feature-dialogs.js`。不为新页面强制新建第二套 Node 工程；已有自动构建机制继续负责前端静态产物。

### 2.8 任务对象状态与跨页面一致性

```text
Contract:  detected → needs_review → confirmed → stale
Profile:   queued → running → ready | failed_retryable | cancelled
FeatureSet: draft → validated → previewed → evaluated → pending_review
                                           ↘ needs_fix        ↓
                                      approved → materializing → published
Run:       queued → validating → running → materializing → succeeded
                             ↘ failed_retryable → queued
Job stale: source SHA 或配方 revision 更新 → stale（禁止旧结果覆盖）
```

状态必须有服务端真值，前端刷新按 `run_id` 查询；所有写状态需要 `expected_revision`，所有创建异步任务需要 `request_id` 幂等。发布只允许 `approved` 且当前质量、来源哈希、PIT 门禁再次通过的版本。

**跨工作区统一：**BI 选择的字段、系列、筛选范围可以传给 FeatureLab 作为 `selection_context`，但不会覆盖 FeatureSpec 的完整来源或正式计算范围。若用户选中的是部分行，必须在预览明确显示“当前选区”；保存配方时可选择“选区条件入契约”或“仅用于预览”，禁止默认混同。

## 3. 大模型如何介入：五个业务环节、一个统一 Planner、可验证的执行循环

### 3.1 模型适合承担什么

| 流程阶段 | 模型可以做的工作 | 模型使用的证据/工具 | 硬性限制 | 模型失败时 |
|---|---|---|---|---|
| 数据契约确认 | 判断字段可能含义，推荐实体键、单位候选、时间角色；解释存在歧义的结构 | `schema.inspect`, `profile.read`, `contract.candidates` | `Time` 和 `A1` 不得自行命名为小时/温度；不自动盖“已确认” | 显示规则解析和待人工确认项 |
| 数据画像 | 帮用户选择应查看的分布、关系、周期、工况；组织事实摘要 | `profile.*`, `viewer.*` | 不编造缺失率、设备数和时间跨度 | 全部统计与图表仍可正常运行 |
| 特征发现 | 根据任务、约束和画像推荐少量高相关候选；形成候选 DAG | `operator.search`, `candidate.propose`, `feature.validate` | 只从可用算子挑选，不得输出自由 SQL | 使用 deterministic candidate templates |
| 特征实验 | 选择适合的分组/时间切分、对照组和评估指标 | `task.inspect`, `evaluation.plan`, `evaluation.run` | 无标签不执行监督评估；不得在测试集拟合预处理 | 使用固定预设协议或请求确认 |
| 结果解释 | 对真实评价结果做差异归纳、指出风险、推荐保留/删除候选 | `evaluation.compare`, `quality.get`, `lineage.get` | 不将相关性解释成因果；不假称性能提升 | 展示机器生成的结构化事实表 |

**采用“一套 Agent 执行框架 + 多个逻辑工作阶段”**。上一版建议的 `FeatureDiscoveryAgent / FeatureValidationAgent / FeatureExperimentAgent / FeatureReportAgent` 可以作为四组 Prompt 和工具权限配置，不需要部署四个常驻模型进程；真正的安全判断由服务端 ValidationService 完成。

### 3.2 智能功能的三个运行档位

1. `local`（无模型）：基础数据契约候选、确定性意图、字段选择、内置算子、预览、评价和发布完整可用；推荐理由来自规则与计算证据。
2. `assist`（默认在模型可用时启用）：用户操作触发结构摘要、候选解释、工具检索、一次性计划建议；危险操作必须手动确认。
3. `agent`（主动任务模式）：用户提出复杂任务，模型可在单一工作流内申请多步只读分析、生成特征草稿和运行成本受限的实验；保留人工中断点，禁止自行批准发布。

三个档位使用同一服务层和工具注册表；模型配置走现有统一 API 设置页面，允许选择 provider/model/推理预算/最大成本、超时与数据传输策略，密钥只存在服务端受保护配置。

### 3.3 规划上下文 Context Builder

每个 Agent Turn 只发最必要信息：`intent_text`、当前项目授权上下文的不可识别 ID、实际字段 `field_id/source_name/semantic_role/type/unit/quality`、TaskSpec 的必要部分、用户选中的当前图/字段、能够调用的工具 schema 摘要、选中算子文档片段和任务预算。

**默认不给模型的内容：**全量数据、未授权字段、用户数据库连接信息、原始身份类敏感字段、SQL 凭据、操作系统路径、私钥、跨项目元数据。需要示例数值时通过 `profile.read` 取得范围受控、脱敏、有限行数的统计/样本，数据传输策略由用户/管理员控制。

超宽表不把前 80 列当作完整上下文：先全量读取 schema 的字段元数据索引，再根据用户请求进行字段检索 `field.search`，返回候选 ID、匹配来源、类型及相似分数；无法唯一确认时给最小澄清，不允许误将未知字段替换成相似字段。

### 3.4 建议使用的受限结构化 Planner 结果

```json
{
  "plan_schema_version": 1,
  "intent": "create_rolling_features",
  "feasibility": "needs_clarification",
  "reason_codes": ["time_axis_unconfirmed"],
  "source": {"asset_id": "asset-123", "version_id": "base", "source_sha256": "sha256-of-existing-source"},
  "tool_actions": [
    {"tool": "feature.schema.inspect", "args": {"field_ids": ["fld_A2", "fld_A4"]}}
  ],
  "missing_slots": [
    {"slot": "event_time", "question": "请选择表示实际采样时间的字段；也可以改为按最近5条记录计算。"}
  ],
  "expected_effect": "read_only",
  "workspace_revision": 12,
  "confidence": 0.92
}
```

`confidence` 是模型估计值，仅用于排序/复核，不承担安全授权；`feasibility` 必须经过后端同意才能变成 `ready`。模型响应 JSON 使用 Provider 支持的结构化输出/Function Calling，仍需二次执行 Pydantic 校验、后端字段白名单、算子版本、权限、源 SHA 和资源预算校验。

**推理模型的设计要求：**允许模型使用更高推理预算完成复杂任务规划，但面向用户展示“理解的任务—数据证据—执行步骤—实际结果—剩余问题”，不存储、展示或要求模型公开内部逐 token 思维链。建议将模型规划与工具执行的结构化过程记入审计日志，便于复现。

### 3.5 受控执行循环：Plan → Validate → Execute → Observe → Replan

```text
用户请求
  → 字段与任务检索（真实 Schema/TaskSpec）
  → 本地确定性路由：简单查询和常用算子直接生成合法 ToolCall
  → 复杂任务：Model Planner 提出不超过 8 个原子动作
  → 独立 Feasibility/Permission/Temporal/Cost Validation
  → 执行工具 1 → 读取真实 ToolResult / 统计证据
  → 执行工具 2 → 获取数据库/Viewer 回执
  → [出现新事实或失败时] 有上限的再规划（默认最多 2 次）
  → 生成特征草稿 / 对比报告 / 明确失败与恢复建议
  → 必需人工确认的节点停在待确认态
```

运行时按步骤记录 `plan_id,step_id,tool_id,validated_args,started_at,status,tool_result_ref,evidence_sha,elapsed_ms,replan_count`；异步执行后获取 `run_id`，通过状态轮询或 SSE 查看进度，模型不能虚构“已完成”。

### 3.6 两个示例任务的自动化路径

**例 A：**“看看这批设备数据有什么规律，建议几个有用的特征。”

1. `feature.schema.inspect` → 实际列/类型/质量与身份字段。
2. `feature.profile.get_or_create` → 运行分布/异常/时间结构画像，等待真实 job 完成。
3. `feature.operator.search` → 按数据形态列出可用基础与时序算子。
4. `feature.candidate.propose` → 生成不超过约 10—30 个有限候选，附每项来源字段、理由、成本和限制。
5. `feature.spec.validate` → 检查多实体窗口、时间可用性、单位、输出命名。
6. 显示可编辑候选卡；用户选择后再 `feature.set.draft_create` 并预览。

**例 B：**“比较维修前后变化最大的参数，并做可用于故障预测的特征。”

1. 调用 `feature.event.catalog` 检查维护事件是否存在、设备关联是否唯一。
2. 调用 `feature.profile.event_effect` 给出维护前后统计、样本数和工况控制。
3. 让模型基于可引用的证据推荐“距上次维修、重置后趋势、稳态偏差”等候选。
4. `feature.spec.validate` 检查维护重置、数据可用时刻、时间窗和单位。
5. 无标签时只生成可解释的候选特征及稳定性报告；有标签且定义了预测时点才 `feature.evaluation.plan` 启动训练折内的模型对照。
6. 汇总实际统计与实验报告，用户审批之后生成正式特征集。

### 3.7 失败恢复、越权与模型注入防护

- 模型返回空 `tool_actions`：若本地路由可执行则用本地工具；否则给出具体缺失槽位，不显示裸 `[]`。
- 模型编造字段/算子：后端拒绝，返回当前真实字段候选及可用能力；不“猜一个最像的字段”继续计算。
- 模型 API 缺配置/429/超时：记录原因和成本，降级至确定性流程，已完成的真实分析结果保留。
- 数据单元格包含“忽略权限/请求外部上传”等指令：作为不可信数据，不得成为系统消息或 Agent 工具授权。
- 用户切换项目/资产/版本：旧计划 source SHA 或 workspace revision 不匹配即 `stale`；禁止作用于新项目。
- 后台计算中断：返回持久 `run_id`，允许同请求 ID 查询、取消和恢复；断点只认经过 hash 核验的检查点。
- `draft/compute` 可以在预算内执行；发布、审批、模型投入训练及删除资产需要真人授权，Admin 操作不暴露通用 Agent Tool。

## 4. API 和 Agent Tools：应当开发，而且从第一版本开始设计

### 4.1 API、工具与工作流三层区别

**Service API**：应用内部 Python 领域服务，实现数据验证、计算、任务、发布与审计，具有唯一的业务真值。  
**HTTP API**：FastAPI 面向前端与程序调用的受控接口，负责身份、项目、序列化、幂等、分页与状态码。  
**Agent Tool**：将若干安全、稳定、具有明确输入/输出 Schema 的领域操作暴露给大模型。一个 Agent Tool 可以组合多个 HTTP/API 服务，但不应只为凑覆盖率把所有底层 CRUD API 原样开放。  
**Workflow**：多个 Tool 的依赖执行链，具备步骤状态、确认点、失败重试和结果回执。

```
手动 UI ────────────────────┐
                            ├── Domain Service ── DuckDB/Parquet/SQLite/Perspective
Feature Agent ─ Tool Host ─┘
                            └── Audit / Permissions / Revision / Job Store
```

首期采用内部 Tool Host/Registry 即可，避免引入额外常驻服务。未来如果需要让外部 IDE、MCP Client 等复用工具，可在业务契约稳定后添加授权 MCP 适配层；MCP 不作为内部工具设计正确性的前置条件。

### 4.2 ToolDefinition 标准

每个工具至少要有：

```text
tool_id / tool_version / display_name / description
input_schema (JSON Schema)
output_schema (JSON Schema)
required_capabilities / allowed_roles / required_data_roles
effect = read | presentation | draft | compute | commit | admin
confirmation = none | plan_preview | explicit_user | independent_reviewer
idempotency = true/false; request_id; expected_revision
validator / executor / response_receipt / timeout / cost_budget
test_case_ids / help_anchor / recommended_ui_entry
```

**能力发现原则：**服务端实际支持的算子、DuckDB 能力、时间语义、字段类型和用户角色决定工具是否暴露。返回 `available=false,reason=...` 可以供 UI 显示，但不可把不可用的执行器当成可执行工具发送给模型。

**Tool Schema 样例（设计稿）：**

```json
{
  "type": "function",
  "name": "feature_preview",
  "description": "预览已验证的特征集；不修改原数据，不进行正式发布。",
  "strict": true,
  "parameters": {
    "type": "object",
    "properties": {
      "feature_set_id": {"type": "string", "description": "当前授权项目内的特征集ID"},
      "revision": {"type": "integer", "description": "期望的特征集修订号"},
      "max_rows": {"type": "integer", "description": "预览上限，由服务端限制"}
    },
    "required": ["feature_set_id", "revision", "max_rows"],
    "additionalProperties": false
  }
}
```

模型参数无法指定 `project_id`、文件路径和 SQL，由受信任会话确定。`max_rows` 即使模型请求过大，也由后端硬限制并明确提示。

### 4.3 最小工具清单与效果分级

| 工具 ID（建议） | 功能 | 关键输入 | 结果 | 效果/授权 |
|---|---|---|---|---|
| `feature.schema.inspect` | 查看当前源字段/类型/角色 | asset/version、字段筛选 | FieldContract 列表 | read |
| `feature.field.search` | 查找字段 ID、别名和候选 | query、类型过滤 | 匹配来源+歧义 | read |
| `feature.contract.inspect` | 查看时间/设备/单位契约 | asset/version | Contract + 状态 | read |
| `feature.contract.validate` | 检查契约差异 | contract draft | 问题清单与建议 | read |
| `feature.contract.propose` | 生成语义候选映射 | field IDs、业务上下文 | 候选注释 | draft |
| `feature.profile.get` | 查看画像及统计范围 | asset、profile ID | 真统计及查询指纹 | read |
| `feature.profile.create_job` | 请求完整画像 | asset、profile config | job ID | compute |
| `feature.profile.event_effect` | 维护/工况对照统计 | event source、范围 | 对照统计结果 | compute |
| `feature.operator.search` | 列出当前可用算子 | types、semantic context、intent | 可用/不可用及原因 | read |
| `feature.candidate.propose` | 提出候选 FeatureSpec | TaskSpec、限制、profile ID | 候选卡清单 | draft |
| `feature.spec.validate` | DAG/类型/时间/单位预检 | FeatureSpec JSON | 验证报告 | read |
| `feature.set.draft_create` | 保存一版特征草稿 | source、Spec、request_id | set ID + revision | draft |
| `feature.set.draft_update` | 修改草稿与依赖 | set、revision、Patch | 新 revision | draft |
| `feature.set.preview` | 执行有限样本预览 | set、revision、scope | 预览及警告 | compute |
| `feature.run.create` | 启动完整计算 | set、revision、预算 | run_id | compute |
| `feature.run.get` | 查看任务进度/日志 | run_id | 实际状态 | read |
| `feature.run.cancel` | 请求取消当前任务 | run_id、revision | 已确认的取消状态 | compute |
| `feature.evaluation.plan` | 检查对照实验协议 | TaskSpec、FeatureSet、baselines | 实验计划、缺口 | read |
| `feature.evaluation.create` | 运行统一切分的真实评价 | plan ID、request_id | eval_id / run ID | compute |
| `feature.evaluation.compare` | 比较同切分的评估证据 | eval_ids | 差异及置信信息 | read |
| `feature.quality.inspect` | 查看特征质量与异常 | set/run | 质量报告 | read |
| `feature.lineage.inspect` | 查看来源、依赖和影响 | feature ID/set ID | DAG/依赖列表 | read |
| `feature.publish.preview` | 审核前影响检查 | set、revision | 门禁/引用/输出大小 | read |
| `feature.review.request` | 向用户建议提交审核 | set、revision | 审核草稿/人工确认入口 | draft |
| `feature.viewer.open` | 在原生 BI 显示结果 | artifact ID、panel config | render receipt | presentation |

真正的 `approve` 与 `publish` 应继续使用现有独立后端 API 与人员授权，不放到通用模型可自动执行的工具集中；如果未来增加 `publish.request`，只负责发起用户确认流程。

### 4.4 工具调用的统一返回值

```json
{
  "tool_id": "feature.set.preview",
  "request_id": "req-099",
  "status": "succeeded",
  "effect": "compute",
  "evidence": {
    "source_sha256": "source-hash",
    "feature_spec_sha256": "spec-hash",
    "rows_scanned": 506,
    "result_scope": "sample",
    "rows_returned": 50
  },
  "artifacts": [{"kind": "preview_table", "artifact_id": "preview-099"}],
  "warnings": ["仅显示50条预览记录，正式计算仍以固定源版本为准"],
  "receipt": {"data_completed": true, "viewer_rendered": false},
  "retryable": false
}
```

不得把 `tool_call 已提交`、`HTTP 202 Accepted`、`计算完成`、`图表已经渲染` 混为一个“成功”。当任务正在运行时响应 `queued/running`，以 `job_id` 单独查询；图表在 Viewer 真正回执后才显示“已生成图表”。

### 4.5 API 约定（新接口应与现有 `/feature-lab/recipes` 共存）

统一前缀 `/api/v1/projects/{pid}/feature-lab`；返回值必须包含 `schema_version`（适用时）、稳定 ID、状态和请求追踪 ID。所有项目/用户身份由服务端鉴权，令牌中的用户 ID 不能由模型修改。

| HTTP | Path | 参数/用途 | 成功/错误 |
|---|---|---|---|
| GET | `/contracts?asset_id=&version_id=` | 查询当前契约、确认状态 | 200；无权限 403/404 |
| PATCH | `/contracts/{cid}` | 更新候选映射；`expected_revision` | 200；冲突 409 |
| POST | `/contracts/{cid}/validate` | 校验类型、时间/实体、单位 | 200，结构化 issues |
| POST | `/contracts/{cid}/confirm` | 人工确认选定字段角色、单位 | 200；未授权 403 |
| GET | `/operators` | 按字段和权限返回算子与参数 | 200，含 disabled_reason |
| POST | `/profile-jobs` | 计算/刷新画像，幂等 ID | 202 + job_id |
| GET | `/profile-jobs/{jid}` | 查询运行与检查点 | 200；失败有 error_code |
| GET | `/profiles/{profile_id}` | 已完成结果、证据、统计范围 | 200 |
| POST | `/candidates` | 生成/保存候选清单 | 201，含方案/验证状态 |
| POST | `/sets` | 建立 FeatureSet 草稿 | 201 + set_id/revision |
| PATCH | `/sets/{sid}` | 受控 Patch 和并发检查 | 200；过期 409 |
| POST | `/sets/{sid}/validate` | 完整约束预检 | 200，feasibility |
| POST | `/sets/{sid}/preview-jobs` | 大数据预览转异步任务 | 202 + run_id |
| POST | `/sets/{sid}/runs` | 全量计算并记录版本与预算 | 202 + run_id |
| GET | `/runs/{rid}` | 运行状态/阶段/错误/进度 | 200 |
| POST | `/runs/{rid}/cancel` | 请求取消 | 202/200 |
| GET | `/runs/{rid}/result` | 不可变结果/授权 Arrow | 200 或 409 未完成 |
| POST | `/evaluations/plans` | 创建/检查 EvaluationProtocol | 201 / issues |
| POST | `/evaluations` | 真实对照实验 | 202 + eval_id |
| GET | `/evaluations/{eid}` | 评价指标、切分和证据 | 200 |
| POST | `/sets/{sid}/request-review` | 发起特征审核 | 200 |
| POST | `/sets/{sid}/approve` | 独立审核人审批 | 200；权限 403 |
| POST | `/sets/{sid}/publish` | 原子发布/依赖核验 | 201；状态冲突 409 |
| GET | `/catalog` | 项目内特征集检索、分页 | 200 |
| GET | `/catalog/{sid}/lineage` | 来源图与模型引用 | 200 |
| GET | `/tools` | 权限及运行能力过滤后的 Tool Manifest | 200 |
| POST | `/agent/plans` | 用户文本→结构化计划 | 201 / needs_clarification |
| POST | `/agent/plans/{plan_id}/execute` | 按状态与权限执行 | 202 + execution_id |
| GET | `/agent/executions/{execution_id}` | 逐步 ToolResult、回执与恢复点 | 200 |

**错误协议**建议统一为：`code,message,field_ids,issues,retryable,request_id,details`。HTTP 400/422 用于非法参数或无法验证的契约，403/404 用于安全边界，409 用于源指纹/版本修订/状态冲突，413 用于超量数据传输，429 用于限流/资源预算，503 用于依赖不可用；用户看到可执行的修复建议，不能只显示“系统错误”。

### 4.6 示例：新建特征草稿与异步预览

```http
POST /api/v1/projects/{pid}/feature-lab/sets
Content-Type: application/json
Idempotency-Key: req-feature-001
```

```json
{
  "asset_id": "asset-123",
  "version_id": "base",
  "contract_revision": 3,
  "name": "负压波动基础特征",
  "feature_spec": {"schema_version": 2, "features": [{"feature_id": "f1", "operator_id": "rolling.mean", "operator_version": "2.0", "input_field_ids": ["fld_negative_pressure"], "parameters": {"window_kind": "rows", "window_value": 5, "min_periods": 5}, "output": {"technical_name": "pressure_ma5"}}]},
  "request_id": "req-feature-001"
}
```

服务器验证后返回 `201 {"set_id":"fs-001","revision":1,"state":"draft","validation_issues":[]}`；调用 `/sets/fs-001/preview-jobs` 返回 `202 {"run_id":"run-001","state":"queued"}`，前端任务中心继续追踪。**上例是部分请求结构示意**；正式实现应由同一个 Pydantic/JSON Schema 生成完整请求校验，不能把缺少来源时间条件的请求解释为可用于监督预测发布。

### 4.7 动态工具发现与生成方式

- 单一 `FeatureToolRegistry` 持有 `ToolDefinition`；从 Python Pydantic Input/Output Models 生成 JSON Schema 与 HTTP OpenAPI 对照，不单独人工维护三份参数清单。
- UI 的算子卡、Agent 可调工具、帮助页和测试都引用同一 `operator_id` 与 ToolDefinition，记录 `op_version`、适用字段类型及 `test_case_ids`。
- 先按用户意图从 Manifest 检索最多约 8—15 个候选工具给模型；模型需要别的能力时调用 `feature.operator.search`，服务端验证能力后再注入。
- `feature.tools` 与 `bi.*` 共用身份、执行回执、任务进度和审计总线；`viewer.*` 的图表操作由同一 Perspective 状态桥执行，避免双源。
- 在内部使用 Python 工具调用接口即可；对外开放 MCP 的条件：项目权限映射/审计/结果脱敏/版本兼容/密钥代理/租户隔离均完成真机验收。

## 5. 文件级开发计划及数据库迁移（按 v0.24.7—v0.25.2 实施）

### 5.1 分层代码目录

```text
src/timepilot/
  feature_contracts.py               # N: DataContract / TaskSpec / FeatureSpec Pydantic 契约
  feature_semantics.py               # N: 字段语义候选、人工确认和来源证据
  feature_operator_registry.py       # N: 版本化受限算子目录与类型/单位/时序规则
  feature_profiler.py                # N: 全量及范围化质量/统计画像
  feature_temporal_profile.py        # N: 时间、实体、工况与维修事件画像
  feature_dag.py                     # N: DAG 检查与依赖解析
  feature_compiler.py                # N: DuckDB 安全编译、缓存 Key、类型推导
  feature_execution.py               # N: 持久化 job、租约、计算、物化和恢复
  feature_evaluation.py              # N: 实验比较与评估指标
  feature_split_protocol.py          # N: 时间/设备/工况分层切分与泄漏检测
  feature_selection.py               # N: 训练折内候选筛选与消融
  feature_registry.py                # N: FeatureSet/FeatureVersion、来源与审批
  feature_materialization.py         # N: Parquet 原子发布与引用统计
  feature_monitor.py                 # N: 漂移、重算差异、版本过期信号
  feature_tool_manifest.py           # N: ToolDefinition、输入/输出 Schema、权限和能力
  feature_agent.py                   # N: ContextBuilder、Planner、ToolExecutor、Observer
  feature_lab.py                     # M: 保留旧 FeatureRecipeInput 与兼容器
  feature_lab_api.py                 # M: 新 v2 API + 原 /recipes 旧路由
  project_migrations.py             # M: 项目库 v18→v19..v23 增量迁移
  native_bi_api.py                  # M: 授权特征 Artifact 注册为 Perspective 数据表
  api.py                            # M: 新路由集成
  static/feature-lab-ui.js          # M: 旧页面迁移适配与路由
  static/feature-shell.js           # N: 状态/导航/任务中心/通用选择
  static/feature-profile-ui.js      # N: 画像
  static/feature-builder-ui.js      # N: 构造
  static/feature-evaluation-ui.js   # N: 评价
  static/feature-catalog-ui.js      # N: 特征资产
  static/feature-dialogs.js         # N: 语义、参数、发布审核 Modal
  static/feature-lab.css            # N/M: 统一响应式布局
  static/feature-assistant.js       # N: 共用 TimePilot 助手浮窗适配
```

建议将旧 `FeatureStep` 映射为 `FeatureSpec v2`：`arithmetic→arithmetic.binary`、`rolling_mean→rolling.mean`、`rolling_std→rolling.std`、`lag→lag.rows`、`difference→difference.rows`，并固化旧排序规则和运算版本。**旧配方应原样读取和复算，不能无提示改变窗口定义。**

### 5.2 数据库增量迁移

| Schema | 实施阶段 | 建表/扩展 | 关键索引与约束 |
|---|---|---|---|
| v19 | v0.24.7 | `feature_data_contracts`,`feature_semantic_bindings`,`feature_operator_versions` | `project_id+asset_id+version_id+revision`；来源 SHA、确认人 |
| v20 | v0.24.8 | `feature_profiles`,`feature_profile_jobs`,`feature_profile_evidence` | 源 SHA、profile version、selection SHA、scope；可恢复任务 |
| v21 | v0.24.9 | `feature_sets`,`feature_definitions`,`feature_dependencies`,`feature_runs`,`feature_run_checkpoints`,`feature_artifacts` | project-scoped FK、DAG 版本、`request_id` 唯一、租约、内容哈希 |
| v22 | v0.25.0 | `feature_tasks`,`feature_evaluation_plans`,`feature_evaluations`,`feature_folds` | split manifest SHA、基线 ID、实际 label 时间字段 |
| v23 | v0.25.1—v0.25.2 | `feature_quality_reports`,`feature_publications`,`feature_monitor_snapshots`,`feature_agent_plans`,`feature_agent_steps` | 审批、模型调用用量、工具执行证据、外键与权限 |

迁移规范：先从 `TIMEPILOT_HOME/projects/catalog.sqlite3` 做 WAL 一致性备份；检查 `PRAGMA integrity_check`；每次事务只提升实际完成版本；重复运行幂等；不支持比当前代码更高的 DB 版本时拒绝写入；迁移后对旧配方、项目、模型和视图数做前后对照。不要通过直接删高版本表实现回滚；旧程序需要旧版写入时使用升级前备份。

### 5.3 v0.24.7：数据格式、语义契约与 Tool 基础（P0）

**目标：**任何导入数据都能获得结构可核实的 DataContract，正式时序任务有明确实体/时间/单位语义；工具清单从此版开始维护。

**改造工作：**

- 建立 FieldContract/DataContract/TaskSpec/FeatureSpec v2 Pydantic 模型及 JSON Schema，定义字段 ID、原名、来源、可用时点、单位字典、类型和证据。
- 建立来源列名/列序号/内部 ID 的稳定映射；重名、空表头和前导零的完整测试。
- 迁移旧 FeatureStep 的只读适配，建立算子注册与版本列表。
- 在数据管理导入完成界面增加“检查字段含义”入口；FeatureLab 顶部出现契约状态与一键确认面板。
- 建立 `FeatureToolRegistry` 基础，先开放 `schema.inspect / field.search / contract.validate / operator.search` 等只读工具。
- 完成数据库 v19、OpenAPI 与版本帮助说明。

**门禁：**506×15 样本列名/类型/列序/源坐标对齐 100%；`Time=1..506` 不会自动确认为时钟时间；跨项目契约编辑阻断；旧配方和项目能正常读取；PIT 校验用例无越界。

### 5.4 v0.24.8：数据画像与四页工作台 Shell（P0/P1）

**目标：**首次进入 FeatureLab 即能读懂数据、选择字段、确认关键语义，生成可追溯的画像。用户不会被算子长表单淹没。

**改造工作：**

- 构造 `DatasetProfile/FieldProfile/TemporalProfile/RegimeProfile`，按统计范围记录来源哈希、版本和扫描行数；异常候选不默认为真实故障。
- 画像任务接入持久化队列、租约、进度、重试；建立 DataContract 更新时局部失效机制。
- 前端实现顶部项目上下文和四个 Tab；本版本先交付数据画像主页面、字段抽屉、语义确认、操作推荐和任务中心。
- 接入单一 Perspective Bridge，已有图表和字段系列在切换 Profile/Build Tab 时保留。
- 增加必要的内嵌手册和字段操作帮助，去除 `prompt/confirm`。
- 完成 v20 迁移及与现有 import-summary 任务状态统一。

**门禁：**数据全量指标与独立 DuckDB 结果对齐；大文件进度可见并可重启恢复；画像不会改变源版本；1440×900 与窄屏下图表可操作；无大模型可完整画像。

### 5.5 v0.24.9：可视化特征构造、DAG 和可恢复执行（P1）

**目标：**用户能将 A8+A10、按设备滚动平均、滞后、维护重置等操作组合成 FeatureSet；来源、结果和错误均可复查。

**改造工作：**

- `FeatureOperatorRegistry` 首批提供算术、差分、lag、rolling mean/std/min/max、EWMA、事件距今、单位换算（条件满足时）；每算子包含 JSON Schema、类型/单位/时间校验及独立 SQL 参考测试。
- `FeatureDAG` 实现类型检查、拓扑排序、无环验证、输出重名防护；`FeatureCompiler` 输出白名单 DuckDB SQL，拒绝用户/模型自由 SQL。
- 完整执行与预览走独立可恢复任务；临时 Parquet 完成哈希校验后原子提交；保存成本、行数、有效窗口覆盖率。
- 前端实现“字段选择 → 算子卡 → 参数抽屉 → 原始/结果对照 → 加入特征集”及列表/DAG 双模式。
- 在原生 Perspective 增加受控派生结果 Panel，保持手工图表配置、排序、筛选和保存状态。
- 完成 v21 迁移及 `feature.set.* / feature.run.*` 工具映射。

**门禁：**A8+A10、A2/A4 按 5 条记录移动平均与独立参考对照；按设备窗口不能混窗；时间窗口与行数窗口不同；`available_at>prediction_time` 训练值不可用；100 万行压力测试记录峰值内存和耗时；幂等重试不生成重复正式物化结果。

### 5.6 v0.25.0：特征实验与评价（P1）

**目标：**让工程师在同一基准下判断“这些特征是否有用”，并能看到解释、成本、工况稳定性及标签约束。

**改造工作：**

- 建立 `EvaluationProtocol`；时间滚动、按设备/批次组隔离、按工况留出和组合切分生成固定 fold manifest。
- 内置 Raw-only、Raw+Basic、Raw+Auto 三类可复现基线；复杂 embedding/机理组作为可选扩展，不影响基础交付。
- 标准化/插补/编码与目标筛选限制在训练折内部拟合，验证与测试使用冻结变换器；防止信息泄漏。
- 开发无监督质量/稳定性路径与监督性能评估路径；任务缺少标签时不能返回“预测贡献提升”。
- 实验页面显示同切分下的按折指标、置信区间、耗时和冗余；接入模型管理已存在的可用算法适配。
- 完成 v22 迁移；开放 `evaluation.plan/create/compare` 工具。

**门禁：**固定标签泄漏样本和同批次泄漏样本全部被正确隔离；实验可由 source SHA + split manifest + feature graph + model seed 复算；相同折对照才允许显示“增益”；不同工况/设备外推结果能独立查看。

### 5.7 v0.25.1：FeatureSet 资产与长期使用（P1）

**目标：**让审核后的特征可版本化发布、被 BI 和模型稳定引用，并可查询漂移与影响。

**改造工作：**

- 完善 `FeatureRegistryService`：特征组/版本、弃用、依赖关系、来源、审核意见、发布 Manifest、复算和回滚。
- 计算结果与源 Parquet 的物理引用计数隔离，资产删除阻断活跃模型/作业/视图引用。
- 模型输入从 `FeatureSet version + TaskSpec + PIT Snapshot` 读取；训练历史与在线可用性规则一致，增量物化失败可以恢复。
- 引入轻量漂移快照：同工况/跨工况分布、缺失、特征覆盖变化。没有稳定参考数据时只显示描述统计。
- 完成特征资产页面、版本差异视图和 v23 迁移。

**门禁：**旧版发布资产仍可读；发布后 SHA 不变、模型引用版本固定；未审核不可发布；用户 A 不能访问用户 B/别的项目；相同请求重复发布幂等；漂移检测有清楚的参考时间窗口。

### 5.8 v0.25.2：动态 Agent、自动特征实验与研究包（P2/P3）

**目标：**复杂需求能够根据真实数据动态检索工具、提出方案、逐步执行、观察结果、有限重规划；用户能审核其结论。

**改造工作：**

- 建立 `FeatureAgentService`：上下文构建、工具检索、结构化规划、执行步骤、观察结果、有限重规划和成本统计。
- 将四种逻辑 Agent（发现、验证、实验、报告）接入一个执行框架，显式区分数据证据与模型观点。
- 支持用户自然语言发起“画像→候选→预览→评价→汇总”，并保留真人发布确认。
- 可选适配 tsfresh Efficient、pycatch22、TSFEL，以及机理特征/学习型表示研究接口；独立 requirements extras 与离线安装校验，不强制改主运行环境。
- 建立真实工业样例双人标注与反馈集，区分无模型/基础模型/推理模型策略；导出真实成功率、错误分类、耗时和模型费用。

**门禁：**可执行的简单指令固定集全部通过；模型输出未知工具、权限越界、虚构字段、注入、超时和空 plan 均能安全降级；复杂执行完成判据同时包含真实数据计算证据及 Viewer 回执；全链路 Task Success Rate 在真实标注集上达到经过基线评估确定的发布阈值。

## 6. 验收工程、数据样例与运行命令

### 6.1 必须固定的测试数据集

| Fixture | 具体内容 | 检验目标 |
|---|---|---|
| F01 | 506×15，`Time=1..506,A1..A14` | 字段保真、顺序时间的语义警示、加法与 MA |
| F02 | 2—10 台设备、有相同时间戳、缺失、重复、乱序 | 排序、实体隔离、重复键、精确窗口 |
| F03 | 维护事件前后、设备重置、多工况切换 | 事件范围、reset、工况漂移 |
| F04 | `event_time=10:00,available_at=12:00,prediction_time=11:00` | 明确阻断未来可用数据 |
| F05 | 贴片节点 A/B、批次与终检标签 | 特征可用时点、标签隔离、评价切分 |
| F06 | 缺失/重复列名、GB18030、Excel 公式、字符串 `00123` | 导入与契约保真 |
| F07 | 1M 行×20 列、多设备、Parquet/CSV | 内存、流式聚合、可恢复任务与渲染 |
| F08 | 模型 429/超时/空调用/恶意单元格指令 | 回退、权限、审计与恢复 |

### 6.2 接口、引擎、UI 三层验收

**契约层：**JSON Schema 校验、Pydantic 版本、类型、单位、列源、时间和身份映射；拒绝自由路径、未知字段和跨项目 ID。  
**计算层：**DuckDB 窗口/ASOF/聚合与独立 NumPy/Pandas 参考结果对照；相同输入、版本、随机种子输出可复算；精度采用数据类型相关的绝对/相对容差。  
**交互层：**系统已有 Chrome 实际加载 Perspective、字段拖拽、配置保存、图表切换、选中系列保留、特征叠加、作业状态、刷新恢复、撤销/重做；浏览器组件模拟不能替代真机 E2E。  
**企业层：**跨租户、项目角色、共享源文件、模型引用、不同审核人和工具调用预算；数据结果与模型发送范围必须有日志。  
**Agent 层：**固定真实任务集逐项核对意图→字段→计划→工具→SQL 结果→Artifact→Viewer 回执；分开报告规则与模型效果，禁止只用工具选择准确率代替完成率。

### 6.3 关键用户故事的验收断言

1. 选择 506 行数据的 A8 与 A10 → 创建新列 → 预览每行数值 → 保存 FeatureSet；源 SHA 和原 Viewer 配置不改变。
2. “对 A2、A4 计算过去 30 分钟平均”而只有顺序数字 `Time` → 系统只问一个时间信息问题，提供“按 5 条记录”替代方案，不能悄悄理解成 30 分钟。
3. 两台设备在同一秒同时有数据 → 设备 A 的 rolling 不引用设备 B；缺失值与窗口覆盖率能够解释。
4. 11:00 预测、12:00 才入库的检验结果 → 无法进入 11:00 的监督特征；用户试图绕过时返回明确 blocker。
5. 发布特征集前人工关闭对话框 → 计算/草稿保留，发布未发生；重复请求不出现重复资产。
6. 模型输出“已完成”但 DuckDB 任务仍在队列 → UI 只显示排队/运行，不能宣称生成正式结果。
7. Perspective 用户手工切图并选中 A2/A4 → Agent 加入新特征 Panel 后保留原系列、筛选和工作区；保存、刷新恢复一致。
8. Agent 提出不存在的“温度”字段 → 展示现有字段及候选来源，不自动把 A1 当作温度。
9. 无标签工况数据 → 能完成画像、特征和稳定性分析，评价页面不显示虚构的召回率/准确率。
10. 历史 v0.24.6 FeatureRecipe → 新版能直接读取、按原语义复算、并与新的 v2 FeatureSet 共存。

### 6.4 建议一键验收命令（开发完成并接入 launcher 后才存在）

```bash
conda activate server
bash TIMEPILOT.sh source-check
bash TIMEPILOT.sh
bash TIMEPILOT.sh accept feature-contract-v0247
bash TIMEPILOT.sh accept feature-profile-v0248
bash TIMEPILOT.sh accept feature-execution-v0249
bash TIMEPILOT.sh accept feature-evaluation-v0250
bash TIMEPILOT.sh accept feature-registry-v0251
bash TIMEPILOT.sh accept feature-agent-v0252
bash TIMEPILOT.sh accept feature-agent-v0252 --mode configured
```

输出应落到 `reports/featurelab/<version>/<work_package>/`，包括 `summary.md,tests.json,environment.json,source_hashes.json,failed_cases.json`；大数据测试另保存执行时长、峰值 RSS、DuckDB 查询计划和浏览器渲染指标。沿用用户原来的 Python 3.12.12 环境、统一 requirements 和本机 Chrome；允许现有自动构建脚本按指纹重建 Perspective，但不要求手动下载额外浏览器。

## 7. 与已有系统的接入顺序及非目标

**已有导入系统：**FeatureLab 读取 DataContract、未确认 Region/字段和数据完整性状态；必要时返回数据管理页进行纠正。FeatureLab 不再另造导入解析逻辑。  
**已有 BI：**FeatureLab 将画像、候选和特征结果注册为同一 Perspective Workspace 的授权数据视图；复用保存、布局、状态桥与 render receipt。  
**已有 Agent：**使用统一模型设置页、工具注册和任务日志，扩展 `feature.*` 命名空间；不放第二个常驻大模型聊天框。  
**已有模型管理：**Model Task 引用已发布 `FeatureSet@version`、EvaluationProtocol 和 PIT Snapshot；不直接拿临时 preview_rows 做训练。  
**现有项目存储：**复用 SQLite/Parquet/项目资产权限与备份机制，延迟引入独立特征服务和在线 Feature Store。

**首期明确不做：**自由 SQL/Python 模型执行、模型自动批准或删除数据、没有预测时点约束的全自动监督训练、无依据把字段命名为业务参数、仅凭相关性声称物理因果关系、未经实测就将所有 tsfresh/TSFEL 算子默认加载、全量把原始生产记录发送给第三方模型。

## 8. 给项目开发和业务部门的交付清单

**研发侧本轮需要交付的规范：**

- `contracts/featurelab/data_contract.schema.json`、`field_contract.schema.json`、`task_spec.schema.json`、`feature_spec_v2.schema.json`、`feature_run.schema.json`。
- `examples/featurelab/` 中可直接导入的设备宽表、长表、维护事件、批次、标签及 JSON 契约，包含“缺少单位/时区/可用时间”的反例。
- `docs/featurelab/USER_GUIDE.md`（面向一线工程师）、`API_CONTRACT.md`（接口与错误码）、`OPERATOR_CATALOG.md`（算子）、`AGENT_TOOL_MANIFEST.md`（工具/权限/回执）。
- 上述四个主页面及每步的原型与真实可运行版本、跨路由状态恢复、系统 Chrome 全链路验收记录。
- 使用真实 DuckDB/Arrow/Perspective 的数值和 UI 证据、数据库迁移/回滚记录、旧配方兼容测试与发布 SHA 报告。

**业务侧首批最少提供：**设备/批次键、采集时间与时区、数据可用时间（至少关键数据）、数值字段及单位、运行工况、维护事件；正式监督任务再增加产品或批次唯一 ID、节点 A/B 决策时点、标签与标签可用时间以及允许的错误代价。缺失内容可以先画像探索并标记待确认，不推断为已经存在。

**第一阶段最小可用范围（MVP）：**按 v0.24.7→v0.24.9 实施后，应能用任意合法设备表完成字段核实、画像、选列、基础/时序特征、原始与结果对照、保存特征草稿和有证据的预览，模型不配置也能完成。这是后续自动评估和多步智能实验的基础。

**附带联调模板：**`TimePilot_FeatureLab2_Contract_Samples.zip` 提供 3 份 CSV、3 份 JSON Schema 和 3 份已校验的示例契约。模板只验证静态结构；时点、单位、权限、SQL/DAG 与业务语义必须继续由正式后端校验。

## 9. 官方依据与进一步核对项

1. DuckDB Window Functions：`ROWS/RANGE`、`PARTITION BY` 与排序语义，窗口是内存敏感算子： https://duckdb.org/docs/stable/sql/functions/window_functions
2. Featuretools Cutoff Time：按指定时点限定特征来源： https://docs.featuretools.com/en/stable/getting_started/handling_time.html
3. Feast Point-in-Time Joins：时间戳之外的回填/可用时间过滤： https://github.com/feast-dev/feast/blob/master/docs/getting-started/concepts/point-in-time-joins.md
4. OpenAI Function Calling：工具参数 JSON Schema、调用/回传结果流程与动态工具检索： https://developers.openai.com/api/docs/guides/function-calling
5. OpenAI Structured Outputs：严格 JSON Schema 及其支持范围/限制： https://developers.openai.com/api/docs/guides/structured-outputs
6. Perspective Documentation：Viewer、工作区和 Virtual Server： https://perspective-dev.github.io/guide/
7. tsfresh/TSFEL/Featuretools/MLForecast/pycatch22：参见上一版研究报告的官方资料目录。

**使用边界：**上述公开资料用于确认算法语义与工具调用设计；本文的 API、数据表、版本号、目录、UI 及验收目标均是针对 TimePilot v0.24.6 的工程设计建议，尚须逐项开发和真实运行验收。
