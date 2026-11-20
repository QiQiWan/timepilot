## 0.39.5 (2026-11-20)

- 模型结果工作台贯通逐折数值、验证集回归残差、分类混淆矩阵、末折置换特征贡献、月度/设备验证切片、源 BI 导航、可核验的任务修订与 Markdown 报告。
- 聚合 BI 诊断仅显示 15 行以上切片；来源失效后清除指标和下载链接，运行失败显示有界错误码及原因。
- 真实同源、同折、单线程且预算上限统一的候选对照；保留原执行器证据不足的明确说明。
- 源码签名/版本/验收门禁更新，保持 Mac 原生、物理和离线发布阻断。

## 0.39.4 (2026-11-20)

- 按每个 X 独立发布时间对齐预测决策时点，训练 Y 按成熟时刻过滤；含时间偏移规范化、实际/声明延迟证据分级及 SHA256 账本。
- 增加滚动时间多折、跨月前向留出和跨设备 + 时间留出，对各折索引和边界做断言与哈希固化。

## 0.38.6 (2026-11-18)

- 原生 BI 资产写入前持久记录来源意图，在真实 Parquet 原子写入与 fsync 后再登记物理 SHA256、字节数和行数；元数据异常时保留已完成文件证据。
- 按项目/导入区域/版本清点未登记快照，未知、遗留或畸形标记继续阻断自动重试；已明确归属其他区域的孤儿文件独立计数。
- 提供只读本地审计和需要停机、有效 Worker 租约检查、实际 SHA256 与案例号的人工隔离归档，不删除原始快照。
- 增加真实 DuckDB/PyArrow Parquet 的写入前后子进程异常退出验收入口；现有源代码回归无法取代目标环境真实物理验证。
- 更新 v0.38.6 源码/物理/浏览器/离线发布门禁、Node 24 CI 和在线构建机 npm 锁引导流程。

## 0.38.5 (2026-11-18)

- 同步数据发布进入与异步/批量发布共用的持久 SQLite 任务；并发时返回任务回执，避免无登记的直接物理写入。
- 增加阶段回执 hash chain（SQLite 同事务写前、写后事件），保存来源版本、CSV SHA256、原生资产 ID、处理结果，不保存源数据行。
- 检查任务领取时的来源版本与提交时指纹一致，禁止用队列中的旧任务发布更改后的数据。
- 过期租约和不确定原生 BI 写入保持待人工对账，并将审批结果加入阶段回执。
- 数据管理页新增按需查看阶段记录的入口，附带项目隔离权限和不可修改审计测试。
- 发布正式门禁增加 v0.38.5 源码回归；真实物理数据库、浏览器和完整离线构建仍需目标环境证据。

## 0.37.9 (2026-11-17)

- 灾备：增加安全恢复到全新目录、哈希验证、SQLite integrity_check、路径安全与覆盖保护。
- 交付：新增无网络依赖安装的 Dockerfile.offline；明确 source/native-only/complete-offline 语义；可验证离线 wheelhouse，修正旧打包脚本版本；默认启动不再隐式 npm bootstrap。
- 前端：筛选刷新使旧图表请求失去并发配额，新统计立即发起，晚到值不得覆盖。
- 质量：恢复异常/交付阻断/并发竞态测试，独立 source/offline/physical/browser 门禁。

## v0.37.8 (2026-11-17)

- 统一数据一览 `DashboardMetrics` 数值/范围契约，补上缺失/非有限/转换失败计数与分母，前端不再误用不存在的统计字段。
- 源文件 SHA256 成功校验使用 30 秒进程内缓存，inode/ctime/mtime/长度/预期 SHA 变化后立即重新检查。
- 图表请求输出可脱敏的性能摘要，新增只允许租户管理员访问的 `/api/v1/system/logs/summary`。
- `/ready` 增加数据库、磁盘和 Worker 检查，拒绝把 API-only 模式推断为完整业务已就绪。
- 增加 12 行和 506 行真实 Parquet 物理验收样例、完整原生 BI Docker 多阶段构建和发布阻断器；目前缺 npm 锁和离线 JS/WASM，保持候选状态。

### v0.37.7 Dashboard, query correctness and diagnostics (2026-11-16)

- v0.37.5: canonical safe category SQL shared by legacy GET and current POST; chart capabilities; per-card request tokens, two-request limit, stale response rejection; accurate chart-incident counts.
- v0.37.6: task-specific overview/quality/statistics DOM and lazy feature UI; actual Tukey whiskers/outliers; invalid numeric counters; pairwise Spearman with budget and schema-role filtering.
- v0.37.7: version/source/filter-scoped bounded TTL summary cache; diagnostic ZIP; fail-closed physical Parquet SQL parity and performance gates in CI. Browser physical verification remains external to this source package.

### v0.37.4 Mac startup hotfix (2026-11-16)

- Detect incomplete Rollup/Esbuild cross-platform optional npm lock entries.
- With explicit `--bootstrap-lock`, generate in an isolated empty directory,
  verify the candidate using real `npm ci`, and atomically publish it after
  backing up an existing local lock outside the source tree.
- Do not change a signed package-lock or any existing verified Perspective
  JS/WASM assets when a registry install fails.
- Distinguish npm EUSAGE lock errors from network and permission failures;
  allow one safe recovery attempt, without unbounded retries.
- Startup preflight treats unsigned generated lock as a repairable build input;
  hidden `.github/workflows/ci.yml` still requires signed source repair.

## v0.37.3 — 证据驱动自然语言、特征对照与报告（2026-11-16）

- 新增只读统一证据引擎：数据版本、筛选范围、SHA256、数值路径、单位、证据 ID 和限制说明；无证据或数值冲突时不生成分析结论。
- 数据一览和特征图表统一展示服务器的数值证据；删除前端自行推断最大波动等重复启发式解释；无模型 API 时正常使用。
- 对于已确认标签和同源、同折指标，输出全部特征与筛选特征相对原始字段的逐折改善及负收益；明确不能据此声称最终测试或生产收益。
- 新增数据一览 Markdown 证据报告，浏览器 WebCrypto SHA256 校验；正式特征分析报告新增证据经过校验的三方案评价折数值。
- 固定合成样本、前端渲染与篡改阻断、统计不一致及无标签反例回归；真实 DuckDB/Parquet 验收独立失败即阻断。

## v0.37.2 — 数据一览、跨字段分析与版本联动

- 新增参数化 DuckDB 多维数据概览：分位数、缺失率、Pearson 相关性、直方图、箱线图、类别统计、散点与确认时间趋势。
- 新增工作台仪表盘、可配置图表、最多 5 项数据筛选、刷新状态恢复及版本切换来源防串线。
- 新增固定合成 CSV 与真实 DuckDB/Parquet 独立 SQL 对账，缺少物理依赖即阻断正式验收；前端 Node 回归覆盖图表配置与筛选同步。
- 保留原有单字段分析、特征工程、正式 BI 及模型评价，报告不虚构算法收益。

## v0.37.1 — Perspective 生命周期、正式特征 BI 与 WebSocket 修复

- 统一原生 Perspective 的 ESM 身份及 SHA256 变更检测，防止重复定义 `regular-layout`。
- 增加仅本地可用的 WebSocket 阶段/关闭码追踪；修复 API 前缀重复、正式特征的固定产物链接和两张原生表绑定。
- 改进基本 BI 字段类型、负值汇总、计数回退，集成同页 30 次 Chrome 往返的独立发布门禁。
- 浏览器实测与离线回归分开记录，缺依赖时不宣称验收通过。

# v0.37.0 — 工业真实数据与性能验证（2026-11-16）

- v0.37.0 最终收束：修复工业评分并列预测分数导致的 AP 顺序敏感问题；新增来自官方 NAB 九个原始文件的独立来源/统计/分折性能基准与 SHA256 关联；增加回顾性跨设备试验时间先后说明和性能 RSS/模型耗时硬门禁；Python 全量回归、工业测试与真实物理门禁分开留证。

- 新增 NAB 官方 8 个 EC2 实例真实时序及标签文件的固定 Git blob 校验与下载工具；MetroPT-3 单设备空压机适配、企业时点明确的字段契约。
- 固定设备内过去窗口、标签收到时点、训练/验证/测试分离与未见设备外推基线；公开事后标注只运行无监督训练，阻断错误的在线收益宣称。
- 新增真实 DuckDB、PyArrow/Pandas Parquet 复核和 1/5/20/100 万行可重复 wall-clock / RSS 基准；CI 可在有网络与依赖环境执行真实数据门禁。
- 默认继续保留 v0.36.5 浏览器、BI 和项目工作流；详细边界见 `docs/FEATURELAB_V0370_INDUSTRIAL_ACCEPTANCE.md`。

## v0.36.5 — Chrome 交互、任务刷新恢复与真实数据闭环验收

- 新增 `scripts/featurelab365_chrome_journey.py`：固定 QA 数据、真实 CSV 上传、任务跨刷新恢复、Parquet / CSV 独立数值核对、自动画像和特征确认、原生 BI WebSocket、不可变报告下载校验、失败状态记录。严格禁止模拟浏览器和跳过物理门禁。
- 修复导入发布页刷新后后台任务轮询停止；保留错误信息于上传表单；自动画像不再受特征预览状态抑制；完善缺失资产告警与可恢复入口。
- 新增前端与 Python 专项回归，CI 以真实 Chrome 用户旅程结果为发布阻断条件；无法运行时明确记为 blocked。
- 源码包包含验收脚本，不预装 Chrome，不声称尚未运行的 Perspective/Parquet 浏览器门禁已通过。

## v0.36.4 - 2026-11-16

- Add real frozen-source DuckDB/Arrow Parquet/chart/rolling-feature/model evaluator. Missing physical packages are BLOCKED with machine-readable reason; no production claim.
- Replace multi-layer nested analysis navigation with a single five-step task switcher and a compact extra-tools menu, preserve current routes.
- Add plain-language evidence-led chart templates and a local MathJax operator help library, maintaining optional technical provenance.
- Rename feature delivery section and clarify manual review, materialization, BI use and report saving.
- Add bounded, rotating, redacted JSONL request/worker logs, with admin-only enterprise read, UI filters and exports.

## v0.36.3 - 2026-11-16

- Align current-version regression tests with the supported adaptive planner and generator versions while retaining historical source behaviors.
- Fix hidden-file restore parity between authoritative CI workflow and visible bootstrap recovery copy, regenerate SHA256 source manifest.
- Fix release metadata, README and CI artifact names; add an online CI lock-bootstrap path and verified npm ci build.
- This source-only archive does not include precompiled Perspective or a committed npm lock. Online CI must generate and publish the reviewed lock/build artifact; until then native BI remains blocked.

## v0.36.2 - 2026-11-14

- Frozen, independent synthetic QA goldens: 8 fixed CSV SHA256, exact quality/error counts, six-device regime transitions, late maintenance, complete time buckets, and supervised model metrics.
- Add real HTTP/SQLite import-publication-workspace state tests for unlabeled and static data, source invalidation, cross-project isolation and refresh/read idempotence. Physical NativeBI remains a separate gate.
- Add regression tests for actual training-fold evaluations on three frozen datasets. Reject restricted fields entering supervised raw features or output lists.
- Add one-command QA audit with pass/fail/blocked classification, separate physical gate, no installs, and no business CSV in log archives. CI executes the same frozen smoke gate on each push.
- A complete production acceptance still requires real DuckDB/Parquet, native Perspective and independent business data.

## v0.36.1 — 数据驱动功能验收（2026-11-14）

- 添加 8 类固定合成 QA CSV、SHA256 和可核验的预期异常；单独区分功能与生产验收。
- 增加统一数据/离线/物理端到端验收入口，复用已有 PIT、模型、Worker、BI 和报告测试。
- 增加按需获取官方 UCI 数据与来源/许可证据，不自动下载或绕过限制。

## v0.36.0 · 2026-11-14

- 将实物 DuckDB/Parquet、时间图表聚合、独立真实 CSV PIT 物化、同折模型评价、Mac 现有 Chrome/Perspective、Worker OS 进程崩溃恢复及 SQLite 向前迁移组织为明确的发布门禁。
- 新增 `featurelab360_gate.py`：生产结论要求全部门禁 PASS；空测试、skipped、跨来源 SHA256、不同物化产物或未观察到真实 WebSocket 均不能被认定为通过。
- 新增 1 万 / 10 万 / 100 万行临时 Parquet 物理压力测试（分桶数量、均值、极值与独立 SQL 比对），记录耗时、RSS 和文件 SHA256；合成压力数据不作为生产模型性能证据。
- 新增真实 OS 子进程终止且等待租约自然过期的恢复测试、跨版本 SQLite 迁移测试，以及本地 UI 静态资源版本校验。
- 发布版本、前端资源缓存版本和完整源码 SHA256 清单同步。无数据库 schema 修改、无新依赖或安装程序；完整验收需在具有 DuckDB/PyArrow、已安装 Chrome、正式 Perspective 资源及**用户提供的真实数据**环境中完成。

## v0.35.6 · 2026-11-13

- 统一分析工作台保留同源数据版本，直接切换手工特征构造、安全公式、高级 tsfresh/Featuretools/PIT、原生 Perspective BI 和不可变报告归档；无需往返旧版多级页面。
- 新增同源报告与特征集工作区，支持只读预览、浏览器 SHA256 下载核验、不可变归档、历史版本及差异，并直达所选特征集。
- BI 使用已有原生 Perspective，复核来源、契约、特征集和 Parquet 哈希后才开放正式特征对照与视图保存；候选预览不充当生产结果。
- 修复统一工作台重复点击监听、版本切换误选数据、长请求回写旧页面以及旧特征集入口不定位的问题。
- 新增 API、前端、安全及离线验收测试；Mac DuckDB/PyArrow/真实 Perspective 端到端需在目标机器执行。

## v0.35.5 · 2026-11-13

- 统一分析工作台新增证据驱动的自动研判区域，分别展示数据质量、候选特征质量、统计变化和模型价值；每项观察关联真实计算结果的证据路径与来源 SHA256。
- 新增只读 `GET /workspace/insights`，核对项目、数据版本、契约及源文件；模型对照仅接受正式物化后通过持久化作业、数据库报告和产物 SHA256 联合核验的监督结果。
- 原始字段、全部候选、筛选后特征展示同模型同折评价，主要指标为回归 MAE 或分类 AP；辅助指标按真实记录显示；折间改善与报表内部一致性被验证，不虚构性能收益。
- 无标签、标签未确认、来源变更、报告不完整等状态不会显示预测收益；统计分布不能推断因果。报告 Markdown 复用同一套证据政策。
- 统一工作台加载新组件，支持来源上下文切换、刷新及只读状态轮询；保留高级手动入口、原有任务流程与审批门禁。
- 新增单元、API、前端与 Chromium DOM 检查。真实 DuckDB/PyArrow 和 Mac Perspective 仍属于实机验收条件。

## v0.35.3—v0.35.4 · 2026-11-13

- v0.35.3：数据分析与特征计算的图表补充真实统计证据解释、坐标语义和时间刻度。不同物理量纲不再共用纵轴；技术 SHA 和配方收进详情。修复时间图错误读取桶内数量的问题。
- v0.35.3：废除重复“继续”与跨页面导航，遗留特征分析路由转入 `/analysis?section=features`，保留资产/版本并支持刷新和浏览器后退。
- v0.35.4：新增项目统一分析工作台（概览、质量、图表、自动特征、成果）；数据发布后以发布者权限排队执行低成本画像和 16 项规则候选预览，自动任务不运行模型训练或正式发布。
- v0.35.4：API 提供同源分析/候选状态和幂等启动；未配置模型 API 也能完成基础工作流，字段和时间安全边界继续保留。
- 现场验收门禁：真实 DuckDB/PyArrow 与 Mac Perspective 浏览器实际数值一致性；当前容器不可替代现场验证。

## v0.34.1—v0.34.3 · 2026-11-13

- v0.34.1：自动分析默认转为自适应多类型候选策略；算法族及字段交错分配，来源统计只用无标签证据，不安全窗口禁用。
- v0.34.2：特征工作台按算子族分组，显示真实质量、图表、策略与方法禁用原因；候选正文点击查看、复选框独立勾选。
- v0.34.3：已有 tsfresh/Featuretools 受控表单与已算高级候选纳入统一工作台；仅提供只读元数据，保留 PIT 回接、人工审核和模型评价隔离。
- 新增接口与前后端合约测试；DuckDB/Parquet、高级依赖和 macOS Perspective 真实端到端仍需现场验收。详见 `FEATURELAB_V0341_V0343_IMPLEMENTATION_REVIEW.md`。

## v0.33.3 - 2026-11-13

- Durable server-owned FeatureLab session continuation; browser progress polling is now read-only.
- Explicit failed-job retry with revision and owner permission checks; background editor rights checked on every continuation.
- Four bounded LLM-mode regression paths: enabled, disabled, timeout, unauthorized tool requests.
- Source/version/contract staleness blocks continuation and current delivery; Markdown reports label old sources.
- Artifact byte SHA256, FeatureSet and evaluation identity, client BI receipt and report UTF-8 hash are cross-checked.
- No new schema or dependencies. Mac DuckDB/Perspective real rendering and provider API remain pending site acceptance.

# v0.32.4 · 2026-11-12

- 增加旧窗口配方只读风险审计与发布阻断；手工窗口显式绑定已确认排序和设备键。
- 智能分析返回持久状态推导的下一步操作。
- 增加独立 CSV 字节与原生 Parquet、模型评价、浏览器身份的同源发行门禁。
- 生产数据与 DuckDB/Perspective 真实运行未取得证据时维持阻断。

# v0.32.3｜P0/P1：时序拓扑安全与自动分析体验闭环（2026-11-12）

- 自动时序窗口只从已确认真实事件时间或数值 `order_index` 推导排序键，多设备按已确认实体键分组。全量检查空值、设备内重复排序值和来源乱序；证据不足时仅生成逐行特征。
- 编译阶段再次拒绝空键或重复键，防止前端/Agent 跳过数据契约门禁；旧版手工按行号计算的历史配方仍可被读取，但不得作为生产时间证据。
- 修复“预测不良率”与“分类特征分布”等任务误判；支持 20 万—200 万行受控后台全量 SQL，并清晰提示 200 万以上需分区策略。
- 高级 tsfresh/Featuretools 仅按数据拓扑推荐且需人工确认，未绑定多表来源时不自动执行。模型提供有界字段与模板建议，内部分级字段名在调用外部模型时脱敏；禁止自动执行写入操作。
- “确认采用并计算”合并草稿保存及正式物化排队，需人工勾选；请求重试幂等。监督反馈保留训练折证据，不改写已物化资产；UI 仅在同源 SHA256 核验后展示反馈。
- 新增 P0/P1 接口/数值/前端回归和源码门禁；DuckDB、PyArrow、真实工业数据和 macOS 原生 Perspective 完整验收仍为生产发布阻断条件。

# v0.32.2｜监督评价自动衔接、同源报告与文档收束（2026-11-12）

- v0.32.1 Agent 的规划/策略/候选筛选/证据解释继续使用固定服务端工具序列、严格白名单和无模型回退，并持久化模型参与决策的审计状态。
- 对监督分析增加**独立人工确认**：标签、预测决策时点、标签可用时间必须已确认；过程数据的可用时间不能冒充标签可用时间。尚不具备契约时保持探索模式。
- 正式物化后核验真实文件、任务/数据库与产物 SHA256，再自动提交相应的监督或探索评价；评价报告必须绑定相同产物、数据来源、配方和契约修订。
- BI 打开请求核对资产/版本/FeatureSet/产物 ID，双面板客户端回执与同源产物 SHA256 绑定；独立浏览器运行尚未验收时继续标记未通过生产门禁。
- 统一报告包含数据、Agent、候选、模型对照（若有）及 BI 同源证据，报告 SHA256 直接按 UTF-8 Markdown 原始字节计算；支持机器读取同源交付 JSON。
- 修正监督字段下拉框传参错误、监督已评价仍显示“探索评价”的矛盾提示；继续沿用 Schema 35，无需重建数据库。
- 重整当前 README、API、ACCEPTANCE、FILE_TREE，新增用户指南及文档审阅；历史说明保存为追溯资料，不再作为当前功能定义。
- 生产级限制：本环境无企业生产原始数据、真实模型 API、完整 DuckDB/Arrow 与 Perspective 浏览器证据，源码检查不能被解释为真实业务效果验收。

# v0.32.1 | Evidence-bounded FeatureLab Agent (2026-11-12)

- LLM advice is constrained to objective, allowlisted numeric fields/template, quality-eligible candidate ordering and server-authored evidence IDs; malformed/overprivileged proposals fall back to deterministic rules.
- Project/actor-scoped analysis session persists a fixed server-executed tool plan and compact per-step receipts with source SHA and contract revision. No model-authored Python, SQL, writes, feature publishing or evaluation claims are executed.
- Automatic post-preview candidate adjustment and evidence-only explanation; both are shown by default and included in the export report, with meaningful missing-model and blocked-proposal states.
- User-confirmed saving/materialization remains separate, recorded as human actions. Added read-only agent evidence endpoint and source QA acceptance entry.
- No destructive DB migration; schema 35 unchanged. Real model provider, DuckDB, native Perspective UI/WS and enterprise production data remain release gates.

# v0.32.0 | Intelligent Feature Analysis Workspace

- Single goal-driven feature analysis page; removed duplicate feature headings and reduced foreground navigation.
- Added source-pinned, resumable automatic profiling, bounded candidate generation, full-scope quality evidence, selection, draft/materialization and exploratory evaluation.
- Added conservative numerical Time/sequence inference with explicit field-role confirmation, without fake calendar-time conversion.
- Added optional bounded LLM goal understanding, field-template strategy and evidence narration; all numerical results computed by existing engine and local fallback retained.
- Normal source-mode start attempts safe native BI auto-build only on missing/stale resources. Current ZIP still requires Node 24 and real npm/Python dependencies for production build.
- Upgraded catalog additively to schema 35; retained all legacy feature interfaces.

### v0.31.0 Mac/source-only 安装修复（2026-11-12）

- 新增 `source-repair`，通过发布清单校验的可见隐藏配置副本修复 `.env.example` 与 CI 哈希问题；任何改动均先保留原始字节及哈希，`--dry-run` 可预览。
- 新增 `bi-setup --bootstrap-lock`，仅在明确请求时使用现有 Node 24/联网 npm 完成锁文件生成、Vite 编译和 Perspective JS/WASM 校验；启动仍保持不自动下载。
- 规范源码版无 BI 构建时的错误提示。项目数据库迁移机制不修改、不重置。

## v0.30.1—v0.31.0 — 2026-11-12

- v0.30.1：统一版本与历史测试；构建期固定 npm 锁文件，启动仅检查原生 BI 就绪；无浏览器回执时正式验收阻断。
- v0.30.2：tsfresh 最少观测数/时间跨度/有效覆盖；Featuretools 历史时间及 JOIN 预算；进程 RSS 内存与任务并发限制。
- v0.30.3：高级候选经审批与逐实例 PIT 严格匹配合并，绑定来源哈希、预测截止时点，生成新的 Parquet 与 BI 资产；模型评价读取同一产物。
- v0.31.0：项目五步状态恢复、特征高级回接 UI、真实产线同源验收命令、严格 Perspective 浏览器回执、Agent 人工业务复核与全套发布门禁；Schema 33→34 加性迁移。
- **当前尚未实现完整生产验收**：本环境无真实企业数据、tsfresh/Featuretools 真包、DuckDB/PyArrow 及 Perspective JS/WASM；缺少审核的 npm lock，真正离线发行阻断。详见 `FEATURELAB_V031_RELEASE_AND_REVIEW.md`。

## v0.30.0 — 2026-11-12

- 增加可选 tsfresh、Featuretools、多表 PIT 及逐实例业务可用时间过滤。
- 增加高级特征持久任务、spawn 子进程硬超时、行数/关联/算子预算、原生 BI 待审核候选、项目归属与来源哈希核验。
- 高级发现前端：已确认字段按角色选择、预执行核验、人工确认、断点恢复与 BI 入口。
- 新增同一外部 CSV → 项目入库 → PIT 物化 → 内置评价器三折对照与设备/工况切片 → BI DuckDB 双视图验收脚本；实际依赖/真实生产数据不足时明确阻断。
- Schema 32→33 加性迁移。详细见 `FEATURELAB_V030_RELEASE_AND_REVIEW.md`。

## v0.29.0 (2026-11-12) — FeatureLab 训练折筛选、模型对照、稳定性与人工审阅

- 新增训练折内质量/冗余/相关性筛选；线性模型及随机森林的三组同折对照、AP/召回/误剔/MAE、逐折稳定性与 PSI。
- 受预算控制的内层时间搜索：绝对值/平方/对数/开方/单位匹配的差值比率。默认关闭；分组时间任务内层独立组策略未完成时拒绝搜索。
- 通过单独保存的筛选决策生成新的未批准 FeatureSet 草稿，保留来源与评价指纹及审核修订；schema 31→32 加性升级。
- 新增真实 CSV 数值复现命令、模拟数据 fixture、前端评价面板和源码一键回归入口。
- 数据限制：当前无可信真实工厂数据可供产线验收；DuckDB/PyArrow/Perspective 本环境未集成通过，数值模拟不能充作生产 PIT/BI 证据。

## v0.28.0 (2026-11-12) — FeatureLab 高级算子、自定义公式与可视化

- 追加 SQL 可复现的滚动/数值高级特征；自动候选模板新增 `advanced`，持续遵守数值语义和来源快照门禁。
- 自定义 `expression.safe` 算子支持至多 8 个数值输入、受控函数和四则运算，禁止任意代码/SQL。
- 候选数据预览增加分布图、散点图、全计算视图统计与扫描范围提示；新增最近预览、最新已验证物化产物查询。
- 新增隔离目录端到端验收程序和 `accept feature-lab-v028` 命令；真实文件验收须设置 `TIMEPILOT_FEATURELAB_QA_CSV`。
- 按模块 Python 与 Node 合约测试、Chromium 模拟数据 UI 测试已运行；真实 DuckDB/Arrow 和 Perspective JS/WASM 全链路因当前环境依赖缺失未验收通过。
- 本次不增加数据库 Schema 版本（仍为 31），原 FeatureSet / BI / 数据目录保持兼容。

## v0.27.0 (2026-11-12) — FeatureLab 半自动工作台（含 v0.26.2 交互修复）

- 基于注册表定义的动态表单，过滤不适用字段及参数；已确认布尔事件/时间契约后开放维护事件特征。
- 新增按物理类型、字段名和人工契约的谨慎语义提示，数字型 Time/ID 默认不进入自动数值计算。
- 新增四种可复现模板、最多60项候选、批量勾选、无模型候选生成与自动名称。
- 新增候选预览后台任务、样本变化图、质量提示；预览不生成已发布产物。
- 项目目录向 schema 31 加性迁移；存储服务端会话并绑定源 SHA 与契约修订；恢复最近的候选选择及布局。
- 保留 FeatureSpecV2/现有审阅发布/BI；修复初始 BI 空白画布占用页面问题。
- 详细实现与测试限制见 `FEATURELAB_V027_RELEASE_AND_REVIEW.md`。

## v0.14.0 (2026-11-10) — 项目异步解析与语义画像

- 21 年 ZIP 上传/解析分离，加入 SQLite 持久化任务、跨项目隔离、取消/重试/断点对账。
- 为候选工作表加入限定扫描且可追踪的字段画像；明确完整/截断数据统计。
- LLM 字段角色建议限制为实际列，保留人工确认；项目中增加历史分析计划恢复。
- `/ready` 门禁检查真实 Python Virtual Server 与 JS/WASM 资源，不提供虚假的 BI 就绪状态。
- 根目录继续只有 `TIMEPILOT.sh`、`TIMEPILOT.bat`；新增项目异步验收测试。

## v0.13.0｜项目管理、智能导入流程重整与 BI 启动门禁

- 新增项目 CRUD、归档恢复、旧数据零拷贝关联和项目间资源范围校验。
- 增加项目数据/模型/任务页面及轻量智能导入会话；表格实际数据集中到正式 BI 页面。
- 增加 Excel 合并表头、公式、隐藏工作表与重复表头结构诊断；抽样数据画像与全量指标分开标记。
- 增加 Perspective 启动就绪门禁，仅在离线 node_modules 已安装时自动重建，绝不自动安装依赖或创建 Python 环境。
- 原生 Chrome 编辑、回滚、重连尚待目标机实测；源码包不含可伪称的 JS/WASM 编译产物。

## v0.12.0｜真实业务 A/B 验收规范化、实验完整性与统一根入口

新增人工金标 CSV 批量导出与审查；固定种子配对 A/B、冻结标注 SHA；模型比较逐滚动窗口诊断、机理证据绑定及独立报告哈希。根目录仅保留 TIMEPILOT.sh / .bat 两个执行脚本，原故障排查和验收包装脚本收束到 scripts/launcher.py 子命令。

## v0.12.0｜真实数据语义评估与受控自主建模

- `src/timepilot/semantic_benchmark.py`：人工真值、规则/模型对照、A/B 实际复核记录及成本统计。
- `src/timepilot/auto_experiment.py`：已确认版本下的多基线与滞后特征滚动模型对比。
- `src/timepilot/mechanisms/identification.py`：状态控制机理辨识和事件边界检查。
- `src/timepilot/research_api.py`、`static/research-ui.js`：受控实验和 UI。
- `tests/test_research_stage4_v0110.py`：人工标注、调用权限、回测和机理证据回归。
- `skills/{semantic-evaluation,autonomous-modeling,mechanism-learning}/`：新增领域技能契约。

# TimePilot 更新记录

## 0.10.0 — 统一 Skills、持久化任务执行与单入口

- 建立统一受控工具注册表和五组本地 Skills：数据理解、质量诊断、BI 分析、特征工程与预测草稿；所有工具使用严格 Pydantic 参数验证和读写权限分类。
- 增加 SQLite WAL 持久化任务与步骤日志、SHA-256 审计、原子租约、只读步骤断点恢复、写入状态不确定时人工核对，支持幂等创建、审批、取消和人工重试。
- 添加证据驱动的受限动态工具选择；模型仅能从服务端白名单选择，模型异常或未配置时回退确定性流程，模型调用计入预算。
- 特征预览与用户批准的导出按序列/发布时间构建滞后特征，保留数据来源版本，超过上限或来源被修改会拒绝执行；预测草稿需单独确认且不会自动训练。
- 新增 Agent 任务中心，支持任务进度、预算、审批、历史记录及刷新后自动继续轮询。
- 统一 Mac/Linux `TIMEPILOT.sh` 和 Windows `TIMEPILOT.bat` 启动、源码检查、测试、专项验收和打包入口；默认启动使用现有 Python 环境，不执行 pip/npm 安装或创建虚拟环境。
- 专项测试覆盖模型降级、审批阻断、多进程租约、预算、断点恢复与前端刷新后的状态续查；原生 Perspective 实机浏览器验收仍为独立条件。

## 0.9.0 — P0 数据正确性与统一 BI 数据资产

- 原始 `available_at` 保留与时间校验；实体角色必须确认，数值型业务编码不再自动合并；按稳定实体 ID 识别跨来源重合与冲突。
- 预测预检禁止未确认的发布时间、日期错误、混合来源/统计口径、重复月份以及存在质量缺陷的序列静默进入训练。
- 统一 DataAsset ID、版本哈希、语义版本、BI 查询、交叉透视、视图以及预测草稿；智能导入生成的数据不再需要额外复制到基础分析工作台。
- Perspective 使用单会话有序 WebSocket 请求、断开处理与换版前传输释放；新增与基础 BI 一致的只读来源及口径筛选。
- 源码签名、全量回归、年度数据压力验证；真实 Chrome Perspective Rust 编辑/回滚的实机闭环仍须完成。

## 0.8.0 — 数据理解 Agent 与受控工具编排

- 独立的数据理解服务和工具白名单：file.inspect、layout.detect、semantics.map、data.clean_preview、quality.evaluate、series.preview；series.build 需要明确确认。
- 导入页面集成结构识别、语义映射、质量、待确认事项、数据版本构建进度与 BI 下一步入口。
- 可选模型建议：字段元数据限定外发，模型响应严格校验；默认无模型运行；不允许模型自行写入、删改或启动训练。
- 强制确认统计口径、来源网址与代码字段；汇总表和车型明细同批混选直接阻断；完整保留数据来源和冲突标记。
- 方案/执行哈希、源文件校验、后台任务幂等及中断重试；固定版本和原始单元格溯源。
- 2006—2026 年真实年度文件验证跨年转换；独立的 Mac 本机 Chrome 页面验收入口。
- 不改变启动环境政策；完整源码包与未构建 Perspective 资源的边界明确标注。

## 0.7.0 — BI 与预测任务统一

- 新增五维级联筛选、月份范围及具体系列多选，保留来源与统计口径，避免重复合计。
- 新增有效数值覆盖率、记录月份覆盖率、月度趋势、质量摘要及原始工作表/单元格追溯。
- 新增不可变保存视图与预测任务草稿，预检历史长度、时间缺口、冲突、缺失、统计口径及发布时间。
- 生成标准训练数据集必须人工确认，数据集包含版本 SHA-256 与筛选视图 lineage，不会自动启动训练。
- 大数据首次创建 SQLite 只读分析索引，查询无需 npm/Perspective；新增浏览器回归入口。
- 已用 2006—2026 年真实 MarkLines 年度文件验收结构识别、跨年转换与大规模 BI 筛选。
- v0.6.1 的基础 BI 继续可用；Perspective 原生 JS/WASM 需在正式发布阶段单独构建和验收。

# TimePilot 更新记录

## 0.5.13 (2026-11-09)

- 交付完整文件体系，补齐隐藏配置文件、源码校验、全量覆盖安装与项目目录说明。
- Perspective WebSocket 改为逐条顺序处理，先等待 SQL 线程执行结束再关闭 Rust Session，修复旧 Session 清理时序风险。
- 远程数据工作区切换前先关闭旧 WebSocket，之后销毁旧 Viewer；减少已删除视图继续收到请求的概率。
- 编辑提交刷新不再重复挂载相同版本，增加浏览器与服务端清理顺序回归测试。
- 保留 v0.5.10 和 v0.5.11 之前的完整代码功能，禁止删除原始运行数据。

## 0.5.11 (2026-11-09)

- 统一完整源码清单、发布包和原子升级修复机制；拒绝不完整补丁。
- 启动、测试、构建前检查缺失 SDK、版本混用、源码哈希和导入路径。
- npm 构建增加已安装顶层依赖版本核查，避免复用不一致的 node_modules。
- SQLite 启动阶段检查数据库现有列，避免旧表结构引发随机 API 报错。
- 增加缺损恢复、恶意 ZIP、源码缺失、旧库结构的专项回归测试。
- 保留 Perspective 表注册和 __tp_rowid 的 v0.5.9/v0.5.10 修复。

## 0.5.10 (2026-11-09)

- 修复 Perspective 5.6.1 对 DuckDB VIEW 使用默认 `ORDER BY rowid` 造成的 BinderException。
- 为版本化 Parquet VIEW 配置上游支持的 `row_id_expr="__tp_rowid"`，保持惰性查询与原始数据不可变。
- 服务端预校验行键存在；新增无依赖工厂回归测试，真实 Python Client 继续执行 view 与 to_json 检查。
- 不引入新 npm 依赖，不下载浏览器；缓存前端资源可复用。

## 0.5.9 (2026-11-09)
- 修复 Perspective 5.6.1 DuckDB Virtual Server 使用数据库限定名称（如 `memory.timepilot_active`）而前端用未限定名称打开，导致真实 Chrome 验收出现 `Unknown table`。
- 前端使用服务端表目录发现与严格匹配，兼容实时流的无前缀表名；连接失败时清理 viewer 和 WebSocket。
- 后端增设 DuckDB 视图注册自检、线程安全发送调度和正常 WebSocket 断开处理。
- 真实 Chrome 验收增加 Python Perspective Client 协议级预检及前后端表名一致性检查。
- 新增 6 个 JavaScript 与 4 个 Python 定向测试，统一 v0.5.9 的缓存与发布版本。
- 交付源码包无截图、浏览器或构建资源；目标 macOS 的完整浏览器验收仍待执行。

## 0.5.8 (2026-11-09)
- 修复 Vite 应用构建剥离 ESM 入口导出导致 `mountPerspectiveRemote is not a function`。
- 构建期真实 Rollup 导出清单检查；Python 发布期合同/SHA256 校验；Chrome 导入期 API 检查。
- 入口 URL 使用 JS SHA256 防缓存；CI 跨任务同步 npm lockfile；统一版本号。
- 删除冗余历史 HOTFIX 文档和过期审阅，保留回归测试；不打包截图、数据和浏览器。

## 0.5.7 — 原生 BI 页面状态与 Playwright 假就绪修复

- Playwright 改用同步就绪判断，杜绝 Promise 最终返回 `false` 后 Python `.get()` 崩溃；单独轮询原生工作区及透视/绘图目标。
- 后端 Virtual Server 缺失时提供显式能力错误；页面不再停留在“准备原生分析组件”。
- 验收新增能力检查、HTTP 4xx/5xx 真实 URL、页面状态诊断；favicon 不再制造无关 404。
- 沿用现有 Python 3.12.12 和本机 Google Chrome；不重新下载浏览器，不产生截图；此次不修改 Perspective npm 源码，不要求重新构建 WASM。

## 0.5.6 — Perspective 5.x 数据表加载与浏览器验收修复

- 改为 Client/具名 Table 初始化 Perspective 5.6.1，并在所有预设透视、图表配置中显式绑定数据表名称。
- 数据表加载、WASM 和 WebSocket 异常直接在原生 BI 页面输出，验收遇到错误会提前终止并给出错误信息。
- 建立独立的前端视图协议测试；构建清单记录源码 SHA256，源文件更新后避免误用旧 JS/CSS/WASM。
- 继续沿用 Python 3.12.12、已安装 Chrome 和单个 requirements.txt；交付包不含截图与历史 HOTFIX 脚本。

## 0.5.5 — 原生 BI 可观测验收与工程收敛

- Chrome 浏览器端到端验收增加分阶段进度、阶段耗时、浏览器错误和 WebSocket 事件记录。
- 验收进程总时限、页面和 WASM/WS 请求单独限时，失败自动保留 `reports/native_bi_acceptance.json`。
- Perspective 前端异步加载增加限时诊断；修正 DuckDB Arrow 已弃用方法。
- 成功构建的 Perspective 资源直接复用；无须重复安装 npm 包或下载浏览器。
- 清理历史截图脚本、过时验收和旧 HOTFIX 文件；交付包不含截图。

## 0.5.4 — 原生 BI / 实时流

Perspective Python Server 实时增量更新、DuckDB Virtual Server、版本化编辑及受控原生视图恢复。保留本地模式约束。

### v0.30.0 发布审阅补丁
- 高级计算 Worker 改为调用局部项目 ID / 资源预算，防止并发串用。
- PIT 时间解析按每个数据来源已确认 IANA 时区归一到 UTC，拒绝歧义时间。
- 增强模型对照的实际物化产物哈希一致性检查。
