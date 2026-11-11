# TimePilot v0.16.0｜通用数据理解与项目资产契约

## 一、适用范围与本次调整

本版将 CSV、XLSX 多工作表和 Parquet 中的**实际源字段**作为唯一的列名来源。解析器不会生成 A1、A2 等占位业务名称；如果源表确实存在 A1，保留 A1 并说明业务含义待确认。用户提供的截图不能作为 `housing.csv` 源文件内容的校验依据，问题复现使用的是人工构造的 506 行同结构样例。

原来的转换链路继续服务月度销量和预测训练等特定时序任务。通用数据资产新增一条完整的业务链路：

**项目 → 上传与任务持久化 → 工作表/区域发现 → 全字段样本画像 → 人工核实表头与业务角色 → 按原列顺序发布不可变通用表 → 正式 Native Perspective BI → 项目资产目录和 Agent 只读发现。**

- 数值类型字段自动识别为“指标候选”，日期、时间、设备编号、事件、批次等字段识别为候选角色，保留推断依据和可信度。代码列的物理类型可推断；业务含义、单位与实际用途不自动臆测。旧的预测专用路径也不再仅凭 `A1` 等代码列名就把数值传感器误认为实体 ID；当样本提示疑似文本标识，或同一时间点的多个数值编码可能对应不同实体时，才要求补充实体映射。
- 支持 CSV 常见 UTF-8/UTF-8 BOM、UTF-16、GB18030 编码以及逗号、分号、Tab、竖线分隔。识别失败会报错，拒绝用替换字符损坏字段名。
- XLSX 支持逐工作表结构扫描和重启检查点；用户可以指定表头实际行号。Parquet 使用文件自身 Schema。
- UI 字段画像改为可搜索的字段表，展示全列名称、推断类型、候选角色、示例、完整率及角色确认控件。示例与样本画像不会伪装成全量统计。全量发布后为每列记录 `nonempty_count`、`missing_count`、`missing_fraction`。
- 数据发布保留源列和秒级时间，不强制每行转换为 `series_id, timestamp, target`。如果有任何数据行包含表头之外的非空列，发布失败并提示原始行号，防止静默丢列。

### 数据与版本结构

```text
TIMEPILOT_HOME/
  smart_imports/
    {import_uuid}/
      manifest.json          # 原始成员、来源 SHA256、候选区域及扫描记录
      source.csv | source.xlsx | source.parquet | workbooks/...
    _generic_versions/
      {import_uuid}/{candidate_id}/{version_id}/
        data.csv             # 全部源字段，UTF-8 CSV；不可变
        manifest.json        # 版本签名、源 hash、角色及全量完整率
  projects/catalog.sqlite3   # 项目、原始导入、通用资产及 Native BI ID 关联
  ...                       # 旧的预测资产、DuckDB/Parquet、模型任务仍保留
```

- `source_sha256` 记录源文件不可变校验；`data_sha256` 校验发布文件，`version_id` 由源哈希、工作表、表头行、字段顺序、确认角色、名称和解析器版本组成。输入一致时发布复用相同版本。
- 原始导入与通用表版本采用两阶段过程：先独立生成通用数据版本，再注册 Native BI 的数据资产 `native_asset_id`，最后把二者关系写入项目数据库。BI 注册失败可以原版本重试。
- 数据存储未进行全平台物理合库：保留既有预测专用结构和原生 BI 的资产体系，项目数据目录提供统一查询入口与统一数据清单页面；结构调整采用向前迁移，不覆盖老数据。
- 通用发布当前限制单区域最多 256 列、500 万行。超宽表和超额行数应显式拒绝，不得偷偷截断。通用 CSV 版本保留值的字符串表示，原 XLSX/Parquet 类型、空字符串与 null 的全部差异需要依赖不可变原件追溯；后续可演进为类型保真的 Parquet 规范化资产。

## 二、REST API

以下路径均位于 `/api/v1/projects/{pid}` 下；需要项目关联才能访问，项目不匹配返回 404。现阶段系统仅面向单用户本地开发，项目隔离不能替代多用户鉴权。

| 方法 | 路径 | 功能 |
|---|---|---|
| `GET` | `/data-catalog` | 统一列出预测专用资产和通用数据表；包含行数、列数、版本、候选指标数与来源 |
| `GET` | `/imports/{iid}/regions/{cid}/schema?header_row=N` | 读取源字段证据、样本画像、角色建议；本地用户界面可显示最多五行预览 |
| `GET` | `/imports/{iid}/regions/{cid}/versions` | 查看已有不可变通用版本 |
| `POST` | `/imports/{iid}/regions/{cid}/publish-general` | 显式人工确认、全字段发布及正式 BI 注册；可幂等重试 |
| `GET` | `/generic-assets` | 查看项目已经注册的通用数据资产 |
| `GET` | `/generic-assets/{native_asset_id}` | 读取项目拥有的完整通用资产字段与版本元数据 |
| `GET` | `/native-assets/{native_asset_id}` | 项目范围内读取通用数据及可编辑快照的 Native BI 元数据 |

`POST .../publish-general` 请求示例：

```json
{
  "name": "贴片工艺过程数据",
  "confirm": true,
  "header_row": 2,
  "roles": {
    "time": "采集时间",
    "device": "设备编号",
    "batch": "批次号",
    "event": "维护事件"
  }
}
```

角色均为**用户确认**，支持 `time`、`entity`、`target`、`available_at`、`device`、`batch`、`process`、`event`、`group`；其余所有列原样进入 BI，不要求用户逐列确认。成功返回 `status=ready`、`native_asset_id`、`meta`；当 Native BI 依赖缺失时为 `status=prepared` 并保留通用版本，补齐依赖后同请求重试。

## 三、Agent 接口及权限边界

工程内受控工具注册表新增/扩展：

| 工具 | 输入要点 | 权限与输出 |
|---|---|---|
| `project.assets.list` | `project_id` | `read`；统一发现预测资产和通用表，返回候选指标数及已确认角色 |
| `generic.schema.inspect` | `project_id, import_id, candidate_id, header_row?` | `read`；返回字段名称、物理类型、推断依据；**不向 Agent 返回源数据行** |
| `generic.version.inspect` | `project_id, import_id, candidate_id, version_id` | `read`；查看已发布版本、全量列完整率与来源哈希 |

`generic_understand` Agent 任务只调用白名单查询工具；不拥有未经审核自行发布、修改原数据、训练或上传到外部模型的权限。保存和发布通过用户显式确认接口完成。

## 四、典型使用方式

1. 在项目数据管理页上传 CSV / XLSX / ZIP / Parquet；后台扫描完成后自动进入数据理解页。
2. 系统列出每张工作表及候选区域。打开字段画像，逐列查看实际源字段、指标候选和时间、事件、设备/批次等角色建议。表头不正确时通过“表头所在行”重新识别。
3. 对有业务用途的字段确认角色；例如温度、压力等可以先不指定预测目标，发布后仍全部保留。没有模型 API 时同样可完成识别和发布。
4. 确认资产名称，点击“将全部字段发布到 BI”。系统校验源哈希、行宽、行数及数据版本哈希；完成后进入正式 BI。
5. 项目目录和受控 Agent 都可发现该资产及版本；完整数据查看、筛选、可视化通过 BI 进行。需要预测时再明确时间、实体、目标、可用时间和评估口径。

## 五、需要继续推进的边界

- 通用发布目前在一次 API 请求中执行，未实现逐块持久化发布检查点，超大文件的发布阶段若中断会重新执行；原来 XLSX **结构扫描阶段**逐工作表恢复能力继续保留。
- 工业多级合并表头、一个工作表中横向/纵向多张表、交叉统计表转长表、嵌套 JSON/半结构事件、时间戳时区统一与单位换算仍需独立的泛化规则和用户确认。
- 通用 Native BI 的视图保存在原生 BI 视图体系中；v0.15 的“项目视图→预测草稿→模型”完整链路针对预测专用标准资产。要让任意通用表也直接驱动预测草稿，仍需引入经确认的目标/时间列映射与全链路血缘校验。
- 本地可进行接口、模拟注册及数据完整性回归；真实 Perspective JS/WASM、DuckDB 数据导入、Chrome 可视化交互和生产规模中断恢复，需要在依赖齐全的目标环境单独验收。
- 当前没有企业级身份验证、细粒度 Agent 数据授权及审计链；外网/多用户使用前必须单独完成安全设计。
