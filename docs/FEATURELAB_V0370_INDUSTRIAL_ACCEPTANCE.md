# TimePilot v0.37.0：真实工业数据契约、跨时间/跨设备验证及性能基准

## 1. 当前版本新增什么

- `industrial_dataset_v0370.py`：公开来源及企业 CSV 适配；时间、设备、标签事件和标签到达时间严格区分；设备内因果 lag/过去窗口；输出无损的来源 SHA256 和归一化数据契约。
- `industrial_eval_v0370.py`：固定切分与独立验证；训练/验证/测试保持行隔离；企业数据按标签真实可用时点限制训练数据；公开事后标注只运行不使用标签训练的异常基线。
- `industrial_physical_v0370.py`：真实 DuckDB 写入 Parquet，使用 Pandas 和 PyArrow 对记录数、异常数、逐设备逐时桶计数/均值做独立复算。
- `featurelab370_fetch_public.py`：从官方 NAB 仓库读取真实 EC2 设备监控数据和官方窗口 JSON；文件必须匹配固定 Git blob SHA1，保存下载后的 SHA256，不依赖本地生成文件。
- `featurelab370_real_data.py`：输出 `industrial_normalized.csv`、`DATA_CONTRACT.json`、`CROSS_TIME_DEVICE_EVAL.json`、`PHYSICAL_QA.json`、`ACCEPTANCE.json`；可以继续在 TimePilot 项目管理中导入 `industrial_normalized.csv`，按契约确认时间、设备和标签字段。
- `featurelab370_performance.py`：每个规模在独立子进程测量 wall-clock、CPU 机型/Python 版本、峰值 RSS、相对基线的内存增量、真实 Arrow/Parquet 阶段状态；每个实验重复执行并比对原始 CSV SHA256。

## 2. 公开真实数据及边界

| 来源 | 原始情况 | 主要使用场景 | 标签定义 | 适用性边界 |
|---|---|---|---|---|
| NAB realAWSCloudwatch EC2 CPU | Numenta/NAB 的 8 台 EC2 实例 CPU 监控时序；官方 `combined_windows.json` | 多设备异常检测、时间隔离、设备独立留出 | **人工/事后整理的异常窗口**；`label_event_at` 为窗口左端，不表示真实故障发生/报告生成 | 研究回放；无官方逐行 `label_available_at`，不得用于证明历史时点的监督标签可用或企业预测增益 |
| UCI MetroPT-3 | 运行中地铁列车空压机 APU，约 151.7 万条，含压力、油温、电流等 | 真实大数据集导入、时序、单设备故障回顾、内存压测 | 官方公布的四个漏气事件时间窗口；由适配器回贴到采样时刻 | **仅一台压缩机**，不能用它证明跨设备泛化；原始数据不是逐行带标签；缺少官方标签回执时间 |
| 企业工艺 CSV | 设备 ID+传感量+已确认标签+事件发生和报告到达时点 | 明确 PIT 的质量/异常分类，跨时间+未见设备测试 | 业务部门提供发生时点、结果可用时点，禁止使用未经确认的字段 | 必须对采集系统时钟、设备标识、缺失/采样间隔及质量追溯规则做现场核验 |

公开来源：
- NAB https://github.com/numenta/NAB （MIT 仓库，研究数据应按来源署名）
- UCI MetroPT-3 https://archive.ics.uci.edu/dataset/791/metropt+3+dataset （CC BY 4.0，署名论文及仓库）

为防止下载源漂移，NAB 的 9 个文件锁定在 `benchmarks/featurelab_v0370/PUBLIC_NAB_PIN.json`；只需官方 CSV 与 JSON，**无需下载整个 Git 仓库**。数据通常需在有公网权限的目标设备下载，软件发行包中不捆绑 200 MB 级以上公开数据，也不收集企业原始数据。

## 3. 工业字段契约

| 标准化字段 | 类型及作用 | 约束 |
|---|---|---|
| `device_id` | 文本，设备或产线实体主键 | 不可缺失；同一设备、同一观测时刻不得重复 |
| `observation_time` | 观测/模型决策时间 | 必须有可靠顺序；企业 CSV 必须声明 IANA 时区（如 `Asia/Shanghai`） |
| `measurement_available_at` | 传感量进入当前决策范围的时点 | 必须早于或等于对应的模型决策时点；否则阻断 |
| `sensor_value` / `signal_*` | 经显式白名单确认的过程量 | 不允许把标签或结果后的质量字段混入输入 |
| `event_label` | 0/1 二分类结果 | 映射规则必须经业务确认；仅在官方异常窗口中标注为 1 不等于真实失效 |
| `label_event_at` | 正样本对应的事件/异常区间起始时间 | 正样本必填；不得凭空将事件起点等于标签可用时点 |
| `label_available_at` | 标签真正进入业务系统的时间 | 企业 PIT 监督要求每行有据可核；公开研究数据**留空**，禁止伪造 |
| `*_lag1`, `*_past3_mean` | 设备内因果特征 | 只读当前设备以前的观测，严禁跨设备串值、未来窗口和标签回填 |

企业契约示例在 `examples/featurelab370_factory_schema.example.json`。请把键的 **值** 替换为企业真实 CSV 表头，而后运行脚本，不会自动推断时区或伪造标签回执。

## 4. 运行命令

先检查当前 Python 3.12.12 环境的依赖，已有依赖不需要重复安装：

```bash
python scripts/verify_installation.py --require-runtime --integration
```

NAB 来源下载（需要访问 GitHub；已存在的符合签名文件可离线校验）：

```bash
python scripts/featurelab370_fetch_public.py --out data/public/nab
python scripts/featurelab370_real_data.py --source nab --input data/public/nab --out reports/v0370/real_nab --require-physical
```

UCI MetroPT-3：从 UCI 下载后放到本地，不要求将其传上公网；真实 CSV 至少包含 `timestamp`、`Motor_current` 和另外两个官方传感字段。

```bash
python scripts/featurelab370_real_data.py --source metropt3 --input data/public/MetroPT3\(AirCompressor\).csv --out reports/v0370/real_metropt --require-physical
```

企业现场数据：

```bash
python scripts/featurelab370_real_data.py --source factory --input /path/to/measurements.csv --schema examples/featurelab370_factory_schema.example.json --out reports/v0370/factory --require-physical
```

内存与性能：

```bash
python scripts/featurelab370_performance.py --rows 10000 50000 200000 1000000 --repeats 2 --require-physical --out reports/v0370/synthetic_performance.json
python scripts/featurelab370_performance.py --real-csv reports/v0370/real_nab/industrial_normalized.csv --repeats 3 --require-physical --out reports/v0370/nab_performance.json
```

其中 `--require-physical` 要求真实 DuckDB/PyArrow 执行，缺依赖返回 `blocked`；没有它时仍可测 CSV、因果特征和模型，`physical_status` 会清楚记录未运行。真实数据性能命令必须在 `--real-csv` 同目录找到且核对 `DATA_CONTRACT.json` 的 SHA256；不得任意 CSV 冒充真实数据。

## 5. 评价协议

- **跨时间**：按照**不重叠的唯一观测时点**划分 60% 以前训练、20% 验证、最后 20% 测试；同一时点的设备记录不被拆到不同集合。企业 PIT 数据训练行需要在第一条验证/测试数据开始前已经得到真实标签回执。
- **跨设备**：独立留出全部验证设备与测试设备的记录。企业 PIT 数据进一步要求训练只使用较早的设备数据，测试留出设备在未来 30% 时间段；这属于 **新设备+未来时间** 双重隔离。NAB 的独立留出设备（`ac20cd`, `fe7f93`）在锁定源清单中事先指定，与实验结果无关；它只证明设备组隔离。
- **模型方案**：企业 PIT 数据固定线性逻辑回归基线，分别对原始特征和因果派生特征评价，缩放/填充值只从训练集拟合；公开事后标注**不使用异常标签训练模型**，只做训练段稳健尺度异常评分，然后在独立段用官方异常窗口回顾性计算 AP / ROC-AUC 和分类计数。
- **不可评判情形**：测试折只有单一类别，AP/ROC-AUC 相关结论需明确为不可判定；样本不足或无官方时间/标签证据时返回 `blocked` 或 `insufficient_class_coverage`，不以 0 代替空结果。

标签可用性约束：

\[
\max_{i\in\mathcal{D}_{\mathrm{train}}} t^{(i)}_{\mathrm{label\ available}} < \min_{j\in\mathcal{D}_{\mathrm{valid/test}}} t^{(j)}_{\mathrm{decision}}
\]

这里 `label_available_at` 指独立记录的结果真正出现在业务系统中的时间；对于只有事件起止时间的公开数据，这一约束**无从证实**，因此不做监督生产预测声明。

## 6. 性能证据与验收判定

- 合成压力数据用于测量相同机器上不同规模的**耗时与峰值 RSS**，原始输入由固定种子生成；不能充当真实工业模型精度。
- 使用真实工业源时，保留源文件 SHA256、归一化文件 SHA256、最终 Parquet SHA256 和各折行索引摘要；将误差、类别支持数、结果不可测的原因完整输出。
- `physical` 门禁实际用 DuckDB 与 Arrow 读取 Parquet，并与 Pandas 的独立 CSV 计算比较逐设备逐小时记录数、标签数、均值。只查文件是否存在不算通过。
- 运行报告的 `status=passed` 必须同时有跨时间和跨设备可测结果，并在请求 `--require-physical` 时有 DuckDB/Arrow 的通过证据；`partial` 和 `blocked` 不属于验收通过。
- 现场发布还需要项目导入与已安装 Chrome 的 v0.36.5 浏览器全流程验收。本模块的 API/CSV 合约和离线计算通过不自动代表网页端全部成功。

## 7. 当前源码执行条件

本次源码审核的 Linux 容器具有 NumPy / Pandas / scikit-learn / psutil，但缺少 DuckDB 与 PyArrow，且容器无法联网下载官方公开数据。故可真正执行：固定种子下的模型对照、数据契约/未来泄漏反例测试、合成压力数据的时间/RSS；公开物理数据、原生 Parquet、现场 Chrome 数据流本次仍应记为 **BLOCKED**。在允许访问 GitHub/UCI 的 CI 或企业目标机上执行后，方可增加正式真实数据结论。

## 8. v0.37.0 最终收束：真实数据独立数值基准与性能硬门禁

**并列分数 AP 修正。** 旧的统一分类指标按预测值降序后逐行求 AP，存在重复数值时可能因 CSV 排列次序不同而产生不同结果。工业异常评价改用 `sklearn.metrics.average_precision_score` 的并列阈值聚合规则，ROC-AUC 同样保持对样本顺序不敏感。固定回归用相同打分值的正负样本乱序反例验证。历史通用评分接口暂不修改，以免影响其他模块的既有科学基准。

**独立 NAB 来源与模型结果锁定。** `benchmarks/featurelab_v0370/NAB_INDEPENDENT_ORACLE.json` 保存了独立于 Python 系统、通过官方 GitHub 原始文件审阅所得的 8 实例/32,256 行、2,760 条窗口标注，以及固定跨时间和留出设备折的 AP/ROC-AUC。新的 `featurelab370_verify_oracle.py` 重新验真九个原始文件的 Git Blob，再核对归一化 SHA256、字段契约、分区计数、每台设备标注数及模型数值（默认 AP/ROC-AUC 绝对容差 0.004），并写出 `NAB_ORACLE_QA.json`。实测偏离时总验收失败。此独立来源基准**不能代替** DuckDB/Arrow 物理执行。

**回顾性跨设备局限。** `temporal_transfer_audit` 明确输出训练最晚时点、各留出设备最早测试时点，以及是否满足训练早于两个测试分区。在官方 NAB 固定留出设备上，部分训练设备包含晚于测试设备的观测，因此只作为回顾性设备迁移对照，`prospective_transfer_claim_allowed=false`。真正企业在线泛化必须另行具备真实标签收据及时间先后约束。

**可执行性能预算。** 新增 `--max-peak-rss-mib`、`--max-model-seconds`、`--baseline-report` 和 `--max-regression-factor`。同机同环境同数据可自动判定耗时、峰值 RSS 是否退化；Python 版本、平台、CPU 数或输入种子不同则返回 `blocked`，禁止宣传与历史版本的性能改善。CI 默认实际百万行上限 3,072 MiB 和模型阶段 120 秒，**是验收防退化阈值，不是用户机器上的性能承诺**。

```bash
python scripts/featurelab370_fetch_public.py --out data/public/nab
python scripts/featurelab370_real_data.py --source nab --input data/public/nab --out reports/v0370/real_nab --require-physical
python scripts/featurelab370_verify_oracle.py --raw-root data/public/nab --artifacts reports/v0370/real_nab
python scripts/featurelab370_performance.py --rows 10000 50000 200000 1000000 --repeats 2 --require-physical --max-peak-rss-mib 3072 --max-model-seconds 120 --out reports/v0370/scaling.json
```

**验收边界**：本会话的隔离环境中无法下载官方源文件，且缺少 DuckDB/PyArrow。因此官方 Python 真实数据与物理计算门禁继续 `BLOCKED`；独立官方数据复核已经完成，使用的机器及算法与本系统物理运行证据必须分开披露。

**特征价值报告**：每个模型与切分均生成 `feature_value_comparison`。只依赖验证集 AP 选择原始特征或派生特征，最终测试集差异仅用于事后评价；任何最终测试退化均明确标记，不以候选数量代替价值证明。`chronological_split_protocol_satisfied` 表示时间约束满足，`prospective_transfer_claim_allowed` 在没有现场独立时钟及业务回执核验前始终为 `false`。

小样本端到端性能工况如果因设备留出不足而只能完成部分模型评价，父进程必须保留已经计算的耗时和峰值 RSS，同时将总结果标为 `partial`（退出码 2）；不会将其错误记录成运行崩溃或完整通过。退出码 1 仅用于失败，`blocked` 也以退出码 2 报告并附带阻断原因。
