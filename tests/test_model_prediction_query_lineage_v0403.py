"""v0.40.3 signed tabular source rows, micro-metrics and forecast window edges."""
import csv
import math

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from timepilot.api import create_app
from timepilot.model_task_store import ModelTaskError, ModelTaskConflict
from timepilot.model_prediction_query import _filters
from timepilot.model_tabular_lineage import rowset_sha256, resolve
from timepilot.model_task_unified_runs import StartUnifiedRun
from timepilot.model_task_spec import canonical_sha
from test_model_task_time_and_results_v0395 import prepare, complete
from test_model_task_unified_runs_v0393 import _real_forecast


def test_tabular_prediction_lineage_uses_signed_training_rowset_and_fails_on_changed_row():
    frame=pd.DataFrame({'device':['a','b'],'target':['1.0','3.0'],'x':['2','4']})
    source={'original_data_sha256':'a'*64}
    class Asset:
        asset_id='asset';native_sha256='b'*64
    class Spec:
        asset=Asset();version_id='base';asset_id='asset'
    result={'source_lineage_schema':'tabular-selected-row-v1',
            'source_rowset_sha256':rowset_sha256(frame),'source_sha256':'a'*64,
            'data_version':'base','native_sha256':'b'*64,'rows':2}
    prediction={'source_row_index':'1','source_row_sha256':canonical_sha({
        'source_sha256':'a'*64,'row_position':1})}
    signed=resolve(frame=frame,prediction=prediction,result=result,source=source,spec=Spec())
    assert signed['status']=='traced_to_selected_snapshot'
    assert signed['source_page']['items'][0]['values']['x']=='4'
    assert signed['selected_source_count']==1
    assert signed['original_file_row_ordinal_certified'] is False
    with pytest.raises(ModelTaskConflict,match='source rows or row ordering'):
        resolve(frame=frame.iloc[::-1].reset_index(drop=True),prediction=prediction,
                result=result,source=source,spec=Spec())
    with pytest.raises(ModelTaskError,match='does not match'):
        resolve(frame=frame,prediction={**prediction,'source_row_sha256':'f'*64},
                result=result,source=source,spec=Spec())
    assert resolve(frame=frame,prediction=prediction,result={},source=source,spec=Spec())['status']=='legacy_lineage_not_recorded'


def test_prediction_query_rejects_invalid_filters():
    for item in ({'model':'forest; DROP TABLE versions'}, {'fold':-1},
                 {'month':'2026-19'}, {'offset':-1}, {'limit':501},
                 {'min_abs_error':float('nan')}, {'device':''}):
        with pytest.raises(ModelTaskError):_filters(**item)


def test_real_tabular_prediction_query_and_full_source_row_trace(tmp_path):
    prepared=prepare(tmp_path,'rolling_origin')
    catalog,model,tasks,runs,unified,tid=prepared
    done=complete(prepared)
    pid=catalog.a
    result=unified.prediction_query(pid,tid,model='forest',fold=1,limit=8)
    assert result['query_backend']=='verified_csv_scan_fallback'
    assert result['metrics']['prediction_rows']>=8
    assert len(result['items'])==8
    errors=[float(x['absolute_error']) for x in result['items']]
    assert all(float(x['fold'])==1 and x['model']=='forest' for x in result['items'])
    assert result['metrics']['mae']>=0
    assert result['metrics']['metric_scope']=='selected_validation_prediction_rows_micro_average'
    complete_set=unified.prediction_rows(pid,tid,model='forest',fold=1,limit=500)['items']
    assert len(complete_set)==result['metrics']['prediction_rows']
    assert result['metrics']['mae']==pytest.approx(sum(float(r['absolute_error']) for r in complete_set)/len(complete_set),abs=1e-9)
    assert result['metrics']['rmse']==pytest.approx(math.sqrt(sum(float(r['residual'])**2 for r in complete_set)/len(complete_set)),abs=1e-9)
    row=int(result['items'][0]['__tp_rowid'])
    traced=unified.prediction_lineage(pid,tid,row_id=row)
    assert traced['status']=='traced_to_selected_snapshot'
    assert traced['source_rowset_sha256']==runs.get(pid,done['run_id'])['result']['source_rowset_sha256']
    assert len(traced['source_page']['items'])==1
    assert traced['source_page']['items'][0]['values'] and len(traced['source_page']['items'][0]['values'])>2
    assert len(traced['selected_source_rows'])==1
    assert len(unified.prediction_query(pid,tid,month='1900-01')['items'])==0
    assert unified.prediction_query(pid,tid,model='forest',fold=1,limit=8,offset=8)['items'][0]['__tp_rowid']!=str(row)
    with pytest.raises(KeyError):unified.prediction_query(catalog.b,tid)
    with pytest.raises(ModelTaskError):unified.prediction_query(pid,tid,model='illegal/../../')
    with pytest.raises(ModelTaskError):unified.prediction_lineage(pid,tid,row_id=1000000)


def test_temporal_window_history_edges_cover_fold_and_block_corruption(tmp_path):
    app=create_app(home=tmp_path,embedded_worker=False)
    with TestClient(app) as http:
        pid,other,tid,_=_real_forecast(app,http)
        running=app.state.unified_model_runs.start(pid,tid,StartUnifiedRun(
            expected_revision=1,confirm_temporal_execution=True,budget_profile='5m'))
        assert app.state.projects.engine.work_once(running['run_id'])
        model=app.state.unified_model_runs
        scope=model.summary(pid,tid)
        assert scope['state']=='succeeded'
        result=model.prediction_window_lineage(pid,tid,row_id=0,limit=8)
        assert result['status']=='certified_forecast_history_source_edges',result
        assert result['historical_observation_membership_certified']
        assert not result['per_feature_usage_or_attribution_certified']
        assert result['history_normalized_rows']>0
        assert result['source_edge_total']>=result['history_normalized_rows']
        assert result['source_edges'] and len(result['source_edges'])<=8
        if result['next_offset'] is not None:
            next_page=model.prediction_window_lineage(pid,tid,row_id=0,offset=result['next_offset'],limit=8)
            assert next_page['source_edges'][0]['selected_row_id']!=result['source_edges'][0]['selected_row_id']
        endpoint=f'/api/v1/projects/{pid}/model-tasks/{tid}/execution/predictions/window-lineage'
        assert http.get(endpoint+'?row_id=0&limit=4').status_code==200
        assert http.get(endpoint+'?row_id=0&limit=101').status_code==422
        assert http.get(endpoint.replace(pid,other)+'?row_id=0').status_code in (404,409)
        doc=app.state.modeling.draft(pid,model.temporal.get(pid,tid)['draft_id'])
        from timepilot.model_source_lineage import FILENAME
        archive=app.state.projects.engine.settings.home/'datasets'/doc['dataset_id']/FILENAME
        archive.write_bytes(archive.read_bytes()+b'change')
        with pytest.raises(ModelTaskError,match='lineage verification failed'):
            model.prediction_window_lineage(pid,tid,row_id=0)
