# TimePilot v0.31.0 · Mac/OneDrive 安装修复

## 故障对应关系

- `CHANGED OR DAMAGED FILE: .env.example / .github/workflows/ci.yml`：本地隐藏文件与发行清单 SHA256 不同；常见于使用 `cp -R new/* old/` 时未覆盖以 `.` 开头的文件，或云同步导致版本混用。不能通过直接修改发布清单来忽略问题。
- `perspective-build.json` 不存在：当前是 source-only 源码版。需要在具备 Node 24 且可访问 npm registry 的构建机器上编译 Perspective JS/CSS/WASM。正常启动不会自动下载或编译。
- `schema 30 -> 34`：升级提示。执行 `db-check` 仅做只读检查，正常启动先创建数据库快照，再自动迁移。不要删除或重建 `TIMEPILOT_HOME`。

## 修复步骤（在项目源码根目录执行）

先确认已激活原有 Python 3.12.12 环境，且当前路径是 TimePilot 根目录：

```bash
bash TIMEPILOT.sh source-repair --dry-run
bash TIMEPILOT.sh source-repair
bash TIMEPILOT.sh source-check
```

`source-repair` 只处理 `.dockerignore`、`.env.example`、`.github/workflows/ci.yml`、`.gitignore`、`.python-version` 五项。脚本先根据 `docs/RELEASE_MANIFEST.json` 对恢复源逐个进行 SHA256/长度核验；对被修改的文件，会先原样备份至 `$TIMEPILOT_HOME/source_repair_backups/repair_*/`，才原子替换。用户私有 `.env`、SQLite、Parquet 和其他源码不会被改写。发现其他文件校验失败时须从完整可信压缩包修复。

随后在 **联网构建机器** 上执行一次：

```bash
node --version  # 要求 v24.x.x
bash TIMEPILOT.sh bi-setup --bootstrap-lock
```

命令按需生成 `frontend/native-bi/package-lock.json`，使用 `npm ci` 安装锁定的 JS 依赖，调用 Vite 构建并核验 Perspective JS/CSS/WASM 的哈希、WASM 文件头和导出契约；构建失败时不会覆盖原有完好 bundle。此操作不下载浏览器，也不修改 Python/Conda 环境。若机器无外网，应在联网构建机器运行后通过 `bash TIMEPILOT.sh build` 生成含编译资源的离线包，再复制到离线机器。

最后启动、由系统完成备份和迁移：

```bash
bash TIMEPILOT.sh db-check
bash TIMEPILOT.sh
bash TIMEPILOT.sh db-check
```

最后一次 `db-check` 预期为 `schema 34 -> 34`。系统若提示备份失败或版本为 `newer/corrupt/unrecognized`，不要删除原库。

## 常见问题

1. **Node 不是 24。** 使用已安装的 nvm 24：`nvm use 24`。如未安装，需要单独在联网机器安装；系统不会自动更改 Node/Python 环境。
2. **`npm` 网络 DNS 错误。** 先检查网络、DNS 和 npm registry 的连通性；不能用伪造的 `package-lock.json` 或空 WASM 绕过校验。
3. **仍显示 Source-only。** 未构建 Perspective 资源时属于预期阻断，运行 `bi-setup` 之后再检查。
4. **OneDrive 同步源码。** 建议解压到一个新的本机非同步目录，再将 `TIMEPILOT_HOME` 指向原有本地数据目录；避免 `source/*` 通配符导致隐藏文件被遗漏。旧数据库保留在原位置。
5. **其他文件哈希异常。** 不要使用“重新签名”来接受未知修改；使用完整原始发行包中 `scripts/repair_installation.py` 的校验恢复流程或全量重新解压。
