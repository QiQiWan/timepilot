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
