"""Unified read-only and reviewable DataAsset API backed by existing versions."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, WebSocket
from timepilot.data_assets import DataAssetCatalog, GridRequest, GroupRequest, PivotRequest, SemanticRevision
from timepilot.smart_import import SmartImportError
from timepilot.smart_workbench import SaveView, ViewSelection, DraftOptions
from timepilot.bi import BIError
from pydantic import BaseModel, Field, ConfigDict


class LegacyLinkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    selection: ViewSelection = Field(default_factory=ViewSelection)
    name: str = Field(default="",max_length=120)


def create_data_assets_router(catalog: DataAssetCatalog, native=None):
    router=APIRouter(prefix='/api/v1/data-assets',tags=['Unified data assets'])

    def fail(exc):
        if isinstance(exc,KeyError):
            raise HTTPException(404,detail=str(exc)) from exc
        if isinstance(exc,(SmartImportError,BIError,ValueError)):
            raise HTTPException(422,detail=str(exc)) from exc
        raise exc

    @router.get('')
    def listing():
        return catalog.list_assets()

    @router.get('/{asset_id}')
    def asset(asset_id: str):
        try:return catalog.asset(asset_id)
        except Exception as exc:fail(exc)

    @router.get('/{asset_id}/versions/{version_id}')
    def version(asset_id: str,version_id: str):
        try:return catalog.version(asset_id,version_id)
        except Exception as exc:fail(exc)

    @router.get('/{asset_id}/versions/{version_id}/semantics')
    def semantics(asset_id: str,version_id: str):
        try:return catalog.semantics(asset_id,version_id)
        except Exception as exc:fail(exc)

    @router.post('/{asset_id}/versions/{version_id}/semantics',status_code=201)
    def revise(asset_id: str,version_id: str,spec: SemanticRevision):
        try:return catalog.revise_semantics(asset_id,version_id,spec)
        except Exception as exc:fail(exc)

    @router.post('/{asset_id}/versions/{version_id}/analyze')
    async def analyze(asset_id: str,version_id: str,selection: ViewSelection):
        try:return await asyncio.to_thread(catalog.analyze,asset_id,version_id,selection)
        except Exception as exc:fail(exc)

    @router.post('/{asset_id}/versions/{version_id}/grid')
    async def grid(asset_id: str,version_id: str,request: GridRequest):
        try:return await asyncio.to_thread(catalog.grid,asset_id,version_id,request)
        except Exception as exc:fail(exc)

    @router.post('/{asset_id}/versions/{version_id}/group')
    async def group(asset_id: str,version_id: str,request: GroupRequest):
        try:return await asyncio.to_thread(catalog.group,asset_id,version_id,request)
        except Exception as exc:fail(exc)

    @router.post('/{asset_id}/versions/{version_id}/legacy-bi-link',status_code=201)
    async def legacy_link(asset_id: str,version_id: str,request: LegacyLinkRequest):
        try:
            return await asyncio.to_thread(catalog.link_legacy_bi,asset_id,version_id,
                                           request.selection,request.name)
        except Exception as exc:fail(exc)

    @router.post('/{asset_id}/versions/{version_id}/pivot')
    async def pivot(asset_id: str,version_id: str,request: PivotRequest):
        try:return await asyncio.to_thread(catalog.pivot,asset_id,version_id,request)
        except Exception as exc:fail(exc)

    @router.get('/{asset_id}/versions/{version_id}/views')
    def views(asset_id: str,version_id: str):
        try:return catalog.views(asset_id,version_id)
        except Exception as exc:fail(exc)

    @router.post('/{asset_id}/versions/{version_id}/views',status_code=201)
    async def save(asset_id: str,version_id: str,payload: SaveView):
        try:return await asyncio.to_thread(catalog.save_view,asset_id,version_id,payload)
        except Exception as exc:fail(exc)

    @router.get('/{asset_id}/versions/{version_id}/views/{view_id}')
    def view(asset_id: str,version_id: str,view_id: str):
        try:return catalog.view(asset_id,version_id,view_id)
        except Exception as exc:fail(exc)

    @router.post('/{asset_id}/versions/{version_id}/views/{view_id}/forecast-drafts',status_code=201)
    async def draft(asset_id: str,version_id: str,view_id: str,options: DraftOptions):
        try:
            import_id,_,_=catalog._resolve(asset_id,version_id)
            return await asyncio.to_thread(catalog.workbench.create_draft,
                                          import_id,version_id,view_id,options)
        except Exception as exc:fail(exc)

    return router
