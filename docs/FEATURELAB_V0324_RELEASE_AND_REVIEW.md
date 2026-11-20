# TimePilot v0.32.4｜旧配方安全审计、任务引导与同源验收

## 版本定位

继承 v0.32.3 的排序键/实体分组修复。本版进一步修复**历史手工窗口配方可能仍使用导入行号**的风险；将用户下一步操作直接写入自动分析状态；将已有的真实 CSV→PIT Parquet→模型评价→Perspective 验收证据收束成强校验门禁。

## 功能修改

| 模块 | 文件 | 本次改动 |
|---|---|---|
| 历史窗口审计 | `feature_legacy_audit.py`、`feature_registry.py` | 逐特征识别隐式导入行序、分组错误、未确认字段；返回只读证据，不改历史配方 |
| 资产安全发布 | `feature_registry.py` | 发布预检根据历史审计显示 `unsafe_legacy_order`；高风险旧窗口禁止再次发布，已有资产保留 |
| 查询 API | `feature_lab_v2_api.py` | `/sets/temporal-audits?asset_id=` 和 `/sets/{sid}/temporal-audit` |
| 手工构造 | `static/feature-shell.js` | 对窗口和事件类特征绑定已确认的真实时间或唯一记录顺序、实体键；无依据时提示用户先确认 |
| 资产 UI | `static/feature-catalog-ui.js` | 显示历史顺序风险与原因；点“查看定义”可看到完整结构化审计 |
| 自动分析 | `feature_auto_analysis.py`、`static/feature-auto-ui.js` | 按真实状态输出下一步操作：继续计算、复核候选、确认物化、评价、报告等 |
| 实际证据门禁 | `scripts/featurelab324_acceptance.py` | 严格比较外部 CSV SHA、物化产物与项目 SQLite 记录、评价及浏览器回执身份；无真实报告时 blocked |

## 用户操作

1. 正常进入“特征工程→智能分析”，任务状态上方会显示当前建议的下一步操作。**建议保留**只说明统计质量，仍需监督评价确认模型贡献。
2. 查看“结果与资产”，历史窗口如采用 `__tp_rowid` 或设备键尚不明确，会标记风险；点击“查看定义”查看原因。已有旧资产不被自动删除或修改。需要重新使用时，复制为新草稿，在字段契约中确认排序和设备分组，再全量物化。
3. 使用“高级工具→手工特征”构造窗口前，先确认实际排序键及实体键。无法证明时序含义时，仍可计算逐行数值变换；窗口会要求核实。

## 真正的工业数据验收

本版继续复用 `scripts/featurelab30_e2e.py`（外部 CSV、PIT、实际物化和同折监督评价）、`scripts/featurelab31_browser_acceptance.py`（本机 Chrome/Perspective 真浏览器）、`scripts/featurelab31_real_advanced_e2e.py`（tsfresh/Featuretools 真包）、`scripts/featurelab31_agent_business_acceptance.py`（人工审阅业务任务）。新增 `scripts/featurelab324_acceptance.py` 在这些脚本结束后核对**文件实际字节**与数据库、评价报告、真实浏览器回执的身份。

终端示例，先在源环境按各脚本帮助配置真实字段及运行记录：

```bash
# 仅验证核心同源数据、评价及双面板；不声称全业务验收
python scripts/featurelab324_acceptance.py \
  --csv /data/plant_measurements.csv \
  --backend reports/v031/same_source_e2e.json \
  --browser reports/v031/browser.json \
  --scope core --out reports/v0324/core_gate.json

# 全部业务门禁（包括可选引擎真包 + 10 项以上实际人工审阅）
export TIMEPILOT_QA_CSV=/data/plant_measurements.csv
export TIMEPILOT_QA_BACKEND=reports/v031/same_source_e2e.json
export TIMEPILOT_QA_BROWSER=reports/v031/browser.json
export TIMEPILOT_QA_ADVANCED=reports/v031/advanced_real.json
export TIMEPILOT_QA_BUSINESS=reports/v031/agent_business.json
bash TIMEPILOT.sh accept feature-lab-v0324 --mode configured
```

**用户提供数据的来源由操作者声明，软件只能验证本地文件指纹与运行一致性，不能独立认证企业数据真实性。** 未提供 CSV、报告、浏览器或高级算法真包时，配置验收返回阻断。离线模式只做源码及替身测试。项目数据目录、正式资产审批及已发布版本均不在自动更新范围。

## 验收结论与限制

- 应通过 Python API、审计、窗口行为、任务提示及同源 SHA 篡改阻断测试；前端测试验证按钮与风险提示可见。
- 在具备 DuckDB + PyArrow 的环境再执行真 Parquet 数值测试。本次无依赖时标记跳过，**不得按已通过统计**。
- 仍需在目标 macOS、Python 3.12.12、Node 24、已安装 Chrome 与完整 Perspective JS/WASM 下执行浏览器真验收。
- 由于没有用户企业测量及质量标签，本次不宣称已测得模型收益、真实设备/工况泛化或实际用户操作成功率。
- 数据库 Schema 仍为 35，保留原有项目、标注、特征资产、发布与历史运行数据。
