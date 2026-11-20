"""FastAPI: local research and opt-in, tenant-scoped enterprise workbench."""
from __future__ import annotations

import csv
import io
import json
import os
import threading
import logging
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from timepilot import __version__
from timepilot.contracts import TaskSpec
from timepilot.data import DataValidationError, create_dataset, get_dataset
from timepilot.demo import bootstrap
from timepilot.sdk import TimePilot
from timepilot.bi import BIService
from timepilot.llm import ModelGateway
from timepilot.bi_api import create_bi_router
from timepilot.native_bi import NativeBIService
from timepilot.native_bi_api import create_native_bi_router
from timepilot.smart_import import SmartImportService
from timepilot.smart_import_api import create_smart_import_router
from timepilot.smart_workbench import SmartWorkbench
from timepilot.smart_workbench_api import create_smart_workbench_router
from timepilot.data_understanding import DataUnderstandingService
from timepilot.data_understanding_api import create_data_understanding_router
from timepilot.data_assets import DataAssetCatalog
from timepilot.data_assets_api import create_data_assets_router
from timepilot.agent_tools import default_registry, ToolServices
from timepilot.agent_executor import DurableExecutor
from timepilot.agent_api import create_agent_router
from timepilot.research_api import create_research_router
from timepilot.semantic_benchmark import SemanticBenchmark
from timepilot.auto_experiment import AutonomousExperiment
from timepilot.mechanisms.identification import MechanismStudy
from timepilot.project_import_jobs import ProjectImportJobs
from timepilot.project_publication import ProjectPublicationJobs
from timepilot.projects import ProjectCatalog
from timepilot.projects_api import create_projects_router
from timepilot.modeling_api import create_modeling_router
from timepilot.modeling_workflow import ModelingWorkbench
from timepilot.native_bi_bundle import bundle_status, frontend_matches_bundle
from timepilot.enterprise_security import EnterpriseSecurity, EnterpriseGuard, identity_router, SecurityError


class RunCreate(BaseModel):
    task: TaskSpec
    dataset: str


def create_app(*, home: str | Path | None = None, embedded_worker: bool = True):
    """Serialize first-run SQLite schema migrations across API and Worker processes.

    Role leases protect execution; this separate short-lived mutex protects
    multi-step startup DDL across the legacy catalog and task tables.
    """
    from timepilot.config import Settings
    from timepilot.startup_guard import startup_guard
    with startup_guard(Settings.from_env(home).home):
        return _create_app_unlocked(home=home, embedded_worker=embedded_worker)


def _create_app_unlocked(*, home: str | Path | None = None, embedded_worker: bool = True):
    # Direct `python -m timepilot serve` must check a catalog before the
    # separate SQLAlchemy metadata DB or business services are initialized.
    from timepilot.config import Settings
    from timepilot.project_migrations import require_supported_catalog
    settings = Settings.from_env(home)
    from timepilot.system_logs import setup_logging
    setup_logging(settings.home)
    require_supported_catalog(settings.home / 'projects' / 'catalog.sqlite3')
    client = TimePilot(home=home)
    enterprise = os.getenv('TIMEPILOT_ENTERPRISE_MODE','0') == '1'
    if not client.settings.local_mode and not enterprise:
        raise RuntimeError('非本地服务必须显式启用 TIMEPILOT_ENTERPRISE_MODE=1')
    if enterprise:
        from urllib.parse import urlsplit
        public_url=os.getenv('TIMEPILOT_PUBLIC_BASE_URL','')
        origin=urlsplit(public_url)
        if origin.scheme!='https' or not origin.netloc or origin.path not in ('','/'):
            raise RuntimeError('企业模式必须配置 HTTPS 的 TIMEPILOT_PUBLIC_BASE_URL（TLS 在可信反向代理终止）')
        security = EnterpriseSecurity(client.settings.home,require_admin=True)
    else:
        security = None
    bi = BIService(client.store, client.settings)
    models = ModelGateway(client.store, client.settings)
    native = NativeBIService(client.store, client.settings)
    smart_import = SmartImportService(client.settings.home)
    workbench = SmartWorkbench(smart_import,client.store,client.settings,client.engine)
    understanding = DataUnderstandingService(smart_import,models)
    data_assets = DataAssetCatalog(smart_import,workbench,legacy_bi=bi)

    from timepilot.worker_runtime import choose_agent_tool, feature_cycle, publication_cycle
    def _model_choose(request: dict, allowed: list[str], evidence: dict) -> str:
        return choose_agent_tool(models, request, allowed, evidence)

    semantic_benchmark = SemanticBenchmark(client.settings.home, smart_import, models)
    mechanism_study = MechanismStudy(client.settings.home)
    auto_experiments = AutonomousExperiment(client.settings.home, data_assets, client.plugins, mechanisms=mechanism_study)

    projects = ProjectCatalog(client.settings.home, smart_import, data_assets,
                              engine=client.engine, experiments=auto_experiments,security=security)
    projects.backfill_legacy_once()
    projects.link_legacy_views_once()
    import_jobs = ProjectImportJobs(projects)
    publication_jobs = ProjectPublicationJobs(projects,native)
    modeling = ModelingWorkbench(projects, native)
    from timepilot.model_task_store import ModelTaskStore
    model_tasks = ModelTaskStore(projects, modeling)
    from timepilot.model_task_execution import ModelTaskRunService
    model_task_runs = ModelTaskRunService(model_tasks, modeling)
    from timepilot.model_task_temporal_bridge import TemporalTaskBridge
    temporal_model_bridge = TemporalTaskBridge(model_tasks, modeling, model_task_runs)
    from timepilot.model_task_unified_runs import ModelTaskUnifiedRuns
    unified_model_runs = ModelTaskUnifiedRuns(model_tasks, model_task_runs, temporal_model_bridge)

    from timepilot.generic_assets import GenericAssetStore
    from timepilot.data_platform import DataPlatform
    generic_assets = GenericAssetStore(smart_import)
    data_platform = DataPlatform(projects,native,generic_assets)
    from timepilot.bi_agent_service import BIWorkspaceService
    from timepilot.bi_agent_api import create_bi_agent_router
    bi_agent = BIWorkspaceService(projects,native,data_platform,modeling=modeling,models=models)
    from timepilot.derived_assets import DerivedAssetService
    from timepilot.derived_assets_api import create_derived_router
    derived = DerivedAssetService(bi_agent)
    modeling.derived = derived
    agent_executor = DurableExecutor(client.settings.home / 'agent_workflows',
        default_registry(), ToolServices(understanding, data_assets, workbench, bi, client.settings,
                                  benchmark=semantic_benchmark, experiments=auto_experiments,
                                  mechanism=mechanism_study,projects=projects,
                                  generic=generic_assets,native=native,modeling=modeling,platform=data_platform),
        model_selector=_model_choose)
    stop = threading.Event()
    worker_thread = None
    agent_thread = None
    import_thread = None
    publication_thread = None
    feature_thread = None

    from timepilot.feature_semantics import FeatureSemanticService
    from timepilot.feature_registry import FeatureRegistry
    from timepilot.feature_execution import FeatureJobEngine
    semantic_features=FeatureSemanticService(projects,native)
    feature_registry=FeatureRegistry(projects,native,semantic_features)
    feature_jobs=FeatureJobEngine(projects,native,semantic_features,feature_registry)
    from timepilot.feature_auto_analysis import FeatureAutoAnalysis
    from timepilot.feature_auto_coordinator import FeatureAutoCoordinator
    feature_auto_coordinator=FeatureAutoCoordinator(FeatureAutoAnalysis(
        projects,native,semantic_features,feature_registry,feature_jobs,models))

    def _feature_once():
        # Advance both durable queues; a busy FeatureLab queue must not
        # starve approved supervised-model tasks.
        feature_worked = feature_cycle(feature_jobs, feature_auto_coordinator)
        model_worked = model_task_runs.work_once()
        return bool(feature_worked or model_worked)

    def _publication_once():
        return publication_cycle(publication_jobs, projects, native, semantic_features,
                                 feature_jobs, feature_auto_coordinator)

    def _embedded_role_loop(role):
        from timepilot.worker_supervisor import WorkerSupervisor, WorkerLeaseBusy, WorkerLeaseLost
        runner = WorkerSupervisor(worker_registry, role, app.state.worker_actions[role],
                                  poll_seconds=(client.settings.worker_poll_seconds
                                                if role == 'forecast' else .75))
        try:
            runner.run(stop)
        except (WorkerLeaseBusy, WorkerLeaseLost):
            logging.getLogger(__name__).exception('Embedded worker role cannot continue: %s', role)
            stop.set()  # Fail closed if any role is owned by another process.

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        nonlocal worker_thread, agent_thread, import_thread, publication_thread,feature_thread
        if embedded_worker:
            occupied = [role for role, info in worker_registry.snapshot()['roles'].items()
                        if info['online']]
            if occupied:
                raise RuntimeError('后台 Worker 角色已由其他进程占用：' + ', '.join(occupied))
            worker_thread = threading.Thread(target=_embedded_role_loop, args=('forecast',),
                                             name='timepilot-local-worker', daemon=True)
            agent_thread = threading.Thread(target=_embedded_role_loop, args=('agent',),
                                            name='timepilot-agent-executor', daemon=True)
            import_thread = threading.Thread(target=_embedded_role_loop, args=('upload',),
                                             name='timepilot-project-upload', daemon=True)
            publication_thread = threading.Thread(target=_embedded_role_loop, args=('publication',),
                                                  name='timepilot-project-publication', daemon=True)
            feature_thread = threading.Thread(target=_embedded_role_loop, args=('feature',),
                                              name='timepilot-featurelab-worker', daemon=True)
            for thread in (worker_thread, agent_thread, import_thread, publication_thread, feature_thread):
                thread.start()
        yield
        stop.set()
        if worker_thread:
            worker_thread.join(timeout=5)
        if agent_thread:
            agent_thread.join(timeout=5)
        if import_thread:
            import_thread.join(timeout=5)
        if publication_thread:
            publication_thread.join(timeout=5)
        if feature_thread:
            feature_thread.join(timeout=5)

    app = FastAPI(title="TimePilot API", version=__version__, lifespan=lifespan,
                  description="Local-first and opt-in tenant-scoped enterprise services.")
    app.state.security = security
    app.state.client = client
    app.state.bi = bi
    app.state.native_bi = native
    app.state.smart_import = smart_import
    app.state.workbench = workbench
    app.state.understanding = understanding
    app.state.data_assets = data_assets
    app.state.agent_executor = agent_executor
    app.state.models = models
    app.state.semantic_benchmark = semantic_benchmark
    app.state.auto_experiments = auto_experiments
    app.state.mechanism_study = mechanism_study
    app.state.projects = projects
    app.state.project_import_jobs = import_jobs
    app.state.project_publication_jobs = publication_jobs
    app.state.generic_assets = generic_assets
    app.state.modeling = modeling
    app.state.model_tasks = model_tasks
    app.state.model_task_runs = model_task_runs
    app.state.unified_model_runs = unified_model_runs
    app.state.temporal_model_bridge = temporal_model_bridge
    app.state.bi_agent = bi_agent
    app.state.derived = derived
    app.state.feature_semantic = semantic_features
    app.state.feature_registry = feature_registry
    app.state.feature_jobs = feature_jobs
    app.state.feature_auto_coordinator = feature_auto_coordinator
    # The exact same business actions run embedded or in five optional external
    # OS processes; no queue semantics are silently changed by deployment mode.
    app.state.worker_actions = {
        'forecast': client.engine.work_once,
        'agent': agent_executor.run_once,
        'upload': import_jobs.work_once,
        'publication': _publication_once,
        'feature': _feature_once,
    }
    from timepilot.worker_supervisor import WorkerRegistry, ROLES, queue_summary
    worker_registry = WorkerRegistry(settings.home)
    app.state.worker_registry = worker_registry
    from timepilot.system_logs_api import create_logs_router
    app.include_router(create_logs_router(settings.home))
    app.include_router(create_projects_router(projects, understanding, native, import_jobs, publication_jobs))
    app.include_router(create_modeling_router(modeling))
    from timepilot.model_task_api import create_model_task_router
    app.include_router(create_model_task_router(model_tasks, model_task_runs, temporal_model_bridge, unified_model_runs))
    app.include_router(create_bi_agent_router(bi_agent))
    app.include_router(create_derived_router(derived,modeling,data_platform))
    from timepilot.feature_lab_api import create_feature_lab_router
    app.include_router(create_feature_lab_router(projects,native,semantic=semantic_features,
        registry=feature_registry,jobs=feature_jobs,models=models))
    app.include_router(create_bi_router(bi, models, client))
    app.include_router(create_native_bi_router(native, bi, client.settings, models, data_assets=data_assets))
    app.include_router(create_smart_import_router(smart_import, native))
    app.include_router(create_data_assets_router(data_assets, native))
    app.include_router(create_smart_workbench_router(workbench))
    app.include_router(create_data_understanding_router(understanding))
    app.include_router(create_agent_router(agent_executor))
    app.include_router(create_research_router(semantic_benchmark, auto_experiments, mechanism_study))
    if security is not None:
        app.include_router(identity_router(security,secure_cookies=True))
    app.add_middleware(CORSMiddleware,
                       allow_origins=[] if security is not None else [os.getenv("TIMEPILOT_ALLOWED_ORIGIN", "http://localhost:5173")],
                       allow_methods=["GET", "POST"], allow_headers=["Content-Type"] )

    @app.middleware('http')
    async def diagnostic_request_log(request, call_next):
        # Do not store query strings, bodies, cookies, auth headers or source row data.
        begin = time.perf_counter()
        rid = uuid.uuid4().hex[:12]
        from timepilot.system_logs import REQUEST_ID
        token = REQUEST_ID.set(rid)
        path = request.url.path
        try:
            response = await call_next(request)
            elapsed=(time.perf_counter()-begin)*1000
            # Drop successful high-frequency polling; retain slow and failed requests.
            if response.status_code>=400 or elapsed>=1000 or not (path in ('/api/v1/runs','/api/v1/system/logs')):
                logging.getLogger('timepilot.http').log(
                    logging.WARNING if response.status_code>=400 else logging.INFO,
                    '%s %s status=%s duration_ms=%.1f', request.method, path,
                    response.status_code, elapsed,extra={'request_id': rid})
            response.headers['X-Request-ID'] = rid
            return response
        except Exception:
            logging.getLogger('timepilot.http').exception(
                '%s %s failed duration_ms=%.1f', request.method, path,
                (time.perf_counter()-begin)*1000, extra={'request_id': rid})
            raise
        finally:
            REQUEST_ID.reset(token)

    @app.get('/api/v1/system/workers')
    def worker_health():
        """Admin-only in enterprise mode; no job payloads or user identities."""
        report = worker_registry.snapshot()
        if embedded_worker:
            threads = (worker_thread, agent_thread, import_thread,
                       publication_thread, feature_thread)
            report['embedded'] = {role: bool(t and t.is_alive())
                                  for role, t in zip(ROLES, threads)}
        report['deployment'] = 'embedded' if embedded_worker else 'external'
        report['queues'] = queue_summary(projects)
        return report

    @app.get("/api/v1/health")
    def health():
        return {"status": "ok", "version": __version__, "ui_version": __version__, "mode": "enterprise" if security else "local", "embedded_worker": embedded_worker}

    @app.get('/api/v1/ready')
    def ready():
        """Readiness probes binaries, metadata storage and embedded worker threads.

        /health remains a cheap liveness signal. This endpoint cannot replace
        an actual Chrome/real MES integration acceptance receipt.
        """
        bundle = Path(__file__).resolve().parent / 'static' / 'native-bi'
        frontend = Path(__file__).resolve().parents[2] / 'frontend' / 'native-bi'
        status = bundle_status(bundle)
        source_current = frontend_matches_bundle(bundle, frontend)
        capability = native.capabilities()
        components = {'native_bundle': bool(status['ready'] and source_current),
                      'duckdb': bool(capability.get('duckdb')),
                      'virtual_server': bool(capability.get('perspective_virtual_server_ready')),
                      'metadata_db': False,'project_catalog': False,
                      'data_storage': False,'embedded_workers': None}
        problems=list(status['problems'])
        if not source_current: problems.append('Perspective 构建与当前源码不匹配')
        if not components['duckdb']: problems.append('DuckDB 运行依赖未就绪')
        if not components['virtual_server']:
            problems.append(capability.get('perspective_virtual_server_error') or 'Perspective Virtual Server 未就绪')
        try:
            from sqlalchemy import text as sql_text
            with client.store.sessions() as session:
                session.execute(sql_text('SELECT 1')).scalar_one()
            components['metadata_db']=True
        except Exception as exc:
            problems.append('元数据数据库无法查询: '+type(exc).__name__)
        try:
            with projects._db() as db:
                db.execute('SELECT 1').fetchone()
            components['project_catalog']=True
        except Exception as exc:
            problems.append('项目目录数据库无法查询: '+type(exc).__name__)
        try:
            import shutil
            disk=shutil.disk_usage(settings.home)
            components['data_storage']=settings.home.is_dir() and disk.free>=settings.min_free_gb*(1024**3)
            if not components['data_storage']:
                problems.append('运行数据目录不可用或剩余磁盘空间不足')
        except OSError as exc:
            problems.append('运行数据目录无法检查: '+type(exc).__name__)
        if embedded_worker:
            workers=[worker_thread,agent_thread,import_thread,publication_thread,feature_thread]
            components['embedded_workers']=bool(all(t is not None and t.is_alive() for t in workers))
            if not components['embedded_workers']:problems.append('内置后台 Worker 尚未启动或已经退出')
        # Heartbeats are cross-process and fenced; API-only readiness demands all
        # five dedicated worker roles. A missing role must never be treated as OK.
        external_report = None
        if not embedded_worker:
            external_report = worker_registry.snapshot()
            components['embedded_workers'] = False
            components['external_workers'] = all(
                info['online'] and info['status'] in ('idle', 'busy')
                for info in external_report['roles'].values())
            if not components['external_workers']:
                missing = [role for role, info in external_report['roles'].items()
                           if not info['online'] or info['status'] == 'memory_paused']
                problems.append('独立后台 Worker 缺失或心跳过期: ' + ', '.join(missing))
        success=all(value for key, value in components.items()
                    if value is not None and (embedded_worker or key != 'embedded_workers'))
        payload={'ready':success,'version':__version__,'components':components,
                 'perspective_bundle':status['version'], 'source_current':source_current,
                 'external_worker_verified':(components['external_workers'])
                     if external_report is not None else None}
        if not success:
            payload['problems']=problems
            raise HTTPException(status_code=503, detail=payload)
        return payload

    @app.get("/api/v1/templates/standard-timeseries.csv")
    def standard_template():
        """Download only structural sample data; never expose real user data."""
        sample = "series_id,timestamp,target,available_at\n" \
            "sample_001,2025-01-01,120,2025-02-05T08:00:00+08:00\n" \
            "sample_001,2025-02-01,135,2025-03-05T08:00:00+08:00\n"
        return Response(content="\ufeff" + sample, media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="timepilot_standard_template.csv"',
                                 "Cache-Control": "no-store"})

    @app.middleware("http")
    async def local_network_guard(request, call_next):
        # Local research prototype: do not allow direct LAN/public exposure of
        # data, model keys or edit APIs before authentication/RBAC is implemented.
        from fastapi.responses import JSONResponse
        remote = request.client.host if request.client else ''
        if client.settings.local_mode and remote not in {'127.0.0.1', '::1', 'localhost', 'testclient'}:
            return JSONResponse({'detail': '此版本仅支持本机访问；企业部署需要身份认证与权限管理'}, status_code=403)
        origin = request.headers.get('origin')
        if request.method in {'POST','PUT','PATCH','DELETE'} and origin:
            from urllib.parse import urlsplit
            parsed = urlsplit(origin)
            if parsed.netloc != request.headers.get('host') or parsed.scheme not in {'http','https'}:
                return JSONResponse({'detail': '跨站请求被阻止'}, status_code=403)
        return await call_next(request)

    @app.middleware("http")
    async def disable_stale_ui_assets(request, call_next):
        """A prior user saw old/mocked UI after updating backend; avoid stale frontend assets."""
        response = await call_next(request)
        if request.url.path.startswith("/projects") or request.url.path in {"/", "/overview", "/new", "/datasets", "/imports", "/runs", "/agent", "/plugins", "/help", "/bi", "/settings"} or request.url.path.startswith(("/assets/", "/imports/", "/agent/", "/bi/", "/runs/", "/settings/")):
            response.headers["Cache-Control"] = "no-store, max-age=0"
            response.headers["X-TimePilot-UI-Version"] = __version__
        return response

    @app.get("/api/v1/plugins")
    def plugins():
        return client.plugins.catalog()

    @app.get("/api/v1/datasets")
    def datasets():
        return client.datasets.list()

    @app.post("/api/v1/datasets/upload", status_code=201)
    async def upload(file: UploadFile = File(...), name: str = Form(...), frequency: str = Form("MS"),
                     timestamp_col: str = Form("timestamp"), target_col: str = Form("target"),
                     entity_col: str = Form("series_id"), available_at_col: str = Form("available_at"),
                     publication_lag_days: int | None = Form(None), unit: str = Form("unit"), source_timezone: str = Form("UTC")):
        content = await file.read(client.settings.max_upload_bytes + 1)
        if len(content) > client.settings.max_upload_bytes:
            raise HTTPException(status_code=413, detail="File exceeds configured upload limit")
        try:
            manifest = create_dataset(client.store, client.settings, content, file.filename or "upload.csv",
                                      name=name, frequency=frequency, timestamp_col=timestamp_col,
                                      target_col=target_col, entity_col=entity_col or None,
                                      available_at_col=available_at_col or None,
                                      publication_lag_days=publication_lag_days, unit=unit, source_timezone=source_timezone)
            return manifest.model_dump(mode="json")
        except DataValidationError as exc:
            raise HTTPException(status_code=422, detail=[x.model_dump() for x in exc.issues]) from exc
        except (ValueError, ImportError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/v1/datasets/{dataset_id}/preview")
    def preview(dataset_id: str):
        try:
            manifest, frame = get_dataset(client.store, client.settings, dataset_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except DataValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        head = frame.head(20).copy()
        for key in ["timestamp", "available_at"]:
            head[key] = head[key].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        return {"manifest": manifest.model_dump(mode="json"), "rows": head.to_dict(orient="records")}

    @app.post("/api/v1/tasks/validate")
    def validate_task(payload: RunCreate):
        try:
            manifest, data = get_dataset(client.store, client.settings, payload.dataset)
            result = client.engine._validate(payload.task, data, manifest)
            return {"valid": True, "result": result}
        except (KeyError, ValueError, DataValidationError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/v1/runs", status_code=201)
    def create_run(payload: RunCreate):
        try:
            return {"run_id": client.engine.queue(payload.task, payload.dataset), "status": "queued"}
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    def _run(run_id: str):
        row = client.store.run(run_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Run does not exist")
        return row

    @app.get("/api/v1/runs")
    def list_runs():
        return [{"run_id": r.id, "task_id": r.task_id, "dataset_id": r.dataset_id, "status": r.status,
                 "stage": r.stage, "progress": r.progress, "created_at": r.created_at} for r in client.store.list_runs()]

    @app.get("/api/v1/runs/{run_id}")
    def run_status(run_id: str):
        r = _run(run_id)
        return {"run_id": r.id, "task_id": r.task_id, "dataset_id": r.dataset_id, "status": r.status,
                "stage": r.stage, "progress": r.progress, "attempts": r.attempts,
                "error_code": r.error_code, "error_message": r.error_message,
                "created_at": r.created_at, "started_at": r.started_at, "finished_at": r.finished_at}

    @app.get("/api/v1/runs/{run_id}/events")
    def run_events(run_id: str):
        _run(run_id)
        return client.engine.events(run_id)

    @app.get("/api/v1/runs/{run_id}/quality")
    def run_quality(run_id: str):
        _run(run_id)
        try:
            return client.engine.report(run_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/v1/runs/{run_id}/forecasts")
    def run_forecasts(run_id: str):
        _run(run_id)
        try:
            return client.engine.forecast(run_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/v1/runs/{run_id}/predict/{series_id}")
    def predict_saved(run_id: str, series_id: str, horizon: int | None = None):
        _run(run_id)
        try:
            return client.engine.predict_saved(run_id, series_id, horizon=horizon)
        except (KeyError, ValueError, FileNotFoundError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/v1/runs/{run_id}/forecasts.csv")
    def download_forecasts(run_id: str):
        _run(run_id)
        try:
            rows = client.engine.forecast(run_id)["future"]
        except FileNotFoundError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=list(rows[0]) if rows else [])
        if rows:
            writer.writeheader()
            writer.writerows(rows)
        return Response(content=output.getvalue(), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="timepilot-{run_id}.csv"'})

    @app.post("/api/v1/runs/{run_id}/cancel")
    def cancel(run_id: str):
        _run(run_id)
        return {"status": client.engine.cancel(run_id)}

    @app.post("/api/v1/runs/{run_id}/retry")
    def retry(run_id: str):
        _run(run_id)
        try:
            return {"run_id": client.engine.retry(run_id), "status": "queued"}
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/v1/demo/bootstrap", status_code=201)
    def demo_bootstrap():
        return bootstrap(client, run=True)

    static = Path(__file__).resolve().parent / "static"
    app.mount("/assets", StaticFiles(directory=static), name="assets")

    @app.get("/favicon.ico", include_in_schema=False)
    def empty_favicon():
        # Modern browsers request this even when no icon is configured.
        # Returning 204 avoids a misleading 404 in BI browser diagnostics.
        return Response(status_code=204)

    @app.get('/login',include_in_schema=False)
    def login_page():
        if security is None:
            return Response(status_code=303,headers={'Location':'/projects'})
        return FileResponse(static/'login.html',headers={'Cache-Control':'no-store'})

    @app.get('/identity',include_in_schema=False)
    def identity_admin_page():
        if security is None:
            raise HTTPException(status_code=404,detail='企业用户管理仅在企业模式提供')
        return FileResponse(static/'identity.html',headers={'Cache-Control':'no-store'})

    @app.get("/", include_in_schema=False)
    def index():
        # Keep a 200 HTML response for browser and existing HTTP diagnostics;
        # the client router opens the project list on first entry.
        return FileResponse(static / "index.html")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa_routes(full_path: str):
        """Only registered UI routes fall back to index; API typos still return 404."""
        parts = full_path.strip("/").split("/")
        static_pages = {"projects", "overview", "new", "datasets", "imports", "runs", "plugins", "help", "bi", "settings", "data-assets", "agent"}
        if full_path not in static_pages and not (
            (len(parts)>=2 and parts[0]=="projects" and len(parts)<=6)
            or
            (len(parts)==2 and parts[0]=='agent' and parts[1])
            or (len(parts) == 2 and parts[0] == "runs" and parts[1])
            or (len(parts) == 2 and parts[0] == "imports" and parts[1])
            or (len(parts) in {4,5} and parts[0] == "data-assets" and parts[2] == "versions" and (len(parts)==4 or parts[4]=="native"))
            or (len(parts)==5 and parts[0]=="imports" and parts[2]=="versions" and parts[4]=="workbench")
            or (len(parts) == 2 and parts[0] == "bi" and parts[1] == "reports")
            or (len(parts) == 3 and parts[0] == "bi" and parts[1] in {"table", "report"} and parts[2])
            or (len(parts) == 4 and parts[0] == "bi" and parts[1] == "table" and parts[2] and parts[3] in {"grid", "fields", "prepare", "pivot", "reports", "ai", "forecast"})
            or len(parts) == 2 and parts[0] == "settings" and parts[1] in {"models", "general", "runtime"}
            or len(parts) in {2, 3} and parts[0] == "bi" and parts[1] == "native"
            or len(parts) == 5 and parts[0] == "bi" and parts[1] == "native" and parts[3] == "view"
            or len(parts) == 4 and parts[:3] == ["bi", "native", "stream"] and parts[3]
        ):
            raise HTTPException(status_code=404, detail="页面不存在")
        return FileResponse(static / "index.html")

    if security is not None:
        # The outer ASGI guard covers every HTTP and WS entry point, including
        # legacy routes that predate tenant-aware catalogues. Default deny.
        app.add_middleware(EnterpriseGuard,security=security,store=client.store)
    return app
