# TimePilot v0.38.9 — 业务目标驱动的建模配置工作台

## 1. 本轮范围

基于 v0.38.8 Mac source-repair 完整基线，改造原有建模抽屉与 FeatureLab 的交接。原始项目、数据版本、持久任务、模型运行后端和 SQLite 结构均未被修改。

**进入路径：** 项目 → 模型管理 → 新建预测任务；选择已有数据资产后打开 3 步引导。

**第一步：任务类型与业务目标**。可输入“预测未来一周销量”“在下一工序前识别不良”等业务描述；系统在当前来源字段名称和抽样类型上做可解释匹配。显示 4 类入口：

| 用户任务 | 配置要点 | 当前执行路径 | 限制 |
|---|---|---|---|
| 时间序列预测 | 目标 Y、时间列、实体/频率、预测步数、X、可用时间 | ModelingWorkbench | 日期真实性、PIT、时序滚动验证需要确认；预测器范围不扩大 |
| 一般数值预测 | Y、X、样本独立/分组/时间切分 | FeatureLab 监督评价 | 仅传入 Y/X 意向，须在 FeatureLab 复核合同、物化与模型评价 |
| 工序提前分类 | 不良标签 Y、判断时间、成熟时点、正负值、X | ModelingWorkbench 二分类 | 标签时点、现场工序必须确认 |
| 一般分类 | Y、X、分层/分组/时间切分 | FeatureLab 监督评价 | 不提供未经核实的自动训练结论 |

**第二步：目标 Y 与特征 X**。按列名、数值/日期可解析率以及现有 profile 角色推荐；每个推荐可查看依据。自动建议不会替代人工确认。支持逐字段勾选、搜索、全选安全候选与清空。标识符、时间字段、标签、明显未来结果和未经编码的文本默认禁止作为原始数值 X。最多 32 个原始数值候选；超出时须先工程化。

**第三步：类型专属配置**。时序任务显示日期、频率、预测跨度；分类任务显示正负标签和标签成熟时间；高级物理约束、重采样与验证参数默认折叠。通用任务跳转 FeatureLab 已有监督评价，不新增一套尚未验证的训练引擎。

## 2. 源码文件

- `src/timepilot/model_task_designer.py`: 增量字段角色/任务建议器，说明推荐依据、保守剔除泄漏项。
- `src/timepilot/modeling_workflow.py`, `modeling_api.py`: 在旧 `proposals` 外增加 `task_designer` 契约；`business_goal` 查询参数；原路由向后兼容。
- `src/timepilot/static/modeling-ui.js`, `modeling-ui.css`: 4 类任务选择；人工 X/Y；动态配置、折叠高级选项和风险提示。
- `src/timepilot/static/feature-shell.js`, `feature-auto-ui.js`: 仅以参数意向导入目标、输入候选和分折偏好，并再次请求人工确认。
- `src/timepilot/static/app.js`: 统一表单路由和字段搜索事件。
- `scripts/release_gate_v0389.py`: 继承 v0.38.8 的物理/浏览器/离线门禁，增加本轮回归，不放宽阻断。
- `tests/test_model_task_designer_v0389.py` 和 `frontend/native-bi/tests/model_task_designer_v0389.test.mjs`: 确认推断、安全剔除、状态切换与旧 API 契约。

## 3. 注意与未完成验收

1. `task_designer` 是基于规则和抽样的推荐，不代表学到了因果关系或验证了预测收益。未发现可靠目标列时，不应自动确定 Y。
2. X 的可用时间（PIT）、高度相关冗余、共线性、数据漂移、真实收益仍应在 FeatureLab 模型评价中验证。
3. FeatureLab 一般任务的 X/Y 由 URL 提供**待核实的输入意向**，不是自动训练或自动物化；原生评分机制保持原样。
4. 当前执行环境不具备 DuckDB/PyArrow/目标 Mac 与原生 Perspective 联调；不能以 Node 或 Python 单测代替生产就绪。
5. 浏览器中的完整 DOM/端到端流程未取得实际通过证据；需要目标 Mac Chrome 联调。

## 4. 测试与命令

```bash
bash TIMEPILOT.sh source-check
python -m pytest -q tests/test_model_task_designer_v0389.py tests/test_modeling_workflow_v017.py tests/test_industrial_modeling_v018.py
node --test frontend/native-bi/tests/model_task_designer_v0389.test.mjs
node --test frontend/native-bi/tests/*.test.mjs
bash TIMEPILOT.sh release-gate --mode physical
bash TIMEPILOT.sh release-gate --mode browser
```

启动不重新建立 Conda 环境，不修改 `TIMEPILOT_HOME`。为了避免先前的 OneDrive 隐藏目录残留问题，请完整解压 ZIP，保留 `.github/` 原样，并在覆盖之前备份旧项目目录。
