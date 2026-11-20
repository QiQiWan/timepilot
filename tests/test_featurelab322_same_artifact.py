"""v0.32.2 contract & delivery tests (stubbed artifact bytes, not a Parquet runtime test).

A successful worker receipt without a durable file / row cannot be treated as
verified; a genuine browser render additionally needs independent target QA.
"""
import hashlib
import json
from uuid import uuid4

import pytest
from test_conversational_bi_v0231 import _env
from test_featurelab32_auto_workflow import simulate_profile, set_job
from timepilot.feature_auto_delivery import confirmed_task, supervision_options
from timepilot.feature_contracts import DataContract, FieldContract, canonical
from timepilot.feature_evaluation import FeatureEvaluator
from timepilot.projects import now


def valid_contract():
    fields=[
      FieldContract(field_id='fld_device',source_name='device_id',source_column_index=0,
                    physical_type='VARCHAR',semantic_type='id',role='entity_key',confirmation='confirmed'),
      FieldContract(field_id='fld_event',source_name='event_time',source_column_index=1,
                    physical_type='TIMESTAMP',semantic_type='datetime',role='event_time',
                    timezone='Asia/Shanghai',confirmation='confirmed'),
      FieldContract(field_id='fld_available',source_name='available_at',source_column_index=2,
                    physical_type='TIMESTAMP',semantic_type='datetime',role='available_at',
                    timezone='Asia/Shanghai',confirmation='confirmed'),
      FieldContract(field_id='fld_decision',source_name='prediction_time',source_column_index=3,
                    physical_type='TIMESTAMP',semantic_type='datetime',role='prediction_time',
                    timezone='Asia/Shanghai',confirmation='confirmed'),
      FieldContract(field_id='fld_label_at',source_name='label_available_at',source_column_index=4,
                    physical_type='TIMESTAMP',semantic_type='datetime',role='available_at',
                    timezone='Asia/Shanghai',confirmation='confirmed'),
      FieldContract(field_id='fld_label',source_name='NG_FLAG',source_column_index=5,
                    physical_type='INTEGER',semantic_type='numeric',role='label',confirmation='confirmed'),
      FieldContract(field_id='fld_signal',source_name='pressure',source_column_index=6,
                    physical_type='DOUBLE',semantic_type='numeric',role='value',confirmation='confirmed'),
    ]
    return DataContract(asset_id='a',version_id='base',source_sha256='a'*64,
        revision=4,state='confirmed',timezone='Asia/Shanghai',fields=fields,
        entity_field_ids=['fld_device'],event_time_field_id='fld_event',
        available_at_field_id='fld_available')


def test_supervised_plan_uses_only_confirmed_real_time_and_label():
    con=valid_contract()
    opts=supervision_options(con,'classification')
    assert opts['eligible'] and opts['task_type']=='binary_classification'
    assert [x['field_id'] for x in opts['label_fields']]==['fld_label']
    task=confirmed_task(con,objective='classification',session_id='a'*36,
        label_field_id='fld_label',prediction_time_field_id='fld_decision',
        label_available_at_field_id='fld_label_at')
    assert task.mode=='supervised' and task.task_type=='binary_classification'
    assert task.entity_field_ids==['fld_device']
    with pytest.raises(ValueError,match='业务确认'):
        confirmed_task(con,objective='classification',session_id='a'*36,
            label_field_id='fld_signal',prediction_time_field_id='fld_decision',
            label_available_at_field_id='fld_label_at')
    con.fields[3].physical_type='DOUBLE'
    with pytest.raises(ValueError):
        confirmed_task(con,objective='classification',session_id='a'*36,
            label_field_id='fld_label',prediction_time_field_id='fld_decision',
            label_available_at_field_id='fld_label_at')


def test_no_calendar_label_contract_does_not_enable_supervised_mode():
    con=valid_contract();con.fields=[x for x in con.fields if x.field_id!='fld_label_at']
    options=supervision_options(con,'classification')
    assert not options['eligible'] and not options['label_available_fields']
    with pytest.raises(ValueError):
        confirmed_task(con,objective='classification',session_id='x',
            label_field_id='fld_label',prediction_time_field_id='fld_decision',
            label_available_at_field_id='fld_available')


def make_review(app,client,pid,asset):
    base=f'/api/v1/projects/{pid}/feature-lab'
    con=client.get(base+'/contracts',params={'asset_id':asset}).json()
    created=client.post(base+'/analysis/sessions',json={
        'asset_id':asset,'goal':'analyse anomaly','task_hint':'anomaly','budget':6,
        'request_id':str(uuid4())}).json()
    sid=created['session_id']
    simulate_profile(app,pid,asset,con,created['profile_job_id'])
    preview=client.post(base+f'/analysis/sessions/{sid}/advance').json()
    candidates=client.get(base+'/candidate-sessions/'+preview['candidate_session_id']).json()['candidates']
    for job_id in preview['preview_job_ids']:
        set_job(app,job_id,{'quality':[{'feature_name':c['output_name'],
            'rows_scanned':100,'finite_count':100,'null_rate':0,
            'constant_on_full_data':False} for c in candidates]})
    review=client.post(base+f'/analysis/sessions/{sid}/advance').json()
    assert review['stage']=='review' and review['selection']
    saved=client.post(base+f'/analysis/sessions/{sid}/save-set',json={
        'name':'QA source-pinned set','request_id':str(uuid4())}).json()
    return base,sid,saved


def inject_mock_artifact(app,pid,mat_job,set_id):
    """Only verifies SQLite/file SHA semantics; contents are NOT valid Parquet."""
    registry=app.state.feature_registry
    rec=registry.get(pid,set_id)
    artifact_id=str(uuid4()); path=(app.state.projects.home/'feature_lab'/pid/
                                    'artifacts'/artifact_id/'data.parquet')
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_bytes(b'DETERMINISTIC-FIXTURE-NOT-PARQUET')
    sha=hashlib.sha256(path.read_bytes()).hexdigest()
    meta={'artifact_id':artifact_id,'sha256':sha,'scope':'full',
          'source_sha256':rec['source_sha256'],'spec_sha256':rec['spec_sha256'],
          'row_count':3,'columns':['A1','a1_mean_5'],'pit':{'status':'not_applicable'}}
    with app.state.projects._lock,app.state.projects._db() as db:
        db.execute("UPDATE feature_runs SET status='succeeded' WHERE run_id=?",(mat_job,))
        db.execute('''INSERT INTO feature_artifacts(artifact_id,run_id,project_id,sha256,
            relpath,row_count,schema_json,source_sha256,created_at)
            VALUES(?,?,?,?,?,?,?,?,?)''',
            (artifact_id,mat_job,pid,sha,str(path.relative_to(app.state.projects.home/'feature_lab')),
             3,canonical(meta),rec['source_sha256'],now()))
    result={'artifact_id':artifact_id,'sha256':sha,'source_sha256':rec['source_sha256'],
            'spec_sha256':rec['spec_sha256'],'row_count':3}
    set_job(app,mat_job,result)
    return result


def test_same_source_exploration_delivery_requires_durable_evaluation_and_bi_receipt(tmp_path):
    app,client,pid,asset,_=_env(tmp_path)
    try:
        base,sid,saved=make_review(app,client,pid,asset)
        mat=client.post(base+f'/analysis/sessions/{sid}/materialize',json={
            'request_id':str(uuid4())}).json()
        result=inject_mock_artifact(app,pid,mat['materialize_job_id'],saved['feature_set_id'])
        ev=client.post(base+f'/analysis/sessions/{sid}/advance')
        assert ev.status_code==200,ev.text
        stage=ev.json();assert stage['status']=='evaluating',stage
        proof_before=client.get(base+f'/analysis/sessions/{sid}/delivery').json()
        assert proof_before['status']=='materialized' and proof_before['artifact']['sha256']==result['sha256']
        assert not proof_before['same_artifact_evaluation']
        # The real FeatureEvaluator's exploration planner/receipt is called.
        e_job=app.state.feature_jobs.get_job(pid,stage['evaluation_job_id'])
        evaluator=FeatureEvaluator(app.state.projects,app.state.native_bi,
            app.state.feature_semantic,app.state.feature_registry,app.state.feature_jobs)
        actual=evaluator.run_plan(pid,e_job['payload']['plan_id'],'fake-worker-evaluation-id')
        set_job(app,stage['evaluation_job_id'],actual)
        done=client.post(base+f'/analysis/sessions/{sid}/advance')
        assert done.status_code==200,done.text
        assert done.json()['stage']=='evaluated' and done.json()['status']=='ready'
        proof=client.get(base+f'/analysis/sessions/{sid}/delivery').json()
        assert proof['same_artifact_evaluation'] and not proof['same_artifact_bi_receipt']
        assert proof['artifact']['sha256']==result['sha256']
        assert proof['evaluation']['artifact_sha256']==result['sha256']
        forged=client.post(base+f'/analysis/sessions/{sid}/viewer-receipt',json={
            'expected_revision':done.json()['revision'],'artifact_id':result['artifact_id'],
            'artifact_sha256':'f'*64,'viewer_version':'5.6.1','panel_count':2,
            'workspace':{'panels':{'source':{},'feature':{}}}})
        assert forged.status_code in (409,422)
        r=client.post(base+f'/analysis/sessions/{sid}/viewer-receipt',json={
            'expected_revision':done.json()['revision'],'artifact_id':result['artifact_id'],
            'artifact_sha256':result['sha256'],'viewer_version':'5.6.1','panel_count':2,
            'workspace':{'panels':{'source':{},'feature':{}}}})
        assert r.status_code==200,r.text
        final=client.get(base+f'/analysis/sessions/{sid}/delivery').json()
        assert final['status']=='app_evidence_linked'
        assert final['same_artifact_bi_receipt']
        assert final['production_verified'] is False
        report=client.get(base+f'/analysis/sessions/{sid}/report').json()
        assert result['sha256'] in report['markdown']
        assert report['delivery_status']=='app_evidence_linked'
        assert report['report_sha256'] == hashlib.sha256(report['markdown'].encode('utf-8')).hexdigest()
        assert report['delivery']['artifact']['sha256'] == result['sha256']
        assert report['delivery']['same_artifact_evaluation'] is True
        assert report['report_encoding'] == 'utf-8'
        # Changing physical bytes must immediately invalidate delivery.
        path=app.state.projects.home/'feature_lab'/pid/'artifacts'/result['artifact_id']/'data.parquet'
        path.write_bytes(b'CORRUPTED')
        broken=client.get(base+f'/analysis/sessions/{sid}/delivery').json()
        assert broken['status']=='blocked' and not broken['same_artifact_evaluation']
    finally:client.__exit__(None,None,None)


def test_agent_cannot_silently_convert_exploration_to_supervised(tmp_path):
    app,client,pid,asset,_=_env(tmp_path)
    try:
        base,sid,state=make_review(app,client,pid,asset)
        assert state['supervision']['mode']=='exploration'
        assert app.state.feature_registry.get(pid,state['feature_set_id'])['spec']['task'] is None
        # Since the data has an integer Time field and no confirmed label,
        # the interface must not offer a supervised shortcut.
        opts=client.get(base+f'/analysis/sessions/{sid}/supervision-options').json()
        assert opts['eligible'] is False
        forbidden=client.post(base+f'/analysis/sessions/{sid}/save-set',json={
            'name':'label bypass','request_id':str(uuid4()),'supervision':{
             'confirm':True,'label_field_id':opts['entity_field_ids'][0] if opts['entity_field_ids'] else 'fld_fake',
             'prediction_time_field_id':'fld_fake','label_available_at_field_id':'fld_fake'}})
        assert forbidden.status_code in (409,422)
    finally:client.__exit__(None,None,None)
