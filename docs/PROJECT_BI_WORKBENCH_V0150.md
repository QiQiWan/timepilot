# TimePilot v0.15.0｜统一项目 BI 与模型工作台

## 已交付的工作流

**项目 → 数据管理 → 上传 → 结构检查与人工审核 → 标准数据资产 → Perspective BI → 保存视图 → 预测草稿 → 训练数据 → 项目模型运行。**

1. 在项目的数据管理页查看已关联的资产版本。新上传的文件先进入持久化扫描队列，结构扫描完成后进入语义审核；用户确认字段和统计口径并生成标准版本后，系统将资产关联项目。点击“打开 BI”进入唯一的项目数据分析入口。旧 `/datasets`、`/imports` 和独立 DataAsset 工作页面在浏览器端重定向至 `/projects`；底层 API 保留兼容。
2. 项目 BI 使用 `GET /api/v1/ready` 检验正式 Perspective 环境，再建立 `/api/v1/native-bi/linked/{asset}/versions/{version}/ws` WebSocket。由 DuckDB 提供完整的不可变数据版本，不产生第二份原始数据副本。若缺少已编译 Perspective JS/WASM 或 Python 依赖，会显示无法连接并阻止保存不完整的 Perspective 布局。
3. 业务筛选条件与 Perspective 官方 `saveWorkspace` 布局存入**同一份**工作台视图记录；同时记录资产版本、源哈希、视图摘要及项目归属。打开历史视图时，按保存时的版本恢复筛选和 Perspective 配置，不随项目当前版本游移。项目版本切换不覆盖已有视图。每个项目只列出自己的视图；同一资产被其他项目引用时，不自动暴露这些视图。
4. 使用“创建预测草稿”将保存视图的**业务筛选范围**固化为预检查输入。系统核验序列连续性、最短历史、历史发布日期、目标值等，校验通过后才可生成训练数据集。业务筛选用于 BI 的数据范围和预测数据范围；Perspective 单张图内部的显示筛选、透视、聚合仅影响图表，不自动改变预测训练样本，需在上方业务筛选区明确设置。
5. 草稿生成训练数据后，建模页面按 `task_spec` 回填模型、跨度、回测窗口和历史长度，并禁改已审核的数据和模型配置。`POST /projects/{pid}/runs/submit` 在入队前核验项目、草稿、数据集、模型及回测参数；新运行记录关联 `project_id / asset_id / version_id / view_id / draft_id / dataset_id / run_id`。旧 `POST /projects/{pid}/runs` 保留为历史运行关联接口。
6. 需要编辑数据时，用户明确点击“建立可编辑工作副本”，转入既有原生 BI 的 Parquet 工作副本、版本编辑及撤销/回滚流程。此操作不会修改原始标准版本。可编辑副本的修订记录仍由原有 NativeBI 模块管理；当前版本不将编辑结果自动写回标准源，也不自动重算基于旧源保存的预测视图。

## 上传大工作簿的恢复机制

每个项目上传任务具有持久化 `job_id`，以该编号对应唯一导入记录。Worker 通过 SQLite `BEGIN IMMEDIATE` 原子领取任务，写入唯一 `lease_owner`、租约过期时间和心跳时间，扫描时由独立线程每约 10 秒（较短租约时自动缩短）续租。失去租约的工作者在下一阶段通知或提交前停止，不允许继续关联项目资产。失败任务最多进行三次扫描尝试；进程崩溃后待租约过期可重新认领，第三次过期会持久化为失败。

XLSX 结构识别使用逐工作表检查点。每张表扫描完毕，持久化表索引、表名称、候选字段结构及少量样本，文件通过临时文件 `fsync` 和原子替换写入任务私有目录。恢复前同时核验**源成员 SHA-256、解析器修订号、工作表位置/名称以及检查点内容摘要**；有一项不匹配就重新扫描该表。对 ZIP 中的 XLSX，校验源文件解压成员的哈希。上传开始前还会核验暂存文件的大小和 SHA-256。

`GET /projects/{pid}/imports/jobs/{job_id}` 显示 `heartbeat_at`、`phase`、`current_file`、`current_sheet`、`total_sheets`、`completed_sheets`、`reused_sheets` 和 `progress_percent`；`GET /.../{job_id}/checkpoints` 可列出已校验检查点的元信息，不返回工作表原始样本。任务成功后清除上传暂存文件与检查点，原始导入归档保留；失败任务保留暂存数据用于明确重试。

**恢复粒度为已完成的工作表。** 在同一张超大工作表的扫描中途退出，该表从头扫描。对 CSV、Parquet 仍为文件级重新扫描。该检查点属于结构识别阶段；后续转换、统计画像、模型训练和文件解压不承诺逐行断点恢复。检查点摘要用于检测意外损坏，不提供防止拥有本地文件读写权限的攻击者蓄意篡改的加密认证。

## 新增或收束的项目 API

| 方法 | 路径 | 用途 |
|---|---|---|
| `GET` / `POST` | `/api/v1/projects/{pid}/assets/{aid}/views` | 列表与保存项目视图（含 Perspective 布局） |
| `GET` | `/api/v1/projects/{pid}/assets/{aid}/views/{view_id}` | 打开历史版本固定的视图 |
| `POST` | `/api/v1/projects/{pid}/assets/{aid}/views/{view_id}/forecast-drafts` | 创建预测预检查草稿 |
| `GET` | `/api/v1/projects/{pid}/forecast-drafts` | 项目预测草稿清单 |
| `GET` | `/api/v1/projects/{pid}/forecast-drafts/{draft_id}` | 草稿详情与检查结果 |
| `POST` | `/api/v1/projects/{pid}/forecast-drafts/{draft_id}/materialize` | 将适用草稿变成正式训练数据集 |
| `POST` | `/api/v1/projects/{pid}/runs/submit` | 带来源关系校验的项目预测任务提交 |
| `GET` | `/api/v1/projects/{pid}/models` | 展示运行及 BI 来源版本、视图、草稿 |
| `GET` | `/api/v1/projects/{pid}/imports/jobs/{job_id}/checkpoints` | 逐工作表检查点与任务恢复审计 |

所有项目读取入口需要项目 ID 和关联对象；API 拒绝未归属该项目的保存视图与预测草稿。这里仍是**本地单用户研发环境**，尚未增加多用户认证与角色权限，不应直接开放公网。

## 数据迁移及上线条件

- 项目 SQLite 元数据结构由版本 `1` 增量升级至版本 `2`，新增项目视图、预测草稿和运行来源关系，不改动 SmartImport、DuckDB 或 Parquet 原始数据。异步上传任务新增阶段/心跳/逐表计数列，原有 `job_id` 和状态不变。
- 旧的工作台保存视图仅当源资产可唯一归属一个项目时才做一次关联回填；同一源资产分属于多个项目的旧视图不会被猜测分配，避免串项目。业务方可按需要重新保存。
- 本包为**完整源码**，未附带已编译的 Perspective JS/CSS/WASM 或 npm 依赖。要验证原生 BI，先在具备既有 Node/Python 依赖的构建机执行 `bash TIMEPILOT.sh build`；运行 `bash TIMEPILOT.sh check` 检查就绪状态。正常启动不新建 Python 环境，不下载 Chrome，不自动安装 npm。
- 线上升级前备份 `TIMEPILOT_HOME`，优先在副本上验证迁移。重大规模 Excel 的恢复需在目标机器进行进程强杀和磁盘不足测试；当前自动化验证包括受控异常、中断后的检查点复用、内容摘要损坏、租约续租、尝试耗尽、跨项目访问及预测任务来源一致性。

## 研发机上建议的验收步骤

1. 设置原有 `TIMEPILOT_HOME`，执行 `bash TIMEPILOT.sh check` 和 `bash TIMEPILOT.sh`。新版本 API 健康接口显示 `0.15.0`。
2. 新建一个项目，上传四张表的 XLSX；检查上传进度、心跳及逐表检查点。中途强制终止 Worker，重启并确认已完成表计入 `reused_sheets`。不在业务原件上试验破坏性写入。
3. 审核字段角色与统计口径，生成标准版本；打开项目 BI，查看完整网格、图表和时间/业务筛选。重新加载和返回后，打开保存视图确认原始版本、筛选与布局被恢复。
4. 将项目当前数据指针切到新版本，再打开旧保存视图，核对旧版本仍可独立读取。
5. 从保存视图创建预测草稿；检查时间连续性和可用时间，无阻断后生成训练数据；提交模型运行并在“模型管理”查看视图、草稿、资产版本的关系。
6. 将同一个数据资产关联到另一个项目，确认新项目无法看到前项目视图和草稿。最后检查正式 Chrome 中的图表交互、页面刷新、编辑副本回滚与 WebSocket 重连。

## 本轮验收边界

代码静态检查和 Python 接口回归可以在当前测试环境进行；**缺少 Perspective 真实编译资源时，不声称已完成浏览器级 Perspective 交互验证**。大文件规模、重启恢复时间、训练精度和不同操作系统发布包还需要结合实际硬件及真实数据测量。
