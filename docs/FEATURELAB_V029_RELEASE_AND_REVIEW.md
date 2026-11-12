# TimePilot v0.29.0｜特征有效性评价闭环：实施与审阅

**2026-11-12。** 基线为 v0.28.0 完整源码。本轮完成训练折特征筛选、三组模型比较、跨时间/设备/工况稳定性评价、受预算限制的内层自动特征搜索、人工审阅与新草稿生成，保持原数据、版本资产和审批发布链路。

## 1. 验收结论与证据级别

- **代码实现与单元回归：完成。** `bash TIMEPILOT.sh accept feature-lab-v029` 通过 **55 项 Python**（v0.29：12，v0.28：30，v0.27：10，v0.25：3）与 **14 项 Node** 合约测试，JS 语法及源码预检通过。另单独执行 6 项历史特征/字段测试通过；整个项目全量测试尚未完成，不能宣称全面回归通过。
- **数值级闭环：完成（确定性生成的工业结构测试数据）。** 通过 `scripts/featurelab29_benchmark.py --synthetic-fixture --models both --search` 在 360 行生成数据上完成 3 折 sklearn 线性/随机森林评估。它是数学与流程检查样例，**绝不能称为真实产线数据**。
- **真实观测的算法兼容性：有限验证。** 使用 sklearn 内置 UCI Wine 化学观测数据执行算法兼容用例，使用的是**人工构造时间戳**，没有真实时序意义，不代表工业生产数据验证。
- **实际工厂测量数据：未执行。** 用户当前没有在本轮提供可核验的产线 CSV。尝试下载公开工业过程数据但本环境 DNS 解析不可用。不能据此给出真实工厂准确率或收益。
- **正式数据导入→PIT物化→监督评价→Perspective 双面板：未完整实机执行。** 本机缺失 DuckDB、PyArrow、Perspective Python 与已编译 JS/WASM；无法诚实标记为通过。已保留原有 PIT/SHA 门禁，提供目标机器按阶段验收脚本。
- **浏览器 UI：模拟数据组件审阅通过。** Chromium 中渲染线性与随机森林对照、跨折质量、PSI、分组切片和人工筛选，两张截图已生成并人工目视复核；不构成真实 API/BI 联调。

### 已保存的验收证据

- `/mnt/data/TimePilot_v029_acceptance_stdout.log`：55 项 Python 与 14 项 Node 完整一键回归日志。
- `/mnt/data/TimePilot_v029_numerical_fixture_QA.json`：生成数据 360 行、逐折训练和验证指标、自动搜索配方及限制。
- `/mnt/data/TimePilot_v029_native_dependency_check.json`：原生运行依赖检查 `duckdb=false,pyarrow=false,perspective=false`，退出码 2，未宣称成功。
- `/mnt/data/QA_TimePilot_v029_Evaluation.png`、`QA_TimePilot_v029_SearchAndStability.png`：仅模拟 API 的 Chromium 组件截图。
- 源码包中保留脚本和测试，审阅证据文件作为单独附件交付，未混入正式用户数据/构建产物。

## 2. 具体代码与行为

| 需求 | 实现文件 | 核心行为 |
|---|---|---|
| 训练折质量筛选 | `src/timepilot/feature_selection.py` | 缺失率、有效样本、常量、冗余、训练期单变量相关性，保留原始基线列；每外层折单独拟合并保留淘汰原因 |
| 三组及扩展对照 | `src/timepilot/feature_evaluation.py` | 原始列 / 原始+全部派生 / 原始+每折筛选派生；可选择线性和随机森林；搜索启用且有合格候选时加第4组 |
| 评价指标 | 同上 | 分类 AP/召回/查准/误剔/平衡准确率及混淆计数；回归 MAE/RMSE；同折成对差异、折间均值/标准差/最差折 |
| 训练期预处理 | 同上 | sklearn `SimpleImputer` 和标准化或随机森林 pipeline，每折仅训练数据 `fit`，验证集只 `transform/predict` |
| 切分与标签时间 | 复用 `feature_split_protocol.py` 和 `feature_label_contract.py` | 时序前推 / 独立组折，净化间隔、同时间戳隔离、标签可用时间过滤；正式监督任务必须关联已核验 PIT 物化产物 |
| 稳定性 | 新增 `src/timepilot/feature_stability.py` | 验证集按设备、批次、工况切片；训练分位数基准 PSI；跨时间折稳定性与证据缺失提示 |
| 受限自动搜索 | 新增 `src/timepilot/feature_search.py` | 基于外层训练折的内层时间验证，生成绝对值/平方/对数/开方与同单位差值/比率；上限 96 候选、24 次候选拟合、每折 120 秒软时间预算；默认禁用 |
| 防泄漏保护 | `feature_contracts.py`、`feature_search.py` | 外层验证标签不参与搜索、训练折筛选或参数拟合；组隔离任务暂拒绝内层自动搜索，直到实现独立组的内层协议 |
| 结果复核/筛选 | `feature_evaluation.py`、`feature_lab_v2_api.py` | 选择原派生特征或搜索公式，保存审阅意见、记录来源与修订；只有人审后生成**新的未批准** FeatureSet 草稿 |
| UI  | `static/feature-evaluation-ui.js`、`feature-shell.js`、`feature-lab2.css` | 计划配置、指标对照（显示有效折数）、跨折 PSI/设备切片、折内特征保留率、可选搜索公式、保存和新建草稿；刷新恢复报告 |
| DB 兼容性 | `project_migrations.py` | Schema **31→32** 加性迁移 `feature_evaluation_selections`；保留原数据、审批、物化和旧报告 |
| 源码一键验收 | `scripts/featurelab29_benchmark.py`、`scripts/launcher.py` | `accept feature-lab-v029`；单独离线数值 CSV 验收与现有 v0.28 CSV→Parquet 验收互不冒充同一条 PIT 任务 |

**算法能力边界：** 首期内置受控标量特征搜索，不包含 tsfresh 或 Featuretools 的高维自动抽取、跨多表实体深度合成、遗传编程、贝叶斯超参数搜索。正式计算仍依赖 Python 3.12.12 环境及 DuckDB、PyArrow、Perspective。自行搜索出的公式必须重新进行物化、标签可用时点检查、模型验证和人工审核。

## 3. 实际计算的模拟验收结果（不代表生产效果）

命令：

```bash
python scripts/featurelab29_benchmark.py --synthetic-fixture --models both --search \
  --report /tmp/featurelab29_fixture_qa.json
```

输入为脚本固定随机种子的 **360 行人工生成工业字段**，标签也按脚本规则生成。生成样例：3 个时间前推折；训练样本分别 89、179、269；验证每折 90 行；`constant_test` 被全部淘汰，`pressure_delta` 和 `temperature_absolute` 在三个训练折均保留。

| 模型 | 原始字段 AP（3折均值） | 全部派生 AP | 筛选后 AP | 结论 |
|---|---:|---:|---:|---|
| 线性 | 0.781787 | 0.783412 | 0.783412 | 仅 +0.001625，低于相对基线 1% 的最小实际收益门槛 0.007818 |
| 随机森林 | 0.733354 | 0.708053 | 0.711660 | 派生特征在这个**生成样本**上反而降低 AP，不得自动发布 |

自动搜索只有部分折产生合格公式，因此该变体的模型均值不能与全 3 折均值直接比较，必须显示 `n有效折` 并输出 `insufficient_evidence`。这是此次审阅修复的一个重要误报点。

## 4. 数据和审计契约

- 正式监督评价从已核验 FeatureArtifact 的 Parquet 读取，绑定 FeatureSet 修订、源数据 SHA、Spec SHA、时间切分协议、PIT 证据；物化产物不符合要求时拒绝生成有效评价。
- 筛选拟合、模型填补、标准化、候选搜索严格限制于每个**外层训练折**；漂移与指标才允许读取外层验证数据。
- 自动搜索目前用线性模型作内层评分，外层评估可以同时选择线性和随机森林；各模型参数与来源保留在报告中。
- 训练/验证样本按决策时间和标签可用时间过滤，用户必须确认设备、批次、工况、业务目标与时间时区。存在多个设备、乱序时间或多时点样本时，应检查实体键、重复事件和有效性边界。
- 模型贡献目前定义为**同折加入特征后的预测指标差异**。不代表因果贡献，不计算 SHAP、置信区间或显著性。样本少、正类太少和有效折不足将提示证据不足。
- 分类阈值暂固定 0.5。真正贴片不良预警应进一步基于训练期阈值调优、缺陷召回、误剔成本和节省加工成本建立业务阈值。禁止使用验证标签调阈值后再汇报同一验证折的结果。
- 自动搜索预算是候选数/模型拟合数硬上限、时间软限制；没有任务级硬进程杀死。大数据应先限制样本/字段并评估内存、运行费用。

## 5. 目标机器验收与使用

**先备份原有 TimePilot 项目数据库和数据目录，不要删除运行库、不创建临时 Python 环境。** 使用已部署的 Python **3.12.12** 环境，安装统一 `requirements.txt`（新增 `scikit-learn>=1.5,<2`），确认 DuckDB、PyArrow、Perspective 依赖和原生浏览器资源可用：

```bash
bash TIMEPILOT.sh db-check
bash TIMEPILOT.sh source-check
bash TIMEPILOT.sh accept feature-lab-v029
```

**若使用产线文件独立检查数值模型对照：** 当前脚本要求 CSV 中已经包含**经用户核实的可用时点正确的原始字段和派生字段**；不能直接把未来标签/终检值当作特征。这条命令只验证数值评价，不证明特征从原始源表安全生成。

```bash
python scripts/featurelab29_benchmark.py --csv /data/plant_quality.csv \
  --time event_time --label-available label_available_at \
  --target quality_flag --process air_pressure,temperature \
  --features pressure_delta,temperature_absolute --group equipment_id \
  --models both --search --report /data/qa29_numerical.json
```

**分阶段的原生数据 + 数值验收：** `configured` 模式要求 `TIMEPILOT_FEATURELAB_QA_CSV` 和五个字段配置，先调用原生 v0.28 CSV→候选→DuckDB→Parquet→BI SQL 视图验收，随后对输入 CSV 中已经准备好的派生特征做 v0.29 数值评价。两条链路当前**各自独立运行**；不能称为同一已验证的生产 PIT 监督评价链路。

```bash
export TIMEPILOT_FEATURELAB_QA_CSV=/data/plant_quality.csv
export TIMEPILOT_FEATURELAB_QA_TIME=event_time
export TIMEPILOT_FEATURELAB_QA_LABEL=quality_flag
export TIMEPILOT_FEATURELAB_QA_LABEL_AVAILABLE=label_available_at
export TIMEPILOT_FEATURELAB_QA_PROCESS=air_pressure,temperature
export TIMEPILOT_FEATURELAB_QA_FEATURES=pressure_delta,temperature_absolute
export TIMEPILOT_FEATURELAB_QA_GROUP=equipment_id
export TIMEPILOT_FEATURELAB_QA_MODELS=both
export TIMEPILOT_FEATURELAB_QA_SEARCH=1
bash TIMEPILOT.sh accept feature-lab-v029 --mode configured
```

BI 原生浏览器验证仍需 `bash TIMEPILOT.sh accept native --mode configured` 和实际打开同一来源/特征物化双面板、图表切换与刷新。仅有 UI mock 截图和 BI SQL 视图无法证明真实渲染。

## 6. 下一阶段待闭合

1. 在获得**真实来源、字段字典与标签可用时点**的生产数据后，核查完整链路：导入→契约确认→特征生成→PIT物化→三组模型实验→审阅选项→新草稿重新计算→Perspective 显示，并存档来源 SHA、标签范围和执行日志。
2. 提供独立的产线时间留出（至少一个完全未用来选特征或调阈值的末端测试集）；对少数类计算 AP、召回、误剔成本；按设备和工况评价最差切片。
3. 若需要高成本时序特征，增加按需插件如 tsfresh/Featuretools，先做资源预算和多表事件时间契约；不直接在 v0.29 的默认依赖里增添大型高维计算库。
4. 将自动搜索升级到有**内层独立组验证**的 grouped_time，并加入超时杀进程、成本账本和嵌套搜索预算。当前对不支持场景显式拒绝，避免误报质量。

**发布门禁：本轮结论为“源码/分模块测试及模拟数据数值闭环可用；真实生产数据 PIT/模型对照与原生 BI 的完整验收未完成”。**
