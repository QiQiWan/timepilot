"""v0.25.7 regressions for seven independently reproduced defects."""
from __future__ import annotations
import json
from pathlib import Path
from uuid import uuid4
import numpy as np
import pytest
from pydantic import ValidationError
from test_conversational_bi_v0231 import _env
from test_featurelab2_contract_registry_v0247 import _spec
from timepilot.feature_contracts import (DataContract, FeatureSpecV2, PlanRequest,
     TaskSpecV2, digest)
from timepilot.feature_evaluation import _metrics
from timepilot.feature_agent import FeatureAgent
from timepilot.feature_tool_manifest import manifest
from timepilot.feature_viewer_state import FeatureViewerState
from timepilot.projects import now
from timepilot.storage import sha256_file


def test_failed_or_cancelled_or_corrupt_artifact_never_publishes(tmp_path):
    app, c, pid, aid, _ = _env(tmp_path)
    try:
        reg=app.state.feature_registry
        contract=app.state.feature_semantic.latest(pid,aid,'base')
        rec=reg.create(pid,FeatureSpecV2.model_validate(_spec(contract)),'gate-create')
        sid=rec['set_id']
        job=app.state.feature_jobs.create_run(pid,sid,'gate-job')
        rid=job['job_id']
        # A real file is necessary to test the status predicate independently.
        path=app.state.projects.home/'feature_lab'/pid/'artifacts'/'gate'/'data.parquet'
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(b'PAR1' + b'not-real-parquet-for-gate' + b'PAR1')
        sha=sha256_file(path)
        aid2=str(uuid4())
        meta={'artifact_id':aid2,'row_count':4,'sha256':sha,'scope':'full',
              'source_sha256':rec['source_sha256'],'spec_sha256':rec['spec_sha256']}
        with app.state.projects._lock,app.state.projects._db() as db:
            db.execute('''INSERT INTO feature_artifacts
                 (artifact_id,run_id,project_id,sha256,relpath,row_count,schema_json,source_sha256,created_at)
                 VALUES (?,?,?,?,?,?,?,?,?)''',
                 (aid2,rid,pid,sha,str(path.relative_to(app.state.projects.home/'feature_lab')),
                  4,json.dumps(meta),rec['source_sha256'],now()))
            for state in ('failed_terminal','cancelled','running'):
                db.execute('UPDATE feature_jobs SET status=? WHERE job_id=?',(state,rid))
                db.execute('UPDATE feature_runs SET status=? WHERE run_id=?',(state,rid))
                assert not reg.publication_preview(pid,sid)['artifact_ready']
            db.execute("UPDATE feature_jobs SET status='succeeded' WHERE job_id=?",(rid,))
            db.execute("UPDATE feature_runs SET status='succeeded' WHERE run_id=?",(rid,))
        assert reg.publication_preview(pid,sid)['artifact_ready']
        path.write_bytes(b'PAR1corruptedPAR1')
        assert not reg.publication_preview(pid,sid)['artifact_ready']
    finally:c.__exit__(None,None,None)


def test_supervised_intent_and_idempotency_are_bound_to_task(tmp_path,monkeypatch):
    app,c,pid,aid,_=_env(tmp_path)
    try:
        agent=FeatureAgent(app.state.projects,app.state.native_bi,
           app.state.feature_semantic,app.state.feature_registry,app.state.feature_jobs)
        monkeypatch.setattr(agent,'tools',lambda *a:{'asset_id':aid,'tools':manifest(engine_ready=True)})
        task=TaskSpecV2(task_id='task-invalid',mode='supervised',task_type='binary_classification',
           entity_field_ids=['missing'],prediction_time_field_id='bad-decision',
           label_field_id='bad-label',label_available_at_field_id='bad-available')
        request=PlanRequest(message='将 A1 和 A2 相加到新列',asset_id=aid,
            request_id='invalid-supervised',task=task)
        plan=agent.plan(pid,request)
        assert plan['status']!='ready'
        assert not plan['plan']['actions']
        assert plan['plan']['task_sha256']==digest(task)
        first=agent.plan(pid,PlanRequest(message='查看数据字段',asset_id=aid,
            request_id='reuse-id',task=TaskSpecV2(task_id='first')))
        assert first['plan']['request_fingerprint']
        with pytest.raises(ValueError,match='冲突'):
            agent.plan(pid,PlanRequest(message='查看数据字段',asset_id=aid,
                request_id='reuse-id',task=TaskSpecV2(task_id='changed')))
    finally:c.__exit__(None,None,None)


def test_viewer_snapshot_stale_after_spec_edit(tmp_path):
    app,c,pid,aid,_=_env(tmp_path)
    try:
        reg=app.state.feature_registry
        contract=app.state.feature_semantic.latest(pid,aid,'base')
        rec=reg.create(pid,FeatureSpecV2.model_validate(_spec(contract)),'viewer-gate')
        sid=rec['set_id'];state=FeatureViewerState(app.state.projects,reg)
        result=state.save(pid,sid,{'panels':{'source':{'columns':['A1']}}},0,rec['source_sha256'])
        assert result['viewer_revision']==1
        assert state.get(pid,sid)['status']=='ready'
        spec=dict(rec['spec']);spec['features']=[dict(x) for x in spec['features']]
        spec['features'][0]['output_name']='changed_feature_column'
        reg.update(pid,sid,FeatureSpecV2.model_validate(spec),expected_revision=1)
        view=state.get(pid,sid)
        assert view['status']=='stale' and view['workspace'] is None
    finally:c.__exit__(None,None,None)


def test_labels_and_timezone_fail_closed():
    for truth in ([0,2],[-1,1],[np.nan,1],[],[0,1.1]):
        with pytest.raises(ValueError,match='二分类'):
            _metrics(np.array(truth,dtype=float),np.array([0.1]*len(truth)), 'binary_classification')
    assert _metrics(np.array([0,1]),np.array([0.2,0.8]),'binary_classification')['recall']==1
    from timepilot.feature_contracts import validate_iana_timezone
    assert validate_iana_timezone('Asia/Shanghai')=='Asia/Shanghai'
    for bad in ('Definitely_Not_A_Timezone','PST','', 'GMT+8'):
        with pytest.raises(ValueError,match='时区'):
            validate_iana_timezone(bad)
    doc={'asset_id':'a','version_id':'base','source_sha256':'e'*64,
        'fields':[{'field_id':'field1','source_name':'time','source_column_index':0,
                   'physical_type':'TIMESTAMP','semantic_type':'datetime','role':'event_time',
                   'confirmation':'confirmed'}],'event_time_field_id':'field1',
        'timezone':'Definitely_Not_A_Timezone','state':'confirmed'}
    with pytest.raises(ValidationError):DataContract.model_validate(doc)
