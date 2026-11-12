# TimePilot FeatureLab 2.0 v0.24.7—v0.25.2 源码改造与验收说明

**交付性质：源码迭代、契约/HTTP 与浏览器组件验收；真实 DuckDB / Arrow / Perspective E2E 需要目标机验证。** 本文的阶段完成表示核心业务接口已实现，不等同于方案中每一项研究扩展算子都已具备可执行计算。

| 阶段 | 实现 | 尚待真实验证/扩展 |
|---|---|---|
| v0.24.7 | Pydantic FieldContract/DataContract/TaskSpec/FeatureSpec v2、稳定字段 ID、人工修订/确认、算子目录、SQLite v19、生成 JSON Schema | Excel 合并表头及物理类型的完全保真应沿原导入系统另行验收 |
| v0.24.8 | 全量质量画像、时间/实体/采样诊断、SQLite v20、可恢复 Profile Job、FeatureLab 四 Tab、内嵌契约确认和帮助 | 复杂事件工况/频域画像、原生 Perspective 内嵌图表尚未完成 |
| v0.24.9 | 七种白名单算子、DAG 校验、SQL 编译、按设备分区、窗口 PIT 检查、租约任务、Parquet 物化和来源核验、SQLite v21 | 时长窗口、事件复位、100 万行实际性能、真实 DuckDB 数值验收待目标环境 |
| v0.25.0 | 无标签探索、时间/组切分、训练折内数值预处理、Raw-only 对照、折级指标及冗余诊断、SQLite v22 | 更广泛预测模型和跨工况/不确定性评价尚未实施 |
| v0.25.1 | FeatureSet 审核、独立人审、不可变新资产发布、血缘、发布审计、全量画像间描述性监控、SQLite v23 | 长期在线服务/增量物化和更丰富漂移统计待实施 |
| v0.25.2 | 21 个工具定义及可用性标记、基础本地意图、可选模型规划、受限执行/真实状态观察、模型不能自动审批、跨项目校验 | 真实工业语义双人评测集、成熟的多步自动特征实验和 Viewer 结果图表联动需继续验收 |

**关键安全修复：**监督计算允许事件发生时间与决策时间不同；历史窗口检查每条被引用历史观测是否在**当前决策时点**可用；禁止以标签或未来数据分区/排序；正式监督基线只使用已确认过程值字段。

**测试分级：**

1. 契约/HTTP/SQLite 单元测试验证 API、修订、DAG、安全门禁与旧版兼容。
2. 安装的 Chromium 组件检查四个新页面、字段确认弹窗和助手；接口使用合成模拟响应。
3. Node 测试验证现有 Perspective 前端状态模块不回退。
4. `tests/test_featurelab2_real_duckdb_v0249.py` 是真实 DuckDB 数值测试，缺依赖会跳过；配置验收必须强制依赖存在。
5. `--mode configured` 必须具备 Python 3.12.12、DuckDB、PyArrow、已编译且源码匹配的 Perspective 5.6.1 JS/WASM，以及本机 Chrome。运行真实数值与 native 浏览器验收；环境不完整时明确 `blocked_environment`，**不可视为通过**。

独立阶段入口 `bash TIMEPILOT.sh accept feature-contract-v0247` 至 `feature-agent-v0252`。结果写入 `reports/featurelab/v0252/<stage>/acceptance.json`，源代码发布包不默认包含运行截图或用户数据。

## 本次构建的离线实测结果

- Python 相关领域联合回归：**46/46 通过**；包括项目、导入摘要、BI 状态、特征契约、时间切分和 Agent API。
- Node 原生 BI 前端测试：**37/37 通过**。
- 本机 Chromium 四页面组件检查：**通过**；使用模拟 HTTP 数据，未加载 Perspective JS/WASM。
- 六个版本 `feature-contract-v0247` 至 `feature-agent-v0252`：**六组离线验收通过**，证据见 `reports/featurelab/v0252/`（实际执行在交付环境中生成）。
- 真实 DuckDB 数值测试：当前环境缺少 DuckDB，**跳过，未宣称通过**。
- 配置版验收：执行后正确返回 `blocked_environment`（当前 Python 3.13.5、缺 DuckDB/PyArrow 及 Perspective 编译资源），**未达到生产发行门禁**。

源码包由 `scripts/package_timepilot.py --source-only` 生成，采用逐文件 SHA-256 清单签名。用户目标机用 `bash TIMEPILOT.sh source-check` 复核，并在依赖齐备后使用 `bash TIMEPILOT.sh accept feature-agent-v0252 --mode configured` 做真实验收。
