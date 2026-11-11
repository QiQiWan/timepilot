# 时骁数据工作台 v0.5.14–v0.6.1：功能与工程验收

## v0.5.14 原生 BI 稳定化

1. 前端 source、npm 锁文件、JS/CSS/WASM 在构建机统一打包，所有源文件和二进制资源使用 SHA-256 记录。`scripts/BUILD_OFFLINE_RELEASE.sh` 只供发布机使用。普通 `TIMEPILOT.sh` 不装包、不建环境、不运行 npm。`scripts/package_timepilot.py --with-native-bundle` 拒绝无锁文件、资源缺失、旧资源或构建不匹配的发布。
2. `src/timepilot/native_bi_api.py` 捕获 Starlette 新旧 WebSocket 断开异常，串行处理每个 Perspective Session 的请求，等待原生 SQL 队列清空再执行 Session.close。`frontend/native-bi/src/session-lifecycle.mjs` 关闭远程 WebSocket 前撤销旧 viewer 的 DOM 引用，避免失效 Rust view 被继续请求；不在远程视图上调用可能发送晚到消息的 `viewer.delete()`。
3. `src/timepilot/static/native-bi.js` 在 Perspective 缺失时使用基础网格与 DuckDB 聚合。智能导入页面的基础 BI 基于 CSV 流式聚合，可独立于 Perspective、DuckDB 构建包运行。
4. `scripts/accept_native_bi_live.py` 覆盖网格、透视、图表、保存/恢复、刷新重连、编辑提交、数据快照哈希、回滚和实时流。须在有 DuckDB/Perspective、真实打包 JS/WASM 及已安装 Chrome 的机器上执行。

**交付边界**：当前执行环境 npm Registry 无法连接且没有缓存，所以没有真实编译 JS/WASM；源码包中的 v0.5.14 发布链路已实现，目标 Mac 的浏览器全流程测试尚待运行，不得把这两项称为已通过。

## v0.6.0 智能文件接收

- `src/timepilot/smart_import.py`：`ingest()` 支持 ZIP、XLSX、CSV、Parquet；`ingest_many()` 支持直接多选多个文件，合并成内部安全归档；只接收受支持格式，ZIP 压缩比、文件数、单文件体积、总解压字节有上限。原始文件保留 SHA-256。
- 工作簿所有工作表均被扫描，前 25 行评估表头，自动区分说明、普通表、含年月列的长表及含 1–12 月列的宽表；按相同工作表名称、结构和列定义自动将多年工作簿分组。默认推荐“车型月度明细”，汇总或新增来源默认不混入。
- `src/timepilot/smart_import_api.py` 提供批量上传、目录、工作表预览、结构转换、进度查询、版本列表、质量预览、基础透视、来源筛选及 CSV 导出。
- `src/timepilot/static/smart-import-ui.js` 是无第三方 JS 依赖的独立页面。首页 `/imports` 上传文件无需先填写 `timestamp`、`series_id`、`target` 等字段。旧手动导入保留为高级选项。

## v0.6.1 结构转换与数据质量

- 宽表 `(年份, 1月,...,12月)` 转换为 `(timestamp, target)` 长表；普通 `(Time,A14)` 时间序列也能自动识别数值列。记录 `source_file`、`source_sheet`、`source_row`、`source_cell`、`raw_value`、`source_url`。
- 依据来源、统计口径和车型等稳定维度计算 `series_id`。产量、销量及新增来源作为独立口径保存。不同口径/来源的聚合会提示可能重复计算；用户可在基础 BI 中筛选统计口径与来源。
- 可选 `duplicate_policy=drop_identical` 仅删除数值完全一致的重合观测；数值不一致的跨来源冲突始终保留并标记 `conflict_review`，不能静默选择一个“正确”来源。源值为空、负数、不可解析数值、统计口径待核实均单独计数；连续月份数值突变采用五倍阈值做“需复核”标记，不自动修正。
- `available_at` 没有证据时保持为空，可选假定发布时间滞后，但明确记录为假设，用于真实回测前需核实。
- 版本 ID 源于原始 ZIP SHA-256、完整转换配方和转换器版本。输出 CSV、配方、原始资产和各自哈希不可变。不同配方产生不同版本，已生成版本不重写。
- 异步结构转换通过 `/transform-jobs` 返回任务 ID，页面按工作表进度轮询并显示已处理行数；运行时不创建新 Python 环境。服务重启后可以重新提交相同配方，已完成的不可变版本会复用。

## 真实数据核查与可接受的解释

用户上传的 2006—2026 年分年 ZIP 共 21 个工作簿、73 个候选工作表。旧数据中“说明”“车型月度明细”“源表汇总”等表的统计口径不能混加。已完成一次真实整包转换，得到 976,632 条月度观测和 18,553 条序列，缺失 113,250，负值 689，统计口径待核实 72,971，相同重合观测 48，数值冲突 288；对冲突保留原记录。此数字来自当前上传的真实测试样本，不代表已经完成人工口径确认。

## 本阶段尚未实现

- 用大模型解析含复杂语义的业务报表，需待后续 Agent/工具编排版本推进；当前是确定性结构识别。
- 根据未经核实的 `available_at` 自动开始严格的生产回测；本阶段不会掩盖时间泄漏风险。
- 在用户目标 Mac 上跑通 Perspective Python + WASM + Chrome 的完整生命周期闭环；这需要目标机器再次执行专门验收。
- 公网多用户鉴权、跨进程任务配额和生产数据库迁移。
