#!/usr/bin/env bash
# Build machine only. Never invoked automatically on normal startup.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
command -v python >/dev/null || { echo 'Activate your existing Python 3.12 environment.' >&2; exit 2; }
command -v npm >/dev/null || { echo 'Node/npm is required ON THE BUILD MACHINE only.' >&2; exit 2; }
if [ "${1:-}" = '--bootstrap-lock' ]; then
  printf '[RELEASE] Resolve npm tree once, then check in package-lock.json.\n'
  (cd frontend/native-bi && npm install --package-lock-only --ignore-scripts --no-audit --no-fund)
fi
if [ ! -f frontend/native-bi/package-lock.json ]; then
  echo '[RELEASE BLOCKED] No reviewed package-lock.json. Run npm install --package-lock-only on a connected BUILD host and commit the lock before packaging.' >&2
  exit 2
fi
printf '[RELEASE] Installing locked dependencies in frontend build directory only.\n'
(cd frontend/native-bi && npm ci --no-audit --no-fund)
printf '[RELEASE] Compiling offline Perspective JS/CSS/WASM.\n'
python scripts/build_native_bi.py
python scripts/verify_native_bi_release.py --require-build --require-source-current
printf '[RELEASE] Packaging entire source tree, npm lock and hashed compiled assets.\n'
python scripts/package_timepilot.py --with-native-bundle --output "${TIMEPILOT_RELEASE_FILE:-$ROOT/TimePilot_v0.31.0_FULL_OFFLINE.zip}"
