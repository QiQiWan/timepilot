"""v0.40.2 actual selected-input materialization, lineage and watchdog log tests."""
import io
import json
import gzip
import time
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from timepilot.api import create_app
from timepilot.modeling_workflow import FieldMapping, ForecastPlan
from timepilot.modeling_adapters import (
    ResamplingSpec, prepare_observations, inspect_device_ood_folds)
from timepilot.model_source_lineage import build, publish, read, FILENAME
from timepilot.model_temporal_evidence import indices
from timepilot.temporal_worker_log import BoundedWorkerLog, read_log
from timepilot.model_task_store import ModelTaskError
from timepilot.model_task_spec import canonical_sha
from test_model_task_unified_runs_v0393 import _real_forecast
from timepilot.model_task_unified_runs import StartUnifiedRun


SCOPE={'asset_id':'asset-01','version_id':'base','selection_hash':'0'*64,
       'selected_input_sha256':'1'*64}


def forecast_raw(minutes=120):
    return pd.DataFrame({'device':['device-A']*minutes,
       'event_time':pd.date_range('2025-01-01',periods=minutes,freq='min',tz='UTC').astype(str),
       'temperature':[i/7 for i in range(minutes)]})


def forecast_contract():
    mapping=FieldMapping(time='event_time',entity='device',target='temperature',
                         availability_policy='declared_lag',publication_lag_days=0,timezone='UTC')
    plan=ForecastPlan(resampling=ResamplingSpec(enabled=True,source_period_seconds=60),frequency='h')
    return mapping,plan


def test_aggregated_lineage_uses_immutable_runtime_float_precision_and_60_source_rows(tmp_path):
    frame=forecast_raw()
    mapping,plan=forecast_contract()
    materialized,_=prepare_observations(frame,mapping=mapping,plan=plan)
    reloaded=pd.read_csv(io.StringIO(materialized.to_csv(index=False,float_format='%.15g')))
    reloaded['timestamp']=pd.to_datetime(reloaded['timestamp'],utc=True)
    reloaded['available_at']=pd.to_datetime(reloaded['available_at'],utc=True)
    lineage=build(frame,reloaded,mapping,plan,scope=SCOPE)
    assert lineage['raw_selected_rows']==120 and lineage['normalized_rows']==2
    assert [len(x['source_rows']) for x in lineage['rows']]==[60,60]
    assert len({row['selected_row_id'] for row in lineage['rows'][0]['source_rows']})==60
    digest=publish(tmp_path,lineage)
    info=read(tmp_path,expected_sha256=digest,expected_scope=SCOPE,expected_rows=2,
              source_fingerprint=indices(reloaded)[0])
    assert info['status']=='traced_to_selected_snapshot'
    assert info['selected_source_count']==60
    assert info['original_file_row_ordinal_certified'] is False
    assert read(tmp_path,expected_sha256=digest,expected_scope=SCOPE,expected_rows=2,
                source_fingerprint='2'*64)['status']=='unmapped_prediction'
    path=tmp_path/FILENAME
    path.write_bytes(path.read_bytes()+b'TAMPER')
    with pytest.raises(ValueError,match='LINEAGE_SHA256_MISMATCH'):
        read(tmp_path,expected_sha256=digest,expected_scope=SCOPE,expected_rows=2,
             source_fingerprint=indices(reloaded)[0])


def test_duplicate_materialized_fingerprint_is_fail_closed(tmp_path):
    frame=pd.DataFrame({'device':['d1']*2,
       'event_time':['2025-01-01T00:00:00+00:00']*2,
       'temperature':['8.0']*2})
    mapping,plan=forecast_contract()
    plan=plan.model_copy(update={'task_kind':'classification','resampling':ResamplingSpec(enabled=False)})
    # The row matcher is intentionally generic; duplicate normalized rows
    # cannot be independently identified by old temporal prediction hashes.
    normalized=pd.DataFrame({'series_id':['d1']*2,
        'timestamp':pd.to_datetime(['2025-01-01']*2,utc=True),
        'target':[8.,8.], 'available_at':pd.to_datetime(['2025-01-01']*2,utc=True)})
    lineage=build(frame,normalized,mapping,plan,scope=SCOPE)
    digest=publish(tmp_path,lineage)
    item=read(tmp_path,expected_sha256=digest,expected_scope=SCOPE,expected_rows=2,
              source_fingerprint=indices(normalized)[0])
    assert item['status']=='ambiguous_normalized_observation' and item['matches']==2
    assert 'selected_source_rows' not in item


def test_device_ood_preflight_detects_missing_class_and_batch_overlap():
    def rows(ids,targets,times,batches):
        return pd.DataFrame({'series_id':ids,'target':targets,'batch_key':batches,
             'timestamp':pd.to_datetime(times,utc=True),
             'label_available_at':pd.to_datetime(times,utc=True)})
    t='2025-01-01';v='2025-01-02'
    partitions={'train':rows(['a','a','b','b'],[0,1,0,1],[t]*4,['ba','bb','bc','bd']),
                'validation':rows(['a','a','b','b'],[0,1,0,0],[v]*4,['va','vb','vc','vd'])}
    check=inspect_device_ood_folds(partitions,folds=2)
    assert not check['ready']
    assert any('正负标签' in item for item in check['blockers'])
    partitions['validation'].loc[3,'target']=1
    good=inspect_device_ood_folds(partitions,folds=2)
    assert good['ready'] and len(good['folds'])==2
    assert all(x['train_rows']==2 and x['validation_rows']==2 for x in good['folds'])
    partitions['validation'].loc[0,'batch_key']='bc'
    bad=inspect_device_ood_folds(partitions,folds=2)
    assert any('批次' in x for x in bad['blockers'])
    with pytest.raises(ValueError,match='至少 4 台'):
        inspect_device_ood_folds(partitions,folds=4)


def test_bounded_worker_logs_redact_tokens_and_record_truncation(tmp_path):
    sample=(b'X'*100000+b'\nAPI_KEY=private_token_123\n'+b'Y'*90)
    captured=BoundedWorkerLog(io.BufferedReader(io.BytesIO(sample)))
    item=captured.save(tmp_path,2)
    assert item['truncated'] and item['bytes_stored']<=65536+100
    assert 'private_token_123' not in item['tail']
    verified=read_log(tmp_path,2)
    assert verified['log_sha256']==item['log_sha256']


def test_actual_temporal_training_publishes_verified_source_lineage(tmp_path):
    app=create_app(home=tmp_path,embedded_worker=False)
    with TestClient(app) as http:
        pid,other,tid,_=_real_forecast(app,http)
        received=app.state.unified_model_runs.start(pid,tid,StartUnifiedRun(
            expected_revision=1,confirm_temporal_execution=True,budget_profile='5m'))
        engine=app.state.projects.engine
        assert engine.work_once(received['run_id'])
        model=app.state.unified_model_runs
        current=model.summary(pid,tid)
        assert current['state']=='succeeded',current
        first=model.prediction_lineage(pid,tid,row_id=0)
        assert first['status']=='traced_to_selected_snapshot',first
        assert first['selected_source_count']==1
        assert first['source_page']['total']==1
        assert len(first['source_page']['items'])==1
        assert 'values' in first['source_page']['items'][0]
        assert first['original_file_row_ordinal_certified'] is False
        response=http.get(f'/api/v1/projects/{pid}/model-tasks/{tid}/execution/predictions/lineage?row_id=0')
        assert response.status_code==200 and response.json()['lineage_sha256']==first['lineage_sha256']
        assert http.get(f'/api/v1/projects/{pid}/model-tasks/{tid}/execution/predictions/lineage?row_id=0&source_limit=21').status_code==422
        denied=http.get(f'/api/v1/projects/{other}/model-tasks/{tid}/execution/predictions/lineage?row_id=0')
        assert denied.status_code in (404,409)
        doc=app.state.modeling.draft(pid,app.state.unified_model_runs.temporal.get(pid,tid)['draft_id'])
        dataset=Path(app.state.projects.engine.settings.home)/'datasets'/doc['dataset_id']
        archive=dataset/FILENAME
        archive.write_bytes(archive.read_bytes()+b'x')
        with pytest.raises(ModelTaskError,match='lineage verification failed'):
            model.prediction_lineage(pid,tid,row_id=0)
