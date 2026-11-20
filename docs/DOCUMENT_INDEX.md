- [v0.37.9 离线交付与灾备恢复验收](V0379_OFFLINE_RECOVERY_AND_QA.md)

# 当前文档索引（v0.38.6）

- [v0.38.6 原生 Parquet 崩溃恢复、孤儿文件审计与物理验收](V0386_NATIVE_SNAPSHOT_RECOVERY_AND_QA.md)

- [`FEATURELAB_V0375_377_IMPLEMENTATION_AND_QA.md`](FEATURELAB_V0375_377_IMPLEMENTATION_AND_QA.md)：类别 SQL、请求竞争、真实箱线图、关联与日志性能门禁。

- [`FEATURELAB_V0373_EVIDENCE_AND_QA.md`](FEATURELAB_V0373_EVIDENCE_AND_QA.md)：数值证据、同折对照、报告、反例和验收限制。
- [`FEATURELAB_V0372_DASHBOARD_QA.md`](FEATURELAB_V0372_DASHBOARD_QA.md)：DuckDB 图表统计、SQL 数值对账和筛选版本控制。

- [v0.37.2 数据一览、筛选与独立 DuckDB 验收](FEATURELAB_V0372_DASHBOARD_QA.md)
- [v0.37.1 Perspective 与正式特征 BI 修复](FEATURELAB_V0371_BI_REPAIR_AND_QA.md)
- [v0.37.0 工业数据适配与性能验收](FEATURELAB_V0370_INDUSTRIAL_ACCEPTANCE.md)
- **v0.36.5 浏览器全流程验收**：[FEATURELAB_V0365_BROWSER_ACCEPTANCE.md](FEATURELAB_V0365_BROWSER_ACCEPTANCE.md)
# TimePilot v0.37.0 当前文档索引

README、API、ACCEPTANCE、FILE_TREE 和 CURRENT_USER_GUIDE 是当前使用说明，旧版本文件仅用于历史审阅。v0.36.4 的真实物理计算状态见 `docs/FEATURELAB_V0364_RELEASE_AND_QA.md`。

# TimePilot v0.33.3｜文档索引与适用版本

本索引用于避免把历史迭代材料当作当前操作规范。实际接口以本机 `/docs` OpenAPI 及源码为准。

| 类型 | 当前推荐文档 | 适用范围 |
|---|---|---|
| 用户操作 | [CURRENT_USER_GUIDE.md](CURRENT_USER_GUIDE.md) | 导入、自动分析、监督确认、BI、报告与故障排查 |
| 主项目入口 | [README.md](../README.md) | 版本、部署、数据备份、验收命令 |
| API | [API.md](API.md) 首页 v0.32.4 部分 | 自动会话、特征集、同源交付与权限 |
| 质量门禁 | [ACCEPTANCE.md](ACCEPTANCE.md) 首页 v0.32.4 部分 | 源码级与生产级验收界限 |
| v0.33.3 reliability review | [FEATURELAB_V0333_RELEASE_REVIEW.md](FEATURELAB_V0333_RELEASE_REVIEW.md) | Model modes, job recovery, stale source, report/BI SHA256 and acceptance limits |
| 设计与复核 | [FEATURELAB_V0330_V0331_RELEASE_REVIEW.md](FEATURELAB_V0330_V0331_RELEASE_REVIEW.md) | 当前工作台、四类特征及真实图表、测试覆盖和限制 |
| 源码目录 | [FILE_TREE.md](FILE_TREE.md) 首页 v0.32.4 部分 | 当前模块定位 |
| 历史变更 | [CHANGELOG.md](CHANGELOG.md) | 时间顺序、版本差异及追溯 |

[HISTORICAL_README.md](HISTORICAL_README.md) 与 `docs/FEATURELAB_V027_RELEASE_AND_REVIEW.md` 等按版本命名的旧材料、`BI_AND_AGENT.md` 与历史 `NATIVE_BI.md` 是**过去版本的技术背景**；其中原始命令、URL、版本号、认证/权限状态可能已发生变化，不得作为最新部署和生产安全依据。

### 文档维护准则

1. 首页 `README.md`、`CURRENT_USER_GUIDE.md`、`API.md` 和 `ACCEPTANCE.md` 必须先说明当前版本及验收边界。
2. 历史文档保留，凡链接至已移除文件需取消失效链接或迁移至现有权威说明。
3. 不以模拟 API、测试替身、客户端双面板回执证明生产全链路通过。
4. 每次发布保留测试记录、源码 SHA256 清单、源数据 SHA/契约修订/配方 SHA、真实物化与评价哈希。

- [v0.37.2 数据一览与 DuckDB 核对](FEATURELAB_V0372_DASHBOARD_QA.md)
