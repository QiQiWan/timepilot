# v0.37.7 当前验收说明：先执行真实 DuckDB/Parquet 门禁

```bash
python scripts/featurelab377_dashboard_acceptance.py
python scripts/featurelab377_dashboard_performance.py
```

缺少真实 DuckDB 的结果为 BLOCKED。生产发布状态保持阻断，直到物理 SQL 和 Chrome 实机验收闭合。

## v0.37.7 证据报告门禁

```
python scripts/featurelab373_evidence_acceptance.py --out reports/v0373/evidence_acceptance.json
python -m pytest -q tests/test_featurelab373_evidence.py tests/test_featurelab372_real_duckdb.py
node --test frontend/native-bi/tests/featurelab373_evidence_ui.test.mjs
```

该命令区分 `passed`、`failed`、`blocked`。本地无 DuckDB 时返回退出码 2，不能用合成数据结论替代真实 Parquet 对账。只有数据与字段契约、数值、模型同折和报告 SHA256 均一致才能发布对应结论。

# TimePilot v0.37.2 当前验收

v0.37.1 还要求在已安装 Google Chrome 的机器上运行 `python scripts/featurelab371_chrome_cycles.py --out reports/v0371/chrome_30_cycles.json`，以同一页面 30 次源表/正式特征往返的真实 WebSocket 和双面板回执证明稳定性，缺依赖必须 BLOCKED。

v0.37.0 另外要求 `python scripts/featurelab370_fetch_public.py --out data/public/nab` 获取官方文件，运行 `python scripts/featurelab370_real_data.py --source nab --input data/public/nab --out reports/v0370/real_nab --require-physical`，再运行 `python scripts/featurelab370_performance.py --real-csv reports/v0370/real_nab/industrial_normalized.csv --repeats 2 --require-physical`。公网或 DuckDB/Arrow 缺失时真实数据验收为 BLOCKED，合成数据和独立在线核对不能替代实际平台实算。

请运行 `python -m pytest -q -rs`、`node --test frontend/native-bi/tests/*.test.mjs`、`python scripts/featurelab362_acceptance.py --mode smoke`，以及 `python scripts/featurelab364_physical_acceptance.py`。真实 DuckDB/Parquet 和 Chrome/Perspective 环境缺失时必须标记 BLOCKED，不能视作生产通过。保留 v0.36.2 冻结基准，不得修改基准以消除失败。

## v0.33.3 - reliability acceptance gates

Run `python -m pytest -q tests/test_featurelab333_resilience.py tests/test_featurelab332_real_duckdb.py` in the existing Python 3.12.12 environment, then `node --test frontend/native-bi/tests/featurelab333_resilience_ui.test.mjs` and verify `bash TIMEPILOT.sh source-check`. The first test uses controlled model responses and mock artifact bytes. The DuckDB tests MUST actually pass. Also independently verify real macOS Chrome/Perspective charts and file SHA256 against a real Parquet artifact. A reported two-panel client receipt is insufficient for production acceptance. Review user revocation, tab-close continuation, retried failed job, stale sources, and report hash in [v0.33.3 release review](FEATURELAB_V0333_RELEASE_REVIEW.md).

---

# TimePilot v0.33.1｜工作台专项验收

在已有 Python 3.12.12 环境运行：

```bash
python -m pytest -q tests/test_feature_workbench_contract_v033.py tests/test_feature_workbench_v033.py
node --test frontend/native-bi/tests/featurelab331_workbench.test.mjs frontend/native-bi/tests/featurelab331_interaction.test.mjs
```

后端三项 DuckDB 实测用例必须执行（不得出现 `skipped`），并在本机 Chrome 中完成真实数据图表值、刷新、后退与版本切换检查。当前隔离交付环境只完成源码、SQL 契约、前端控制器和模拟数据浏览器渲染，尚未取得 Mac 端真实计算验收结果。详见 [审阅记录](FEATURELAB_V0330_V0331_RELEASE_REVIEW.md)。

---

# TimePilot v0.32.4｜当前验收与发布门禁

**本地源码验收：**

```bash
bash TIMEPILOT.sh db-check
bash TIMEPILOT.sh source-check
bash TIMEPILOT.sh accept feature-lab-v0324 --mode offline
bash TIMEPILOT.sh
```

**源码通过不代表现场验收。** 必须分别记录模型配置有效/无 API/超时/越权响应、真实 DuckDB/Arrow Parquet 物化、训练折内无泄漏模型评价、Perspective 真浏览器双面板、真实工况及设备切片、报告与真实物化文件 SHA256、人工使用验收。任何一项无可信回执，生产发布状态保持阻断。

`accept feature-lab-v0324 --mode configured` 仅在具备独立、真实可复核的 E2E 发布脚本和所有证据时可用于生产签核；源码单测不能代替。目标环境 Python 3.12.12、Node 24，BI 所需真实 JS/WASM 及 npm 锁文件需先构建并校验。所有原项目数据库均应备份、执行非破坏性升级，禁止为通过测试删除历史数据。

**v0.32.4 P0/P1 新增重点：**真实全量排序键/实体键唯一性检查；无时间顺序时不自动生成窗口；200,000 行以上启用受控后台 SQL、超过 2,000,000 行阻断要求分区策略；任务意图区分连续比率/分类与探索；监督结果反馈只作为人工复核且必须同产物 SHA。

**历史 v0.32.2 重点：**确认监督任务时间契约，物化失败/产物损坏、伪造任务回执、同源哈希不一致均阻断；客户端 BI 回执只能归类为 `client_reported`，不得作为生产浏览器验收。


---

## 历史版本说明（保留供追溯；操作请以上文为准）

# TimePilot v0.5.13｜原生 BI 集成验收

## 在目标 Mac 执行

先激活 TimePilot 当前 Python 3.12.12 的 conda 环境，停止旧服务，再进入项目根目录。验收使用已经安装的 Google Chrome，不会下载浏览器，也不会截屏。

```bash
python scripts/verify_installation.py --require-runtime
bash TIMEPILOT.sh accept native 2>&1 | tee acceptance_v0511.log
```

脚本执行顺序：

1. **安装完整性**：验证每个源码文件的 SHA-256、版本、`sdk.py`、实际 Python 导入位置。
2. **Python 回归**：测试数据、任务、API、版本编辑、DuckDB/Perspective 接口（可选依赖已安装时执行真实测试）。
3. **Perspective 构建**：前端源码/JS/WASM 哈希一致则复用；缺失或变更才执行 npm/Vite 构建。
4. **集成依赖**：检查 Python 3.12.12、DuckDB、Arrow、Perspective Virtual Server 5.6.1、浏览器资源。
5. **现有 Chrome**：检测本机 Chrome 路径；无需下载 Chrome for Testing。
6. **浏览器闭环**：真实 Parquet、HTTP、WebSocket、网格、透视、图表、保存/刷新、编辑/提交、回滚校验、实时流。

Windows 可运行 `TIMEPILOT.bat accept native`。

## 查看失败原因

`acceptance_v0511.log` 保存终端输出；`reports/native_bi_acceptance.json` 保存结构化阶段、失败摘要、HTTP 错误、浏览器 JS 错误及 WebSocket 状态。验收子进程最多运行 `TIMEPILOT_ACCEPTANCE_MAX_SECONDS`（默认 300 秒），超时后保留最后阶段。

若停在最前面的 `verify_installation.py`，优先从完整 v0.5.13 压缩包修复源码，不建议删除 conda 环境或重新安装 Chrome。

若停在 `perspective-python-table-registry`，检查真实 Python 客户端能否 `get_hosted_table_names()`、`open_table()` 和 `table.view().to_json()`。只有注册、创建视图与数据读取全部成功，才有条件继续浏览器 UI 测试。

若停在 `perspective-websocket-and-datagrid`，通过结构化报告中的 `python_virtual_table`、`browser_virtual_table`、`page_state`、`websockets` 确定表名、通信与前端渲染是否一致。

## 本版重点回归

- `ModuleNotFoundError: timepilot.sdk`：源码清单会在 pytest 前定位缺失文件。
- `Unknown table "timepilot_active"`：前端使用 Python Virtual Server 实际公开的全名。
- `Referenced column "rowid" not found`：DuckDB VIEW 使用 `__tp_rowid` 自然行序。
- npm 构建偶发异常：复用 node_modules 之前核对顶层依赖实际版本，并校验构建输出的 ESM 公共接口、WASM 与 SHA-256。
- SQLite 旧库字段缺失：启动报出具体不兼容的表与字段，保留原有业务数据。

## 限制

仅收到此前 v0.5.10 在测试收集阶段的失败日志。本版在非目标环境执行的 Python/JavaScript/Chromium 局部测试不能替代 Mac 上的完整 Perspective 5.6.1 + DuckDB + Chrome 验收。只有完整日志和 JSON 报告全阶段通过，才能认定原生 BI 浏览器闭环完成。


### v0.37.2 实际数据统计

`python scripts/featurelab372_dashboard_acceptance.py` 使用真实 DuckDB Parquet 查询独立核对 12 行固定合成数据的聚合、筛选、分位数和版本变化。退出 0 才能认定物理通过；缺依赖退出 2 为 BLOCKED。源码内模拟前端测试不得替代真实 Chrome 验收。
