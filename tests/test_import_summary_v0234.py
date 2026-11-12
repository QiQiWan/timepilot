"""v0.23.4 source-verified summary contracts and import ownership."""
from __future__ import annotations
from timepilot.project_migrations import TARGET_SCHEMA
import time
from uuid import uuid4
from fastapi.testclient import TestClient
from test_universal_import_v0221 import _app, _make_project, _upload
from timepilot.import_summary import ImportSummaryService


def _await(c, url):
    for _ in range(150):
        r=c.get(url);assert r.status_code==200,r.text
        obj=r.json()
        if obj['status'] in ('succeeded','failed'):return obj
        time.sleep(.025)
    raise AssertionError('import summary did not complete')


def test_three_region_workbook_evidence_and_cached_versions(tmp_path):
    app,_=_app(tmp_path)
    with TestClient(app) as c:
        pid=_make_project(c)
        iid,tree=_upload(c,pid)
        base=f'/api/v1/projects/{pid}/imports/{iid}'
        empty=c.get(base+'/summary')
        assert empty.status_code==200 and empty.json()['region_count']==3
        assert empty.json()['ready']==0
        job=c.post(base+'/summary-jobs',json={'refresh':False})
        assert job.status_code==202,job.text
        done=_await(c,base+'/summary-jobs/'+job.json()['job_id'])
        assert done['status']=='succeeded',done
        overview=c.get(base+'/summary')
        assert overview.status_code==200,overview.text
        data=overview.json();assert data['ready']==3 and len(data['summaries'])==3
        outputs={tuple(x['fields'][0]['name'] for _ in [1]):x for x in data['summaries']}
        assert outputs[('Batch',)]['shape']=={'row_count':2,'column_count':2,'count_scope':'full_nonempty_region'}
        assert outputs[('Device',)]['shape']['row_count']==2
        assert outputs[('Event',)]['shape']['row_count']==1
        batch=outputs[('Batch',)]
        assert batch['fields'][0]['source_cell']=='B2'
        assert batch['fields'][0]['numeric_parseable']==2
        assert batch['fields'][0]['distinct_count']==2
        assert batch['provenance']['source_verified'] is True
        assert all(x['shape']['row_count']<10 for x in data['summaries'])
        with app.state.projects._db() as db:
            count=db.execute('SELECT count(*) FROM import_region_summaries').fetchone()[0]
        for region in tree['files'][0]['worksheets'][0]['regions']:
            view=c.get(base+'/regions/'+region['region_id']+'/summary')
            assert view.status_code==200,view.text
        with app.state.projects._db() as db:
            assert db.execute('SELECT count(*) FROM import_region_summaries').fetchone()[0]==count
        forbidden=_make_project(c,'另一团队')
        assert c.get(f'/api/v1/projects/{forbidden}/imports/{iid}/summary').status_code==404
        assert c.post(f'/api/v1/projects/{forbidden}/imports/{iid}/summary-jobs',json={}).status_code==404


def test_summary_source_hash_rechecked_and_publish_does_not_depend_on_summary(tmp_path):
    app,saved=_app(tmp_path)
    with TestClient(app) as c:
        pid=_make_project(c)
        iid,tree=_upload(c,pid,b'Code,Value\n00123,1.5\n00124,2.5\n','simple.csv')
        rid=tree['files'][0]['worksheets'][0]['regions'][0]['region_id']
        service=ImportSummaryService(app.state.projects)
        one=service.summarize_region(pid,iid,rid)
        assert one['shape']['row_count']==2
        assert one['fields'][0]['source_name']=='Code'
        assert one['fields'][1]['numeric_min']==1.5
        assert one['fields'][1]['numeric_max']==2.5
        manifest=app.state.projects.require_import(pid,iid)
        blob=app.state.projects.imports._dir(iid)/manifest['files'][0]['stored']
        blob.write_bytes(b'Code,Value\n00123,9000\n')
        try:
            service.summarize_region(pid,iid,rid)
        except Exception as exc:
            assert '哈希' in str(exc)
        else:
            raise AssertionError('tampered source must never use cached values')
        # Existing summary cache is no longer evidence for the modified bytes.
        view=c.get(f'/api/v1/projects/{pid}/imports/{iid}/regions/{rid}/summary')
        assert view.status_code in (409,422),view.text


def test_schema_migration_idempotence_and_old_data_preserved(tmp_path):
    app,_=_app(tmp_path)
    with TestClient(app) as c:
        pid=_make_project(c)
        iid,_=_upload(c,pid,b'X,Y\n1,2\n3,4\n','raw.csv')
    cat=app.state.projects
    with cat._db() as db:
        assert db.execute('SELECT version FROM project_schema').fetchone()[0]==TARGET_SCHEMA
        old=db.execute('SELECT count(*) FROM project_imports').fetchone()[0]
        db.execute('UPDATE project_schema SET version=4')
    from timepilot.project_migrations import migrate
    with cat._db() as db:
        migrate(db)
        migrate(db)
        assert db.execute('SELECT version FROM project_schema').fetchone()[0]==TARGET_SCHEMA
        assert db.execute('SELECT count(*) FROM project_imports').fetchone()[0]==old
        assert db.execute('SELECT count(*) FROM bi_analysis_plans').fetchone()[0]==0
