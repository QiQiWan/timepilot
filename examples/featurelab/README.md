# FeatureLab 2.0 联调模板

这些 CSV 为**说明性合成数据**，不代表真实产线记录。`00123` 等编号应按字符串导入。时间以 ISO-8601 带时区文本给出，**导入后物理类型可能仍为 VARCHAR**：只有经过受控时间转换、字段证据核验与人工确认，才能将其标记为正式事件时间。原文件不变。

- `equipment_wide.csv`：设备宽表，每行对应一条观测。
- `measurements_long.csv`：测点长表；同一设备同时存在多个 `metric_name` 的记录；正式窗口前先明确透视及重复观测规则。
- `maintenance_events.csv`：维护事件；`available_at` 表示事件信息业务可用时点。
- `production_batches.csv`：批次工况背景数据。
- `quality_decisions.csv`：节点 A 监督任务示例；终检标签在决策后可获得，不可作为节点 A 输入。
- `field_dictionary.csv`：业务字段含义建议模板，导入后需做源字段映射和确认。

JSON Schema 位于 `contracts/featurelab/`，使用 `python scripts/export_featurelab_schemas.py` 重新从 Pydantic 模型生成；以 Python 模型为唯一结构契约来源。

部署端必须执行类型、时区、项目权限、来源 SHA、时点可用性和算子预算校验，单独的 JSON Schema 校验不能证明业务安全。
