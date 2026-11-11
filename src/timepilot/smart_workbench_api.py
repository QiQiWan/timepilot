"""v0.7.0 BI/forecast bridge; no tasks are queued without user confirmation."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException
from timepilot.smart_import import SmartImportError
from timepilot.smart_workbench import DraftOptions, SaveView, SmartWorkbench, ViewSelection


def create_smart_workbench_router(workbench: SmartWorkbench):
    router = APIRouter(prefix='/api/v1/smart-import', tags=['Smart BI and forecast'])
    base = '/imports/{import_id}/versions/{version_id}/workbench'

    def fail(exc: Exception):
        if isinstance(exc, KeyError):
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if isinstance(exc, (SmartImportError,ValueError)):
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        raise exc

    @router.get(base + '/index-status')
    def index_status(import_id: str, version_id: str):
        try:return workbench.index_status(import_id,version_id)
        except Exception as exc:fail(exc)

    @router.post(base + '/build-index',status_code=202)
    def build_index(import_id: str,version_id: str):
        try:return workbench.start_index(import_id,version_id)
        except Exception as exc:fail(exc)

    @router.post(base + '/analyze')
    async def analyze(import_id: str,version_id: str, selection: ViewSelection):
        try:return await asyncio.to_thread(workbench.analyze,import_id,version_id,selection)
        except Exception as exc:fail(exc)

    @router.post(base + '/facets/{field}')
    async def facet(import_id: str,version_id: str,field: str,selection: ViewSelection, q: str=''):
        try:return await asyncio.to_thread(workbench.facet_search,import_id,version_id,selection,field,q)
        except Exception as exc:fail(exc)

    @router.get(base + '/views')
    def views(import_id: str,version_id: str):
        try:return workbench.views(import_id,version_id)
        except Exception as exc:fail(exc)

    @router.post(base + '/views',status_code=201)
    async def save(import_id: str,version_id: str,payload: SaveView):
        try:return await asyncio.to_thread(workbench.save_view,import_id,version_id,payload)
        except Exception as exc:fail(exc)

    @router.get(base + '/views/{view_id}')
    def saved(import_id: str,version_id: str,view_id: str):
        try:return workbench.view(import_id,version_id,view_id)
        except Exception as exc:fail(exc)

    @router.post(base + '/views/{view_id}/forecast-drafts',status_code=201)
    async def create_draft(import_id: str,version_id: str,view_id: str,payload: DraftOptions):
        try:return await asyncio.to_thread(workbench.create_draft,import_id,version_id,view_id,payload)
        except Exception as exc:fail(exc)

    @router.get(base + '/forecast-drafts')
    def drafts(import_id: str,version_id: str):
        try:return workbench.drafts(import_id,version_id)
        except Exception as exc:fail(exc)

    @router.get(base + '/forecast-drafts/{draft_id}')
    def draft(import_id: str,version_id: str,draft_id: str):
        try:return workbench._public_draft(workbench.draft(import_id,version_id,draft_id))
        except Exception as exc:fail(exc)

    @router.post(base + '/forecast-drafts/{draft_id}/materialize',status_code=201)
    async def materialize(import_id: str,version_id: str,draft_id: str):
        try:return await asyncio.to_thread(workbench.materialize,import_id,version_id,draft_id)
        except Exception as exc:fail(exc)

    return router
