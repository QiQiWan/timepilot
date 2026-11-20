"""v0.35.3 evidence-only explanations and unit-safe chart comparisons."""
from timepilot.feature_chart_insights import explain_chart,feature_unit


def test_series_insight_reports_actual_range_and_explicit_device_warning():
    doc={'mode':'series','field_name':'Pressure','field_id':'pressure','order_field':'Time',
         'population_count':506,'source_sha256':'a'*64,
         'points':[{'x':'2026-01-01','value':5,'min':2,'max':10,'count':100},
                   {'x':'2026-01-02','value':9,'min':1,'max':24,'count':120},
                   {'x':'2026-01-03','value':3,'min':2,'max':4,'count':90}]}
    x=explain_chart(doc,unit='bar')
    assert x['scope']['records']==506
    assert x['axes']['y']=='Pressure（bar）'
    assert '9' in x['observations'][0]['text']
    assert x['observations'][1]['evidence']==['bucket_1']
    assert '设备' in ' '.join(x['cautions'])
    assert x['evidence_refs']==['a'*64]


def test_distribution_and_category_insights_do_not_invent_predictive_gain():
    for mode in ['distribution','category']:
        x=explain_chart({'mode':mode,'field_name':'A1','points':[
            {'label':'A','count':25},{'label':'B','count':12}]})
        assert x['observations']
        assert '预测精度' not in str(x)
        assert '25' in str(x)


def test_different_unit_is_explicit_and_not_overlaid():
    out,ok=feature_unit('relative.change','kPa')
    assert out=='1' and ok is False
    assert feature_unit('rolling.mean','kPa')==('kPa',True)
    assert feature_unit('arithmetic.binary','kPa')[1] is False
    x=explain_chart({'mode':'series','field_id':'f','points':[{'x':0,'value':1}]},
                    unit=out,original_unit='kPa',axis_compatible=ok)
    assert '量纲' in ' '.join(x['cautions'])
