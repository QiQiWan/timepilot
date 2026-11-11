# TimePilot v0.5.13 完整文件体系与联动稳定性修复

## 日志复核

1. `acceptance_v0510(2).log`：Python 3.12.12 和 0.5.11 release manifest 已加载；五个隐藏文件在目标工程缺失，S0 失败。v0.5.11 完整 ZIP 中均存在这些文件。因此需要全量安装，不能通过 `cp *`、不含隐藏文件的同步脚本或单独替换 Python 模块恢复。
2. `acceptance_v0510(1).log`：148 项 Python 测试通过、3 项跳过；Python Perspective registry 及 Chrome 的 Datagrid、Pivot、Chart、Save、Restore 均 PASS；在 `edit-preview-and-commit` 发生 `View not found` 随即 Rust `unwrap()` panic。

## v0.5.13 修改

- `frontend/native-bi/src/session-lifecycle.mjs`：远程会话先关闭 WebSocket，再调用 Viewer 删除操作；防止已删除 Rust view 收到迟到的 schema 请求。
- `src/timepilot/native_bi_api.py`：独立有序 WebSocket handler，逐个等待原生请求完成；关闭 Session 之前必须先 drain 单线程 SQL executor。原版官方 Starlette handler 会先 close() 再由外层 shutdown executor，这存在并发时序风险。
- `src/timepilot/static/native-bi.js`：编辑、撤销、回滚刷新后复用新版本已加载的 virtual source，不重复重连。
- `scripts/repair_installation.py`：完整归档可以安装到新的空目录，安装前验证 ZIP 文件清单和 SHA-256；保留已有数据和配置。
- `scripts/release_integrity.py`：隐藏配置文件纳入必须文件清单；根目录 `scripts/repair_installation.py` 提供全量安装入口。
- `tests/test_ordered_perspective_session_v0512.py` 与 JS 生命周期测试验证清理顺序和错误处理。

## 验收边界

现有执行环境无法模拟 Mac 本机的 Perspective 5.6.1 + DuckDB/Arrow + Google Chrome 组合。增加的回归测试可验证清理顺序，但无法证明上游 Rust `ViewSchemaReq` 极端竞态已全面消除。目标机应运行 `bash TIMEPILOT.sh accept native` 并确认 `edit-preview-and-commit`、`chart-preserved-after-edit` 和实时流阶段全部 PASS。保留原始输出日志和 `reports/native_bi_acceptance.json`。

运行数据、企业模型权重、OneDrive 同步数据、浏览器、node_modules 和 Python site-packages 不属于源码发布范围。源代码发布包提供按需构建前端逻辑，不包含可直接运行的已编译 JS/WASM。
