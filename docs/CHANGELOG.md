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
