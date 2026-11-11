# TimePilot v0.13.0 项目化工作流与集成审查

## 1. 当前交付范围

本版包含项目 CRUD 和软删除、项目内历史资产关联、上传结构识别与复核、既有智能数据转换与审批、统一 BI 数据入口以及离线 BI 启动门禁。通过旧版 `DataAssetCatalog` 引用现有不可变版本，未对业务原始文件实施复制或迁移。操作入口为 `TIMEPILOT.sh` / `.bat`。

## 2. 实际用户流程

1. 打开 `/projects`，新建一个项目。
2. 进入项目的“数据管理”，上传 CSV、Excel 或 ZIP（可多选）。
3. 打开解析结果：查看候选数据区域、样本字段画像和工作簿结构警告。页面不显示完整行网格或简易图表。
4. 选择数据区域；需要业务语义辅助时，用户手动许可 LLM 请求。审核字段含义、统计口径和源文件歧义后执行不可变数据转换。
5. 转换完成后，项目数据清单登记稳定 `DataAsset` 引用。点击 BI 进入同一个版本的原生只读数据视图。
6. 如需编辑，明确创建“可编辑工作副本”；编辑/回滚仍由现有原生 BI 引擎实施，原始标准化资产保持不可变。
7. 从当前 BI 视图创建预测草稿，经过真实 `available_at` 等预检后再确认训练。

## 3. 核心模型与文件

- `projects.py`：SQLite WAL `projects` / `project_imports` / `project_assets` / `project_native_assets` / `project_runs`；项目软删除和旧资产一次性迁移标记。
- `projects_api.py`：项目 CRUD、资产、导入、计划审批与执行、视图、模型及任务入口。
- `workbook_intelligence.py`：有边界的 XLSX 表结构补充扫描和源 SHA-256 复核；`scope=structure_and_sample`。
- `project-ui.js` 和 `project-ui.css`：项目页、文件结构和语义确认页面、项目 BI 导航。
- `ensure_native_bi.py`：启动时离线 BI 资源、导出契约、源指纹和 Python Virtual Server 检查；条件允许才从现有 node_modules 编译。
- `launcher.py`：项目、数据、Agent、研究和原生 BI 验收统一子命令。

## 4. 验收方式

```bash
# 使用现有 Python 环境。不要创建新环境。
bash TIMEPILOT.sh source-check
bash TIMEPILOT.sh accept projects
bash TIMEPILOT.sh status
# 目标机已有 Perspective 编译物及依赖后:
bash TIMEPILOT.sh accept native
bash TIMEPILOT.sh
```

`status`/启动门禁在源码包缺少原生 BI 资源时返回失败是**预期的保护行为**。源码测试通过不代表已获得免构建的离线安装包；需要发布机生成并归档 JS/CSS/WASM、`perspective-build.json`、npm 锁文件和浏览器验收证据。

## 5. 本版功能边界和待完成事项

- 复杂 Excel 的合并单元格、公式和多块表区域已可生成警告。自动分块、公式重算、跨表语义关联与人工标注数据集仍需继续开发。
- 大模型参与语义识别由用户授权；模型失败不应清除确定性解析结果。真实模型 API 的准确率、人工修正工时和成本尚未量化。
- 原生 BI 的真实 Chrome 编辑、撤销、回滚、刷新与重连仍需在拥有 DuckDB、Perspective 和已编译 WASM 的目标 Mac 验证，尤其是 Rust `View not found`。
- 本地项目作用域 API 实施资源引用约束，但旧版全局 API 为兼容保留，本地单用户模式尚未达到企业多租户强认证要求。
- 项目模型页目前区分插件和运行记录，尚未实现完整模型权重元数据及企业工艺任务的全部管理视图。
- 数据文件解析后的质量页保留明确的 `sample` 与 `full` 标识，不将前 85 行或候选头部样本的统计当作全量结果。
