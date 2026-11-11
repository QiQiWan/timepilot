#!/usr/bin/env bash
# Single Mac/Linux entry. Running with no arguments starts the local server.
set -euo pipefail
PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_ROOT"
# On macOS the Node 24 binary may live under nvm and be absent from Finder's
# non-interactive PATH. Reuse an already installed local nvm runtime; never
# download Node or change the active Conda/Python environment.
if [ -s "${NVM_DIR:-$HOME/.nvm}/nvm.sh" ]; then
  current_node="$(node --version 2>/dev/null || true)"
  case "$current_node" in
    v24.*) ;;
    *)
      set +u
      # nvm is implemented as a shell function, so it must be sourced here.
      if . "${NVM_DIR:-$HOME/.nvm}/nvm.sh"; then
        nvm use --silent 24 >/dev/null 2>&1 || true
      fi
      set -u
      ;;
  esac
fi
command -v python >/dev/null 2>&1 || { echo '[FAIL] Activate your EXISTING Python 3.12 environment.' >&2; exit 2; }
export PYTHONPATH="$PROJECT_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
export TIMEPILOT_HOME="${TIMEPILOT_HOME:-$PROJECT_ROOT/runtime}"
exec python "$PROJECT_ROOT/scripts/launcher.py" "$@"
