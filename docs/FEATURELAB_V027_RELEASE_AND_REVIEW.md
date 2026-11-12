# TimePilot v0.26.2 → v0.27.0 特征工程交付与审阅

**基线**：`TimePilot_v0.26.1_COMPLETE_SOURCE_FIXED(1).zip`；版本：`0.27.0`；项目目录：schema `31`。本次交付为完整源码，不依赖额外 npm 项目或独立数据库。保留旧 FeatureSpecV2、特征计算任务、发布审批及 Perspective 接口。

## 1. 实际完成的改造

| 版本 | 子任务 | 代码路径 | 实现方式及可核查行为 |
|---|---|---|---|
| 0.26.2 | 动态参数表单 | `static/feature-builder-ui.js`, `feature_operator_registry.py` | 算子公开 `controls`、适用字段 ID；加减乘除只显示两字段和运算符，行窗口显示记录数/最少观测，真实时长窗口显示秒数，事件显示 BOOLEAN 事件字段；切换算子会更新表单 |
| 0.26.2 | 类型适配 | `feature_operator_registry.py`, `feature_candidates.py` | 排除标签、实体键、时间列、数值 ID 与高度疑似时间/编码字段；真实时间由已确认 DATE/TIMESTAMP + 时区验证；数值型 `Time` 暂禁入候选，需确认角色 |
| 0.26.2 | 名称自动生成 | `feature-builder-ui.js` + `feature_candidates.py` | 手工特征自动生成唯一技术名；批量候选采用输入+算子+参数+指纹确定的可复现名称。可另设中文显示名 |
| 0.26.2 | 维护事件 | `feature_operator_registry.py`, `feature-builder-ui.js` | 只有确认事件时间/时区且确认 BOOLEAN 事件列后开放 `event.since`；在编译器中按实体分区计算上次事件的时间差 |
| 0.27.0 | 工作台布局 | `feature-builder-ui.js`, `feature-lab2.css`, `feature-shell.js` | 默认快速生成，分析配置／预览／候选选择三栏；BI 画布默认折叠，可展开；手工模式独立存在 |
| 0.27.0 | 语义提示 | `feature_candidates.py`, `feature_lab_v2_api.py` | 对源列的物理类型/命名/已有确认信息提出候选角色和置信度，不直接改写正式数据契约 |
| 0.27.0 | 批量候选 | `feature_candidates.py` | 基础、时序、工业、质量四种模板；遵守注册算子与字段契约，最多 60 项；去重、带来源、参数与解释 |
| 0.27.0 | 候选预览 | `feature_execution.py`, `feature_candidates.py` | 走既有租约队列的 `candidate_preview`；真实数据计算后再限制返回行数，展示样本缺失与常量提示；不会生成正式物化资产 |
| 0.27.0 | 持久化与恢复 | `project_migrations.py`, `feature_candidates.py`, `feature-shell.js` | SQLite schema 31 保存 owner/project/source-SHA/契约修订、模板、候选、选择、布局；刷新页面恢复最新会话；源数据/契约变更时标记过期 |
| 0.27.0 | 资产接续 | `feature_candidates.py`, `feature_lab_v2_api.py` | 选中候选经正式 FeatureSpecV2 校验，保存成已有 FeatureSet 草稿，再走原有预览、物化、审阅和发布流程 |

所有新接口需经项目授权。候选 API 接受字段 ID，不接受自由 SQL/Python 代码。模型 API 不作为基础功能的前置条件。

## 2. 使用流程

1. 以现有启动脚本启动 TimePilot，在项目内导入/选择数据资产。
2. 进入 **特征工程 → 特征工作台**。页面会读出最近一次候选会话；首次使用时选择模板与字段，点击“自动生成候选”。
3. 左侧查看自动判断的字段类别；数值型 `Time`、ID、日期疑似字段不会自动纳入数值计算。需要修正语义，点击“核实字段语义”，更新正式字段契约后重新生成候选。
4. 在右侧勾选保留的特征，点击“预览所选”读取最多八项。计算任务返回后，中部会展示源值/派生值、趋势图和样本质量。选择超过八项时只预览列表中的前八项，正式保存可以包含所有选中项。
5. 确认后输入特征集名称，点击“保存所选特征集”。在下方当前特征集上执行完整计算；结果可在 BI 中查看，并进入原有的审阅与发布流程。
6. 点击“手工构造”可以使用更精细的单算子编辑，算子切换时只显示必要字段；保存后同样进入原有的特征资产链路。
7. 刷新/返回工作台：服务端会按当前项目、用户、资产和版本恢复最近会话与勾选状态；未绑定后端的手工未保存表单不作为持久草稿。

## 3. 新增 HTTP 接口

基础前缀：`/api/v1/projects/{pid}/feature-lab`。

| 方法与路由 | 语义 | 重点约束 |
|---|---|---|
| `GET /operators?asset_id=...` | 返回算子 controls 与适用字段 | 不适用/未实现算子禁用并给出原因 |
| `GET /field-suggestions?asset_id=...` | 识别建议 | 只读，关键角色需用户确认 |
| `GET /candidate-sessions/latest?asset_id=...&version_id=base` | 恢复最近会话 | 项目+用户+数据版本隔离，返回 stale 标志 |
| `POST /candidate-sessions` | 按模板创建会话 | `request_id` 幂等；`template` 为 basic/temporal/industrial/quality；`budget` 为 1–60 |
| `GET /candidate-sessions/{sid}` | 查询会话 | 由 owner 与项目过滤，校验来源 |
| `PATCH /candidate-sessions/{sid}` | 选择、布局与焦点 | `expected_revision` 乐观并发，不能写入非法候选 ID |
| `POST /candidate-sessions/{sid}/preview-jobs` | 提交只读候选预览 | 每次 1–8 个候选；最多返回 100 行，扫描预算 100–500000 行 |
| `POST /candidate-sessions/{sid}/save-set` | 保存选中特征集 | 经原 `FeatureSpecV2` + registry.validate；初版自动工作流只支持探索模式 |

示例：

```json
POST /candidate-sessions
{
  "asset_id":"<实际资源ID>","version_id":"base",
  "request_id":"<客户端生成的UUID>",
  "template":"industrial","budget":30,"task_mode":"exploration"
}
```

```json
POST /candidate-sessions/{sid}/preview-jobs
{
  "request_id":"<客户端生成的UUID>",
  "candidate_ids":["<会话中返回的候选ID>"],
  "max_rows":30,"scan_budget":200000
}
```

执行后通过既有 `GET /runs/{job_id}` 查询 queued/running/succeeded/failed。预览结果附 `scope=sample`、`sample_scope=first_n_rows_after_full_context`、来源 SHA、实际返回行数及逐候选质量信息。`predictive_value_evaluated=false`，不得在 UI 当成预测增益证明。

## 4. 持久化契约

新表 `feature_candidate_sessions`（schema 31），以 `session_id` 为主键，保存 `project_id, owner_id, request_id, asset_id, version_id, source_sha256, contract_revision, revision, state_json, created_at, updated_at`。`(project_id,owner_id,request_id)` 唯一。`state_json` 包括模板、预算、候选定义、选中 ID、字段建议、布局/焦点、生成器版本和请求参数指纹；相同 request_id 若换模板或字段参数，将拒绝作为幂等重放。旧目录采用前进式迁移，升级前由现有备份机制执行 SQLite backup，不对原始文件和已发布特征写入。

## 5. 已进行的验证与结果边界

**代码/接口测试**：按模块隔离执行共 **65 项通过**：本次新增 10 项（字段语义、候选、并发、幂等参数冲突、名称冲突、30→31 升级保留旧特征集、预览排队及模拟 SQL 工作线程等）、原有特征/时间契约/项目目录 27 项、Agent/安全/特征预览相关 23 项、静态 UI 与发布资源 5 项。模拟工作线程只核验 SQL 生成及队列闭环，不能代替 DuckDB 数值正确性测试。合并为单个 pytest 进程的长套件曾在 100 秒时限内执行到 53 项后超时，尚未取得完整的合并回归结论；已分别重新运行相关模块。另有 3 个 DuckDB 测试模块因缺少依赖在收集阶段跳过。

**浏览器审阅**：本机 Chromium + Playwright，使用**模拟 API 与构造样本**确认：三栏布局、候选状态与勾选、真实类型对应的动态字段显隐、预览表格、简化趋势图、BI 区域默认折叠。模拟画面中自动生成了 18 项候选并展示预览表格；这些数据均为测试构造数据。截图保存在外部 QA 文件中，故意不打包进代码。另经 Python `compileall`、Node `--check` 和单一 `requirements.txt` 同步检测。已修复健康检查/API/静态资源响应头固定写入旧版 `0.26.1` 的问题，统一从包版本号读取。

**尚未完成的生产验证**：当前执行容器运行 Python **3.13.5**，缺少 `duckdb` 且无法联网下载依赖；项目部署目标仍为 Python **3.12.12**。因此不能在此容器内声明真实 DuckDB 候选计算、Parquet 全量物化、Perspective 原生加载、Windows/目标 Python 3.12.12 现场部署以及百万级数据性能通过。浏览器截图中的数据是模拟结果，不能作为这些测试的替代。发布判定为：**源码与界面测试通过，真实数据执行及生产部署待验收**。

## 6. 已知边界与下一次验收

- 特征候选现在基于字段物理类型、字段名称与已确认契约；字段基数、统计分布与工况特征的深度推断仍待 0.28 以后完善。
- 默认已实现的算子仅限后端确认可执行的算子；高级 tsfresh、Featuretools、公式编辑、模型内特征选择及自动监督实验不属于本次交付。
- 自动候选暂按**探索特征**生成。有监督任务仍需在手工构造中绑定实体键、事件时间、预测时点、标签和可用时间，走原有评价协议，不能绕过时点核验。
- 预览按完整上下文计算，再限制结果行数；源表超过扫描预算时会拒绝预览。大规模数据的可核验裁剪、时间窗口下推与采样预算自适应安排在后续版本。
- 原生 BI 展开后仍按现有 Perspective 渲染，需要在目标机器使用实际上传数据验收性能和图表交互。

**建议现场最小验收**：使用一份含数值 `Time`、真实 TIMESTAMP、设备 ID、压力数值和 BOOLEAN 维修标记的工业 CSV，核实角色后生成工业模板；预览8项→保存→完整物化→BI原生打开→刷新恢复→修改契约确认过期拦截。随后以10万、50万行进行扫描预算及窗口一致性测试，并记录耗时与峰值内存。

## 7. 目标环境验证命令

在原有 Python 3.12.12 环境运行，无需创建临时环境，也无需重新安装 Chrome：

```bash
bash TIMEPILOT.sh db-check
bash TIMEPILOT.sh source-check
PYTHONPATH=src:tests python -m pytest -q tests/test_featurelab27_candidates.py tests/test_ui_release.py
bash TIMEPILOT.sh
```

启动后在真实工业 CSV 中按第 2 节路径确认字段契约、产生候选、预览、保存、物化、BI 读取与刷新恢复。如果首次使用，schema 31 会在现有自动备份机制下从合法旧目录迁移；禁止清空历史目录。
