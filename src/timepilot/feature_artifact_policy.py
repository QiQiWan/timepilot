"""Single fail-closed gate for finalized, project-scoped FeatureLab artifacts.

A SQLite artifact row is insufficient: a successful run, successful job, immutable
source/spec, on-disk bytes and (for supervised tasks) PIT evidence must agree.
The same gate is used before publication, evaluation and opening a derived view.
"""
from __future__ import annotations
import json
from pathlib import Path
from timepilot.feature_contracts import FeatureSpecV2, digest
from timepilot.storage import sha256_file
from timepilot.feature_safety import ensure_pit_evidence


def eligible_artifact(projects, pid, rec, semantics=None, *, artifact_id=None, verify_bytes=True):
    root=(projects.home/'feature_lab').resolve()
    params=[pid,rec['set_id'],rec['revision'],rec['source_sha256'],rec['spec_sha256']]
    sql="""SELECT a.*,r.status AS run_status,j.status AS job_status,
        r.revision AS run_revision,r.spec_sha256 AS run_spec,
        r.source_sha256 AS run_source,j.kind AS job_kind
        FROM feature_artifacts a
        JOIN feature_runs r ON r.run_id=a.run_id AND r.project_id=a.project_id
        JOIN feature_jobs j ON j.job_id=r.job_id AND j.project_id=r.project_id
        WHERE a.project_id=? AND r.set_id=? AND r.revision=?
          AND r.source_sha256=? AND r.spec_sha256=?
          AND r.status='succeeded' AND j.status='succeeded'
          AND j.kind='materialize' """
    if artifact_id is not None:
        sql+=' AND a.artifact_id=?';params.append(artifact_id)
    sql+=' ORDER BY a.created_at DESC'
    with projects._db() as db:
        rows=db.execute(sql,params).fetchall()
    spec=FeatureSpecV2.model_validate(rec['spec'])
    if digest(spec)!=rec['spec_sha256']:
        raise ValueError('特征配方指纹不一致，拒绝读取物化结果')
    contract=None
    if spec.task and spec.task.mode=='supervised':
        if semantics is None:raise ValueError('监督特征产物必须核验字段契约及PIT证据')
        contract=semantics.get_revision(pid,rec['asset_id'],rec['version_id'],spec.contract_revision)
    for raw in rows:
        row=dict(raw)
        try:
            rel=Path(row['relpath'])
            path=(root/rel).resolve()
            if rel.is_absolute() or not path.is_relative_to(root) or not path.is_file():continue
            if row['source_sha256']!=rec['source_sha256']:continue
            metadata=json.loads(row['schema_json'])
            if not isinstance(metadata,dict) or metadata.get('scope')!='full':continue
            if (metadata.get('source_sha256')!=rec['source_sha256']
                    or metadata.get('spec_sha256')!=rec['spec_sha256']
                    or metadata.get('artifact_id')!=row['artifact_id']
                    or metadata.get('sha256')!=row['sha256']
                    or int(metadata.get('row_count',-1))!=row['row_count']):continue
            if not path.stat().st_size:continue
            if verify_bytes and sha256_file(path)!=row['sha256']:continue
            if contract is not None:
                ensure_pit_evidence(spec,spec.task,metadata,contract)
                with projects._db() as db:
                    audit=db.execute("""SELECT pit_json FROM feature_pit_audits WHERE
                        project_id=? AND artifact_id=? AND task_sha256=?""",
                        (pid,row['artifact_id'],digest(spec.task))).fetchone()
                if audit is None or json.loads(audit['pit_json'])!=metadata['pit']:continue
            return {'artifact':row,'path':path,'metadata':metadata}
        except (ValueError,TypeError,KeyError,OSError):
            # A corrupt or orphaned row cannot become a publication candidate.
            continue
    return None
