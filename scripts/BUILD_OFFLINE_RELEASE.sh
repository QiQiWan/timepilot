#!/usr/bin/env bash
# Official full-offline distribution: build from a reviewed locked frontend,
# verify real Python/Parquet/Perspective/Chrome and the exact ZIP *before* publish.
# No runtime databases or user profiles are touched. Requires existing 3.12.12.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
if [ "$#" -ne 0 ]; then
  echo '[BLOCKED] Official releases require a committed, reviewed package-lock.json.' >&2
  echo '[BLOCKED] Bootstrap it separately, review it and include it in the source release.' >&2
  exit 2
fi
command -v python >/dev/null || { echo '[BLOCKED] Activate existing Python 3.12.12.' >&2; exit 2; }
command -v node >/dev/null || { echo '[BLOCKED] Node 24 required.' >&2; exit 2; }
command -v npm >/dev/null || { echo '[BLOCKED] npm required.' >&2; exit 2; }
python - <<'PY'
import sys
if sys.version_info[:3] != (3,12,12):
    print('[BLOCKED] Use the existing CPython 3.12.12 environment.', file=sys.stderr)
    raise SystemExit(2)
PY
case "$(node --version)" in
  v24.*) ;;
  *) echo '[BLOCKED] Official bundle build requires Node 24.' >&2; exit 2 ;;
esac
python scripts/verify_installation.py --source-only
python scripts/npm_lock_health.py
python scripts/setup_native_bi.py
python scripts/verify_native_bi_release.py --require-python31212 --require-duckdb --require-build --require-source-current --require-virtual-server
WHEELHOUSE="${TIMEPILOT_WHEELHOUSE:-$ROOT/dist/wheelhouse}"
if [ -d "$WHEELHOUSE" ]; then
  python scripts/offline_wheels.py verify --wheelhouse "$WHEELHOUSE"
else
  python scripts/offline_wheels.py prepare --wheelhouse "$WHEELHOUSE"
fi
VERSION="$(python -c 'import sys;sys.path.insert(0,"scripts");from release_integrity import project_version;print(project_version(__import__("pathlib").Path.cwd()))')"
OUT="${TIMEPILOT_RELEASE_FILE:-$ROOT/TimePilot_v${VERSION}_COMPLETE_OFFLINE.zip}"
if [ -e "$OUT" ] || [ -L "$OUT" ]; then
  echo '[BLOCKED] Refuse to overwrite a release artifact: '"$OUT" >&2
  exit 2
fi
TEMP="${OUT}.pending.$$"
if [ -e "$TEMP" ] || [ -L "$TEMP" ]; then
  echo '[BLOCKED] Candidate artifact path already exists.' >&2
  exit 2
fi
trap 'rm -f -- "$TEMP"' EXIT
# The pending archive has no official release name until all gates pass.
python scripts/package_timepilot.py --with-native-bundle --complete-offline --wheelhouse "$WHEELHOUSE" --output "$TEMP"
python scripts/verify_offline_archive_v0388.py --archive "$TEMP" --require-complete --source-root "$ROOT" --out reports/v0391/archive_integrity.json
python scripts/release_gate_v0396.py --mode offline --archive "$TEMP" --wheelhouse "$WHEELHOUSE" --out reports/v0396/release_gate_offline.json
mv -- "$TEMP" "$OUT"
trap - EXIT
printf '[RELEASED] %s\n' "$OUT"
