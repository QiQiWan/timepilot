# TimePilot v0.35.6｜统一手工工具、高级算法、BI 与报告交付

日期：2026-11-13。基线：完整 v0.35.5 源码。交付：完整源码 ZIP + 仅含本轮变化的直接覆盖包（无安装器）。

## 一、实现范围与代码位置

| 项目 | 改动及复用机制 | 证据/边界 |
|---|---|---|
| 同一工作台 | `static/analysis-workspace.js`：顶部统一导航 `自动仪表盘 / 手工构造 / 高级算法 / BI 数据 / 报告与成果`；数据资产、版本保留在 `asset/version` URL 参数；历史浏览器返回恢复 | 手工/高级模式直接渲染既有 `TimePilotFeatureV2UI`，不另建执行器；切换时移除重复事件监听 |
| 手工构造 | `feature-shell.js` + `feature-builder-ui.js`：复用既有算子、公式安全编译、预览、保存、正式计算、FeatureSet 管理；按数据版本保存内存草稿 | 需要人工确认；刷新后的未保存草稿不承诺持久化，建议先保存草稿。报告列表可直达已保存 `set_id` |
| 高级算法 | `feature-shell.js` + `feature-advanced-ui.js`：在统一工作台展开 tsfresh/Featuretools 与 PIT 回接/评价入口 | 继续受控源字段、真实时间、权限和预算校验；缺高级依赖仍可完成基础分析 |
| BI | 新增 `feature-integrated-bi-ui.js`：挂载现有 Perspective Bridge；源数据与已正式物化的特征对照、视图保存、同源双面板回执 | `feature-shell.js` 对 asset/version/contract/SHA256/set/artifact 交叉核验。候选预览不能冒充正式特征；客户端回执≠独立生产数值验证 |
| 报告与资产 | 新增 `analysis-delivery-ui.js` 和 CSS：当前报告预览、Markdown 下载、不可变归档、报告版本列表/差异、特征集入口 | 使用已有 HTTP API/不可变归档服务；读档重算 SHA256，并在浏览器下载前复算 UTF-8 字节哈希；只接受当前源版本 |
| 可靠交互 | `analysis-workspace.js`、`analysis-delivery-ui.js`：版本切换源校验；刷新与工具状态通过 URL；长请求返回后检查会话仍有效；点击监听不累积 | 不存在或无权限访问的资产不得自动切换为其他资产；报告列表只显示当前分析会话 |

## 二、用户操作路径

在项目中打开 **数据分析/特征工程** 统一工作台，默认看到自动仪表盘。切换“手工构造”时，当前项目、数据和版本保持不变，使用原有受控算子/公式和草稿编辑器；切换“高级算法”时继续使用原有 tsfresh、Featuretools 和 PIT 关联流程；在“BI 数据”中加载真实 Perspective，正式特征对照须通过同源核验；在“报告与成果”中预览/归档同源报告、浏览历史版本、跳转现有特征集继续管理。

URL 示例：`/projects/{pid}/analysis?asset={aid}&version=base&tool=manual&set={set_id}`。其他工具为 `advanced`、`bi`、`reports`，删除 `tool` 返回自动仪表盘。原有 FeatureLab 旧路由保持兼容，供历史收藏和后端测试使用。

## 三、协议与安全边界

- 没有新增数据库 Schema、Python 第三方依赖、手工执行入口或可绕过的写入 API。
- 页面统一调用现有 FeatureLab 操作；正式特征计算、模型评价、PIT 回接及发布继续执行原权限、契约、安全审核。
- BI 只有产物、FeatureSet 与当前数据版本、契约修订和 SHA256 一致才加载正式结果；未核验时可以查看源数据，不能宣称正式特征已验证。
- 报告归档记录分析 Session、数据来源、特征产物和模型评价关联；文件字节不可变，并在读取和下载时核对 SHA256；跨数据版本报告不会混用。
- 无模型 API 时手工编辑、数据查询、BI、报告均通过已有非模型路径运行。

## 四、测试清单与审阅

- 后端：`tests/test_featurelab356_delivery_contract.py`；历史不可变归档、证据校验、会话恢复和自动工作流回归。本次独立执行的相关测试合计 **47 passed**；离线验收脚本的定向集合为 **32 passed**。
- 前端：`frontend/native-bi/tests/featurelab356_unified_tools.test.mjs`；含路由保留、重复点击、指定特征集定位、跨源拦截、报告 XSS、SHA256 和 BI 门禁；全量 Node **110 passed / 0 failed**。
- 源码：`scripts/verify_installation.py --source-only --strict-source` 及原始 v0.35.5 + 覆盖包一致性复核。
- 实物：DuckDB/Parquet、Perspective WASM/Chrome、本机多表 PIT 与真实算法 API，须在目标 Mac 环境完成。本次环境缺少 DuckDB 和 PyArrow，物理验收标记 **blocked**，不可视为通过；环境的浏览器策略阻止了完整 HTTP 页面导航，只有局部 Chromium DOM 渲染检查。

**审阅结论：** 统一入口、报告链路与权限复用已形成可测试源码闭环。工作台的组件级模拟测试与现有 HTTP 报告接口测试可以确认前后端契约，但尚不足以认定 Mac Chrome + 原生 Perspective、真实 tsfresh/Featuretools 以及生产 Parquet 的端到端质量通过。下一阶段重点为目标环境实机验收与工具页面的实际交互优化。
