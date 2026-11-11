# 原生 BI 性能基准

在 Python 3.12.12 且已安装全部依赖的目标工作站执行：

```bash
python scripts/benchmark_native_bi.py --rows 100000 1000000 10000000
```

脚本现场生成合成数据，测量 Parquet 构建、资产导入、过滤和分组耗时，并在 `benchmarks/native_bi_results.json` 输出环境和结果。**随源代码不提供预先生成的耗时**；文件生成、读写和并发条件会影响结果。

正式性能验收还应另行记录峰值 RSS、CPU/GPU 状态、查询 p50/p95、磁盘临时文件峰值、浏览器首屏和实际任务完成率。当前脚本仅提供单次基线，不可用于证明生产性能 SLA。
