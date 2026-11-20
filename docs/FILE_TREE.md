# TimePilot v0.37.7 当前源码目录

- `src/timepilot/feature_evidence_v0373.py`：只读数值证据生成、特征图表与报告一致性校验。
- `src/timepilot/feature_insights.py`：同源监督同折特征三方案比较。
- `src/timepilot/feature_dashboard_v0372.py`：统一 DuckDB 数据一览与证据源。
- `src/timepilot/static/data-dashboard-v0372.js`：证据卡片、报告导出与浏览器 SHA256 验证。
- `tests/test_featurelab373_evidence.py`、`frontend/native-bi/tests/featurelab373_evidence_ui.test.mjs`：数值证据与界面反例。
- `scripts/featurelab373_evidence_acceptance.py`：固定数据及物理门禁。

# TimePilot v0.37.2 当前源码目录

v0.37.1 新增统一加载入口 `src/timepilot/static/perspective-loader.js`、诊断 `frontend/native-bi/src/connection-diagnostics.mjs`、浏览器门禁 `scripts/featurelab371_chrome_cycles.py`；见 `docs/FEATURELAB_V0371_BI_REPAIR_AND_QA.md`。

核心目录：`src/timepilot` 为业务 API、特征与 Worker；`src/timepilot/static` 为浏览器工作台；`frontend/native-bi` 为 Perspective 构建工程；`tests` 为回归；`scripts/featurelab364_physical_acceptance.py` 为物理计算验收；`src/timepilot/system_logs.py` 与 `system_logs_api.py` 为日志中心。原始数据始终存放于 `TIMEPILOT_HOME`。

## v0.33.3 - added reliability components

- `src/timepilot/feature_auto_coordinator.py`: durable background stage progression and per-step enterprise owner authorization.
- `src/timepilot/feature_auto_analysis.py`: failed-job retry, source staleness and historical report marker.
- `src/timepilot/feature_lab_v2_api.py`: revision-checked retry endpoint.
- `src/timepilot/static/feature-shell.js`: read-only stage polling and retry action.
- `tests/test_featurelab333_resilience.py`, `frontend/native-bi/tests/featurelab333_resilience_ui.test.mjs`: new regression cases.
- `docs/FEATURELAB_V0333_RELEASE_REVIEW.md`: review and Mac acceptance limits.

---

# 当前源码目录（v0.32.4）

- `src/timepilot/feature_auto_analysis.py`：自动分析状态机、Agent 规划、候选质量、保存与评价联动。
- `src/timepilot/feature_topology.py`、`src/timepilot/feature_temporal_profile.py`、`src/timepilot/feature_auto_delivery.py`：监督标签/时间契约、同源 Parquet 与评价/BI 交付清单。
- `src/timepilot/feature_auto_agent.py`：模型输出白名单、无模型回退及证据解释。
- `src/timepilot/feature_lab_v2_api.py`：统一 HTTP 接口。
- `src/timepilot/static/feature-auto-ui.js` / `feature-shell.js`：引导式工作台、监督确认、同源报告及 Perspective 入口。
- `frontend/native-bi/`：Perspective 原生构建源码，JS/WASM 由目标构建机产生。
- `tests/test_featurelab322_same_artifact.py`：同源与监督契约专项测试（含受控假产物）。
- `docs/CURRENT_USER_GUIDE.md`：当前用户操作说明；`docs/FEATURELAB_V0322_RELEASE_AND_REVIEW.md`：交付审阅。
- `docs/RELEASE_MANIFEST.json`：源码 SHA256；`TIMEPILOT_HOME` 保存真实运行数据，不属于源码 ZIP。


---

## 历史版本说明（保留供追溯；操作请以上文为准）

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
