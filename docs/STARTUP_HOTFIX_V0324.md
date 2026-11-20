# TimePilot v0.32.4 启动修复（startupfix1）

现象：`source-check` 通过，但 `bash TIMEPILOT.sh` 在 `--require-runtime` 阶段出现 `UnboundLocalError: cannot access local variable 'importlib'`。

原因：`scripts/verify_installation.py` 在 `inspect()` 函数内部声明 `import importlib.util`，使 `importlib` 被标记为该函数的局部变量，随后运行到 `importlib.import_module()` 时触发未绑定变量异常。

修复：把 `importlib.util` 移到模块顶层；增加模拟 Python 3.12 的正式启动预检回归测试；重新生成完整源码 SHA256 发布清单，保持版本 `0.32.4` 和既有数据格式不变。

## 在已有源码根目录修复

请先退出正在运行的 TimePilot，保留原有 `TIMEPILOT_HOME`。下载完整修复源码包之后，在旧项目根目录运行（将 ZIP 路径换成下载文件的实际路径）：

```bash
python scripts/repair_installation.py --archive "$HOME/Downloads/TimePilot_v0.32.4_STARTUPFIX1_COMPLETE_SOURCE.zip" --target .
python scripts/repair_installation.py --archive "$HOME/Downloads/TimePilot_v0.32.4_STARTUPFIX1_COMPLETE_SOURCE.zip" --target . --apply
bash TIMEPILOT.sh source-check
bash TIMEPILOT.sh check
bash TIMEPILOT.sh db-check
bash TIMEPILOT.sh
```

`repair_installation.py` 在写入前校验完整 ZIP 的 SHA256，先备份被替换的文件（`.timepilot-backup/`），最后写入发布清单并校验整体源码。不会删除 `.env`、SQLite、Parquet、既有 `runtime/` 或 `TIMEPILOT_HOME`。若已有额外开发代码则保留。

正式启动的其他前置条件不变：使用既有 Python 3.12 环境及依赖；首次编译原生 BI 时需要已安装的 Node 24 与 npm，以及可获取前端依赖的构建环境。源码包不预装浏览器或编译后的 Perspective JS/WASM；首次无数据目录时仍由原有启动流程初始化数据库。

## 验证边界

`source-check` 与运行时预检的回归测试覆盖 Python 作用域错误；Linux/当前测试环境的 Python 版本和依赖不能代替用户的 macOS/Conda/Node 实机验收。启动后应继续核对 `check`、`db-check`、BI 构建与网页访问结果。
