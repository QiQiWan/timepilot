"""v0.25.3: real SQLite state + schema contracts, no numerical mock success."""
from uuid import uuid4
import pytest
from pydantic import ValidationError
from timepilot.feature_contracts import DataContract,FieldContract
from test_conversational_bi_v0231 import _env
from test_featurelab2_contract_registry_v0247 import _spec


def test_reject_numeric_available_and_event_times():
    with pytest.raises(ValidationError, match='时间角色'):
        FieldContract(field_id='fld_numeric_time',source_name='Time',source_column_index=0,
                      physical_type='DOUBLE',semantic_type='numeric',role='available_at')
    fields=[FieldContract(field_id='fld_num',source_name='Time',source_column_index=0,
                          physical_type='DOUBLE',semantic_type='numeric')]
    with pytest.raises(ValidationError,match='日期时间'):
        DataContract(asset_id='a',version_id='base',source_sha256='f'*64,
                     fields=fields,event_time_field_id='fld_num',timezone='Asia/Shanghai')


def test_cancel_updates_both_tables_and_remains_idempotent(tmp_path):
    app,c,pid,aid,_=_env(tmp_path)
    try:
        base=f'/api/v1/projects/{pid}/feature-lab'
        doc=app.state.feature_semantic.latest(pid,aid,'base')
        spec=_spec(doc)
        made=c.post(base+'/sets',json={'spec':spec,'request_id':'cancel-plan'})
        assert made.status_code==201,made.text
        sid=made.json()['set_id']
        run=c.post(base+f'/sets/{sid}/runs',json={'request_id':'cancel-once'})
        assert run.status_code==202,run.text
        rid=run.json()['job_id']
        for _ in range(2):
            stopped=c.post(base+f'/runs/{rid}/cancel')
            assert stopped.status_code==200,stopped.text
            assert stopped.json()['status']=='cancelled'
        with app.state.projects._db() as db:
            assert db.execute('SELECT status FROM feature_runs WHERE run_id=?',(rid,)).fetchone()[0]=='cancelled'
            assert db.execute('SELECT status FROM feature_jobs WHERE job_id=?',(rid,)).fetchone()[0]=='cancelled'
        assert c.get(base+f'/runs/{rid}/result').status_code==409
    finally:c.__exit__(None,None,None)


def test_supervised_plan_fails_when_draft_has_no_pit_artifact(tmp_path):
    app,c,pid,aid,_=_env(tmp_path)
    try:
        base=f'/api/v1/projects/{pid}/feature-lab'
        spec=_spec(app.state.feature_semantic.latest(pid,aid,'base'))
        created=c.post(base+'/sets',json={'spec':spec,'request_id':'supervised-guard'})
        assert created.status_code==201,created.text
        sid=created.json()['set_id']
        task={'task_id':'test','mode':'supervised','task_type':'binary_classification',
              'prediction_time_field_id':'fld_d','label_field_id':'fld_y',
              'label_available_at_field_id':'fld_label_at'}
        result=c.post(base+'/evaluations/plans',json={'protocol':{'task':task,'set_id':sid,'revision':1}})
        assert result.status_code==422,result.text
        assert not result.json().get('plan_id')
    finally:c.__exit__(None,None,None)
