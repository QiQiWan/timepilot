# TimePilot v0.37.3｜证据驱动解释、特征对照与报告交付

## 本轮实现及数据依据

本轮保留 v0.37.2 的 DuckDB 统计、项目管理、FeatureLab、正式物化和 Perspective。新增只读 `feature_evidence_v0373.py`，把解释逻辑从浏览器端统一移到服务端，不要求大模型 API。报告和页面使用相同的数值证据记录，不重复进行独立的启发式描述。

每项可发布的统计结论均携带 `id`、`title`、`observation`、`claim_type`、`status`、`scope`、`evidence`、`limitations`：

- `scope` 包括 `source_sha256`、`contract_revision`、`version_id`、筛选 `scope_sha256`（仪表盘）或候选指纹（候选特征图）。
- `evidence` 使用服务端计算结果的数字和具体字段路径（例如 `fields[0].null_count`），证据 ID 为范围和数值的规范化 SHA256；不能凭字符串拼接创建预测收益。
- 对记录数、字段缺失数、统计卡片和相关系数进行一致性检查；有冲突时返回 `insufficient_evidence`，前端明确显示“无法判断”并停用报告下载。
- 结论分为筛选范围、缺失情况、线性关联、分位数、特征直方图集中比例、时间桶极差和分组规模。不同字段类型只触发对应模板，缺少数值则不编造。
- `Pearson` 属于描述相关；趋势、极差与异常原因不能相互替代。统计显著性、时序泄漏与独立测试集收益需另行证据。

## 三种模型对照

保留 v0.35.5 的原始字段、全部候选、筛选后特征同源、同折验证逻辑。新增按模型任务方向计算两个候选方案相对于原始字段的逐折改善：回归 MAE 使用 `MAE(raw)-MAE(candidate)`；二分类 AP 使用 `AP(candidate)-AP(raw)`。保留负值和改善折数。只有下列条件同时满足时才返回 `variant_deltas`：

1. 字段契约确认标签且不包含受限标签；
2. 已物化的 `artifact_id` / SHA256 与评价报告一致，原数据来源及契约修订一致；
3. 各外层折的三个方案均有有限指标，且汇总的同折改善与逐折数字一致；
4. 验证折数满足现有门槛；结果明确标记 `outer_validation_folds_only`，不以最终测试集选特征。

**无标签 / 同源失败 / 缺折 / 汇总数字冲突：**不返回三方案收益表、不返回 `variant_deltas`，用中文给出具体的证据缺失原因。

## 页面、接口和报告

- `POST /api/v1/projects/{pid}/feature-lab/data-analysis/dashboard`：原数据总览新增 `evidence_analysis`。
- `POST /api/v1/projects/{pid}/feature-lab/data-analysis/dashboard/report`：接收相同数据版本、来源 SHA、契约修订和白名单筛选；服务端**重新计算**数据概况，生成含证据 ID、路径、数值及 SHA256 的 Markdown。不接受客户端自报统计数值；来源冲突、数据不完整时拒绝导出。
- `GET /api/v1/projects/{pid}/feature-lab/analysis/sessions/{sid}/candidates/{cid}/chart`：`explanation.evidence_analysis` 新增可计算证据；保留 `explanation.what_is_shown` 供技术说明。
- `GET /api/v1/projects/{pid}/feature-lab/workspace/insights`：验证后返回 `model.variant_deltas`；前端显示原始字段与两种方案的评价折比较，标明退化或尚无可靠收益。
- `GET .../analysis/sessions/{sid}/report`：沿用旧版带 SHA256 的归档链路，新增“特征方案同折对照”章节（仅通过证据门槛时出现）；未确认标签时只有数据质量及模型状态说明。
- 仪表盘导出前，在浏览器内用 WebCrypto 验证下载的 Markdown SHA256；回传的来源 SHA、筛选指纹必须与当前页面一致，任何一个不符均拒绝导出。

## 验收与反例

```
python scripts/featurelab373_evidence_acceptance.py --out reports/v0373/evidence_acceptance.json
python -m pytest -q tests/test_featurelab373_evidence.py tests/test_featurelab355_evidence.py
node --test frontend/native-bi/tests/featurelab373_evidence_ui.test.mjs
```

完整物理验收依赖真实 DuckDB/Parquet，运行 `tests/test_featurelab372_real_duckdb.py` 新增 v0.37.3 对账：从真实 Parquet 独立 SQL 核对当前筛选数据计数，要求报告的证据 ID、SHA256 和来源与数据概况一致，切换版本后强制更新证据。**当前环境缺少 DuckDB 时退出码 2 / BLOCKED，不能用 Python CSV 测试代替物理验收。**

主要反例：无来源 SHA、来源替换、筛选范围变化、字段缺失计数冲突、相关系数超出范围、空数据、直方图有限记录与桶计数不符、缺少外层评价折、负收益、报告字节被修改、跨数据版本的响应。

## 剩余边界

1. 大模型自然语言润色尚未纳入本次无证据数值门禁；目前使用可复算的确定性中文模板。
2. 统计解释不宣称因果关系或实际工厂收益；真实产线需验证采样时点、标签可用时间和设备泛化。
3. WebSocket 原生 Perspective 和真实 Chrome 视觉仍按 v0.37.1 门禁验收；本次数据证据测试不代替浏览器渲染检查。
4. 数据一览导出的 Markdown 是当前筛选范围的只读报告。完整特征分析报告继续使用已有不可变归档，并保留产物 SHA256。
