# TimePilot v0.40.2：筛选源行血缘、设备 OOD 预检查、时序预算交互与 Worker 日志

## 1. 结论与实现边界

本轮沿用 v0.40.1 的四任务统一模型运行、冻结预算、逐折评价、预测资产和原生 Perspective 入口，新增经过签名校验的**筛选源行 → 规范化观测行 → 验证预测行**可追溯链路，同时解决时序任务预算选择未在 UI 下传、跨设备预检查过浅和子进程输出被全部丢弃的工程问题。

- **证据边界**：源记录 ID 绑定的是批准的数据版本与筛选结果的行位置及内容哈希。对编辑版本或筛选视图，它不冒充最早上传 CSV 的物理行号。结果 API 显式返回 `original_file_row_ordinal_certified=false`。
- **窗口血缘边界**：目前可准确追溯**被预测目标观测**的全部聚合源记录。连续预测插件历史输入窗口的逐值使用明细尚未统一采集；不能推断某条历史记录一定进入了最终模型拟合。
- **分类重复记录边界**：若两个规范化行具有同一时间、设备、标签与可用时间，旧时序插件的 `source_row_sha256` 无法区分，查询返回 `ambiguous_normalized_observation`，不会选择任意一行。
- **旧运行边界**：v0.40.1 及更早物化的数据缺少已签名的映射，返回 `legacy_lineage_not_recorded`。升级不偷偷回填或伪造过去的血缘。
- **物理门禁**：当前无真实 DuckDB、PyArrow、Perspective 与目标 Mac；本次仍是源码候选包，原生浏览器/离线发行需实机测试。

## 2. 血缘合同与数据流

源数据来自项目、资产、版本、冻结的 BI 选择条件。

```
项目 + AssetID + VersionID + SelectionHash
      | (_read_selected + 完整输入 SHA256)
      v
筛选后源记录 selected_row_position / selected_row_sha256 / selected_row_id
      | 非重采样 1:1 / 完整桶聚合 N:1
      v
规范化观测 normalized_row_position / normalized_row_sha256
      | 真实时序 Worker 逐折预测（按规范化观测哈希）
      v
已签名验证预测 CSV / 可选 Parquet
      | 审批/数据版本/预测资产/血缘四级 SHA256 联合核验
      v
受项目权限保护的逐样本原始字段查询
```

`src/timepilot/model_source_lineage.py` 在物化期间使用 `get_dataset()` 重新读取**实际落盘后的规范化快照**，避免原始浮点内存值和写 CSV 的 `%.15g` 精度不一致。记录每条来源行的 `selected_row_id` 和内容哈希，重采样桶保存全部源行，严格验证来源行仅被消费一次。数据文件为 `datasets/<dataset_id>/selected_row_lineage_v1.json.gz`，gzip 具备固定 mtime，文件 SHA256 登记在 `modeling_provenance.json` 与数据库草稿物化记录中。

映射独立于模型训练，模型插件无需修改算法数学逻辑。文件不对外静态暴露。查询先验证模型任务仍获批准、运行结果与预测资产签名，再核验数据集快照、草稿物化哈希、血缘文件哈希和所选源行原值哈希。历史数据与最新版本不匹配时阻断。原始字段值仅在项目级授权 API 中按页返回，不进入模型报告和运行事件日志。

### 2.1 API

`GET /api/v1/projects/{pid}/model-tasks/{task_id}/execution/predictions/lineage?row_id=0&source_offset=0&source_limit=20`

- `row_id` 为验证预测资产的全局 `__tp_rowid`，限定 0–239999；`source_limit` 不超过 20。
- `status=traced_to_selected_snapshot`：返回规范化观测哈希、数据集签名、源版本、源行总数、全部源行 ID/内容摘要，以及最多 20 条实际源字段值。
- `status=ambiguous_normalized_observation`：旧预测哈希撞上多条完全相同的规范化记录，不能唯一反查。
- `status=unmapped_prediction`：不存在匹配的观测，不编造映射。
- `status=legacy_lineage_not_recorded`：当前训练引用历史物化快照，不具备新血缘。
- `status=not_available_for_tabular_source`：表格模型已有原始版本证据，但本轮尚未为表格执行器发布单独的源行边表。
- 不属于项目、任务或已批准资产，返回 HTTP 404/409；非法分页与 ID 返回 422；篡改文件后拒绝返回来源字段值。

### 2.2 源行 ID 含义

`selected_row_id = SHA256({ asset_id, version_id, selection_hash, selected_input_sha256, selected_position, content_sha256 })`

明确保证：同一次被冻结选择的行身份稳定、不同选择版本不会串用。无法保证：未被导入时保存的外部文件真实行号、被编辑覆盖之前的输入行历史、模型最终历史窗口所用的全部中间行。

## 3. 全实验预算与 Worker 可观测性

旧时序任务页面新增 `5m / 15m / 30m` 的预算选择，提交统一执行入口时传递 `budget_profile`；确认后继续使用旧时序运行器冻结的不可变墙钟截止时间。

运行中的完整外层 Worker 使用 `stdout=PIPE,stderr=STDOUT` 并由专用线程持续消费。仅保留**最多 64 KiB 的最终输出**，敏感 token/API key/password 模式进行边界长度受限的脱敏处理，保存为私有 `runs/<run_id>/worker_logs/attempt-<n>.txt`，签名后返回给经过项目授权的执行步骤接口。超量输出不会无限增长磁盘。退出码与原有 `EXPERIMENT_BUDGET_EXHAUSTED` / `RUN_CANCELLED` / `TEMPORAL_CHILD_CRASHED` 等失败码仍然是状态依据。日志中的文本属于诊断线索，不能单独作为可靠完成回执。

日志脱敏通过有界匹配，防止超长重复字符导致灾难性回溯。不会记录完整子进程环境变量，日志被授权项目成员查看，需要运维侧控制项目权限。

## 4. OOD 分折预检查

`inspect_device_ood_folds()` 复刻真实时序分类器的候选折样本规则：先确定独立时间测试边界，再对历史训练/验证设备排序分组；每折训练样本必须在验证开始前并且标签成熟，不包含留出设备；验证设备必须在冻结验证区间有样本，训练/验证均包含正负标签，批次不得交叉。返回折级样本数、正例数、留出设备与 blocker。

该检查**不拟合任何模型**，前端只显示“分折可行性”，不会将其解释为预测准确率或泛化提升。独立测试仍为后续时间段测试，可包含验证中出现的设备。

## 5. 文件级改造

| 文件 | 作用 |
|---|---|
| `src/timepilot/model_source_lineage.py` | 新增已签名、压缩的行级多对多映射构建、完整消费和只读核验 |
| `src/timepilot/modeling_workflow.py` | 模型数据物化同时写血缘，读取设备 OOD 折预检查 |
| `src/timepilot/modeling_adapters.py` | 新增无拟合跨设备折样本可行性审计 |
| `src/timepilot/model_task_unified_runs.py` | 授权预测行 → 当前冻结源行与原始字段值，读取 Worker 日志 |
| `src/timepilot/model_task_api.py` | 新增受项目任务约束的血缘查询 GET API |
| `src/timepilot/temporal_worker_log.py` | 有界、脱敏、原子落盘 Worker 输出采集 |
| `src/timepilot/runtime.py` | 实际子进程 stdout/stderr 捕获与尝试日志回执 |
| `src/timepilot/static/modeling-ui.js` | 预算档位、折预检查、日志与逐样本源行查询 |
| `tests/test_model_source_lineage_v0402.py` | 真实物化与训练、聚合、重复值、篡改、跨项目、安全分页、日志和 OOD 反例 |
| `frontend/native-bi/tests/model_source_lineage_v0402.test.mjs` | 四组前端合同测试 |
| `scripts/release_gate_v0402.py` | 将上述测试加入发布门禁，保留历史门禁 |

## 6. 验收与实际限制

源码与独立解压门禁以 `scripts/release_gate_v0402.py --mode source` 的退出码为准，完整结果另见最终交付 `SOURCE_TEST_RECEIPT.json`。当前 Python 解释器为 3.13.5，不是目标 Mac 的 Python 3.12.12；Docker/CI 仿真无法替代真实 Perspective WS/Chrome 交互。

剩余 P0：对普通表格任务追加源行边表；为每个训练窗口记录真实使用的历史输入行，区分历史上下文和训练标签；将源行高亮和 Perspective 视图筛选形成可传递的原生行身份；使用 DuckDB 实现百万行以上索引式资产查询而非按页扫描 CSV；关闭 Mac 物理门禁。
