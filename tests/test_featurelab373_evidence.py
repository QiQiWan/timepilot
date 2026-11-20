"""v0.37.3 fixed, no-LLM evidence and non-fabrication gates."""
from __future__ import annotations
from copy import deepcopy
from types import SimpleNamespace
import hashlib

import pytest
from timepilot.feature_evidence_v0373 import (
    make_claim,dashboard_explanations,chart_explanations,dashboard_markdown,
)
from timepilot.feature_insights import build_insight_brief

SRC='a'*64
SCOPE='b'*64


def dashboard():
    return {'asset_id':'asset','version_id':'base','source_sha256':SRC,
        'scope_sha256':SCOPE,'contract_revision':7,'rows_total':100,'rows_filtered':80,
        'metrics':{'records':80,'source_records':100,'missing_cells':12},
        'filters':[{'field_id':'unit','op':'eq','value':'A'}],
        'fields':[{'field_id':'measure','name':'A1','numeric':True,'non_null':68,
                   'null_count':12,'null_rate':.15,'median':4.0,'p90':10.0},
                  {'field_id':'other','name':'A2','numeric':True,'non_null':80,
                   'null_count':0,'null_rate':0,'median':3.0,'p90':3.5}],
        'correlations':[{'x_field_id':'measure','y_field_id':'other',
                         'pearson':.87,'pair_count':68}]}


def test_every_dashboard_claim_has_server_numeric_evidence_and_id():
    r=dashboard_explanations(dashboard())
    assert r['status']=='supported'
    assert {c['code'] for c in r['claims']}=={
        'filtered-coverage','missing-top','strong-linear-pair','observed-quantiles'}
    for claim in r['claims']:
        assert len(claim['id'])==64
        assert claim['status']=='supported'
        assert claim['scope']['source_sha256']==SRC
        assert claim['scope']['scope_sha256']==SCOPE
        assert claim['evidence']
        for e in claim['evidence']:
            assert isinstance(e['value'],(float,int))
            assert e['path']
    assert r['predictive_gain_evaluated'] is False
    assert '收益' not in ''.join(c['observation'] for c in r['claims'])


def test_report_uses_exact_same_claim_ids_and_bytes():
    source=dashboard();r=dashboard_explanations(source)
    output=dashboard_markdown(source)
    assert output['claim_ids']==[x['id'] for x in r['claims']]
    assert hashlib.sha256(output['markdown'].encode()).hexdigest()==output['report_sha256']
    assert output['scope_sha256']==SCOPE
    assert '独立测试' not in output['markdown']
    assert '预测收益' in output['markdown']
    assert 'fields[0].null_count' in output['markdown']


def test_filter_change_rebinds_evidence_identity():
    a=dashboard();b=dashboard();b['scope_sha256']='c'*64
    out1=dashboard_explanations(a);out2=dashboard_explanations(b)
    assert [x['id'] for x in out1['claims']]!=[x['id'] for x in out2['claims']]


@pytest.mark.parametrize('mutator',[
    lambda d:d.update(source_sha256='not-a-hash'),
    lambda d:d.update(scope_sha256=None),
    lambda d:d.update(rows_filtered=101),
    lambda d:d['metrics'].update(records=999),
    lambda d:d['metrics'].update(missing_cells=1),
    lambda d:d['fields'][0].update(non_null=70),
])
def test_inconsistent_or_stale_statistics_fail_closed(mutator):
    data=dashboard();mutator(data)
    out=dashboard_explanations(data)
    assert out['status']=='insufficient_evidence'
    assert out['claims']==[]
    with pytest.raises(ValueError):dashboard_markdown(data)


def test_no_evidence_returns_no_invented_conclusion():
    data=dashboard();data.update(rows_total=0,rows_filtered=0)
    data['metrics'].update(records=0,source_records=0,missing_cells=0)
    for f in data['fields']:
        f.update(non_null=0,null_count=0,median=None,p90=None)
    data['correlations']=[]
    out=dashboard_explanations(data)
    assert out['status']=='insufficient_evidence' and not out['claims']
    assert '无法判断' in out['message']


def test_chart_histogram_percentage_is_from_complete_bins():
    data={'source_sha256':SRC,'contract_revision':7,'mode':'distribution','feature_name':'Pressure',
      'quality':{'finite_count':20},'points':[{'label':'0-1','count':16},{'label':'1-2','count':4}]}
    out=chart_explanations(data)
    assert out['status']=='supported'
    assert '80.0%' in out['claims'][0]['observation']
    assert out['claims'][0]['evidence'][1]['value']==20
    data['quality']['finite_count']=21
    assert chart_explanations(data)['status']=='insufficient_evidence'


def test_series_spread_uses_actual_bucket_min_max_not_fake_trend():
    data={'source_sha256':SRC,'mode':'series','feature_name':'Temperature','points':[
      {'x':'2026-01-01','min':4,'max':6},{'x':'2026-01-02','min':0,'max':20}]}
    out=chart_explanations(data)
    assert out['claims'][0]['evidence']==[
      {'path':'points[1].min','value':0.0,'unit':'value'},
      {'path':'points[1].max','value':20.0,'unit':'value'}]
    assert '20' in out['claims'][0]['observation']
    assert out['predictive_gain_evaluated'] is False


def test_chart_rejects_missing_source_or_bad_values():
    assert chart_explanations({'mode':'distribution','points':[{'count':12}]})['status']=='insufficient_evidence'
    data={'source_sha256':SRC,'mode':'series','points':[{'x':'A','min':None,'max':float('nan')}]}
    assert chart_explanations(data)['status']=='insufficient_evidence'
    with pytest.raises(ValueError):make_claim(data,'fake','X','unfounded', [('points[0].max',float('nan'))])


def test_model_comparison_three_arms_verified_and_no_label_guard():
    from test_featurelab355_evidence import preview,proof,contract
    args={'source_sha256':SRC,'contract_revision':5,'session':preview(True),
          'delivery':proof(),'contract':contract(1)}
    result=build_insight_brief(**args)
    model=result['model'];assert model['status']=='promising_in_validation'
    assert model['value_scope']=='outer_validation_folds_only'
    assert model['test_set_claimed'] is False
    assert len(model['variant_deltas'])==2
    assert model['variant_deltas'][0]['positive_folds']==3
    assert model['variant_deltas'][1]['positive_folds']==3
    args['contract']=contract(0)
    no_label=build_insight_brief(**args)
    assert no_label['model']['comparisons']==[]
    assert 'variant_deltas' not in no_label['model']


def test_model_variant_regression_keeps_negative_improvement():
    from test_featurelab355_evidence import preview,proof,contract
    session=preview(True)
    for f in session['evaluation']['report']['folds']:
        # Keep the verified comparison for all features, but selected worsens.
        m=f['model_metrics']['linear']
        m['selected_features']['mae']=m['raw_only']['mae']+0.5
    result=build_insight_brief(source_sha256=SRC,contract_revision=5,
        session=session,delivery=proof(),contract=contract(1))
    selected=result['model']['variant_deltas'][1]
    assert selected['mean_delta']<0
    assert selected['verdict']=='validation_regression'


def test_report_route_has_server_scoped_contract():
    from timepilot.api import create_app
    from fastapi.testclient import TestClient
    from tempfile import TemporaryDirectory
    with TemporaryDirectory() as td:
        with TestClient(create_app(home=td,embedded_worker=False)) as client:
            path='/api/v1/projects/{pid}/feature-lab/data-analysis/dashboard/report'
            assert path in {str(r.path) for r in client.app.routes}
            response=client.post('/api/v1/projects/unknown/feature-lab/data-analysis/dashboard/report',
                json={'asset_id':'not-found','filters':[]})
            assert response.status_code in (404,409,422)


def test_series_original_vs_feature_only_when_units_compatible():
    doc={'source_sha256':SRC,'mode':'series','candidate_id':'c1',
         'candidate_fingerprint':'d'*64,'axis_compatible':True,
         'feature_unit':'kPa','source_unit':'kPa',
         'points':[{'min':1,'max':3,'value':2,'original':1,'x':'A'},
                   {'min':1,'max':5,'value':3,'original':2,'x':'B'},
                   {'min':4,'max':6,'value':5,'original':7,'x':'C'}]}
    proof=chart_explanations(doc)
    codes={c['code'] for c in proof['claims']}
    assert 'bucket-mean-difference' in codes
    c=next(x for x in proof['claims'] if x['code']=='bucket-mean-difference')
    assert c['scope']['candidate_fingerprint']=='d'*64
    assert c['evidence'][0]['unit']=='kPa'
    doc['axis_compatible']=False
    assert 'bucket-mean-difference' not in {c['code'] for c in chart_explanations(doc)['claims']}


def test_group_comparison_does_not_invent_machine_causes():
    doc={'source_sha256':SRC,'mode':'group','feature_name':'pressure','points':[
        {'label':'machine_A','count':30,'mean':9},
        {'label':'machine_B','count':34,'mean':16}]}
    out=chart_explanations(doc)
    assert 'group-mean-spread' in {c['code'] for c in out['claims']}
    assert all('故障原因' not in c['observation'] for c in out['claims'])
    assert out['predictive_gain_evaluated'] is False
