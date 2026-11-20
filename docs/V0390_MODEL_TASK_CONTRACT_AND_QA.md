# TimePilot v0.39.0 -- Unified ModelTaskSpec, review and persistence

P0 scope: four business modeling families share one server-owned contract (Y, X, time,
entity, split, immutable data version and source hashes). SQLite maintains revisions,
preflight evidence, approval hash and a project-scoped event ledger. A revision change
invalidates preflight and approval. Cross-project IDs return 404, conflicting updates 409.

The existing forecasting/classification engine and FeatureLab evaluation are preserved;
there is no claim that this release introduced a unified training executor.

Endpoints: GET/POST `/api/v1/projects/{pid}/model-tasks`, GET/PATCH `/{task_id}`,
POST `/{task_id}/preflight`, POST `/{task_id}/approve`, GET `/{task_id}/events`.

One-time migrations only add new tables. No application data are erased or moved.

TimePilot v0.38.10 physical gates are retained; target Mac 3.12.12 is required for
real DuckDB/Parquet, Chromium/Perspective and full offline acceptance.

## Acceptance on the target macOS system (existing environment)

```bash
export TIMEPILOT_HOME="$HOME/TimePilot-runtime-test"
bash TIMEPILOT.sh source-check
python scripts/target_mac_acceptance_v03810.py --run --mode physical --out reports/v0390/mac_physical.json
# Before the real browser test, build reviewed Perspective JS/WASM from the committed npm lock.
python scripts/npm_lock_health.py
python scripts/setup_native_bi.py
python scripts/target_mac_acceptance_v03810.py --run --mode browser --out reports/v0390/mac_browser.json
# The following command is for the fully prepared release host, after physical/browser PASS.
bash scripts/BUILD_OFFLINE_RELEASE.sh
python scripts/target_mac_acceptance_v03810.py --run --mode offline \
  --archive TimePilot_v0.39.0_COMPLETE_OFFLINE.zip \
  --out reports/v0390/mac_offline.json
```

The acceptance command does not install or change the user's Conda environment. The separate reviewed setup/build commands install frontend dependencies in the source tree and download the platform wheelhouse when needed. It saves independent
per-gate JSON receipts. Source checks run in the current Python; physical and browser
checks require actual CPython 3.12.12, DuckDB/PyArrow, the verified native Perspective
JS/WASM bundle and target Chrome. Offline needs a reviewed npm lock and a complete
platform-specific wheelhouse. Missing prerequisites yield BLOCKED, not PASS.

Official `.github/workflows/offline-release.yml` now derives the archive name and
receipt directory from `pyproject.toml`; it cannot accidentally verify a v0.38.8 archive.
The version-specific Mac hidden workflow repair remains SHA256 guarded.
