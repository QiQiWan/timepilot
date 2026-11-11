"""Local-only smart import HTTP API: upload -> discover -> transform -> inspect."""
from __future__ import annotations

import asyncio
import csv
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

from timepilot.smart_import import (MAX_UPLOAD_BYTES, SmartImportError, SmartImportService,
                                    TransformRecipe)


def create_smart_import_router(service: SmartImportService, native=None):
    router = APIRouter(prefix='/api/v1/smart-import', tags=['Smart import'])

    def fail(exc: Exception):
        if isinstance(exc, KeyError):
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if isinstance(exc, (SmartImportError, ValueError)):
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        raise exc

    @router.get('/imports')
    def list_imports():
        return service.list_imports()

    @router.post('/uploads', status_code=201)
    async def upload(file: UploadFile = File(...)):
        if not file.filename:
            raise HTTPException(status_code=422, detail='请选择需要导入的数据文件')
        try:
            return await asyncio.to_thread(service.ingest, file.file, file.filename,
                                           max_bytes=MAX_UPLOAD_BYTES)
        except Exception as exc:
            fail(exc)

    @router.post('/uploads/batch', status_code=201)
    async def upload_batch(files: list[UploadFile] = File(...)):
        if not files or any(not item.filename for item in files):
            raise HTTPException(status_code=422, detail='请选择完整的批量文件')
        try:
            return await asyncio.to_thread(service.ingest_many,
                 [(item.file, item.filename) for item in files])
        except Exception as exc:
            fail(exc)

    @router.get('/imports/{import_id}')
    def import_info(import_id: str):
        try: return service.manifest(import_id)
        except Exception as exc: fail(exc)

    @router.get('/imports/{import_id}/regions/{candidate_id}/preview')
    def region_preview(import_id: str, candidate_id: str, limit: int=15):
        try: return service.preview_region(import_id,candidate_id,limit)
        except Exception as exc: fail(exc)

    @router.post('/imports/{import_id}/transform', status_code=201)
    async def transform(import_id: str, recipe: TransformRecipe):
        try:
            return await asyncio.to_thread(service.transform,import_id,recipe)
        except Exception as exc: fail(exc)

    @router.post('/imports/{import_id}/transform-jobs', status_code=202)
    def submit_transform(import_id: str, recipe: TransformRecipe):
        try:
            return service.start_transform(import_id,recipe)
        except Exception as exc:
            fail(exc)

    @router.get('/imports/{import_id}/transform-jobs/{job_id}')
    def transform_status(import_id: str, job_id: str):
        try:
            return service.transform_job(import_id,job_id)
        except Exception as exc:
            fail(exc)

    @router.get('/imports/{import_id}/versions')
    def versions(import_id: str):
        try:
            data=service.manifest(import_id)
            return [service.version(import_id,v) for v in data['versions']]
        except Exception as exc:fail(exc)

    @router.get('/imports/{import_id}/versions/{version_id}')
    def version(import_id: str, version_id: str):
        try:return service.version(import_id,version_id)
        except Exception as exc:fail(exc)

    @router.get('/imports/{import_id}/versions/{version_id}/preview')
    def version_preview(import_id: str, version_id: str, limit: int=50,offset: int=0,
                        scopes: str='',sources: str='',series_id: str=''):
        try:return service.preview_version(import_id,version_id,limit=limit,offset=offset,
                                            scopes=[x for x in scopes.split(',') if x],sources=[x for x in sources.split(',') if x],series_id=series_id)
        except Exception as exc:fail(exc)

    @router.get('/imports/{import_id}/versions/{version_id}/pivot')
    def pivot(import_id: str, version_id: str, group: str='timestamp',scopes: str='',sources: str='',
              series_id: str='',limit: int=80):
        try:return service.basic_pivot(import_id,version_id,group=group,
                      scopes=[x for x in scopes.split(',') if x],sources=[x for x in sources.split(',') if x],series_id=series_id,limit=limit)
        except Exception as exc:fail(exc)

    @router.get('/imports/{import_id}/versions/{version_id}/export.csv')
    def export_csv(import_id: str,version_id: str):
        try:
            path=service.version_file(import_id,version_id)
            return FileResponse(path,media_type='text/csv; charset=utf-8',
                filename=f'timepilot_normalized_{version_id}.csv',headers={'Cache-Control':'no-store'})
        except Exception as exc:fail(exc)

    @router.post('/imports/{import_id}/versions/{version_id}/publish-bi',status_code=201)
    async def publish_bi(import_id: str,version_id: str):
        if native is None:
            raise HTTPException(status_code=503,detail='原生 BI 未就绪，仍可通过基础 BI 查看和导出')
        try:
            path=service.version_file(import_id,version_id)
            meta=service.version(import_id,version_id)
            lineage={'type':'smart_import_v061','import_id':import_id,
                     'version_id':version_id,'source_sha256':meta['source_sha256'],
                     'recipe_sha256':meta['recipe_sha256']}
            # NativeBIService uses trusted internal paths here. No HTTP filesystem paths accepted.
            return await asyncio.to_thread(native.import_from_path,path,meta['name'],lineage)
        except Exception as exc:
            try:
                from timepilot.native_bi import NativeBIDependencyError,NativeBIError
                if isinstance(exc,NativeBIDependencyError):
                    raise HTTPException(status_code=503,detail=str(exc)) from exc
                if isinstance(exc,NativeBIError):
                    raise HTTPException(status_code=422,detail=str(exc)) from exc
            except ImportError:
                pass
            fail(exc)
    return router
