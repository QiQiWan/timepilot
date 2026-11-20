# TimePilot v0.36.5 — 浏览器与交互验收

## 目标与执行原则

本轮在 v0.36.4 基线上修复 UI 刷新恢复与错误反馈，并建设不依赖模拟浏览器或假数据响应的端到端验收。验收运行目录使用临时隔离 HOME，不改写现有业务项目、数据库和历史产物。数据统一由冻结种子生成，并与 `benchmarks/featurelab_v0362/BASELINE.json` 交叉核对。

## 源码改造

- `static/project-ui.js`：发布中刷新后从持久化批次恢复轮询；上传失败在表单保留 `role=alert`，可修正后重试。
- `static/data-analysis-ui.js`：数据画像排队使用数据画像自身的状态，避免特征预览任务已启动时屏蔽画像任务；服务端仍确保幂等。
- `static/analysis-workspace.js`：统一的分析任务区域增加语义标识，保持查询参数决定活跃任务。
- `static/analysis-delivery-ui.js`：缺失的数据版本给出返回数据管理的入口；加载失败跳转至系统日志。
- `scripts/featurelab365_chrome_journey.py`：实际 Google Chrome UI 动作 + 独立数据库 / Parquet / API 的数据对账 + 分阶段不可变验收记录。
- `.github/workflows/ci.yml`：打包并校验原生 Perspective JS/WASM 后，真实浏览器阶段新增 v0.36.5 门禁，失败和缺依赖禁止发布。

## 运行

项目要求 Python 3.12.12、完整 `requirements.txt`、Node 24、锁定构建的 Perspective JS/WASM 与本机 Google Chrome。依赖可在联网构建机安装；验收脚本本身不会下载浏览器，不会自动修复物理依赖。

```bash
python scripts/verify_installation.py --require-runtime --integration --require-bundle
python scripts/featurelab364_physical_acceptance.py
TIMEPILOT_QA_SCREENSHOTS=1 python scripts/featurelab365_chrome_journey.py --out reports/v0365/chrome_journey.json --timeout 180
python -m pytest -q -rs
cd frontend/native-bi && npm test
```

如本机 Chrome 不是系统默认安装路径，可以明确提供 `TIMEPILOT_CHROME_PATH` 指向真实 `Google Chrome` 或 `chrome.exe`。脚本不接受 Chromium 冒充 Chrome。报告存放 `reports/v0365/chrome_journey.json`，截图需 `TIMEPILOT_QA_SCREENSHOTS=1` 才会保存。

## 关键验收及判定

| 门禁 | 实际动作与独立证据 | 失败条件 |
|---|---|---|
| 输入基线 | 冻结 CSV 1200 行、标准 SHA256 | 漂移即失败 |
| 项目创建 | Chrome 点击创建，回读项目 API | 页面/ID不一致 |
| 数据导入 | 上传文件并观察真实后台扫描 | 超时、扫描失败 |
| 发布恢复 | 真正点击发布，在未完成任务阶段刷新页面；状态及资产 ID 连续 | 停止轮询、重复提交、资产缺失 |
| 数据一致性 | 读取真实 Parquet，并用 Python CSV 独立汇总对照 DuckDB 分组总和及记录数；签名一致 | 数据漂移、哈希不符 |
| 自动分析 | 浏览器进入数据分析，实测画像及字段直方图 | 字段/行数或图表数值不符 |
| 人工调整 | 勾选候选、真实点击保存，刷新后重新核对候选列表 | 选择丢失、静默覆盖 |
| 正式计算 | 人工勾选确认，后台物化特征产物 | 源数据或产物 SHA256 不一致 |
| 原生 BI | 真实 Perspective JS/WASM、WebSocket、页面刷新恢复工作区 | 未加载真实面板或 WebSocket |
| 报告 | UI 归档/浏览器下载，与服务端 markdown / SHA256 对账 | 报告被篡改或下载不一致 |
| 异常恢复 | 错误资产呈现可读告警及返回入口；控制台/HTTP 5xx 探测 | 页面崩溃、错误不可恢复 |

退出码：`0` 全部通过，`1` 有真实失败，`2` 依赖或浏览器无法执行（BLOCKED）。接受条件是每一步均 `passed`，且根 `status=passed`；专项测试和源码检查不能替代 Chrome 页面验收。

## 本次执行边界

当前源码改造环境只有 Chromium，没有可用的 Google Chrome；DuckDB/PyArrow/Perspective 亦未安装，且无法使用联网构建服务。因此这里只能执行离线回归及门禁自检，真实端到端报告会如实记为 `BLOCKED`。后续在使用本机 Google Chrome 的目标环境或项目 CI 运行真实测试，取得可审计的页面、计算、网络与报告证据。

本套数据均为合成数据，报告不构成工业产线实际精度证明。
