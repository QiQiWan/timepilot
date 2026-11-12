"""v0.30: same-source PIT gates and optional library adapter contracts.

Mocks only assert wrapper behavior; they do not count as a real tsfresh,
Featuretools, DuckDB or Perspective integration run.
"""
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock
import pandas as pd
import numpy as np
import pytest
from timepilot.feature_contracts import DataContract,FieldContract
from timepilot.feature_advanced import (
    TSFreshRecipe,FeaturetoolsRecipe,resolve_recipe,prepare_tsfresh,
    prepare_asof_pairs,run_tsfresh,run_featuretools,capabilities)
from timepilot.feature_advanced_service import AdvancedService,BoundedToolCall


def contract(names,roles,*,id='asset',restricted=()):
    fields=[]
    for i,name in enumerate(names):
        role=roles[i]
        temporal=role in ('event_time','available_at','prediction_time')
        numeric=role=='value'
        fields.append(FieldContract(field_id='fld_'+str(i),source_name=name,
            source_column_index=i,physical_type='TIMESTAMPTZ' if temporal else 'DOUBLE' if numeric else 'VARCHAR',
            semantic_type='datetime' if temporal else 'numeric' if numeric else 'id',
            role=role,confirmation='confirmed',classification='restricted' if name in restricted else 'internal'))
    return DataContract(asset_id=id,version_id='base',source_sha256='a'*64,revision=1,
                        state='confirmed',fields=fields,timezone='UTC',
                        event_time_field_id=next((f.field_id for f in fields if f.role=='event_time'),None),
                        available_at_field_id=next((f.field_id for f in fields if f.role=='available_at'),None),
                        entity_field_ids=[f.field_id for f in fields if f.role=='entity_key'])


def spec(primitive='mean'):
    return {'engine':'featuretools','decision_id':'decision_id','decision_entity':'device',
      'cutoff':'prediction_time','event_entity':'device','event_time':'event_time',
      'available_at':'available_at','values':['pressure'],'primitives':[primitive],
      'limits':{'max_rows':200,'max_pairs':100,'max_features':10,'max_seconds':30}}


def dataframes():
    decisions=pd.DataFrame({'decision_id':['A','B','C'], 'device':['D1','D1','D2'],
      'prediction_time':['2026-01-01T10:00:00Z','2026-01-01T12:00:00Z','2026-01-01T12:00:00Z']})
    events=pd.DataFrame({'device':['D1','D1','D1','D2'],
      'event_time':['2026-01-01T09:00:00Z','2026-01-01T09:30:00Z','2026-01-01T11:00:00Z','2026-01-01T08:00:00Z'],
      'available_at':['2026-01-01T09:10:00Z','2026-01-01T10:30:00Z','2026-01-01T11:05:00Z','2026-01-01T08:10:00Z'],
      'pressure':[10.,20.,30.,40.]})
    return decisions,events


def test_pit_asof_filters_late_and_future_by_each_decision():
    d,e=dataframes()
    p,c,proof=prepare_asof_pairs(d,e,spec())
    assert len(p)==3
    assert proof=={'candidate_pairs':7,'eligible_pairs':5,'late_or_future_excluded':2}
    assert c.groupby('__decision_id')['pressure'].apply(list).to_dict()=={'A':[10.],'B':[10.,20.,30.],'C':[40.]}
    assert not any(col in c for col in ('label','prediction_time','available_at'))


def test_large_join_is_rejected_before_merge(monkeypatch):
    d,e=dataframes()
    d=pd.concat([d]*40,ignore_index=True)
    d['decision_id']=[f'ID{x}' for x in range(len(d))]
    called=[]
    original=pd.DataFrame.merge
    def witness(self,*args,**kwargs):
        called.append(True)
        return original(self,*args,**kwargs)
    monkeypatch.setattr(pd.DataFrame,'merge',witness)
    with pytest.raises(ValueError,match='JOIN 前'):
        prepare_asof_pairs(d,e,spec())
    assert not called


def test_no_unconfirmed_restricted_or_label_fields_as_inputs():
    names=['cycle_id','event_time','available_at','prediction_time','pressure','target']
    roles=['record_id','event_time','available_at','prediction_time','value','label']
    c=contract(names,roles,restricted=('pressure',))
    r=TSFreshRecipe(asset_id='asset',sample_id_field_id='fld_0',event_time_field_id='fld_1',
      available_at_field_id='fld_2',prediction_time_field_id='fld_3',value_field_ids=['fld_4'])
    with pytest.raises(ValueError,match='restricted'):
        resolve_recipe(r,{'primary':c})
    r=r.model_copy(update={'value_field_ids':['fld_5']})
    with pytest.raises(ValueError,match='角色'):
        resolve_recipe(r,{'primary':c})


def test_pit_tsfresh_rejects_late_arrivals_and_inconsistent_cutoffs():
    sp={'sample':'cycle','event_time':'event_time','available_at':'available_at','cutoff':'cutoff',
        'values':['pressure'],'preset':'minimal','limits':{'max_rows':100,'max_features':30,'max_seconds':15}}
    data=pd.DataFrame({'cycle':['a','a'],'event_time':['2026-01-01 09:00Z','2026-01-01 09:10Z'],
        'available_at':['2026-01-01 09:01Z','2026-01-01 10:10Z'],
        'cutoff':['2026-01-01 10:00Z']*2,'pressure':[1,2]})
    with pytest.raises(ValueError,match='预测时点之后'):
        prepare_tsfresh(data,sp)
    data.loc[1,'available_at']='2026-01-01 09:11Z'
    assert len(prepare_tsfresh(data,sp))==2
    data.loc[1,'cutoff']='2026-01-01 11:00Z'
    with pytest.raises(ValueError,match='多个预测时点'):
        prepare_tsfresh(data,sp)


def test_tsfresh_adapter_uses_allowlisted_parameters(monkeypatch):
    class Minimal(dict):
        def __init__(self):super().__init__(mean=None)
    def extraction(df,**kwargs):
        assert kwargs['n_jobs']==1
        assert kwargs['column_id']=='cycle'
        assert list(kwargs['default_fc_parameters'])==['mean']
        return df.groupby('cycle')['sensor_0'].mean().to_frame('sensor_0__mean')
    monkeypatch.setitem(sys.modules,'tsfresh',SimpleNamespace(extract_features=extraction))
    monkeypatch.setitem(sys.modules,'tsfresh.feature_extraction',SimpleNamespace(MinimalFCParameters=Minimal,EfficientFCParameters=Minimal))
    sp={'sample':'cycle','event_time':'event_time','available_at':'available_at','cutoff':'cutoff',
        'values':['pressure'],'preset':'minimal','limits':{'max_rows':100,'max_features':30,'max_seconds':15}}
    data=pd.DataFrame({'cycle':['a','a'],'event_time':['2026-01-01 09:00Z','2026-01-01 09:10Z'],
        'available_at':['2026-01-01 09:01Z','2026-01-01 09:11Z'],
        'cutoff':['2026-01-01 10:00Z']*2,'pressure':[1,2]})
    frame,pit=run_tsfresh(data,sp)
    assert frame['sensor_0__mean'].tolist()==[1.5]
    assert pit['pit']=='verified_per_sample'


def test_featuretools_adapter_receives_asof_filtered_rows(monkeypatch):
    class FakeEntitySet:
        def __init__(self,id):self.frames={}
        def add_dataframe(self,*,dataframe_name,dataframe,index,time_index):
            self.frames[dataframe_name]=dataframe.copy();return self
        def add_relationship(self,*a):return self
    def dfs(**kw):
        assert kw['n_jobs']==1 and kw['max_depth']==2
        assert kw['agg_primitives']==['mean']
        parent=kw['entityset'].frames['decisions'];child=kw['entityset'].frames['events']
        v=child.groupby('__decision_id')['pressure'].mean()
        output=parent[['__decision_id']].copy().set_index('__decision_id')
        output['MEAN(events.pressure)']=v
        return output,['mock_feature']
    monkeypatch.setitem(sys.modules,'featuretools',SimpleNamespace(EntitySet=FakeEntitySet,dfs=dfs))
    d,e=dataframes()
    frame,proof=run_featuretools(d,e,spec())
    assert frame['MEAN(events.pressure)'].tolist()==[10.,20.,40.]
    assert proof['late_or_future_excluded']==2


def test_explicit_agent_tool_approval_and_disallowed_tool():
    service=AdvancedService(MagicMock(),MagicMock(),MagicMock(),MagicMock(),MagicMock())
    with pytest.raises(ValueError,match='未授权工具'):
        service.tool_call('project',BoundedToolCall(tool_id='execute.python'))
    with pytest.raises(ValueError,match='明确确认'):
        service.submit('project',None,'abc',confirmed=False)


def test_invalid_algorithms_and_budgets_fail_closed():
    with pytest.raises(ValueError):
        TSFreshRecipe(asset_id='s',sample_id_field_id='fld_0',event_time_field_id='fld_1',
          available_at_field_id='fld_2',prediction_time_field_id='fld_3',value_field_ids=['fld_1'])
    with pytest.raises(ValueError):
        FeaturetoolsRecipe(decisions_asset_id='same',events_asset_id='same',
          decision_id_field_id='a',decision_entity_field_id='b',prediction_time_field_id='c',
          event_entity_field_id='d',event_time_field_id='e',event_available_at_field_id='f',
          event_value_field_ids=['g'])


def test_cross_type_decision_identifiers_cannot_collapse_after_normalization():
    decisions,events=dataframes()
    decisions['decision_id']=pd.Series([1,'1','3'],dtype=object)
    with pytest.raises(ValueError,match='主键'):
        prepare_asof_pairs(decisions,events,spec())


def test_missing_dependences_return_clear_engine_status():
    c=capabilities()
    assert set(('tsfresh','featuretools','duckdb','pyarrow','perspective'))<=set(c['engines'])
    assert all(isinstance(x,bool) for x in c['engines'].values())


def test_http_unconfirmed_source_and_forbidden_tool(tmp_path):
    from test_conversational_bi_v0231 import _env
    app,client,pid,aid,_=_env(tmp_path)
    base=f'/api/v1/projects/{pid}/feature-lab/advanced'
    try:
        response=client.get(base+'/capabilities')
        assert response.status_code==200,response.text
        assert 'tsfresh' in response.json()['engines']
        assert client.post(base+'/tool-call',json={'tool_id':'system.shell','confirm':True}).status_code==422
        assert client.post(base+'/tool-call',json={'tool_id':'feature.advanced.submit','confirm':False}).status_code==422
        # Valid source is mandatory: no guessed asset or unsigned data can be used.
        recipe={'engine':'tsfresh','asset_id':aid,'sample_id_field_id':'fld_fake',
                'event_time_field_id':'fld_fake','available_at_field_id':'fld_fake',
                'prediction_time_field_id':'fld_fake','value_field_ids':['fld_fake']}
        response=client.post(base+'/validate',json=recipe)
        assert response.status_code in (409,422)
        assert not any(x.get('status')=='succeeded' for x in app.state.feature_jobs.list_jobs(pid,aid))
    finally:
        client.__exit__(None,None,None)


def test_migration_v32_to_v33_preserves_existing_data(tmp_path):
    from timepilot.project_migrations import migrate,TARGET_SCHEMA
    from test_conversational_bi_v0231 import _env
    app,client,pid,asset,_=_env(tmp_path)
    try:
        with app.state.projects._lock,app.state.projects._db() as db:
            before=db.execute('SELECT COUNT(*) FROM projects').fetchone()[0]
            db.execute('DROP TABLE IF EXISTS feature_advanced_artifacts')
            db.execute('DROP INDEX IF EXISTS idx_feature_advanced_artifacts')
            db.execute('UPDATE project_schema SET version=32')
            migrate(db)
            assert db.execute('SELECT version FROM project_schema').fetchone()[0]==TARGET_SCHEMA==34
            assert db.execute('SELECT COUNT(*) FROM projects').fetchone()[0]==before
            assert db.execute("SELECT name FROM sqlite_master WHERE name='feature_advanced_artifacts'").fetchone()
    finally:client.__exit__(None,None,None)


def test_advanced_service_load_scope_is_call_local():
    """Concurrent project jobs must never read a previous job's project scope."""
    from unittest.mock import MagicMock
    native=MagicMock()
    semantic=MagicMock()
    semantic.source.return_value={'sha256':'a'*64,'row_count':1,
        'columns':[{'name':'pressure'}]}
    cn=MagicMock();cn.revision=3;cn.state='confirmed'
    semantic.latest.return_value=cn
    connection=MagicMock()
    connection.__enter__.return_value.execute.return_value.df.return_value=pd.DataFrame({'pressure':[1.]})
    native._connection.return_value=connection
    service=AdvancedService(MagicMock(),native,semantic,MagicMock(),MagicMock())
    pin={'asset_id':'a1','version_id':'base','sha256':'a'*64,'contract_revision':3}
    assert service._load('project_A',pin,['pressure'],10).shape==(1,1)
    assert service._load('project_B',pin,['pressure'],10).shape==(1,1)
    assert [call.args[0] for call in semantic.source.call_args_list]==['project_A','project_B']
    assert not hasattr(service,'_pid') and not hasattr(service,'_limits')


def test_pit_converts_each_source_confirmed_timezone_before_join():
    d,e=dataframes()
    # A decision at 10:00 UTC is 18:00 local in Shanghai. Events at 17:00
    # Shanghai and 10:30 UTC are respectively eligible/ineligible.
    d=d.iloc[:1].copy()
    d['prediction_time']=['2026-01-01 10:00:00']
    e=e.iloc[:2].copy()
    e['event_time']=['2026-01-01 17:00:00','2026-01-01 10:30:00']
    e['available_at']=['2026-01-01 17:01:00','2026-01-01 10:31:00']
    cfg=spec();cfg['decision_timezone']='UTC';cfg['event_timezone']='Asia/Shanghai'
    p,c,proof=prepare_asof_pairs(d,e,cfg)
    assert len(c)==2, 'Both 17:xx Shanghai and 10:xx Shanghai are before 10:00 UTC'
    assert proof['eligible_pairs']==2
    e.loc[e.index[1],'event_time']='2026-01-01 18:30:00'
    e.loc[e.index[1],'available_at']='2026-01-01 18:31:00'
    p,c,proof=prepare_asof_pairs(d,e,cfg)
    assert c['pressure'].tolist()==[10.]
    assert proof['late_or_future_excluded']==1
