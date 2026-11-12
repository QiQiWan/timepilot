"""Forward-only, additive catalog migration through v0.31.0 (project schema 34).

No migration changes an original data file, published dataset, model or audit row.
SQLite backup() captures committed WAL state before the first schema upgrade.
"""
from __future__ import annotations
import sqlite3
import re
from pathlib import Path

TARGET_SCHEMA = 34


class CatalogSchemaError(RuntimeError):
    """A project catalog cannot be safely initialized or upgraded."""


def inspect_catalog(path: Path) -> dict:
    """Read-only catalog inspection. Never create a missing SQLite file."""
    from urllib.parse import quote

    path = Path(path).expanduser().resolve()
    result = {'path': str(path), 'target_version': TARGET_SCHEMA,
              'exists': path.exists(), 'current_version': None, 'status': 'new',
              'reason': '', 'size_bytes': 0}
    if not path.exists():
        result['reason'] = 'No project catalog found; first start will create it.'
        return result
    if not path.is_file():
        result.update(status='invalid_path', reason='Catalog path exists but is not a regular file.')
        return result
    result['size_bytes'] = path.stat().st_size
    try:
        uri = 'file:' + quote(str(path), safe='/:') + '?mode=ro'
        with sqlite3.connect(uri, uri=True, timeout=15) as db:
            checked = db.execute('PRAGMA quick_check(1)').fetchone()
            if not checked or checked[0] != 'ok':
                result.update(status='corrupt', reason='SQLite quick_check failed: ' + str(checked))
                return result
            tables = {r[0] for r in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            if not tables:
                result.update(status='empty', reason='Empty SQLite file; safe to initialize.')
                return result
            if not {'projects', 'project_schema'}.issubset(tables):
                result.update(status='unrecognized', reason=(
                    'Non-empty database has no complete TimePilot projects/project_schema tables. '
                    'Refusing to infer a version or replace existing data.'))
                return result
            versions = db.execute('SELECT version FROM project_schema').fetchall()
            if len(versions) != 1 or type(versions[0][0]) is not int:
                result.update(status='unrecognized', reason=(
                    'Expected exactly one integer project_schema.version row.'))
                return result
            current = versions[0][0]
            result['current_version'] = current
            if current < 1:
                result.update(status='unrecognized', reason='Invalid project_schema version.')
            elif current > TARGET_SCHEMA:
                result.update(status='newer', reason=(
                    f'Catalog schema v{current} is newer than the supported v{TARGET_SCHEMA}. '
                    'Use the matching newer TimePilot version; automatic downgrade is forbidden.'))
            elif current < TARGET_SCHEMA:
                result.update(status='upgrade', reason=(
                    f'Catalog schema v{current} will be backed up and upgraded to v{TARGET_SCHEMA}.'))
            else:
                result.update(status='current', reason=f'Catalog schema v{current} is supported.')
    except (sqlite3.DatabaseError, OSError) as exc:
        result.update(status='corrupt', reason=f'SQLite cannot open/validate catalog: {exc}')
    return result


def require_supported_catalog(path: Path) -> dict:
    state = inspect_catalog(path)
    if state['status'] not in {'new', 'empty', 'upgrade', 'current'}:
        raise CatalogSchemaError(
            f"Project catalog cannot be opened: {state['path']} "
            f"(status={state['status']}, version={state['current_version']}). "
            + state['reason'] + ' No database file was deleted or reset.')
    return state


def backup_before_upgrade(path: Path):
    """Use SQLite backup() for a complete pre-upgrade snapshot, including WAL."""
    import os
    from datetime import datetime, timezone
    from uuid import uuid4

    state = require_supported_catalog(path)
    if state['status'] != 'upgrade':
        return None
    source_path = Path(path)
    version = state['current_version']
    timestamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    target = source_path.with_name(
        f'catalog.pre-v{version}-to-v{TARGET_SCHEMA}.{timestamp}.sqlite3')
    staging = target.with_name(target.name + '.staging-' + uuid4().hex)
    try:
        from urllib.parse import quote
        uri = 'file:' + quote(str(source_path.resolve()), safe='/:') + '?mode=ro'
        with sqlite3.connect(uri, uri=True, timeout=30) as source:
            with sqlite3.connect(staging, timeout=30) as replica:
                source.backup(replica)
                if replica.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise CatalogSchemaError('The pre-upgrade SQLite backup failed integrity_check.')
                archived_version = replica.execute('SELECT version FROM project_schema').fetchone()
                if not archived_version or archived_version[0] != version:
                    raise CatalogSchemaError('The SQLite backup schema version changed unexpectedly.')
        os.replace(staging, target)
        return target
    finally:
        staging.unlink(missing_ok=True)


from contextlib import contextmanager
import os
import time


@contextmanager
def catalog_startup_lock(path: Path, *, timeout_seconds: float = 90):
    """Process-safe advisory migration lock, released automatically on crash.

    Avoid stale O_EXCL sentinel files and overlapping first-start WAL setup.
    """
    lockfile = Path(path)
    lockfile.parent.mkdir(parents=True, exist_ok=True)
    with lockfile.open('a+b') as stream:
        if os.name == 'nt':
            import msvcrt
            if stream.seek(0, 2) == 0:
                stream.write(b'\0')
                stream.flush()
            stream.seek(0)
        else:
            import fcntl
        started = time.monotonic()
        while True:
            try:
                if os.name == 'nt':
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except (BlockingIOError, OSError):
                if time.monotonic() - started > timeout_seconds:
                    raise TimeoutError('Project catalog is locked by another startup or migration.')
                time.sleep(0.1)
        try:
            yield
        finally:
            if os.name == 'nt':
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


# All definitions are confined to the project catalog database. Keep each
# revision independently testable; a partially completed migration can be
# safely replayed, but a *newer* database is never written by older code.
MIGRATIONS={
  8: '''
    CREATE TABLE IF NOT EXISTS asset_lifecycle(
      project_id TEXT NOT NULL REFERENCES projects(id),asset_id TEXT NOT NULL,
      state TEXT NOT NULL CHECK(state IN ('active','trashed')),
      trashed_at TEXT,trashed_by TEXT,restored_at TEXT,restored_by TEXT,
      retention_until TEXT,reason TEXT,revision INTEGER NOT NULL DEFAULT 1,
      updated_at TEXT NOT NULL,PRIMARY KEY(project_id,asset_id));
    CREATE INDEX IF NOT EXISTS idx_asset_lifecycle_state ON asset_lifecycle(project_id,state);
    CREATE TABLE IF NOT EXISTS asset_lifecycle_events(
      event_id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),
      asset_id TEXT NOT NULL,action TEXT NOT NULL,actor_id TEXT NOT NULL,
      request_id TEXT NOT NULL,before_state TEXT NOT NULL,after_state TEXT NOT NULL,
      dependency_snapshot_json TEXT NOT NULL,created_at TEXT NOT NULL,
      UNIQUE(project_id,asset_id,request_id));
    CREATE TABLE IF NOT EXISTS asset_search_index(
      project_id TEXT NOT NULL REFERENCES projects(id),asset_id TEXT NOT NULL,
      name TEXT NOT NULL,source_file TEXT NOT NULL DEFAULT '',source_sheet TEXT NOT NULL DEFAULT '',
      fields_json TEXT NOT NULL DEFAULT '[]',updated_at TEXT NOT NULL,
      PRIMARY KEY(project_id,asset_id));
  ''',
  9: '''
    CREATE TABLE IF NOT EXISTS bi_perspective_snapshots(
      workspace_id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),
      owner_id TEXT NOT NULL,asset_id TEXT NOT NULL,version_id TEXT NOT NULL,
      source_sha256 TEXT NOT NULL,workspace_revision INTEGER NOT NULL,
      viewer_revision INTEGER NOT NULL,viewer_version TEXT NOT NULL,
      token_json TEXT NOT NULL,token_sha256 TEXT NOT NULL,updated_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS bi_perspective_snapshot_events(
      event_id TEXT PRIMARY KEY,workspace_id TEXT NOT NULL REFERENCES bi_perspective_snapshots(workspace_id),
      request_id TEXT NOT NULL,actor_id TEXT NOT NULL,viewer_revision INTEGER NOT NULL,
      before_sha256 TEXT NOT NULL,after_sha256 TEXT NOT NULL,created_at TEXT NOT NULL,
      UNIQUE(workspace_id,request_id));
  ''',
 10: '''
    CREATE TABLE IF NOT EXISTS bi_perspective_capability_cache(
      project_id TEXT NOT NULL REFERENCES projects(id),asset_id TEXT NOT NULL,
      version_id TEXT NOT NULL,source_sha256 TEXT NOT NULL,
      manifest_sha256 TEXT NOT NULL,manifest_json TEXT NOT NULL,
      updated_at TEXT NOT NULL,PRIMARY KEY(project_id,asset_id,version_id));
  ''',
 11: '''
    CREATE TABLE IF NOT EXISTS bi_perspective_tool_receipts(
      receipt_id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),
      workspace_id TEXT NOT NULL,request_id TEXT NOT NULL,tool_name TEXT NOT NULL,
      status TEXT NOT NULL,expected_revision INTEGER NOT NULL,
      source_sha256 TEXT NOT NULL,payload_sha256 TEXT NOT NULL,
      receipt_json TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
      UNIQUE(workspace_id,request_id));
    CREATE INDEX IF NOT EXISTS idx_bi_tool_receipts_workspace ON bi_perspective_tool_receipts(workspace_id,created_at);
  ''',
 12: '''
    CREATE TABLE IF NOT EXISTS bi_reasoning_runs(
      run_id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),
      workspace_id TEXT NOT NULL,request_id TEXT NOT NULL,owner_id TEXT NOT NULL,
      utterance TEXT NOT NULL,planner TEXT NOT NULL,status TEXT NOT NULL,
      source_sha256 TEXT NOT NULL,expected_revision INTEGER NOT NULL,
      plan_json TEXT NOT NULL,model_id TEXT,usage_json TEXT NOT NULL,
      created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
      UNIQUE(workspace_id,owner_id,request_id));
    CREATE TABLE IF NOT EXISTS bi_reasoning_steps(
      step_id TEXT PRIMARY KEY,run_id TEXT NOT NULL REFERENCES bi_reasoning_runs(run_id),
      ordinal INTEGER NOT NULL,tool_name TEXT NOT NULL,arguments_json TEXT NOT NULL,
      status TEXT NOT NULL,observation_json TEXT,created_at TEXT NOT NULL,
      UNIQUE(run_id,ordinal));
  ''',
 13: '''
    ALTER TABLE import_summary_jobs ADD COLUMN refresh INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE import_summary_jobs ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE import_summary_jobs ADD COLUMN lease_owner TEXT;
    ALTER TABLE import_summary_jobs ADD COLUMN lease_until TEXT;
    CREATE INDEX IF NOT EXISTS idx_summary_jobs_recover ON import_summary_jobs(status,lease_until);
    CREATE TABLE IF NOT EXISTS asset_references(
      project_id TEXT NOT NULL REFERENCES projects(id),asset_id TEXT NOT NULL,
      ref_kind TEXT NOT NULL,ref_id TEXT NOT NULL,state TEXT NOT NULL,
      updated_at TEXT NOT NULL,PRIMARY KEY(project_id,asset_id,ref_kind,ref_id));
  ''',
 14: '''
    CREATE TABLE IF NOT EXISTS import_region_reviews(
      project_id TEXT NOT NULL REFERENCES projects(id),import_id TEXT NOT NULL,
      region_id TEXT NOT NULL,fingerprint TEXT NOT NULL,decision TEXT NOT NULL,
      actor_id TEXT NOT NULL,reason TEXT NOT NULL,updated_at TEXT NOT NULL,
      PRIMARY KEY(project_id,import_id,region_id));
    CREATE TABLE IF NOT EXISTS import_data_contracts(
      project_id TEXT NOT NULL REFERENCES projects(id),import_id TEXT NOT NULL,
      region_id TEXT NOT NULL,fingerprint TEXT NOT NULL,contract_json TEXT NOT NULL,
      updated_at TEXT NOT NULL,PRIMARY KEY(project_id,import_id,region_id));
  ''',
 15: '''
    CREATE TABLE IF NOT EXISTS bi_analysis_artifacts(
      artifact_id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),
      workspace_id TEXT NOT NULL,owner_id TEXT NOT NULL,source_sha256 TEXT NOT NULL,
      artifact_type TEXT NOT NULL,config_json TEXT NOT NULL,result_sha256 TEXT,
      state TEXT NOT NULL,created_at TEXT NOT NULL);
  ''',
 16: '''
    CREATE TABLE IF NOT EXISTS feature_recipes(
      recipe_id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),
      owner_id TEXT NOT NULL,asset_id TEXT NOT NULL,version_id TEXT NOT NULL,
      source_sha256 TEXT NOT NULL,name TEXT NOT NULL,spec_json TEXT NOT NULL,
      spec_sha256 TEXT NOT NULL,state TEXT NOT NULL,derived_id TEXT,
      created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
      UNIQUE(project_id,owner_id,asset_id,name));
    CREATE INDEX IF NOT EXISTS idx_feature_recipes_source
      ON feature_recipes(project_id,asset_id,version_id);
  ''',
 17: '''
    CREATE TABLE IF NOT EXISTS bi_reasoning_observations(
      run_id TEXT NOT NULL REFERENCES bi_reasoning_runs(run_id),ordinal INTEGER NOT NULL,
      observation_sha256 TEXT NOT NULL,status TEXT NOT NULL,metadata_json TEXT NOT NULL,
      created_at TEXT NOT NULL,PRIMARY KEY(run_id,ordinal));
    CREATE TABLE IF NOT EXISTS feature_recipe_events(
      event_id TEXT PRIMARY KEY,recipe_id TEXT NOT NULL REFERENCES feature_recipes(recipe_id),
      project_id TEXT NOT NULL REFERENCES projects(id),actor_id TEXT NOT NULL,
      event_type TEXT NOT NULL,from_state TEXT,to_state TEXT NOT NULL,
      evidence_sha256 TEXT NOT NULL,created_at TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS idx_feature_recipe_events
      ON feature_recipe_events(project_id,recipe_id,created_at);
  ''',
 18: '''
    ALTER TABLE bi_ui_preferences ADD COLUMN height INTEGER NOT NULL DEFAULT 670;
  ''',
 19: '''
    CREATE TABLE IF NOT EXISTS feature_data_contracts(
      contract_id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),
      asset_id TEXT NOT NULL,version_id TEXT NOT NULL,revision INTEGER NOT NULL,
      source_sha256 TEXT NOT NULL,state TEXT NOT NULL,contract_json TEXT NOT NULL,
      actor_id TEXT NOT NULL,created_at TEXT NOT NULL,
      UNIQUE(project_id,asset_id,version_id,revision));
    CREATE INDEX IF NOT EXISTS idx_feature_contracts_latest
      ON feature_data_contracts(project_id,asset_id,version_id,revision);
    CREATE TABLE IF NOT EXISTS feature_semantic_bindings(
      binding_id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),
      asset_id TEXT NOT NULL,contract_revision INTEGER NOT NULL,
      evidence_json TEXT NOT NULL,created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS feature_operator_versions(
      operator_id TEXT NOT NULL,operator_version TEXT NOT NULL,manifest_json TEXT NOT NULL,
      PRIMARY KEY(operator_id,operator_version));
  ''',
 20: '''
    CREATE TABLE IF NOT EXISTS feature_profiles(
      profile_id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),
      asset_id TEXT NOT NULL,version_id TEXT NOT NULL,source_sha256 TEXT NOT NULL,
      contract_revision INTEGER NOT NULL,scope TEXT NOT NULL,result_json TEXT NOT NULL,
      created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS feature_jobs(
      job_id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),
      kind TEXT NOT NULL,target_id TEXT NOT NULL,request_id TEXT NOT NULL,
      owner_id TEXT NOT NULL,status TEXT NOT NULL,lease_owner TEXT,lease_until TEXT,
      attempts INTEGER NOT NULL DEFAULT 0,progress REAL NOT NULL DEFAULT 0,
      payload_json TEXT NOT NULL,result_json TEXT,error_code TEXT,error_text TEXT,
      created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
      UNIQUE(project_id,kind,owner_id,request_id));
    CREATE INDEX IF NOT EXISTS idx_feature_jobs_work ON feature_jobs(status,lease_until,created_at);
    CREATE TABLE IF NOT EXISTS feature_profile_evidence(
      evidence_id TEXT PRIMARY KEY,profile_id TEXT NOT NULL REFERENCES feature_profiles(profile_id),
      field_id TEXT NOT NULL,scope TEXT NOT NULL,stats_json TEXT NOT NULL);
  ''',
 21: '''
    CREATE TABLE IF NOT EXISTS feature_sets(
      set_id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),
      owner_id TEXT NOT NULL,name TEXT NOT NULL,asset_id TEXT NOT NULL,version_id TEXT NOT NULL,
      source_sha256 TEXT NOT NULL,contract_revision INTEGER NOT NULL,
      revision INTEGER NOT NULL,spec_json TEXT NOT NULL,spec_sha256 TEXT NOT NULL,
      request_id TEXT NOT NULL, state TEXT NOT NULL,approval_actor TEXT,approval_note TEXT,published_asset_id TEXT,
      created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS idx_feature_sets_scope ON feature_sets(project_id,asset_id,state);
    CREATE UNIQUE INDEX IF NOT EXISTS idx_feature_sets_req ON feature_sets(project_id,owner_id,request_id);
    CREATE TABLE IF NOT EXISTS feature_definitions(
      set_id TEXT NOT NULL REFERENCES feature_sets(set_id),feature_id TEXT NOT NULL,
      operator_id TEXT NOT NULL,output_name TEXT NOT NULL,definition_json TEXT NOT NULL,
      PRIMARY KEY(set_id,feature_id));
    CREATE TABLE IF NOT EXISTS feature_dependencies(
      set_id TEXT NOT NULL REFERENCES feature_sets(set_id),feature_id TEXT NOT NULL,
      dependency_id TEXT NOT NULL,PRIMARY KEY(set_id,feature_id,dependency_id));
    CREATE TABLE IF NOT EXISTS feature_runs(
      run_id TEXT PRIMARY KEY,set_id TEXT NOT NULL REFERENCES feature_sets(set_id),
      project_id TEXT NOT NULL REFERENCES projects(id),revision INTEGER NOT NULL,
      job_id TEXT,source_sha256 TEXT NOT NULL,spec_sha256 TEXT NOT NULL,
      status TEXT NOT NULL,artifact_id TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS feature_run_checkpoints(
      run_id TEXT PRIMARY KEY REFERENCES feature_runs(run_id),stage TEXT NOT NULL,
      source_sha256 TEXT NOT NULL,checkpoint_json TEXT NOT NULL,updated_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS feature_artifacts(
      artifact_id TEXT PRIMARY KEY,run_id TEXT NOT NULL REFERENCES feature_runs(run_id),
      project_id TEXT NOT NULL REFERENCES projects(id),sha256 TEXT NOT NULL,
      relpath TEXT NOT NULL,row_count INTEGER NOT NULL,schema_json TEXT NOT NULL,
      source_sha256 TEXT NOT NULL,created_at TEXT NOT NULL,
      UNIQUE(project_id,sha256,run_id));
  ''',
 22: '''
    CREATE TABLE IF NOT EXISTS feature_tasks(
      task_id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),
      task_json TEXT NOT NULL,created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS feature_evaluation_plans(
      plan_id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),
      set_id TEXT NOT NULL REFERENCES feature_sets(set_id),protocol_json TEXT NOT NULL,
      split_sha256 TEXT NOT NULL,state TEXT NOT NULL,created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS feature_evaluations(
      evaluation_id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),
      plan_id TEXT NOT NULL REFERENCES feature_evaluation_plans(plan_id),
      status TEXT NOT NULL,report_json TEXT,created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS feature_folds(
      evaluation_id TEXT NOT NULL REFERENCES feature_evaluations(evaluation_id),
      fold INTEGER NOT NULL,train_start TEXT NOT NULL,train_end TEXT NOT NULL,
      validation_start TEXT NOT NULL,validation_end TEXT NOT NULL,metrics_json TEXT NOT NULL,
      PRIMARY KEY(evaluation_id,fold));
  ''',
 23: '''
    CREATE TABLE IF NOT EXISTS feature_quality_reports(
      report_id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),
      set_id TEXT NOT NULL REFERENCES feature_sets(set_id),severity TEXT NOT NULL,
      report_json TEXT NOT NULL,created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS feature_publications(
      publication_id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),
      set_id TEXT NOT NULL REFERENCES feature_sets(set_id),artifact_id TEXT NOT NULL REFERENCES feature_artifacts(artifact_id),
      published_asset_id TEXT NOT NULL,manifest_sha256 TEXT NOT NULL,
      manifest_json TEXT NOT NULL,reviewer_id TEXT NOT NULL,created_at TEXT NOT NULL,
      UNIQUE(set_id,artifact_id));
    CREATE TABLE IF NOT EXISTS feature_monitor_snapshots(
      snapshot_id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),
      reference_profile_id TEXT NOT NULL REFERENCES feature_profiles(profile_id),
      current_profile_id TEXT NOT NULL REFERENCES feature_profiles(profile_id),
      report_json TEXT NOT NULL,created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS feature_agent_plans(
      plan_id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),
      actor_id TEXT NOT NULL,request_id TEXT NOT NULL,asset_id TEXT NOT NULL,
      source_sha256 TEXT NOT NULL,plan_json TEXT NOT NULL,status TEXT NOT NULL,
      created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
      UNIQUE(project_id,actor_id,request_id));
    CREATE TABLE IF NOT EXISTS feature_agent_steps(
      step_id TEXT PRIMARY KEY,plan_id TEXT NOT NULL REFERENCES feature_agent_plans(plan_id),
      ordinal INTEGER NOT NULL,tool_id TEXT NOT NULL,args_json TEXT NOT NULL,
      result_json TEXT,status TEXT NOT NULL,created_at TEXT NOT NULL,
      UNIQUE(plan_id,ordinal));
    CREATE TABLE IF NOT EXISTS feature_set_events(
      event_id TEXT PRIMARY KEY,set_id TEXT NOT NULL REFERENCES feature_sets(set_id),
      project_id TEXT NOT NULL REFERENCES projects(id),actor_id TEXT NOT NULL,
      event_type TEXT NOT NULL,evidence_sha256 TEXT NOT NULL,created_at TEXT NOT NULL);
  ''',
 24: '''
    CREATE TABLE IF NOT EXISTS feature_pit_audits(
      audit_id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),
      artifact_id TEXT NOT NULL REFERENCES feature_artifacts(artifact_id),
      task_sha256 TEXT NOT NULL,pit_json TEXT NOT NULL,created_at TEXT NOT NULL,
      UNIQUE(project_id,artifact_id,task_sha256));
  ''',
 25: '''
    CREATE TABLE IF NOT EXISTS feature_preview_receipts(
      preview_id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),
      job_id TEXT NOT NULL REFERENCES feature_jobs(job_id),set_id TEXT NOT NULL REFERENCES feature_sets(set_id),
      scope TEXT NOT NULL,rows_returned INTEGER NOT NULL,source_sha256 TEXT NOT NULL,
      result_sha256 TEXT NOT NULL,created_at TEXT NOT NULL);
  ''',
 26: '''
    CREATE TABLE IF NOT EXISTS feature_set_revisions(
      set_id TEXT NOT NULL REFERENCES feature_sets(set_id),project_id TEXT NOT NULL REFERENCES projects(id),
      revision INTEGER NOT NULL,spec_json TEXT NOT NULL,spec_sha256 TEXT NOT NULL,
      contract_revision INTEGER NOT NULL,actor_id TEXT NOT NULL,created_at TEXT NOT NULL,
      PRIMARY KEY(set_id,revision));
    CREATE TABLE IF NOT EXISTS feature_lab_viewer_state(
      project_id TEXT NOT NULL REFERENCES projects(id),owner_id TEXT NOT NULL,
      set_id TEXT NOT NULL,viewer_revision INTEGER NOT NULL DEFAULT 0,
      source_sha256 TEXT NOT NULL,viewer_json TEXT NOT NULL,updated_at TEXT NOT NULL,
      PRIMARY KEY(project_id,owner_id,set_id));
  ''',
 27: '''
    CREATE TABLE IF NOT EXISTS feature_agent_observations(
      observation_id TEXT PRIMARY KEY,plan_id TEXT NOT NULL REFERENCES feature_agent_plans(plan_id),
      ordinal INTEGER NOT NULL,kind TEXT NOT NULL,evidence_json TEXT NOT NULL,created_at TEXT NOT NULL,
      UNIQUE(plan_id,ordinal,kind));
  ''',
 28: '''
    ALTER TABLE feature_lab_viewer_state ADD COLUMN set_revision INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE feature_lab_viewer_state ADD COLUMN spec_sha256 TEXT NOT NULL DEFAULT '';
    ALTER TABLE feature_lab_viewer_state ADD COLUMN artifact_id TEXT;
    ALTER TABLE feature_lab_viewer_state ADD COLUMN artifact_sha256 TEXT;
  ''',
 29: '''
    CREATE INDEX IF NOT EXISTS idx_feature_artifact_gate
       ON feature_artifacts(project_id,run_id,source_sha256);
    CREATE INDEX IF NOT EXISTS idx_feature_jobs_lease
       ON feature_jobs(status,lease_until,lease_owner);
  ''',
 30: '''
    CREATE TABLE IF NOT EXISTS feature_execution_receipts(
      receipt_id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),
      plan_id TEXT NOT NULL,step_id TEXT,tool_id TEXT NOT NULL,
      status TEXT NOT NULL,result_sha256 TEXT,artifact_id TEXT,
      viewer_receipt_json TEXT,created_at TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS idx_feature_execution_receipts_plan
      ON feature_execution_receipts(project_id,plan_id,created_at);
  ''',
 31: '''
    CREATE TABLE IF NOT EXISTS feature_candidate_sessions(
      session_id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),
      owner_id TEXT NOT NULL,request_id TEXT NOT NULL,asset_id TEXT NOT NULL,
      version_id TEXT NOT NULL,source_sha256 TEXT NOT NULL,contract_revision INTEGER NOT NULL,
      revision INTEGER NOT NULL,state_json TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
      UNIQUE(project_id,owner_id,request_id));
    CREATE INDEX IF NOT EXISTS idx_feature_candidate_sessions_recent
      ON feature_candidate_sessions(project_id,owner_id,asset_id,version_id,updated_at);
  ''',
 32: '''
    CREATE TABLE IF NOT EXISTS feature_evaluation_selections(
      selection_id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),
      evaluation_id TEXT NOT NULL REFERENCES feature_evaluations(evaluation_id),
      owner_id TEXT NOT NULL,revision INTEGER NOT NULL,selection_json TEXT NOT NULL,
      selection_sha256 TEXT NOT NULL,source_sha256 TEXT NOT NULL,spec_sha256 TEXT NOT NULL,
      created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
      UNIQUE(project_id,evaluation_id,owner_id));
    CREATE INDEX IF NOT EXISTS idx_feature_selection_report
      ON feature_evaluation_selections(project_id,evaluation_id,owner_id);
  ''',
 33: '''
    CREATE TABLE IF NOT EXISTS feature_advanced_artifacts(
      job_id TEXT PRIMARY KEY REFERENCES feature_jobs(job_id),
      project_id TEXT NOT NULL REFERENCES projects(id),
      owner_id TEXT NOT NULL,engine TEXT NOT NULL,
      source_fingerprint TEXT NOT NULL,recipe_sha256 TEXT NOT NULL,
      native_asset_id TEXT NOT NULL,artifact_sha256 TEXT NOT NULL,
      result_json TEXT NOT NULL,created_at TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS idx_feature_advanced_by_project
      ON feature_advanced_artifacts(project_id,created_at);
  ''',
 34: '''
    CREATE TABLE IF NOT EXISTS feature_advanced_joins(
      join_id TEXT PRIMARY KEY REFERENCES feature_jobs(job_id),
      project_id TEXT NOT NULL REFERENCES projects(id),owner_id TEXT NOT NULL,
      advanced_job_id TEXT NOT NULL,decision_asset_id TEXT NOT NULL,
      plan_sha256 TEXT NOT NULL,joined_sha256 TEXT NOT NULL,
      native_asset_id TEXT NOT NULL,result_json TEXT NOT NULL,
      created_at TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS idx_feature_advanced_joins_project
      ON feature_advanced_joins(project_id,created_at);
    CREATE TABLE IF NOT EXISTS feature_advanced_join_evaluations(
      evaluation_id TEXT PRIMARY KEY REFERENCES feature_jobs(job_id),
      project_id TEXT NOT NULL REFERENCES projects(id),owner_id TEXT NOT NULL,
      join_id TEXT NOT NULL REFERENCES feature_advanced_joins(join_id),
      source_sha256 TEXT NOT NULL,artifact_sha256 TEXT NOT NULL,
      report_json TEXT NOT NULL,created_at TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS idx_feature_join_eval_project
      ON feature_advanced_join_evaluations(project_id,join_id);
  '''

}

# Keep the v0.23.5 structures for old v4-v6 projects; indexes and FKs are
# additive. Avoid executescript(), which would commit the caller's transaction.
BASE_DDL='''
 CREATE TABLE IF NOT EXISTS bi_ui_preferences(project_id TEXT NOT NULL REFERENCES projects(id),user_id TEXT NOT NULL,asset_id TEXT NOT NULL,mode TEXT NOT NULL DEFAULT 'closed',width INTEGER NOT NULL DEFAULT 440,last_workspace_id TEXT,dismissed_tips TEXT NOT NULL DEFAULT '[]',updated_at TEXT NOT NULL,PRIMARY KEY(project_id,user_id,asset_id));
 CREATE TABLE IF NOT EXISTS import_region_summaries(project_id TEXT NOT NULL REFERENCES projects(id),import_id TEXT NOT NULL,region_id TEXT NOT NULL,fingerprint TEXT NOT NULL,source_sha256 TEXT NOT NULL,algorithm_version TEXT NOT NULL,summary_json TEXT NOT NULL,updated_at TEXT NOT NULL,PRIMARY KEY(project_id,import_id,region_id));
 CREATE TABLE IF NOT EXISTS import_semantic_annotations_v2(project_id TEXT NOT NULL REFERENCES projects(id),import_id TEXT NOT NULL,region_id TEXT NOT NULL,fingerprint TEXT NOT NULL,annotation_json TEXT NOT NULL,model_identifier TEXT,updated_at TEXT NOT NULL,PRIMARY KEY(project_id,import_id,region_id));
 CREATE TABLE IF NOT EXISTS import_summary_jobs(job_id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),import_id TEXT NOT NULL,region_ids_json TEXT NOT NULL,status TEXT NOT NULL,completed INTEGER NOT NULL DEFAULT 0,total INTEGER NOT NULL DEFAULT 0,error TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
 CREATE INDEX IF NOT EXISTS idx_summary_jobs_scope ON import_summary_jobs(project_id,import_id,created_at);
 CREATE TABLE IF NOT EXISTS bi_analysis_plans(plan_id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),workspace_id TEXT NOT NULL,owner_id TEXT NOT NULL,request_id TEXT NOT NULL,message TEXT NOT NULL,expected_revision INTEGER NOT NULL,plan_revision INTEGER NOT NULL,source_sha256 TEXT NOT NULL,status TEXT NOT NULL,plan_json TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,UNIQUE(workspace_id,owner_id,request_id));
 CREATE INDEX IF NOT EXISTS idx_analysis_plans_ws ON bi_analysis_plans(project_id,workspace_id,updated_at);
 CREATE TABLE IF NOT EXISTS bi_analysis_executions(execution_id TEXT PRIMARY KEY,plan_id TEXT NOT NULL REFERENCES bi_analysis_plans(plan_id),request_id TEXT NOT NULL,status TEXT NOT NULL,result_json TEXT,error_code TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,UNIQUE(plan_id,request_id));
 CREATE TABLE IF NOT EXISTS bi_analysis_matrices(analysis_id TEXT PRIMARY KEY,plan_id TEXT NOT NULL REFERENCES bi_analysis_plans(plan_id),project_id TEXT NOT NULL REFERENCES projects(id),owner_id TEXT NOT NULL,source_sha256 TEXT NOT NULL,selection_fingerprint TEXT NOT NULL,matrix_json TEXT NOT NULL,created_at TEXT NOT NULL);
 CREATE INDEX IF NOT EXISTS idx_matrix_plan ON bi_analysis_matrices(plan_id);
'''

def _ddl(db, ddl):
    for statement in ddl.split(';'):
        if not statement.strip():continue
        alter=re.match(r'\s*ALTER\s+TABLE\s+(\w+)\s+ADD\s+COLUMN\s+(\w+)\b',statement,re.I)
        if alter:
            # Allow replay after a restored schema ledger: an existing column
            # must be checked by introspection, never by masking SQL errors.
            if alter.group(2) in {r[1] for r in db.execute('PRAGMA table_info('+alter.group(1)+')')}:
                continue
        db.execute(statement)

def migrate(db:sqlite3.Connection):
    current=db.execute('SELECT version FROM project_schema').fetchone()[0]
    if current > TARGET_SCHEMA or current < 4:
        raise ValueError(f'不支持的项目目录版本 {current}，拒绝自动降级或猜测性升级')
    _ddl(db,BASE_DDL)
    if current < 8:
        # v4-v7 lacked the independently versioned migration ledger. Apply
        # each later change exactly once (ALTER TABLE is not replayable).
        for version in range(8,TARGET_SCHEMA+1):
            _ddl(db,MIGRATIONS[version])
        db.execute('UPDATE project_schema SET version=? WHERE version=?',(TARGET_SCHEMA,current))
    else:
        for version in range(current+1,TARGET_SCHEMA+1):
            _ddl(db,MIGRATIONS[version])
            db.execute('UPDATE project_schema SET version=? WHERE version=?',(version,version-1))
    if db.execute('SELECT version FROM project_schema').fetchone()[0] >= 26:
        db.execute('''INSERT OR IGNORE INTO feature_set_revisions
            (set_id,project_id,revision,spec_json,spec_sha256,contract_revision,actor_id,created_at)
            SELECT set_id,project_id,revision,spec_json,spec_sha256,contract_revision,owner_id,updated_at
            FROM feature_sets''')
    if db.execute('SELECT version FROM project_schema').fetchone()[0] != TARGET_SCHEMA:
        raise RuntimeError('项目目录迁移版本不一致')
