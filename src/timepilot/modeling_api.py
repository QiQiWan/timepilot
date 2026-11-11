"""Project-scoped generic BI -> reviewed forecast endpoints."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

from timepilot.modeling_workflow import (
    ModelingWorkbench, ModelingError, CreateModelingDraft,
    EditModelingDraft, ApproveModelingDraft, ClassificationScoreRequest,
)
from timepilot.data import DataValidationError
from timepilot.native_bi import NativeBIError, NativeBIDependencyError
from timepilot.projects import ProjectError


def create_modeling_router(service:ModelingWorkbench):
    router=APIRouter(prefix='/api/v1/projects/{pid}/modeling',tags=['Project Modeling'])

    def fail(exc):
        if isinstance(exc,KeyError):
            raise HTTPException(404,str(exc)) from exc
        if isinstance(exc,NativeBIDependencyError):
            raise HTTPException(503,str(exc)) from exc
        if isinstance(exc,(ValueError,DataValidationError,NativeBIError,ProjectError,ModelingError)):
            raise HTTPException(422,str(exc)) from exc
        raise exc

    @router.get('/assets/{aid}/profile')
    async def profile(pid:str,aid:str,version_id:str='base'):
        try:return await asyncio.to_thread(service.profile,pid,aid,version_id)
        except Exception as exc:fail(exc)

    @router.get('/assets/{aid}/suggestions')
    async def suggest(pid:str,aid:str,version_id:str='base'):
        try:return await asyncio.to_thread(service.suggestions,pid,aid,version_id)
        except Exception as exc:fail(exc)

    @router.get('/drafts')
    def drafts(pid:str):
        try:return service.drafts(pid)
        except Exception as exc:fail(exc)

    @router.post('/drafts',status_code=201)
    def create(pid:str,request:CreateModelingDraft):
        try:return service.create_draft(pid,request)
        except Exception as exc:fail(exc)

    @router.get('/drafts/{did}')
    def draft(pid:str,did:str):
        try:return service.draft(pid,did)
        except Exception as exc:fail(exc)

    @router.patch('/drafts/{did}')
    def edit(pid:str,did:str,request:EditModelingDraft):
        try:return service.edit_draft(pid,did,request)
        except Exception as exc:fail(exc)

    @router.post('/drafts/{did}/preflight')
    async def preflight(pid:str,did:str):
        try:return await asyncio.to_thread(service.preflight,pid,did)
        except Exception as exc:fail(exc)

    @router.post('/drafts/{did}/approve')
    async def approve(pid:str,did:str,request:ApproveModelingDraft):
        try:return await asyncio.to_thread(service.approve,pid,did,request)
        except Exception as exc:fail(exc)

    @router.post('/drafts/{did}/materialize',status_code=201)
    async def materialize(pid:str,did:str):
        try:return await asyncio.to_thread(service.materialize,pid,did)
        except Exception as exc:fail(exc)

    @router.post('/drafts/{did}/submit',status_code=201)
    async def submit(pid:str,did:str):
        try:return await asyncio.to_thread(service.submit,pid,did)
        except Exception as exc:fail(exc)

    @router.post('/drafts/{did}/reproduce',status_code=201)
    def reproduce(pid:str,did:str):
        try:return service.reproduce(pid,did)
        except Exception as exc:fail(exc)

    @router.get('/drafts/{did}/lineage')
    def lineage(pid:str,did:str):
        try:return service.lineage(pid,did)
        except Exception as exc:fail(exc)

    @router.get('/drafts/{did}/audit')
    def audit(pid:str,did:str,offset:int=0,limit:int=50):
        try:return service.audit(pid,did,offset=offset,limit=limit)
        except Exception as exc:fail(exc)

    @router.get('/drafts/{did}/evaluation')
    def evaluation(pid:str,did:str,offset:int=0,limit:int=100):
        try:return service.evaluation(pid,did,offset=offset,limit=limit)
        except Exception as exc:fail(exc)

    @router.post('/drafts/{did}/score')
    def score(pid:str,did:str,request:ClassificationScoreRequest):
        try:return service.score_classification(pid,did,request)
        except Exception as exc:fail(exc)

    return router
