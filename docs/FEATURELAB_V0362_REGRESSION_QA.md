# TimePilot v0.36.2 - Frozen synthetic regression QA

## Status and scope

Source baseline: v0.36.1. This release adds frozen independent validation, actual
SQLite/HTTP workflow tests, numerical fold-evaluator regression checks, and
long-lived CI gates. It does not add new modeling algorithms or schema migrations.

Three different levels of evidence must remain separate:

| Level | Evidence | Allowed conclusion |
| --- | --- | --- |
| S0 fixed data | 8 source SHA256 files; independent Python CSV oracle | The fixed sample was not changed and expected counts remain correct |
| S1 functionality | HTTP import/publication/workspace; supervised train/validation folds; permissions, retry and provenance tests; Node UI | Offline code logic passed with synthetic data and an explicit NativeBI test double |
| S2 physical | DuckDB, actual Parquet, source-bound time chart, full synthetic E2E and native Perspective browser | Real physical synthetic pipeline works; cannot certify plant forecasting quality |
| S3 industrial | Independent production data, field meanings, observed business outcomes and ongoing monitoring | Only this level can support claims of production suitability |

## Stable test corpus and oracles

- `benchmarks/featurelab_v0362/BASELINE.json`: exact hashes, row/column shapes,
  missing=24, invalid timestamp=10, out-of-range=8, duplicate ID=12, time,
  maintenance, regime A-B-A-C, 12-bucket 2-device min/mean/max reference.
- `benchmarks/featurelab_v0362/MODEL_BASELINE.json`: three fixed evaluation
  protocol results, split manifest hash, selected feature frequencies and
  allowed tolerances (MAE 0.05; AP 0.005).
- The fixture generator (`featurelab361_fixtures.py`) writes to a separate QA
  folder. It never updates approved goldens. Changing the generator alone
  cannot convert a drift into a passing result.
- Data are synthetic, have leakage-trap columns and contain no production
  metrics. Leakage traps are restricted by explicit field contract. TimePilot
  cannot infer every post-outcome field from a name alone.

## End-to-end state audit

The HTTP/SQLite test uses a NativeBI registration double so it can run without
DuckDB; it verifies the actual import API, publication job status, project
isolation, asset-version selection, idempotent background preview initiation,
read-only refresh, and no unsanctioned materialization or model evaluation.
The 500-row dirty CSV is published without silent mutation. A downstream
field/quality contract still needs to classify its invalid timestamps and
outliers; retaining raw data is not the same as declaring them valid.

The existing real worker crash, model API enabled/disabled/error/unauthorized,
report SHA256, and cross-version tests are incorporated as separate gates.

## Reproduction on active Python environment

```bash
python scripts/featurelab362_acceptance.py --mode smoke
python scripts/featurelab362_acceptance.py --mode full --with-browser --with-performance
```

Expected outputs: `reports/featurelab362_acceptance/AUDIT.json`, `AUDIT.md`,
`NUMERIC_MODEL_EVIDENCE.json`, execution logs and the archive in
`reports/featurelab362_acceptance.zip`. Raw CSVs stay in
`reports/featurelab362_data/` and are excluded from the log archive. No
new Python environment, package or Chrome download is performed.

If a fixed dataset or model metric genuinely changes, create a separately
reviewed revision of `BASELINE.json` / `MODEL_BASELINE.json` with an explanation
of the difference. Never regenerate goldens automatically during acceptance.

## Unclosed points and next actions

1. S2 physical tests require DuckDB, PyArrow and installed Perspective resources.
   The code cannot claim S2 passed until the respective tests executed.
2. A test-double HTTP workflow does not prove actual Parquet contents.
3. A native browser receipt must be source-bound and show the real charts;
   Node component tests are insufficient.
4. For post-outcome leakage, explicit semantic field classification remains
   a required human input; an unknown future-leak field should not be treated
   as safe simply because it parses as a number.
5. Business suitability needs a separate final holdout, prospective testing,
   and actual equipment/operating-condition data.
