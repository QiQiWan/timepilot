"""FastAPI: local research and opt-in, tenant-scoped enterprise workbench."""
from __future__ import annotations

import csv
import io
import json
import os
import threading
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
    # Direct `python -m timepilot serve` must check a catalog before the
    # separate SQLAlchemy metadata DB or business services are initialized.
    from timepilot.config import Settings
    from timepilot.project_migrations import require_supported_catalog
    settings = Settings.from_env(home)
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

    def _model_choose(request: dict, allowed: list[str], evidence: dict) -> str:
        # Only goal, allowlisted tool names and aggregate quality counters leave the machine.
        # No source row, file name, API key, free-form SQL or dataset is transmitted.
        payload = {'goal': request['goal'], 'allowed_tools': allowed, 'evidence': evidence}
        answer = models._message([
            {'role':'system','content':'只选以下给定工具之一。仅返回 JSON {"tool":"工具名"}；不能提出额外参数、命令或权限。'},
            {'role':'user','content':json.dumps(payload,ensure_ascii=False)}], max_tokens=80)
        result = models._parse_json(answer['content'])
        return result.get('tool', '')

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

    def _worker_loop():
        # SQLite mode must run a single worker; process-level multi-host exclusion is not provided in v0.1.
        while not stop.is_set():
            try:
                result = client.engine.work_once()
            except Exception:
                import logging
                logging.getLogger(__name__).exception("Worker loop failed")
                result = None
            if not result:
                stop.wait(client.settings.worker_poll_seconds)

    def _upload_loop():
        import logging
        while not stop.is_set():
            try:
                result = import_jobs.work_once()
            except Exception:
                logging.getLogger(__name__).exception('Project upload worker failed')
                result = None
            if not result:
                stop.wait(0.5)

    from timepilot.feature_semantics import FeatureSemanticService
    from timepilot.feature_registry import FeatureRegistry
    from timepilot.feature_execution import FeatureJobEngine
    semantic_features=FeatureSemanticService(projects,native)
    feature_registry=FeatureRegistry(projects,native,semantic_features)
    feature_jobs=FeatureJobEngine(projects,native,semantic_features,feature_registry)

    def _feature_loop():
        import logging
        while not stop.is_set():
            try:worked=feature_jobs.work_once()
            except Exception:
                logging.getLogger(__name__).exception('FeatureLab background job failed')
                worked=False
            if not worked:stop.wait(1.0)

    def _publication_loop():
        import logging
        while not stop.is_set():
            try:result=publication_jobs.work_once()
            except Exception:
                logging.getLogger(__name__).exception('Project publication worker failed')
                result=None
            if not result:stop.wait(0.5)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        nonlocal worker_thread, agent_thread, import_thread, publication_thread,feature_thread
        if embedded_worker:
            worker_thread = threading.Thread(target=_worker_loop, name="timepilot-local-worker", daemon=True)
            worker_thread.start()
        if embedded_worker:
            agent_thread = threading.Thread(target=agent_executor.work_loop,args=(stop,),
                                            name='timepilot-agent-executor',daemon=True)
            agent_thread.start()
        if embedded_worker:
            import_thread = threading.Thread(target=_upload_loop,name='timepilot-project-upload',daemon=True)
            import_thread.start()
            publication_thread=threading.Thread(target=_publication_loop,name='timepilot-project-publication',daemon=True)
            publication_thread.start()
            feature_thread=threading.Thread(target=_feature_loop,name='timepilot-featurelab-worker',daemon=True)
            feature_thread.start()
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
    app.state.bi_agent = bi_agent
    app.state.derived = derived
    app.state.feature_semantic = semantic_features
    app.state.feature_registry = feature_registry
    app.state.feature_jobs = feature_jobs
    app.include_router(create_projects_router(projects, understanding, native, import_jobs, publication_jobs))
    app.include_router(create_modeling_router(modeling))
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

    @app.get("/api/v1/health")
    def health():
        return {"status": "ok", "version": __version__, "ui_version": __version__, "mode": "enterprise" if security else "local", "embedded_worker": embedded_worker}

    @app.get('/api/v1/ready')
    def ready():
        # A 200 response must mean the actual Perspective release assets are
        # present and tied to the current frontend sources, not a basic fallback.
        bundle = Path(__file__).resolve().parent / 'static' / 'native-bi'
        frontend = Path(__file__).resolve().parents[2] / 'frontend' / 'native-bi'
        status = bundle_status(bundle)
        source_current = frontend_matches_bundle(bundle, frontend)
        capability = native.capabilities()
        if not (status['ready'] and source_current and
                capability.get('duckdb') and capability.get('perspective_virtual_server_ready')):
            missing=[]
            if not capability.get('duckdb'): missing.append('DuckDB 运行依赖未就绪')
            if not capability.get('perspective_virtual_server_ready'):
                missing.append(capability.get('perspective_virtual_server_error') or 'Perspective Virtual Server 未就绪')
            raise HTTPException(status_code=503, detail={
                'ready': False, 'component': 'perspective_bundle',
                'problems': status['problems'] + ([] if source_current else ['构建来源版本与当前源码不一致']) + missing,
                'repair': '检查现有 Python 环境的 DuckDB/Perspective，并使用 bash TIMEPILOT.sh build 校验已编译 JS/WASM'})
        return {'ready':True,'version':__version__,'perspective_bundle':status['version'],
                'source_current':True}

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
