"""PIT join and fail-closed release gate, independent of optional heavy engines."""
import sys
from pathlib import Path
import numpy as np
import pandas as pd
import pytest

from timepilot.feature_advanced_join import pit_join


def source_pair():
    instances=pd.DataFrame({'sample':['s1','s2'], 'prediction_time':['2026-01-01T10:00:00+08:00','2026-01-02T10:00:00+08:00'],
                            'sensor':[1.,2.], 'label':[0,1]})
    candidates=pd.DataFrame({'__sample_id':['s2','s1'],
                             '__candidate_cutoff_utc':['2026-01-02T02:00:00Z','2026-01-01T02:00:00Z'],
                             'signal_abs':[3.,4.]})
    return instances,candidates


def joined(d=None,c=None,**kwargs):
    if d is None or c is None:d,c=source_pair()
    return pit_join(d,c,decision_key='sample',prediction_time='prediction_time',candidate_key='__sample_id',chosen=['signal_abs'],timezone='Asia/Shanghai',**kwargs)


def test_pit_join_reorders_with_exact_cutoffs():
    frame,proof=joined()
    assert frame['sample'].tolist()==['s1','s2']
    col=next(iter(proof['advanced_names'].values()))
    assert frame[col].tolist()==[4.,3.]
    assert frame[['sample','label','sensor']].to_dict('records')==[{'sample':'s1','label':0,'sensor':1.},{'sample':'s2','label':1,'sensor':2.}]
    assert proof['rows']==2


@pytest.mark.parametrize('failure', ['late_cutoff','missing','duplicate','source_collision','not_numeric','infinite','null_key','reserved_key'])
def test_pit_join_rejects_unsafe_inputs(failure):
    d,c=source_pair()
    if failure=='late_cutoff':c.loc[0,'__candidate_cutoff_utc']='2026-01-03T02:00:00Z'
    elif failure=='missing':c=c.iloc[:1]
    elif failure=='duplicate':c.loc[1,'__sample_id']='s2'
    elif failure=='source_collision':d['signal_abs']=[2.,3.]
    elif failure=='not_numeric':c['signal_abs']=['a','b']
    elif failure=='infinite':c.loc[1,'signal_abs']=np.inf
    elif failure=='null_key':d.loc[0,'sample']=None
    elif failure=='reserved_key':d['__join_key']=['x','y']
    with pytest.raises((ValueError,KeyError)):
        joined(d,c)


def test_release_gate_fails_without_actual_browser():
    sys.path.insert(0,str(Path(__file__).parents[1]/'scripts'))
    from featurelab31_release_gate import check
    backend={'status':'passed_backend_same_source_browser_pending',
             'artifact_sha256':'a'*64,'source_sha256':'b'*64}
    issues=check(backend,{},require_advanced=False)
    assert issues and any('browser' in item for item in issues)


def test_project_workflow_is_read_only_and_asset_pinned(tmp_path):
    from test_conversational_bi_v0231 import _env
    app,client,pid,aid,_=_env(tmp_path)
    url=f'/api/v1/projects/{pid}/feature-lab'
    try:
        first=client.get(url+'/workflow/summary',params={'asset_id':aid})
        assert first.status_code==200,first.text
        one=first.json()
        assert len(one['steps'])==5
        assert one['asset_id']==aid
        assert one['next_step']['key']=='profile'
        created=client.post(url+'/candidate-sessions',json={'asset_id':aid,
                         'request_id':'w31test','template':'basic'})
        assert created.status_code==201,created.text
        second=client.get(url+'/workflow/summary',params={'asset_id':aid}).json()
        assert second['evidence']['candidate_sessions']==1
        assert second['steps'][1]['completed'] is True
        assert second['steps'][-1]['completed'] is False
        assert client.get(url+'/workflow/summary',params={'asset_id':'alien'}).status_code in (404,422)
    finally:client.__exit__(None,None,None)


def test_strict_release_gate_requires_real_business_agent_outcome():
    sys.path.insert(0,str(Path(__file__).parents[1]/'scripts'))
    from featurelab31_release_gate import check
    sha_a='a'*64;sha_b='b'*64
    backend={'status':'passed_backend_same_source_browser_pending',
        'source':{'sha256':sha_a}, 'artifact':{'sha256':sha_b},
        'evaluation':{'evaluated_artifact_sha256':sha_b},
        'qa_project_id':'p','source_native_asset_id':'s','materialized_native_asset_id':'m'}
    browser={'status':'passed','qa_project_id':'p','source_native_asset_id':'s',
        'materialized_native_asset_id':'m','source_sha256':sha_a,
        'artifact_sha256':sha_b,'panel_count':2,'websocket_opened':True,
        'refresh_restored':True,'native_bundle_verified':True,'installed_chrome':True}
    advanced={'status':'passed','source_sha256':sha_a}
    advanced.update({kind:{'status':'passed','source_sha256':sha_a,'artifact_sha256':sha_b}
        for kind in ('tsfresh','featuretools')})
    issues=check(backend,browser,advanced=advanced,require_advanced=True,
        business={},require_business=True)
    assert any('human-reviewed' in x for x in issues)
    business={'status':'passed','scope':'real_user_reviewed_agent_plans',
              'eligible_tasks':10,'agent_completion_rate':.85,'business_acceptance_rate':.75}
    assert check(backend,browser,advanced=advanced,business=business,require_business=True)==[]
    business['business_acceptance_rate']=.3
    assert any('business acceptance rate' in x for x in
        check(backend,browser,advanced=advanced,business=business,require_business=True))
