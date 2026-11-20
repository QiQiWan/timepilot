# TimePilot v0.40.5: Synthetic industrial acceptance and reproducible evidence

## Engineering scope

This release upgrades the quality gate and deliverable acceptance. It does not
claim new time-series architectures, million-row training, or real Mac runtime
support. It retains all v0.40.4 model-task contracts, prediction assets,
DuckDB/Parquet optional paths, and Perspective UI.

1. `synthetic_industrial.py` streams a deterministic seed-bound industrial
   telemetry table: equipment, batch, event/decision/publication/label clocks,
   temperature, vibration, loading, simulated target, binary quality labels,
   and a defined regime shift. All labels and errors are synthetic.
2. For every single source observation, it generates three prediction records,
   one for `baseline`, `linear`, and `forest`. The candidates share source-row
   identity, fold, device, clock, and target. The prediction values are
   deterministic **simulations**, not outputs from fitted models; never quote
   their ranking as TimePilot's empirical algorithm quality.
3. Negative scenarios `future_x` and `premature_label` deliberately violate
   publication time or label maturation; the REAL PIT auditor rejects them.
4. `accept_synthetic_industrial_v0405.py` verifies actual PIT/fold code,
   independent filtered-count and MAE/RMSE oracles, stable prediction row IDs,
   repeatable sequential p50/p95 and concurrent queries. It measures an O(1)
   memory streaming generator, not training on one million observations.
5. When real DuckDB and PyArrow are installed, native Parquet parity is
   executed; if absent, receipt shows `blocked` without fabricated timings.
6. Installed Chromium executes the shipped `predictionIdFromCell` JS module,
   rejecting grouped/pivoted or invalid rows. This is only a cell-event DOM
   contract: Perspective v5/WebSocket authorization and target Mac release
   remain separate blocked gates.

## Usage

Run using the existing active Python environment; no virtual environment or
network downloads are needed.

```bash
PYTHONPATH=src python scripts/accept_synthetic_industrial_v0405.py --rows 5000 --repeats 5 --workers 3 --data-dir reports/v0405/synthetic_example --out reports/v0405/synthetic_example.json
PYTHONPATH=src python scripts/accept_synthetic_industrial_v0405.py --rows 333333 --repeats 5 --workers 4 --out reports/v0405/million_validation_rows.json
python scripts/accept_prediction_browser_contract_v0405.py --out reports/v0405/chromium_contract.json
bash TIMEPILOT.sh source-check
bash TIMEPILOT.sh release-gate --mode source
bash TIMEPILOT.sh release-gate --mode physical
bash TIMEPILOT.sh release-gate --mode browser
```

`--rows` means **source observations**. Each source observation produces 3
paired prediction rows. The largest allowed command is 333,333 observations
(999,999 predictions), respecting the existing 1,000,000 prediction-row cap.

## Data contract

- `industrial_telemetry.csv`: generated sensor measurements, clocks, device,
  shift regime, quality label and numerical target.
- `validation_predictions.csv`: exact `validation-predictions-v1` column order,
  stable `__tp_rowid`, model/fold, SHA256-linked generated source-row identity.
  These are synthetic validation predictions and should not be injected as an
  authorized production prediction asset without the normal task approvals.
- `synthetic_manifest.json`: SHA256 of both CSVs, deterministic seed, physical
  case counts, candidate pairing declaration and synthetic-only flag.

The fixture cannot establish real-world accuracy, class balance, calibration,
causal feature use, downstream MES data provenance, or real-time data latency.

## Acceptance semantics

- Source QA gate: Python model regression, Node regression, new synthetic
  acceptance. The PyArrow/DuckDB-dependent tests remain skipped unless real
  modules are installed; they are not counted as passes.
- Real browser contract: actual installed Chromium but synthetic cell events;
  never mark as a Perspective physical end-to-end acceptance.
- Real physical/bundle/browser/offline: retain the historical strict target
  gates. A missing macOS Python 3.12.12, native JS/WASM or DuckDB/Parquet
  runtime remains BLOCKED.

## Next engineering steps

1. On target Mac, install the pinned real dependencies and build Perspective;
   run actual row selection, WebSocket reconnect and authorization revocation.
2. Verify real Parquet publication, 100k/240k/999999 prediction queries,
   memory RSS and actual parallel process throughput (not just Python threads).
3. Implement chunked model output and source lineage for original upload
   physical row numbering; then begin v0.41 automatic model recommendation
   only when all comparable evaluation folds are signed.
