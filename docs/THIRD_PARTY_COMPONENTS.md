# 第三方依赖与集成边界（v0.5.0）

| 组件 | 作用 | 使用与发布方式 | 开源协议（应以发布时随包 LICENSE 为准） |
|---|---|---|---|
| DuckDB | 服务端列式查询、CSV/Parquet 读取 | Python 包，`requirements.txt` | MIT |
| Apache Arrow / PyArrow | 列式交换、Arrow IPC | Python 包，`requirements.txt` | Apache-2.0 |
| Perspective 5.6.1 | WebAssembly 网格、透视和可视化 | 前端源码 + NPM 构建，未包含二进制 | Apache-2.0 |
| Vite 6.3.5 | Perspective 构建工具 | 开发构建依赖，不需要最终用户安装 | MIT |
| AutoGluon TimeSeries 1.6.1 | 通用时序预测基线 | Python 包，需按其自身许可及模型权重许可证审核 | Apache-2.0（框架） |

本项目不包含 Superset、AG Grid Enterprise、DataEase 或 Metabase 的运行代码。此前已使用的 FastAPI、pandas、Pydantic、PySINDy 等依赖依照各自发行许可证处理。首次形成正式对外发行包时，应自动收集全量直接/传递依赖的许可证、NOTICE 和软件物料清单（SBOM），并审查预训练模型权重许可证。不要根据本表替代法律或供应链审核。

**当前离线构建限制**：需要的 DuckDB/PyArrow 二进制和 Perspective npm 包未在此环境安装，无法提供第三方运行二进制或实测性能数据。后续将固定 NPM 锁文件并纳入签名/哈希发布流程。
