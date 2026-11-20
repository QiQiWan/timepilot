# TimePilot v0.36.3--v0.36.4 release / verification

See `docs/ACCEPTANCE.md` for the active gates and `README.md` for installation.

The current release keeps v0.36.2 frozen datasets and independent oracles unchanged.
Run `python scripts/featurelab364_physical_acceptance.py` with real DuckDB and PyArrow installed.
This script physically loads frozen CSV, writes/reads Parquet, compares series buckets with
an independent CSV oracle, writes a rolling-mean feature Parquet and checks every row,
then verifies three supervised frozen model baselines. It writes ACCEPTANCE.json/markdown;
missing physical packages return exit code 2 with BLOCKED, failures return code 1.

Run `python scripts/featurelab362_acceptance.py --mode full --with-browser`
separately on the supported environment for the native Perspective browser gate.
No browser results, business data, production outcome or model predictive gains are
certified by the standalone physical synthetic runner.

New log endpoints: `/api/v1/system/logs` and `/api/v1/system/logs/download`.
Filtering: `level`, `source`, `q`, `since` (ISO-8601), `limit`, `offset`.
Downloads are capped at 500 entries, old logs rotate with a bounded size.
Enterprise log access is restricted to tenant administrators.

The native BI source bundle currently has no committed npm lock and no JS/WASM build.
CI's Node 24 job generates a package-lock.json on the online builder if missing,
executes `npm ci`, checks fixed top-level package versions, and uploads its lock +
compiled frontend artifact; commit the reviewed lock to make subsequent `npm ci` installations fully
reproducible. The source-only download deliberately does not claim offline readiness.
