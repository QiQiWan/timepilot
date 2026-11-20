# TimePilot v0.37.9：运行时灾备、离线交付及交互并发修复

目标：从 v0.37.8 源码候选版推进到可复现的发布流水线，同时保持用户现有 `TIMEPILOT_HOME` 和 Python 环境不被后台更新。当前交付代码如未携带 `package-lock.json`、Perspective WASM 和 wheelhouse，**仍属于 source-only 候选版**；不能对外声明完整离线或生产发布成功。

## 已实现

1. `runtime_backup.py`：已有 SQLite 在线快照和不可变文件 SHA256 备份基础上增加读取校验及独立恢复函数。解压前后检查 SHA256；SQLite `integrity_check` 必须通过；拒绝重复/非法/跨目录条目。恢复目标必须是全新的不存在路径，借助同盘暂存目录完成校验后原子改名；不会主动覆盖 `TIMEPILOT_HOME`。
2. `backup_runtime.py`、`launcher.py`：增加可审计恢复 CLI；启动时不主动安装 npm 或生成锁；`TIMEPILOT_AUTO_BUILD_BI=1` 可显式授权在源码模式尝试使用当前 npm 构建。
3. `offline_wheels.py`：只在联网构建机、现有 CPython 3.12.12 下生成目标 OS/CPU 的二进制 wheelhouse；生成完整 SHA-pinned requirements lock、各 wheel SHA256 和构建环境清单；使用 `pip --no-index --dry-run --ignore-installed --require-hashes` 校验依赖闭包。源码、解释器或操作系统不一致时阻断。
4. `package_timepilot.py`：明确区分 `source-only`、`native-bi-offline` 与 `complete-offline`；完整离线包缺 JS/WASM 或 Python wheels 一律拒绝；ZIP 全部内容流式 SHA256 回读，避免数 GB wheelhouse 全量加载内存。
5. `BUILD_OFFLINE_RELEASE.sh`：统一 Node 24 + Python 3.12.12 + npm 锁 + DuckDB/Perspective + Python wheels + ZIP 生成顺序；修正历史 v0.31.0 错误文件名。
6. 数据图表：刷新/变更筛选后新请求不会等待已作废的慢图表请求，晚返回的旧数据不会覆盖当前版本。
7. `release_gate_v0379.py`：source/offline/physical/browser 四种门禁独立记录，`blocked` 与 `failed` 不计入 PASS。

## 使用命令

```bash
# 应用退出后创建备份（路径必须在 TIMEPILOT_HOME 之外）
bash TIMEPILOT.sh backup --output /path/to/timepilot_backup.zip
python scripts/backup_runtime.py --verify /path/to/timepilot_backup.zip

# 恢复到新的目录，不自动切换生产库
bash TIMEPILOT.sh restore --backup-archive /path/to/timepilot_backup.zip --destination /path/to/restored_timepilot_home
# 人工验收后再显式设置 TIMEPILOT_HOME=/path/to/restored_timepilot_home

# 仅检查当前源码
bash TIMEPILOT.sh release-gate --mode source
# 在联网且已审查 npm lock 的 BUILD 机器构建本机架构的完整离线包
bash scripts/BUILD_OFFLINE_RELEASE.sh
# 已下载的完整离线包，需在同 OS/CPU 且现有 Python 3.12.12 下安装依赖：
python -m pip install --no-index --find-links offline-wheelhouse --require-hashes -r offline-wheelhouse/requirements-offline-lock.txt
# 安装行为需由管理员显式执行；系统不会自动修改原 Python 环境
```

## 发布前真实验收

- 目标 Python 3.12.12 + DuckDB/PyArrow：固定 SQL 数字反例、506 行宽表、10k/100k/1M 行性能测量。
- Node 24 + npm lock：`npm ci` 成功、完整 JS/CSS/WASM、manifest/source SHA 一致。
- Google Chrome：页面刷新/返回/版本切换 30 次；HTTP500 0 次；DataAnalysis 与 FeatureLab 双表切换无重复注册。
- Mac ARM64（优先）及其他官方支持平台：空用户目录首次启动、已有库恢复、升级回滚；记录版本、日志与 RTO/RPO。
- 业务工厂数据：真实 PIT、标签时间与长期回归尚未在此阶段完成。

执行证据只认当前环境的实测日志，不用单元测试替代独立数据库、原生浏览器或目标 Mac 验收。

## 离线 Docker 运行说明

从 `complete-offline` ZIP 解压后，Linux 同架构可选择 `docker build -f Dockerfile.offline -t timepilot:0.37.9 .`。此 Dockerfile 只使用 ZIP 中的 wheelhouse，使用 pip 无网络解析及哈希校验；资源不齐全或 Linux 架构不匹配立即失败。现有 `Dockerfile` 仍为联网多阶段构建方案。实际 Docker 镜像构建、容器中的 Ready 探针需要目标 Docker 环境验收。
