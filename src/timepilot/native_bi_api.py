"""Native BI: strictly bounded Parquet/DuckDB endpoints (local prototype only)."""
from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, WebSocket
from fastapi.responses import Response
from starlette.websockets import WebSocketDisconnect
import starlette.websockets as _starlette_websockets
_SOCKET_CLOSED_ERRORS = tuple(set((WebSocketDisconnect, getattr(_starlette_websockets, "WebSocketDisconnected", WebSocketDisconnect))))
from urllib.parse import urlsplit
import asyncio
import os
import threading
import logging
from concurrent.futures import ThreadPoolExecutor
from pydantic import BaseModel, Field

from timepilot.native_bi import (
    NativeBIDependencyError, NativeBIError, NativeBIService, QuerySpec, ViewSpec
)
from timepilot.native_bi_edit import EditBatch, EditCommit, RollbackRequest, UndoRequest
from timepilot.native_bi_realtime import RealtimeStreamHub, StreamCreate, StreamAppend


class NativeForecastRequest(BaseModel):
    name: str
    frequency: str = 'MS'
    timestamp_col: str = 'timestamp'
    target_col: str = 'target'
    entity_col: str = 'series_id'
    available_at_col: str = 'available_at'
    source_timezone: str = 'Asia/Shanghai'
    unit: str = 'unit'


class NativeQueryDraftRequest(BaseModel):
    asset_id: str
    request: str = Field(min_length=1, max_length=1000)


class ConvertTypesRequest(BaseModel):
    expected_version: str
    name: str | None = None
    changes: dict[str, str] = Field(min_length=1, max_length=20)


logger = logging.getLogger(__name__)
PERSPECTIVE_ROOT_TABLE = 'timepilot_active'


def prepare_perspective_source(connection, effective_sql: str) -> str:
    """Publish a *lazy* revision view and confirm its actual Perspective table ID.

    The v5.6.1 DuckDB Virtual Server discovers `SHOW ALL TABLES` and exposes
    database-qualified names (`memory.timepilot_active` for an in-memory DB).
    `CREATE VIEW` leaves Parquet query execution on the server and avoids
    materializing every row into a second copy.
    """
    connection.execute('SET default_null_order=NULLS_FIRST_ON_ASC_LAST_ON_DESC')
    connection.execute(f'CREATE VIEW {PERSPECTIVE_ROOT_TABLE} AS SELECT * FROM ({effective_sql})')
    # Perspective's default DuckDB SQL model emits ORDER BY rowid (a hidden
    # DuckDB base-table pseudocolumn). This source is a VIEW, so rowid does not
    # exist. Validate the immutable snapshot row key without reading its rows.
    try:
        connection.execute(f'SELECT "__tp_rowid" FROM {PERSPECTIVE_ROOT_TABLE} LIMIT 0')
    except Exception as exc:
        raise NativeBIError('Perspective 数据源缺少稳定行键 __tp_rowid') from exc
    hosted = [f'{row[0]}.{row[2]}' for row in connection.execute('SHOW ALL TABLES').fetchall()]
    matches = [name for name in hosted if name.endswith('.' + PERSPECTIVE_ROOT_TABLE)]
    if len(matches) != 1:
        raise NativeBIError('DuckDB 已创建分析视图，但 Perspective 无法唯一识别它。'
                            f'期望 {PERSPECTIVE_ROOT_TABLE}，实际 {hosted[:12]}')
    return matches[0]


def create_timepilot_virtual_server(connection):
    """Perspective 5.6.1 DuckDB virtual server bound to TimePilot's stable row key.

    DuckDB's implicit `rowid` is available only on physical tables. Our
    immutable Parquet + versioned edit data is published through a lazy SQL
    VIEW; it already carries `__tp_rowid` from the original data import.

    Override the supported GenericSQLVirtualServerModel `row_id_expr` option
    for EACH session rather than materializing a duplicate physical table or
    altering the end user's schema. The `__tp_rowid` column is hidden from
    the public Perspective schema by the upstream `__` prefix filter.

    Keep imports lazy so the app still offers dependency diagnostics when
    optional Perspective/DuckDB packages are not installed.
    """
    import perspective
    from perspective.virtual_servers.duckdb import (
        DuckDBVirtualServer, DuckDBVirtualServerHandler, DuckDBVirtualSession,
    )

    class _TimePilotDuckDBHandler(DuckDBVirtualServerHandler):
        def __init__(self, db):
            super().__init__(db)
            self.sql_builder = perspective.GenericSQLVirtualServerModel(
                {'row_id_expr': '"__tp_rowid"'}
            )

    class _TimePilotDuckDBSession(DuckDBVirtualSession):
        def __init__(self, callback, db):
            self.session = perspective.VirtualServer(_TimePilotDuckDBHandler(db))
            self.callback = callback

    class _TimePilotDuckDBServer(DuckDBVirtualServer):
        def new_session(self, callback):
            return _TimePilotDuckDBSession(callback, self.db)

    return _TimePilotDuckDBServer(connection)


class _OrderedPerspectiveHandler:
    """WebSocket session whose native requests and teardown are strictly ordered.

    Perspective's upstream v5.6.1 Starlette reference handler submits SQL to
    an executor but calls session.close() before waiting for the queue to
    finish. A pending view request can then race with deleted Rust view state.
    Here each request is awaited in arrival order, and the session is closed
    only after the SQL executor has drained. A browser disconnect never races
    against a queued request to the same session.
    """
    def __init__(self, server, websocket, executor, loop):
        self.server = server
        self.websocket = websocket
        self.executor = executor
        self.loop = loop
        self.closing = False
        self._pending_sends = set()

    def _respond(self, content):
        if self.closing or self.loop.is_closed():
            return
        try:
            self.loop.call_soon_threadsafe(self._schedule_response, content)
        except RuntimeError:
            pass

    def _schedule_response(self, content):
        if self.closing or self.loop.is_closed():
            return
        task = self.loop.create_task(self.websocket.send_bytes(content))
        self._pending_sends.add(task)
        task.add_done_callback(self._report_response)

    def _report_response(self, task):
        self._pending_sends.discard(task)
        if task.cancelled():
            return
        try:
            err = task.exception()
        except asyncio.CancelledError:
            return
        if err is not None and not self.closing:
            logger.debug('Perspective WebSocket response failed: %s', err)

    async def run(self):
        session = self.server.new_session(self._respond)
        try:
            await self.websocket.accept()
            while True:
                frame = await self.websocket.receive_bytes()
                # Official Perspective VirtualServer mutates per-session Rust
                # view registries. Never process the same session concurrently.
                future = self.executor.submit(session.handle_request, frame)
                await asyncio.wrap_future(future)
                # Drain response callbacks before processing the next request.
                # This keeps ViewDelete -> ViewSchema callbacks ordered and
                # removes another source of stale Rust view registries.
                if self._pending_sends:
                    await asyncio.gather(*tuple(self._pending_sends),return_exceptions=True)
        finally:
            self.closing = True
            # Drain native work BEFORE session.close(). In the official Starlette
            # reference implementation these operations occur in reverse order.
            # Close the native session on the SAME executor that handled its
            # requests. This prevents thread-affinity and teardown races.
            try:
                await asyncio.wrap_future(self.executor.submit(session.close))
            finally:
                await asyncio.to_thread(self.executor.shutdown)
            if self._pending_sends:
                for task in tuple(self._pending_sends):
                    task.cancel()
                await asyncio.gather(*tuple(self._pending_sends), return_exceptions=True)


class _TimedPerspectiveExecutor:
    """A single threaded request executor with DuckDB interrupt watchdog.

    Each Perspective request has a bounded execution window. Connection lifetime
    and per-request limits are separate. This is an in-process safeguard, not
    OS-level resource isolation or a full query planner governor.
    """
    def __init__(self, connection=None):
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='tp-native-ws')
        self._connection = connection
        self._seconds = max(1, min(60, int(os.getenv('TIMEPILOT_BI_WS_QUERY_TIMEOUT', '15'))))

    def submit(self, fn, *args, **kwargs):
        def bounded():
            timer = None
            if self._connection is not None:
                timer = threading.Timer(self._seconds, self._connection.interrupt)
                timer.daemon = True
                timer.start()
            try:
                return fn(*args, **kwargs)
            finally:
                if timer is not None:
                    timer.cancel()
        future = self._pool.submit(bounded)
        # Perspective's reference Starlette handler does not inspect Future
        # results. Log worker failures instead of silently swallowing them.
        def report_failure(done):
            if done.cancelled():
                return
            error = done.exception()
            if error is not None:
                logger.error('Perspective SQL request failed',
                             exc_info=(type(error), error, error.__traceback__))
        future.add_done_callback(report_failure)
        return future

    def shutdown(self):
        self._pool.shutdown(wait=True, cancel_futures=True)


def create_native_bi_router(native: NativeBIService, legacy, settings, models=None, data_assets=None):
    router = APIRouter(prefix="/api/v1/native-bi", tags=["Native BI"])
    stream_hub = RealtimeStreamHub()
    # Process-local slots and per-session thread pools prevent unrestricted WS growth.
    # Multi-process quotas, per-statement cancellation, RBAC remain enterprise blockers.
    ws_slots = threading.BoundedSemaphore(max(1, min(8, int(os.getenv('TIMEPILOT_BI_WS_MAX', '2')))))
    ws_timeout = max(30, min(3600, int(os.getenv('TIMEPILOT_BI_WS_MAX_AGE_SECONDS', '600'))))

    def allowed_origin(websocket: WebSocket) -> bool:
        host = websocket.headers.get('host', '')
        origin = websocket.headers.get('origin')
        remote = websocket.client.host if websocket.client else ''
        if settings.local_mode and remote not in {'127.0.0.1', '::1', 'localhost', 'testclient'}:
            return False
        if not origin:
            # A browser must provide Origin. This local prototype does not accept
            # non-browser WebSocket clients without origin authentication.
            return False
        parsed = urlsplit(origin)
        if not (parsed.scheme in {'http', 'https'} and parsed.netloc == host and bool(host)):
            return False
        security=getattr(websocket.app.state,'security',None)
        if security is None:
            return True
        if parsed.scheme!='https' or '/streams/' in websocket.url.path:
            return False
        from timepilot.enterprise_security import SESSION_COOKIE,SecurityError
        principal=security.authenticate(websocket.cookies.get(SESSION_COOKIE))
        if principal is None:
            return False
        project_id=websocket.query_params.get('project_id')
        if not project_id:
            return False
        import re
        match=re.search(r'/((?:assets)|(?:linked))/([^/]+)/versions/([^/]+)(?:/feature-artifacts/[^/]+)?/ws$',websocket.url.path)
        if not match:
            return False
        try:
            security.asset_access(principal,project_id,match.group(2),linked=(match.group(1)=='linked'))
            return True
        except (SecurityError,ValueError):
            return False

    async def run_limited_websocket(websocket: WebSocket, server, *, connection=None):
        if not allowed_origin(websocket):
            await websocket.close(code=1008, reason='Same-origin WebSocket required')
            return
        if not ws_slots.acquire(blocking=False):
            await websocket.close(code=1013, reason='BI connections busy')
            return
        executor = None
        try:
            executor = _TimedPerspectiveExecutor(connection)
            handler = _OrderedPerspectiveHandler(
                server, websocket, executor, asyncio.get_running_loop())
            await asyncio.wait_for(handler.run(), timeout=ws_timeout)
        except _SOCKET_CLOSED_ERRORS as exc:
            # Starlette added WebSocketDisconnected (distinct from its older
            # WebSocketDisconnect) in newer releases. Both are expected on
            # reload/navigation and must not generate ASGI 500 tracebacks.
            code = getattr(exc, 'code', 1001)
            if code not in (1000, 1001):
                logger.info('Perspective WebSocket peer disconnected, code=%s', code)
        except asyncio.TimeoutError:
            if connection is not None:
                connection.interrupt()
            try:
                await websocket.close(code=1001, reason='BI session lifetime exceeded')
            except (*_SOCKET_CLOSED_ERRORS, RuntimeError):
                pass  # Peer closed while the deadline was being enforced.
        finally:
            # Session cleanup already drained the executor. An interrupt is
            # reserved for timed-out/cancelled work, not normal tab closure.
            if executor is not None:
                await asyncio.to_thread(executor.shutdown)
            ws_slots.release()


    def fail(exc: Exception):
        if isinstance(exc, KeyError):
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if isinstance(exc, NativeBIDependencyError):
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        if isinstance(exc, (NativeBIError, ValueError)):
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        raise exc

    @router.websocket('/assets/{asset_id}/versions/{version_id}/ws')
    async def live_perspective(websocket: WebSocket, asset_id: str, version_id: str):
        """Official Perspective DuckDB Virtual Server over a same-origin WebSocket.

        Each session is pinned to one immutable asset revision and gets its own
        DuckDB connection. A new revision requires a *new* WebSocket/session;
        stale views cannot silently become charts of a different dataset.
        """
        if not allowed_origin(websocket):
            await websocket.close(code=1008, reason='Same-origin WebSocket required')
            return
        try:
            meta = native.asset(asset_id)
            native._version(asset_id, version_id)
            with native._connection() as con:
                # System row key retained for native-grid editing; version is immutable.
                effective = native._effective_sql(asset_id, version_id, meta['columns'])
                exposed = prepare_perspective_source(con, effective)
                logger.debug('Perspective asset=%s version=%s hosted_table=%s',
                             asset_id, version_id, exposed)
                server = create_timepilot_virtual_server(con)
                await run_limited_websocket(websocket, server, connection=con)
        except ImportError:
            await websocket.close(code=1011, reason='Perspective Python dependency missing')
        except (NativeBIError, ValueError, KeyError):
            await websocket.close(code=1008, reason='Invalid asset or immutable data revision')

    @router.websocket('/assets/{asset_id}/versions/{version_id}/feature-artifacts/{artifact_id}/ws')
    async def feature_comparison_perspective(websocket:WebSocket,asset_id:str,version_id:str,artifact_id:str):
        # One server; source and immutable derived result share its connection.
        # No original bytes or arbitrary filesystem paths pass through the client.
        if not allowed_origin(websocket):
            await websocket.close(code=1008,reason='Same-origin project access required')
            return
        pid=websocket.query_params.get('project_id')
        jobs=getattr(websocket.app.state,'feature_jobs',None)
        registry=getattr(websocket.app.state,'feature_registry',None)
        if not pid or jobs is None or registry is None:
            await websocket.close(code=1008,reason='FeatureLab project scope missing')
            return
        try:
            registry.projects.require_native(pid,asset_id)
            with registry.projects._db() as db:
                linked=db.execute('''SELECT r.set_id FROM feature_artifacts a
                    JOIN feature_runs r ON r.run_id=a.run_id
                    JOIN feature_sets f ON f.set_id=r.set_id AND f.project_id=a.project_id
                    WHERE a.project_id=? AND a.artifact_id=? AND f.asset_id=?
                      AND f.version_id=?''',(pid,artifact_id,asset_id,version_id)).fetchone()
            if not linked:raise ValueError('特征物化结果或来源不存在')
            rec=registry.get(pid,linked['set_id'])
            from timepilot.feature_artifact_policy import eligible_artifact
            semantic=getattr(websocket.app.state,'feature_semantic',None)
            candidate=eligible_artifact(registry.projects,pid,rec,semantic,artifact_id=artifact_id)
            if candidate is None:raise ValueError('特征结果不满足成功、版本及文件完整性要求')
            path=candidate['path']
            meta=native.asset(asset_id)
            native._version(asset_id,version_id)
            with native._connection() as con:
                source_sql=native._effective_sql(asset_id,version_id,meta['columns'])
                prepare_perspective_source(con,source_sql)
                # Path is server-resolved from an authorized row; do not accept
                # any user-provided SQL, identifier, or filesystem path.
                path_sql=str(path).replace("'","''")
                con.execute('''CREATE VIEW timepilot_feature_artifact AS
                  SELECT ROW_NUMBER() OVER ()-1 AS "__tp_rowid", *
                  FROM read_parquet('''+"'"+path_sql+"'"+''')''')
                server=create_timepilot_virtual_server(con)
                await run_limited_websocket(websocket,server,connection=con)
        except ImportError:
            await websocket.close(code=1011,reason='Perspective or DuckDB missing')
        except (KeyError,ValueError,NativeBIError):
            await websocket.close(code=1008,reason='Feature artifact unavailable')

    @router.websocket('/linked/{asset_id}/versions/{version_id}/ws')
    async def linked_asset_perspective(websocket: WebSocket, asset_id: str, version_id: str):
        """Read-only Perspective view of a shared immutable DataAsset.

        No duplicated Parquet copy and no edit API for linked source versions.
        The existing workbench remains available without DuckDB/Perspective.
        """
        if not allowed_origin(websocket):
            await websocket.close(code=1008,reason='Same-origin WebSocket required')
            return
        if data_assets is None:
            await websocket.close(code=1011,reason='DataAsset catalog unavailable')
            return
        try:
            import json
            from timepilot.smart_workbench import ViewSelection
            raw=websocket.query_params.get('selection','')
            if len(raw)>4096:
                raise ValueError('BI selection exceeds the URL safety limit')
            selection=ViewSelection.model_validate(json.loads(raw)) if raw else ViewSelection()
            sql=data_assets.native_sql_source(asset_id,version_id,selection)
            with native._connection() as con:
                # Cursor order is fixed for stable per-session row IDs.
                con.execute('SET threads=1')
                con.execute('SET preserve_insertion_order=true')
                prepare_perspective_source(con,sql)
                server=create_timepilot_virtual_server(con)
                await run_limited_websocket(websocket,server,connection=con)
        except (NativeBIError,ValueError,KeyError):
            await websocket.close(code=1008,reason='Invalid linked asset or revision')
        except ImportError:
            await websocket.close(code=1011,reason='Perspective dependencies missing')

    @router.get('/streams')
    def list_streams():
        return stream_hub.list()

    @router.post('/streams', status_code=201)
    def create_stream(payload: StreamCreate):
        try:
            return stream_hub.create(payload)
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=503 if isinstance(exc, RuntimeError) else 422,
                                detail=str(exc)) from exc

    @router.get('/streams/{stream_id}')
    def stream_info(stream_id: str):
        try:
            return stream_hub.get(stream_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post('/streams/{stream_id}/append')
    def stream_append(stream_id: str, payload: StreamAppend):
        try:
            return stream_hub.append(stream_id, payload)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.delete('/streams/{stream_id}')
    def stream_delete(stream_id: str):
        try:
            return stream_hub.delete(stream_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.websocket('/streams/{stream_id}/ws')
    async def realtime_websocket(websocket: WebSocket, stream_id: str):
        try:
            server, _ = stream_hub.server_and_table(stream_id)
            await run_limited_websocket(websocket, server)
        except KeyError:
            await websocket.close(code=1008, reason='Stream does not exist')
        except ImportError:
            await websocket.close(code=1011, reason='Perspective Python dependency missing')

    @router.post('/assets/{asset_id}/derive-types', status_code=201)
    def derive_types(asset_id: str, payload: ConvertTypesRequest):
        try:
            return native.derive_types(asset_id, **payload.model_dump())
        except Exception as exc:
            fail(exc)

    @router.get("/capabilities")
    def capabilities():
        return native.capabilities()

    @router.get("/assets")
    def list_assets():
        return native.list_assets()

    @router.post("/assets/upload", status_code=201)
    async def import_file(file: UploadFile = File(...), name: str = Form(...)):
        # The path is created by the server, never passed by a browser or agent.
        content = await file.read(settings.max_upload_bytes + 1)
        try:
            return native.import_file(content, file.filename or "data.csv", name)
        except Exception as exc:
            fail(exc)

    @router.post("/assets/from-legacy/{table_id}", status_code=201)
    def migrate_legacy(table_id: str):
        try:
            return native.migrate_legacy(legacy, table_id)
        except Exception as exc:
            fail(exc)

    @router.get("/assets/{asset_id}")
    def asset(asset_id: str):
        try:
            return native.asset(asset_id)
        except Exception as exc:
            fail(exc)

    @router.get("/assets/{asset_id}/verify")
    def verify(asset_id: str):
        try:
            return native.verify(asset_id)
        except Exception as exc:
            fail(exc)

    @router.get("/assets/{asset_id}/profile")
    def profile(asset_id: str, sample_rows: int = 5000):
        try:
            return native.profile(asset_id, sample_rows=sample_rows)
        except Exception as exc:
            fail(exc)

    @router.post("/query")
    def query(spec: QuerySpec):
        try:
            return native.query(spec)
        except Exception as exc:
            fail(exc)

    @router.post('/query.arrow')
    def query_arrow(spec: QuerySpec):
        try:
            pinned = spec.version_id or native._head(spec.asset_id)
            request = spec.model_copy(update={'version_id': pinned})
            payload = native.query_arrow(request)
            return Response(content=payload, media_type="application/vnd.apache.arrow.stream",
                            headers={"Cache-Control":"no-store", "X-TimePilot-Asset": spec.asset_id,
                                     "X-TimePilot-Version": pinned, "X-TimePilot-Source": "duckdb_query"})
        except Exception as exc:
            fail(exc)

    @router.get('/assets/{asset_id}/versions')
    def versions(asset_id: str):
        try:
            return native.history(asset_id)
        except Exception as exc:
            fail(exc)

    @router.get('/assets/{asset_id}/versions/verify')
    def verify_versions(asset_id: str):
        try:
            return native.verify_history(asset_id)
        except Exception as exc:
            fail(exc)

    @router.post('/edits/preview')
    def preview_edit(batch: EditBatch):
        try:
            return native.preview_edit(batch)
        except Exception as exc:
            fail(exc)

    @router.post('/edits/commit', status_code=201)
    def commit_edit(request: EditCommit):
        try:
            return native.commit_edit(request)
        except Exception as exc:
            fail(exc)

    @router.post('/assets/{asset_id}/undo', status_code=201)
    def undo(asset_id: str, request: UndoRequest):
        try:
            return native.undo(asset_id, request)
        except Exception as exc:
            fail(exc)

    @router.post('/assets/{asset_id}/rollback', status_code=201)
    def rollback(asset_id: str, request: RollbackRequest):
        try:
            return native.rollback(asset_id, request)
        except Exception as exc:
            fail(exc)

    @router.post('/assets/{asset_id}/to-timeseries', status_code=201)
    def to_timeseries(asset_id: str, request: NativeForecastRequest):
        try:
            return native.to_timeseries(asset_id, **request.model_dump())
        except Exception as exc:
            fail(exc)

    @router.post('/assistant/query-draft')
    def query_draft(request: NativeQueryDraftRequest):
        """LLM creates a schema-validated read-only QuerySpec, never executable SQL."""
        if models is None:
            raise HTTPException(status_code=503, detail='LLM gateway is not installed')
        if models.general()['assistant_mode']=='off':
            raise HTTPException(status_code=409, detail='智能辅助已关闭')
        try:
            asset = native.asset(request.asset_id)
            return models.plan_native_query(request.request, asset)
        except Exception as exc:
            fail(exc)

    @router.get("/assets/{asset_id}/sample.arrow")
    def sample_arrow(asset_id: str, limit: int = 5000, version_id: str | None = None):
        try:
            pinned = version_id or native._head(asset_id)
            payload = native.preview_arrow(asset_id, limit=limit, version_id=pinned)
            return Response(content=payload, media_type="application/vnd.apache.arrow.stream",
                            headers={"Cache-Control": "no-store", "X-TimePilot-Sample-Limit": str(min(10000, max(1, limit))),
                                     "X-TimePilot-Asset": asset_id, "X-TimePilot-Version": pinned,
                                     "X-TimePilot-Source": "bounded_sample"})
        except Exception as exc:
            fail(exc)

    @router.get("/assets/{asset_id}/views")
    def saved_views(asset_id: str):
        try:
            return native.list_views(asset_id)
        except Exception as exc:
            fail(exc)

    @router.post("/views", status_code=201)
    def save_view(spec: ViewSpec):
        try:
            return native.save_view(spec)
        except Exception as exc:
            fail(exc)

    @router.get("/views/{view_id}")
    def get_view(view_id: str):
        try:
            return native.get_view(view_id)
        except Exception as exc:
            fail(exc)

    return router
