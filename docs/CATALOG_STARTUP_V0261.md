# TimePilot v0.26.1: Safe catalog bootstrap, upgrade and runtime home

## The fixed startup failure

Prior source releases advanced `project_migrations.TARGET_SCHEMA` to 30 but
`ProjectCatalog._setup()` still rejected all on-disk schema versions 18-30.
A perfectly valid v30 catalog could start once and then fail on the next
startup with an "unknown project database version" error.

The v0.26.1 fix accepts the full range of known versions and uses the current
migration target as the single source of truth. A clean installation creates
both `projects/catalog.sqlite3` and `metadata.sqlite3` automatically. Existing
catalogs v1-29 use the reviewed forward-only migration path, and v30 is
reopened without resetting records. Newer/unknown/corrupt catalogs are never
renamed, overwritten, or downgraded.

## Commands (use your existing Conda Python 3.12 environment)

```bash
conda activate server
bash TIMEPILOT.sh db-check      # read-only: prints exact runtime data paths
bash TIMEPILOT.sh source-check  # validates source manifest
bash TIMEPILOT.sh              # initializes/updates catalog as needed
bash TIMEPILOT.sh accept catalog-bootstrap-v0261
bash TIMEPILOT.sh accept catalog-bootstrap-v0261 --mode configured
```

`db-check` does not create the database or change user records. It checks the
project catalog version/integrity and validates any existing metadata schema.
The `configured` acceptance copies a live catalog using SQLite's backup API
into an isolated temporary directory; it does not migrate the live database.
Other TimePilot integration tests (DuckDB/Arrow/Perspective) have independent
acceptance gates.

## Default data directory: source and user data are separate

If `TIMEPILOT_HOME` is set, it is always respected. Without an override:

1. If an existing `<source>/runtime` folder contains data, it stays in use
   (compatibility; no automatic migration).
2. Otherwise the stable `~/.timepilot` folder is used for a new installation.

Removing or replacing a source directory therefore does not delete new
installations' datasets and model history. For existing installations the
location shown by `db-check` is authoritative; the application never infers
where a deleted database went.

When using OneDrive, iCloud Drive, or Dropbox for source code, keep the SQLite
and Parquet runtime **outside** the synced directory. To move an existing
runtime, stop the server, create a verified backup (using the existing
`TIMEPILOT.sh backup --output /path/outside/runtime-backup.zip` command), copy
the entire runtime directory including journals after shutdown, set
`TIMEPILOT_HOME` to the copied directory, and run `db-check` before restarting.
Do not copy a live SQLite `.sqlite3` file without its WAL or use cloud-sync
as the backup mechanism.

## Safety boundaries

- Fresh: a missing or empty SQLite catalog is initialized to schema v30.
- Old v1-29: committed-WAL SQLite backup, transaction-protected migrations.
- Current v30: idempotent reopen, no accidental version rejection.
- Newer than v30: blocked; use matching newer code, no downgrade.
- Non-TimePilot file, missing ledger, corrupt file: blocked without reset.
- Two concurrent first starts: an OS advisory lock serializes migration,
  with automatic release if a process crashes.
- Existing SQLAlchemy metadata columns are checked before `create_all()`
  can add unrelated tables. Unknown metadata files are also blocked.

The pre-upgrade backup is named
`projects/catalog.pre-vX-to-v30.<UTC timestamp>.sqlite3`. The broader
pre-release snapshots remain in `.schema_backups/`.

Do not manually set `project_schema.version` to 4/17/30. The value is a
migration ledger, not a user-selectable application setting.
