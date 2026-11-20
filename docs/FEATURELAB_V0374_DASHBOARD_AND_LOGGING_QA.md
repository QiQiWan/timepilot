# v0.37.4 多字段图表、分区页面与日志诊断

## 问题与处理

1. 概况仅展示数据总体信息、质量提醒和操作入口；质量检查保留质量指标与缺失信息；统计图表独立呈现 DuckDB 驱动的多维仪表盘。离开统计页停止仪表盘请求。
2. 统计图表新增可勾选的多字段箱线汇总（各字段独立 min-max 缩放，保留原始数值），和最多 10 个字段的全 Pearson 矩阵。非恒定、有至少两条有限记录的对角线为 1，恒定字段为不可定义；不伪造小提琴核密度。
3. 单图改动只执行该图查询；全图批量查询最大并发 2，避免切换某一图时请求风暴。非法字段组合自动调整或由 API 返回 422；单纯 TIME 字段不能作为日历日期分桶。
4. 500 响应只返回简明分类、诊断编号和请求编号；日志保存脱敏的模块位置/行号/函数、类型、阶段、重复计数，60 秒内相同调用栈错误按 1/2/4/8 次稀疏记录。
5. 日志 UI 提供近期异常聚合、诊断编号筛选、调用栈查看及 TXT/JSONL 导出，不记录请求体、源数据行和原始 SQL 参数。

## 物理验收

本地开发环境若缺少 DuckDB/PyArrow，不得把数据统计 SQL 验收、Chrome 视觉及 macOS 执行状态标为通过。业务机器使用原有 housing.csv 或固定 12 行测试集逐项验证概况/质量/统计页面、矩阵数值与独立 DuckDB SQL、真实 500 的诊断回执，刷新、筛选和版本切换的来源指纹一致性。

## 回归命令

```bash
python -m pytest -q tests/test_featurelab374_dashboard_diagnostics.py tests/test_featurelab372_dashboard_contract.py tests/test_system_logs_v0364.py
node --test frontend/native-bi/tests/featurelab374_dashboard_ui.test.mjs
python scripts/featurelab372_dashboard_acceptance.py --out reports/v0374/dashboard_physical.json
```
