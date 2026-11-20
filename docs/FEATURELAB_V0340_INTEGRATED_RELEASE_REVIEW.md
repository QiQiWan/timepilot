# TimePilot v0.34.0 — v0.33.4–v0.34.0 implementation and independent acceptance review

## Scope and delivery

Baseline: **complete unmodified v0.33.3 TimePilot tree**. Distribution is an **overlay source archive**, extracted directly into the original repository root; it contains no installer, environment bootstrapping, database deletion or compiled Perspective build. Stop TimePilot, back up source and `TIMEPILOT_HOME`, then copy the archive contents over existing paths. Preserve existing DuckDB/Parquet and SQLite files. On startup `project_schema` migrates forward from 35 to 38 with catalog backup; downgrade is unsupported.

## 1. v0.33.4 — trustworthy worker execution

- Source: `feature_execution.py`, `feature_auto_coordinator.py`, `feature_auto_analysis.py`, `project_migrations.py`.
- Bounded feature-job lease (default **90 s**, configurable `TIMEPILOT_FEATURE_LEASE_SECONDS` between 30–600 s), heartbeat, claim-specific random token and incrementing lease generation.
- SQL conditional job claim, guarded finishing and physical artifact commit. Expired or cancelled workers cannot commit a late result. Real child-process abrupt exit is exercised in `test_featurelab334_worker_crash.py`; the test advances the SQLite lease deadline in place of waiting 90 wall-clock seconds, then validates claim recovery and fencing.
- Fixed `_finish` reference to an undefined `cur` variable; this was a real runtime bug. Profile evidence remains unique across idempotent job reclaim. Physical materialization uses a per-attempt build file and deterministic artifact ID; it checks source bytes before and after computation, plus the live job lease at commit.
- Coordinator owns a durable per-session lease and periodically renews it. Multiple coordinators cannot advance the same session at the same time. This is a SQLite single-catalog process coordination strategy, not a claim of distributed multi-database consensus.
- Source mutation invalidates active analysis; a stale state cannot be advanced or archived as current. Formal materialization verifies source bytes on each run.
- Remaining risks: hard power failure between final file rename and SQLite transaction commit, production network storage durability and exact recovery latency under load still require acceptance on the target filesystem. These are not covered by the synthetic lease-expiry test.

## 2. v0.33.5 — data correctness and immutable reporting

- `feature_report_artifacts.py` creates an append-only Markdown snapshot identified by project/session/revision. The database saves the report's exact SHA256, source SHA, feature artifact and evaluation/BI references. A byte mismatch on read fails validation; old versions are never overwritten. Revision conflict, stale source and current-delivery blocking prevent new archive issuance.
- Added authenticated report archive/list/detail/diff endpoints. Kept existing dynamic report endpoint for inspection and export.
- The per-feature model feedback UI has an additional tested same-artifact SHA256 gate: fold frequencies render only when the evaluated artifact matches the visible FeatureSet. Model evaluation and report are pinned to real materialized FeatureSet SHA256 where available. Reviewer can inspect the full manifest and exact report revision.
- `test_featurelab335_real_duckdb_charts.py` is an **executable physical Parquet acceptance test** for the new automatic analysis: independent SQL aggregation of row counts, histogram bins, categories and time ordering vs chart API. Existing `test_featurelab332_real_duckdb.py` and `test_feature_workbench_v033.py` cover actual compiler arithmetic and grouped temporal features. **These tests are skipped in the current container because DuckDB is not installed**. A skip is a blocked production gate, not a pass.
- Perspective two-panel source/feature view and client receipt are still client-supplied evidence. Browser-native actual plotted value inspection and rendering with a locally built Perspective bundle remain **unverified** here. Do not mark production BI as independently verified based only on a JSON receipt.

## 3. v0.34.0 — guided analysis before FeatureLab

- New `feature_data_analysis.py` uses existing durable profile jobs, raw data source SHA and field contract revision; avoids model dependency and does not mutate source assets.
- The publication worker enqueues automatic profiling on completed authorized source publication; opening `/projects/{pid}/analysis` also starts an idempotent profile when necessary. The embedded Worker must be running. A 2,000,000-row/256-column automatic profile budget is enforced; large data require an explicit partition workflow.
- `GET /feature-lab/data-analysis` returns profile summary, basic quality findings and deterministic next-action recommendations. The chart endpoint computes source-pinned DuckDB numeric histograms, categorical top-counts and bounded sorted time previews. Chart API output includes scope and SHA256. Restricted fields are not exposed.
- New project **数据分析** navigation, data/version selector, status, missingness summary, real chart, recommendations and direct FeatureLab/BI links. URL restores selected source, version, field and chart type. Frontend rejects source/contract-mismatched charts. Script performs proper HTML escaping and does not invent computed statistics.
- Feature candidate details show actual verified **training-fold selection frequencies** if a same-artifact supervised evaluation exists; avoids claiming causal value or production improvement.
- Historical routes, data and Python 3.12 runtime contract remain unchanged; no dependency change is made by this release.

## 4. Automated QA and boundaries

Run on the target Mac's existing Python 3.12 environment and locally installed Chrome; do not install a new browser:

```sh
bash TIMEPILOT.sh source-check
python -m pytest -q tests/test_featurelab334_worker_crash.py tests/test_featurelab335_immutable_reports.py tests/test_featurelab340_data_analysis.py
python -m pytest -q tests/test_featurelab332_real_duckdb.py tests/test_feature_workbench_v033.py tests/test_featurelab335_real_duckdb_charts.py
node --test frontend/native-bi/tests/*.test.mjs
bash TIMEPILOT.sh db-check
bash TIMEPILOT.sh
```

Manually confirm actual Perspective data and plotted values for original vs feature output, preserving asset/version/FeatureSet ID and SHA256; compare chart totals with a fresh independent DuckDB aggregate; test a real application process kill while a long Parquet calculation is active and confirm a single committed artifact; test two simultaneous workers; test source modification while analysis is running. Also test browser back/refresh and a new upload automatically producing a data analysis summary and guidance. Record environment, versions, number of checks and result in a reproducible audit package.

The environment used to produce this release has **no DuckDB/PyArrow and no live Perspective bundle**, so the physical gates have not been marked successful. Verified fake/mocked acceptance is separately labelled. The package is a source implementation plus tests, not a production-certification claim.
