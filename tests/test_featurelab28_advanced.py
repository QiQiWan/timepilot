"""v0.28 contract, operator, formula and HTTP safety tests without DuckDB."""
from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from test_conversational_bi_v0231 import _env
from test_featurelab27_candidates import _url
from timepilot.feature_contracts import DataContract, FieldContract, FeatureDefinition, FeatureSpecV2
from timepilot.feature_compiler import FeatureCompiler
from timepilot.feature_custom import validate_custom_definition
from timepilot.feature_expression import compile_formula, validate_formula
from timepilot.feature_operator_registry import OPERATORS, discover, validate_operator
from timepilot.feature_candidates import propose


def _contract():
    return DataContract(asset_id='industrial-01', version_id='base', source_sha256='f'*64,
        state='confirmed', revision=2, timezone='Asia/Shanghai',
        entity_field_ids=['fld_device'], event_time_field_id='stamp', fields=[
            FieldContract(field_id='fld_device', source_name='device_id', source_column_index=0,
                physical_type='VARCHAR', semantic_type='id', role='entity_key', confirmation='confirmed'),
            FieldContract(field_id='stamp', source_name='event_time', source_column_index=1,
                physical_type='TIMESTAMP', semantic_type='datetime', role='event_time', confirmation='confirmed'),
            FieldContract(field_id='pressure_a', source_name='pressure_A', source_column_index=2,
                physical_type='DOUBLE', semantic_type='numeric', role='value', unit='kPa', confirmation='confirmed'),
            FieldContract(field_id='pressure_b', source_name='pressure_B', source_column_index=3,
                physical_type='DOUBLE', semantic_type='numeric', role='value', unit='kPa', confirmation='confirmed'),
            FieldContract(field_id='fld_temp', source_name='temperature', source_column_index=4,
                physical_type='DOUBLE', semantic_type='numeric', role='value', unit='degC', confirmation='confirmed'),
            FieldContract(field_id='target', source_name='ng_flag', source_column_index=5,
                physical_type='INTEGER', semantic_type='numeric', role='label', confirmation='confirmed'),
        ])


def _one(op, input_fields=None, parameters=None):
    input_fields=input_fields or ['pressure_a']
    return FeatureDefinition(feature_id='f1', output_name='result', operator_id=op,
        operator_version=OPERATORS[op].version, input_field_ids=input_fields,
        parameters=parameters if parameters is not None else (
            {'window':4,'min_periods':2,'order_field_id':'stamp'} if op.startswith('rolling.') else
            {'lag':1} if op=='relative.change' else
            {'expression':'safe_div(x0-x1,x1)'} if op=='expression.safe' else {}))


def _spec(c, feat):
    return FeatureSpecV2(name='industrial', source={'asset_id':c.asset_id,
      'version_id':'base','source_sha256':c.source_sha256},contract_revision=c.revision,
      features=[feat])


@pytest.mark.parametrize('formula',[
    '__import__("os").system("id")', 'x0.__class__', 'x0[0]', 'x0**2',
    'x0 // x1','sum(x0)','x9+3','x1+3','x0 if x0>1 else 0',
    'x0;DROP TABLE data','[x0 for x in (1,2)]',
    'round(x0,2,3)', 'sqrt(x0, y=1)', '1e1000+x0',
])
def test_formula_rejects_injection_and_unsupported_syntax(formula):
    with pytest.raises(ValueError): validate_formula(formula,1)


def test_formula_guards_div_zero_and_math_domain():
    expr=compile_formula('safe_div(x0 - x1, x1) + sqrt(x0) + log1p(x0)',
                         ['"pressure_A"','"pressure_B"'],['kPa','kPa'])
    assert expr.count('NULLIF')>=1
    assert 'SQRT' in expr and 'LN(1 +' in expr
    assert 'CASE WHEN' in expr
    assert 'DROP TABLE' not in expr


def test_formula_requires_unit_match_and_valid_ref():
    with pytest.raises(ValueError,match='单位'):
        compile_formula('x0 - x1',['"pressure_A"','"temperature"'],['kPa','degC'])
    with pytest.raises(ValueError,match='至少引用'):
        validate_formula('1234',1)
    with pytest.raises(ValueError,match='字段'):
        validate_custom_definition(_contract(),['stamp'],'x0')
    with pytest.raises(ValueError,match='字段'):
        validate_custom_definition(_contract(),['target'],'x0')
    with pytest.raises(ValueError):
        validate_custom_definition(_contract(),['pressure_a','pressure_a'],'x0+x1')


def test_formula_output_is_deterministic_and_uses_verified_columns():
    contract=_contract()
    result=validate_custom_definition(contract,['pressure_a','pressure_b'],
                                       'safe_div(x0-x1,x1)','两路压力相对差')
    again=validate_custom_definition(contract,['pressure_a','pressure_b'],
                                      'safe_div(x0-x1,x1)','两路压力相对差')
    assert result==again
    assert result['formula']['execution']=='duckdb_whitelist_only'
    assert result['definition']['output_name'].startswith('expr_')
    assert [f['alias'] for f in result['field_mapping']]==['x0','x1']
    assert not result['calculates_now']
    assert FeatureCompiler(contract,_spec(contract,FeatureDefinition.model_validate(
        result['definition']))).expression(FeatureDefinition.model_validate(result['definition']))


@pytest.mark.parametrize('operator',[
    'rolling.sum','rolling.median','rolling.p90','rolling.range','rolling.cv',
    'rolling.zscore','relative.change','transform.log1p','transform.abs','transform.sqrt'])
def test_advanced_operators_generate_bounded_sql(operator):
    contract=_contract(); definition=_one(operator)
    assert validate_operator(definition)
    sql=FeatureCompiler(contract,_spec(contract,definition)).expression(definition)
    assert 'pressure_A' in sql
    assert ';' not in sql and 'DROP' not in sql
    if operator.startswith('rolling.'):
        assert 'PARTITION BY "device_id"' in sql
        assert 'ORDER BY "event_time"' in sql
        assert 'ROWS BETWEEN 3 PRECEDING' in sql
    if operator=='rolling.p90':assert 'QUANTILE_CONT' in sql
    if operator in ('rolling.cv','rolling.zscore'):assert 'NULLIF' in sql
    if operator=='relative.change':assert 'LAG(' in sql and 'NULLIF' in sql


def test_advanced_proposal_skips_label_and_time_with_bounded_budget():
    contract=_contract()
    items=propose(contract,template='advanced',budget=10)
    assert 1<=len(items)<=10
    assert all(x['input_field_ids'][0] in ('pressure_a','pressure_b','fld_temp') for x in items)
    assert any(x['operator_id']=='rolling.median' for x in items)
    assert all(x['parameters']['order_field_id']=='stamp' for x in items)
    assert len({x['output_name'] for x in items})==len(items)


def test_api_custom_expression_and_stale_pins(tmp_path):
    app,client,pid,aid,_=_env(tmp_path)
    api=_url(pid)
    try:
        doc=client.get(api+'/contracts',params={'asset_id':aid}).json()
        by_name={f['source_name']:f['field_id'] for f in doc['fields']}
        source=app.state.feature_semantic.source(pid,aid,'base')
        payload={'asset_id':aid,'source_sha256':source['sha256'],
                 'contract_revision':doc['revision'],'input_field_ids':[by_name['A1'],by_name['A2']],
                 'expression':'safe_div(x0-x1, x1)','display_name':'相对偏差'}
        accepted=client.post(api+'/expressions/validate',json=payload)
        assert accepted.status_code==200,accepted.text
        assert accepted.json()['definition']['operator_id']=='expression.safe'
        assert accepted.json()['field_mapping'][0]['source_name']=='A1'
        assert client.post(api+'/expressions/validate',json={**payload,
                           'source_sha256':'0'*64}).status_code==409
        assert client.post(api+'/expressions/validate',json={**payload,
                           'contract_revision':999}).status_code==409
        assert client.post(api+'/expressions/validate',json={**payload,
                           'input_field_ids':[by_name['Time']]}).status_code==422
        bad=client.post(api+'/expressions/validate',json={**payload,
                        'expression':'x0.__class__'})
        assert bad.status_code==422
        advanced=client.post(api+'/candidate-sessions',json={
            'asset_id':aid,'request_id':'advanced-28-'+str(uuid4()),'template':'advanced','budget':9})
        assert advanced.status_code==201,advanced.text
        assert advanced.json()['generator_version']=='0.28.0'
        assert 'rolling.median' in [x['operator_id'] for x in advanced.json()['candidates']]
        sid=advanced.json()['session_id']
        revision=advanced.json()['revision']
        saved=client.patch(api+'/candidate-sessions/'+sid,json={
            'expected_revision':revision,'layout':{'mode':'custom','chart_mode':'scatter'}})
        assert saved.status_code==200,saved.text
        assert saved.json()['layout']['chart_mode']=='scatter'
        assert saved.json()['layout']['mode']=='custom'
        latest=client.get(api+'/candidate-sessions/'+sid+'/latest-preview')
        assert latest.status_code==200 and latest.json()['job'] is None
    finally:client.__exit__(None,None,None)


def test_front_end_has_three_plots_safe_formula_and_artifact_restore():
    root=Path(__file__).resolve().parents[1]/'src'/'timepilot'/'static'
    view=(root/'feature-builder-ui.js').read_text()
    shell=(root/'feature-shell.js').read_text()
    for name in ('trend','distribution','scatter','tp-fl2-add-formula',
                 'tp-fl2-chart-tabs','samplePairs'):
        assert name in view
    for name in ('expressions/validate','latest-artifact','latest-preview','chart-scatter',
                 'mode-custom'):
        assert name in shell
    assert 'eval(' not in shell and 'eval(' not in view
