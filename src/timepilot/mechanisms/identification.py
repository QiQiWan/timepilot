"""Audited first-order grey-box identification using measured state/control pairs.

Requires known sampling, units and event locations. No equipment verification is
inferred from an uploaded array. Uses chronological holdout and tests the data
against persistence; does not silently treat unrelated sales data as physics.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from pathlib import Path
from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator


def _json(v):
    return json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',', ':'))


def _digest(v):
    return hashlib.sha256(_json(v).encode()).hexdigest()


class MechanismEvidence(BaseModel):
    model_config=ConfigDict(extra='forbid')
    kind: Literal['simulation','measured_equipment']
    reference: str=Field(min_length=3,max_length=250)
    sample_interval_seconds: float=Field(gt=0,le=1e8)
    state_unit: str=Field(min_length=1,max_length=40)
    control_unit: str=Field(min_length=1,max_length=40)
    state: list[float]=Field(min_length=40,max_length=20000)
    control: list[float]=Field(min_length=40,max_length=20000)
    maintenance_indices: list[int]=Field(default_factory=list,max_length=1000)
    source_asset_id: str | None=Field(default=None,max_length=120)
    source_version_id: str | None=Field(default=None,max_length=64)
    researcher_note: str=Field(default='',max_length=500)

    @model_validator(mode='after')
    def check(self):
        if len(self.control)!=len(self.state):
            raise ValueError('State and control samples must have identical length')
        if any(not math.isfinite(x) for x in (*self.state,*self.control)):
            raise ValueError('Non-finite mechanism measurements are unsupported')
        if len(set(self.maintenance_indices))!=len(self.maintenance_indices) or any(
                not 1 < idx < len(self.state)-1 for idx in self.maintenance_indices):
            raise ValueError('Invalid or repeated maintenance boundary')
        if bool(self.source_asset_id)!=bool(self.source_version_id):
            raise ValueError('Asset references require an ID and version together')
        return self


class MechanismStudy:
    def __init__(self, home: Path):
        self.root=Path(home)/'research_mechanisms'
        self.root.mkdir(parents=True,exist_ok=True)

    def register(self, evidence: MechanismEvidence | dict) -> dict:
        e=MechanismEvidence.model_validate(evidence)
        payload=e.model_dump(mode='json')
        digest=_digest(payload)
        path=self.root/(digest+'.json')
        if not path.exists():
            fd,tmp=tempfile.mkstemp(prefix='.evidence-',dir=self.root)
            try:
                with os.fdopen(fd,'w',encoding='utf-8') as stream:
                    stream.write(_json(payload)+'\n')
                if not path.exists():
                    os.rename(tmp,path)
            finally:
                if os.path.exists(tmp):os.unlink(tmp)
        return {'evidence_id':digest,'data_kind':e.kind,'n_samples':len(e.state),
                'evidence_sha256':digest,'engineer_verified':False,
                'note':'Declaration of measured_equipment is not independent equipment validation'}

    def load(self, evidence_id: str) -> MechanismEvidence:
        if len(evidence_id)!=64 or any(c not in '0123456789abcdef' for c in evidence_id):
            raise ValueError('Invalid mechanism evidence ID')
        raw=json.loads((self.root/(evidence_id+'.json')).read_text(encoding='utf-8'))
        if _digest(raw)!=evidence_id:
            raise ValueError('Mechanism evidence SHA256 mismatch')
        return MechanismEvidence.model_validate(raw)

    @staticmethod
    def _estimate(state: np.ndarray, control: np.ndarray, transitions: np.ndarray):
        predictors=np.column_stack([state[transitions],control[transitions],
                                    np.ones(len(transitions))])
        if len(predictors)<15:
            raise ValueError('At least fifteen usable transitions are required')
        condition=float(np.linalg.cond(predictors))
        if not math.isfinite(condition) or condition>1e8:
            raise ValueError('Parameters are not identifiable: insufficient independent control excitation')
        beta=np.linalg.lstsq(predictors,state[transitions+1],rcond=None)[0]
        return beta,condition

    def assess(self, evidence_id: str) -> dict:
        e=self.load(evidence_id)
        x=np.asarray(e.state,dtype=float)
        u=np.asarray(e.control,dtype=float)
        n=len(x)
        boundary=set(e.maintenance_indices)
        cutoff=int(n*0.8)
        usable=np.array([i for i in range(n-1) if (i+1) not in boundary],dtype=int)
        train=usable[usable+1<cutoff]
        test=usable[usable>=cutoff]
        if len(test)<5:
            raise ValueError('Insufficient independent holdout transitions')
        theta,condition=self._estimate(x,u,train)
        design=np.column_stack([x[test],u[test],np.ones(len(test))])
        predicted=design@theta
        baseline=x[test]
        actual=x[test+1]
        error=float(np.sqrt(np.mean((predicted-actual)**2)))
        baseline_error=float(np.sqrt(np.mean((baseline-actual)**2)))
        stable=abs(float(theta[0]))<=1.0
        useful=error<baseline_error*0.95 if baseline_error>0 else False
        event_count=len(boundary)
        # A single global linear model cannot establish genuine regime switching.
        report={'evidence_id':evidence_id,'source_kind':e.kind,'reference':e.reference,
                'source_asset_id':e.source_asset_id,'source_version_id':e.source_version_id,
                'hypothesis':'first_order_linear_state_control',
                'parameters':{'decay':float(theta[0]),'gain':float(theta[1]),'bias':float(theta[2])},
                'holdout':{'transitions':len(test),'rmse':error,'persistence_rmse':baseline_error,
                           'relative_improvement':(1-error/baseline_error) if baseline_error>0 else None},
                'identifiability':{'design_condition_number':condition,'full_rank':True},
                'events':{'known_maintenance_boundaries':event_count,
                          'transition_pairs_crossing_events_excluded':True},
                'physical_checks':{'discrete_decay_stable':stable,
                                   'state_unit':e.state_unit,'control_unit':e.control_unit,
                                   'sample_interval_seconds':e.sample_interval_seconds},
                'candidate_status':'review_candidate' if useful and stable else 'not_supported_by_holdout',
                'verified_on_real_equipment':False,
                'requires_engineer_approval':True,
                'not_validated_for':'unobserved_controls_or_other_equipment_regimes',
                'auto_deployed':False}
        return report
