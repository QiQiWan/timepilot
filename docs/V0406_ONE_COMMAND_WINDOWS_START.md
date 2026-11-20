# TimePilot v0.40.6 — Windows 单命令启动与 Perspective 首次构建

## 现象和根因

用户 Windows PowerShell 日志显示 Python 3.12.12 正常、数据库可初始化、`perspective` Python import 正常，但 `[BI CHECK] Offline bundle: missing/not built`，且 `bi-setup` 因未安装 Node.js 24/npm 被阻断。日志的 `Release manifest: 0.40.4` 表明当时正在运行 v0.40.4 源码，而非此前交付的 v0.40.5。此前 `start` 默认仅运行 `ensure_native_bi.py --status-only`，即使源码提供构建接口也不会默认构建；另一个问题是原源码 ZIP 未包含 npm lock 或编译 JS/WASM，首次在线构建必然有初始化步骤。

## 修改

- `scripts/launcher.py`：`start` 默认使用 `--auto-build --auto-bootstrap --auto-node`；保留 `TIMEPILOT_AUTO_BUILD_BI=0` 禁止自动构建的配置。`bi-setup` 仍可显式调用，且共享 Node 检查。
- `scripts/ensure_native_bi.py`：在跨进程构建锁内调用 `setup_native_bi.py --auto-node --bootstrap-lock`，完成重验、构建和发布；已有效的 bundle 直接返回，不下载。
- `scripts/node_toolchain.py`：新增 Windows Node 24 免管理员缓存下载模块。先检测系统 Node 24 与显式 `TIMEPILOT_NODE_HOME`，再检测缓存；找不到时且非离线状态下从官方 Node 24 LTS 版本索引读取 Windows ZIP，下载官方 SHA256 清单，并完成流式 hash 核验、ZIP 解压安全检查及 Node/npm 可执行检测。工具缓存位于 `TIMEPILOT_HOME/toolchains/node24`，只调整本次构建进程的 PATH。
- `scripts/npm_runtime.py`：Windows 通过 `node.exe node_modules/npm/bin/npm-cli.js` 调用 npm，避免依赖 `npm.cmd` 的隐式 shell 行为。
- `scripts/setup_native_bi.py`：统一复用 Node24、npm lock 安全修复、npm ci 和原生 JS/WASM 原子构建。已签名 lock 不会被静默修改，构建出错时保留上一份有效资源。
- `tests/test_one_command_start_v0406.py`：一键启动传参、手动禁用、显式 bi-setup、缓存复用、SHA256 篡改、ZIP 越界、离线和显式安装路径反例。

## 使用

将 ZIP 解压到一个**新的本地源码目录**，不要覆盖原文件混入旧版。

```powershell
# 已激活现有 Python 3.12.12 环境、安装过 requirements.txt
cd D:\work\TimePilot_v0.40.6
.\TIMEPILOT.bat start
```

首次需要可访问 `https://nodejs.org/dist/index.json`、`https://nodejs.org/dist/<version>/SHASUMS256.txt`、对应 Node ZIP 和 npm registry。构建完成后正常启动，访问 `http://127.0.0.1:8000`。随后启动复用已签名资源，不需要 Node/npm；未启用离线模式不代表服务会在每次启动主动联网。

若企业网络禁止 Node 下载，在已安装 Node 24 时可执行（当前 PowerShell 会话）：

```powershell
$env:TIMEPILOT_NODE_HOME='C:\Program Files\nodejs'
.\TIMEPILOT.bat start
```

严格离线条件（只有已存在有效 BI 资源才能成功）：

```powershell
$env:TIMEPILOT_OFFLINE='1'
.\TIMEPILOT.bat start
```

程序永远不删除 `TIMEPILOT_HOME`，不会重建 Python/Conda，也不会全局安装 Node。已存在的数据库按原有迁移逻辑处理。不能下载/构建时给出阻断原因，避免静默降级为缺少 BI 的版本。

## 测试与边界

本轮在 Linux 容器中完成了 Python 的一键入口与工具链契约测试、签名源码校验与 ZIP 解压校验。Node ZIP 测试使用独立模拟字节与损坏/越界反例，不表示真实下载已成功。实际 Windows Node24 官方安装、npm registry 访问、Vite 构建、Chrome 原生 Perspective 交互及目标 Mac 离线发行，必须在对应目标环境继续执行物理验收。若实际机报网络/DNS、证书或权限错误，应依据日志定位，不能将源码测试等同为物理验收。

## Windows Node cache publish hotfix (WinError 5)

A first run can successfully download, SHA256-verify, extract and execute Node 24,
then fail during `os.replace(staged, cache/node-v24.x.y-win-x64)` with WinError 5.
Successful manual folder create/rename/delete tests do not exclude this Windows
transient lock on the just-executed `node.exe` or endpoint-security inspection.

The installer now retries the atomic directory move with bounded delays. On a
persistent access-denied move, it claims a new target directory exclusively,
copies from the already verified ZIP extraction, compares all regular file bytes
(including the official SHA256 provenance JSON), then independently probes the
published `node.exe`. Failed copy verification cleans up only the newly claimed
Node target folder. Existing valid cached Node is reused and existing invalid
folders are never overwritten without operator inspection. Python environments,
PATH persistence, project databases, and other TimePilot files are unchanged.

Target Windows still requires physical acceptance of the official Node download,
npm installation, Perspective build and browser startup. Synthetic Linux tests
inject transient and permanent Windows-style PermissionError, a corrupted copy,
and an already valid concurrent cache; they do not constitute Windows AV testing.
