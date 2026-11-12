# FeatureLab 2.0 API 契约

**当前实际接口，以 `src/timepilot/feature_lab_v2_api.py` 为准。**统一前缀 `/api/v1/projects/{pid}/feature-lab`，项目和用户身份由当前服务端会话决定。所有字段绑定使用当前资产的 `field_id`；CSV 列名只负责展示。HTTP 422 表示参数/字段/算子错误，409 表示修订、状态或哈希不匹配，403/404 为权限边界，503 表示计算引擎不可用。

| 方法 | 相对路径 | 作用与主要参数 |
|---|---|---|
| GET | `/contracts?asset_id=&version_id=base` | 返回 DataContract 和修订号 |
| PATCH | `/contracts/{asset_id}?version_id=base` | `{expected_revision,patch}`，仅受控语义字段 |
| POST | `/contracts/{asset_id}/confirm` | 人工确认，输入 `{expected_revision}` |
| GET | `/fields/search?asset_id=&q=&limit=50` | 查找稳定字段 ID |
| GET | `/operators?asset_id=` | 可用算子及禁用原因 |
| POST | `/profile-jobs` | `{asset_id,version_id,request_id}`，202 返回作业 ID |
| GET | `/profile-jobs?asset_id=` | 当前用户已授权的近期作业 |
| GET | `/profile-jobs/{job_id}` | 画像作业状态 |
| GET | `/profiles?asset_id=` | 最近生成的真实画像 |
| GET | `/profiles/{profile_id}` | 已生成画像、来源指纹、完整统计范围 |
| GET/POST | `/sets` | 特征集列表/建草稿；POST `{spec,request_id}` |
| GET/PATCH | `/sets/{set_id}` | 详情与乐观修订更新 |
| POST | `/sets/validate` | FeatureSpec v2，算子/DAG/时间约束预检 |
| POST | `/sets/{set_id}/runs` | `{request_id}`，启动全量特征计算 |
| GET | `/runs/{run_id}` | 作业状态、进度、错误 |
| POST | `/runs/{run_id}/cancel`、`/retry` | 持久化作业控制 |
| GET | `/runs/{run_id}/result?preview_rows=25` | 实际物化记录及有限行样本，校验 SHA |
| POST | `/sets/{set_id}/request-review`、`/approve`、`/publish` | 人工审核与不可变发布 |
| GET | `/sets/{set_id}/publication-preview`、`/lineage` | 发布影响与来源链 |
| POST | `/evaluations/plans` | `{protocol}`，基线/切分方案 |
| POST | `/evaluations/plans/{plan_id}/run` | `{request_id}`，202 评价任务 |
| GET | `/evaluations/{evaluation_id}` | 真实实验结果 |
| POST | `/monitor/compare` | `{reference_profile_id,current_profile_id}`，对比同资产两次全量画像 |
| GET | `/monitor/{snapshot_id}` | 描述性监控报告 |
| GET | `/tools?asset_id=&version_id=base` | 运行时可用工具及禁用原因 |
| POST | `/agent/plans` | `{message,asset_id,version_id,request_id}` |
| GET | `/agent/plans/{plan_id}` | 计划、真实步骤、审计 |
| POST | `/agent/plans/{plan_id}/execute` | 执行可用工具，异步时 `awaiting_job` |

**运行状态：**`queued`, `running`, `cancel_requested`, `cancelled`, `succeeded`, `failed_retryable`, `failed_terminal`。只读画像、预览、全量物化及发布为不同动作，HTTP 202 不等于计算成功；真正图表渲染还需要 Perspective Viewer 回执。

**契约源：**`feature_contracts.py` 里的 Pydantic 为权威结构。`python scripts/export_featurelab_schemas.py` 生成 `contracts/featurelab/*.schema.json`。需要手工确认的字段语义、完整性、PIT 与权限在后台校验，不能只依赖 JSON Schema。

**旧接口兼容：**`/recipes`、`/recipes/{id}/preview`、审核、发布等 v0.24.6 路由保留。历史配方不会隐式更换运算窗口的定义。
