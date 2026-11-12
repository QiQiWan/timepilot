# Feature Agent Tool 契约

唯一的工具目录由 `feature_tool_manifest.py` 导出；HTTP GET `/feature-lab/tools?asset_id=...` 按引擎运行状态输出每个工具的 `tool_id`、`input_schema`、`effect`、`available`、`disabled_reason`。`feature_agent.py` 使用相同目录验证每个调用参数。

当前实现与可用性由实际执行器决定：

- **Read**：`feature.schema.inspect`、`feature.field.search`、`feature.contract.inspect/validate`、`feature.operator.search`、`feature.candidate.propose`、`feature.run.get`、`feature.quality.inspect`、`feature.lineage.inspect`、`feature.publish.preview`。
- **Draft**：`feature.spec.validate`、`feature.set.draft_create`。字段/算子/来源版本均须重新校验。
- **Compute**：`feature.profile.get_or_create`、`feature.set.preview`、`feature.run.create`，缺少实际 DuckDB/Arrow 能力时返回不可用。
- **Evaluation**：`feature.evaluation.plan` 检查任务和固定切分契约，不自动生成模型增益。
- **明确暂不提供执行器**：`feature.viewer.open`、`feature.profile.event_effect`、`feature.window.duration`、`feature.join.virtual`、`feature.expression.custom`。UI 可以展示受限原因，模型不得执行。

服务端身份、当前项目、原始文件绝对路径、租户信息和 API 密钥不进入模型工具参数。LLM 返回工具计划后必须再次核验真实字段、操作者权限、Source SHA、FeatureSpec、算子能力和预算。数据操作只能形成草稿或计算结果。`approve/publish/asset.purge/admin` 不在通用自动化执行目录里。

简单要求（查看字段、两列相加、多列移动平均）优先由确定性路由识别。复杂要求调用已配置的模型，可进行不超过 8 个步骤的受限计划；队列返回真实 `job_id`，状态保持 `awaiting_job` 直到任务完成；从不把排队或模型文本当成执行成功。

**当前限制：**FeatureLab 的 Agent 仅有一部分工具具备完整执行器，部分复杂指令需要人工补齐时间/实体语义。未形成双人标注的真实工业任务集，不能声称达到生产级自主实验能力。
