# TimePilot v0.38.8 | 物理验证与完整离线交付闭环

日期：2026-11-18。基线：v0.38.7 已验收源码候选包。本轮仅修改源码/发布工具，不触碰现有 `TIMEPILOT_HOME`。

## 1. 审阅发现及整改

1. **完整离线门禁存在条件缺口。** v0.38.7 的 `--mode offline` 检查 wheelhouse、JS/WASM 及运行组件，但不执行真实 DuckDB/Parquet 查询和 Chrome 30 次联动，存在「离线依赖就绪」误写为「完整业务通过」的可能。v0.38.8 将物理、浏览器、完整离线归档和独立解压运行测试设为串行强制条件。任一阶段 BLOCKED/FAILED，最终就不能 PASS。
2. **交付先于验收。** 旧构建脚本在完整离线门禁运行前就输出正式 ZIP。v0.38.8 把 ZIP 先写入私有候选路径，只在全部验证通过后原子改名。已有最终包拒绝覆盖，失败清除的仅是本次候选文件。
3. **未经审阅的 npm 依赖解析可能污染正式构建。** 正式 CI 不再使用 `--bootstrap-lock`；`package-lock.json` 必须在源码发布前审阅和固定。开发者仍可通过独立的 `bi-setup --bootstrap-lock` 命令生成候选锁，但必须先纳入源文件审核/哈希清单。
4. **压缩包完整性和生产可用性需要分开验收。** 新增 `scripts/verify_offline_archive_v0388.py`，逐文件 SHA256、大小、版本、路径与 source-root 等价性核对，拒绝路径穿越、符号链接、重复、混合根目录及虚假完整离线声明。这个结果只证明归档的结构和字节一致，不能独立证明业务运行。
5. **必须在真正交付的 ZIP 上复跑。** 新增 `scripts/accept_offline_zip_runtime_v0388.py`，在全新临时目录验证完整 ZIP、解压后核查真实 Perspective/WASM、离线 wheelhouse、Parquet 崩溃恢复、已登记资产哈希、DuckDB SQL 和真实 Chrome 30 次切换。使用现有 Python 3.12.12，`TIMEPILOT_HOME` 指向隔离临时目录，`PIP_NO_INDEX=1`，无用户数据库访问或环境创建。子进程按进程组回收，防止历史子进程继承输出管道导致挂起。

## 2. 四类门禁

| 门禁 | 必需证据 | 失败/阻断语义 |
| --- | --- | --- |
| source | 源码 SHA、独立测试组、Node 专项、五角色 Worker | 测试失败 `FAILED`；不接受「仅看到通过日志但进程未正常退出」 |
| physical | 目标 CPython 3.12.12、DuckDB+PyArrow、物理崩溃恢复、Parquet SQL 数值、规模性能 | 依赖缺失 `BLOCKED`，数值不匹配 `FAILED` |
| browser | physical 全部通过、真实 Perspective 构建、Chrome、WebSocket/30 次切换 | Chrome/JS/WASM 缺失 `BLOCKED`，交互或请求失败 `FAILED` |
| offline | source + physical + browser + npm lock + wheelhouse resolver + ZIP 校验 + **解压后同环境运行复测** | 任意一项未通过则不得发布完整离线 ZIP |

## 3. 当前环境证据边界

本会话运行环境为 Linux、Python 3.13.5、Node 22.16.0，安装有 Chromium，但没有 DuckDB、PyArrow、Perspective JS/WASM；联网 pip DNS 查询失败。**不能据此声称物理 SQL、Chrome/Perspective 实时联动、Mac 离线安装通过。** Chromium 的本地模拟 API 检查（如实际执行）只能证明 DOM/前端逻辑。

## 4. 目标环境操作命令

```bash
bash TIMEPILOT.sh source-check
bash TIMEPILOT.sh release-gate --mode source
bash TIMEPILOT.sh release-gate --mode physical
bash TIMEPILOT.sh release-gate --mode browser
# 完整离线打包仅在目标已装的 Python 3.12.12、Node 24 且锁文件固定后执行
bash scripts/BUILD_OFFLINE_RELEASE.sh
# 独立核验实际交付 ZIP
python scripts/verify_offline_archive_v0388.py --archive TimePilot_v0.38.8_COMPLETE_OFFLINE.zip --require-complete --source-root .
python scripts/accept_offline_zip_runtime_v0388.py --archive TimePilot_v0.38.8_COMPLETE_OFFLINE.zip --require-target
```

注意：当前源码候选版不含 npm lock、编译 JS/WASM 或 Python wheelhouse。运行正式构建脚本会明确 BLOCKED，不会生成伪完整离线包。

## 5. 尚待独立关闭的验收

- 真实数据可用性：Mac / Python 3.12.12，执行 Parquet 同长度篡改、孤儿资产清点、真实 DuckDB SQL 与 Dashboard 的独立 Oracle 对账。
- 浏览器联调：安装的 Chrome 中 30 次真实 Perspective/FeatureLab 页面切换、WebSocket 与恢复、0 个合法场景 HTTP 500。
- 离线运行：在目标 Mac/Linux/Windows 上分别从独立完整 ZIP 解压复测；数据升级回滚、停电/磁盘不足、磁盘空间和权限问题尚待实测。
- 安全：ZIP 与清单 SHA256 只检测不一致，尚未提供由独立可信密钥签发的数字签名或软件来源证明；真实生产发布需要供应链签名和多租户安全审查。
