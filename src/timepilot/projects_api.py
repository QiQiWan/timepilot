"""Project-scoped API over existing import, DataAsset, Agent and experiment services."""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict, Field

from timepilot.projects import ProjectCatalog, ProjectCreate, ProjectEdit, ProjectError
from timepilot.data_assets import asset_id_for
from timepilot.generic_assets import GenericAssetStore, PublishGeneral
from timepilot.data_platform import (DataPlatform, SelectionInput, QualityPolicy, QualityDecision, AssetRename)
from timepilot.project_publication import ProjectPublication
from timepilot.data_understanding import UnderstandingRequest, Approval
from timepilot.workbook_intelligence import WorkbookIntelligence
from timepilot.smart_import import SmartImportError
from timepilot.project_publish_batches import ProjectPublishBatches, BatchPublishRequest
from timepilot.import_tool_registry import ImportToolRegistry
from timepilot.import_semantics import build_import_catalog, ImportSemanticAssistant
from timepilot.project_region_catalog import ProjectRegionDirectory
from timepilot.smart_workbench import SaveView, DraftOptions
from timepilot.contracts import TaskSpec
from timepilot.industrial_bi import IndustrialBI, IndustrialSpec, SaveIndustrialView, IndustrialBIError
from timepilot.data import DataValidationError, get_dataset


class RegionLayoutUpdate(BaseModel):
    model_config=ConfigDict(extra='forbid')
    bbox:dict[str,int]|None=None
    header_row:int|None=Field(default=None,ge=1,le=10_000_000)
    confirm:bool=False


class SemanticExplainRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    use_model:bool=False


class ProjectAssetLink(BaseModel):
    model_config = ConfigDict(extra='forbid')
    asset_id: str
    version_id: str | None = None


class ProjectImportLink(BaseModel):
    model_config = ConfigDict(extra='forbid')
    import_id: str


class ProjectRunLink(BaseModel):
    model_config = ConfigDict(extra='forbid')
    run_id: str
    draft_id: str | None = None


class ProjectRunSubmit(BaseModel):
    """Start one model run with a checked, project-scoped BI view lineage."""
    model_config = ConfigDict(extra='forbid')
    task: TaskSpec
    dataset: str
    draft_id: str


def create_projects_router(catalog: ProjectCatalog, understanding, native=None, import_jobs=None, publication_jobs=None):
    inspector=WorkbookIntelligence(catalog.imports)
    generic=GenericAssetStore(catalog.imports)
    platform=DataPlatform(catalog,native,generic)
    publisher=ProjectPublication(catalog,native,platform=platform,generic=generic)
    industrial=IndustrialBI(catalog,native)
    batches=ProjectPublishBatches(catalog,publication_jobs) if publication_jobs is not None else None
    import_tools=ImportToolRegistry(catalog)
    region_directory=ProjectRegionDirectory(catalog)
    import_semantics=ImportSemanticAssistant(import_tools,getattr(understanding,'gateway',None))
    router = APIRouter(prefix='/api/v1/projects',tags=['Projects'])

    def fail(exc):
        if isinstance(exc,KeyError):
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if isinstance(exc,(ProjectError,SmartImportError,DataValidationError,IndustrialBIError,ValueError)):
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        raise exc

    @router.get('')
    def projects():
        return catalog.list()

    @router.post('',status_code=201)
    def create(payload: ProjectCreate):
        try:return catalog.create(payload)
        except Exception as exc:fail(exc)

    @router.get('/{pid}')
    def info(pid:str):
        try:return catalog.project_summary(pid)
        except Exception as exc:fail(exc)

    @router.patch('/{pid}')
    def edit(pid:str,payload:ProjectEdit):
        try:return catalog.edit(pid,payload)
        except Exception as exc:fail(exc)

    @router.delete('/{pid}')
    def delete(pid:str):
        try:return catalog.status(pid,'trash')
        except Exception as exc:fail(exc)

    @router.post('/{pid}/archive')
    def archive(pid:str):
        try:return catalog.status(pid,'archived')
        except Exception as exc:fail(exc)

    @router.post('/{pid}/restore')
    def restore(pid:str):
        try:return catalog.status(pid,'active')
        except Exception as exc:fail(exc)

    @router.get('/{pid}/assets')
    def assets(pid:str):
        try:return catalog.assets_for(pid)
        except Exception as exc:fail(exc)

    @router.post('/{pid}/assets',status_code=201)
    def link_asset(pid:str,payload:ProjectAssetLink):
        try:return catalog.attach_asset(pid,payload.asset_id,payload.version_id)
        except Exception as exc:fail(exc)

    @router.get('/{pid}/assets/{aid}')
    def asset(pid:str,aid:str):
        try:
            link=catalog.require_asset(pid,aid)
            return {'project_id':pid,**catalog.assets.version(aid,link['version_id'])}
        except Exception as exc:fail(exc)

    @router.get('/{pid}/assets/{aid}/versions')
    def versions(pid:str,aid:str):
        try:
            catalog.require_asset(pid,aid)
            return catalog.assets.asset(aid)
        except Exception as exc:fail(exc)

    @router.get('/{pid}/assets/{aid}/views')
    def views(pid:str,aid:str):
        try:
            return catalog.views_for(pid,aid)
        except Exception as exc:fail(exc)

    @router.post('/{pid}/assets/{aid}/views',status_code=201)
    def save_view(pid:str,aid:str,payload:SaveView):
        try:
            return catalog.save_view(pid,aid,payload)
        except Exception as exc:fail(exc)

    @router.get('/{pid}/assets/{aid}/views/{view_id}')
    def get_view(pid:str,aid:str,view_id:str):
        try:return catalog.view(pid,aid,view_id)
        except Exception as exc:fail(exc)

    @router.post('/{pid}/assets/{aid}/views/{view_id}/forecast-drafts',status_code=201)
    async def forecast_from_view(pid:str,aid:str,view_id:str,options:DraftOptions):
        try:
            return await asyncio.to_thread(catalog.create_forecast_draft,pid,aid,view_id,options)
        except Exception as exc:fail(exc)

    @router.get('/{pid}/forecast-drafts')
    def forecast_drafts(pid:str):
        try:return catalog.drafts_for(pid)
        except Exception as exc:fail(exc)

    @router.get('/{pid}/forecast-drafts/{draft_id}')
    def forecast_draft(pid:str,draft_id:str):
        try:return catalog.forecast_draft(pid,draft_id)
        except Exception as exc:fail(exc)

    @router.post('/{pid}/forecast-drafts/{draft_id}/materialize',status_code=201)
    async def materialize(pid:str,draft_id:str):
        try:return await asyncio.to_thread(catalog.materialize_draft,pid,draft_id)
        except Exception as exc:fail(exc)

    @router.get('/{pid}/assets/{aid}/profile')
    async def profile(pid:str,aid:str):
        try:
            link=catalog.require_asset(pid,aid)
            version=catalog.assets.version(aid,link['version_id'])
            # Reuse full-version analytical summary, not another sampled table.
            from timepilot.smart_workbench import ViewSelection
            summary=await asyncio.to_thread(catalog.assets.analyze,aid,link['version_id'],ViewSelection())
            return {'project_id':pid,'asset':version,'profile':summary,
                    'scope':'full','source_sha256':version['source_sha256']}
        except Exception as exc:fail(exc)

    @router.get('/{pid}/imports')
    def imports(pid:str):
        try:return catalog.imports_for(pid)
        except Exception as exc:fail(exc)

    # Async file scan: HTTP only stages and hashes bytes. The project worker
    # performs workbook discovery separately with durable status/retry.
    @router.post('/{pid}/imports/jobs',status_code=202)
    async def stage_upload(pid:str,files:list[UploadFile]=File(...)):
        if import_jobs is None:
            raise HTTPException(503,detail='异步上传服务未就绪')
        try:
            return await asyncio.to_thread(import_jobs.submit,pid,
                    [(f.file,f.filename) for f in files])
        except Exception as exc:fail(exc)

    @router.get('/{pid}/imports/jobs')
    def upload_jobs(pid:str):
        try:return import_jobs.list(pid)
        except Exception as exc:fail(exc)

    @router.get('/{pid}/imports/jobs/{job_id}')
    def upload_job(pid:str,job_id:str):
        try:return import_jobs.get(pid,job_id)
        except Exception as exc:fail(exc)

    @router.get('/{pid}/imports/jobs/{job_id}/checkpoints')
    def upload_checkpoints(pid:str,job_id:str):
        try:return import_jobs.checkpoints(pid,job_id)
        except Exception as exc:fail(exc)

    @router.post('/{pid}/imports/jobs/{job_id}/cancel')
    def cancel_upload(pid:str,job_id:str):
        try:return import_jobs.cancel(pid,job_id)
        except Exception as exc:fail(exc)

    @router.post('/{pid}/imports/jobs/{job_id}/retry')
    def retry_upload(pid:str,job_id:str):
        try:return import_jobs.retry(pid,job_id)
        except Exception as exc:fail(exc)

    @router.post('/{pid}/imports',status_code=201)
    async def upload(pid:str,files:list[UploadFile]=File(...)):
        try:
            catalog.writable(pid)
            if not files or any(not x.filename for x in files):
                raise SmartImportError('请上传完整的数据文件')
            result=await asyncio.to_thread(catalog.imports.ingest_many,
                      [(f.file,f.filename) for f in files])
            catalog.attach_import(pid,result['import_id'])
            return {'project_id':pid,'import_id':result['import_id'],
                    'filename':result['filename'],'file_count':len(result['files']),
                    'candidate_count':len(result['candidates']),
                    'suggested_candidate_ids':result['suggested_candidate_ids'],
                    'status':'parsed'}
        except Exception as exc:fail(exc)

    @router.post('/{pid}/imports/link',status_code=201)
    def link_import(pid:str,payload:ProjectImportLink):
        try:return catalog.attach_import(pid,payload.import_id)
        except Exception as exc:fail(exc)

    @router.get('/{pid}/imports/{iid}')
    def import_info(pid:str,iid:str):
        try:
            item=catalog.require_import(pid,iid)
            # Do not return raw sample_rows to import summary clients.
            return {'project_id':pid,'import_id':iid,'filename':item['filename'],
                'created_at':item['created_at'],'file_count':len(item['files']),
                'file_summaries':[{k:x.get(k) for k in ('file_id','filename','bytes','sheet_count','warning')}
                                  for x in item['files']],
                'candidate_count':len(item['candidates']),
                'candidates':[{k:c.get(k) for k in ('candidate_id','sheet','logical_file','kind','header_row',
                    'columns','source_columns','source_fields','bbox','row_start','row_end','data_start_row',
                    'rows_estimate','suggested','header_confidence','superseded','needs_review')}
                    for c in item['candidates']],
                'suggested_candidate_ids':item['suggested_candidate_ids'],
                'versions':item['versions'],'status':'parsed'}
        except Exception as exc:fail(exc)

    @router.get('/{pid}/imports/{iid}/catalog')
    def import_catalog(pid:str,iid:str):
        try:
            manifest=catalog.require_import(pid,iid)
            indexed=region_directory.sync(pid,manifest)
            return {**build_import_catalog(manifest),'directory':indexed}
        except Exception as exc:fail(exc)

    @router.get('/{pid}/imports/{iid}/regions/{rid}/preview')
    async def import_region_preview(pid:str,iid:str,rid:str,limit:int=12):
        try:
            catalog.require_import(pid,iid)
            return await asyncio.to_thread(catalog.imports.preview_region,iid,rid,min(max(limit,1),30))
        except Exception as exc:fail(exc)

    @router.patch('/{pid}/imports/{iid}/regions/{rid}/layout')
    async def revise_import_region(pid:str,iid:str,rid:str,request:RegionLayoutUpdate):
        try:
            catalog.writable(pid)
            catalog.require_import(pid,iid)
            if not request.confirm:
                raise SmartImportError('请确认需要修改的原始数据区域及表头')
            candidate=await asyncio.to_thread(catalog.imports.revise_region,iid,rid,
                                               bbox=request.bbox,header_row=request.header_row)
            return {'import_id':iid,'region_id':candidate['candidate_id'],
                    'supersedes_candidate_id':rid,'bbox':candidate['bbox'],
                    'header_row':candidate['header_row'],'source_fields':candidate['source_fields']}
        except Exception as exc:fail(exc)

    @router.get('/{pid}/imports/{iid}/tools')
    def import_tools_available(pid:str,iid:str):
        try:
            catalog.require_import(pid,iid)
            return {'import_id':iid,'tools':import_tools.names()}
        except Exception as exc:fail(exc)

    @router.post('/{pid}/imports/{iid}/semantic-explain')
    async def explain_import(pid:str,iid:str,request:SemanticExplainRequest):
        try:
            catalog.require_import(pid,iid)
            return await asyncio.to_thread(import_semantics.explain,pid,iid,use_model=request.use_model)
        except Exception as exc:fail(exc)

    @router.post('/{pid}/imports/{iid}/publish-batch',status_code=202)
    def submit_batch(pid:str,iid:str,request:BatchPublishRequest):
        if batches is None:raise HTTPException(503,detail='后台发布服务未就绪')
        try:
            region_directory.sync(pid,catalog.require_import(pid,iid))
            return batches.submit(pid,iid,request)
        except Exception as exc:fail(exc)

    @router.get('/{pid}/imports/{iid}/publish-batches')
    def list_batches(pid:str,iid:str):
        if batches is None:raise HTTPException(503,detail='后台发布服务未就绪')
        try:
            catalog.require_import(pid,iid)
            return {'batches':[x for x in batches.list(pid) if x['import_id']==iid]}
        except Exception as exc:fail(exc)

    @router.get('/{pid}/publish-batches/{batch_id}')
    def get_batch(pid:str,batch_id:str):
        if batches is None:raise HTTPException(503,detail='后台发布服务未就绪')
        try:return batches.get(pid,batch_id)
        except Exception as exc:fail(exc)

    @router.post('/{pid}/publish-batches/{batch_id}/retry')
    def retry_batch(pid:str,batch_id:str):
        if batches is None:raise HTTPException(503,detail='后台发布服务未就绪')
        try:return batches.retry(pid,batch_id)
        except Exception as exc:fail(exc)

    @router.get('/{pid}/imports/{iid}/profile')
    def import_profile(pid:str,iid:str):
        try:
            item=catalog.require_import(pid,iid)
            files=item['files'];candidates=item['candidates']
            kinds={}
            for c in candidates:kinds[c['kind']]=kinds.get(c['kind'],0)+1
            cols=set()
            for c in candidates:cols.update(c['columns'])
            return {'project_id':pid,'import_id':iid,'scope':'structure_sample',
                    'file_count':len(files),'candidate_count':len(candidates),
                    'structure_counts':kinds,'unique_field_names':len(cols),
                    'years':sorted({int(c['year']) for c in candidates if isinstance(c.get('year'),int)}),
                    'warnings':[f['warning'] for f in files if f.get('warning')],
                    'full_stats_ready':bool(item['versions']),
                    'requires_review':any(not x['suggested'] for x in candidates)}
        except Exception as exc:fail(exc)

    @router.get('/{pid}/imports/{iid}/layout')
    async def layout(pid:str,iid:str):
        try:
            catalog.require_import(pid,iid)
            return await asyncio.to_thread(inspector.inspect,iid)
        except Exception as exc:fail(exc)

    @router.get('/{pid}/imports/{iid}/regions/{candidate_id}/schema')
    async def region_schema(pid: str, iid: str, candidate_id: str,
                            header_row: int | None = None):
        """Source-backed, role-unconfirmed schema; safe for a read-only Agent."""
        try:
            catalog.require_import(pid,iid)
            return await asyncio.to_thread(generic.inspect,iid,candidate_id,header_row=header_row)
        except Exception as exc:fail(exc)

    @router.get('/{pid}/imports/{iid}/regions/{candidate_id}/versions')
    def generic_versions(pid: str, iid: str, candidate_id: str):
        try:
            catalog.require_import(pid,iid)
            return generic.list_versions(iid,candidate_id)
        except Exception as exc:fail(exc)

    @router.post('/{pid}/imports/{iid}/regions/{candidate_id}/publish-general',status_code=201)
    async def publish_general(pid: str, iid: str, candidate_id: str, request: PublishGeneral):
        """Confirm actual source schema and publish ALL columns to Perspective BI.

        Ingestion and BI registration are independent. A failed BI import can
        be retried with exactly the same source/version without reconversion.
        """
        try:
            return await asyncio.to_thread(publisher.publish,pid,iid,candidate_id,request)
        except Exception as exc:fail(exc)

    @router.post('/{pid}/imports/{iid}/regions/{candidate_id}/publish-jobs',status_code=202)
    def enqueue_publication(pid:str,iid:str,candidate_id:str,request:PublishGeneral):
        if publication_jobs is None:
            raise HTTPException(503,detail='后台数据发布服务未启用')
        try:return publication_jobs.submit(pid,iid,candidate_id,request)
        except Exception as exc:fail(exc)

    @router.get('/{pid}/publish-jobs')
    def publication_list(pid:str):
        try:return {'jobs':publication_jobs.list(pid)}
        except Exception as exc:fail(exc)

    @router.get('/{pid}/publish-jobs/{jid}')
    def publication_get(pid:str,jid:str):
        try:return publication_jobs.get(pid,jid)
        except Exception as exc:fail(exc)

    @router.get('/{pid}/publish-jobs/{jid}/checkpoint')
    def publication_checkpoint(pid:str,jid:str):
        try:return publication_jobs.checkpoint(pid,jid)
        except Exception as exc:fail(exc)

    @router.post('/{pid}/publish-jobs/{jid}/retry')
    def publication_retry(pid:str,jid:str):
        try:return publication_jobs.retry(pid,jid)
        except Exception as exc:fail(exc)

    @router.get('/{pid}/industrial/assets/{aid}/suggestions')
    def industrial_suggestions(pid:str,aid:str):
        try:return industrial.suggestions(pid,aid)
        except Exception as exc:fail(exc)

    @router.post('/{pid}/industrial/preview')
    def industrial_preview(pid:str,payload:IndustrialSpec):
        try:return industrial.preview(pid,payload)
        except Exception as exc:fail(exc)

    @router.post('/{pid}/industrial/arrow')
    def industrial_arrow(pid:str,payload:IndustrialSpec):
        from fastapi.responses import Response
        try:
            content=industrial.preview(pid,payload,arrow=True)
            return Response(content=content,media_type='application/vnd.apache.arrow.stream',
                            headers={'Cache-Control':'no-store','X-TimePilot-Version':payload.version_id})
        except Exception as exc:fail(exc)

    @router.get('/{pid}/industrial/views')
    def industrial_views(pid:str):
        try:return industrial.list(pid)
        except Exception as exc:fail(exc)

    @router.post('/{pid}/industrial/views',status_code=201)
    def save_industrial_view(pid:str,payload:SaveIndustrialView):
        try:return industrial.save(pid,payload)
        except Exception as exc:fail(exc)

    @router.get('/{pid}/industrial/views/{vid}')
    def industrial_view(pid:str,vid:str):
        try:return industrial.get(pid,vid)
        except Exception as exc:fail(exc)

    @router.get('/{pid}/data-catalog')
    def data_catalog(pid: str):
        """All project assets; BI copies are references, not duplicate user assets."""
        try:
            catalog.get(pid)
            standard=[{'kind':'forecast_series','asset_id':x['asset_id'],
                       'name':x.get('name'),'version_id':x.get('version_id'),
                       'row_count':x.get('row_count'),'source':'specialized'}
                      for x in catalog.assets_for(pid)]
            general=[]
            for item in platform.list_assets(pid):
                if item.get('missing'):
                    general.append(item)
                    continue
                link=catalog.require_generic(pid,item['native_asset_id'])
                meta=generic.version(link['import_id'],link['candidate_id'],link['version_id'])
                general.append({**item,'import_id':link['import_id'],
                                'candidate_id':link['candidate_id'],
                                'metric_candidates':sum(bool(f.get('metric_candidate')) for f in meta['columns']),
                                'confirmed_roles':meta['confirmed_roles']})
            return {'project_id':pid,'assets':standard+general,
                    'count':len(standard)+len(general)}
        except Exception as exc:fail(exc)

    @router.get('/{pid}/catalog/assets')
    def unified_assets(pid:str):
        try:return {'project_id':pid,'assets':platform.list_assets(pid)}
        except Exception as exc:fail(exc)

    @router.get('/{pid}/catalog/assets/{aid}/versions')
    def unified_versions(pid:str,aid:str):
        try:return {'asset_id':aid,'versions':platform.versions(pid,aid)}
        except Exception as exc:fail(exc)

    @router.get('/{pid}/catalog/assets/{aid}/compare')
    def version_compare(pid:str,aid:str,left:str,right:str):
        try:return platform.diff_versions(pid,aid,left,right)
        except Exception as exc:fail(exc)

    @router.patch('/{pid}/catalog/assets/{aid}')
    def unified_rename(pid:str,aid:str,request:AssetRename):
        try:return platform.rename(pid,aid,request.name)
        except Exception as exc:fail(exc)

    @router.get('/{pid}/catalog/assets/{aid}/schema')
    def unified_schema(pid:str,aid:str,version_id:str|None=None):
        try:return platform.schema(pid,aid,version_id)
        except Exception as exc:fail(exc)

    @router.post('/{pid}/selections/preview')
    def preview_selection(pid:str,request:SelectionInput):
        try:return platform.create_selection(pid,request,save=False)
        except Exception as exc:fail(exc)

    @router.post('/{pid}/selections',status_code=201)
    def freeze_selection(pid:str,request:SelectionInput):
        try:return platform.create_selection(pid,request,save=True)
        except Exception as exc:fail(exc)

    @router.get('/{pid}/selections/{sid}')
    def selection_detail(pid:str,sid:str):
        try:return platform.get_selection(pid,sid)
        except Exception as exc:fail(exc)

    @router.post('/{pid}/catalog/assets/{aid}/quality/policy',status_code=201)
    def quality_policy(pid:str,aid:str,request:QualityPolicy):
        try:return platform.save_quality_policy(pid,aid,request)
        except Exception as exc:fail(exc)

    @router.post('/{pid}/catalog/assets/{aid}/quality/evaluate',status_code=201)
    async def quality_evaluate(pid:str,aid:str,version_id:str|None=None):
        try:return await asyncio.to_thread(platform.evaluate_quality,pid,aid,version_id)
        except Exception as exc:fail(exc)

    @router.get('/{pid}/catalog/assets/{aid}/quality')
    def quality_report(pid:str,aid:str):
        try:
            platform._resolve(pid,aid)
            return platform.latest_quality(pid,aid) or {'asset_id':aid,'status':'unchecked','issues':[]}
        except Exception as exc:fail(exc)

    @router.get('/{pid}/quality/issues')
    def quality_issues(pid:str,run_id:str|None=None):
        try:return {'issues':platform.issues(pid,run_id)}
        except Exception as exc:fail(exc)

    @router.post('/{pid}/quality/issues/{iid}/decision')
    def quality_decision(pid:str,iid:str,request:QualityDecision):
        try:return platform.decide_issue(pid,iid,request)
        except Exception as exc:fail(exc)

    @router.get('/{pid}/catalog/assets/{aid}/impact')
    def impact(pid:str,aid:str):
        try:return platform.impact(pid,aid)
        except Exception as exc:fail(exc)

    @router.get('/{pid}/catalog/assets/{aid}/integrity')
    async def integrity(pid:str,aid:str):
        try:return await asyncio.to_thread(platform.storage_scan,pid,aid)
        except Exception as exc:fail(exc)

    @router.get('/{pid}/governance/audit')
    def governance_audit(pid:str,limit:int=30):
        try:return {'events':platform.audits(pid,limit)}
        except Exception as exc:fail(exc)

    @router.get('/{pid}/generic-assets')
    def generic_assets(pid: str):
        try:
            items=[]
            for item in catalog.generic_for(pid):
                try:
                    meta=generic.version(item['import_id'],item['candidate_id'],item['version_id'])
                    # Native metadata may be temporarily unavailable, but
                    # provenance is still visible for recovery.
                    items.append({**item,'name':meta['name'],'row_count':meta['row_count'],
                                  'column_count':meta['column_count'],
                                  'source_file':meta['source_file'],
                                  'source_sheet':meta['source_sheet'],
                                  'confirmed_roles':meta['confirmed_roles'],
                                  'data_sha256':meta['data_sha256']})
                except (OSError,KeyError,ValueError):
                    items.append({**item,'missing':True})
            return items
        except Exception as exc:fail(exc)

    @router.get('/{pid}/generic-assets/{native_asset_id}')
    def generic_asset(pid: str, native_asset_id: str):
        try:
            item=catalog.require_generic(pid,native_asset_id)
            return {'project_id':pid,'native_asset_id':native_asset_id,
                    'meta':generic.version(item['import_id'],item['candidate_id'],item['version_id'])}
        except Exception as exc:fail(exc)

    @router.get('/{pid}/imports/{iid}/regions/{candidate_id}/profile')
    async def region_profile(pid:str,iid:str,candidate_id:str,max_rows:int=100000):
        try:
            catalog.require_import(pid,iid)
            return await asyncio.to_thread(inspector.profile_region,iid,candidate_id,max_rows=max_rows)
        except Exception as exc:fail(exc)

    @router.get('/{pid}/imports/{iid}/plans')
    def plans(pid:str,iid:str):
        try:
            catalog.require_import(pid,iid)
            return understanding.plans(iid)
        except Exception as exc:fail(exc)

    @router.post('/{pid}/imports/{iid}/plans',status_code=201)
    async def plan(pid:str,iid:str,payload:UnderstandingRequest):
        try:
            catalog.writable(pid)
            catalog.require_import(pid,iid)
            return await asyncio.to_thread(understanding.propose,iid,payload)
        except Exception as exc:fail(exc)

    @router.get('/{pid}/imports/{iid}/plans/{plan_id}')
    def get_plan(pid:str,iid:str,plan_id:str):
        try:
            catalog.require_import(pid,iid)
            return understanding.plan(iid,plan_id)
        except Exception as exc:fail(exc)

    @router.post('/{pid}/imports/{iid}/plans/{plan_id}/execute',status_code=202)
    async def execute(pid:str,iid:str,plan_id:str,payload:Approval):
        try:
            catalog.writable(pid)
            catalog.require_import(pid,iid)
            return await asyncio.to_thread(understanding.execute,iid,plan_id,payload)
        except Exception as exc:fail(exc)

    @router.get('/{pid}/imports/{iid}/plans/{plan_id}/execution')
    def execution(pid:str,iid:str,plan_id:str):
        try:
            catalog.require_import(pid,iid)
            response=understanding.execution(iid,plan_id)
            if response.get('status')=='completed' and response.get('version_id'):
                catalog.attach_asset(pid,asset_id_for(iid),response['version_id'])
            return response
        except Exception as exc:fail(exc)

    @router.post('/{pid}/imports/{iid}/versions/{version_id}/assets',status_code=201)
    def publish(pid:str,iid:str,version_id:str):
        try:
            catalog.require_import(pid,iid)
            return catalog.attach_asset(pid,asset_id_for(iid),version_id)
        except Exception as exc:fail(exc)

    @router.post('/{pid}/assets/{aid}/editable',status_code=201)
    async def editable(pid:str,aid:str):
        try:
            if native is None:
                raise ProjectError('原生 BI 服务未配置')
            catalog.writable(pid)
            link=catalog.require_asset(pid,aid)
            import_id,_,_=catalog.assets._resolve(aid,link['version_id'])
            path=catalog.imports.version_file(import_id,link['version_id'])
            meta=catalog.imports.version(import_id,link['version_id'])
            lineage={'type':'project_editable_snapshot','project_id':pid,
                     'asset_id':aid,'version_id':link['version_id'],
                     'source_sha256':meta['source_sha256']}
            result=await asyncio.to_thread(native.import_from_path,path,meta['name']+' / 可编辑',lineage)
            catalog.attach_native(pid,result['asset_id'],aid)
            return {'project_id':pid,'asset_id':result['asset_id'],'source_asset_id':aid,
                    'copied_explicitly':True,'version_id':link['version_id']}
        except Exception as exc:fail(exc)

    @router.get('/{pid}/native-assets')
    def editable_list(pid:str):
        try:
            output=[]
            for x in catalog.native_for(pid):
                try:output.append({**x,'asset':native.asset(x['native_asset_id'])})
                except (KeyError,ValueError):output.append({**x,'missing':True})
            return output
        except Exception as exc:fail(exc)

    @router.get('/{pid}/native-assets/{aid}')
    def editable_info(pid:str,aid:str):
        try:
            # Native BI routes are shared by editable snapshots and immutable
            # generic tables. Both must be linked to the requesting project.
            try:
                catalog.require_native(pid,aid)
            except KeyError:
                catalog.require_generic(pid,aid)
            return native.asset(aid)
        except Exception as exc:fail(exc)

    @router.get('/{pid}/models')
    def models(pid:str):
        try:return catalog.models(pid)
        except Exception as exc:fail(exc)

    @router.get('/{pid}/tasks')
    def tasks(pid:str):
        try:
            return {'runs':catalog.runs(pid),'imports':catalog.imports_for(pid),
                    'upload_jobs':import_jobs.list(pid) if import_jobs else [],
                    'forecast_drafts':catalog.drafts_for(pid)}
        except Exception as exc:fail(exc)

    @router.post('/{pid}/runs',status_code=201)
    def link_run(pid:str,payload:ProjectRunLink):
        try:return catalog.attach_run(pid,payload.run_id,draft_id=payload.draft_id)
        except Exception as exc:fail(exc)

    @router.post('/{pid}/runs/submit',status_code=201)
    def submit_forecast_from_view(pid:str,payload:ProjectRunSubmit):
        """Validate the precise approved draft *before* enqueuing a run.

        Unlike the backwards-compatible two-request run/link flow, dataset,
        model and evaluation parameters are all checked against the pinned
        saved view and the preflight record in this one request.
        """
        try:
            catalog.writable(pid)
            draft=catalog.forecast_draft(pid,payload.draft_id)
            expected=draft['options']
            if draft.get('dataset_id') != payload.dataset:
                raise ProjectError('建模数据集与已保存 BI 预测草稿不一致')
            if draft.get('status') != 'dataset_ready':
                raise ProjectError('请先完成预测草稿的适用性检查和训练数据生成')
            task=payload.task
            if (task.task_family!='demand_forecast' or
                task.forecast_horizon!=expected['forecast_horizon'] or
                task.evaluation.test_horizon!=expected['forecast_horizon'] or
                task.evaluation.validation_windows!=expected['validation_windows'] or
                task.evaluation.min_history!=expected['min_history'] or
                set(task.model_candidates)!=set(expected['model_candidates'])):
                raise ProjectError('预测跨度、模型或回测参数与保存的草稿不一致；请在 BI 中重新创建草稿')
            if not task.approved:
                raise ProjectError('建模任务需要人工确认后提交')
            if catalog.engine is None:
                raise ProjectError('当前未配置模型运行引擎')
            manifest,frame=get_dataset(catalog.engine.store,catalog.engine.settings,payload.dataset)
            catalog.engine._validate(task,frame,manifest)
            run_id=catalog.engine.queue(task,payload.dataset)
            catalog.attach_run(pid,run_id,draft_id=payload.draft_id)
            return {'project_id':pid,'run_id':run_id,'status':'queued','draft_id':payload.draft_id,
                    'view_id':draft['view_id'],'asset_id':draft['asset_id'],'version_id':draft['version_id']}
        except Exception as exc:fail(exc)

    return router
