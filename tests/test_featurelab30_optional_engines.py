"""Real third-party integration tests; SKIP means not installed, never success."""
from __future__ import annotations
import importlib.util
import pandas as pd
import pytest
from test_featurelab30_advanced import dataframes,spec
from timepilot.feature_advanced import run_featuretools,run_tsfresh


@pytest.mark.skipif(importlib.util.find_spec('tsfresh') is None,reason='tsfresh optional package missing')
def test_real_tsfresh_minimal_pit_safe_statistics():
    source=pd.DataFrame({'cycle':['B','A','A','B'],'event_time':[
        '2026-01-01T09:02:00Z','2026-01-01T09:00:00Z',
        '2026-01-01T09:01:00Z','2026-01-01T09:03:00Z'],
        'available_at':['2026-01-01T09:02:10Z','2026-01-01T09:00:10Z',
        '2026-01-01T09:01:10Z','2026-01-01T09:03:10Z'],
        'cutoff':['2026-01-01T10:00:00Z']*4,'value':[9.,2.,4.,11.]})
    cfg={'engine':'tsfresh','sample':'cycle','event_time':'event_time','available_at':'available_at',
         'cutoff':'cutoff','values':['value'],'preset':'minimal',
         'limits':{'max_rows':100,'max_features':80,'max_seconds':90}}
    result,proof=run_tsfresh(source,cfg)
    assert len(result)==2
    assert proof['pit']=='verified_per_sample'
    assert '__sample_id' in result.columns
    assert any('mean' in c.lower() for c in result.columns)
    assert any('maximum' in c.lower() for c in result.columns)


@pytest.mark.skipif(importlib.util.find_spec('featuretools') is None,reason='Featuretools optional package missing')
def test_real_featuretools_asof_histories():
    d,e=dataframes()
    result,proof=run_featuretools(d,e,spec())
    assert len(result)==3 and '__decision_id' in result
    assert proof['eligible_pairs']==5 and proof['late_or_future_excluded']==2
    assert len(result.columns)>1
    assert result['__decision_id'].tolist()==['A','B','C']
