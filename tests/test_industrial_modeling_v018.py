"""v0.18 real source publisher -> reviewed materialization -> two execution paths."""
import csv
import io
import json
import math
from datetime import datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from timepilot.api import create_app
from timepilot.data import get_dataset
from test_modeling_workflow_v017 import prepare_bi, _post


def minutes_csv(n_hours=56,*,missing=False,event=True,regime=True):
    out=io.StringIO(newline='');writer=csv.writer(out)
    writer.writerow(['设备','采集时间','温度','压力','检修','工况'])
    for i in range(n_hours*60):
        if missing and i==90:continue
        t=datetime(2026,3,1)+timedelta(minutes=i)
        writer.writerow(['机台甲',t.strftime('%Y-%m-%d %H:%M:%S'),
                         round(27+.02*i/60+math.sin(i/22),3),
                         round(1.1+math.cos(i/60)*.2,4),
                         '维修' if event and i==60*25 else '',
                         '加工' if regime and i>=20*60 else '待机'])
    return out.getvalue().encode('utf-8')


def classification_csv(n=230):
    out=io.StringIO(newline='');writer=csv.writer(out)
    writer.writerow(['设备','节点时间','锡膏厚度','负压','是否不良','标签确认时间','批次'])
    for i in range(n):
        t=datetime(2026,3,1)+timedelta(minutes=i*5)
        positive=(i%5==0)
        writer.writerow(['贴片机-1',t.strftime('%Y-%m-%d %H:%M:%S'),
                         12+3*int(positive)+math.sin(i/3),
                         round(1+int(positive)*.5+math.cos(i/8)*.05,4),
                         'NG' if positive else 'OK',
                         (t+timedelta(minutes=2)).strftime('%Y-%m-%d %H:%M:%S'),f'B-{i:04d}'])
    return out.getvalue().encode('utf-8')


def forecast_payload(aid):
    return {'asset_id':aid,'name':'温度与压力联合预测','confirm_scope':True,
            'mapping':{'time':'采集时间','entity':'设备','target':'温度','target_unit':'℃',
                       'observed_features':['压力'],
                       'availability_policy':'declared_lag','publication_lag_days':0,
                       'event':{'column':'检修','truthy_values':['维修']},
                       'regime':{'column':'工况'}},
            'plan':{'task_kind':'continuous_forecast','task_family':'equipment_state',
                    'frequency':'h','horizon_steps':2,'min_history':24,
                    'validation_windows':2,'primary_metric':'MAE',
                    'feature_policy':'historical_observed',
                    'model_candidates':['historical_exog_ridge'],
                    'resampling':{'enabled':True,'source_period_seconds':60,
                                  'aggregation':'mean','min_coverage':1},
                    'mechanism':{'target_min':-50,'target_max':150,
                                 'max_step_change':12,'enforcement':'block'}}}


def classification_payload(aid):
    return {'asset_id':aid,'name':'贴片节点 A 不良提前识别','confirm_scope':True,
            'mapping':{'time':'节点时间','entity':'设备','batch':'批次',
                       'target':'是否不良','observed_features':['锡膏厚度','负压'],
                       'availability_policy':'declared_lag','publication_lag_days':0},
            'plan':{'task_kind':'classification','task_family':'equipment_state',
                    'frequency':'h','horizon_steps':1,'validation_windows':2,
                    'min_history':24,'primary_metric':'MAE',
                    'feature_policy':'snapshot_at_decision',
                    'model_candidates':['binary_logistic'],
                    'classification':{'positive_values':['NG'],
                                      'negative_values':['OK'],
                                      'label_available_at':'标签确认时间',
                                      'decision_stage':'节点A','min_recall':0.8}}}


def exercise(http,app,pid,payload):
    base=f'/api/v1/projects/{pid}/modeling'
    d=_post(http,base+'/drafts',payload)
    url=base+'/drafts/'+d['draft_id']
    check=_post(http,url+'/preflight')
    assert check['ready'],check['blockers']
    approval=_post(http,url+'/approve',{'expected_revision':1,'confirm':True,
                                       'acknowledge_single_target':True,'acknowledge_availability':True})
    assert approval['status']=='approved'
    final=_post(http,url+'/materialize')
    assert final['status']=='dataset_ready'
    task=_post(http,url+'/submit')
    app.state.projects.engine.work_once(task['run_id'])
    row=app.state.projects.engine.store.run(task['run_id'])
    assert row.status=='succeeded',(row.status,row.error_message)
    return check,final,task,url


def test_auditable_minute_resampling_exogenous_events_and_regimes(tmp_path):
    app=create_app(home=tmp_path,embedded_worker=False)
    with TestClient(app) as http:
        pid,_,aid,_,_=prepare_bi(app,http,minutes_csv())
        check,d,task,url=exercise(http,app,pid,forecast_payload(aid))
        assert check['resampling']['bucket_count']==56
        assert check['selected_rows']==3360 and check['eligible_rows']==56
        assert check['feature_count']>=4
        manifest,observations=get_dataset(app.state.projects.engine.store,app.state.projects.engine.settings,d['dataset_id'])
        assert 'obs_01' in observations
        assert 'event_flag' in observations and 'event_age_steps' in observations
        assert any(x.startswith('regime_') for x in observations)
        assert manifest.feature_columns==d['materialization']['feature_columns']
        assert len(observations)==56
        assert observations['available_at'].iloc[0]==observations['timestamp'].iloc[1]
        folder=app.state.projects.engine.settings.home/'datasets'/d['dataset_id']
        audit=json.loads((folder/'resampling_audit.json').read_text())
        assert len(audit['buckets'])==56 and audit['buckets'][0]['input_rows']==60
        assert all(x['coverage']==1 for x in audit['buckets'])
        lineage=http.get(url+'/lineage').json()
        assert lineage['materialization']['resampling_audit_sha256']
        from timepilot.storage import sha256_file
        assert sha256_file(folder/'resampling_audit.json')==lineage['materialization']['resampling_audit_sha256']
        forecast=app.state.projects.engine.forecast(task['run_id'])
        assert forecast['test'] and forecast['future']
        assert all(x['model_id']=='historical_exog_ridge' for x in forecast['test'])
        assert app.state.projects.engine.predict_saved(task['run_id'],'机台甲',2)


def test_missing_minute_bucket_rejected_without_imputation(tmp_path):
    app=create_app(home=tmp_path,embedded_worker=False)
    with TestClient(app) as http:
        pid,_,aid,_,_=prepare_bi(app,http,minutes_csv(missing=True))
        base=f'/api/v1/projects/{pid}/modeling'
        d=_post(http,base+'/drafts',forecast_payload(aid))
        result=_post(http,base+'/drafts/'+d['draft_id']+'/preflight')
        assert not result['ready']
        assert any('覆盖率' in x or '重采样拒绝' in x for x in result['blockers']),result


def test_binary_defect_classifier_causal_split_and_artifact(tmp_path):
    app=create_app(home=tmp_path,embedded_worker=False)
    with TestClient(app) as http:
        pid,other,aid,_,_=prepare_bi(app,http,classification_csv())
        check,d,task,url=exercise(http,app,pid,classification_payload(aid))
        assert check['classification']['train_positive']>0
        assert check['classification']['no_batch_overlap']
        assert http.get(f'/api/v1/projects/{other}/modeling/drafts/{d["draft_id"]}').status_code==404
        report=app.state.projects.engine.report(task['run_id'])
        assert report['protocol_id']=='chronological_matured_labels_v1'
        assert report['threshold_source']=='validation_only'
        result=json.loads((app.state.projects.engine.run_dir(task['run_id'])/'stages/classification_result.json').read_text())
        assert result['test']['tp']+result['test']['fp']+result['test']['fn']+result['test']['tn']==result['split_audit']['test_rows']
        assert result['validation']['recall']>=.8
        assert len(result['predictions'])==result['split_audit']['test_rows']
        second=_post(http,url+'/reproduce')
        second_url=f'/api/v1/projects/{pid}/modeling/drafts/{second["draft_id"]}'
        assert _post(http,second_url+'/preflight')['ready']
        _post(http,second_url+'/approve',{'expected_revision':1,'confirm':True,
                                         'acknowledge_single_target':True,'acknowledge_availability':True})
        _post(http,second_url+'/materialize')
        queued=_post(http,second_url+'/submit')
        app.state.projects.engine.work_once(queued['run_id'])
        copied=json.loads((app.state.projects.engine.run_dir(queued['run_id'])/'stages/classification_result.json').read_text())
        assert copied['test']==result['test']
        assert copied['predictions']==result['predictions']


def test_classification_label_contract_blocks_future_feature(tmp_path):
    from timepilot.modeling_adapters import ClassificationSpec
    from pydantic import ValidationError
    import pytest
    with pytest.raises(ValidationError):ClassificationSpec(positive_values=['NG'],negative_values=['NG'],label_delay_seconds=60)
    app=create_app(home=tmp_path,embedded_worker=False)
    with TestClient(app) as http:
        pid,_,aid,_,_=prepare_bi(app,http,classification_csv())
        draft=classification_payload(aid)
        draft['mapping']['feature_available_at']={'锡膏厚度':'标签确认时间'}
        d=_post(http,f'/api/v1/projects/{pid}/modeling/drafts',draft)
        check=_post(http,f'/api/v1/projects/{pid}/modeling/drafts/{d["draft_id"]}/preflight')
        assert not check['ready'] and any('判断时刻后' in x for x in check['blockers'])


def test_classification_decision_api_and_evaluation_are_project_scoped(tmp_path):
    """Real persisted classifier can score reviewed point-in-time inputs only."""
    from datetime import timezone
    app=create_app(home=tmp_path,embedded_worker=False)
    with TestClient(app) as http:
        pid,other,aid,_,_=prepare_bi(app,http,classification_csv())
        _,_,run,url=exercise(http,app,pid,classification_payload(aid))
        evaluation=http.get(url+'/evaluation')
        assert evaluation.status_code==200,evaluation.text
        response=evaluation.json()
        assert response['status']=='succeeded'
        assert response['task_kind']=='classification'
        assert response['total_predictions']>20
        assert response['classification']['threshold_source']=='validation_only'
        assert http.get(f'/api/v1/projects/{other}/modeling/drafts/{url.rsplit("/",1)[1]}/evaluation').status_code==404
        request={'confirm_feature_availability':True,'records':[
            {'values':{'锡膏厚度':15.8,'负压':1.52},
             'decision_time':'2026-03-11T09:00:00+08:00',
             'features_available_at':'2026-03-11T08:59:00+08:00','entity_id':'贴片机-1'}]}
        assert http.post(url+'/score',json={**request,'confirm_feature_availability':False}).status_code==422
        assert http.post(url+'/score',json={**request,'records':[{**request['records'][0],
                 'features_available_at':'2026-03-11T09:01:00+08:00'}]}).status_code==422
        assert http.post(url+'/score',json={**request,'records':[{**request['records'][0],
                 'values':{'锡膏厚度':15.8,'负压':1.52,'是否不良':1}}]}).status_code==422
        audit=http.get(url+'/audit',params={'offset':0,'limit':5})
        assert audit.status_code==200,audit.text
        assert audit.json()['audit_sha256']
        if response['quality']['status']=='passed':
            scored=http.post(url+'/score',json=request)
            assert scored.status_code==200,scored.text
            result=scored.json()
            assert result['threshold_source']=='validation_only'
            assert len(result['results'])==1
            assert 0<=result['results'][0]['probability']<=1
            # Persisted artifacts have a checksum gate even if a user tampers with files.
            model_dir=app.state.projects.engine.run_dir(run['run_id'])/'classification_models'/'binary_logistic'
            (model_dir/'model.json').write_text('{}',encoding='utf-8')
            assert http.post(url+'/score',json=request).status_code==422


def test_resampling_reset_rule_applies_to_final_model(tmp_path):
    """Maintenance boundary must survive validation and independent final refit."""
    from timepilot.storage import load_json
    app=create_app(home=tmp_path,embedded_worker=False)
    with TestClient(app) as http:
        pid,_,aid,_,_=prepare_bi(app,http,minutes_csv(n_hours=64))
        payload=forecast_payload(aid)
        payload['mapping']['event']['reset_policy']='exclude_cross_event_windows'
        _,_,run,url=exercise(http,app,pid,payload)
        for m in app.state.projects.engine.forecast(run['run_id'])['models']:
            artifact=load_json(app.state.projects.engine.run_dir(run['run_id'])/m['artifact'])
            # 64 rows / 2-step horizon normally permit 59 design origins;
            # at least one cross-maintenance design window must be discarded.
            assert 0<artifact['training_windows']<59
        assert http.get(url+'/audit',params={'offset':0,'limit':4}).json()['total_buckets']==64
