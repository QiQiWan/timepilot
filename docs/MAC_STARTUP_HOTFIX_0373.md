# TimePilot v0.37.3 Mac 启动修复与验收

## 证据及原因

日志已经证实 Python 3.12.12 与 SQLite Catalog v38 正常，两个异常独立存在：

1. `source-check` 报 `.github/workflows/ci.yml` SHA256 不同。原因可能是
   使用通配符复制未覆盖隐藏 `.github` 或 OneDrive 同步的混合版本。
2. `npm ci` 报 `EUSAGE`，旧 `package-lock.json` 缺少 `@rollup/rollup-...`
   多平台可选依赖。原实现只验证 package.json 顶层版本，未验证平台依赖。

## 在现有 Mac 上执行

先退出 TimePilot，保留原有 `TIMEPILOT_HOME`、SQLite、Parquet；解压完整
Hotfix 源码至一个新的本机目录（建议不在 OneDrive 中），打开该目录并激活
原有的 Python 3.12.12 环境：

```bash
bash TIMEPILOT.sh source-check
bash TIMEPILOT.sh db-check
node --version  # 需要已安装的 v24.x
bash TIMEPILOT.sh bi-setup --bootstrap-lock
bash TIMEPILOT.sh
```

如果继续在旧源码目录运行，须先恢复已变化的隐藏 CI 文件（有原文件备份）：

```bash
bash TIMEPILOT.sh source-repair --dry-run
bash TIMEPILOT.sh source-repair
bash TIMEPILOT.sh source-check
```

`bi-setup --bootstrap-lock` 会检测到旧锁遗漏 Rollup 平台依赖；只要能访问
npm registry，就在系统临时目录中按当前 `package.json` 真正解析依赖，
执行 `npm ci` 验证，然后保存原始旧锁与 SHA256，原子更新 npm lock，再构建
真实 Perspective JS/CSS/WASM。失败时不会写入新锁，不破坏原 BI 构建。

严格禁止通过空锁文件、伪造 WASM、`--force` 或不检查 `npm ci`
来绕过错误。

## 可独立测试的验收标准

- 旧缺失 `@rollup/rollup-darwin-arm64` 锁：修复前能识别，修复后跨平台
  条目完整。
- 无网络/DNS：保留旧 lock，保留原 BI，不修改项目数据库。
- 已签名锁：禁止覆盖，要求完整可信源包。
- 被修改 `.github/workflows/ci.yml`：先备份，再还原签名正确文件。
- 真正 Mac + Node 24 + npm registry：`npm ci` 通过，生成实际 WASM，
  `scripts/ensure_native_bi.py --status-only` 通过，启动成功。

当前代码测试环境无法连接 npm registry，故暂时不能证明 Mac 端物理
`npm ci` 和真实 WASM 完整通过。这两项仍应以目标机器日志为准。
