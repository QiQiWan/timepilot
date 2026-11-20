# TimePilot v0.32.0 | 智能特征分析工作台

## 1. 用户主流程

TimePilot FeatureLab now opens on a single goal-driven page. The project breadcrumb is retained, repeated feature headings and the five-item foreground stage navigation are collapsed. Legacy manual builder, evaluation, assets, tsfresh and Featuretools remain accessible through advanced tools.

The automatic path is:

1. Select an existing native source asset and describe a goal (or choose a task preset).
2. Detect record-order / elapsed-time / verified calendar-time candidates, with explicit confirmation for non-calendar semantics.
3. Run an immutable, full-scope source profile using the existing durable FeatureJobEngine.
4. Rank eligible numeric fields by full-source coverage and variability. Optionally use the configured LLM to propose a bounded field priority/template over an allowlist; deterministic fallback is available.
5. Generate candidate FeatureDefinitions using the existing validated operator registry. Run batch previews in durable, source-pinned jobs and calculate aggregate quality on full-scope computed views.
6. Explain missingness, constant features, value coverage and eligibility. Propose a default selection. Never infer predictive importance without a verified supervised task.
7. Let the user review and save a FeatureSet draft. On confirmation, materialize via the established feature run. Automatically run the existing exploratory evaluation, show the receipt and link to the BI artifact. Publish/approve remain separate and require existing policy gates.

State is persisted per project, user, source version, SHA256 and contract revision. Refreshing/re-entering can resume current tasks. Changing the source or schema invalidates old sessions. All history and raw data remain unchanged.

## 2. 文件级改造

| Component | Files | Main work |
|---|---|---|
| Auto controller | src/timepilot/feature_auto_analysis.py | Durable orchestration, bounded jobs, quality screening, structured LLM planning, draft/materialize/exploratory evaluation |
| Time interpretation | src/timepilot/feature_time_inference.py | Conservative calendar vs record index vs elapsed-time suggestions, including Chinese names |
| Contract and migrations | src/timepilot/feature_contracts.py; src/timepilot/project_migrations.py | Non-destructive Schema 34 -> 35 migration, order_index / elapsed_time fields |
| Feature candidate ranking | src/timepilot/feature_candidates.py | Whitelisted numeric fields sorted by measured coverage, without reading labels |
| API | src/timepilot/feature_lab_v2_api.py | /analysis/sessions, /analysis/latest, /time-basis/infer, /time-basis/accept, report and explanation |
| UI | src/timepilot/static/feature-auto-ui.js; feature-shell.js; project-ui.js; feature-lab2.css; index.html | One guided screen, role confirmation, evidence, coverage visualization, draft/save/materialize/BI/report |
| First-start BI | scripts/launcher.py; scripts/ensure_native_bi.py | Validate current JS/CSS/WASM source fingerprint; on missing build attempt Node 24/npm build with a lock and atomic asset activation; hard fail if real dependencies unavailable |
| Tests | tests/test_featurelab32_auto_workflow.py; frontend/native-bi/tests/featurelab32_auto_ui.test.mjs | HTTP, state, contract and mock-worker receipts, UI text/structure |

## 3. 数据与安全契约

- Physical numeric `Time=1..506` is a candidate record sequence; it cannot be manually relabeled as a calendar date. The original data column is never overwritten.
- Any model-suggested fields/templates must exist in the source contract and pass deterministic role/quality limits. Model calls receive only schema and aggregate statistics, and are disabled for restricted-classification fields. No arbitrary model-generated SQL or Python is executed.
- Labels and label-availability times must be verified before the original supervised evaluation flow can measure actual gain. Default automatic reports are unsupervised/exploratory; `predictive_value_evaluated=false` is explicit.
- The exploratory evaluation triggered after materialization is a real queued TimePilot evaluation job. It must not be represented as an accuracy or causal-effect experiment.
- Source-wide profile and candidate preview scan budget is capped at 200,000 rows in the normal guided UI. A more expensive task must be separately approved.
- User datasets and project catalogs remain in TIMEPILOT_HOME. No database deletion or reset is performed by source upgrade. Project migration v35 is additive.

## 4. 验收与限制

**Automated tests:** Python interface and feature regression suites; Node UI contract tests. The feature-worker fake in the controller test simulates completion receipts and does not demonstrate physical DuckDB computation or Perspective rendering.

**Known environmental blockers:** current QA environment lacks DuckDB, PyArrow, Perspective Python, Node 24 and a reviewed npm package-lock.json. The present artifact is therefore a source-only delivery; live numerical pipeline and native Perspective JS/WASM browser rendering require verification on a configured Mac.

**Suggested onsite acceptance (source-only deployment):**

```bash
bash TIMEPILOT.sh db-check
bash TIMEPILOT.sh source-check
bash TIMEPILOT.sh
```

The normal startup now checks for the Perspective native bundle and tries a one-time build when the pinned frontend resources are missing or stale. It cannot build without Node 24, npm access/cache and the Perspective Python dependencies. Do not advertise this source ZIP as an offline executable distribution.

**Business acceptance scenario:** import a verified industrial CSV, confirm row/index or real time, start an exploratory analysis without an API key, inspect measured candidate-quality evidence, save selection, materialize and view a SHA-bound Perspective asset; repeat with an enabled model and verify that invalid tool/field suggestions are rejected. For supervised benefit evaluation, provide labels, source decision times and label-availability times and use the separate PIT-safe evaluator.

## 5. 验收与限制

Remaining release gates: complete Mac Python 3.12.12 + Node 24 + real DuckDB/Parquet + Perspective browser tests, verify enterprise ACL and high-row-count performance, then document product-level accept/reject criteria from real industrial data. Do not mark these steps PASS without corresponding receipts.
