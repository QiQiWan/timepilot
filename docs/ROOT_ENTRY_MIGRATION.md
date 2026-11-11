# 根目录入口收束（v0.12.0）

根目录不再放置历史故障排查包装脚本。保留 `TIMEPILOT.sh` 和 `TIMEPILOT.bat` 两个可执行入口。`scripts/` 内保留被调用的实际功能和浏览器测试，避免删除有效的回归能力。

| 旧入口 | 新用法 |
|---|---|
| START_TIMEPILOT.sh / START_TIMEPILOT.bat | TIMEPILOT.sh / TIMEPILOT.bat |
| RUN_NATIVE_BI_ACCEPTANCE.sh / .bat | TIMEPILOT.sh accept native |
| RUN_SMART_IMPORT_ACCEPTANCE.sh | TIMEPILOT.sh accept smart |
| RUN_DATA_AGENT_ACCEPTANCE.sh | TIMEPILOT.sh accept agent |
| RUN_P0_UNIFIED_ASSET_ACCEPTANCE.sh | TIMEPILOT.sh accept unified |
| RUN_TESTS_WINDOWS.bat | TIMEPILOT.bat test |
| RESTORE_HIDDEN_FILES.sh | TIMEPILOT.sh restore-hidden |
| INSTALL_COMPLETE.sh | 在新目录解压完整 ZIP；升级采用 scripts/repair_installation.py 的已校验恢复机制 |
| scripts/BUILD_OFFLINE_RELEASE.sh | TIMEPILOT.sh build（仅发布机） |

启动流程先执行 `verify_installation.py --require-runtime`，随后调用既有解释器运行服务；没有任何环境创建、自动依赖安装、浏览器下载操作。验收命令检查源码与发布资源，不会隐式重编译 npm。若原生 BI 未携带 JS/WASM，会明确阻断原生验收，基础工作台继续可用。

原根目录 `RUN_*`/`START_*`/`INSTALL_*`/`RESTORE_*` 已移除，实际 Python 功能和测试在 `scripts/` 内保留。旧版 CI/项目文档中的命令已迁移；用户可在根目录运行 `bash TIMEPILOT.sh --help` 查看最新选项。
