# TimePilot v0.9.0 | P0 数据正确性与统一 BI 资产

## 交付边界

本版本增加统一的 `DataAssetCatalog` 和只读 BI 工作台，继续使用 v0.8.0 的智能导入、数据理解 Agent、预测草稿、原生 BI。**完整源码包包含基础 BI、智能导入和统一资产后端；原生 Perspective JS/WASM 编译资源尚未随源码归档交付**。目标 Mac 必须具备正确的编译资源、DuckDB 和 Perspective 5.6.1，才能验证真实 WebSocket、编辑、回滚和重连。

## P0：不允许静默错误生成训练数据

- `available_at`：源文件存在对应日期列时读取并校验，保留真实值；带时区的日期时间归一化为 UTC；无时区的日期时间标记 `publication_timezone_unverified`，进入预测预检阻断项。错误时间、发布时间早于观测日期，直接终止转换且不生成已完成版本。
- `series_id`：对于普通时序数据，存在 `A2`、`SKU`、设备编号等不明业务编码字段时必须显式映射；数值型编码也不能因其被发现为数量候选而跳过确认。
- `entity_id`：在已确认的业务维度和序列编码上形成源无关的实体身份；`series_id` 仍为来源内的时序 ID。跨来源重叠检查按实体身份 + 观测时间 + 统计口径进行，不会把不同实体同月记录误判为冲突。
- 预测草稿检查完整性：缺失值、无效发布时间、未确认时区、重复月份、混合来源与口径、历史长度不足都应报告，生成训练数据时再次核验。确认不足时保留草稿而拒绝训练集生成。
- BI 筛选：统一 `ViewSelection`；图表、明细、质量预览传递同一来源及统计口径条件。

## 统一数据资产

标准化转换结果生成确定性资产编号 `si_<import_uuid>`，版本 ID 沿用智能导入版本编号。`DataAssetCatalog` 以只读引用访问 `normalized.csv`；SQLite 分析索引是可重建的查询缓存；原始 CSV 不复制。现有 DataAsset ID、语义快照、视图均绑定源哈希。

可调用的 API：

- `GET /api/v1/data-assets` 资产目录。
- `GET /api/v1/data-assets/{asset_id}` 元数据与版本。
- `GET /api/v1/data-assets/{asset_id}/versions/{version_id}` 版本、字段、来源哈希。
- `GET|POST /api/v1/data-assets/{asset_id}/versions/{version_id}/semantics` 查看或登记经过人工确认的字段语义版本。
- `POST .../analyze` 多维筛选与质量统计。
- `POST .../grid` 分页明细。
- `POST .../group` 分组统计；跨多个口径或来源求和自动拒绝。
- `POST .../pivot` 共享筛选上下文的双维交叉透视；数值聚合继续执行相同口径隔离。
- `GET|POST .../views` 保存与检索不可变筛选视图，与时序工作台共享视图 ID 和哈希。
- `GET .../views/{view_id}` 获取已保存的筛选条件。
- `POST .../views/{view_id}/forecast-drafts` 生成需要用户确认的预测草稿，不自动训练。
- `WS /api/v1/native-bi/linked/{asset_id}/versions/{version_id}/ws` 从同一 CSV 延迟读取的原生 Perspective **只读**入口，需要目标机的可选依赖。

浏览器页面：`/data-assets`、`/data-assets/si_.../versions/<version_id>` 以及后缀 `/native`。基础网格及趋势无需 npm。数据资产页面支持保存与加载视图，并通过相同的 `view_id` 跳转 `/imports/.../workbench?view_id=` 进行预测草稿准备。

## Perspective 生命周期修复与剩余条件

`_OrderedPerspectiveHandler` 规定每个 WebSocket Session 单线程顺序处理 SQL 请求，等待请求执行完再关闭 Session；适配 Starlette 正常断开异常。前端修订数据版本时先关闭旧 WebSocket，随后移除旧 View DOM，避免向已删除 View 继续请求 Schema。专用验收脚本 `TIMEPILOT.sh accept native` 检查网格、透视、图表、保存视图、刷新后 WebSocket 重连、编辑、版本保存、回滚与历史版本保留。

**重要：** 以上属于源码修改与单元/API 回归。当前执行容器没有 Perspective、DuckDB、PyArrow 的完整依赖和可连本地服务的真实 Chrome，因此暂未取得 Mac 运行时真实端到端通过记录。不得把 Rust v5.6.1 的 `View not found` panic 标为已完全消除。若 Mac 仍遇到上游 Rust 原生崩溃，保留基础 BI 降级模式，需独立评估 Perspective 升级或隔离进程。

## 执行方法（使用当前 Python）

```bash
conda activate server
cd /path/to/TimePilot_v0.9.0
python scripts/verify_installation.py --source-only
bash TIMEPILOT.sh accept unified
bash TIMEPILOT.sh
```

原生 Perspective 浏览器完整验收（先在构建机完成 JS/WASM 资源构建和部署）：

```bash
bash TIMEPILOT.sh accept native 2>&1 | tee acceptance_v090.log
```

不创建任何新的 Python/Conda 环境，不自动 pip/npm install，不下载 Chrome。历史版本的运行数据须通过 `TIMEPILOT_HOME` 保持一致，覆盖源码前做好备份。

## 通用 BI 原生关联（新增）

原 v0.4 版 `/bi/table/{table_id}` 工作台已能按相同筛选条件直接引用 DataAsset，不需要用户重新上传或复制原始标准 CSV。选择统一资产中的“通用 BI（引用）”后，系统登记一个确定性的、只读来源引用，复用原有字段画像、网格、聚合、透视和受控数据变换。

- `POST /api/v1/data-assets/{asset_id}/versions/{version_id}/legacy-bi-link` 传入 `{"selection":{"filters":{"source_name":["A"],"statistical_scope":["销量"]}}`，返回通用 BI `table_id`。相同的资产版本和条件重复执行返回同一表 ID。
- 通用 BI 单次数据处理最多 250,000 行；超出阈值返回明确的 422，**不会抽样冒充全量**。大数据可以先用基础 BI / 原生 BI 筛选，再创建通用 BI 引用。
- 通用 BI 在读取时逐次检查源文件 SHA256，并且按白名单筛选原始文件；不保存第二份原始 CSV。使用“变换”创建派生结果时，派生数据才成为新快照，原版永久保留。
- 多统计口径或多来源时，通用 BI 引用上的数值聚合和透视直接拒绝；计数、字段质量画像仍可使用。
- 通用 BI 引用不可直接调用 `/to-timeseries` 绕开预检。训练数据只能经过时序工作台检查真实发布时间、系列身份及数据质量后生成。

局限：这是面向现有 Pandas 工作台的**有界惰性关联**；一次查询仍可能扫描完整来源 CSV，海量数据筛选建议使用 SQLite 索引或 DuckDB 原生查询。生产版需要再增加查询结果缓存和执行资源隔离。

## 统一筛选状态跨页面

基础工作台、原生 BI 和通用 BI 在切换时均使用同一 `ViewSelection`。未保存的筛选条件会作为 URL 查询参数传给原生 BI，刷新后可以恢复；永久视图通过带哈希的 `view_id` 恢复，预测工作台可沿用同一 ID 创建待确认草稿。服务端仍负责对过滤列及值进行白名单校验。
