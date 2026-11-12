# TimePilot v0.24.6：Perspective 图表连续性与 BI 交互修复

本轮基线：v0.24.5 完整源码。改动全部为增量更新，保留历史项目数据、文件哈希、数据版本、分析计划、FeatureLab 资产和审批记录。

## 一、完成的修复

| 编号 | 问题与改造 | 文件 | 验收 |
|---|---|---|---|
| UI-01 | 手工切换插件后恢复原先选中的数值系列，不改筛选、排序和其它面板 | `frontend/native-bi/src/series-preservation.mjs`、`main.js`、`workspace-bridge.mjs` | 多面板、连续事件、无效字段 Node 测试 |
| UI-02 | 替换聊天和旧 BI 高级操作中的浏览器 prompt/confirm，选择字段、运算、排序依据和关联表均走受控表单 | `static/bi-dialogs.js/.css`、`bi-conversation.js`、`native-bi.js` | 静态检索及 Chromium 表单提交 |
| UI-03 | 助手可拖动、CSS 双向缩放、标题栏只保留关闭按钮，按资产和用户保存宽高 | `bi-assistant-fab.js/.css`、`bi_ui_preferences.py`、`project_migrations.py` | 迁移 v17→v18，偏好隔离，Chromium 组件 |
| UI-04 | BI 顶部使用手册；分类目录、搜索、操作步骤、常见问题 | `bi-help.js/.css`、`bi-workspace-layout.js`、`docs/BI_USER_GUIDE_CN.md` | Chromium 搜索、Esc、关闭 |
| UI-05 | 高级操作按数据查询、编辑、恢复、类型、工业分析整理，低频审批、建模二次折叠 | `bi-workspace-layout.js/.css`、`bi-conversation.js/.css` | JS 静态检查、浏览器组件 |
| UI-06 | 历史 UI 版本断言改为软件版本动态读取 | `tests/test_ui_release.py` | 当前版本回归 |

## 二、Series 修复说明

Perspective 官方文档明确指出单独执行 `restore({plugin: ...})` 会将 `columns` 重置为插件默认值。新增 `createSeriesPreserver` 监听 `perspective-config-update` / `perspective-layout-update`，对每个面板独立保留用户最近选择的系列。只有发生插件变更时尝试重新应用兼容字段；采用 `restore({columns},{panel,suppress_errors:true})`，不重新创建 Viewer、不切换数据来源、不重置布局。若目标插件拒绝字段，保留 Perspective 原生默认值。`bi.chart.plugin.set` 的 Agent 操作只修改插件属性。

**未确认的部分**：当前执行环境缺少 Perspective 5.6.1 JS/WASM 已编译包，故本次测试中的切换是公共 API 行为的模拟测试，未证明真实 Rust Viewer 全链路中的所有插件组合都通过。目标机必须用真实数据验证 Line → Bar → Area → Scatter、Datagrid → Line 和多面板场景。

## 三、测试方法

```bash
python -m pytest -q tests/test_bi_ux_preferences_v0246.py tests/test_ui_release.py
npm --prefix frontend/native-bi test
python scripts/accept_bi_ux_v0246.py --output reports/v0246/bi-ux-chromium.json
bash TIMEPILOT.sh source-check
```

新增统一执行入口：`bash TIMEPILOT.sh accept bi-ux-v0246`；目标机强制真实环境门禁：`bash TIMEPILOT.sh accept bi-ux-v0246 --mode configured`。

本轮离线测试记录：Python 联合回归 34/34 通过（包含静态契约验收）；Node 37/37 通过；系统 Chromium 组件检验通过（手册搜索与关闭、字段弹窗提交、浮窗单按钮和显示状态）。

Python 3.12.12 + DuckDB + PyArrow + Perspective 编译资源的目标机真实 E2E 仍作为独立发布门禁。请不要将 Chromium 组件测试解释为真实数据分析全链路通过。

## 四、重点手动验收

1. 在原生 Line 图中从字段侧栏拖入 A1、A3、A14，切换 Bar 和 Area，三个数值系列保持；改变字段选择后再次切换，应以最近手工选择为准。
2. 配置 Group By、Split By、过滤器，切换图表后仍保持；对不支持的 Scatter 给出提示，不产生空面板或字段越权。
3. 点击「按时间汇总」，直接显示可选日期、数值列、间隔和聚合方法，提交后检查真实结果。应用内不得弹出浏览器原生提示框。
4. 打开分析助手，桌面端用鼠标调整大小并拖动位置；关闭后重新打开，同一资产恢复宽高；其他用户/资产隔离。
5. 点击顶部「使用手册」，按目录浏览、搜索关键字、Esc 退出，退出后原 Perspective 配置保持。
6. 检查「高级操作」包含查询、编辑、历史、字段类型和设备事件等原入口，均不影响普通图表操作。

## 五、本次交付的实际验收记录（2026-11-11）

| 验收项 | 实测结果 | 判断 |
|---|---|---|
| Python 资产/摘要/分析/FeatureLab/交互回归 | 34/34 通过 | 通过（当前容器） |
| 前端 Node 组件回归 | 37/37 通过 | 通过 |
| 单组件真实 Chromium：使用手册、表单、浮窗 | 通过，无 JS 页面异常 | 组件通过 |
| `bash TIMEPILOT.sh accept bi-ux-v0246` | 返回 0 | 源码级通过 |
| `bash TIMEPILOT.sh source-check` | 返回 0 | 清单版本一致 |
| `bash TIMEPILOT.sh accept bi-ux-v0246 --mode configured` | 返回 2，环境阻塞：当前 Python 3.13.5，目标 Python 3.12 | 未通过完整链路 |

须在用户机器上对已有 Perspective 5.6.1 编译包、DuckDB、PyArrow 与真实数据执行下一轮完整浏览器测试。特别要验证 `Y Line → Y Bar → Y Area` 字段连续性，非数值列与散点图兼容规则，以及工作区保存与刷新后恢复。图表插件切换后成功保留多系列目前仅由官方公共接口规则和模拟 Viewer 测试支持，**不代表已完成真实 5.6.1 引擎验收**。

**回滚方式：**启用历史工作台入口或恢复 v0.24.5 代码，并将运行数据库通过升级前备份恢复；禁止让旧版代码直接写已升级的 v18 SQLite 数据库。保留原始资产及训练版本。
