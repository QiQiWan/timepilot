# 隐藏配置文件恢复

完整源码包包含 `.dockerignore`、`.env.example`、`.github/workflows/ci.yml`、`.gitignore`、`.python-version`。macOS Finder 可能隐藏这些文件，`cp source/* target/` 会遗漏它们。

在完整工程根目录执行：

```bash
bash TIMEPILOT.sh restore-hidden
```

该命令通过 `scripts/restore_hidden.py` 从 `bootstrap/visible_hidden_config_backup/` 恢复缺失的五个配置文件，在写入前核对 `docs/RELEASE_MANIFEST.json` 中的 SHA-256；不会覆盖已经存在且内容不同的配置文件。不创建新 Python 环境，也不改动 `.env`、运行数据、npm 缓存或系统依赖。

若 `TIMEPILOT.sh`、源码校验清单或恢复副本也缺失，请重新解压签名完整源码 ZIP 到新目录，不要尝试用其他版本补丁修复混合源码。
