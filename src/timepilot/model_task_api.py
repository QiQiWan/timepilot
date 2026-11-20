"""Project-scoped ModelTaskSpec REST API, separate from legacy model execution."""
from __future__ import annotations

import asyncio
from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import Field
from typing import Literal
from .model_task_spec import Strict

from .model_task_spec import ModelTaskInput, ReplaceModelTask, ApproveModelTask
from .model_task_temporal_bridge import BindTemporalDraft
from .model_task_unified_runs import StartUnifiedRun
from .model_task_store import ModelTaskConflict, ModelTaskError
from .native_bi import NativeBIDependencyError
from .projects import ProjectError


class ModelExperimentPreview(Strict):
    budget_profile: Literal['5m','15m','30m'] = '15m'

class ModelExperimentCancel(Strict):
    expected_revision: int = Field(ge=1)
    run_id: str | None = None

def create_model_task_router(service, runs=None, temporal_bridge=None, unified_runs=None):
    router = APIRouter(prefix='/api/v1/projects/{pid}/model-tasks', tags=['Project Model Tasks'])

    def fail(exc):
        if isinstance(exc, KeyError):
            raise HTTPException(404, str(exc)) from exc
        if isinstance(exc, ModelTaskConflict):
            raise HTTPException(409, str(exc)) from exc
        if isinstance(exc, NativeBIDependencyError):
            raise HTTPException(503, str(exc)) from exc
        if isinstance(exc, (ModelTaskError, ProjectError, ValueError)):
            raise HTTPException(422, str(exc)) from exc
        raise exc

    @router.get('')
    def list_tasks(pid: str, limit: int = 100):
        try:
            return {'items': service.list(pid, limit=limit)}
        except Exception as exc:
            fail(exc)

    @router.post('', status_code=201)
    async def create(pid: str, request: ModelTaskInput):
        try:
            return await asyncio.to_thread(service.create, pid, request)
        except Exception as exc:
            fail(exc)

    @router.get('/{task_id}')
    async def get(pid: str, task_id: str):
        try:
            return await asyncio.to_thread(service.get, pid, task_id)
        except Exception as exc:
            fail(exc)

    @router.patch('/{task_id}')
    async def update(pid: str, task_id: str, request: ReplaceModelTask):
        try:
            return await asyncio.to_thread(service.replace, pid, task_id, request)
        except Exception as exc:
            fail(exc)

    @router.post('/{task_id}/preflight')
    async def preflight(pid: str, task_id: str):
        try:
            return await asyncio.to_thread(service.preflight, pid, task_id)
        except Exception as exc:
            fail(exc)

    @router.post('/{task_id}/approve')
    async def approve(pid: str, task_id: str, request: ApproveModelTask):
        try:
            return await asyncio.to_thread(service.approve, pid, task_id, request)
        except Exception as exc:
            fail(exc)

    @router.get('/{task_id}/events')
    def audit(pid: str, task_id: str):
        try:
            return {'items': service.events(pid, task_id)}
        except Exception as exc:
            fail(exc)

    if temporal_bridge is not None:
        @router.post('/{task_id}/temporal-link')
        async def bind_temporal(pid: str, task_id: str, request: BindTemporalDraft):
            try:
                return await asyncio.to_thread(temporal_bridge.bind,pid,task_id,request)
            except Exception as exc:
                fail(exc)

        @router.get('/{task_id}/temporal-link')
        async def read_temporal(pid: str, task_id: str):
            try:
                return await asyncio.to_thread(temporal_bridge.get,pid,task_id)
            except Exception as exc:
                fail(exc)

        @router.get('/{task_id}/evidence')
        async def unified_evidence(pid: str, task_id: str):
            try:
                return await asyncio.to_thread(temporal_bridge.evidence,pid,task_id)
            except Exception as exc:
                fail(exc)

    if unified_runs is not None:
        @router.get('/{task_id}/execution')
        async def current_execution(pid: str, task_id: str):
            try:
                return await asyncio.to_thread(unified_runs.summary, pid, task_id)
            except Exception as exc:
                fail(exc)

        @router.post('/{task_id}/execution', status_code=202)
        async def start_execution(pid: str, task_id: str, request: StartUnifiedRun):
            try:
                return await asyncio.to_thread(unified_runs.start, pid, task_id, request)
            except Exception as exc:
                fail(exc)

        @router.post('/{task_id}/execution/plan/preview')
        async def preview_plan(pid: str, task_id: str, request: ModelExperimentPreview):
            try:
                return await asyncio.to_thread(unified_runs.preview,pid,task_id,request.budget_profile)
            except Exception as exc:
                fail(exc)

        @router.post('/{task_id}/execution/cancel')
        async def cancel_execution(pid: str, task_id: str, request: ModelExperimentCancel):
            try:
                return await asyncio.to_thread(unified_runs.cancel,pid,task_id,
                                              request.expected_revision,request.run_id)
            except Exception as exc:
                fail(exc)

        @router.get('/{task_id}/execution/steps')
        async def execution_steps(pid: str, task_id: str):
            try:
                return await asyncio.to_thread(unified_runs.steps,pid,task_id)
            except Exception as exc:
                fail(exc)

        @router.get('/{task_id}/execution/history')
        async def execution_history(pid: str, task_id: str, limit: int = 30):
            try:
                return await asyncio.to_thread(unified_runs.history, pid, task_id, limit)
            except Exception as exc:
                fail(exc)

        @router.get('/{task_id}/execution/diagnostics')
        async def execution_diagnostics(pid: str, task_id: str, model: str | None = None,
                                        dimension: str | None = None, value: str | None = None):
            try:
                return await asyncio.to_thread(unified_runs.diagnostics, pid, task_id,
                                               model=model, dimension=dimension, value=value)
            except Exception as exc:
                fail(exc)

        @router.get('/{task_id}/execution/predictions')
        async def prediction_metadata(pid: str, task_id: str):
            try:
                result=await asyncio.to_thread(unified_runs.summary,pid,task_id)
                if not result.get('report_available') or not result.get('prediction_asset'):
                    raise ModelTaskConflict('No current approved prediction asset')
                return result['prediction_asset']
            except Exception as exc:
                fail(exc)

        @router.get('/{task_id}/execution/predictions/rows')
        async def prediction_page(pid: str, task_id: str, model: str | None = None,
                                  fold: int | None = None, offset: int = 0, limit: int = 100):
            try:
                return await asyncio.to_thread(unified_runs.prediction_rows,pid,task_id,
                                               model=model,fold=fold,offset=offset,limit=limit)
            except Exception as exc:
                fail(exc)

        @router.get('/{task_id}/execution/predictions/query')
        async def prediction_query(pid: str, task_id: str, model: str | None = None,
                                   fold: int | None = None, month: str | None = None,
                                   device: str | None = None, min_abs_error: float | None = None,
                                   offset: int = 0, limit: int = 100):
            try:
                return await asyncio.to_thread(unified_runs.prediction_query,pid,task_id,
                    model=model,fold=fold,month=month,device=device,
                    min_abs_error=min_abs_error,offset=offset,limit=limit)
            except Exception as exc:
                fail(exc)

        @router.get('/{task_id}/execution/predictions/lineage')
        async def prediction_lineage(pid: str, task_id: str, row_id: int, source_offset: int = 0, source_limit: int = 20):
            try:
                return await asyncio.to_thread(unified_runs.prediction_lineage,pid,task_id,row_id=row_id,
                                               source_offset=source_offset,source_limit=source_limit)
            except Exception as exc:
                fail(exc)

        @router.get('/{task_id}/execution/predictions/window-lineage')
        async def prediction_window_lineage(pid: str, task_id: str, row_id: int,
                                            offset: int = 0, limit: int = 100):
            try:
                return await asyncio.to_thread(unified_runs.prediction_window_lineage,
                    pid,task_id,row_id=row_id,offset=offset,limit=limit)
            except Exception as exc:
                fail(exc)

        @router.get('/{task_id}/execution/report', response_class=PlainTextResponse)
        async def execution_report(pid: str, task_id: str):
            try:
                document = await asyncio.to_thread(unified_runs.report, pid, task_id)
                return PlainTextResponse(document, media_type='text/markdown; charset=utf-8',
                                         headers={'Content-Disposition': 'attachment; filename="model-evaluation.md"'})
            except Exception as exc:
                fail(exc)

    if runs is not None:
        @router.post('/{task_id}/runs',status_code=202)
        async def submit_run(pid: str, task_id: str):
            try:
                return await asyncio.to_thread(runs.submit, pid, task_id)
            except Exception as exc:
                fail(exc)

        @router.get('/{task_id}/runs')
        async def list_runs(pid: str, task_id: str):
            try:
                return {'items':await asyncio.to_thread(runs.list,pid,task_id)}
            except Exception as exc:
                fail(exc)

        @router.get('/{task_id}/runs/{run_id}')
        async def get_run(pid: str, task_id: str, run_id: str):
            try:
                result=await asyncio.to_thread(runs.get,pid,run_id)
                if result['task_id']!=service._task_id(task_id):
                    raise KeyError('Run does not belong to the specified task')
                return result
            except Exception as exc:
                fail(exc)

    return router
