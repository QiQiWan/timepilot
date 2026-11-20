"""Role-scoped background runtime, without FastAPI or HTTP router initialization.

The same durable queue classes are used by embedded and external workers.
Only a single-host SQLite deployment is supported. Role leases in the
supervisor do not replace business-job ownership/fencing at commit time.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from timepilot.worker_supervisor import ROLES, WorkerRegistry

LOG = logging.getLogger(__name__)


def feature_cycle(jobs, coordinator) -> bool:
    """Advance two durable feature queues, even when the first one errors."""
    try:
        worked = jobs.work_once()
    except Exception:
        LOG.exception('FeatureLab background job failed')
        worked = False
    try:
        continued = coordinator.work_once()
    except Exception:
        LOG.exception('FeatureLab auto coordinator failed')
        continued = False
    return bool(worked or continued)


def publication_cycle(jobs, projects, native, semantic, feature_jobs, coordinator):
    """Preserve the post-publication analysis enqueue from the API implementation."""
    result = jobs.work_once()
    if result and result.get('status') == 'succeeded':
        published = result.get('result') or {}
        aid = published.get('native_asset_id')
        if aid and result.get('owner_id'):
            from timepilot.feature_data_analysis import FeatureDataAnalysis
            from timepilot.feature_analysis_journey import FeatureAnalysisJourney
            journey = FeatureAnalysisJourney(
                FeatureDataAnalysis(projects, semantic, feature_jobs, native),
                coordinator.analysis,
            )
            try:
                with coordinator._owner_context(result['project_id'], result['owner_id']):
                    journey.start(result['project_id'], aid, 'base')
            except Exception:
                LOG.exception('Published source preserved; auto-analysis enqueue failed')
    return result


def choose_agent_tool(models, request: dict, allowed: list[str], evidence: dict) -> str:
    """Expose only the same minimal, aggregate model prompt used by the API."""
    payload = {'goal': request['goal'], 'allowed_tools': allowed, 'evidence': evidence}
    answer = models._message([
        {'role': 'system', 'content': '只选以下给定工具之一。仅返回 JSON {"tool":"工具名"}；不能提出额外参数、命令或权限。'},
        {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)},
    ], max_tokens=80)
    return models._parse_json(answer['content']).get('tool', '')


@dataclass
class WorkerRuntime:
    role: str
    registry: WorkerRegistry
    action: Callable[[], Any]
    services: dict[str, Any] = field(default_factory=dict)
    store: Any = None

    def close(self) -> None:
        if self.store is not None:
            self.store.engine.dispose()


def build_worker_runtime(role: str, *, home: str | Path | None = None) -> WorkerRuntime:
    """Construct only the dependencies necessary to execute ``role``.

    Serialize first-run schema and migration DDL with the normal API startup.
    Do not import/create FastAPI, WebSocket handlers, BI routers or user pages.
    """
    if role not in ROLES:
        raise ValueError('Unknown worker role: ' + str(role))

    from timepilot.config import Settings
    from timepilot.startup_guard import startup_guard
    settings = Settings.from_env(home)
    with startup_guard(settings.home):
        return _build_unlocked(role, settings)


def _build_unlocked(role: str, settings) -> WorkerRuntime:
    from timepilot.project_migrations import require_supported_catalog
    from timepilot.system_logs import setup_logging
    setup_logging(settings.home)
    # Catalog compatibility is checked before initializing operational storage.
    require_supported_catalog(settings.home / 'projects' / 'catalog.sqlite3')
    settings.ensure_dirs()
    enterprise = os.getenv('TIMEPILOT_ENTERPRISE_MODE', '0') == '1'
    if not settings.local_mode and not enterprise:
        raise RuntimeError('非本地服务必须显式启用 TIMEPILOT_ENTERPRISE_MODE=1')
    security = None
    if enterprise:
        origin = urlsplit(os.getenv('TIMEPILOT_PUBLIC_BASE_URL', ''))
        if origin.scheme != 'https' or not origin.netloc or origin.path not in ('', '/'):
            raise RuntimeError('企业模式必须配置 HTTPS 的 TIMEPILOT_PUBLIC_BASE_URL')
        from timepilot.enterprise_security import EnterpriseSecurity
        security = EnterpriseSecurity(settings.home, require_admin=True)

    services: dict[str, Any] = {'settings': settings}
    store = None

    if role == 'forecast':
        from timepilot.sdk import TimePilot
        client = TimePilot(home=settings.home)
        store = client.store
        services['client'] = client
        action = client.engine.work_once
    else:
        from timepilot.smart_import import SmartImportService
        from timepilot.projects import ProjectCatalog
        imports = SmartImportService(settings.home)
        services['imports'] = imports
        # Upload needs only a catalog and importer; no forecasting or BI engine.
        if role == 'upload':
            projects = ProjectCatalog(settings.home, imports, None, security=security)
            from timepilot.project_import_jobs import ProjectImportJobs
            jobs = ProjectImportJobs(projects)
            services.update(projects=projects, import_jobs=jobs)
            action = jobs.work_once
        else:
            from timepilot.db import Store
            from timepilot.native_bi import NativeBIService
            client = None
            if role == 'agent':
                from timepilot.sdk import TimePilot
                client = TimePilot(home=settings.home)
                store = client.store
            else:
                store = Store(settings.db_url)
            native = NativeBIService(store, settings)
            services.update(native=native)
            if role in ('feature', 'publication'):
                # No forecast engine, modeling router, AI agent or HTTP service.
                projects = ProjectCatalog(settings.home, imports, None, security=security)
                services['projects'] = projects
                from timepilot.feature_semantics import FeatureSemanticService
                from timepilot.feature_registry import FeatureRegistry
                from timepilot.feature_execution import FeatureJobEngine
                from timepilot.feature_auto_analysis import FeatureAutoAnalysis
                from timepilot.feature_auto_coordinator import FeatureAutoCoordinator
                from timepilot.llm import ModelGateway
                models = ModelGateway(store, settings)
                semantic = FeatureSemanticService(projects, native)
                registry = FeatureRegistry(projects, native, semantic)
                jobs = FeatureJobEngine(projects, native, semantic, registry)
                coordinator = FeatureAutoCoordinator(FeatureAutoAnalysis(
                    projects, native, semantic, registry, jobs, models))
                services.update(models=models, feature_semantic=semantic,
                                feature_registry=registry, feature_jobs=jobs,
                                feature_auto_coordinator=coordinator)
                if role == 'feature':
                    from timepilot.modeling_workflow import ModelingWorkbench
                    from timepilot.model_task_store import ModelTaskStore
                    from timepilot.model_task_execution import ModelTaskRunService
                    modeling = ModelingWorkbench(projects, native)
                    model_tasks = ModelTaskStore(projects, modeling)
                    model_runs = ModelTaskRunService(model_tasks, modeling)
                    services.update(model_tasks=model_tasks, model_task_runs=model_runs)
                    def _run_feature_and_model():
                        worked = feature_cycle(jobs, coordinator)
                        trained = model_runs.work_once()
                        return bool(worked or trained)
                    action = _run_feature_and_model
                else:
                    from timepilot.project_publication import ProjectPublicationJobs
                    publications = ProjectPublicationJobs(projects, native)
                    services['publication_jobs'] = publications
                    action = lambda: publication_cycle(
                        publications, projects, native, semantic, jobs, coordinator)
            else:
                # Agent uses its complete *tool* dependency graph, without any
                # FastAPI routers, feature queues, or worker threads.
                from timepilot.sdk import TimePilot
                from timepilot.bi import BIService
                from timepilot.llm import ModelGateway
                from timepilot.smart_workbench import SmartWorkbench
                from timepilot.data_understanding import DataUnderstandingService
                from timepilot.data_assets import DataAssetCatalog
                from timepilot.semantic_benchmark import SemanticBenchmark
                from timepilot.mechanisms.identification import MechanismStudy
                from timepilot.auto_experiment import AutonomousExperiment
                from timepilot.generic_assets import GenericAssetStore
                from timepilot.data_platform import DataPlatform
                from timepilot.modeling_workflow import ModelingWorkbench
                from timepilot.bi_agent_service import BIWorkspaceService
                from timepilot.derived_assets import DerivedAssetService
                from timepilot.agent_tools import default_registry, ToolServices
                from timepilot.agent_executor import DurableExecutor
                # Agent reuses its SDK storage instead of opening a second pool.
                bi = BIService(store, settings)
                models = ModelGateway(store, settings)
                workbench = SmartWorkbench(imports, store, settings, client.engine)
                understanding = DataUnderstandingService(imports, models)
                assets = DataAssetCatalog(imports, workbench, legacy_bi=bi)
                benchmark = SemanticBenchmark(settings.home, imports, models)
                mechanism = MechanismStudy(settings.home)
                experiments = AutonomousExperiment(settings.home, assets, client.plugins, mechanisms=mechanism)
                projects = ProjectCatalog(settings.home, imports, assets,
                                          engine=client.engine, experiments=experiments,
                                          security=security)
                projects.backfill_legacy_once()
                projects.link_legacy_views_once()
                generic = GenericAssetStore(imports)
                platform = DataPlatform(projects, native, generic)
                modeling = ModelingWorkbench(projects, native)
                bi_agent = BIWorkspaceService(projects, native, platform, modeling=modeling, models=models)
                modeling.derived = DerivedAssetService(bi_agent)
                executor = DurableExecutor(settings.home / 'agent_workflows',
                    default_registry(), ToolServices(understanding, assets, workbench, bi, settings,
                        benchmark=benchmark, experiments=experiments,
                        mechanism=mechanism, projects=projects, generic=generic,
                        native=native, modeling=modeling, platform=platform),
                    model_selector=lambda request, allowed, evidence:
                        choose_agent_tool(models, request, allowed, evidence))
                services.update(client=client, projects=projects, agent_executor=executor)
                action = executor.run_once

    # The registry is constructed under the same startup guard; claiming the
    # role happens later in WorkerSupervisor once initialization has completed.
    registry = WorkerRegistry(settings.home)
    return WorkerRuntime(role, registry, action, services, store)
