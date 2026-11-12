"""Authenticated project BI agent API. Every URL is bound to one project scope."""
from __future__ import annotations
from uuid import uuid4
from fastapi import APIRouter,HTTPException
from fastapi.responses import Response
from pydantic import BaseModel,ConfigDict,Field
from timepilot.bi_agent_service import BIWorkspaceService,BIWorkspaceError
from timepilot.bi_ui_preferences import BIUIPreferences
from timepilot.bi_perspective_workspace import PerspectiveWorkspaceSnapshots
from timepilot.perspective_capabilities import discover
from timepilot.bi_tool_manifest import for_workspace
from timepilot.bi_analysis_plan import BIAnalysisPlans
from timepilot.bi_reasoning_planner import BIReasoningRuns
from timepilot.bi_tool_registry import (OpenWorkspace, ToolBatch, ChatTurn, SaveWorkspace,
                                        CreateModelDraft)
from timepilot.native_bi import NativeBIError,NativeBIDependencyError
from timepilot.industrial_bi import IndustrialBIError
from timepilot.enterprise_security import SecurityError
from timepilot.modeling_workflow import ModelingError

class Strict(BaseModel):
    model_config=ConfigDict(extra='forbid')

class RevisionCommand(Strict):
    expected_revision:int=Field(ge=1)
    request_id:str=Field(min_length=8,max_length=100)

class OpenSaved(RevisionCommand):
    view_id:str

class UIPatch(Strict):
    asset_id:str
    mode:str|None=None
    width:int|None=None
    last_workspace_id:str|None=None
    dismissed_tips:list[str]|None=None

class PlanCreate(RevisionCommand):
    message:str=Field(min_length=1,max_length=1200)

class PlanUpdate(Strict):
    plan_revision:int=Field(ge=1)
    field_ids:list[str]=Field(min_length=2,max_length=40)
    method:str='pearson'

class PlanRun(Strict):
    plan_revision:int=Field(ge=1)
    request_id:str=Field(min_length=8,max_length=100)
    confirm:bool=False

class PlanClarify(Strict):
    plan_revision:int=Field(ge=1)
    answer:str=Field(min_length=1,max_length=1200)

class PlanCancel(Strict):
    plan_revision:int=Field(ge=1)

class RenderAck(Strict):
    success:bool


class ViewerSnapshotWrite(Strict):
    workspace_revision:int=Field(ge=1)
    viewer_revision:int=Field(ge=0)
    request_id:str=Field(min_length=8,max_length=100)
    token:dict

class ViewerRenderReceipt(Strict):
    request_id:str=Field(min_length=8,max_length=100)
    viewer_revision:int=Field(ge=0)
    source_sha256:str=Field(min_length=64,max_length=64)
    panel_id:str=Field(min_length=1,max_length=128)
    plugin:str=Field(min_length=1,max_length=80)
    success:bool

class ReasoningAck(Strict):
    success:bool
    viewer_receipt:dict

class ReasoningApply(Strict):
    request_id:str=Field(min_length=8,max_length=100)
    confirm:bool=False

class ReasoningCreate(RevisionCommand):
    message:str=Field(min_length=1,max_length=1200)

class ConfirmFreeze(Strict):
    confirmed:bool=False


def create_bi_agent_router(service:BIWorkspaceService):
    router=APIRouter(prefix='/api/v1/projects/{pid}/bi-agent',tags=['Conversational BI'])
    preferences=BIUIPreferences(service)
    analysis=BIAnalysisPlans(service)
    viewer_snapshots=PerspectiveWorkspaceSnapshots(service)
    reasoning=BIReasoningRuns(service)
    def fail(exc):
        if isinstance(exc,SecurityError):raise HTTPException(exc.code,str(exc)) from exc
        if isinstance(exc,KeyError):raise HTTPException(404,str(exc)) from exc
        if isinstance(exc,NativeBIDependencyError):raise HTTPException(503,str(exc)) from exc
        if isinstance(exc,BIWorkspaceError) and any(w in str(exc) for w in ('版本冲突','已变化','修订冲突','状态已改变','必须重新计算','数据来源或工作区版本','计划版本')):
            raise HTTPException(409,str(exc)) from exc
        if isinstance(exc,(ValueError,NativeBIError,IndustrialBIError,ModelingError)):
            raise HTTPException(422,str(exc)) from exc
        raise exc

    @router.get('/ui-preferences')
    def ui_preferences(pid:str,asset_id:str):
        try:return preferences.get(pid,asset_id)
        except Exception as exc:fail(exc)

    @router.patch('/ui-preferences')
    def save_preferences(pid:str,payload:UIPatch):
        try:return preferences.patch(pid,payload.asset_id,payload.model_dump(exclude_none=True,exclude={'asset_id'}))
        except Exception as exc:fail(exc)

    @router.post('/workspaces/{wid}/analysis-plans',status_code=201)
    def create_plan(pid:str,wid:str,payload:PlanCreate):
        try:return analysis.create(pid,wid,payload.message,payload.expected_revision,payload.request_id)
        except Exception as exc:fail(exc)

    @router.get('/workspaces/{wid}/analysis-plans/{plan_id}')
    def get_plan(pid:str,wid:str,plan_id:str):
        try:return analysis.get(pid,wid,plan_id)
        except Exception as exc:fail(exc)

    @router.patch('/workspaces/{wid}/analysis-plans/{plan_id}')
    def revise_plan(pid:str,wid:str,plan_id:str,payload:PlanUpdate):
        try:return analysis.update(pid,wid,plan_id,payload.plan_revision,field_ids=payload.field_ids,method=payload.method)
        except Exception as exc:fail(exc)

    @router.post('/workspaces/{wid}/analysis-plans/{plan_id}/clarify')
    def clarify_plan(pid:str,wid:str,plan_id:str,payload:PlanClarify):
        try:return analysis.clarify(pid,wid,plan_id,payload.plan_revision,payload.answer)
        except Exception as exc:fail(exc)

    @router.post('/workspaces/{wid}/analysis-plans/{plan_id}/cancel')
    def cancel_plan(pid:str,wid:str,plan_id:str,payload:PlanCancel):
        try:return analysis.cancel(pid,wid,plan_id,payload.plan_revision)
        except Exception as exc:fail(exc)

    @router.post('/workspaces/{wid}/analysis-plans/{plan_id}/apply')
    def run_plan(pid:str,wid:str,plan_id:str,payload:PlanRun):
        try:return analysis.apply(pid,wid,plan_id,payload.request_id,payload.plan_revision,confirm=payload.confirm)
        except Exception as exc:fail(exc)

    @router.get('/workspaces/{wid}/analysis-plans/{plan_id}/execution/{execution_id}')
    def get_execution(pid:str,wid:str,plan_id:str,execution_id:str):
        try:return analysis.execution(pid,wid,plan_id,execution_id)
        except Exception as exc:fail(exc)

    @router.post('/workspaces/{wid}/analysis-plans/{plan_id}/execution/{execution_id}/rendered')
    def rendered(pid:str,wid:str,plan_id:str,execution_id:str,payload:RenderAck):
        try:return analysis.render_ack(pid,wid,plan_id,execution_id,success=payload.success)
        except Exception as exc:fail(exc)

    @router.get('/analyses/{analysis_id}/matrix')
    def matrix(pid:str,analysis_id:str):
        try:return analysis.matrix(pid,analysis_id)
        except Exception as exc:fail(exc)

    @router.post('/workspaces/{wid}/reasoning-plans',status_code=201)
    def create_reasoning(pid:str,wid:str,payload:ReasoningCreate):
        try:return reasoning.create(pid,wid,payload.message,payload.expected_revision,payload.request_id)
        except Exception as exc:fail(exc)

    @router.get('/workspaces/{wid}/reasoning-plans/{run_id}')
    def reasoning_status(pid:str,wid:str,run_id:str):
        try:return reasoning.get(pid,wid,run_id)
        except Exception as exc:fail(exc)

    @router.post('/workspaces/{wid}/reasoning-plans/{run_id}/apply')
    def apply_reasoning(pid:str,wid:str,run_id:str,payload:ReasoningApply):
        try:return reasoning.apply(pid,wid,run_id,payload.request_id,confirm=payload.confirm)
        except Exception as exc:fail(exc)

    @router.post('/workspaces/{wid}/reasoning-plans/{run_id}/rendered')
    def acknowledge_reasoning(pid:str,wid:str,run_id:str,payload:ReasoningAck):
        try:return reasoning.rendered(pid,wid,run_id,payload.success,payload.viewer_receipt)
        except Exception as exc:fail(exc)

    @router.get('/workspaces/{wid}/perspective/capabilities')
    def perspective_capabilities(pid:str,wid:str):
        try:return discover(service,pid,wid)
        except Exception as exc:fail(exc)

    @router.get('/workspaces/{wid}/perspective/tools')
    def perspective_tools(pid:str,wid:str):
        try:return for_workspace(service,pid,wid)
        except Exception as exc:fail(exc)

    @router.get('/workspaces/{wid}/snapshot')
    def get_snapshot(pid:str,wid:str):
        try:return viewer_snapshots.get(pid,wid)
        except Exception as exc:fail(exc)

    @router.patch('/workspaces/{wid}/snapshot')
    def save_snapshot(pid:str,wid:str,payload:ViewerSnapshotWrite):
        try:return viewer_snapshots.save(pid,wid,payload.token,payload.workspace_revision,
                                         payload.viewer_revision,payload.request_id)
        except Exception as exc:fail(exc)

    @router.post('/workspaces/{wid}/render-receipts')
    def viewer_render_receipt(pid:str,wid:str,payload:ViewerRenderReceipt):
        try:return viewer_snapshots.render_receipt(pid,wid,payload.request_id,
           payload.success,payload.viewer_revision,payload.panel_id,payload.plugin,payload.source_sha256)
        except Exception as exc:fail(exc)

    @router.get('/tools')
    def tools(pid:str):
        try:return service.tools(pid)
        except Exception as exc:fail(exc)

    @router.get('/workspaces')
    def workspaces(pid:str,asset_id:str|None=None):
        try:return service.list_workspaces(pid,asset_id)
        except Exception as exc:fail(exc)

    @router.post('/workspaces',status_code=201)
    def open_workspace(pid:str,payload:OpenWorkspace):
        try:return service.open(pid,payload.asset_id,payload.version_id)
        except Exception as exc:fail(exc)

    @router.get('/workspaces/{wid}')
    def get_workspace(pid:str,wid:str):
        try:return service.workspace(pid,wid)
        except Exception as exc:fail(exc)

    @router.post('/workspaces/{wid}/preview')
    def preview(pid:str,wid:str,payload:ToolBatch):
        try:return service.preview(pid,wid,payload)
        except Exception as exc:fail(exc)

    @router.post('/workspaces/{wid}/apply')
    def apply(pid:str,wid:str,payload:ToolBatch):
        try:return service.apply(pid,wid,payload)
        except Exception as exc:fail(exc)

    @router.get('/workspaces/{wid}/turns')
    def turns_history(pid:str,wid:str,limit:int=80):
        try:return service.turns_history(pid,wid,limit)
        except Exception as exc:fail(exc)

    @router.post('/workspaces/{wid}/turns')
    def turns(pid:str,wid:str,payload:ChatTurn):
        try:return service.turn(pid,wid,payload.message,payload.expected_revision,payload.request_id,
                    execute=payload.execute,confirm=payload.confirm)
        except Exception as exc:fail(exc)

    @router.get('/workspaces/{wid}/query')
    def query(pid:str,wid:str,limit:int=300):
        try:return service.query(pid,wid,limit=limit)
        except Exception as exc:fail(exc)

    @router.get('/workspaces/{wid}/query.arrow')
    def arrow(pid:str,wid:str,limit:int=1000):
        try:
            item=service.workspace(pid,wid)
            content=service.query(pid,wid,arrow=True,limit=limit)
            return Response(content=content,media_type='application/vnd.apache.arrow.stream',
                headers={'X-TimePilot-Version':item['version_id'],'Cache-Control':'no-store',
                         'X-TimePilot-Workspace-Revision':str(item['revision'])})
        except Exception as exc:fail(exc)

    @router.post('/workspaces/{wid}/undo')
    def undo(pid:str,wid:str,payload:RevisionCommand):
        try:return service.undo_redo(pid,wid,payload.expected_revision,payload.request_id,'undo')
        except Exception as exc:fail(exc)

    @router.post('/workspaces/{wid}/redo')
    def redo(pid:str,wid:str,payload:RevisionCommand):
        try:return service.undo_redo(pid,wid,payload.expected_revision,payload.request_id,'redo')
        except Exception as exc:fail(exc)

    @router.get('/views')
    def views(pid:str,asset_id:str|None=None):
        try:return service.list_views(pid,asset_id)
        except Exception as exc:fail(exc)

    @router.post('/workspaces/{wid}/views',status_code=201)
    def save_view(pid:str,wid:str,payload:SaveWorkspace):
        try:return service.save_view(pid,wid,payload.name,payload.perspective,payload.shared)
        except Exception as exc:fail(exc)

    @router.get('/views/{vid}')
    def view(pid:str,vid:str):
        try:return service.view(pid,vid)
        except Exception as exc:fail(exc)

    @router.get('/views/{vid}/query')
    def saved_query(pid:str,vid:str,limit:int=300):
        try:
            saved=service.view(pid,vid)
            return service.engine.execute(pid,saved['state'],limit=limit)
        except Exception as exc:fail(exc)

    @router.get('/views/{vid}/query.arrow')
    def saved_arrow(pid:str,vid:str,limit:int=1000):
        try:
            saved=service.view(pid,vid)
            content=service.engine.execute(pid,saved['state'],arrow=True,limit=limit)
            return Response(content=content,media_type='application/vnd.apache.arrow.stream',
                headers={'X-TimePilot-Version':saved['version_id'],'Cache-Control':'no-store'})
        except Exception as exc:fail(exc)

    @router.post('/workspaces/{wid}/open-view')
    def open_view(pid:str,wid:str,payload:OpenSaved):
        try:return service.open_view(pid,wid,payload.view_id,payload.expected_revision,payload.request_id)
        except Exception as exc:fail(exc)

    @router.post('/workspaces/{wid}/freeze',status_code=201)
    def freeze(pid:str,wid:str,payload:ConfirmFreeze):
        try:return service.freeze(pid,wid,payload.confirmed)
        except Exception as exc:fail(exc)

    @router.post('/workspaces/{wid}/model-drafts',status_code=201)
    def create_draft(pid:str,wid:str,payload:CreateModelDraft):
        try:return service.model_draft(pid,wid,payload.name,payload.confirm_scope)
        except Exception as exc:fail(exc)

    return router
