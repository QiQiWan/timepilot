# TimePilot v0.32.2｜智能特征 Agent 与同源交付闭环审阅

## 1. 工作范围与结论

本次基于 v0.32.1 的受限特征 Agent 和 v0.32.0 自动分析会话继续改造，保留原始数据、特征集注册、异步计算任务和原生 Perspective 体系。新增经过人工确认的监督任务绑定、真实物化校验后自动提交模型评价、经验证的资产/模型/BI/报告同源清单，并集中更新当前使用文档。仍采用 **Schema 35**；不要求重新初始化项目数据库。

**发布状态**：这份 ZIP 为源码版。单元/接口测试中的控制面产物字节可能是受控替身，用于验证伪造回执拦截；不能据此声称在目标 Mac 上完成真实 DuckDB+PyArrow 物化、第三方模型真实计算或 Perspective 浏览器端到端。即使有客户端 `viewer-receipt`，`production_verified` 仍为 `false`。正式发布需要独立现场证据。

## 2. 业务流程

| 阶段 | 系统执行 | 用户确认 | 可审计证据 |
|---|---|---|---|
| 分析目标 | 规则或受控大模型确定目标/字段与候选策略 | 可改目标，模型不覆盖明确指令 | 模型/规则来源、被拒绝提案与工具回执 |
| 自动分析 | 契约校验、全量画像、候选计算、质量筛选 | 筛选/保留候选 | 来源 SHA、契约修订、候选 ID/质量统计 |
| 监督任务（可选） | 列出**已确认**的标签、预测时点、标签可用时点 | 勾选并确认三项字段；按时间/设备协议切分 | 绑定进 FeatureSpec 的 TaskSpecV2 |
| 特征物化 | 真实异步任务产生 Parquet，检查 DB、任务及文件 SHA | 用户点击“正式计算” | artifact_id、sha256、source/spec SHA、PIT 核验 |
| 自动评价 | 按绑定任务选择探索或监督；监督切分与筛选限定训练折 | 不自动批准或发布 | 实际评价任务、DB、评估产物 ID/SHA 和折内指标 |
| BI 与报告 | 用同一已确认产物打开原始/特征双面板并记录客户端回执；报告包含来源指针 | 浏览器使用和正式业务验收 | BI 客户端回执、报告原始字节 SHA256、delivery JSON |

对于没有合法时间或标签的 `housing.csv` 等样例，系统仍可以给出质量洞察和可复用候选特征，但不能把记录序号当作真实时刻，不能运行监督预测对照，也不会声称模型精度提高。

## 3. 代码位置与数据契约

- `src/timepilot/feature_auto_agent.py`：v0.32.1 受控规划、模型回复白名单、证据 ID、模型无效/超时回退。
- `src/timepilot/feature_auto_analysis.py`：服务端固定工具流程、会话源/契约绑定；保存 FeatureSet 时绑定人工确认 TaskSpec；物化、评价和报告阶段均重新核验身份。
- `src/timepilot/feature_auto_delivery.py`：新增只读的 `supervision_options`、`confirmed_task`、`verified_delivery`。不使用模型猜测标签；只返回已确认且来源不冲突的标签、预测时点与独立标签可用时间。
- `src/timepilot/feature_lab_v2_api.py`：自动会话下的 `/supervision-options`、`/delivery`、`/viewer-receipt`、`/report` 端点，沿用项目/操作者权限与 Pydantic 严格输入校验。
- `src/timepilot/static/feature-auto-ui.js` / `feature-shell.js`：修复监督字段选择，增加监督绑定、同源结果卡片和真实 BI 打开回执处理；人工保存/物化按钮不由 Agent 代点。
- `tests/test_featurelab322_same_artifact.py` 与 `frontend/native-bi/tests/featurelab322_delivery_ui.test.mjs`：覆盖标签独立可用时点、虚假成功、哈希损坏、同源证据、浏览器回执的限度及页面交互。

`verified_delivery` 逐次检查：源资产 ID、版本与 SHA；FeatureSet 的 contract revision、spec SHA；已成功的计算任务、已写入数据库的不可变物化产物及实际文件 SHA；持久化模型评价计划/作业/评价行及其引用的**同一** artifact_id、artifact_sha256；客户端 BI `workspace` 的产物指针与面板声明。任何层级不一致时状态应阻断；客户端回执只能列为 `client_reported_rendered`。

报告 API 输出 UTF-8 Markdown，工作台提供单独的 JSON 同源证据导出、其**字节级 SHA256**、源 SHA、特征集 ID、产物 SHA，以及结构化 `delivery`；报告文本既提供候选质量，也在有同源监督评价时显示训练折内指标，并注明不能直接解释为生产收益。无模型、超时、越权提案回退规则流程，不能跳过上述任一步。

## 4. 文档审阅

本次将全部顶层/子目录 Markdown 纳入链接与版本审计，集中修复以下问题：

| 发现 | 影响 | 处理 |
|---|---|---|
| 根目录 README 首项仍是 v0.32.1，历史说明与最新命令混用 | 安装、数据契约与版本容易混淆 | README 保留简明当前操作，将旧版完整记录迁移到 `HISTORICAL_README.md` |
| `docs/API.md` 从 v0.9.0 开始并宣称“API 尚未认证” | 企业模式可能误判权限要求 | 首页说明当前项目隔离与安全模式，历史段落标注已过期 |
| `docs/ACCEPTANCE.md` 从 v0.5.13 开始 | 容易把旧 BI 验收步骤当成当前统一发布门禁 | 新增 v0.32.2 源码 vs 现场验收要求，旧内容保留历史 |
| `docs/FILE_TREE.md` 仍为 v0.13.0 | 文件级定位失准 | 首页补最新模块索引，旧图标注历史 |
| 文档引用已移除 `BI_WORKFLOW_V040.md` 和 `BI_GRID_V030.md` | 无法打开文档链接 | 移除失效链接，指向仍存在的指南和 OpenAPI |
| 多个按旧版命名的专项报告 | 老接口、命令、权限与数据口径可能已改变 | 增加 `DOCUMENT_INDEX.md` 标明唯一当前指南与历史材料 |
| v0.32.1 分类模型仍显示“探索性评价” | 误解已执行的模型类型 | UI/报告根据同源评价模式显示监督或探索状态 |

本次保留历史技术说明供审计，不将旧说明直接删改为无证据的“新版本已通过”。优先以 [CURRENT_USER_GUIDE.md](CURRENT_USER_GUIDE.md)、[API.md](API.md) 首页、[ACCEPTANCE.md](ACCEPTANCE.md) 首页和运行中的 `/docs` 为当前规范。

## 5. 源码验收与现场阻断

```bash
bash TIMEPILOT.sh db-check
bash TIMEPILOT.sh source-check
bash TIMEPILOT.sh accept feature-lab-v0322 --mode offline
bash TIMEPILOT.sh
```

源码验收至少覆盖：真实字节 SHA、失效契约、任务和 DB 作假、跨项目与未经确认标签拒绝、同一物化文件评价的指标来源、客户端 BI 回执的非生产声明、模型不可用回退、模型越权拒绝、JS 页面监督绑定。模拟环境和假 Parquet 文件仅用于控制面一致性测试。

**生产联调待验证：**Mac Python 3.12.12 + Node 24、真实 Perspective JS/WASM 构建/Chrome 渲染、实际 DuckDB+PyArrow 输出与文件字节 SHA、真实产线传感和质量标签、独立标签可用时点、设备/工况时间外推、真实 LLM API 安全/费用/超时统计、正式权限/人工审批与业务人员体验。缺少这些任何一项不得把 Source QA 写成生产验收完成。

## 6. 回归与发布记录

**代码环境回归（Python 3.13.5 / Node 22.16.0）：** 全量 Python 第一轮 `540 passed / 14 skipped / 0 failed`（220 秒以上，缺失可选依赖导致跳过），之后修订 README 归档、模型证据导出及注释，针对修改区域重新执行 Python 22 项通过，最终全部 Node 47 项通过。源码验收入口 `accept feature-lab-v0322 --mode offline` 在封版后独立执行，完整日志保存在交付证据包。

**验收边界：**未执行目标 Mac Python 3.12.12、Node 24 的完整同机部署；未完成真实工业 Parquet 物化和 Perspective JS/WASM 浏览器运行，也未使用真实模型供应商进行费用/时延评估。跳过/受阻项目不得计入生产通过。
