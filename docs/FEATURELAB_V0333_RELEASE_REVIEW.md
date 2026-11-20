# TimePilot v0.33.3 - FeatureLab reliability and provenance review

## Scope and release contract

Baseline: the unmodified v0.33.2 direct-overlay release. Distribution: a **direct-copy source overlay**, containing only relative source paths that changed since v0.33.2, plus the complete new `docs/RELEASE_MANIFEST.json`. There is no installer, package manager command, dependency bootstrap or database reset. Stop the service, back up the source and TIMEPILOT_HOME data, then copy this ZIP's contents over the project root. Keep the existing Python 3.12.12 environment and data directory. The source release does not bundle compiled Perspective artifacts.

The database schema is unchanged; previously persisted analysis sessions, jobs, feature sets, evaluation records and source fingerprints remain in their existing tables.

## 1. Server-owned durable continuation

The existing embedded FeatureLab Worker now runs `FeatureAutoCoordinator.work_once()` after each `FeatureJobEngine.work_once()`. It scans persisted, active `feature_analysis_sessions`, examines their **existing** job IDs and proceeds only after their underlying jobs have terminal states. Its advance method still applies the existing optimistic session revision, source SHA and field-contract checks. The worker can re-enter the next stage after a service restart without a browser open or a front-end POST. Work is bounded to one eligible session per pass; a rotating cursor prevents a waiting session at the top of the queue from monopolizing checks.

Enterprise mode re-reads the original owner's `users.active` record and the *current* project editor permission before each background continuation. A revoked or disabled user does not silently inherit service-wide rights. Local-only mode is restricted to the saved local owner. The coordinator does not approve/publish features or create drafts on behalf of the user. Explicit human review and authorization remain necessary for FeatureSet creation and materialization.

The main UI now uses read-only `GET /analysis/sessions/{sid}` for progress polling. `POST /analysis/sessions/{sid}/advance` is retained solely for API compatibility. A new owner/editor-scoped `POST /analysis/sessions/{sid}/retry` takes `{"expected_revision": integer}` and requeues only failed underlying jobs, within the existing retry-attempt limit. A succeeded job whose bytes or provenance are corrupted is not made valid by retrying the previous success. The UI offers evaluation retry only if an underlying evaluation job was actually created; errors at the evaluation planning stage require a new review/plan. UI actions are hidden for stale sources. If the source or its confirmed field-contract revision has changed, the old session is read-only historical evidence.

## 2. Four bounded LLM modes

| Mode | Expected control flow | Verified assertion |
|---|---|---|
| Enabled/healthy | Model suggests objectives and candidates within a fixed allowlist | Allowed recommendation and trace only, no authorizing side effects |
| Disabled | All steps execute with deterministic server-side rules | No model calls; candidate analysis and report available |
| API unavailable/timeout | Error is isolated and workflow continues by rules | Existing results retained; explanation provider reports rules |
| Unauthorized tool request | Unexpected `feature.publish` model tool proposal is rejected | Proposal recorded as rejected and safe rules substituted; no publish action |

Tests use a deterministic local mock conforming to the model-call interface. They are **not** proof of a successful live third-party model API call. The model is not sent raw rows, passwords or credentials. The model's selection remains subject to computed evidence and allowed candidate IDs.

## 3. Data version, reports, BI and SHA256

An automatic session is pinned to its asset ID, version ID, source SHA256 and DataContract revision. On each progression and delivery read, the active source is compared with the stored fingerprint. The same applies to feature-set specification SHA256, the stored materialization record, the actual bytes of the Parquet artifact and the evaluation's artifact fingerprint. A mismatch causes `stale` or `blocked`, never a success verdict.

The report includes source identity, current/old-source validity, FeatureSet and artifact references, evaluation linkage and client-reported Perspective evidence. `report_sha256` is computed from the returned UTF-8 markdown bytes and is checked by tests. Reports from old sources are labeled historical and cannot be reused as current deliverables.

The BI route opened from the workbench checks current project, source asset/version, FeatureSet identity and artifact ID/SHA256 against the server's delivery proof. The viewer receipt endpoint checks matching SHA256 and requires a declared two-panel workspace. Such a receipt remains **client-reported evidence**. It cannot demonstrate a successful screenshot, correctly rendered data, or acceptance by an independent browser test. The server intentionally keeps `production_verified=false` until the separate production gate succeeds.

## 4. Automated regression evidence

Executed in a container with Python 3.13.5 and Node 22, not the target Mac Python 3.12.12 / Node 24:

- New `tests/test_featurelab333_resilience.py`: four LLM modes, server continuation and no-browser idempotence, failed-job and failed-evaluation retry with preserved artifacts and revision checks, changed contract and source SHA, report and BI proof linkage, simulated byte tampering, enterprise owner/permission revocation.
- New `frontend/native-bi/tests/featurelab333_resilience_ui.test.mjs`: failed/stale/evaluation-error views, GET-only progress polling, POST retry, BI source/feature-set ID routing restrictions.
- Focused Python tests (v0.33.3 + prior model/auto workflow/evaluation/workbench): **48 passed, 4 skipped** (see command below).
- Separate Python compatibility regression (topology, historical safety, releases, business metrics and agent receipts): **42 passed, 2 skipped**.
- Full Node suite under `frontend/native-bi/tests/*.test.mjs`: **67 passed, 0 failed**.

Python command A:

```sh
PYTHONPATH=src:tests python -m pytest -q \
 tests/test_featurelab333_resilience.py \
 tests/test_featurelab321_agent_orchestration.py \
 tests/test_featurelab322_same_artifact.py \
 tests/test_featurelab32_auto_workflow.py \
 tests/test_featurelab332_fixed_benchmarks.py \
 tests/test_featurelab332_real_duckdb.py \
 tests/test_feature_workbench_contract_v033.py \
 tests/test_feature_workbench_v033.py
```

Python command B:

```sh
PYTHONPATH=src:tests python -m pytest -q \
 tests/test_featurelab323_topology_and_p1.py \
 tests/test_featurelab324_legacy_gate_and_evidence.py \
 tests/test_featurelab324_next_action.py \
 tests/test_featurelab31_join_release.py \
 tests/test_featurelab31_business_metrics.py \
 tests/test_featurelab30_acceptance_contract.py \
 tests/test_featurelab4_agent_receipts_v0260.py
```

Node command:

```sh
node --test frontend/native-bi/tests/*.test.mjs
```

The source archive is also checked by `scripts/verify_installation.py --source-only --strict-source`. A v0.33.2 baseline + extracted v0.33.3 overlay must match the v0.33.3 manifest, with no required source missing and no mixed release versions.

## 5. Unverified and remaining limitations

1. This container has no `duckdb` or `pyarrow`. The tests mocking an artifact store write bytes for hash verification, **not an actual Parquet encoding**. Real DuckDB/Arrow materialization, the 3 v0.33.2 SQL integration tests, and artifact stream reading remain for the target Mac.
2. There is no user-specific actual LLM API account or live provider credential. LLM modes have been tested with controllable mocks, not with all provider SDK failure patterns.
3. Actual macOS Chrome with a built Perspective JavaScript/WASM bundle, real WebSocket and its original/feature panels needs user-environment acceptance. A client receipt never constitutes independent visual validation.
4. A mocked restart recreates the coordinator/service object against persistent SQLite, rather than terminating the OS process mid-write. OS crash consistency under file-system failure and multi-instance deployment remains to test.
5. Any supervised features chosen using complete-dataset exploratory information may still have selection bias. The v0.33.2 outer-fold isolation safeguards remain, and a locked independent holdout is required for real deployment claims.
6. This change runs continuation in the existing **embedded** feature worker. Installations that disable `embedded_worker` must run an equivalent worker process; GET requests will not start or advance a job.
7. End-to-end production acceptance, real plant metrics, and enterprise user acceptance have not been established in this environment.

## 6. Mac acceptance checklist

From the project root, in the existing Python 3.12.12 environment:

```sh
bash TIMEPILOT.sh source-check
python -m pytest -q tests/test_featurelab333_resilience.py tests/test_featurelab332_real_duckdb.py
node --test frontend/native-bi/tests/featurelab333_resilience_ui.test.mjs
bash TIMEPILOT.sh
```

The DuckDB tests must finish as actual **passed**, not skipped. For the browser, create a project using a real published dataset and verify (a) model enabled, disabled and failed calls, (b) user closes/reloads the tab during profiling and preview, (c) deliberate source update marks the old session stale, (d) model/BI/report refer to the same physical artifact and SHA256, and (e) corrupting a *test-only* copied artifact blocks delivery. Do not corrupt a real production dataset or production output. Retain log evidence, screenshots, manifest, run IDs and SHA256 for independent production acceptance.
