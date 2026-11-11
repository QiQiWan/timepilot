# TimePilot v0.12.0 · 真实业务评估与受控自主建模

## 业务入口

- Web：`/research`，包含「人工语义评估」「自主预测实验」「机理学习验证」三个页签。
- Agent：`/agent`，`goal=autonomous_modeling` 进入持久化工具流程；`goal=semantic_benchmark` 生成标注队列；`goal=mechanism` 检验已有机理证据。
- Mac/Linux：`bash TIMEPILOT.sh` 启动，`bash TIMEPILOT.sh accept research` 运行第四阶段测试。
- Windows：`TIMEPILOT.bat`。日常启动沿用现有 Python 环境，不新建 Conda/venv、不运行 pip/npm。

## 1. 人工标注评估集

`POST /api/v1/research/semantic/seed/{import_id}` 从实际导入的工作表生成只含字段结构的案例。`GET /api/v1/research/semantic/cases` 返回标注队列；业务人员对照原始 Excel，通过 `POST /api/v1/research/semantic/cases/{case_id}/review` 选择业务角色、表格结构并提供人员标识。首次自动生成的候选**不计入人工真值**。

`POST /api/v1/research/semantic/evaluate` 对所有已确认样本计算规则识别效果。显式传入 `use_model=true` 后，调用在设置中启用的真实模型进行同集对照。记录完整任务成功率、字段角色准确率、布局准确率、估算字段修正操作量、实际模型有效回复数、Token、调用时间与人工填写的单价得到的预计 API 费用。没有真实标签时返回 `awaiting_human_labels`，未返回 usage 的 API 不显示捏造的成本。

模型只接收有限的列名、结构类型和月份列数量；人工真值、原始数据行和来源网址不会进入模型请求。人工标注身份目前是本地单用户提交的标识，不构成企业审计级实名签名。

## 2. 自动特征与候选模型对比

`POST /api/v1/research/experiments/plan` 检查数据来源、统计口径、实体、缺失、真实带时区的发布时间、连续月份和预算。用户确认方案哈希后调用 `/experiments/execute`，或在 `/agent` 创建任务，审查 `waiting_approval` 后明确批准。

运行四种轻量 CPU 模型：Last Value、Moving Average、Seasonal Naive、Lag Ridge。Lag Ridge 使用 1/2/3/6/12 期滞后、过去三/六期滚动均值和日历周期特征，滚动多步递归使用此前**预测值**，不会读取未来真实 target。所有模型在相同的滚动时间窗口比较；预测可用时间采用过去记录的真实 `available_at`，未来目标值发布时间必须在预测起点之后。

选取 WAPE 最低的合格模型，报告 MAE、RMSE、sMAPE、Bias、相对 Last Value 的改进和失败候选，并保存模型拟合产物及逐折预测。模型输出属于**研究性实验结果**，不会创建审批通过的生产训练任务，也不会自动部署。

原始历史 Excel 没有 verified `available_at` 时，实验将被阻断；不能为了得到排行榜自动制造发布时间。

## 3. 机理学习

`POST /api/v1/research/mechanisms/evidence` 接收有来源的测量/仿真状态、控制、采样间隔、单位和维修事件位置并计算 SHA256；`POST /mechanisms/{evidence_id}/assess` 进行基于历史训练与时间后段留出的最小二乘动力学辨识，剔除跨维修事件的转移对，核查可辨识条件数与离散稳定性，与持久性基线比较。

上传资料标注为 `measured_equipment` 也不会自动改为 `verified_on_real_equipment=true`。当前仅支持一阶线性灰箱状态控制假设，尚未实现 Neural ODE、SINDy 或跨工况企业实机验收。

## 4. 可用性和后续研究

- 标注任务要由业务人员确认证据；当前样本池没有人工金标，报告应显示待审核。
- 大模型提供与规则的实际对照，无法从无外部 API 的单元测试推断真实模型增益。
- 执行器保留持久化任务记录、审批、预算和取消。同步第三方训练目前仍采用协作式时间预算，不能强制终止卡死的外部本地原生库，需要进程隔离进一步实现硬超时。
- 实体可用时间、口径和质量是训练前的强制检查。首次大规模企业数据实验应经统计师和业务专家核对。
- Mac 原生 Perspective 真实 Chrome 编辑、回滚、重连尚未在此执行环境闭环，需由 `accept native` 独立验收。

## 5. 实际人工修正会话（A/B）

通过 `POST /api/v1/research/semantic/cases/{case_id}/trial` 指定独立复核者和规则/模型试验组；`POST /api/v1/research/semantic/trials/{trial_id}/finish` 提交复核后的结构和字段，服务器记录起止时间、提交更改字段数和与人工真值的一致性；`GET /semantic/trials/summary` 给出各组描述性汇总。用户界面 `/research?tab=semantic` 提供对应复核操作。缺少金标、缺少模型配置、模型返回非法字段等情况明确阻断模型组。

当前没有真实人工会话，因此不能报告真实效率收益。完整的对照实验还应控制案例难度、审核人员及随机分组，避免选择偏差；服务器计时包含用户空闲时间，尚不代表纯人工工作时间。评估样本只保留来源工作表、列名与结构提示，不包含完整业务行数据。

## 6. 独立机理证据关联

Agent 从自主预测实验转入机理验证时，要求证据 `source_asset_id`、`source_version_id` 与当前数据资产/版本一致；不允许将无关联设备测量或合成机理证据应用于销量序列。单独提交机理证据可独立检验但不会影响销售预测排名。当前辨识为一阶状态控制假设的候选检验，尚无企业真实物理数据验收。

## 7. Mac 安装 Chrome 实机验收

执行 `bash TIMEPILOT.sh accept research-browser`，复用系统已安装 Chrome，不下载浏览器、不截图；检查人工审核、规则组复核、保存后的 A/B 统计、预测实验计划和执行及机理页面入口。测试仅写入临时 home，使用合成时间序列。当前开发容器的浏览器 HTTP 导航受限制，因此此项必须在目标 Mac 上运行后才可标记完成。
