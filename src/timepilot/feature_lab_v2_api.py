"""Typed FeatureLab 2.0 HTTP API. All user/project authorization is server-side."""
from __future__ import annotations
from uuid import uuid4
from fastapi import APIRouter,HTTPException,Query
from pydantic import BaseModel,ConfigDict,Field
from timepilot.feature_contracts import FeatureSpecV2,EvaluationProtocol,PlanRequest
from timepilot.feature_evaluation import FeatureEvaluator
from timepilot.enterprise_security import SecurityError
from timepilot.native_bi import NativeBIError,NativeBIDependencyError,safe_json
from timepilot.feature_tool_manifest import manifest
from timepilot.feature_operator_registry import discover
from timepilot.feature_candidates import FeatureCandidateSessions,infer_fields,TEMPLATES
from timepilot.feature_advanced import TSFreshRecipe,FeaturetoolsRecipe,capabilities as advanced_capabilities
from timepilot.feature_advanced_service import AdvancedService,BoundedToolCall
from timepilot.feature_advanced_join import AdvancedJoinService,AdvancedJoinPlan,AdvancedEvaluationRequest
from timepilot.feature_candidate_bridge import list_advanced_batches


class Strict(BaseModel):model_config=ConfigDict(extra='forbid')
class PatchContract(Strict):
    expected_revision:int=Field(ge=0)
    patch:dict
class Confirmation(Strict):
    expected_revision:int=Field(ge=0)
class NewSet(Strict):
    spec:FeatureSpecV2
    request_id:str=Field(min_length=1,max_length=120)
class PatchSet(Strict):
    spec:FeatureSpecV2
    expected_revision:int=Field(ge=1)
class NewJob(Strict):
    request_id:str=Field(min_length=1,max_length=120)
class PreviewJob(NewJob):
    max_rows:int=Field(default=50,ge=1,le=100)
    scan_budget:int=Field(default=200000,ge=100,le=500000)
class ReplanInput(Strict):
    feedback:str=Field(default="",max_length=500)
class ViewerRenderReceipt(Strict):
    render_request_id:str=Field(min_length=1,max_length=120)
    artifact_id:str=Field(min_length=1,max_length=120)
    viewer_version:str=Field(min_length=1,max_length=60)
    workspace:dict
    panel_count:int=Field(ge=2,le=100)
class ProfileJob(NewJob):
    asset_id:str
    version_id:str='base'
class HumanApproval(Strict):
    confirm:bool=False
    reason:str=Field(default='',max_length=600)
class EvaluationPlanRequest(Strict):
    protocol:EvaluationProtocol
class ViewerUpdate(Strict):
    workspace:dict
    expected_revision:int=Field(ge=0)
    source_sha256:str
class CandidateSessionCreate(Strict):
    asset_id:str
    version_id:str='base'
    request_id:str=Field(min_length=1,max_length=120)
    template:str='basic'
    budget:int=Field(default=30,ge=1,le=64)
    selected_field_ids:list[str]|None=None
    task_mode:str='exploration'
class CandidateSessionPatch(Strict):
    expected_revision:int=Field(ge=1)
    selected_candidate_ids:list[str]|None=None
    layout:dict|None=None
class CandidatePreviewRequest(NewJob):
    candidate_ids:list[str]=Field(min_length=1,max_length=8)
    max_rows:int=Field(default=30,ge=1,le=100)
    scan_budget:int=Field(default=200000,ge=100,le=500000)
class CandidateSave(Strict):
    name:str=Field(min_length=1,max_length=120)
    request_id:str=Field(min_length=1,max_length=120)
    candidate_ids:list[str]|None=None

class TimeBasisAccept(Strict):
    asset_id:str=Field(min_length=1)
    version_id:str='base'
    field_id:str=Field(min_length=5)
    role:str
    unit:str|None=None
    expected_revision:int=Field(ge=0)

class AutoAnalysisCreate(Strict):
    asset_id:str=Field(min_length=1)
    version_id:str='base'
    goal:str=Field(default='',max_length=1000)
    task_hint:str='auto'
    budget:int=Field(default=16,ge=1,le=64)
    request_id:str=Field(min_length=1,max_length=120)

class AutoAnalysisRetry(Strict):
    expected_revision:int=Field(ge=1)

class ReportArchiveRequest(Strict):
    expected_revision:int=Field(ge=1)

class DataAnalysisStart(Strict):
    asset_id:str=Field(min_length=1)
    version_id:str='base'


class DashboardQuery(Strict):
    asset_id: str=Field(min_length=1)
    version_id: str='base'
    expected_sha256: str|None=None
    expected_revision: int|None=Field(default=None,ge=0)
    filters: list[dict]=Field(default_factory=list,max_length=5)
    field_ids: list[str]|None=Field(default=None,max_length=24)
    association_ids: list[str]|None=Field(default=None,max_length=10)
    association_method: str='pearson'

class DashboardChartQuery(DashboardQuery):
    kind: str='histogram'
    field_id: str
    other_field_id: str|None=None
    bins: int=Field(default=16,ge=4,le=50)
    limit: int=Field(default=12,ge=3,le=25)
    granularity: str='day'


class AutoAnalysisSelection(Strict):
    expected_revision:int=Field(ge=1)
    candidate_ids:list[str]=Field(default_factory=list,max_length=64)

class AutoSupervision(Strict):
    confirm:bool=False
    label_field_id:str=Field(min_length=1,max_length=100)
    prediction_time_field_id:str|None=Field(default=None,max_length=100)
    label_available_at_field_id:str|None=Field(default=None,max_length=100)
    split_kind:str='time'
    static_independence_confirmed:bool=False
    group_field_ids:list[str]=Field(default_factory=list,max_length=3)

class AutoAnalysisSave(Strict):
    name:str=Field(min_length=1,max_length=120)
    request_id:str=Field(min_length=1,max_length=120)
    supervision:AutoSupervision|None=None

class AutoAcceptAndRun(Strict):
    name:str=Field(min_length=1,max_length=120)
    request_id:str=Field(min_length=1,max_length=100)
    confirmed:bool=False
    supervision:AutoSupervision|None=None

class AutoViewerReceipt(Strict):
    expected_revision:int=Field(ge=1)
    artifact_id:str=Field(min_length=1,max_length=120)
    artifact_sha256:str=Field(min_length=64,max_length=64)
    viewer_version:str=Field(min_length=1,max_length=60)
    panel_count:int=Field(ge=2,le=100)
    workspace:dict

class ExpressionCheck(Strict):
    asset_id:str
    version_id:str='base'
    source_sha256:str=Field(min_length=32,max_length=128)
    contract_revision:int=Field(ge=0)
    input_field_ids:list[str]=Field(min_length=1,max_length=8)
    expression:str=Field(min_length=1,max_length=240)
    display_name:str|None=Field(default=None,max_length=160)

class EvaluationSelectionRequest(Strict):
    feature_ids:list[str]=Field(default_factory=list,max_length=100)
    search_candidate_ids:list[str]=Field(default_factory=list,max_length=96)
    expected_revision:int=Field(default=0,ge=0)
    note:str=Field(default='',max_length=1000)

class DeriveSelectionRequest(Strict):
    selection_revision:int=Field(ge=1)
    request_id:str=Field(min_length=1,max_length=120)
    name:str=Field(min_length=1,max_length=120)

class MonitorCompare(Strict):
    reference_profile_id:str
    current_profile_id:str


def create_feature_lab_v2_router(projects,native,semantic,registry,jobs,models=None):
    router=APIRouter()
    evaluator=FeatureEvaluator(projects,native,semantic,registry,jobs)
    from timepilot.feature_monitor import FeatureMonitor
    monitor=FeatureMonitor(projects)
    from timepilot.feature_viewer_state import FeatureViewerState
    viewer_state=FeatureViewerState(projects,registry)
    from timepilot.feature_agent import FeatureAgent
    agent=FeatureAgent(projects,native,semantic,registry,jobs,models)
    candidates=FeatureCandidateSessions(projects,native,semantic,registry)
    from timepilot.feature_auto_analysis import FeatureAutoAnalysis
    auto_analysis=FeatureAutoAnalysis(projects,native,semantic,registry,jobs,models)
    from timepilot.feature_report_artifacts import FeatureReportArtifacts
    report_artifacts=FeatureReportArtifacts(auto_analysis)
    from timepilot.feature_data_analysis import FeatureDataAnalysis
    eda=FeatureDataAnalysis(projects,semantic,jobs,native)
    from timepilot.feature_analysis_journey import FeatureAnalysisJourney
    journey=FeatureAnalysisJourney(eda,auto_analysis)
    from timepilot.feature_insights import build_insight_brief
    from timepilot.feature_workbench import FeatureWorkbenchCharts
    workbench_charts=FeatureWorkbenchCharts(auto_analysis,candidates,semantic,native)
    advanced=AdvancedService(projects,native,semantic,registry,jobs)
    advanced_join=AdvancedJoinService(projects,native,semantic,registry,jobs)
    from timepilot.feature_workflow import FeatureWorkflow
    workflow=FeatureWorkflow(projects,semantic,registry)

    def fail(exc):
        if isinstance(exc,HTTPException):raise exc
        if isinstance(exc,SecurityError):raise HTTPException(exc.code,str(exc)) from exc
        if isinstance(exc,KeyError):raise HTTPException(404,str(exc)) from exc
        if isinstance(exc,(NativeBIDependencyError,ModuleNotFoundError,ImportError)):
            raise HTTPException(503,{'code':'dependency_unavailable','message':str(exc)}) from exc
        if isinstance(exc,(ValueError,NativeBIError)):
            detail=str(exc)
            status=409 if any(x in detail for x in ('冲突','已变化','已过期','失效','指纹不一致')) else 422
            raise HTTPException(status,{'code':'validation_failed','message':detail}) from exc
        raise exc

    @router.get('/workflow/summary')
    def workflow_summary(pid:str,asset_id:str,version_id:str='base'):
        try:return workflow.summary(pid,asset_id,version_id)
        except Exception as exc:fail(exc)

    @router.get('/contracts')
    def contract(pid:str,asset_id:str,version_id:str='base'):
        try:return semantic.latest(pid,asset_id,version_id)
        except Exception as e:fail(e)

    @router.patch('/contracts/{aid}')
    def update_contract(pid:str,aid:str,body:PatchContract,version_id:str='base'):
        try:return semantic.patch(pid,aid,version_id,body.patch,body.expected_revision)
        except Exception as e:fail(e)

    @router.post('/contracts/{aid}/confirm')
    def confirm_contract(pid:str,aid:str,body:Confirmation,version_id:str='base'):
        try:return semantic.confirm(pid,aid,version_id,body.expected_revision)
        except Exception as e:fail(e)

    @router.get('/fields/search')
    def search_fields(pid:str,asset_id:str,version_id:str='base',q:str='',limit:int=50):
        try:return semantic.search(pid,asset_id,version_id,q,limit)
        except Exception as e:fail(e)

    @router.get('/operators')
    def operators(pid:str,asset_id:str,version_id:str='base'):
        try:
            doc=semantic.latest(pid,asset_id,version_id)
            return {'operators':discover(contract=doc),'contract_revision':doc.revision}
        except Exception as e:fail(e)

    @router.get('/field-suggestions')
    def suggestions(pid:str,asset_id:str,version_id:str='base'):
        try:
            doc=semantic.latest(pid,asset_id,version_id)
            return {'asset_id':asset_id,'version_id':version_id,
                    'contract_revision':doc.revision,'fields':infer_fields(doc)}
        except Exception as exc:fail(exc)

    @router.get('/time-basis/infer')
    def time_basis_infer(pid:str,asset_id:str,version_id:str='base'):
        try:
            from timepilot.feature_time_inference import infer_time_fields
            contract=semantic.latest(pid,asset_id,version_id)
            profile=None
            previous=jobs.recent_profiles(pid,asset_id,version_id,limit=1)
            if previous and previous[0]['source_sha256']==contract.source_sha256 and previous[0]['contract_revision']==contract.revision:
                profile=jobs.get_profile(pid,previous[0]['profile_id'])
            return {'fields':infer_time_fields(contract,profile),
                    'source_sha256':contract.source_sha256,
                    'contract_revision':contract.revision,
                    'profile_scope':profile.get('scope') if profile else 'name_and_type_only'}
        except Exception as exc:fail(exc)

    @router.post('/time-basis/accept')
    def time_basis_accept(pid:str,body:TimeBasisAccept):
        try:
            from timepilot.feature_time_inference import infer_time_basis
            from timepilot.feature_compiler import NUMERIC
            contract=semantic.latest(pid,body.asset_id,body.version_id)
            if contract.revision!=body.expected_revision or contract.state=='stale':
                raise ValueError('Field contract revision changed; refresh first')
            field=next((f for f in contract.fields if f.field_id==body.field_id),None)
            if field is None:raise ValueError('Unknown source field')
            basis=infer_time_basis(field)
            if body.role not in ('order_index','elapsed_time'):
                raise ValueError('Only sequence or elapsed-time roles can be confirmed here')
            if not NUMERIC.match(field.physical_type):
                raise ValueError('Sequence and elapsed time roles require a numeric field')
            if body.role=='order_index' and basis['kind']!='sequence_order_candidate':
                raise ValueError('This column is not a supported sequence-order suggestion')
            if body.role=='elapsed_time' and (body.unit not in ('s','ms','min','h')
                or basis['kind']!='elapsed_time_candidate'):
                raise ValueError('Elapsed time needs a supported unit and matching source evidence')
            # No calendar time is fabricated. This is a field-role change only.
            return semantic.patch(pid,body.asset_id,body.version_id,{
                'fields':[{'field_id':field.field_id,'semantic_type':'numeric',
                           'role':body.role,'confirmation':'confirmed',
                           'unit':body.unit if body.role=='elapsed_time' else field.unit,
                           'evidence':(field.evidence+['User confirmed a '+body.role+' interpretation'])[:30]}]},
                body.expected_revision)
        except Exception as exc:fail(exc)

    @router.get('/workspace')
    def analysis_workspace(pid:str,asset_id:str,version_id:str='base'):
        try:return journey.status(pid,asset_id,version_id)
        except Exception as exc:fail(exc)

    @router.post('/workspace/start',status_code=202)
    def start_workspace(pid:str,body:DataAnalysisStart):
        try:return journey.start(pid,body.asset_id,body.version_id)
        except Exception as exc:fail(exc)

    @router.get('/workspace/insights')
    def analysis_workspace_insights(pid:str,asset_id:str,version_id:str='base'):
        try:
            # The existing source/owner checks run before any result is returned.
            analysis=eda.status(pid,asset_id,version_id)
            source=eda.semantic.source(pid,asset_id,version_id)
            contract=eda.semantic.latest(pid,asset_id,version_id,source=source)
            session=auto_analysis.latest(pid,asset_id,version_id)
            delivery=None
            if session and not session.get('stale') and session.get('feature_set_id'):
                delivery=auto_analysis.delivery(pid,session['session_id'])
            result=build_insight_brief(source_sha256=analysis['source_sha256'],
                contract_revision=analysis['contract_revision'],
                data_analysis=analysis,session=session,delivery=delivery,contract=contract)
            result['task_status']=(session or {}).get('status') or 'not_started'
            result['task_stage']=(session or {}).get('stage')
            return result
        except Exception as exc:fail(exc)

    @router.get('/data-analysis')
    def data_analysis(pid:str,asset_id:str,version_id:str='base'):
        try:return eda.status(pid,asset_id,version_id)
        except Exception as exc:fail(exc)

    @router.post('/data-analysis/start',status_code=202)
    def start_data_analysis(pid:str,body:DataAnalysisStart):
        try:return eda.start(pid,body.asset_id,body.version_id)
        except Exception as exc:fail(exc)

    @router.get('/data-analysis/chart')
    def data_analysis_chart(pid:str,asset_id:str,field_id:str,version_id:str='base',mode:str='distribution', entity_field_id:str|None=None, entity_value:str|None=None, start:str|None=None, end:str|None=None, resolution:int=120):
        try:return eda.chart(pid,asset_id,version_id,field_id,mode,entity_field_id=entity_field_id,entity_value=entity_value,start=start,end=end,resolution=resolution)
        except Exception as exc:dashboard_fail(exc,stage='dashboard.legacy_chart',kind=mode)

    @router.get('/data-analysis/chart-capabilities')
    def data_analysis_capabilities(pid:str,asset_id:str,version_id:str='base'):
        try:
            from timepilot.feature_dashboard_v0372 import _numeric, _time
            src,con=eda._source(pid,asset_id,version_id)
            numeric=[f.field_id for f in con.fields if f.classification!='restricted' and _numeric(f)]
            categories=[f.field_id for f in con.fields if f.classification!='restricted']
            t=next((f for f in con.fields if f.field_id==con.event_time_field_id and
                    f.confirmation=='confirmed' and f.classification!='restricted' and _time(f)),None)
            all_kinds={'histogram':numeric,'box':numeric,'quantiles':numeric,
                       'category':categories,'scatter':numeric if len(numeric)>1 else [],
                       'trend':numeric if t else []}
            hints={'trend':'先确认真实日历时间字段（DATE/TIMESTAMP）；记录序号不能直接作为日期轴',
                   'scatter':'需要两个不同的数值字段',
                   'histogram':'需要有限的数值字段', 'box':'需要有限的数值字段',
                   'quantiles':'需要有限的数值字段','category':'请选择非受限字段，连续数值优先使用直方图'}
            return {'asset_id':asset_id,'version_id':version_id,'source_sha256':src['sha256'],
                'contract_revision':con.revision,'confirmed_time_field_id':t.field_id if t else None,
                'kinds':{kind:{'enabled':bool(ids),'field_ids':ids,'hint':hints[kind]}
                         for kind,ids in all_kinds.items()}}
        except Exception as exc:dashboard_fail(exc,stage='dashboard.capabilities')

    def observe_dashboard(operation, kind, action):
        import time
        from timepilot.system_logs import log_dashboard_performance
        started=time.perf_counter()
        succeeded=False
        try:
            result=action()
            succeeded=True
            return result
        finally:
            log_dashboard_performance(operation=operation, chart_kind=kind,
                milliseconds=(time.perf_counter()-started)*1000, success=succeeded)

    def dashboard_fail(exc, *, stage:str, kind:str=''):
        # Expected invalid chart/filter options retain 422/409 responses.
        if isinstance(exc,(HTTPException,SecurityError,KeyError,ValueError,
                           NativeBIError,NativeBIDependencyError,ModuleNotFoundError,ImportError)):
            return fail(exc)
        from timepilot.system_logs import log_failure, REQUEST_ID, classify_error
        diagnostic=log_failure(exc,stage=stage,context={'kind':kind,
            'sql_template_id':'tp.category.counts.v2' if kind=='category' else 'tp.dashboard.'+str(kind or 'overview')})
        raise HTTPException(status_code=500,detail={
            'code':classify_error(exc),
            'message':'统计计算未完成，请在系统日志中按诊断编号查看原因',
            'diagnostic_id':diagnostic,'request_id':REQUEST_ID.get()}) from exc

    @router.post('/data-analysis/dashboard')
    def data_analysis_dashboard(pid:str, body:DashboardQuery):
        try:
            return observe_dashboard('dashboard.overview','overview',lambda: eda.dashboard(
                pid,body.asset_id,body.version_id,
                filters=body.filters,field_ids=body.field_ids,
                association_ids=body.association_ids,association_method=body.association_method,
                expected_sha256=body.expected_sha256,expected_revision=body.expected_revision))
        except Exception as exc:dashboard_fail(exc,stage='dashboard.overview')

    @router.post('/data-analysis/dashboard/report')
    def data_analysis_dashboard_report(pid:str, body:DashboardQuery):
        try:
            from timepilot.feature_evidence_v0373 import dashboard_markdown
            payload=observe_dashboard('dashboard.report','report',lambda: eda.dashboard(
                pid,body.asset_id,body.version_id,
                filters=body.filters,field_ids=body.field_ids,
                association_ids=body.association_ids,association_method=body.association_method,
                expected_sha256=body.expected_sha256,expected_revision=body.expected_revision))
            return dashboard_markdown(payload)
        except Exception as exc:dashboard_fail(exc,stage='dashboard.report')

    @router.post('/data-analysis/dashboard/chart')
    def data_analysis_dashboard_chart(pid:str, body:DashboardChartQuery):
        try:
            return observe_dashboard('dashboard.chart',body.kind,lambda: eda.dashboard_chart(
                pid,body.asset_id,body.version_id,
                kind=body.kind,field_id=body.field_id,other_field_id=body.other_field_id,
                filters=body.filters,bins=body.bins,limit=body.limit,
                granularity=body.granularity,expected_sha256=body.expected_sha256,
                expected_revision=body.expected_revision))
        except Exception as exc:dashboard_fail(exc,stage='dashboard.chart',kind=body.kind)

    @router.get('/analysis/latest')
    def auto_latest(pid:str,asset_id:str,version_id:str='base'):
        try:return {'session':auto_analysis.latest(pid,asset_id,version_id)}
        except Exception as exc:fail(exc)

    @router.post('/analysis/sessions',status_code=201)
    def auto_create(pid:str,body:AutoAnalysisCreate):
        try:return auto_analysis.create(pid,body.asset_id,body.version_id,
               goal=body.goal,task_hint=body.task_hint,request_id=body.request_id,budget=body.budget)
        except Exception as exc:fail(exc)

    @router.get('/analysis/sessions/{sid}')
    def auto_get(pid:str,sid:str):
        try:return auto_analysis.get(pid,sid)
        except Exception as exc:fail(exc)

    @router.post('/analysis/sessions/{sid}/advance')
    def auto_advance(pid:str,sid:str):
        try:return auto_analysis.advance(pid,sid)
        except Exception as exc:fail(exc)

    @router.post('/analysis/sessions/{sid}/retry')
    def auto_retry(pid:str,sid:str,body:AutoAnalysisRetry):
        try:return auto_analysis.retry_failed(pid,sid,body.expected_revision)
        except Exception as exc:fail(exc)

    @router.put('/analysis/sessions/{sid}/selection')
    def auto_select(pid:str,sid:str,body:AutoAnalysisSelection):
        try:return auto_analysis.select(pid,sid,body.candidate_ids,body.expected_revision)
        except Exception as exc:fail(exc)

    @router.post('/analysis/sessions/{sid}/save-set',status_code=201)
    def auto_save(pid:str,sid:str,body:AutoAnalysisSave):
        try:return auto_analysis.save_set(pid,sid,body.name,body.request_id,
             supervision=body.supervision.model_dump() if body.supervision else None)
        except Exception as exc:fail(exc)

    @router.post('/analysis/sessions/{sid}/accept-and-run',status_code=202)
    def auto_accept_and_run(pid:str,sid:str,body:AutoAcceptAndRun):
        try:return auto_analysis.accept_and_run(pid,sid,body.name,body.request_id,
            confirmed=body.confirmed,
            supervision=body.supervision.model_dump() if body.supervision else None)
        except Exception as exc:fail(exc)

    @router.post('/analysis/sessions/{sid}/materialize',status_code=202)
    def auto_materialize(pid:str,sid:str,body:NewJob):
        try:return auto_analysis.materialize(pid,sid,body.request_id)
        except Exception as exc:fail(exc)

    @router.get('/analysis/sessions/{sid}/supervision-options')
    def auto_supervision_options(pid:str,sid:str):
        try:return auto_analysis.supervision(pid,sid)
        except Exception as exc:fail(exc)

    @router.get('/analysis/sessions/{sid}/delivery')
    def auto_delivery(pid:str,sid:str):
        try:return auto_analysis.delivery(pid,sid)
        except Exception as exc:fail(exc)

    @router.post('/analysis/sessions/{sid}/viewer-receipt')
    def auto_viewer_receipt(pid:str,sid:str,body:AutoViewerReceipt):
        try:return auto_analysis.viewer_receipt(pid,sid,**body.model_dump())
        except Exception as exc:fail(exc)

    @router.get('/analysis/sessions/{sid}/candidates/{cid}/chart')
    def candidate_chart(pid:str,sid:str,cid:str,mode:str='auto', entity_field_id:str|None=None, entity_value:str|None=None, start:str|None=None, end:str|None=None, resolution:int=120):
        try:return workbench_charts.candidate_chart(pid,sid,cid,mode=mode,entity_field_id=entity_field_id,entity_value=entity_value,start=start,end=end,resolution=resolution)
        except Exception as exc:fail(exc)

    @router.get('/analysis/sessions/{sid}/report')
    def auto_report(pid:str,sid:str):
        try:return auto_analysis.report_markdown(pid,sid)
        except Exception as exc:fail(exc)

    @router.post('/analysis/sessions/{sid}/reports',status_code=201)
    def archive_report(pid:str,sid:str,body:ReportArchiveRequest):
        try:return report_artifacts.archive(pid,sid,body.expected_revision)
        except Exception as exc:fail(exc)

    @router.get('/analysis/sessions/{sid}/reports')
    def list_reports(pid:str,sid:str):
        try:return {'reports':report_artifacts.list(pid,sid)}
        except Exception as exc:fail(exc)

    @router.get('/reports/{report_id}')
    def archived_report(pid:str,report_id:str):
        try:return report_artifacts.get(pid,report_id)
        except Exception as exc:fail(exc)

    @router.get('/reports/{report_id}/diff')
    def report_diff(pid:str,report_id:str,other_id:str):
        try:return report_artifacts.diff(pid,report_id,other_id)
        except Exception as exc:fail(exc)

    @router.post('/analysis/sessions/{sid}/explain')
    def auto_explain(pid:str,sid:str):
        try:return auto_analysis.explain(pid,sid)
        except Exception as exc:fail(exc)

    @router.get('/analysis/sessions/{sid}/agent')
    def auto_agent_status(pid:str,sid:str):
        try:return auto_analysis.agent_status(pid,sid)
        except Exception as exc:fail(exc)

    @router.post('/expressions/validate')
    def expression_validate(pid:str,body:ExpressionCheck):
        try:
            registry._editor(pid)
            source=semantic.source(pid,body.asset_id,body.version_id)
            contract=semantic.latest(pid,body.asset_id,body.version_id,source=source)
            if (source['sha256']!=body.source_sha256
                    or contract.revision!=body.contract_revision or contract.state=='stale'):
                raise ValueError('公式引用的数据源或字段契约已变化')
            from timepilot.feature_custom import validate_custom_definition
            return validate_custom_definition(contract,body.input_field_ids,
                                              body.expression,body.display_name)
        except Exception as exc:fail(exc)

    @router.get('/analysis/advanced-batches')
    def advanced_candidate_batches(pid:str,asset_id:str,version_id:str='base'):
        try:
            return list_advanced_batches(pid,asset_id,version_id,
                projects=projects,native=native,semantic=semantic,
                registry=registry,advanced=advanced,join_service=advanced_join)
        except Exception as exc:fail(exc)

    @router.get('/advanced/capabilities')
    def advanced_capabilities_get(pid:str):
        try:
            projects.get(pid)
            return advanced_capabilities()
        except Exception as exc:fail(exc)

    @router.post('/advanced/validate')
    def advanced_validate(pid:str,body:TSFreshRecipe|FeaturetoolsRecipe):
        try:return advanced.validate(pid,body)
        except Exception as exc:fail(exc)

    @router.post('/advanced/tool-call')
    def advanced_agent_call(pid:str,body:BoundedToolCall):
        try:return advanced.tool_call(pid,body)
        except Exception as exc:fail(exc)

    class JoinRequest(Strict):
        plan:AdvancedJoinPlan
        request_id:str=Field(min_length=1,max_length=120)
        confirm:bool=False

    @router.post('/advanced/join/validate')
    def advanced_join_validate(pid:str,body:AdvancedJoinPlan):
        try:return advanced_join.plan(pid,body)
        except Exception as exc:fail(exc)

    @router.post('/advanced/join/jobs',status_code=202)
    def advanced_join_submit(pid:str,body:JoinRequest):
        try:return advanced_join.submit(pid,body.plan,body.request_id,confirmed=body.confirm)
        except Exception as exc:fail(exc)

    @router.get('/advanced/join/results/{jid}')
    def advanced_join_result(pid:str,jid:str):
        try:return advanced_join.result(pid,jid)
        except Exception as exc:fail(exc)

    @router.post('/advanced/join/evaluations',status_code=202)
    def advanced_join_evaluate(pid:str,body:AdvancedEvaluationRequest):
        try:return advanced_join.submit_evaluation(pid,body)
        except Exception as exc:fail(exc)

    @router.get('/advanced/join/evaluations/{eid}')
    def advanced_join_eval_result(pid:str,eid:str):
        try:return advanced_join.evaluation_result(pid,eid)
        except Exception as exc:fail(exc)

    @router.get('/advanced/jobs/latest')
    def advanced_latest_job(pid:str,asset_id:str):
        try:
            projects.require_native(pid,asset_id)
            with projects._db() as db:
                row=db.execute('''SELECT job_id FROM feature_jobs
                  WHERE project_id=? AND owner_id=? AND target_id=?
                  AND kind IN ('advanced_tsfresh','advanced_featuretools')
                  ORDER BY created_at DESC LIMIT 1''',
                  (pid,registry._actor(),asset_id)).fetchone()
            return {'job':jobs.get_job(pid,row['job_id']) if row else None}
        except Exception as exc:fail(exc)

    @router.get('/advanced/jobs/{jid}')
    def advanced_job_status(pid:str,jid:str):
        try:
            job=jobs.get_job(pid,jid)
            if job['kind'] not in ('advanced_tsfresh','advanced_featuretools'):
                raise KeyError('高级特征计算任务不存在')
            return job
        except Exception as exc:fail(exc)

    @router.get('/advanced/artifacts/{jid}')
    def advanced_result_get(pid:str,jid:str):
        try:return advanced.result(pid,jid)
        except Exception as exc:fail(exc)

    @router.get('/candidate-sessions/latest')
    def candidate_latest(pid:str,asset_id:str,version_id:str='base'):
        try:return {'session':candidates.latest(pid,asset_id,version_id)}
        except Exception as exc:fail(exc)

    @router.post('/candidate-sessions',status_code=201)
    def candidate_new(pid:str,body:CandidateSessionCreate):
        try:return candidates.create(pid,body.asset_id,body.version_id,request_id=body.request_id,
            template=body.template,budget=body.budget,selected_fields=body.selected_field_ids,
            task_mode=body.task_mode)
        except Exception as exc:fail(exc)

    @router.get('/candidate-sessions/{sid}')
    def candidate_get(pid:str,sid:str):
        try:return candidates.get(pid,sid)
        except Exception as exc:fail(exc)

    @router.patch('/candidate-sessions/{sid}')
    def candidate_patch(pid:str,sid:str,body:CandidateSessionPatch):
        try:return candidates.update(pid,sid,body.expected_revision,
              selected_candidate_ids=body.selected_candidate_ids,layout=body.layout)
        except Exception as exc:fail(exc)

    @router.get('/candidate-sessions/{sid}/latest-preview')
    def candidate_latest_preview(pid:str,sid:str):
        try:
            session=candidates.get(pid,sid)
            if session['stale']:return {'job':None,'stale':True}
            with projects._db() as db:
                row=db.execute('''SELECT job_id FROM feature_jobs
                    WHERE project_id=? AND owner_id=? AND kind='candidate_preview' AND target_id=?
                    ORDER BY created_at DESC LIMIT 1''',
                    (pid,registry._actor(),sid)).fetchone()
            job=jobs.get_job(pid,row['job_id']) if row else None
            if job and (job['payload'].get('source_sha256')!=session['source_sha256']
                        or job['payload'].get('contract_revision')!=session['contract_revision']):
                return {'job':None,'stale':True}
            return {'job':job,'stale':False}
        except Exception as exc:fail(exc)

    @router.post('/candidate-sessions/{sid}/preview-jobs',status_code=202)
    def candidate_preview(pid:str,sid:str,body:CandidatePreviewRequest):
        try:return jobs.create_candidate_preview(pid,sid,body.candidate_ids,
               body.request_id,body.max_rows,body.scan_budget)
        except Exception as exc:fail(exc)

    @router.post('/candidate-sessions/{sid}/save-set',status_code=201)
    def candidate_save(pid:str,sid:str,body:CandidateSave):
        try:
            registry._editor(pid)
            spec=candidates.to_spec(pid,sid,body.name,body.candidate_ids)
            return registry.create(pid,spec,body.request_id)
        except Exception as exc:fail(exc)

    @router.post('/profile-jobs',status_code=202)
    def create_profile(pid:str,body:ProfileJob):
        try:return jobs.create_profile(pid,body.asset_id,body.version_id,body.request_id)
        except Exception as e:fail(e)

    @router.get('/profile-jobs')
    def recent_jobs(pid:str,asset_id:str|None=None,limit:int=20):
        try:return {'items':jobs.list_jobs(pid,asset_id,limit)}
        except Exception as e:fail(e)

    @router.get('/profile-jobs/{jid}')
    def profile_status(pid:str,jid:str):
        try:
            result=jobs.get_job(pid,jid)
            if result['kind']!='profile':raise KeyError('画像任务不存在')
            return result
        except Exception as e:fail(e)

    @router.get('/profiles')
    def recent_profiles(pid:str,asset_id:str,version_id:str='base',limit:int=20):
        try:return {'items':jobs.recent_profiles(pid,asset_id,version_id,limit)}
        except Exception as e:fail(e)

    @router.get('/profiles/{profile_id}')
    def profile(pid:str,profile_id:str):
        try:return jobs.get_profile(pid,profile_id)
        except Exception as e:fail(e)

    @router.get('/sets')
    def list_sets(pid:str,asset_id:str|None=None,state:str|None=None):
        try:return registry.list(pid,asset_id,state)
        except Exception as e:fail(e)

    @router.post('/sets',status_code=201)
    def new_set(pid:str,body:NewSet):
        try:return registry.create(pid,body.spec,body.request_id)
        except Exception as e:fail(e)

    @router.get('/sets/temporal-audits')
    def temporal_audit_list(pid:str,asset_id:str|None=None):
        try:
            rows=registry.list(pid,aid=asset_id)
            return {'items':[registry.temporal_audit(pid,r['set_id']) for r in rows],
                    'scope':'schema_only','count':len(rows)}
        except Exception as exc:fail(exc)

    @router.get('/sets/{sid}')
    def get_set(pid:str,sid:str):
        try:return registry.get(pid,sid)
        except Exception as e:fail(e)

    @router.patch('/sets/{sid}')
    def update_set(pid:str,sid:str,body:PatchSet):
        try:return registry.update(pid,sid,body.spec,body.expected_revision)
        except Exception as e:fail(e)

    @router.get('/sets/{sid}/temporal-audit')
    def temporal_audit_one(pid:str,sid:str):
        try:return registry.temporal_audit(pid,sid)
        except Exception as exc:fail(exc)

    @router.get('/sets/{sid}/latest-artifact')
    def latest_artifact(pid:str,sid:str):
        try:
            rec=registry.get(pid,sid)
            registry.pinned(pid,rec)
            from timepilot.feature_artifact_policy import eligible_artifact
            ready=eligible_artifact(projects,pid,rec,semantic)
            if not ready:return {'ready':False,'artifact':None}
            return {'ready':True,'artifact':ready['metadata'],
                    'verified':True,'scope':'full','source_sha256':rec['source_sha256']}
        except Exception as exc:fail(exc)

    @router.get('/sets/{sid}/revisions')
    def revisions(pid:str,sid:str):
        try:return registry.list_revisions(pid,sid)
        except Exception as e:fail(e)

    @router.get('/sets/{sid}/revisions/{revision}')
    def revision(pid:str,sid:str,revision:int):
        try:return registry.get_revision(pid,sid,revision)
        except Exception as e:fail(e)

    @router.get('/sets/{sid}/diff')
    def revision_diff(pid:str,sid:str,left:int,right:int):
        try:return registry.diff_revisions(pid,sid,left,right)
        except Exception as e:fail(e)

    @router.get('/sets/{sid}/viewer-state')
    def get_viewer(pid:str,sid:str):
        try:return viewer_state.get(pid,sid)
        except Exception as e:fail(e)

    @router.patch('/sets/{sid}/viewer-state')
    def save_viewer(pid:str,sid:str,body:ViewerUpdate):
        try:return viewer_state.save(pid,sid,body.workspace,body.expected_revision,body.source_sha256)
        except Exception as e:fail(e)

    @router.post('/sets/validate')
    def validate_spec(pid:str,body:FeatureSpecV2):
        try:return registry.validate(pid,body)
        except Exception as e:fail(e)

    @router.post('/sets/{sid}/preview-jobs',status_code=202)
    def preview_set(pid:str,sid:str,body:PreviewJob):
        try:return jobs.create_preview(pid,sid,body.request_id,body.max_rows,body.scan_budget)
        except Exception as e:fail(e)

    @router.post('/sets/{sid}/runs',status_code=202)
    def materialize(pid:str,sid:str,body:NewJob):
        try:return jobs.create_run(pid,sid,body.request_id)
        except Exception as e:fail(e)

    @router.get('/runs/{rid}')
    def run_status(pid:str,rid:str):
        try:return jobs.get_job(pid,rid)
        except Exception as e:fail(e)

    @router.post('/runs/{rid}/cancel')
    def cancel_run(pid:str,rid:str):
        try:return jobs.cancel(pid,rid)
        except Exception as e:fail(e)

    @router.post('/runs/{rid}/retry')
    def retry_run(pid:str,rid:str):
        try:return jobs.retry(pid,rid)
        except Exception as e:fail(e)

    @router.get('/runs/{rid}/result')
    def run_result(pid:str,rid:str,preview_rows:int=Query(default=25,ge=0,le=100)):
        try:
            job=jobs.get_job(pid,rid)
            if job['status']!='succeeded' or job['kind']!='materialize':
                raise HTTPException(409,{'code':'not_ready','message':'全量物化结果尚未完成'})
            artifact=job['result']
            if not isinstance(artifact,dict) or not artifact.get('artifact_id'):
                raise ValueError('任务缺少可核验的结果产物')
            with projects._db() as db:
                owner=db.execute('''SELECT r.set_id FROM feature_artifacts a
                    JOIN feature_runs r ON r.run_id=a.run_id
                    WHERE a.project_id=? AND a.artifact_id=? AND r.job_id=?''',
                    (pid,artifact['artifact_id'],rid)).fetchone()
            if owner is None:raise ValueError('计算产物与任务或项目不匹配')
            rec=registry.get(pid,owner['set_id'])
            from timepilot.feature_artifact_policy import eligible_artifact
            valid=eligible_artifact(projects,pid,rec,semantic,artifact_id=artifact['artifact_id'])
            if valid is None:raise ValueError('计算产物未通过成功状态、来源和文件完整性校验')
            path=valid['path']
            rows=[]
            if preview_rows:
                with native._connection() as con:
                    cur=con.execute('SELECT * FROM read_parquet(?) LIMIT ?',[str(path),preview_rows])
                    columns=[item[0] for item in cur.description]
                    rows=[dict(zip(columns,safe_json(x))) for x in cur.fetchall()]
            return {'artifact':artifact,'scope':'full','preview_scope':'sample',
                    'preview_rows':rows,'preview_count':len(rows)}
        except Exception as e:fail(e)

    @router.post('/sets/{sid}/request-review')
    def review_request(pid:str,sid:str):
        try:return registry.request_review(pid,sid)
        except Exception as e:fail(e)

    @router.post('/sets/{sid}/approve')
    def approve(pid:str,sid:str,body:HumanApproval):
        try:return registry.approve(pid,sid,body.reason,body.confirm)
        except Exception as e:fail(e)

    @router.get('/sets/{sid}/publication-preview')
    def publication_preview(pid:str,sid:str):
        try:return registry.publication_preview(pid,sid)
        except Exception as e:fail(e)

    @router.post('/sets/{sid}/publish',status_code=201)
    def publish(pid:str,sid:str,body:HumanApproval):
        try:return registry.publish(pid,sid,body.confirm)
        except Exception as e:fail(e)

    @router.get('/sets/{sid}/lineage')
    def lineage(pid:str,sid:str):
        try:return registry.lineage(pid,sid)
        except Exception as e:fail(e)

    @router.post('/evaluations/plans',status_code=201)
    def evaluation_plan(pid:str,body:EvaluationPlanRequest):
        try:return evaluator.plan(pid,body.protocol)
        except Exception as e:fail(e)

    @router.post('/evaluations/plans/{plan_id}/run',status_code=202)
    def evaluation_run(pid:str,plan_id:str,body:NewJob):
        try:return evaluator.submit(pid,plan_id,body.request_id)
        except Exception as e:fail(e)

    @router.get('/evaluations/sets/{sid}/latest')
    def latest_evaluation(pid:str,sid:str):
        try:return {'evaluation':evaluator.latest(pid,sid)}
        except Exception as exc:fail(exc)

    @router.get('/evaluations/{eid}/selection')
    def get_evaluation_selection(pid:str,eid:str):
        try:return {'selection':evaluator.selection(pid,eid)}
        except Exception as exc:fail(exc)

    @router.put('/evaluations/{eid}/selection')
    def save_evaluation_selection(pid:str,eid:str,body:EvaluationSelectionRequest):
        try:return evaluator.save_selection(pid,eid,**body.model_dump())
        except Exception as exc:fail(exc)

    @router.post('/evaluations/{eid}/derive-set',status_code=201)
    def derive_evaluation_selection(pid:str,eid:str,body:DeriveSelectionRequest):
        try:return evaluator.derive_set(pid,eid,**body.model_dump())
        except Exception as exc:fail(exc)

    @router.get('/evaluations/{eid}')
    def evaluation(pid:str,eid:str):
        try:return evaluator.get(pid,eid)
        except Exception as e:fail(e)

    @router.post('/monitor/compare',status_code=201)
    def compare_monitor(pid:str,body:MonitorCompare):
        try:
            registry._editor(pid)
            return monitor.record(pid,body.reference_profile_id,body.current_profile_id)
        except Exception as e:fail(e)

    @router.get('/monitor/{snapshot_id}')
    def monitor_status(pid:str,snapshot_id:str):
        try:return monitor.get(pid,snapshot_id)
        except Exception as e:fail(e)

    @router.get('/tools')
    def tools(pid:str,asset_id:str,version_id:str='base'):
        try:return agent.tools(pid,asset_id,version_id)
        except Exception as e:fail(e)

    @router.post('/agent/plans',status_code=201)
    def agent_plan(pid:str,body:PlanRequest):
        try:return agent.plan(pid,body)
        except Exception as e:fail(e)

    @router.get('/agent/plans/{plan_id}')
    def agent_plan_get(pid:str,plan_id:str):
        try:return agent.get(pid,plan_id)
        except Exception as e:fail(e)

    @router.post('/agent/plans/{plan_id}/replan',status_code=201)
    def replan(pid:str,plan_id:str,body:ReplanInput):
        try:return agent.replan(pid,plan_id,body.feedback)
        except Exception as e:fail(e)

    @router.post('/agent/plans/{plan_id}/render-receipt')
    def viewer_receipt(pid:str,plan_id:str,body:ViewerRenderReceipt):
        try:return agent.confirm_render(pid,plan_id,**body.model_dump())
        except Exception as e:fail(e)

    @router.post('/agent/plans/{plan_id}/execute')
    def agent_execute(pid:str,plan_id:str):
        try:return agent.execute(pid,plan_id)
        except Exception as e:fail(e)

    return router
