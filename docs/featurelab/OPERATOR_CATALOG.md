# FeatureLab 2.0 可用算子及参数

下表反映 `feature_operator_registry.py` 当前实现，不能将没有执行器的能力展示为已支持。

| `operator_id` | 操作 | 参数 | 当前状态 |
|---|---|---|---|
| `arithmetic.binary` | 两数值列加减乘除 | `operator` 为 `+ - * /`；除零输出 NULL | 可执行 |
| `lag.rows` | 按来源稳定顺序或已确认时间滞后 | `lag` 1—240 | 可执行 |
| `difference.rows` | 与 N 条记录前的值作差 | `lag` 1—240 | 可执行 |
| `rolling.mean` | 最近 N 条有效观测均值 | `window`、`min_periods` | 可执行 |
| `rolling.std` | 最近 N 条记录的样本标准差 | 同上 | 可执行 |
| `rolling.min` / `rolling.max` | 最近 N 条记录的最值 | 同上 | 可执行 |
| `ewma` | 指数加权平均 | 待验证 | 不可用 |
| `event.since` | 上次维护距今 | 事件表和时间关联待验证 | 不可用 |
| `unit.convert` | 受控单位转换 | 单位注册表未落地 | 不可用 |
| 时长窗口/自定义公式/跨表 JOIN | 根据真实时间或多表计算 | 尚无完整安全执行器 | 不可用 |

**统一规则：**`input_field_ids` 必须来自当前授权契约，输出名必须是安全标识符，不接受自由 SQL/Python；数值字段的物理类型、依赖 DAG、窗口分组和排序、算子版本都需通过后端检查。时间序列可按行数探索；监督任务只接受真实且确认的时间与设备实体。窗口内历史记录的业务可用时间还需要不晚于当前决策时点。

**与 v0.24.6 兼容：**旧 `arithmetic / lag / difference / rolling_mean / rolling_std` 保留原有历史行为和接口。新版本使用上述 `operator_id`，旧配方不会强制更换版本或原列顺序。
