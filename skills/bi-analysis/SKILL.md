---
name: bi-analysis
description: Query and visualize governed business tables and unified data assets with schema-checked grids, groups, aggregation and pivots without arbitrary SQL execution.
---
# BI analysis

1. Use `bi.asset_grid` for inspection, including when numeric aggregation would mix scopes or sources.
2. Use `bi.asset_group` only when the selected data is appropriate for the operation; avoid cross-source sums.
3. Use `bi.profile`, `bi.aggregate` and `bi.pivot` for existing generic BI tables with explicit validated fields and metric specs.
4. Keep source asset ID, version ID and selection stable when switching backends.
