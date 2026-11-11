# v0.13.0 完整目录结构

```text
TimePilot_v0.13.0/
├── TIMEPILOT.sh              # Mac/Linux 单入口
├── TIMEPILOT.bat             # Windows 单入口
├── README.md
├── pyproject.toml
├── requirements.txt
├── .env.example / .python-version / .gitignore / .dockerignore
├── .github/workflows/ci.yml
├── Dockerfile / compose.yaml
├── src/timepilot/           # Python SDK / FastAPI / 数据、BI、Agent 与研究组件
│   ├── semantic_benchmark.py
│   ├── auto_experiment.py
│   ├── mechanisms/identification.py
│   ├── research_api.py
│   └── static/              # 浏览器 UI；原生 Perspective 的 JS/WASM 在发布机编译
├── frontend/native-bi/      # npm 源码与 JS 单元测试
├── scripts/                 # 内部工具，不需要从根目录逐一调用
│   ├── launcher.py
│   ├── restore_hidden.py
│   ├── verify_installation.py / release_integrity.py
│   ├── package_timepilot.py / repair_installation.py
│   ├── build_native_bi.py / accept_native_bi_live.py
│   └── accept_research_browser.py / 其他专项验收
├── skills/                  # 8 组工程技能说明与受控工具定义
├── tests/                   # Python/API/数据和前端回归
├── docs/                    # 数据契约、版本、审阅、迁移和参考文档
├── bootstrap/               # dotfiles 可见签名副本
├── examples/ configs/ benchmarks/
└── docs/RELEASE_MANIFEST.json  # 文件 SHA-256 发布清单
```

运行数据放在 `TIMEPILOT_HOME`，绝不放进源码 ZIP。历史根目录下的十份重复 `.sh`/`.bat` 脚本已经移除；同等操作统一通过 `TIMEPILOT.sh` 或 `TIMEPILOT.bat` 选择子命令。
