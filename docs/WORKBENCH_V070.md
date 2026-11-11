# TimePilot v0.7.0 · BI 分析与预测任务联动

## 使用入口

1. 在 `/imports` 上传 ZIP、多份 Excel 或其他受支持的数据，确认候选数据区域，执行转换。原始工作簿与转换版本保留。
2. 在转换版本详情点击 **分析并用于预测**，进入 `/imports/{import_id}/versions/{version_id}/workbench`。
3. 首次打开时后台为不可变转换 CSV 建立 SQLite 只读分析索引；页面显示进度。后续使用已建好的索引，不重复扫描 Excel。
4. 按国家/地区、品牌、车型、数据来源、统计口径和起止月份筛选；可在系列表中选择多个具体 `series_id`，选择条件共同生效。
5. 查看观测、有效数值覆盖率、记录月份覆盖、缺失、负值、来源冲突及发布时间情况。混合统计口径或来源时，阻断合计趋势图，避免把产量与销量等直接相加。
6. 命名并保存筛选视图；视图固定筛选条件、转换版本和来源文件 SHA-256，之后可恢复。
7. 点击 **用于预测** 生成预测任务草稿，查看缺失月份、重复月份、发布时间、最短历史及统计口径的阻断原因。可显式选择只使用通过质量检查的系列。
8. 确认草稿后，单独生成标准时序数据集。系统保存来源 lineage，**不会自动训练**。随后进入 `/new` 页面，人工确认跨度、模型、回测与预算，再决定是否执行。

## 关键数据契约

| 字段 | 来源与用途 |
|---|---|
| `country`, `brand`, `vehicle` | 从原始表识别的地域、品牌和车型信息，仅用于筛选和描述 |
| `source_name` | 业务数据来源，训练要求来源范围清晰 |
| `statistical_scope` | 原始统计口径，过滤销量/产量混合 |
| `series_id` | 稳定系列身份，作为标准数据集的实体列 |
| `timestamp`, `target` | 月初时间及其目标数值 |
| `available_at` | 数据真实可用时间，防止回测用到未来才发布的数据 |
| `source_file`, `source_sheet`, `source_row`, `source_cell` | 逐条来源追溯 |
| `quality_flags` | 缺失、异常、重复、冲突及待核实统计口径 |

分析层使用不可变 CSV 的内容哈希建立一次性 SQLite 查询索引，原始文件及转换结果从不在筛选时被覆盖。每份保存视图生成新的 UUID 和 SHA-256 签名。预测草稿单独保存创建时间、数据源哈希、预检结果和业务假定。

### 覆盖率定义

- **有效月份覆盖率** = 每条系列首末月份区间内有有效数值的月份数之和 / 同区间的日历月份总数。缺失目标值不会被当作有效覆盖。
- **记录月份覆盖率** = 同区间有记录的不同月份数之和 / 日历月份总数。这两个指标可能不同。
- 选择日期区间后，覆盖率仅描述筛选后的区间，不能直接视作未来预测准确率。
- 当前月度趋势按有效目标值求和，不填补缺失值；仅当统计口径和来源各唯一时展示合计。

## 预检与建模边界

- 预检检查：同一月份重复或冲突、月份缺口、缺失/非数值目标、历史长度不足、未核实统计口径、发布时间缺失、来源混用、记录数上限。
- `publication_lag_days` 是用户显式提出的业务假定。系统保存该假定及警告，**不声称它等同真实发布日期**；进行严格回测前应补齐真实可用时间。
- `series_policy=eligible_only` 必须由用户选择；被排除系列及原因保留在草稿中，不静默删除。
- 标准训练数据集存在 200,000 条记录上限，超过时需要进一步筛选。分析层可容纳更多行，但本地索引需要磁盘空间。
- `model_training_started=false` 永远写入初次生成数据集的来源说明。生成草稿及数据集没有提交 `/runs` 请求。
- 这是本地单用户研发工作流；尚未加入企业多用户 ACL 或通用 BI 查询权限分层。

## 接口

公共前缀 `/api/v1/smart-import/imports/{import_id}/versions/{version_id}/workbench`。

| 方法 | 子路径 | 说明 |
|---|---|---|
| GET | `/index-status` | 查询索引阶段及进度 |
| POST | `/build-index` | 异步建立一次性 SQLite 分析索引 |
| POST | `/analyze` | 提交筛选条件，返回级联选项、KPI、趋势、系列、样本 |
| POST | `/facets/{field}?q=...` | 按允许的字段搜索筛选选项 |
| GET/POST | `/views` | 列出/新建不可变筛选视图 |
| GET | `/views/{view_id}` | 恢复已保存视图 |
| POST | `/views/{view_id}/forecast-drafts` | 生成预测草稿并预检 |
| GET | `/forecast-drafts` | 草稿列表 |
| GET | `/forecast-drafts/{draft_id}` | 草稿详情 |
| POST | `/forecast-drafts/{draft_id}/materialize` | 用户确认后生成标准数据集，不启动训练 |

所有筛选字段均使用白名单，SQL 值均采用绑定参数；UUID 路径与原始版本 SHA-256 严格校验。

## 验收与回归

在**已有** Python 3.12.12 环境运行：

```bash
bash TIMEPILOT.sh accept smart
```

包含 v0.6.1 智能导入与 v0.7.0 工作台 API 回归。真实 Mac Chrome 端到端验收使用：

```bash
TIMEPILOT_WORKBENCH_BROWSER_TEST=1 bash TIMEPILOT.sh accept smart
```

该脚本调用系统现有 Google Chrome，不下载浏览器、不安装 npm、不创建 Python 虚拟环境，也不运行真正的训练。使用隔离的临时运行数据，只测试 HTTP 导入、五维筛选、趋势、来源追溯、保存视图、发布时间阻断、草稿准备及跳转建模页面。

原生 Perspective 属于独立的 JS/WASM 发布构建。v0.7.0 的工作台依赖 Python/FastAPI、SQLite 和基础 HTML/SVG，Perspective 不能加载时仍可使用；之前的原生 Perspective Rust 视图生命周期问题不在本功能验收内，需使用 `TIMEPILOT.sh accept native` 单独验收。

## 真实业务数据验证

2006—2026 年 21 个年度工作簿的完整性能和数据质量审阅见 [REAL_DATA_21YEAR_REVIEW_V070.md](REAL_DATA_21YEAR_REVIEW_V070.md)。约 97.7 万条观测的分析索引约占用 368 MiB，建议将 `TIMEPILOT_HOME` 设置在非云同步的数据磁盘。
