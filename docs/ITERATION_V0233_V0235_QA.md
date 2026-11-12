# TimePilot v0.23.3—v0.23.5 源码迭代与验收记录

2026-11-11。基线为用户提供的 `TimePilot_v0.23.2_COMPLETE_SOURCE(1).zip` 和同日《源码逐条迭代计划》。发布类型：**可复现的完整源码迭代包；未获得目标机生产验收签字**。

## 一、按版本完成的实际改动

| 版本/工作包 | 源码交付 | 当前可复核证据 | 尚待验收或未实现 |
| --- | --- | --- | --- |
| 0.23.3 路由/大画布 | `bi-workspace-layout.js/css`, `native-bi.js/css`, `app.js`, `index.html`, `frontend/native-bi/src/main.js` | 现有 Chromium，1440×900 静态工作台画布高 651.5px；其他四档视口无水平溢出 | 真实 Perspective 5.6.1 运行态全链路，以及 20 次带真实数据的 mount/dispose |
| 0.23.3 浮窗 | `bi-assistant-fab.js/css`；旧 `bi-conversation.js` 移入浮层 | 20 次组件挂载/销毁，打开/收起/最小化、响应式组件测试通过 | 头像离线素材因未确认使用权，采用本地图形降级；拖动位置后端仅持久化 mode/width；历史 prompt/confirm 引导操作尚待无阻塞表单替换；消息 Store/View/Controller 尚未完全抽离 |
| 0.23.3 用户偏好 | `bi_ui_preferences.py`、两条 API、SQLite 表 | 非法 mode/width、跨项目偏好拒绝、偏好复读测试 | 企业双账号实机授权验收 |
| 0.23.4 Region 摘要 | `import_summary.py`、`import.region.stats`、摘要 UI、SQLite 缓存及摘要任务 API | Excel 单 Sheet 三 Region、前导零、源哈希更改后的拒绝、独立缓存、API 跨项目隔离 | 百万行、复杂合并单元格/GB18030 及 DuckDB 直接聚合性能基准；任务跨进程租约/自动重启恢复仍未覆盖 |
| 0.23.4 可选语义解释 | 复用 `ImportSemanticAssistant` 的 schema-only 模型注释，故障注入单测 | 无模型配置、超时/429、格式错误时保留本地证据与导入能力 | `SummaryValidator` 严格逐证据核对、全新 annotations-v2 持久化和正式 LLM 质量指标仍需完成 |
| 0.23.5 意图/可行性 | `bi_intent_resolver.py`, `bi_feasibility.py` | 白名单/未知列/无可用算子/未知模型能力均可解释 | 当前高阶模糊意图只覆盖 Pearson 矩阵；趋势、对比、分布的模糊任务仍以 unsupported 返回，原明确工具不受影响 |
| 0.23.5 计划状态机 | `bi_analysis_plan.py`、持久化 plans/executions/matrices、API | plan-id、expected_revision、request-id 幂等、Stale、Cancel、渲染回执、Undo/Redo，模拟计算的全周期接口测试 | 跨进程执行租约/完整中断恢复、更多计划类型；真实计算长任务取消必须目标机验证 |
| 0.23.5 Pearson 算子 | `bi_statistics.py`, `bi.stats.correlation` 工具 | SQL 字段受限，完整筛选行集、不做隐形 1000 行抽样，n_pair、常量/NULL/不足样本状态 | **运行容器无 DuckDB，真实 Pearson 数值测试被 SKIP**；100 万行耗时/内存未量化 |
| 0.23.5 热力图 | `correlation-heatmap.js`、计划卡、工作区渲染/查询 | Chromium 本地 2×2 合成矩阵可渲染并标注 n_pair；导出 CSV 控件存在；服务器模拟矩阵经计划执行后保存/撤销回放 | 真实 DuckDB→Arrow→Perspective/SVG→保存/刷新恢复的**同一个** Chrome 端到端链路尚未得到证据 |
| 0.23.5 开发者模糊评测 | `benchmarks/bi_nl/v2_fuzzy_cases.jsonl` 56 条、schema、离线评分器和测试 | 离线意图 54/56；可行性 55/56，旧 99 条文件未修改 | 双人盲标/仲裁、真实模型调用、费用/延迟与真正工具执行正确率 |

## 二、数据库及权限

原 `project_schema` v4 增量升级到 v7，新增 `bi_ui_preferences`、`import_region_summaries`、`import_semantic_annotations_v2`、`import_summary_jobs`、`bi_analysis_plans`、`bi_analysis_executions`、`bi_analysis_matrices`。数据库迁移保持原有记录、原件哈希、BI workspace/event/view/turn 数据；升级前尝试生成一致性备份 `catalog.pre-v0235.sqlite3`。SQL DDL 使用调用方事务执行，拒绝自动降级。

计划只接收服务器验证的项目、资产版本、工作区修订、列名和注册工具；不存在“允许模型输出 SQL 并执行”的逻辑。项目/租户/用户边界沿用原有 `BIWorkspaceService` 和 `ProjectCatalog`。Matrix 再次核验用户、来源 SHA 和筛选 fingerprint。图表渲染后发送回执才可进入 `succeeded`。

## 三、已执行和可复核的测试

- 原版行为回归：`tests/test_conversational_bi_v0231.py` 旧接口继续可用；项目与导入测试覆盖原目录和发布流程。
- 新增 API、摘要、权限、计划、SQL 编译、静态图表与拟合度测试；真实 DuckDB 测试在本容器执行时显示 `SKIPPED`，不得计入通过。
- `scripts/accept_bi_workspace_ui_v0233.py`：利用已有 Chromium 进行 5 个尺寸的组件测试、20 次浮窗挂载销毁和 2×2 合成矩阵 DOM 检查。**这是浏览器 fixture，不含真实数据引擎、实际 Perspective 或线上模型。**
- `scripts/evaluate_bi_fuzzy_v0235.py --actual-engine-ready`：将引擎 *可用性模拟为真*，独立测试本地规划器；56 条开发者合成标注，意图 54/56 (96.43%)、可行性 55/56 (98.21%)。该参数**不执行引擎**，报告 `real_statistical_executions=0`、`actual_browser_renderings=0`、`llm_calls=0`。

当前验证容器使用 Python 3.13.5；所需的目标 Python 3.12.12、DuckDB、PyArrow、已构建 Perspective 和目标机器 Chrome 全链路并不齐全，且无联网安装条件。源码可校验，目标机生产级验收暂为 `BLOCKED`。**不得根据本文件描述宣称 0.23.5 已生产发布。**

## 四、命令

在 **Python 3.12.12** 和已安装 `requirements.txt` 依赖的目标环境运行。继续使用原统一入口，不新建 Conda 环境，不自动下载浏览器。

```bash
conda activate server
cd /path/to/TimePilot_v0.23.5
bash TIMEPILOT.sh source-check
bash TIMEPILOT.sh
```

另开终端分别执行：

```bash
bash TIMEPILOT.sh accept bi-ui-v0233
bash TIMEPILOT.sh accept import-summary-v0234
bash TIMEPILOT.sh accept bi-fuzzy-v0235
bash TIMEPILOT.sh accept bi-fuzzy-v0235 --mode configured
bash TIMEPILOT.sh accept native
bash TIMEPILOT.sh accept conversational-bi
```

前三个命令是源码、组件和合成规划评测；第四个命令强制检查真实 DuckDB/PyArrow/Perspective 并调用已有 Live Native BI 验收，若缺依赖必定返回非零，不会静默当作 PASS。当前真实统计测试、原生 BI Live 和新模糊闭环测试仍需目标机再验证。

## 五、剩余工作优先级（按拒绝发布风险）

1. **P0**：在目标机安装原有锁定依赖并执行真实 DuckDB Pearson 数值核验；补充 1,000,000 行 / 40 字段资源预算数据，处理真实 `COUNT/CORR` 与 Arrow 流程兼容问题。
2. **P0**：为同一条模糊指令编排真实 Chrome E2E：Plan → DuckDB → Matrix → SVG → 保存 → 刷新 → 恢复 → 撤销，捕获请求、SQL 行数、结果 SHA 与浏览器回执。
3. **P0**：企业用户权限和两个独立浏览器 Session 并发测试；断网/超时重试、计划取消执行租约，确保多 worker 不会重复运行。
4. **P1**：完成原对话 UI 的 Store/View/Controller 全面拆分；用内联表单替换旧 prompt/confirm；持久化草稿/弹窗位置并实现移动端体验细节。
5. **P1**：将摘要大文件统计迁至 DuckDB/Parquet，补足 GB18030/多 Sheet 极端样例；独立任务续跑，严格 Schema/证据 LLM 校验及 annotation v2 缓存。
6. **P1**：扩展模糊意图至趋势、比较、异常和分布，完成真实 56 条/更多样本人工双标及模型评测。独立保存模型、规则两套实验报告和成本。

**交付结论：** v0.23.3—v0.23.5 的主要源码路径、基础 API、页面组件和合成/离线测试已实现。该包可以继续开发与在完整目标环境验收；当前没有足够证据将全部计划任务认定为生产级完成。
