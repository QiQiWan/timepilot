"""v0.37.8 metrics denominator, invalid counts, SHA verification invalidation."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from timepilot.dashboard_metrics_contract import DashboardMetrics, normalized_metrics
from timepilot.native_bi import NativeBIService

ROOT=Path(__file__).resolve().parents[1]


def example():
    return {'records':5,'source_records':10,'columns':32,'numeric_fields':20,
            'selected_fields':2,'missing_cells':1,'invalid_numeric_cells':2,
            'non_finite_numeric_cells':1,'conversion_failed_cells':1,
            'missing_percent':30.0,'coverage_percent':50.0,'confirmed_time':False,
            'metric_scope':{'field_ids':['a','b'],'rows':5,'denominator_cells':10,
                'field_limit':24}}


def test_dashboard_metrics_schema_is_generated_from_live_pydantic_model():
    source=json.loads((ROOT/'contracts/featurelab/dashboard_metrics.schema.json').read_text())
    assert source==DashboardMetrics.model_json_schema()
    assert source['additionalProperties'] is False
    data=normalized_metrics(example())
    assert data['missing_percent']==30
    assert data['metric_scope']['missing_policy']=='sql_null_plus_non_finite_plus_conversion_failed'


@pytest.mark.parametrize('key,bad_value',[('invalid_numeric_cells',3),('missing_cells',10),('selected_fields',3),('missing_percent',101)])
def test_metrics_fail_closed_when_counters_disagree(key,bad_value):
    data=example();data[key]=bad_value
    with pytest.raises(ValidationError):DashboardMetrics.model_validate(data)


def test_zero_denominator_has_null_percent():
    data=example();data.update(records=0,selected_fields=0,missing_cells=0,
        invalid_numeric_cells=0,non_finite_numeric_cells=0,conversion_failed_cells=0,
        missing_percent=None,coverage_percent=0)
    data['metric_scope'].update(rows=0,field_ids=[],denominator_cells=0)
    assert normalized_metrics(data)['missing_percent'] is None


def test_sha_cache_reuses_only_unchanged_success_and_fails_tamper(tmp_path,monkeypatch):
    from timepilot import native_bi
    asset='unit-asset'
    path=tmp_path/'immutable.parquet';path.write_bytes(b'original-asset-bytes')
    expected=hashlib.sha256(path.read_bytes()).hexdigest()
    service=object.__new__(NativeBIService)
    service._verify_lock=__import__('threading').RLock()
    service._verify_cache={}
    service._verify_stats={'hits':0,'hashes':0,'hash_bytes_read':0}
    service._asset=lambda a:SimpleNamespace(id=a,sha256=expected,file_bytes=len(b'original-asset-bytes'))
    service._path=lambda a:path
    original=native_bi.sha256_file
    calls=Counter()
    def digest(p):calls['read']+=1;return original(p)
    monkeypatch.setattr(native_bi,'sha256_file',digest)
    assert service.verify(asset)['valid'] is True
    assert service.verify(asset)['valid'] is True
    assert calls['read']==1
    assert service.verify(asset,force=True)['valid'] is True
    assert calls['read']==2
    # Same byte length and restored mtime still changes ctime on normal filesystems.
    old_mtime=path.stat().st_mtime_ns
    path.write_bytes(b'tampered-asset-byte!')
    # Exact size must match for checksum mismatch path, not only size gate.
    path.write_bytes(b'x'*len(b'original-asset-bytes'))
    import os
    os.utime(path,ns=(old_mtime,old_mtime))
    assert service.verify(asset)['valid'] is False
    assert service.verify(asset)['valid'] is False
    assert calls['read']>=4  # failures are never cached
    assert service.verification_metrics()['hash_bytes_read']>=len(b'original-asset-bytes')*3


def test_cache_rejects_length_changes_without_hashing(tmp_path,monkeypatch):
    from timepilot import native_bi
    p=tmp_path/'a.parquet';p.write_bytes(b'v1')
    service=object.__new__(NativeBIService)
    service._verify_lock=__import__('threading').RLock();service._verify_cache={}
    service._verify_stats={'hits':0,'hashes':0,'hash_bytes_read':0}
    service._asset=lambda aid:SimpleNamespace(id=aid,sha256=hashlib.sha256(b'v1').hexdigest(),file_bytes=2)
    service._path=lambda aid:p
    def forbidden(_):raise AssertionError('Must short-circuit on size mismatch')
    monkeypatch.setattr(native_bi,'sha256_file',forbidden)
    p.write_bytes(b'wrong!')
    assert service.verify('a')['reason']=='snapshot_size_mismatch'


def test_report_uses_identical_scope_and_invalid_counters():
    from timepilot.feature_evidence_v0373 import dashboard_explanations,dashboard_markdown
    report={'asset_id':'fixture','version_id':'base','source_sha256':'a'*64,
        'scope_sha256':'b'*64,'contract_revision':1,'rows_total':10,'rows_filtered':5,
        'fields':[{'field_id':'a','name':'a','non_null':4,'null_count':1,'null_rate':.2,
                   'invalid_numeric_count':1,'non_finite_count':1,'conversion_failed_count':0},
                  {'field_id':'b','name':'b','non_null':5,'null_count':0,'null_rate':0,
                   'invalid_numeric_count':1,'non_finite_count':0,'conversion_failed_count':1}],
        'metrics':example(),'correlations':[]}
    assert dashboard_explanations(report)['status']=='supported'
    md=dashboard_markdown(report)['markdown']
    assert '统计口径' in md and 'SQL NULL：1' in md and '数值转换失败：1' in md
    assert '综合缺失率：30%' in md
    report['metrics']['metric_scope']['field_ids']=['b','a']
    assert dashboard_explanations(report)['status']=='insufficient_evidence'
    report['metrics']['metric_scope']['field_ids']=['a','b']
    report['metrics']['invalid_numeric_cells']=0
    assert dashboard_explanations(report)['status']=='insufficient_evidence'
