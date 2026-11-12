"""Live project-scoped blockers for logical asset removal.

The persistent reference index covers future workers. Existing built-in jobs
are inspected directly in the SAME SQLite connection as lifecycle transition.
Physical file deletion is never authorized by this check.
"""
from __future__ import annotations

ACTIVE={'queued','running','publishing','prepared','materializing','awaiting_approval'}


def _exists(db, table):
    return bool(db.execute('SELECT 1 FROM sqlite_master WHERE type="table" AND name=?',(table,)).fetchone())


def _columns(db, table):
    return {r[1] for r in db.execute('PRAGMA table_info('+table+')')}


def blockers(db,pid,aid,native_ids,import_ids=()):
    issues=[]
    def collect(sql,args,kind):
        for row in db.execute(sql,args):
            issues.append({'kind':kind,'id':str(row[0]),'status':str(row[1])})
    if _exists(db,'asset_references'):
        collect('''SELECT ref_id,state FROM asset_references WHERE project_id=? AND asset_id=?
          AND state IN (?,?,?,?,?,?)''',(pid,aid,*sorted(ACTIVE)),'registered_reference')
    for nid in native_ids:
        if _exists(db,'project_model_drafts'):
            collect('''SELECT draft_id,status FROM project_model_drafts WHERE project_id=? AND asset_id=?
                AND status IN ('queued','running','publishing')''',(pid,nid),'model_job')
        if _exists(db,'feature_sets'):
            collect('''SELECT set_id,state FROM feature_sets WHERE project_id=? AND asset_id=?
                AND state IN ('draft','pending_review','approved','published')''',
                (pid,nid),'feature_set_v2')
        if _exists(db,'feature_jobs') and _exists(db,'feature_sets'):
            collect('''SELECT j.job_id,j.status FROM feature_jobs j
                LEFT JOIN feature_sets s ON s.set_id=j.target_id
                WHERE j.project_id=? AND (s.asset_id=? OR (j.kind='profile' AND j.target_id=?))
                AND j.status IN ('queued','running','cancel_requested')''',
                (pid,nid,nid),'featurelab_job')
        if _exists(db,'feature_recipes'):
            collect('''SELECT recipe_id,state FROM feature_recipes WHERE project_id=? AND asset_id=?
                AND state IN ('draft','pending_review','approved','publishing','published')''',
                (pid,nid),'feature_recipe')
        if _exists(db,'bi_derived_requests'):
            # This table snapshots sources as structured JSON. Match actual
            # asset IDs through JSON1 rather than substring LIKE queries.
            collect('''SELECT d.derivation_id,d.status FROM bi_derived_requests d
                WHERE d.project_id=? AND d.status IN ('pending_review','approved','publishing','published')
                AND EXISTS (SELECT 1 FROM json_each(d.source_json) x
                    WHERE json_extract(x.value,'$.asset_id')=?)''',(pid,nid),'derived_asset')
        if _exists(db,'bi_agent_workspaces') and _exists(db,'bi_reasoning_runs'):
            collect('''SELECT r.run_id,r.status FROM bi_reasoning_runs r JOIN bi_agent_workspaces w
              ON r.workspace_id=w.workspace_id WHERE r.project_id=? AND w.asset_id=?
              AND r.status='running' ''',(pid,nid),'reasoning')
        if _exists(db,'bi_analysis_plans') and _exists(db,'bi_agent_workspaces'):
            collect('''SELECT p.plan_id,p.status FROM bi_analysis_plans p JOIN bi_agent_workspaces w
              ON p.workspace_id=w.workspace_id WHERE p.project_id=? AND w.asset_id=?
              AND p.status IN ('running','awaiting_confirmation')''',(pid,nid),'analysis_plan')
    if import_ids and _exists(db,'project_publication_jobs'):
        for iid in import_ids:
            collect('''SELECT job_id,status FROM project_publication_jobs WHERE project_id=?
               AND import_id=? AND status IN ('queued','running','prepared')''',(pid,iid),'publication')
    if import_ids and _exists(db,'import_summary_jobs'):
        for iid in import_ids:
            collect('''SELECT job_id,status FROM import_summary_jobs WHERE project_id=?
               AND import_id=? AND status IN ('queued','running')''',(pid,iid),'import_summary')
    return issues


def referenced_imports(db,pid,native_ids):
    if not native_ids or not _exists(db,'project_generic_assets'):return []
    col=_columns(db,'project_generic_assets')
    if not {'native_asset_id','import_id'}<=col:return []
    result=[]
    for nid in native_ids:
        result.extend(x[0] for x in db.execute('''SELECT DISTINCT import_id FROM project_generic_assets
          WHERE project_id=? AND native_asset_id=?''',(pid,nid)))
    return list(dict.fromkeys(result))
