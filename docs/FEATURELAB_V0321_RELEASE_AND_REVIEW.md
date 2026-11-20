# TimePilot v0.32.1 迭代与质量审阅

## 1. 改造定位

针对 v0.32.0 的自动分析任务，增加有界模型规划、工具执行回执、实测后的候选调整与默认呈现的可验证解释。原有数据导入、项目隔离、DuckDB/Parquet、FeatureSet 审批与 Perspective 的架构和存储不变。数据库 Schema 保持 35，不进行新的破坏性迁移。

### 1.1 用户操作路径

1. 进入项目 → 特征工程 → 选择已发布数据并输入目标。
2. 未配置模型时自动运行规则计划；配置模型后，在允许共享字段元数据的条件下尝试模型目标推荐及字段/模板建议。
3. 服务端执行固定 DAG：检查源契约 → 全量画像 → 受限候选 → 真实计算预览 → 质量筛选 → 证据解释。每一步记录状态及结果关联，不向模型开放写入工具。
4. 模型可从**通过全量质量门槛**的候选中重排或减少选择；不能把排除项、标签、缺失质量证据的候选重新引入。
5. 结果页自动显示解释摘要、逐条证据、候选来源和工具回执；用户复核后选择保存草稿、发起物化。发布仍遵从原有人工审批。

### 1.2 模型调用与回退

| 环节 | 模型可建议 | 后端硬边界 | 无模型/异常回退 |
|---|---|---|---|
| 任务规划 | `explore / anomaly / quality / classification / regression / forecast` | 显式 task_hint 不得覆盖；仅目标枚举 | `classify_goal` |
| 字段与模板 | 数值允许字段 ID、已注册模板 | 字段契约、角色、缺失/变异质量、最大候选预算 | 基于画像的字段排序与固定模板 |
| 实测后再筛选 | 已被判定 `recommended` 的候选 ID | 不得提升 `review/exclude/unverified`；至多 8 项 | 原推荐集合 |
| 结果解释 | 选择服务端生成的 claim IDs | 模型不直接提供数值、预测收益或因果结论 | 已验证结论模板 |

最多四次受限建议阶段（不等于四次成功调用），不保存原始模型提示、密钥或数据行。任何字段级 `classification=restricted` 都关闭该自动任务的对外模型增强。即使在模型的全局设置中选择了较宽的数据共享，FeatureLab Agent 仍只发送字段元信息及聚合统计；用户输入的目标文字也会发送，涉及敏感业务内容时应关闭外部模型。

**与原有“模型生成自然语言解释”相比**，v0.32.1 默认使用证据 ID 选择机制。原文由服务端依据计算结果生成，模型只选择展示哪几条，避免对计算结果的无依据改写。模型输出任意工具、Python/SQL、未授权字段或额外 JSON 键时拒绝并回退。

## 2. 源码级改造

| 文件 | 改造 |
|---|---|
| `src/timepilot/feature_auto_agent.py`（新增） | 固定工具白名单、模型可用性检查、候选 ID 限制、证据 claimbook、模型建议验证与回退、执行回执 |
| `src/timepilot/feature_auto_analysis.py` | 任务规划审计、头像/候选/预览/质量/解释阶段回执、候选再排序、解释缓存、人工确认记录、报告出处 |
| `src/timepilot/feature_lab_v2_api.py` | 项目/操作者范围内的 Agent 回执只读 API |
| `src/timepilot/static/feature-auto-ui.js` | 自动说明、受限模型状态、候选建议理由、工具回执、证据下钻 |
| `src/timepilot/static/feature-lab2.css` | 可折叠的轻量执行审计与结果卡片 |
| `tests/test_featurelab321_agent_orchestration.py` | 正常模型响应、未授权工具、非法候选、虚假指标、受限字段、超时、明示目标、权限隔离 |
| `frontend/native-bi/tests/featurelab321_agent_ui.test.mjs` | 模型/规则两模式的 UI 呈现、转义防注入 |
| `scripts/launcher.py` | `accept feature-lab-v0321 --mode offline` 独立验收入口 |

HTTP 新接口：`GET /api/v1/projects/{pid}/feature-lab/analysis/sessions/{sid}/agent`。现有分析会话创建、推进和审批 API 继续兼容。`state_json` 包含 `agent.plan/trace/candidate_adjustment` 和 `explanation`，既有会话可读；它们没有新的破坏性数据库字段。

## 3. 防越权与数据边界

- 服务器允许的自动工具仅有：`feature.contract.inspect`、`feature.profile.compute`、`feature.candidate.propose`、`feature.candidate.preview`、`feature.quality.screen`、`feature.report.explain`。
- `feature.set.draft_create`、`feature.run.create`、`feature.publish` 和 `feature.contract.patch` 均需合法用户触发；模型不能直接调用这些工具，所有候选输出均必须通过字段/来源/预算校验。
- 模型生成的字段 ID、候选 ID、JSON 顶层键严格校验，异常时回退系统推荐，留下 `blocked` 状态，不执行模型任意工具请求。
- 数据完整性：源 `sha256`、`contract_revision` 贯穿同一会话；原有乐观锁、幂等 request ID、项目/用户隔离和失效检查继续生效。
- 证据解释完全由服务端生成，包含 `predictive_value_evaluated=False` 的限制，自动筛选结果不能宣称模型预测性能提升。

## 4. 验收与未闭合条件

推荐执行：

```bash
bash TIMEPILOT.sh db-check
bash TIMEPILOT.sh source-check
bash TIMEPILOT.sh accept feature-lab-v0321 --mode offline
bash TIMEPILOT.sh
```

源码验收可在测试用例中使用受控模型替身模拟可用、超时、越权、无效推荐。该方式验证系统如何响应模型输出，**没有验证真实模型 API 费用、响应质量或第三方服务可用性**。

正式企业上线仍需：Python 3.12.12 和 Node 24 Mac 实机运行、真实 DuckDB+Parquet 数值计算、Perspective JS/WASM 浏览器回执、企业数据与标签 PIT 核验、人工审批链、实际模型 API 配置及用户体验指标。未取得这些证据时，不能标记为生产完整验收。

## 5. 后续改进重点

- 如果业务确认不同工况的实际时间，可启用真实时间窗口及 PIT 监督评价；数值型 Time/序号仍不得默认为日历时间。
- 在 v0.32.2 将自动分析结果与监督评价及 BI 报告联动，保留同一产物哈希与可信数据切分的结果；基于真实车间质量标签测量模型收益。
- 提供模型调用费用/延时统计、任务取消与超时限额，在正式外部 API 配置后补齐现场验收记录。

## 6. 本次已执行的审阅（2026-11-12）

- 对上一正式源包 v0.32.0 进行增量审阅；本版修改项目版本、FeatureLab 服务/API、自动工作台 UI、回归测试与发布清单，新增受控 Agent 模块。
- 全量 Python 回归：首轮 `531 passed, 14 skipped, 0 failed`，运行时 Python 3.13.5；随后修复规则解释覆盖范围和模拟 UI 上的一处尾部字符，已对改动区域重新运行专项 Python 测试 `15 passed`。
- 全部 Node 前端契约测试：最终 `44 passed, 0 failed`，运行于当前环境的 Node 22；目标 Node 24 和 Python 3.12.12 的兼容性仍需在部署机补验。
- `bash TIMEPILOT.sh accept feature-lab-v0321 --mode offline` 已通过源码级入口；新 API 的项目隔离、模型越权回退及无模型运行均有测试。
- Chromium 静态模拟页面完成视觉审阅：检查 Agent 记录、候选调整与证据卡片并修复了一个渲染尾部残留字符。截图为模拟状态，不属于实际业务数据或 Perspective 原生验收。
- 发布 Manifest 将在最终文件冻结后重签；对整个源码 ZIP 的 ZIP CRC、逐文件 SHA256 进行独立校验。

**不能宣称已通过的事项：**真实 OpenAI-compatible 模型供应商请求；外部 API 的费用与延时；企业真实产线测量、标签和 PIT 物化；真实 Perspective JS/WASM 浏览器运行；Mac Python 3.12.12 + Node 24 一键启动。这些项目在当前运行环境未完成，须与 Source QA 分开报告。
