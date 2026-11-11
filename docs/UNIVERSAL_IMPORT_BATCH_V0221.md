# TimePilot v0.22.0—v0.22.1：通用表格扫描与多表批量发布

## 1. 交付边界

**v0.22.0**：结构扫描器自动发现 XLSX 各工作表的独立矩形数据区域，包括上下和左右分隔的数据表；CSV/TSV 的空白分隔表块及原样重复表头可分别识别；Parquet 使用原生 schema。导入首页不要求目标、实体、指标名称及每列业务角色。扫描来源、工作表、表头单元格与字段别名可复核。提供 8 种项目作用域的只读 `import.*` 工具和可选模型辅助解释。

**v0.22.1**：批量发布任务将选中的每个 Region 独立写入不可变 CSV 版本，继承每 2 万行分块校验和后台租约/心跳/重试机制，注册正式 BI 资产并保存项目关联。新建仅追加的 SQLite 目录索引，旧项目、旧版 API 和既有预测模型来源继续可用。首次访问旧导入目录时可从原始 manifest 重建索引。

**不在这两个版本内**：BI 自然语言筛选/绘图工具库（v0.23）、工业图层和 JOIN（v0.23.1）、生产级真实浏览器与千万行验收（v0.24）。无已编译 Perspective/未安装 DuckDB 时，新版本仍不能将准备完成的资产误标为 BI 已注册。

## 2. 数据流

```text
项目 → 上传文件 → 入队扫描 → [File / Worksheet / TableRegion]
          ↘ 按工作表缓存结构扫描结果 (源哈希 + 解析器版本)
               ↓
           源字段 / 源单元格 / 内部别名 / 行列位置 / 样本与待确认区域
               ↓（可选）
       受控 ImportUnderstandingAgent：只读结构工具 → 有依据的说明
               ↓
       选择所有或部分 Region → 单次批次提交
               ↓
       每 Region 持久化子任务：写分块 → 生成不可变版本 → 注册 BI → 质量检查
               ↓
       项目数据目录 → 数据表 → 正式 Perspective BI
```

原始源字段叫 `A1` 就保留 `A1`；不得自动猜测字段含义。内部技术别名只用于唯一查询列名，例如两个同名源字段分别成为 `X`、`X__2`，源字段数组和原始单元格坐标仍分别保存。CSV 里的 `00123` 将按源文本保留，不强制转换成整数。Excel 公式作为文本保留，不执行公式或凭空补充缓存值。

## 3. 数据结构

新增 `table_region_scanner.py`，候选区域包含：

- `region_id / candidate_id`：由源文件编号、Sheet、坐标与扫描版本确定；
- `bbox`：`row_start,row_end,col_start,col_end` 一基原始坐标；
- `header_row/header_rows/data_start_row`；
- `source_fields[]`：`ordinal,source_name,source_cell,internal_alias,missing_header,duplicate_source_name`；
- `sample_rows[]`：仅有界、标注为样本，完整行数在发布时统计；
- `needs_review`、`unassigned_regions[]`：显示注释、无法认定的区域及结构不确定性。

区域修订使用 `PATCH .../regions/{rid}/layout` 创建新 `region_id`，旧版本和源文件保持可追溯；原区域被标记 superseded，不能再发起新的批次发布。旧引用不改写。

项目 SQLite 新增 `import_directory_sources`, `import_directory_sheets`, `import_directory_regions`, `import_directory_fields`, `import_region_directory_migrations`, `import_publication_batches`, `import_publication_batch_items`, `import_publication_migrations`。均为 `CREATE TABLE IF NOT EXISTS` 的新增对象；不覆盖历史文件或强制迁移历史模型。

## 4. 核心 API

| 方法 | 路径（均以 `/api/v1/projects/{pid}` 为前缀） | 行为 |
|---|---|---|
| `POST` | `/imports/jobs` | 上传并加入后台扫描任务 |
| `GET` | `/imports/jobs/{jid}` | 查询扫描阶段、工作表检查点与心跳 |
| `GET` | `/imports/{iid}/catalog` | 返回文件/工作表/区域目录，并同步项目源字段索引 |
| `GET` | `/imports/{iid}/regions/{rid}/preview` | 有界预览，显示源行号、真实字段 |
| `PATCH` | `/imports/{iid}/regions/{rid}/layout` | 显式确认后建立新的布局修订 |
| `GET` | `/imports/{iid}/tools` | 受授权限制的只读 import 工具目录 |
| `POST` | `/imports/{iid}/semantic-explain` | 本地结构说明，可选 LLM 通过工具核查 |
| `POST` | `/imports/{iid}/publish-batch` | 批量创建子任务；需要 `confirm=true` |
| `GET` | `/publish-batches/{batch_id}` | 所有子任务状态、注册 BI 资产及失败原因 |
| `POST` | `/publish-batches/{batch_id}/retry` | 只重试失败或待注册子任务 |

调用示例：

```json
{"region_ids":["region_hash_a","region_hash_b","region_hash_c"],"confirm":true}
```

Region ID 需取自实际目录返回。请求按排序后的区域集合签名，重复提交复用相同批次；每个区域单独生成稳定版本，不自动合并/关联多表。批次身份先持久化；子任务注册期间异常退出后，重交相同区域集合会补全缺失子任务。没有跨文件系统、SQLite 和 BI 的原子事务：**部分失败可见、已完成资产保留、只恢复失败项**。将其称为“全有或全无发布”是不准确的。

## 5. LLM 与隐私

无模型时，扫描、预览、结构解释、资产发布全部可用。

模型启用后，服务端先自行执行 `import.layout.scan`、`import.table.relations`；模型可请求少量 `import.schema.profile` 或 `import.header.compare`，服务端固定范围再授权执行。第二轮模型收到限定列名、类型和坐标的证据，生成**不具有修改权限**的结构解释。默认 `schema_only`，不上传表格样本行、文件路径、原始文件哈希或跨项目信息。模型申请其他工具、越权资源或发布动作一律拒绝。单元格文字是外部非可信数据。

## 6. 启动与升级

原有 `TIMEPILOT.sh` 自动 BI 检查和构建行为保留，不需要先执行 `nvm use 24` 或 `build`（前提是本机已安装 Node 24，可访问依赖缓存或 npm Registry）。继续使用 Python 3.12.12 和既有 `requirements.txt`，不新建虚拟环境。

升级建议：

1. 停止正在运行的旧服务，先备份 `TIMEPILOT_HOME`（尤其是 SQLite、原件及 Parquet）；
2. 将 v0.22.1 源码解压到独立目录，保留旧 `TIMEPILOT_HOME`；先用备份副本试运行；
3. 执行 `bash TIMEPILOT.sh source-check`、`bash TIMEPILOT.sh`；
4. 使用 CSV、多个 Sheet、单 Sheet 多表块分别核查目录、表头、完整行数、源坐标和 BI 数据；
5. 确认后再决定是否使用原运行目录；迁移索引是追加式，旧导入项目保持可读。

## 7. 验收矩阵和已知限制

| 用例 | 应验收行为 |
|---|---|
| XLSX 单 Sheet 左右并排与下方独立表 | 拆成 3 个区域，bbox/字段名与实际单元格一致 |
| 两个 Sheet、包括空工作表 | 文件目录展示完整 Sheet 清单，无虚构数据表 |
| CSV 空白/重复字段名、前导零 | 技术别名唯一、源字段名保留、原始值不丢失 |
| CSV 精确重复表头 | 单文件分段发现两个可独立发布的区域 |
| 含公式和注释的 XLSX | 公式文本不执行，注释出现在待归属区域 |
| 100,000 行文件 | 全部行范围被识别，无只读取前 32 行的截断 |
| 批量发布中途失败 | 完成项保留；重复提交幂等；失败项单独恢复 |
| 目录跨项目读取 | 项目隔离，无法访问未授权导入/批次 |
| 无模型 / 恶意工具请求 | 仍可导入；不执行模型未经允许的工具 |
| 重启读取旧 manifest | 重新建立结构索引，不改写旧版本/来源 |

当前结构发现适用于有空白分隔行列、明确重复表头的常见矩形表。完全无分隔、层级多表头、装饰性单元格桥接多个区域、公式缓存和合并单元格表头仍可能需要人工核实。单个区域最多 256 列、500 万行，且总候选最多 500 个；超限时明确拒绝、绝不静默截断。对复杂 Excel 的所有单元格自动 100% 归类仍需更完整的标注评测。

真实 DuckDB/Perspective 浏览器验收依赖部署环境原生资源；使用 API mock 的集成测试不代表已经在真实 Chrome 中验证了 BI 图表。正式部署前继续运行目标机原生 BI 验收脚本。

## 8. 真实数据引擎专项验收

在目标 Mac/Windows 的 Python 3.12.12 + DuckDB + PyArrow 环境执行：

```bash
bash TIMEPILOT.sh accept universal-import
```

该命令先运行通用导入及发布兼容测试，再在**独立临时运行目录**中使用真实 DuckDB/Arrow 完成 3 子表发布、Parquet SHA-256 检查、实际 BI 查询、Arrow 回传及重复提交幂等核验。另构造 3101 行 CSV，确认第 3101 行首次出现的 `00123` 不会因前 3000 行类型样本为数字而被错误数值化。报告写入 `reports/universal_import_v0221_real.json`。

缺少 DuckDB/PyArrow 时报告 `blocked` 并返回非零退出码；不会将 API 模拟测试计作真实 BI 验收通过。真实 Perspective UI、JS/WASM/Chrome 仍需独立执行 `bash TIMEPILOT.sh accept native`。
