# Deploy into an existing Python environment

Use the already active Python 3.12 environment (`conda activate server` when
necessary). No conda/venv/pip environment creation is performed by the launch,
source repair or acceptance commands. From the installed project directory:

```bash
bash ~/Downloads/TimePilot_v0.5.13_REPAIR_AND_START.sh
```

The self-contained repair script includes a SHA-256 verified full source ZIP,
backs up changed source files and preserves `.env`, `runtime/`, `node_modules/`
and the database. For release verification use `--accept`; to repair without
launching use `--repair-only`.

After a successful repair, the existing `TIMEPILOT.sh` launches the API
using the current interpreter and explicitly adds the current project's `src`
to `PYTHONPATH`. Normal startup does not install Python or Node packages or
create an environment. The server listens only on localhost; set
`TIMEPILOT_PORT=8001` to choose another local port.

A source-only release intentionally excludes compiled Perspective JS/CSS/WASM.
When those are not already installed, core TimePilot can still start. For
native BI, run the explicit acceptance/build command; it may run npm package
installation if the frontend dependencies are absent. It does not download
Chrome or replace the active Python environment.
