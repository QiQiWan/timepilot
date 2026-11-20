# TimePilot v0.36.1 数据驱动功能验收

## 结论和验收边界

本版本在 v0.36.0 的发布门禁旁新增可复现的**合成数据功能验收**，不修改已有生产数据、项目数据库、Worker 运行机制、模型计算和权限控制。通过内部生成数据可检验数据导入契约、质量统计、基础特征规划、时序 PIT、标签泄漏阻断、标准模型对照、报告溯源和前端交互。所有数据文件均记录 SHA256、随机种子、源类别及预期错误。

功能测试（synthetic smoke）、真实物理端到端（DuckDB/Parquet + API + Chrome）和生产验收采用三级状态，**绝不将合成或 UCI 公开数据误报为真实工厂泛化证据**。独立的 v0.36.0 生产验收入口和正式发布门禁保持原样。

## 一、固定数据（8 个 CSV）

| 文件 | 目标模块 | 可核验的预期性质 | 特别约束 |
|---|---|---|---|
| `static_regression.csv` | 静态回归与特征评估 | 面积、房龄、房型、非线性交互及无信息噪声 | `post_sale_price__LEAKAGE_TRAP` 是结果泄漏，必须在字段契约中隔离 |
| `static_binary.csv` | 稀少不良品分类 | 固定不平衡类别、预测前变量和批次 | `post_inspection_rework__LEAKAGE_TRAP` 是后工序泄漏 |
| `industrial_pit.csv` | 工业时序、质量检测、监督评价 | 6 台设备、A/B/A/C 工况、3 次维修、事件/可用/预测/标签时刻，2,160 行 | 特征和标签在各自正确时点可用，`future_quality_label__LEAKAGE_TRAP` 禁用 |
| `sensor_unlabeled.csv` | 无监督自动分析 | 设备/时序/数值特征，但无任何标签 | 不允许显示模型性能改善百分比 |
| `dirty_import.csv` | 数据质量与异常报告 | 500 行，24 缺失、10 非法日期、8 越界、12 重复主键 | 不应静默修正原始数据 |
| `maintenance_events.csv` | 多表关联、维护重置、PIT | 6 台设备 × 3 次维修记录；第 195 条维护晚到 | 晚到维修事件不能用于预测时点之前的特征 |
| `industrial_late_measurement.csv` | 时点安全负例 | 故意放入预测后才可用的特征值 | 必须拒绝并解释 PIT 违规 |
| `industrial_early_label.csv` | 标签时点安全负例 | 故意放入预测时点之前已标记的标签 | 必须拒绝不成立的预测任务定义 |

数据完全合成、无需第三方许可、不得作为产线准确率或业务有效性宣传。生产数据不进入本验收包。

## 二、无安装执行命令

在已有 TimePilot Python 环境执行：

```bash
python scripts/featurelab361_data.py --out reports/featurelab361_data
python scripts/featurelab361_data.py --mode verify --out reports/featurelab361_data
python scripts/featurelab361_acceptance.py --mode smoke
```

- `--mode smoke`：生成并核验数据、现有 PIT 合同正负例、固定模型基准、报告与 Worker 安全回归、前端测试及源码校验。通过代表代码与模拟数据功能验收，不代表物理计算全链路或生产环境通过。
- `--mode full --with-browser --with-performance`：在目标 Mac 上使用已安装的 DuckDB/PyArrow/Perspective/Chrome，运行实际 CSV→项目导入→受控特征物化→模型对照→原生 BI，并测试 1 万/10 万/100 万行性能。缺少依赖或浏览器则 `blocked`。
- 默认输出：`reports/featurelab361_acceptance/ACCEPTANCE.json`、`.md` 和 `reports/featurelab361_acceptance.zip`。原始数据保存在独立 `reports/featurelab361_data/` 文件夹；日志 ZIP 不包含任何原始数据。
- 要保留完整固定数据用于其他电脑，只打包 `reports/featurelab361_data/` 即可；这些数据与数据库相互独立。

**前置要求**：Python 3.12、现有项目依赖，使用已有本机 Chrome。不需要创建新环境或自动下载任何软件。

## 三、公开数据的可选验收

当前代码提供一个**显式选择才联网**的公开数据获取器，不执行页面爬虫、登录或绕过访问权限。下载后将其来源、许可和文件字节 SHA256 写入旁边的 JSON。

```bash
python scripts/featurelab361_public_data.py --dataset ai4i --out reports/featurelab361_public
python scripts/featurelab361_public_data.py --dataset appliances --out reports/featurelab361_public
```

- `ai4i`：UCI AI4I 2020 Predictive Maintenance Dataset，ID 601、DOI `10.24432/C5HS5C`，CC BY 4.0，**该数据本身是合成数据**；适合不平衡分类、字段语义、泄漏规避等测试。
- `appliances`：UCI Appliances Energy Prediction，ID 374、DOI `10.24432/C5VC8G`，CC BY 4.0，真实采样的家居能源时序；适合带时间字段的回归、可视化和跨时间验证，但不是工业产线数据。

来源：https://archive.ics.uci.edu/dataset/601 和 https://archive.ics.uci.edu/dataset/374 。下载失败时标记 blocked，不制造来源记录，不使用替代镜像。遵守 CC BY 4.0 署名，保留原文件不进行任何悄悄修改。

## 四、门禁清单

| 级别 | 需要通过 | 允许结论 |
|---|---|---|
| S0：离线数据 | 8 个 CSV、SHA256、预期异常、字段映射 | 数据样本与预期检查逻辑正确 |
| S1：代码功能 | 现有 FeatureLab PIT 校验、固定模型基准、报告/Worker 回归、Node UI | 基础模拟功能通过 |
| S2：物理链路 | DuckDB、PyArrow、项目 API、真实 Parquet 产物、可复核同折指标、Perspective Chrome 回执 | 合成数据全链路功能通过 |
| S3：生产适用性 | 业务来源确证、实际设备与工况、独立最终测试集、工厂验收 | 才能讨论生产上线 |

## 五、审阅风险

1. `__LEAKAGE_TRAP` 字段故意复制了标签，系统只能在明确将其标注为受限/后验字段后防止监督学习泄漏；自动识别这类语义风险仍需真实业务字段契约及人工审查。
2. 固定数据的模型指标变化来自人为构造的规则，不代表生产精度或工业机理真实性。
3. UCI AI4I 许可记录已经公开核查；网络环境无法访问其官方地址时，不擅自提供来源不明的同名文件。
4. 项目级端到端测试若缺少 DuckDB、PyArrow、Perspective 或 Chrome，则不得计为 passed。
5. 全部生成结果位于单独的报告目录，源文件与 `TIMEPILOT_HOME` 数据库均不变。
