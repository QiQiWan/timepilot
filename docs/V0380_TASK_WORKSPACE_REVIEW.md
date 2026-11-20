# TimePilot v0.38.0 — 任务化工作台源码迭代与系统审阅

日期：2026-11-17。基线为 v0.37.9 源码候选包；在保留其数据治理、建模、原生 BI 和安全恢复功能的前提下，集中修复分析工作台的状态与交互。**当前交付类型为 source-only candidate；实机运行就绪和企业生产适用性尚未验收。**

## 1. 主要判断

1. 现有项目、数据画像、统计、特征工程、模型与报告链路功能较多，但原有 UI 存在“概况页自动请求特征预览、成果页装载特征构造器”的额外后台工作。v0.38.0 通过按需初始化减少了这种隐含行为；无法据此声称实机查询延迟已下降，需要在真实服务上量化。
2. 同名字段在版本切换后容易产生误导。统计页面现在用 `project/asset/version/source SHA256/contract revision` 五元组对 URL 状态进行绑定，超出绑定的旧筛选、字段选择、卡片选择不予恢复。图表请求仍沿用之前基于版本和筛选 SHA 的响应校验与 AbortController 失效处理。
3. 超过 24 列的表现在允许**明确选择**参与统计的 1–24 列，替代无操作入口的默认前 24 列。计数、分母、相关分析、多字段箱线图与导出报告根据同一 `field_ids` 保持口径；独立图表卡片可选择其他字段但始终绑定相同数据来源与筛选条件。计算预算未增加；当前仍需工业数据实测。
4. 检查固定/测试输入：重复、空、非字符串和超限字段列表，以及不合约的图表、筛选和关联字段，在 DuckDB 执行前拦截。API 请求类型错误由请求 schema 返回 HTTP 422。生产场景的所有 422/409 错误映射仍需真实服务逐项测试。

## 2. 文件级实施清单

| 文件 | 变化 | 验收证据 |
|---|---|---|
| `src/timepilot/feature_dashboard_v0372.py` | 统计字段列表、关联字段、筛选操作、图表参数输入的类型与预算验证 | `tests/test_featurelab380_field_scope.py`，纯服务/协议测试 |
| `src/timepilot/static/data-dashboard-v0372.js` | 24 列可选统计范围、URL 五元绑定、统一请求/导出字段、口径不一致拒绝显示、连续勾选字段 | Node 交互测试与实际 Chromium DOM 测试 |
| `src/timepilot/static/data-analysis-ui.js` | 只在 features 任务主动启动特征预览；防止离开页面后的迟到请求再次启动 | Node 交互测试、Chromium 模拟 API |
| `src/timepilot/static/analysis-workspace.js` | 成果页独立展示已保存结果；避免触发完整特征工作台；限制跨项目资源回退 | Chromium DOM 测试 |
| `src/timepilot/static/analysis-workspace.css` | 特征与成果独立可见区域，字段选择在滚动/多选时保持可操作 | Chromium DOM 测试 |
| `src/timepilot/static/index.html`, `pyproject.toml`, `src/timepilot/__init__.py`, `frontend/native-bi/package.json` | 统一版本至 0.38.0，静态资源查询参数随版本更新 | `source-check`、版本一致性检查 |
| `scripts/release_gate_v0380.py`, `scripts/launcher.py`, `scripts/BUILD_OFFLINE_RELEASE.sh` | 本轮源码专项回归、沿用严格 physical/offline/browser 门禁；发行命令指向当前版本 | 源码门禁与已知环境前置阻断 |
| `scripts/featurelab380_chromium_dom_qa.py` | 使用已安装 Chromium + Playwright 加载真实前端资源，在模拟 API 下做 30 次操作回归，不下载浏览器 | 独立 DOM 证据 JSON |
| `.github/workflows/ci.yml`, `.github/workflows/offline-release.yml` | 新增 0.38.0 专项测试，并更新版本化制品名 | YAML/源代码静态检查；本轮未运行真实远端 Actions |
| `scripts/release_integrity.py` | 新版代码、测试和审阅文档均成为来源清单必需文件 | 打包、解压后校验 |

## 3. 固定验收场景和口径

- **统计字段**：31 列数据默认首 24 列，勾选时保持面板展开；取消某列后可以加入第 25 列；提交 SQL 的 `field_ids` 与 URL、指标 `metric_scope.field_ids` 一致。
- **跨版本**：当五元来源绑定变化时，旧 URL 中 `da_filters`、`da_fields`、`da_corr`、`da_box`、`da_cards` 不复用；重新从当前字段契约初始化。
- **后台任务**：从概况/质量/统计之间切换不自动 POST 特征预览。用户进入 features 才执行检查/必要的启动。提前退出后，迟到的预览 GET 不能自动启动任务。
- **成果交付**：打开 outputs 独立装载成果阅读器，保持源码中已有的同源报告/特征集机制；不要求先装载完整特征构造 UI。
- **图表竞争**：继承 v0.37.9 的请求取消、epoch 和跨版本响应屏蔽；实际后端取消与数据库查询释放仍需实测。
- **异常输入**：空/重复/非字符串/超预算字段，非预期图表字段及非法筛选操作必须可解释地报错，避免 HTTP 500。

## 4. 测试证据及解释边界

- `node --test frontend/native-bi/tests/*.test.mjs`：146/146 通过（最终交付结果以外部 QA 记录为准）。
- `python -m pytest -q tests/test_featurelab380_field_scope.py`：新协议及异常输入测试；与旧统计/安全/交付组合回归需再次执行并归档。
- `python scripts/featurelab380_chromium_dom_qa.py`：在**已安装** `/usr/bin/chromium` 上执行真实 DOM 操作，加载实际 JS/CSS，模拟接口；30 次“质量/统计”切换与任务按需加载被检查。它**没有连接** DuckDB、Perspective、真实 HTTP Server，也不能代表线上 Mac 浏览器性能与 SQL 数值一致性。
- 目标 Python 版本为 **3.12.12**。若本执行机为 3.13.x，缺 DuckDB、PyArrow、Perspective、npm lock、已编译 JS/WASM 或目标 Chrome，则 `physical`, `offline`, `browser` 应返回 `BLOCKED`。自动化跳过不等于通过。
- 未对真实 MES 数据的未来可用性、标签误差、模型业务收益或企业级鉴权隔离做充分测试。合成/模拟数据只用于功能回归，不可作工业收益依据。

## 5. 系统级审阅：仍需关闭的问题

| 优先级 | 问题与风险 | 下一步实作及关闭条件 |
|---|---|---|
| **P0** | 当前候选源包尚无经过审查的 npm lock、完整原生 Perspective JS/WASM、Python 3.12.12 平台 wheelhouse | 在目标 Mac 构建；`npm ci` 与 SHA 可复现；原生 BI 真实加载、过滤、绘图；完成断网安装与恢复 |
| **P0** | 本环境不具备可证明的 DuckDB/Parquet 全链路 SQL 对账 | 12 行反例、506 行复现与 1 万/10 万/100 万行数据逐字段 SQL 对账，合法操作零 500，非法输入归为 422/409 |
| **P0** | 模拟 Chromium DOM 验收无法覆盖原生 Perspective、真实网络与授权 | 目标 Chrome 30 次同文档切换、API/WebSocket、刷新/返回、项目切换、权限验证及浏览器控制台零严重错误 |
| **P1** | 统计 24 列预算依旧是硬上限，更多字段需手工分批；当前关联图对设备/工况可解释性有限 | 新增按字段组批次统计与结果页明确“已选字段/分母”；采用分层独立数值基准 |
| **P1** | UI 模块继续保留历史兼容层；真实后端长任务取消是否释放资源未证实 | 统一路由状态机与懒加载边界、加请求 trace/epoch、测试暂停/刷新/后退不出现重复任务 |
| **P1** | 后台 Worker 仍与服务进程耦合；资源隔离、恢复时间与运维 SLO 缺实测 | 独立 Worker、租约恢复注入、1M 行下 p50/p95/RSS、RTO/RPO 正式记录 |
| **P1** | 安全与产业收益证据仍未满足企业生产门槛 | RBAC/多租户/数据出站审计；真实 MES PIT/留出设备/时间外推；同折基线和业务成本复核 |

## 6. 下一版本计划

1. **v0.38.0 验收关门**：在真实 Python 3.12.12 + DuckDB + Chrome 目标环境，运行 source/physical/browser；修正发现的真实 500 和查询口径差异，确保原数据不动。
2. **v0.38.1**：工作台保存视图/撤销/恢复与 Worker 进程隔离，加入真实数据 p95、资源预算、失败恢复；完成 10k/100k/1M 性能表。
3. **v0.39.0**：与设备温度、早期不良拦截和设备异常等业务场景对齐；只在标签时间与特征可用时点合法时评价模型收益，保留负收益与无标签的真实显示。

### 执行（源码候选，不会修改已有用户数据）

```bash
bash TIMEPILOT.sh source-check
bash TIMEPILOT.sh release-gate --mode source
node --test frontend/native-bi/tests/*.test.mjs
python -m pytest -q tests/test_featurelab380_field_scope.py
python scripts/featurelab380_chromium_dom_qa.py --out reports/v0380/dom_browser_qa.json
```

`source` 门禁只覆盖源码内容和精选测试；`physical`, `browser`, `offline` 通过之前不得将本版本标记为生产就绪。
