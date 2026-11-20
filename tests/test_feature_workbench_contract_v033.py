"""v0.33.0/v0.33.1 contract and SQL compilation tests (no DB dependency)."""
from __future__ import annotations

import pytest

from timepilot.feature_contracts import DataContract, FeatureDefinition, FeatureSpecV2
from timepilot.feature_compiler import FeatureCompiler
from timepilot.feature_candidates import propose
from timepilot.feature_operator_registry import OPERATORS, compatible_fields, validate_operator
from timepilot.feature_workbench import FeatureWorkbenchCharts


def contract(*, group=False):
    fields=[
        {'field_id':'fld_pressure','source_name':'pressure','source_column_index':0,
         'physical_type':'DOUBLE','semantic_type':'numeric','role':'value','confirmation':'confirmed'},
        {'field_id':'fld_category','source_name':'factory_mode','source_column_index':1,
         'physical_type':'VARCHAR','semantic_type':'category','role':'regime','confirmation':'confirmed'},
        {'field_id':'fld_label','source_name':'target','source_column_index':2,
         'physical_type':'VARCHAR','semantic_type':'category','role':'label','confirmation':'confirmed'},
        {'field_id':'fld_secrets','source_name':'private_mode','source_column_index':3,
         'physical_type':'VARCHAR','semantic_type':'category','role':'value',
         'classification':'restricted','confirmation':'confirmed'},
    ]
    if group:
        fields.extend([
            {'field_id':'fld_order','source_name':'record_sequence','source_column_index':4,
             'physical_type':'INTEGER','semantic_type':'numeric','role':'order_index','confirmation':'confirmed'},
            {'field_id':'fld_device','source_name':'device','source_column_index':5,
             'physical_type':'VARCHAR','semantic_type':'id','role':'entity_key','confirmation':'confirmed'},
        ])
    return DataContract(asset_id='source1',version_id='base',source_sha256='a'*64,
        state='confirmed',revision=2,fields=fields,
        entity_field_ids=['fld_device'] if group else [])


def feature(op,inputs,params=None):
    return FeatureDefinition(feature_id='feature1',output_name='feature_result',
        operator_id=op,operator_version=OPERATORS[op].version,
        input_field_ids=inputs,parameters=params or {})


def compiler(c,f):
    return FeatureCompiler(c,FeatureSpecV2(name='test',source={
        'asset_id':c.asset_id,'version_id':c.version_id,'source_sha256':c.source_sha256},
        contract_revision=c.revision,features=[f]))


def test_numeric_feature_executable_sql_is_row_local_and_not_label():
    c=contract();f=feature('transform.abs',['fld_pressure'])
    expr=compiler(c,f).expression(f)
    assert 'ABS' in expr and 'pressure' in expr
    assert 'target' not in expr and 'private_mode' not in expr


def test_categorical_feature_is_safe_confirmed_only_and_sql_literal_is_escaped():
    c=contract();allowed=compatible_fields(c,'category')
    assert allowed==['fld_category']
    f=feature('category.is_value',['fld_category'],{'category_value':"A'B"})
    assert validate_operator(f)
    sql=compiler(c,f).expression(f)
    assert "A''B" in sql
    assert 'CASE WHEN' in sql and 'IS NULL THEN NULL' in sql
    for fid in ('fld_label','fld_secrets'):
        with pytest.raises(ValueError,match='普通类别字段'):
            compiler(c,f.model_copy(update={'input_field_ids':[fid]}))
    with pytest.raises(ValueError,match='类别值'):
        validate_operator(f.model_copy(update={'parameters':{'category_value':'a\x00z'}}))


def test_categorical_candidate_generation_uses_only_profile_evidence():
    c=contract()
    candidates=propose(c,template='basic',budget=8,category_values={
        'fld_category':['A','B'], 'fld_label':['good'], 'fld_secrets':['secret']})
    cat=[x for x in candidates if x['operator_id']=='category.is_value']
    assert len(cat)==2 and {x['parameters']['category_value'] for x in cat}=={'A','B'}
    assert all(x['input_field_ids']==['fld_category'] for x in cat)
    assert all('label' not in x['display_name'] for x in candidates)


def test_temporal_window_sql_uses_confirmed_order_and_entity_partition():
    c=contract(group=True)
    f=feature('rolling.mean',['fld_pressure'],{'window':2,'min_periods':1,
        'order_field_id':'fld_order','partition_field_ids':['fld_device']})
    expression=compiler(c,f).expression(f)
    assert 'PARTITION BY "device"' in expression
    assert 'ORDER BY "record_sequence", "__tp_rowid"' in expression
    assert 'ROWS BETWEEN 1 PRECEDING AND CURRENT ROW' in expression
    with pytest.raises(ValueError,match='跨设备混合'):
        compiler(c,f.model_copy(update={'parameters':{
           'window':2,'min_periods':1,'order_field_id':'fld_order','partition_field_ids':[]}})).expression(
              f.model_copy(update={'parameters':{
                  'window':2,'min_periods':1,'order_field_id':'fld_order','partition_field_ids':[]}}))


def test_chart_endpoint_rejects_stale_source_and_invalid_candidate_before_sql():
    class Auto:
        def get(self, pid, sid):
            return {'stale':True,'session_id':sid}
    class Fake:
        pass
    charts=FeatureWorkbenchCharts(Auto(),Fake(),Fake(),Fake())
    with pytest.raises(ValueError,match='来源或契约已变化'):
        charts.candidate_chart('p1','s1','c1')


def test_chart_endpoint_rejects_unsupported_mode_without_query():
    class Auto:
        def get(self,pid,sid):
            return {'stale':False,'session_id':sid}
    charts=FeatureWorkbenchCharts(Auto(),None,None,None)
    with pytest.raises(ValueError,match='不支持的特征图表类型'):
        charts.candidate_chart('p','s','c',mode='user-supplied-sql')
