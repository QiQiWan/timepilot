"""v0.29 persisted decisions, optimistic locking, draft-only promotion and additive migration."""
from __future__ import annotations
import sqlite3
import threading
from pathlib import Path
from uuid import uuid4
import pytest
from timepilot.feature_contracts import FeatureSpecV2,DataContract,FieldContract,TaskSpecV2,SourceRef,FeatureDefinition,canonical
from timepilot.feature_evaluation import FeatureEvaluator
from timepilot.project_migrations import TARGET_SCHEMA,migrate
from test_conversational_bi_v0231 import _env

class FakeProjects:
    def __init__(self,path):self.path=path;self._lock=threading.Lock()
    def _db(self):
        db=sqlite3.connect(self.path);db.row_factory=sqlite3.Row
        return db
    def get(self,pid):return {'id':pid}

class FakeRegistry:
    def __init__(self,record,spec):self.record=record;self.spec=spec;self.saved=[]
    def _editor(self,pid):return True
    def _actor(self):return 'test-reviewer'
    def get(self,pid,sid):return self.record
    def pinned(self,pid,record):return self.spec
    def create(self,pid,spec,request_id):
        self.saved.append((spec,request_id))
        return {'set_id':'derived-set','state':'draft','spec':spec.model_dump()}

class FakeSemantic:
    def __init__(self,doc):self.doc=doc
    def get_revision(self,pid,aid,vid,revision):return self.doc


def test_review_save_conflicts_and_draft_promotion(tmp_path):
    path=tmp_path/'test.sqlite'
    conn=sqlite3.connect(path)
    conn.executescript('''CREATE TABLE feature_evaluation_plans(plan_id TEXT,project_id TEXT,set_id TEXT);
      CREATE TABLE feature_evaluations(evaluation_id TEXT,project_id TEXT,plan_id TEXT,status TEXT,report_json TEXT,created_at TEXT);
      CREATE TABLE feature_evaluation_selections(selection_id TEXT PRIMARY KEY,project_id TEXT,
        evaluation_id TEXT,owner_id TEXT,revision INTEGER,selection_json TEXT,selection_sha256 TEXT,
        source_sha256 TEXT,spec_sha256 TEXT,created_at TEXT,updated_at TEXT,
        UNIQUE(project_id,evaluation_id,owner_id));''')
    spec=FeatureSpecV2(name='origin',source=SourceRef(asset_id='a',version_id='base',source_sha256='a'*64),
      contract_revision=1,task=TaskSpecV2(task_id='task',mode='supervised',task_type='binary_classification',
      prediction_time_field_id='fld_time',label_field_id='fld_label',
      label_available_at_field_id='fld_available'),
      features=[FeatureDefinition(feature_id='feat-A',output_name='f_A',operator_id='transform.abs',
                    operator_version='2.8',input_field_ids=['fld_pressure'])])
    contract=DataContract(asset_id='a',version_id='base',source_sha256='a'*64,revision=1,
      fields=[FieldContract(field_id='fld_pressure',source_name='pressure',source_column_index=0,
          physical_type='DOUBLE',semantic_type='numeric',role='value',confirmation='confirmed')])
    record={'set_id':'s','source_sha256':'a'*64,'spec_sha256':'b'*64,
      'asset_id':'a','version_id':'base','spec':spec.model_dump()}
    from timepilot.feature_contracts import digest
    candidate={'candidate_id':'autofeat_x','expression':'log1p(abs(x0))','label':'压力对数',
               'source_fields':['fld_pressure'],'folds':2}
    report={'mode':'supervised','source_sha256':'a'*64,'spec_sha256':'b'*64,
            'searched_candidate_recipes':[candidate]}
    conn.execute('INSERT INTO feature_evaluation_plans VALUES(?,?,?)',('p','p','s'))
    conn.execute('INSERT INTO feature_evaluations VALUES(?,?,?,?,?,?)',
             ('e','p','p','succeeded',canonical(report),'2026-11-12'))
    conn.commit();conn.close()
    registry=FakeRegistry(record,spec)
    evaluator=FeatureEvaluator(FakeProjects(path),None,FakeSemantic(contract),registry,None)
    with pytest.raises(ValueError,match='不存在'):
        evaluator.save_selection('p','e',feature_ids=['feat-unknown'],search_candidate_ids=[])
    saved=evaluator.save_selection('p','e',feature_ids=['feat-A'],
                                  search_candidate_ids=['autofeat_x'],note='检验')
    assert saved['revision']==1
    with pytest.raises(ValueError,match='版本冲突'):
        evaluator.save_selection('p','e',feature_ids=['feat-A'],search_candidate_ids=[],expected_revision=0)
    result=evaluator.derive_set('p','e',selection_revision=1,request_id='id-1',name='特征筛选版')
    assert result['state']=='draft'
    assert len(result['spec']['features'])==2
    assert {f['operator_id'] for f in result['spec']['features']}=={'transform.abs','expression.safe'}
    with pytest.raises(ValueError,match='已变化'):
        evaluator.derive_set('p','e',selection_revision=0,request_id='id-2',name='旧版本')
    # Source/spec fingerprint does not silently adapt if current record changes.
    registry.record={**record,'spec_sha256':'f'*64}
    with pytest.raises(ValueError,match='过期'):
        evaluator.save_selection('p','e',feature_ids=['feat-A'],search_candidate_ids=[],expected_revision=1)


def test_schema_upgrade_31_to_32_keeps_projects_and_assets(tmp_path):
    app,client,pid,asset,_=_env(tmp_path)
    try:
        p=app.state.projects
        with p._lock,p._db() as db:
            assert db.execute('SELECT version FROM project_schema').fetchone()[0]==TARGET_SCHEMA==34
            before=db.execute('SELECT COUNT(*) FROM projects').fetchone()[0]
            db.execute('DROP TABLE feature_evaluation_selections')
            db.execute('DROP INDEX IF EXISTS idx_feature_selection_report')
            db.execute('UPDATE project_schema SET version=31')
            migrate(db)
            assert db.execute('SELECT version FROM project_schema').fetchone()[0]==34
            assert db.execute('SELECT COUNT(*) FROM projects').fetchone()[0]==before
            assert db.execute("SELECT name FROM sqlite_master WHERE name='feature_evaluation_selections'").fetchone()
    finally:client.__exit__(None,None,None)
