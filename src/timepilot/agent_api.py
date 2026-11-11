"""Unified audited agent endpoints for locally authenticated-only TimePilot."""
from __future__ import annotations

from typing import Literal
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from timepilot.agent_executor import DurableExecutor, JobRequest
from timepilot.agent_tools import SKILL_TOOLS


class Confirmation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    pending_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    confirmed: Literal[True]


def create_agent_router(executor: DurableExecutor) -> APIRouter:
    router = APIRouter(prefix='/api/v1/agent', tags=['Unified skills and durable agents'])

    def guarded(work):
        try: return work()
        except KeyError as exc: raise HTTPException(404, detail=str(exc)) from exc
        except (ValueError, TypeError) as exc: raise HTTPException(422, detail=str(exc)) from exc

    @router.get('/skills')
    def skills():
        tools = executor.registry.public()
        labels = {'data-understanding':'数据结构识别','data-quality':'数据质量诊断',
                  'bi-analysis':'BI 可视化分析','feature-engineering':'时序特征工程',
                  'forecast-preparation':'预测任务准备',
                  'semantic-evaluation':'人工语义评估',
                  'autonomous-modeling':'自主预测实验',
                  'mechanism-learning':'机理辨识'}
        return {'skills':[{'name':name,'display_name':labels[name],'tools':list(names)}
                          for name,names in SKILL_TOOLS.items()],
                'tools':tools,'dynamic_selection':True,'local_only':True,
                'write_tools_require_confirmation':True}

    @router.get('/jobs')
    def jobs(limit: int=25):
        return guarded(lambda: executor.listing(limit))

    @router.post('/jobs', status_code=201)
    def create(payload: JobRequest):
        return guarded(lambda: executor.create(payload))

    @router.get('/jobs/{job_id}')
    def status(job_id: str):
        return guarded(lambda: executor.status(job_id))

    @router.post('/jobs/{job_id}/approve')
    def approve(job_id: str, decision: Confirmation):
        return guarded(lambda: executor.approve(job_id,pending_sha256=decision.pending_sha256,
                                                confirmed=decision.confirmed))

    @router.post('/jobs/{job_id}/cancel')
    def cancel(job_id: str):
        return guarded(lambda: executor.cancel(job_id))

    @router.post('/jobs/{job_id}/retry')
    def retry(job_id: str):
        return guarded(lambda: executor.retry(job_id))

    return router
