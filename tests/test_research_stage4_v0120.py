"""Stage IV hardening: independent human review, reproducible A/B, bounded models and one entry.

These tests use SYNTHETIC, code-defined reference labels. They are not evidence
of observed business reviewers, real API/model improvement, or equipment results.
"""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from timepilot.api import create_app
from timepilot.auto_experiment import ExperimentSpec
from timepilot.data_assets import asset_id_for
from timepilot.mechanisms.catalog import simulated_maintenance_series
from timepilot.smart_import import TransformRecipe

ROOT=Path(__file__).resolve().parents[1]


def synthetic_case(app, filename: str = 'test-A.csv', rows: int = 40):
    lines=['Time,A2,A14,available_at,source_name,统计口径']
    for i in range(rows):
        y,m = 2020 + i//12, i%12+1
        publish=f'{y+1 if m==12 else y}-{1 if m==12 else m+1:02d}-08T10:00:00+08:00'
        variant = 1 if 'B' in filename else 0
        lines.append(f'{y}-{m:02d}-01,M1,{100+i+variant},' + publish + ',SourceX,销量')
    manifest=app.state.smart_import.ingest(io.BytesIO('\n'.join(lines).encode()),filename)
    ver=app.state.smart_import.transform(manifest['import_id'], TransformRecipe(
        candidate_ids=manifest['suggested_candidate_ids'],field_mapping={
            'series_id':'A2','target':'A14','available_at':'available_at',
            'source_name':'source_name','statistical_scope':'统计口径'}))
    return manifest,ver


class FakeProposer:
    """Pure in-memory fixture, never an external LLM request."""
    def propose_semantic_labels(self, schema):
        return {'proposal':{'layout':'ordinary_timeseries','roles':{
            'timestamp':'Time','target':'A14','series_id':'A2'}},
            'model':'synthetic-fixture','usage':{'prompt_tokens':5,'completion_tokens':7}}


def _review(app, manifest, reviewer='标注者甲'):
    bench=app.state.semantic_benchmark
    bench.seed(manifest['import_id'])
    item=next(x for x in bench.cases() if x['import_id']==manifest['import_id'])
    bench.review(item['case_id'],{'reviewer':reviewer,'confirmed_by_human':True,
        'layout':'ordinary_timeseries','roles':{'timestamp':'Time','series_id':'A2','target':'A14'}})
    return item['case_id']


def test_paired_protocol_requires_independent_reviewers_and_real_completions(tmp_path):
    app=create_app(home=tmp_path,embedded_worker=False)
    m1,_=synthetic_case(app,'case_A.csv')
    m2,_=synthetic_case(app,'case_B.csv')
    ids={_review(app,m1),_review(app,m2)}
    bench=app.state.semantic_benchmark
    result=bench.create_protocol(reviewers=['复核乙','复核丙'],seed=29)
    assert result['cases']==2
    assert {x['case_id'] for x in result['assignments']}==ids
    assert len(result['assignments'])==4
    assert bench.create_protocol(reviewers=['复核乙','复核丙'],seed=29)['protocol_id']==result['protocol_id']
    for case_id in ids:
        pair=[x for x in result['assignments'] if x['case_id']==case_id]
        assert {x['arm'] for x in pair}=={'rules','model'}
        assert len({x['reviewer'] for x in pair})==2
    report=bench.protocol_summary(result['protocol_id'])
    assert report['complete_paired_cases']==0
    assert report['mean_model_minus_rule_edits'] is None
    bench.gateway=FakeProposer()
    for assignment in result['assignments']:
        trial=bench.start_trial(assignment['case_id'],arm=assignment['arm'],
                               reviewer=assignment['reviewer'],protocol_id=result['protocol_id'])
        assert 'annotation' not in trial and 'gold' not in trial
        assert trial['protocol_id']==result['protocol_id']
        final=bench.finish_trial(trial['trial_id'],reviewer=assignment['reviewer'],corrected={
            'layout':'ordinary_timeseries','roles':{'timestamp':'Time','series_id':'A2','target':'A14'}})
        assert final['status']=='completed'
        with pytest.raises(ValueError,match='already started'):
            bench.start_trial(assignment['case_id'],arm=assignment['arm'],
                              reviewer=assignment['reviewer'],protocol_id=result['protocol_id'])
    report=bench.protocol_summary(result['protocol_id'])
    assert report['complete_paired_cases']==2
    assert report['incomplete_cases']==0
    assert report['model_final_correct_rate']==1.0
    assert report['rules_final_correct_rate']==1.0
    assert report['interpretation'].startswith('descriptive_')
    with pytest.raises(ValueError,match='Independent'):
        bench.create_protocol(reviewers=['标注者甲','复核乙'])


def test_protocol_frozen_reference_revision_and_forged_assignment(tmp_path):
    app=create_app(home=tmp_path,embedded_worker=False)
    manifest,_=synthetic_case(app)
    case_id=_review(app,manifest)
    bench=app.state.semantic_benchmark
    result=bench.create_protocol(reviewers=['复核乙','复核丙'])
    alloc=result['assignments'][0]
    with pytest.raises(ValueError,match='frozen randomized assignment'):
        bench.start_trial(case_id,reviewer='未经分配人员',arm=alloc['arm'],
                          protocol_id=result['protocol_id'])
    bench.review(case_id,{'reviewer':'标注者甲','confirmed_by_human':True,
        'layout':'ordinary_timeseries','notes':'second gold revision',
        'roles':{'timestamp':'Time','series_id':'A2','target':'A14'}})
    with pytest.raises(ValueError,match='frozen randomized assignment'):
        bench.start_trial(case_id,reviewer=alloc['reviewer'],arm=alloc['arm'],
                          protocol_id=result['protocol_id'])


def test_bulk_human_label_csv_requires_explicit_confirmation_and_hash(tmp_path):
    app=create_app(home=tmp_path,embedded_worker=False)
    manifest,_=synthetic_case(app)
    bench=app.state.semantic_benchmark
    bench.seed(manifest['import_id'])
    exported=bench.export_review_template()
    record=list(csv.DictReader(io.StringIO(exported)))[0]
    assert record['reviewer']=='' and record['roles_json']==''
    record.update({'reviewer':'标注者甲','confirmed_by_human':'true',
                   'layout':'ordinary_timeseries',
                   'roles_json':json.dumps({'timestamp':'Time','series_id':'A2','target':'A14'})})
    buf=io.StringIO(); writer=csv.DictWriter(buf,fieldnames=list(record));writer.writeheader();writer.writerow(record)
    assert bench.import_human_reviews(buf.getvalue())['imported_human_labels']==1
    assert bench.evaluate(use_model=False)['labeled_cases']==1
    with TestClient(app) as client:
        response=client.get('/api/v1/research/semantic/review-template.csv')
        assert response.status_code==200 and 'case_id' in response.text
        protocol=client.post('/api/v1/research/semantic/protocols',json={
            'reviewers':['复核乙','复核丙'],'seed':8})
        assert protocol.status_code==201,protocol.text
        summary=client.get('/api/v1/research/semantic/protocols/'+protocol.json()['protocol_id']+'/results')
        assert summary.status_code==200 and summary.json()['complete_paired_cases']==0


def test_batch_import_rejects_incorrect_source_before_writing(tmp_path):
    app=create_app(home=tmp_path,embedded_worker=False)
    manifest,_=synthetic_case(app)
    bench=app.state.semantic_benchmark
    bench.seed(manifest['import_id'])
    line=list(csv.DictReader(io.StringIO(bench.export_review_template())))[0]
    line.update({'reviewer':'标注者甲','confirmed_by_human':'true','layout':'ordinary_timeseries',
                 'roles_json':json.dumps({'timestamp':'Time','series_id':'A2','target':'A14'})})
    valid=line.copy();broken=line.copy();broken['source_sha256']='0'*64
    f=io.StringIO();w=csv.DictWriter(f,fieldnames=list(line));w.writeheader();w.writerow(broken)
    with pytest.raises(ValueError,match='Source hash mismatch'):
        bench.import_human_reviews(f.getvalue())
    assert bench.cases()[0]['annotation'] is None
    # An explicit approval from the human reviewer is mandatory.
    valid['confirmed_by_human']='false';f=io.StringIO();w=csv.DictWriter(f,fieldnames=list(line));w.writeheader();w.writerow(valid)
    with pytest.raises(ValueError,match='Human confirmation'):
        bench.import_human_reviews(f.getvalue())
    assert bench.cases()[0]['annotation'] is None


def test_model_experiment_mechanism_link_and_report_sha(tmp_path):
    app=create_app(home=tmp_path,embedded_worker=False)
    manifest,ver=synthetic_case(app)
    asset_id=asset_id_for(manifest['import_id'])
    series=simulated_maintenance_series(length=160,seed=21,maintenance_step=50)
    evidence=app.state.mechanism_study.register({
        'kind':'simulation','reference':'simulation for linkage audit',
        'sample_interval_seconds':60,'state_unit':'MPa','control_unit':'A',
        'state':series['observed_state'].tolist(),'control':series['control'].tolist(),
        'maintenance_indices':[50],
        'source_asset_id':asset_id,'source_version_id':ver['version_id']})
    spec=ExperimentSpec(asset_id=asset_id,version_id=ver['version_id'],horizon=1,
                        folds=2,min_history=24,model_ids=['last_value','lag_ridge'],
                        mechanism_evidence_id=evidence['evidence_id'])
    ex=app.state.auto_experiments
    proposed=ex.plan(spec)
    assert proposed['mechanism_status']['can_influence_forecast'] is False
    report=ex.run(spec)
    assert report['mechanism_status']['source_kind']=='simulation'
    assert report['mechanism_status']['verified_on_real_equipment'] is False
    assert len(report['paired_fold_diagnostics'])==1
    assert report['feature_engineering']['evaluation'].startswith('all candidates')
    saved=ex.root/report['experiment_id']
    assert (saved/'report.sha256').exists()
    assert ex.run(spec)['reused'] is True
    follow=ex.next_plan(report['experiment_id'])
    assert follow['status']=='plan_requires_human_confirmation'
    assert follow['auto_executed'] is False
    assert follow['proposed_spec']['folds']==3
    with pytest.raises(ValueError,match='资产版本不一致'):
        ex._mechanism_link(spec.model_copy(update={'asset_id':'other_asset'}))
    (saved/'report.json').write_text((saved/'report.json').read_text()+'\n')
    with pytest.raises(ValueError,match='report SHA256'):
        ex.report(report['experiment_id'])


def test_root_has_only_unified_shell_and_batch_entrypoints():
    assert sorted(p.name for p in ROOT.iterdir() if p.is_file() and p.suffix in {'.sh','.bat'})==[
        'TIMEPILOT.bat','TIMEPILOT.sh']
    assert (ROOT/'scripts/launcher.py').is_file()
    assert (ROOT/'scripts/restore_hidden.py').is_file()
    launcher=(ROOT/'scripts/launcher.py').read_text()
    assert "elif target == 'native'" in launcher
    assert "elif target == 'research-browser'" in launcher
    for fragment in ('conda create','python -m venv','playwright install chromium',
                     'pip install -r requirements.txt'):
        assert fragment not in launcher
