"""Local-only research endpoints for human gold review and audited experiments."""
from __future__ import annotations

from typing import Literal
from fastapi.responses import Response
from fastapi import APIRouter, HTTPException, File, UploadFile
from pydantic import BaseModel, ConfigDict, Field

from timepilot.auto_experiment import AutonomousExperiment, ExperimentSpec, _digest
from timepilot.mechanisms.identification import MechanismEvidence, MechanismStudy
from timepilot.semantic_benchmark import Annotation, SemanticBenchmark


class BenchmarkRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    use_model: bool=False
    max_cases: int=Field(default=30,ge=1,le=200)
    input_usd_per_million: float | None=Field(default=None,ge=0,le=1000)
    output_usd_per_million: float | None=Field(default=None,ge=0,le=1000)


class StartTrial(BaseModel):
    model_config=ConfigDict(extra='forbid')
    reviewer: str=Field(min_length=2,max_length=100)
    arm: Literal['rules','model']
    protocol_id: str | None = Field(default=None,pattern=r'^[0-9a-f]{64}$')


class FinishTrial(BaseModel):
    model_config=ConfigDict(extra='forbid')
    reviewer: str=Field(min_length=2,max_length=100)
    corrected: dict



class CreateTrialProtocol(BaseModel):
    model_config=ConfigDict(extra='forbid')
    reviewers: list[str]=Field(min_length=2,max_length=2)
    seed: int=Field(default=42,ge=0,le=2**31-1)
    max_cases: int=Field(default=50,ge=1,le=200)

class ConfirmExperiment(BaseModel):
    model_config=ConfigDict(extra='forbid')
    spec: ExperimentSpec
    plan_sha256: str=Field(pattern=r'^[0-9a-f]{64}$')
    confirmed: Literal[True]


def create_research_router(benchmark: SemanticBenchmark, experiments: AutonomousExperiment,
                           mechanisms: MechanismStudy) -> APIRouter:
    router=APIRouter(prefix='/api/v1/research',tags=['Real-data evaluation and autonomous modeling'])

    def guarded(action):
        try:
            return action()
        except KeyError as exc:
            raise HTTPException(404, detail=str(exc)) from exc
        except (FileNotFoundError,ValueError,TypeError,RuntimeError,TimeoutError) as exc:
            raise HTTPException(422,detail=str(exc)) from exc

    @router.post('/semantic/seed/{import_id}',status_code=201)
    def seed(import_id: str, max_cases: int=200):
        return guarded(lambda: benchmark.seed(import_id,max_cases=max(1,min(max_cases,300))))

    @router.get('/semantic/cases')
    def cases(status: Literal['all','pending','confirmed']='all',limit: int=200):
        return guarded(lambda: benchmark.cases(status=status,limit=limit))

    @router.get('/semantic/review-template.csv')
    def review_template(limit: int=200):
        csv_text=guarded(lambda: benchmark.export_review_template(limit=max(1,min(limit,200))))
        return Response(content='\ufeff'+csv_text,media_type='text/csv; charset=utf-8',
                        headers={'Content-Disposition':'attachment; filename="timepilot_manual_review.csv"'})

    @router.post('/semantic/review-import',status_code=201)
    async def import_reviews(file: UploadFile=File(...)):
        raw=await file.read(2_000_001)
        if len(raw)>2_000_000:
            raise HTTPException(413,detail='Human labels CSV limit is 2 MB')
        try:
            content=raw.decode('utf-8-sig')
        except UnicodeDecodeError as exc:
            raise HTTPException(422,detail='Human label CSV must use UTF-8') from exc
        return guarded(lambda: benchmark.import_human_reviews(content))

    @router.post('/semantic/cases/{case_id}/review',status_code=201)
    def review(case_id: str, payload: Annotation):
        return guarded(lambda: benchmark.review(case_id,payload))

    @router.post('/semantic/protocols',status_code=201)
    def create_protocol(payload: CreateTrialProtocol):
        return guarded(lambda: benchmark.create_protocol(**payload.model_dump()))

    @router.get('/semantic/protocols/{protocol_id}')
    def get_protocol(protocol_id: str):
        return guarded(lambda: benchmark.protocol(protocol_id))

    @router.get('/semantic/protocols/{protocol_id}/results')
    def get_protocol_results(protocol_id: str, input_usd_per_million: float | None = None,
                             output_usd_per_million: float | None = None):
        return guarded(lambda: benchmark.protocol_summary(protocol_id,
                       input_usd_per_million=input_usd_per_million,
                       output_usd_per_million=output_usd_per_million))

    @router.post('/semantic/cases/{case_id}/trial',status_code=201)
    def start_trial(case_id: str, payload: StartTrial):
        return guarded(lambda: benchmark.start_trial(case_id,**payload.model_dump()))

    @router.post('/semantic/trials/{trial_id}/finish')
    def finish_trial(trial_id: str, payload: FinishTrial):
        return guarded(lambda: benchmark.finish_trial(trial_id,**payload.model_dump()))

    @router.get('/semantic/trials/summary')
    def trial_summary(limit: int=200):
        return guarded(lambda: benchmark.trial_summary(limit=limit))

    @router.post('/semantic/evaluate')
    def evaluate(payload: BenchmarkRequest):
        return guarded(lambda: benchmark.evaluate(**payload.model_dump()))

    @router.get('/semantic/reports')
    def reports(limit: int=20):
        return guarded(lambda: benchmark.reports(limit=limit))

    @router.post('/experiments/plan')
    def plan(spec: ExperimentSpec):
        def action():
            proposal=experiments.plan(spec)
            return {**proposal, 'plan_sha256':_digest(proposal),
                    'next_step':'确认模型候选、滚动窗口与数据来源后明确提交实验'}
        return guarded(action)

    @router.post('/experiments/execute',status_code=201)
    def execute(payload: ConfirmExperiment):
        def action():
            current=experiments.plan(payload.spec)
            if _digest(current)!=payload.plan_sha256:
                raise ValueError('实验数据、来源或参数与已确认方案不同，拒绝执行')
            return experiments.run(payload.spec)
        return guarded(action)

    @router.get('/experiments')
    def listing(limit: int=25):
        return guarded(lambda: experiments.experiments(limit=limit))

    @router.get('/experiments/{experiment_id}/next-plan')
    def next_plan(experiment_id: str):
        return guarded(lambda: experiments.next_plan(experiment_id))

    @router.get('/experiments/{experiment_id}')
    def experiment(experiment_id: str):
        return guarded(lambda: experiments.report(experiment_id))

    @router.post('/mechanisms/evidence',status_code=201)
    def evidence(payload: MechanismEvidence):
        return guarded(lambda: mechanisms.register(payload))

    @router.post('/mechanisms/{evidence_id}/assess')
    def assess(evidence_id: str):
        return guarded(lambda: mechanisms.assess(evidence_id))

    return router
