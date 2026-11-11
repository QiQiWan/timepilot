# TimePilot v0.14.0｜项目导入任务、字段画像与受控语义建议

## 1. 本轮已实现

本轮基于 v0.13.0 的项目对象、不可变 DataAsset 和统一 BI 入口继续开发。

- 项目文件上传改走 `POST /api/v1/projects/{pid}/imports/jobs`。HTTP 端只保存原文件、文件名和 SHA-256，返回可持久化的 `job_id`；后台 Worker 负责解压、逐 Sheet 识别、候选区域归组。旧 `/imports` 同步接口保留兼容。
- 上传任务状态持久化至项目 SQLite 表；任务支持 `queued/running/succeeded/failed/cancelled`、排队取消、失败有限重试，以及多 Worker 原子认领。后台扫描成功后自动关联项目，上传暂存副本自动清理；原始导入数据保持不变。
- 以 `job_id` 作为最终 `import_id`，异常断开后再次执行可读取已经提交的原始导入清单，不重复创建数据资产。
- `WorkbookIntelligence.profile_region()` 可以逐行计算候选区域字段非空数、缺失、数值类型、最小值和最大值，计算范围明确标记 `full_candidate` 或 `bounded_incomplete`。唯一值是最多 1,024 个不同值的下界，不声称是全量去重结果；提供源文件哈希校验与按候选缓存。
- 模型参与时只发送字段结构，不默认发送行值；模型可返回字段角色候选，服务器拒绝未知列、未知角色、未知区域，结果保留 `suggested_unconfirmed`，不替用户确认业务含义。
- 项目页面增加任务状态、重试、取消、字段画像、可恢复的历史语义计划。具体数据网格和互动透视仍只从正式 BI 进入。
- BI `/api/v1/ready` 检查发布资源、源码指纹及 DuckDB/Perspective Python 能力。无法就绪则返回 503；正常启动仍不安装依赖、不创建 Python 环境、不自动下载浏览器。

## 2. 项目 API

```text
POST   /api/v1/projects/{pid}/imports/jobs
GET    /api/v1/projects/{pid}/imports/jobs
GET    /api/v1/projects/{pid}/imports/jobs/{job_id}
POST   /api/v1/projects/{pid}/imports/jobs/{job_id}/cancel
POST   /api/v1/projects/{pid}/imports/jobs/{job_id}/retry
GET    /api/v1/projects/{pid}/imports/{iid}/regions/{candidate_id}/profile?max_rows=100000
GET    /api/v1/projects/{pid}/imports/{iid}/plans
```

文件上传采用 Multipart，多选普通 Excel/CSV/Parquet；ZIP 请单独上传，避免混淆压缩源路径。异步扫描返回 202。可使用上传任务页面或查询接口获取扫描状态；扫描成功后 `import_id=job_id`，从原有人工语义确认接口继续推进。

字段画像是候选源区域的计算结果；转换后保留独立的不可变版本和完整数据质量统计。扫描含未识别表头、公式无缓存、复杂合并单元格时，应由人工复核，当前并不承诺自动正确拆分全部复杂工作簿。

## 3. 数据正确性与安全

项目关系表仍引用 SmartImport 原文件，不复制全量业务数据。源数据 SHA-256 在后续读取/转换前重新核验。归档或移入回收站时若存在待处理上传任务，将阻止操作。模型只能提出元数据建议，不能执行 SQL、Shell、Python、自动训练或修改原始文件。业务主键、统计口径与真实发布时间等仍遵循现有强制校验。

任务在进程崩溃后按租约过期重新认领，最迟需等待当前租约超时；单个 Excel 解析期间暂不支持硬中断。后台 worker 在进程停止时结束认领，新版保留阶段/错误状态供后续恢复。

## 4. 统一操作入口

继续保留根目录 `TIMEPILOT.sh` 和 `TIMEPILOT.bat`，其他实现位于 `scripts/`。

```bash
conda activate server
bash TIMEPILOT.sh source-check
bash TIMEPILOT.sh accept projects
bash TIMEPILOT.sh status
bash TIMEPILOT.sh
```

这里交付的是 **完整源码包**，不包含 node_modules、前端编译 JS/CSS/WASM 或生产数据。若 Perspective 编译物缺失且离线依赖不可用，完整模式会在启动前阻断。发布者在构建机执行 `bash TIMEPILOT.sh build --bootstrap-lock` 生成可信锁文件及发布包；真实 Mac Chrome 的编辑/回滚/重连验收仍需执行 `bash TIMEPILOT.sh accept native`。

## 5. 后续验收与研发边界

本轮需要在目标 Mac Python 3.12.12 环境复核：21 年 ZIP 上传、后台扫描完成、浏览器刷新恢复、语义方案审阅、数据版本保存、正式 Perspective 的网格/透视/编辑/回滚及预测任务草稿。不能将缺少 JS/WASM 的源码归档宣称为离线免构建安装包。当前复杂 Excel 的多逻辑表块精确拆分和真实模型 A/B 效果验证仍在 v0.15 后续迭代范围。
