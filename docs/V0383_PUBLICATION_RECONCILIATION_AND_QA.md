# TimePilot v0.38.3 | Publication reconciliation and release review

Baseline: `TimePilot_v0.38.2_SOURCE_CANDIDATE_20261117.zip`; 2026-11-17.
This is an independently verified *source candidate*, not a production or complete-offline distribution. The original TIMEPILOT_HOME was not modified.

## 1. Root cause and scope

A publication job previously entered `failed` after a BI write/import error, but the error could have occurred *after* a Parquet snapshot or metadata record was committed. Retrying could duplicate an external artifact, and a newly named submission or the legacy synchronous publication route could bypass the failed-job state. The previous `needs_reconciliation` state contained no operator-facing proof or controlled recovery operation.

This version retains the project-first flow and existing SQLite publication queue. Changes are additive, preserving all existing source, project and published-asset references.

## 2. Implemented changes

| Component | File(s) | Contract |
| --- | --- | --- |
| Explicit review state | `project_publication.py` | A crash/exception after entering native registration or quality verification becomes `failed + needs_reconciliation`; ordinary retry and duplicate submission are blocked. Closed incidents stay closed. |
| Stable candidate identity | `generic_assets.py` | Verify source SHA and calculate immutable version identity without materializing another CSV; reuse this identity during publication. |
| Authoritative native inventory | `native_bi.py` | Enumerate metadata beyond the 200-item UI list; check unregistered `data.parquet` directories; incomplete scans or ambiguous matches do not prove absence. |
| Controlled reconciliation | `publication_reconciliation.py` | Read-only evidence snapshot; verify raw CSV, published CSV, native lineage, snapshot SHA and project/unified links. Evidence hash must match the operator decision. |
| Recovery paths | `publication_reconciliation.py` | Confirm a fully linked asset; attach one independently verified orphan/partially linked asset; retry after verified absence; or close without retry. Every action needs explicit confirmation and a substantive reason. |
| Audit | `publication_reconciliation.py` | Project-scoped append-only SQLite audit with per-job SHA256 hash chaining, UUID idempotency and terminal state committed in the same transaction. |
| API and front end | `projects_api.py`, `static/project-ui.js` | Dedicated evidence/history/decision endpoints; display review controls in project data management. Cross-project access and CSRF controls remain enforced. |
| Release | `scripts/release_gate_v0383.py`, workflow files, tests | Version-specific source/physical/browser/offline gates; dedicated uncertainty, tenant ACL and reconciliation regression cases. |

## 3. Decision matrix

| Evidence class | Allowed recovery | Automatic BI write |
| --- | --- | --- |
| `verified_complete` | `confirm_complete`, `close_without_retry` | No |
| `verified_incomplete_link` | `adopt_verified_orphan`, `close_without_retry` | No; only validated association/quality operations |
| `verified_absence` | `retry_verified_absence`, `close_without_retry` | Only after approved requeue and subsequent worker claim |
| `blocked_local_artifact` | `close_without_retry` | No |
| `blocked_ambiguous_or_invalid` | `close_without_retry` | No |
| `blocked_inventory_incomplete` | `close_without_retry` | No |

The failure cases include corrupted/modified source, unknown inventory, unregistered Parquet snapshots, multiple matching native versions and stale evidence. A `close_without_retry` action records a closed incident but does not delete files; another submission for the same project/import/region stays blocked to prevent bypass.

## 4. Scientific and engineering evidence levels

- **Source level:** SHA256 and version lineage are checked in code. The live native Parquet checksum is independently re-hashed (`verify(force=True)`) during manual review. SQLite state update and audit insertion share a transaction.
- **Test level:** Synthetic CSV + genuine SQLite state tests; mocked native BI crash-after-write injection; real metadata/file inventory scanner; API-level enterprise role/CSRF tests; Node UI contract checks.
- **Not yet verified:** Real DuckDB/Parquet numerical parity, compiled Perspective JS/WASM in installed Chrome, Python 3.12.12 + Node 24 platform offline build, factory data, concurrent multi-host writers, or signed independent audit storage. Fake-native fault tests cannot establish physical BI quality.

## 5. Remaining risks (prioritized)

- **P0:** `physical`, `browser` and `offline` release gates remain blocked until target dependencies and compiled assets are available. CI must run the physical oracle and install/restore on a supported machine before production approval.
- **P1:** Native BI registration, platform linking and audit insertion cannot be one atomic transaction across SQLite/filesystem/external integrations. The operator workflow is conservative and resumable, but multi-process races between review/commit still require a durable per-artifact reservation or external idempotency key.
- **P1:** SHA hash chaining is local tamper-evidence; a privileged administrator with full DB filesystem access could replace both audit records and hashes. External signed checkpoints and append-only audit storage are required for stronger claims.
- **P1:** `find_published_candidates` performs a bounded metadata scan and filesystem walk; large deployments need a indexed immutable source-fingerprint mapping and a recovery index. Unknown unregistered files conservatively block absence assertions.
- **P1:** Persisted worker roles are single-host; process-level CPU, memory and concurrent source scans have not been validated with 10k/100k/1M real physical data under load.
- **P2:** Extend review to full cross-service tracing and a two-person approval option for enterprise deployments.

## 6. Reproduction

```bash
python scripts/verify_installation.py --source-only
python scripts/release_gate_v0383.py --mode source
python -m pytest -q tests/test_v0383_publication_reconciliation.py tests/test_project_publication_jobs_v020.py
node --test frontend/native-bi/tests/featurelab383_reconciliation_ui.test.mjs
node --test frontend/native-bi/tests/*.test.mjs
# The following require actual CPython 3.12.12 and native BI/Perspective assets.
python scripts/release_gate_v0383.py --mode physical
python scripts/release_gate_v0383.py --mode browser
python scripts/release_gate_v0383.py --mode offline
```

`BLOCKED` is an explicit unavailable prerequisite, not a success. A regression run passing with the fake BI backend does not verify physical SQL, Chrome/WebSocket or production-ready release behavior.
