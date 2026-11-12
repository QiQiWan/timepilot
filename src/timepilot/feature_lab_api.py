"""Project scoped FeatureLab HTTP workflow. Never exposes raw SQL or paths."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from timepilot.feature_lab import FeatureRecipeInput,FeatureLab
from timepilot.enterprise_security import SecurityError
from timepilot.native_bi import NativeBIDependencyError,NativeBIError

class Review(BaseModel):
    model_config=ConfigDict(extra='forbid')
    reason:str=Field(default='',max_length=500)
    confirm:bool=False


def create_feature_lab_router(projects,native,*,semantic=None,registry=None,jobs=None,models=None):
    features=FeatureLab(projects,native)
    router=APIRouter(prefix='/api/v1/projects/{pid}/feature-lab',tags=['FeatureLab'])
    def fail(exc):
        if isinstance(exc,SecurityError):raise HTTPException(status_code=exc.code,detail=str(exc)) from exc
        if isinstance(exc,KeyError):raise HTTPException(status_code=404,detail=str(exc)) from exc
        if isinstance(exc,NativeBIDependencyError):raise HTTPException(status_code=503,detail=str(exc)) from exc
        if isinstance(exc,(ValueError,NativeBIError)):raise HTTPException(status_code=422,detail=str(exc)) from exc
        raise exc
    @router.get('/recipes')
    def list_recipes(pid:str,asset_id:str|None=None):
        try:return features.list(pid,asset_id)
        except Exception as e:fail(e)
    @router.post('/recipes',status_code=201)
    def create(pid:str,data:FeatureRecipeInput):
        try:return features.create(pid,data)
        except Exception as e:fail(e)
    @router.get('/recipes/{rid}')
    def read(pid:str,rid:str):
        try:return features.get(pid,rid)
        except Exception as e:fail(e)
    @router.post('/recipes/{rid}/preview')
    def preview(pid:str,rid:str,limit:int=50):
        try:return features.preview(pid,rid,limit)
        except Exception as e:fail(e)
    @router.post('/recipes/{rid}/request-review')
    def review(pid:str,rid:str):
        try:return features.request_review(pid,rid)
        except Exception as e:fail(e)
    @router.post('/recipes/{rid}/approve')
    def approve(pid:str,rid:str,body:Review):
        try:return features.approve(pid,rid,body.reason,body.confirm)
        except Exception as e:fail(e)
    @router.post('/recipes/{rid}/publish')
    def publish(pid:str,rid:str):
        try:return features.publish(pid,rid)
        except Exception as e:fail(e)
    if semantic is not None and registry is not None and jobs is not None:
        from timepilot.feature_lab_v2_api import create_feature_lab_v2_router
        router.include_router(create_feature_lab_v2_router(projects,native,semantic,registry,jobs,models))
    return router
