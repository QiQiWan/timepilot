# TimePilot v0.23.0 and v0.23.1: Conversational BI and industrial analytics

Release status: complete source candidate with API and browser-DOM regression; **real DuckDB/Arrow and the production Perspective WASM browser workflow require acceptance on a machine with those dependencies**. A stubbed browser or SQL stand-in is not a substitute for real-engine acceptance.

## 1. Separation of responsibilities

- Import: auto-discover source files, worksheets and table regions and publish immutable data versions. Do not rename columns, classify time-series targets, or force business-role mapping at import.
- BI: select a project data asset, run verified queries and calculation tools, render charts with the native Perspective viewer, and record a reversible workspace history. Optional language-model calls generate only validated tool operations.
- Modeling: build a model draft from a **confirmed, full-detail row selection** with project, native asset, pinned immutable version, original source SHA-256 and explicit filters. Query sample limits, panel filters, virtual JOINs, derived expressions or chart-only aggregations are never silently treated as training data.

## 2. Implemented in v0.23.0

| Capability | Implementation | User-visible behavior |
|---|---|---|
| Tool catalog | `bi_tool_registry.py` | JSON-schema-backed, allowlisted tool directory, discoverable by authenticated project users |
| Tool executor | `bi_agent_service.py` | Separate plan/preview/apply paths, field validation, request idempotency, optimistic revision checks |
| DuckDB executor | `bi_query_engine.py` | Safely compiled row filters, sort, projection, grouping, numeric AST expression; JSON/Arrow data responses |
| Multi-turn chat | `bi_agent_service.py`, `bi-conversation.js` | Contextual follow-up, server-side turn record, no direct raw SQL or Python execution |
| Perspective | `frontend/native-bi/src/main.js`, `native-bi.js` | Map pinned query results to native charts, validate axes, compose up to eight panels |
| History | `bi_agent_workspaces`, `bi_agent_events`, `bi_agent_views`, `bi_agent_turns` | Save personal or shared views, reopen, undo, redo, audit and source verification |

The lightweight fallback planner handles a small, deterministic subset of Chinese instructions (field-based filters, sorting, chart creation, selected arithmetic and aggregates). More general open-ended language requires a configured LLM provider. Unrecognized instructions return an explicit error; they do not invent a query result.

## 3. Implemented in v0.23.1

| Capability | Contract |
|---|---|
| Virtual multi-table JOIN | Preview exact cardinality using grouped counts first; only explicit `left` or `inner` JOIN between two accessible project assets. Max output 250,000 rows, expansion budget 5x. Require confirmation. Retain source SHA for the right table; do not alter immutable physical assets. |
| Calculated features | Validated numeric expression tree (`+`, `-`, `*`, `/`, `abs`, `round`, `coalesce`). Max recursion depth and selected-column count; no arbitrary SQL. |
| Time-series aggregation | Explicit timestamp-typed time column, window 1m, 5m, 15m, 1h or 1d; `avg`, `sum`, `min`, `max`, `count`. Numeric index columns are not interpreted as dates. |
| Moving average | Row-window average with optional entity partition, deterministic time order. |
| Industrial views | Native industrial template selection, maintenance event markers, operating regime transition, temperature/quality/equipment views, source provenance. |
| Dual/multiple-panel dashboards | Chart panel sequence saved alongside Perspective layout, version, row selection and revisions. |
| Model draft | Confirmed source-level selection, full row-count check and data hash. Creates an existing project modeling draft in `review_required` state. |

These operations reuse the v0.22.1 generic published asset directory; no additional edited BI copy is required to discover a right-hand association table.

**Deliberate limitation:** A virtual join, a computation, a resampling operation and a maintenance overlay are not materialized into a training dataset in v0.23.1. `freeze` rejects these states. Restore pure row-level filters and confirm their range before creating a model draft; use the existing reviewed data materialization path for feature-engineered training data. This safeguard prevents a chart sample from being used inadvertently as the training population.

## 4. APIs (project-scoped)

Root: `GET|POST /api/v1/projects/{project_id}/bi-agent`.

| Method | Route | Function |
|---|---|---|
| GET | `/tools` | Authorized tools and argument schemas |
| POST | `/workspaces` | Open a workspace pinned to an asset and version |
| GET | `/workspaces` | Workspace list for current account |
| GET | `/workspaces/{id}` | Current revision/state/source |
| POST | `/workspaces/{id}/turns` | Natural-language plan or execute |
| GET | `/workspaces/{id}/turns` | Conversation history |
| POST | `/workspaces/{id}/preview` | Validate actions before write |
| POST | `/workspaces/{id}/apply` | Commit allowlisted actions with request ID and revision |
| GET | `/workspaces/{id}/query` | Actual query results; bounded preview |
| GET | `/workspaces/{id}/query.arrow` | Arrow data for Perspective |
| POST | `/workspaces/{id}/undo` | Restore previous reversible state |
| POST | `/workspaces/{id}/redo` | Reapply undone state |
| POST | `/workspaces/{id}/views` | Save private or explicitly shared view |
| GET | `/views` | Saved view list |
| POST | `/workspaces/{id}/open-view` | Restore versioned view and Perspective panels |
| GET | `/views/{id}/query` | Evaluate a saved view with pinned data |
| POST | `/workspaces/{id}/freeze` | Explicitly confirm and freeze underlying row selection |
| POST | `/workspaces/{id}/model-drafts` | Create existing project modeling draft |

Sample action:

```json
{
  "expected_revision": 4,
  "request_id": "8905e66c-9a34-4c2c-941d-8339706f3d04",
  "actions": [
    {"tool": "bi.filter.set", "arguments": {"column": "A2", "op": "gt", "value": 0}},
    {"tool": "bi.sort.set", "arguments": {"field": "Time", "descending": false}},
    {"tool": "bi.chart.create", "arguments": {"type": "line", "x": "Time", "y": ["A1", "A5"]}}
  ]
}
```

`A1`, `A2` and `Time` are **example source column names** only. The tool registry does not rename or invent business meanings.

## 5. Access control and audit

- Enterprise HTTP requests inherit server principal, project role and tenant scope. No tool can select an arbitrary filesystem path, user identifier or SQL string.
- Workspace owner reads and writes are restricted. Shared saved views require explicit share permission. A reader cannot force a frozen modeling dataset or write to a different project.
- Each tool re-verifies asset access, immutable version/source fingerprints, declared columns and resource limits. The right-hand join asset must be in the same authorized project.
- Operation events record request ID, actor, before/after state digests, actions, UTC timestamp, revision; conversation turns are separately stored.
- Dynamic model output is untrusted input, and Pydantic rejects unknown fields and unauthorized tools.

## 6. UI workflow

1. Project > Data > choose a published source table > formal BI.
2. Open the conversation panel. View original source schema and active immutable version.
3. Enter a filter/sort/chart request. First preview the plan, then execute it.
4. Use history undo/redo, create calculations, add another chart, or select an explicit industrial analysis template.
5. Save the dashboard with Perspective layout, refresh and reopen the named view.
6. For table associations preview projected row counts/fanout, then explicitly approve.
7. For modeling, clear virtual overlays/joins/calculations if present, freeze the full underlying filtered source, review the dataset scope, create a modeling draft and proceed with existing manual approval.

## 7. Tests and environment boundaries

### Source-level and API regressions

Run from the existing Python 3.12 environment:

```bash
bash TIMEPILOT.sh source-check
PYTHONPATH=src python -m pytest -q tests/test_conversational_bi_v0231.py
cd frontend/native-bi && npm test
```

### Real target-machine acceptance (does not download a browser or alter user data)

```bash
bash TIMEPILOT.sh accept conversational-bi
bash TIMEPILOT.sh accept conversational-bi-ui
bash TIMEPILOT.sh accept native
```

- `conversational-bi` uses an isolated temporary project, actual import/publication, real DuckDB and Arrow, JOIN, aggregation, source-frozen selection and model draft. **Exit status 2 means BLOCKED** when DuckDB/PyArrow are missing. Never treat the mock API suite as full runtime acceptance.
- `conversational-bi-ui` launches the user's existing Chrome/Chromium, validates DOM behavior and saved panel interactions using an in-page stub. It does not validate real DuckDB or Perspective WASM.
- `native` performs real Perspective WebSocket and Chrome checks and requires the complete compiled frontend, Python 3.12 and matching source hashes.
- No release build should claim real dashboard performance or real WASM compatibility from the DOM-only stub.

### Exit criteria for production

Exact row selection parity (before/after filters); no unauthorized project access; no unbounded joins; read-only tool isolation; saved view restore on same source SHA; full DuckDB/Arrow integration; real Chrome + Perspective viewer with WASM; million-row query latency measured on deployment host. User data or sync-backed SQLite files must never be used as temporary acceptance fixtures.

## 8. Remaining work (explicitly not counted as complete)

- True computed/JOIN-derived feature materialization for trainable model datasets, with separate approval and version lineage.
- Richer natural-language multilingual interpretation, semantic tool selection quality evaluation on a fixed human-labeled prompt set.
- Full native Perspective UI end-to-end Chrome validation with real WASM/Arrow source on the user's deployment machine.
- Formal latency and concurrent multi-tenant performance benchmarking on production-like storage and workloads.
- More advanced safe JOIN variants, outlier/correlation charts and native time-index ambiguity resolution.
