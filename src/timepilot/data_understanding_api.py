"""v0.8.0 allowlisted, audited data-understanding agent endpoints."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException
from timepilot.data_understanding import (Approval, DataUnderstandingService,
                                          ReadToolRequest, UnderstandingRequest)
from timepilot.smart_import import SmartImportError


def create_data_understanding_router(service: DataUnderstandingService) -> APIRouter:
    router=APIRouter(prefix='/api/v1/smart-import',tags=['Data understanding agent'])

    def fail(exc: Exception):
        if isinstance(exc,KeyError):
            raise HTTPException(status_code=404,detail=str(exc)) from exc
        if isinstance(exc,(SmartImportError,ValueError)):
            raise HTTPException(status_code=422,detail=str(exc)) from exc
        raise exc

    @router.get('/agent/capabilities')
    def capabilities():
        return service.capabilities()

    @router.post('/imports/{import_id}/agent/tools/{tool_name}/preview')
    async def preview_tool(import_id: str,tool_name: str,request: ReadToolRequest):
        try:
            result=await asyncio.to_thread(service.run_read_tool,import_id,tool_name,request)
            return {'tool':tool_name,'executed':True,'permission':'read','result':result,
                    'data_modified':False}
        except Exception as exc:fail(exc)

    @router.get('/imports/{import_id}/agent/plans')
    def list_plans(import_id: str):
        try:return service.plans(import_id)
        except Exception as exc:fail(exc)

    @router.post('/imports/{import_id}/agent/plans',status_code=201)
    async def propose(import_id: str,request: UnderstandingRequest):
        try:return await asyncio.to_thread(service.propose,import_id,request)
        except Exception as exc:fail(exc)

    @router.get('/imports/{import_id}/agent/plans/{plan_id}')
    def plan(import_id: str,plan_id: str):
        try:return service.plan(import_id,plan_id)
        except Exception as exc:fail(exc)

    @router.post('/imports/{import_id}/agent/plans/{plan_id}/execute',status_code=202)
    async def execute(import_id: str,plan_id: str,decision: Approval):
        try:return await asyncio.to_thread(service.execute,import_id,plan_id,decision)
        except Exception as exc:fail(exc)

    @router.get('/imports/{import_id}/agent/plans/{plan_id}/execution')
    def status(import_id: str,plan_id: str):
        try:return service.execution(import_id,plan_id)
        except Exception as exc:fail(exc)

    @router.post('/imports/{import_id}/agent/plans/{plan_id}/retry',status_code=202)
    async def retry(import_id: str,plan_id: str,decision: Approval):
        try:return await asyncio.to_thread(service.retry,import_id,plan_id,decision)
        except Exception as exc:fail(exc)

    return router
