"""v0.32.0: source-pinned auto-workflow, evidence and review, no fake engine success."""
from __future__ import annotations

import json
from uuid import uuid4

import pytest
from test_conversational_bi_v0231 import _env
from timepilot.feature_auto_analysis import classify_goal, candidate_quality
from timepilot.feature_time_inference import infer_time_basis
from timepilot.feature_contracts import FieldContract
from timepilot.project_migrations import TARGET_SCHEMA
from timepilot.projects import now


def base(pid):
    return f'/api/v1/projects/{pid}/feature-lab'


def set_job(app, job_id, payload):
    with app.state.projects._lock, app.state.projects._db() as db:
        db.execute("UPDATE feature_jobs SET status='succeeded', result_json=?,updated_at=? WHERE job_id=?",
                   (json.dumps(payload), now(), job_id))


def simulate_profile(app, pid, asset, contract, job_id):
    source = app.state.feature_semantic.source(pid, asset, 'base')
    fields = []
    for f in contract['fields']:
        is_time = f['source_name'].lower() == 'time'
        fields.append({'field_id': f['field_id'], 'source_name': f['source_name'],
                       'physical_type': f['physical_type'], 'null_rate': 0.0,
                       'rows_scanned': 3, 'min': 1 if is_time else 0.1,
                       'max': 3 if is_time else 0.3,
                       'distinct_count': 3, 'mean': 2 if is_time else 0.2})
    result = {'scope': 'full', 'rows_scanned': 3, 'fields': fields,
              'time': {'status': 'unconfirmed'}, 'source_sha256': source['sha256']}
    pid_profile = str(uuid4())
    with app.state.projects._lock, app.state.projects._db() as db:
        db.execute('''INSERT INTO feature_profiles(profile_id,project_id,asset_id,version_id,
           source_sha256,contract_revision,scope,result_json,created_at)
           VALUES (?,?,?,?,?,?,?,?,?)''', (pid_profile, pid, asset, 'base', source['sha256'],
              contract['revision'], 'full', json.dumps(result), now()))
    set_job(app, job_id, {'profile_id': pid_profile, 'scope': 'full', 'rows_scanned': 3,
                          'source_sha256': source['sha256']})


def test_goal_inference_in_chinese_and_time_kept_as_sequence():
    assert classify_goal('\u8bc6\u522b\u5f02\u5e38') == 'anomaly'
    assert classify_goal('\u8d28\u91cf\u5206\u7c7b') == 'classification'
    assert classify_goal('\u672a\u6765\u9500\u91cf\u9884\u6d4b') == 'forecast'
    field = FieldContract(field_id='fld_time', source_name='Time', source_column_index=0,
                          physical_type='DOUBLE')
    suggestion = infer_time_basis(field, {'min': 1, 'max': 506, 'distinct_count': 506}, 506)
    assert suggestion['kind'] == 'sequence_order_candidate'
    assert suggestion['recommended_role'] == 'order_index'
    assert not suggestion['supports_pit']
    assert not suggestion['supports_duration_windows']


def test_screening_does_not_fabricate_predictive_contribution():
    candidate = {'candidate_id': 'c1', 'output_name': 'sensor_mean_5',
                 'display_name': 'sensor mean', 'operator_id': 'rolling.mean'}
    result = candidate_quality([candidate], [{'feature_name': 'sensor_mean_5',
              'rows_scanned': 200, 'finite_count': 200, 'null_rate': 0.0,
              'constant_on_full_data': False}])
    assert result[0]['status'] == 'recommended'
    assert '\u9884\u6d4b' in result[0]['reason']
    assert candidate_quality([candidate], [])[0]['status'] == 'unverified'


def test_end_to_end_control_plane_and_source_contract_guard(tmp_path):
    app, client, pid, asset, _ = _env(tmp_path)
    url = base(pid)
    try:
        schema = app.state.projects._db()
        with schema as db:
            assert db.execute('SELECT version FROM project_schema').fetchone()[0] == TARGET_SCHEMA
        basis = client.get(url + '/time-basis/infer', params={'asset_id': asset})
        assert basis.status_code == 200, basis.text
        seq = next(x for x in basis.json()['fields'] if x['name'] == 'Time')
        assert seq['kind'] == 'sequence_order_candidate'
        assert seq['supports_pit'] is False
        contract = client.get(url + '/contracts', params={'asset_id': asset}).json()
        bad_time = client.patch(url + '/contracts/' + asset, json={'expected_revision': contract['revision'],
            'patch': {'fields': [{'field_id': seq['field_id'], 'semantic_type': 'datetime',
                                 'role': 'event_time', 'confirmation': 'confirmed'}]}})
        assert bad_time.status_code == 422
        # Explicitly confirm ordered numeric semantics, without creating fake datetime values.
        confirmation = client.post(url + '/time-basis/accept', json={'asset_id': asset,
            'field_id': seq['field_id'], 'role': 'order_index',
            'expected_revision': contract['revision']})
        assert confirmation.status_code == 200, confirmation.text
        assert next(x for x in confirmation.json()['fields'] if x['field_id'] == seq['field_id'])['role'] == 'order_index'
        payload = {'asset_id': asset, 'version_id': 'base', 'goal': '\u5206\u6790\u6570\u636e\u7684\u5f02\u5e38',
                   'task_hint': 'auto', 'request_id': 'stable-v032', 'budget': 6}
        created = client.post(url + '/analysis/sessions', json=payload)
        assert created.status_code == 201, created.text
        first = created.json()
        assert first['status'] == 'profiling'
        assert first['objective'] == 'anomaly'
        assert client.post(url + '/analysis/sessions', json=payload).json()['session_id'] == first['session_id']
        conflict = client.post(url + '/analysis/sessions', json={**payload, 'budget': 8})
        assert conflict.status_code in (409, 422)
        sid = first['session_id']
        simulate_profile(app, pid, asset, confirmation.json(), first['profile_job_id'])
        progressed = client.post(url + f'/analysis/sessions/{sid}/advance')
        assert progressed.status_code == 200, progressed.text
        doc = progressed.json()
        assert doc['status'] == 'previewing'
        assert doc['candidate_session_id']
        candidates = client.get(url + '/candidate-sessions/' + doc['candidate_session_id']).json()['candidates']
        quality = [{'feature_name': c['output_name'], 'rows_scanned': 3, 'finite_count': 3,
                    'null_rate': 0, 'constant_on_full_data': False} for c in candidates]
        assert len(candidates) >= 1
        for jid in doc['preview_job_ids']:
            set_job(app, jid, {'quality': quality, 'source_sha256': first['source_sha256']})
        done = client.post(url + f'/analysis/sessions/{sid}/advance')
        assert done.status_code == 200, done.text
        result = done.json()
        assert result['status'] == 'ready'
        assert result['report']['predictive_value_evaluated'] is False
        assert result['report']['recommended_count'] >= 1
        selection = client.put(url + f'/analysis/sessions/{sid}/selection', json={
            'expected_revision': result['revision'], 'candidate_ids': result['selection'][:1]})
        assert selection.status_code == 200, selection.text
        duplicate = client.put(url + f'/analysis/sessions/{sid}/selection', json={
            'expected_revision': result['revision'], 'candidate_ids': result['selection'][:1]})
        assert duplicate.status_code in (409, 422)
        saved = client.post(url + f'/analysis/sessions/{sid}/save-set', json={
            'name': 'Auto feature draft', 'request_id': 'save-v032'})
        assert saved.status_code == 201, saved.text
        document = saved.json()
        assert document['feature_set_id']
        report = client.get(url + f'/analysis/sessions/{sid}/report')
        assert report.status_code == 200 and '\u9884\u6d4b\u6536\u76ca\u9a8c\u8bc1: \u5c1a\u672a\u5b8c\u6210' in report.json()['markdown']
        mat = client.post(url + f'/analysis/sessions/{sid}/materialize', json={'request_id': 'mat-v032'})
        assert mat.status_code == 202, mat.text
        assert mat.json()['status'] == 'materializing'
        # v0.32.2 intentionally rejects a *forged* worker receipt without an
        # actual recorded, SHA-verified artifact. This is a safety regression,
        # not an end-to-end DuckDB test.
        set_job(app, mat.json()['materialize_job_id'], {
            'artifact_id': 'test-artifact-id', 'sha256': 'b' * 64})
        ev = client.post(url + f'/analysis/sessions/{sid}/advance')
        assert ev.status_code == 200, ev.text
        evaluated=ev
        assert evaluated.json()['status'] == 'failed'
        assert evaluated.json()['stage'] == 'materializing'
        assert 'SHA256' in evaluated.json()['error']
        done_report=client.get(url + f'/analysis/sessions/{sid}/report').json()['markdown']
        assert evaluated.json()['feature_set_id'] in done_report
        assert '正式产物 SHA256: `待物化`' in done_report
        # Immutable evidence must not be relinked to another FeatureSet by editing after materialization.
        assert client.put(url + f'/analysis/sessions/{sid}/selection', json={
            'expected_revision': evaluated.json()['revision'],
            'candidate_ids': evaluated.json()['selection']}).status_code in (409, 422)
        assert client.post(url + f'/analysis/sessions/{sid}/save-set', json={
            'name': 'Overwrite evidence', 'request_id': 'new-save'}).status_code in (409, 422)
        assert client.post(url + f'/analysis/sessions/{sid}/materialize', json={
            'request_id': 'new-run'}).status_code in (409, 422)
        # Changing the source contract must invalidate even a completed work session.
        current_contract = client.get(url + '/contracts', params={'asset_id': asset}).json()
        edit = client.patch(url + '/contracts/' + asset, json={
            'expected_revision': current_contract['revision'],
            'patch': {'fields': [{'field_id': seq['field_id'], 'display_name': 'sequence checked'}]}})
        assert edit.status_code == 200, edit.text
        after = client.get(url + f'/analysis/sessions/{sid}')
        assert after.status_code == 200 and after.json()['stale'] is True
        assert client.post(url + f'/analysis/sessions/{sid}/advance').status_code in (409, 422)
    finally:
        client.__exit__(None,None,None)


def test_llm_strategy_is_allowlisted_and_falls_back_for_untrusted_ids():
    from timepilot.feature_auto_analysis import FeatureAutoAnalysis
    from timepilot.feature_contracts import DataContract
    class Model:
        sent = None
        answer = None
        def config(self):return {'enabled': True, 'has_api_key': True}
        def _message(self, messages, max_tokens=500):
            self.sent = messages
            return {'content': json.dumps(self.answer)}
        def _parse_json(self, output):return json.loads(output)
    model = Model()
    service = object.__new__(FeatureAutoAnalysis)
    service.models = model
    contract = DataContract(asset_id='dataset',version_id='base',source_sha256='a'*64,
        fields=[FieldContract(field_id='fld_01',source_name='temp',source_column_index=0,
                              physical_type='DOUBLE'),
                FieldContract(field_id='fld_02',source_name='label',source_column_index=1,
                              physical_type='DOUBLE',role='label')])
    profile = {'fields': [{'field_id': 'fld_01', 'std': 2.0, 'null_rate': 0.01}]}
    model.answer={'field_ids':['fld_02'],'template':'industrial','reason':'use label'}
    result=service._llm_strategy('inspect', 'explore',contract,profile,['fld_01'],'basic',8)
    assert result[2]=='rules' and result[0]==['fld_01']
    assert 'temp' in model.sent[1]['content']
    assert 'label' not in model.sent[1]['content']
    model.answer={'field_ids':['fld_01'],'template':'quality','reason':'signal variation'}
    result=service._llm_strategy('inspect', 'explore',contract,profile,['fld_01'],'basic',8)
    assert result[2]=='llm' and result[0]==['fld_01'] and result[1]=='quality'
    contract.fields[0].classification='restricted'
    model.sent=None
    result=service._llm_strategy('inspect', 'explore',contract,profile,['fld_01'],'basic',8)
    assert result[2]=='rules' and model.sent is None


def test_schema34_to35_additive_migration_keeps_old_project(tmp_path):
    from timepilot.project_migrations import migrate
    app,client,pid,asset,_=_env(tmp_path)
    try:
        with app.state.projects._lock, app.state.projects._db() as db:
            before=db.execute('SELECT COUNT(*) FROM projects').fetchone()[0]
            db.execute('DROP TABLE feature_analysis_sessions')
            db.execute('UPDATE project_schema SET version=34')
            db.commit()
            db.execute('BEGIN IMMEDIATE')
            migrate(db)
            version=db.execute('SELECT version FROM project_schema').fetchone()[0]
            after=db.execute('SELECT COUNT(*) FROM projects').fetchone()[0]
            tables={row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert version==TARGET_SCHEMA and version>=35
        assert before==after and 'feature_analysis_sessions' in tables
    finally:
        client.__exit__(None,None,None)


def test_elapsed_time_with_unit_is_not_a_calendar_duration_window():
    elapsed=FieldContract(field_id='fld_elapsed', source_name='elapsed_s',
        source_column_index=0,physical_type='DOUBLE',role='elapsed_time',
        unit='s',confirmation='confirmed')
    advice=infer_time_basis(elapsed,{'min':0,'max':10,'distinct_count':10},10)
    assert advice['kind']=='elapsed_time_candidate'
    assert advice['supports_row_windows'] is True
    assert advice['supports_duration_windows'] is False
    assert advice['supports_pit'] is False

def test_generated_npm_lock_is_signed_by_source_manifest_when_present(tmp_path):
    import sys
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]
    sys.path.insert(0,str(root/'scripts'))
    from release_integrity import tracked_path
    generated=tmp_path/'frontend/native-bi/package-lock.json'
    generated.parent.mkdir(parents=True)
    generated.write_text('{}',encoding='utf-8')
    # Locks generated on an online build machine must be audited and signed by
    # the source release, so subsequent npm ci resolutions remain reproducible.
    assert tracked_path(tmp_path,generated) is True
