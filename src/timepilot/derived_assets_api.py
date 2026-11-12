"""Project-scoped, explicit review gates for immutable BI derived data."""
from __future__ import annotations
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from timepilot.derived_assets import DerivedAssetError
from timepilot.native_bi import NativeBIDependencyError
from timepilot.enterprise_security import SecurityError
from timepilot.modeling_workflow import CreateModelingDraft, ModelingError

class Strict(BaseModel):
    model_config=ConfigDict(extra='forbid')

class RequestDerived(Strict):
    workspace_id: str
    name: str=Field(min_length=2,max_length=120)
    request_id: str=Field(min_length=8,max_length=100)

class ApproveDerived(Strict):
    confirmed: bool=False
    reason: str=Field(min_length=4,max_length=500)

class DerivedModelDraft(Strict):
    name: str=Field(min_length=2,max_length=120)
    confirm_scope: bool=False


def create_derived_router(service,modeling,platform):
    router=APIRouter(prefix='/api/v1/projects/{pid}/derived',tags=['Reviewed Derived Data'])
    def fail(exc):
        if isinstance(exc,SecurityError):raise HTTPException(exc.code,str(exc)) from exc
        if isinstance(exc,KeyError):raise HTTPException(404,str(exc)) from exc
        if isinstance(exc,NativeBIDependencyError):raise HTTPException(503,str(exc)) from exc
        if isinstance(exc,(DerivedAssetError,ModelingError,ValueError)):
            raise HTTPException(422,str(exc)) from exc
        raise exc

    @router.get('')
    def list_derived(pid:str):
        try:return service.list(pid)
        except Exception as exc:fail(exc)

    @router.post('',status_code=201)
    def create(pid:str,payload:RequestDerived):
        try:return service.request(pid,payload.workspace_id,payload.name,payload.request_id)
        except Exception as exc:fail(exc)

    @router.get('/{rid}')
    def get(pid:str,rid:str):
        try:return service._record(pid,rid)
        except Exception as exc:fail(exc)

    @router.get('/{rid}/audit')
    def history(pid:str,rid:str):
        try:return service.audit(pid,rid)
        except Exception as exc:fail(exc)

    @router.post('/{rid}/approve')
    def approve(pid:str,rid:str,payload:ApproveDerived):
        try:return service.approve(pid,rid,payload.confirmed,payload.reason)
        except Exception as exc:fail(exc)

    @router.post('/{rid}/publish')
    def publish(pid:str,rid:str):
        try:return service.publish(pid,rid)
        except Exception as exc:fail(exc)

    @router.post('/{rid}/model-drafts',status_code=201)
    def model(pid:str,rid:str,payload:DerivedModelDraft):
        try:
            source=service._record(pid,rid)
            if source['status']!='published':raise DerivedAssetError('派生数据必须先审核发布才能建模')
            if not payload.confirm_scope:raise DerivedAssetError('请先确认对完整派生数据创建待审核模型草稿')
            aid=source['native_asset_id']
            # The full derived asset is frozen as an independent immutable row set.
            from timepilot.data_platform import SelectionInput
            selected=platform.create_selection(pid,SelectionInput(native_asset_id=aid,confirm=True))
            spec=CreateModelingDraft(asset_id=aid,version_id='base',selection_id=selected['selection_id'],
                                      name=payload.name,confirm_scope=True)
            draft=modeling.create_draft(pid,spec)
            return {'draft':draft,'selection':selected,'derivation_id':rid,'review_required':True}
        except Exception as exc:fail(exc)

    return router
