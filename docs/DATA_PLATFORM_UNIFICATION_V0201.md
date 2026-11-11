# TimePilot v0.20.1｜统一数据平台与自动启动构建：实施及验收记录

## 1. 本版交付范围

本版基于 v0.18.0 完整源码顺序推进 v0.19.0（统一资产/范围）、v0.19.1（质量治理/发布恢复）、v0.20.0（数据管理/BI 体验）及 v0.20.1（Agent 与运维）。基础研究、时序预测与贴片二分类插件保留兼容。

版本检查：Python 包、FastAPI、HTML 资源查询参数、前端 package.json、源码清单均为 `0.20.1`。源代码完整性由 `docs/RELEASE_MANIFEST.json` 签名；Perspective 编译资源使用独立 `perspective-build.json`，两者职责不同。

**术语与存储边界：** 一个项目下的逻辑数据资产 `asset_id` 对应来自一次导入和一个候选工作表的真实数据区域；每次发布产生内容版本 `version_id`。逻辑名称单独维护，修改名称不制造新的内容版本。物理上仍存在原始上传文件、不可变标准 CSV、用于 BI 的 Parquet 及版本化编辑增量。此版收束了目录与身份，保留必要的双格式物化；没有进行破坏性存储迁移，也没有声明物理去重已经完成。

## 2. 对照专项审阅问题的逐条验收

| 项目 | 调整 | 当前交付状态 | 验收口径 |
| --- | --- | --- | --- |
| F01 统一资产身份 | 新增 `unified_assets`、`unified_versions`，项目内稳定 `asset_id`、版本列表、字段字典、来源关系 | 已落地；旧表/物理副本仍兼容共存 | 一资产多版本；旧库数据可读取 |
| F02 改名生成重复版本 | 内容签名剔除显示名称，更新逻辑资产名称 | 已落地 | 同源同角色改名 `version_id`、内容 SHA 不变，BI 不重复注册 |
| F03 BI/模型选择不一致 | `DataSelection` 保存行级过滤、版本、源哈希、筛选哈希和完整 `row_count`；模型准备复核 | 已落地；目标机原生 DuckDB 仍需实机测 | 预览范围与冻结范围相同；局部图表筛选不自动训练 |
| F04 质量治理 | 必填、唯一键、数值范围、缺失率、时间间隔/顺序/重复规则；治理审计及人工决定 | 已落地 | 有问题的源行定位、违规总数、质量状态与处理原因 |
| F05 数据目录 | 项目数据页展示逻辑资产、版本、质量及发布任务；质量明细可查 | 已落地基础管理；高级大数据检索尚待强化 | 列表状态可见、跨项目不可读 |
| F06 发布恢复 | 后台发布队列、租约与独立心跳、2 万行 SHA 分块检查点、重试、部分完成状态 | 已落地 | 文件转换中断可继续；未完成资产不入目录 |
| F07 可靠备份/巡检 | 显式备份 ZIP、SQLite backup API、一致性检查、数据文件及 BI 校验 | 已落地基础能力；自动恢复和空间回收未实施 | 备份内所有文件 SHA256 核验通过 |
| F08 数据库升级 | 启动前 SQLite integrity_check、版本前快照、增量建表登记 | 已落地部分；尚缺全量迁移回退编排 | 原始 SQLite 不因预检查改写；重复执行复用快照 |
| F09 BI 视图使用体验 | 收束高级编辑功能、显示筛选口径、建模前冻结范围、保留原生图表 | 已落地基础体验；旧 BI 兼容接口仍在 | 用户能区分图表范围和训练行集 |
| F10 工业图表模板 | 根据实际时间/设备字段提供趋势、对比、明细预设 | 部分完成 | 可由字段结构推荐图表；维修事件覆盖图尚待做 |
| F11 企业安全 | 保留本地 127.0.0.1 服务和项目级逻辑校验 | **未完成企业统一认证、RBAC、真实租户隔离** | 不能直接公网部署，企业验收前必须补齐 |
| F12 Agent 治理工具 | 增加资产/版本/字段/质量/冻结选择/影响/完整性工具，白名单和项目限制 | 已落地 | Agent 不接收任意 SQL/文件路径，写操作需授权 |

## 3. 开发与部署：一条命令即可启动

要求：激活**已有** Python 3.12 环境及运行依赖；在 macOS 上若 Node 24 已安装于 nvm 但不在 PATH，启动入口会优先自动使用它。首次前端构建需要可访问 npm Registry，或已有本地依赖/已编译离线资源。

```bash
conda activate server
bash TIMEPILOT.sh
```

脚本执行次序：运行代码完整性检查 → 检查并备份已存在的 SQLite → doctor → 检查 Perspective JS/CSS/WASM 与编译来源指纹 → 必要时安装**前端** Node 依赖、编译到独立临时目录 → 验证编译导出 API、WASM 魔数、文件哈希 → 原子替换有效 BI 包 → 启动绑定 `127.0.0.1` 的服务。已编译且校验通过时不安装或构建。**不自动运行 pip、不创建 Conda 环境、不下载 Chrome。** 构建失败时保留旧资源并阻止服务发布损坏 BI。

```bash
bash TIMEPILOT.sh check
bash TIMEPILOT.sh source-check
bash TIMEPILOT.sh backup --output "$HOME/timepilot-runtime-backup.zip"
TIMEPILOT_OFFLINE=1 bash TIMEPILOT.sh
```

严格离线模式只可使用现成的 npm 依赖或已验签 JS/WASM 包；首次没有缓存时拒绝启动，日志说明缺失项。`build` 仍可用于制作附编译资源的发布压缩包，日常启动无需执行。**本源码发行包未附 Node `node_modules` 和编译 WASM；本次环境无法访问 npm Registry，故没有宣称真实 macOS 上的首次构建已完成。**

### 云同步目录风险

截图中 `TIMEPILOT_HOME` 位于 OneDrive 云同步项目。SQLite WAL、Parquet 的正在写入的文件不宜放在活跃云同步目录。推荐将运行目录迁到 Mac 本地非同步路径（例如 `~/Library/Application Support/TimePilot/runtime`），在迁移前停止服务、执行备份、校验历史数据后通过 `TIMEPILOT_HOME` 显式指定。应用启动时只警告，不擅自迁移或删除历史目录。

## 4. 数据结构与 API

核心对象：

- `unified_assets(asset_id, project_id, import_id, candidate_id, name, status, created_at, updated_at)`：逻辑资产，显示名称可修改。
- `unified_versions(asset_id, version_id, native_asset_id, data_sha256, row_count, column_count)`：不可变标准数据版本与 BI 投影引用。
- `unified_selections(selection_id, native_asset_id, version_id, source_sha256, selection_hash, row_count, document)`：已确认的、完整行级筛选结果定义；不使用前端分页行集。
- `unified_quality_policies`、`unified_quality_runs`、`unified_quality_issues`：规则、按数据版本进行的质量运行和人工处理记录。
- `unified_audit`：项目内治理动作及原因；研发本地日志，**不替代企业身份审计**。
- `project_publication_jobs`：后台阶段、尝试次数、租约、心跳与错误。
- `timepilot_data_schema_migrations`：本轮扩展表登记。

主要项目 API（以下 `{pid}`、`{aid}` 分别是项目及稳定逻辑资产编号）：

| 操作 | 接口 |
| --- | --- |
| 统一数据清单 | `GET /api/v1/projects/{pid}/data-catalog` |
| 规范版本及字段 | `GET /api/v1/projects/{pid}/catalog/assets/{aid}/versions`；`.../schema` |
| 版本差异 | `GET /api/v1/projects/{pid}/catalog/assets/{aid}/compare?left=...&right=...` |
| 修改显示名称 | `PATCH /api/v1/projects/{pid}/catalog/assets/{aid}` |
| 预览完整行数 | `POST /api/v1/projects/{pid}/selections/preview` |
| 确认并冻结行级选择 | `POST /api/v1/projects/{pid}/selections`，含 `confirm: true` |
| 查询冻结定义 | `GET /api/v1/projects/{pid}/selections/{selection_id}` |
| 修改质量规则 | `POST /api/v1/projects/{pid}/catalog/assets/{aid}/quality/policy` |
| 执行质量检查 | `POST /api/v1/projects/{pid}/catalog/assets/{aid}/quality/evaluate` |
| 质量结果/缺陷列表 | `GET /api/v1/projects/{pid}/catalog/assets/{aid}/quality`；`GET /api/v1/projects/{pid}/quality/issues` |
| 质量问题处理 | `POST /api/v1/projects/{pid}/quality/issues/{issue_id}/decision` |
| 数据血缘与完整性 | `GET /api/v1/projects/{pid}/catalog/assets/{aid}/impact`；`.../integrity` |
| 发布到正式 BI | `POST /api/v1/projects/{pid}/imports/{iid}/regions/{cid}/publish-jobs` |
| 发布进度/恢复 | `GET /api/v1/projects/{pid}/publish-jobs/{jid}`；`.../checkpoint`；`POST .../{jid}/retry` |
| 审计记录 | `GET /api/v1/projects/{pid}/governance/audit` |

Agent 新增面向项目的受控查询与质量/选择工具：`assets.catalog.list`、`assets.versions`、`assets.schema.inspect`、`quality.version.inspect`、`quality.issues.list`、`quality.version.evaluate`、`selection.preview`、`selection.freeze`、`assets.downstream`、`assets.storage.inspect`。执行引擎继续约束参数类型、项目归属及写操作审批；未对 Agent 开放原始 DuckDB SQL。

## 5. 从数据导入到模型训练的审计流程

1. 上传文件并完成 CSV/Excel 工作表解析与字段身份确认，原始文件保留。
2. 确认后向异步发布队列提交任务；同请求重复提交可查询同一个进行中的任务。
3. 每 20,000 行写入独立 CSV 分块和 SHA256；进程意外终止后核验源文件 SHA 和已保存分块，仅复用合法前缀；最后原子提交标准数据清单。
4. 后台将不可变 CSV 物化为 BI Parquet、关联同一逻辑资产；执行默认质量检查并在项目数据目录呈现状态。
5. 用户在正式 BI 使用全局服务端行级筛选。图表的局部透视、排序和抽样只影响可视展示，不能静默改变训练范围。
6. 先预览完整行数，再经用户确认创建 `selection_id`；其中包含数据版本、SHA256、过滤规则、时区和查询结果行数。
7. 由保存 BI 视图或冻结选择新建预测/分类草稿，模型准备阶段再核验冻结来源、筛选行数及最新质量状态；最终训练使用相同的服务端查询规则。非 base BI 编辑版本维持原有数据适用性限制。
8. 实验记录保留已有的训练数据 SHA、模型插件、评估协议和项目引用。质量人工“已知悉”会保留原始问题与审计原因，**不会修改/清除源数据中的缺陷**。

## 6. 数据治理约定

质量规则支持必填字段、联合唯一键、数值上下界、字段缺失率上限。需要时可开启按实体的时间质量检查：`time_field`、`entity_field`、`max_gap_seconds`、`require_monotonic`。按实际行顺序识别时间倒序，按设备+时间识别重复，同设备正向间隔超过阈值记为缺口；不能解析的时间值记录源行号。无明确日历含义的 A1 类字段不会自动视为时间戳。

当前检查会对已发布标准数据执行顺序扫描。批量修改后的 BI 增量版本需要新的规范数据发布及质量复验才可作为长期训练源。质量基准和阻断项可由用户核对，但所有问题的改变均需留有文本原因。

## 7. 发布验收与明确限制

自动化测试覆盖：同源改名复用、分块中断恢复、同步/异步发布、任务重试、时间质量规则、跨项目拒绝、数据选择冻结、原生 BI 选择契约、备份 ZIP SHA 和旧库检查。源码清单和压缩包按所有源文件 SHA256 核对。

**需目标机补做：** Python3.12 + Node24 首次实际 npm 安装及 Perspective JS/WASM 构建；真实 DuckDB/Parquet / Perspective 视图互动、复杂筛选到模型训练逐行一致性；大型真实 Excel 的高负载/断电恢复；Mac 本机重启后的预检备份与迁移；真实产线质量规则阈值评审。没有完成以上项目不得宣称已通过生产环境验收。

**未完成范围：** 企业登录/角色权限/多租户隔离；生产级 SSO 和访问审计；自动生命周期回收、物理文件全局去重；可视化跨视图维修事件覆盖；完整数据仓库多级迁移自动回退；未经业务标注的工业任务准确率与效益验证。这些工作建议在企业部署前独立收束，不应通过放开接口权限绕过。

## 8. 升级建议

先停用旧服务并备份原 `TIMEPILOT_HOME`。不要覆盖/删除现有 runtime。使用 v0.20.1 完整源码包，激活当前 Python 环境，设置正确 `TIMEPILOT_HOME`。执行 `bash TIMEPILOT.sh` 将自动检验源码与 SQLite、创建幂等版本前数据库快照、构建或验证 Perspective，然后启动服务。先检查历史项目及数据清单，再测试上传→发布→质量检查→冻结选择→任务运行。发生问题时停止服务，保留当前数据目录与快照；用旧版本源码及经人工校验的原数据库副本回滚。
