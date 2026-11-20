"""v0.34.3: read-only bridge from advanced artifacts to one review workbench.

Advanced features have *instance-level* PIT grain and must never be spliced
into a row-level FeatureSpecV2. This bridge exposes audited candidate metadata
only; the existing advanced join service performs any approved materialization.
"""
from __future__ import annotations

from timepilot.feature_contracts import digest


def _safe_columns(info):
    # Internal keys, source identifiers, and cutoff evidence must never be
    # presented as trainable features.
    return [x for x in info.get('columns', []) if isinstance(x,str)
            and x and not x.startswith('__')][:256]


def normalize_advanced(info, *, asset_id, version_id='base', source_valid=True, issue=None):
    pins=info.get('sources') or {}
    primary=pins.get('primary') or {}
    if primary.get('asset_id')!=asset_id or primary.get('version_id')!=version_id:
        return None
    job_id=str(info['job_id'])
    status='review_required' if source_valid else 'source_stale'
    group={'batch_id':'adv_'+job_id,'job_id':job_id,'kind':'advanced',
           'engine':info['engine'],'family':'advanced','family_label':'高级算法',
           'status':status,'issue':issue,'rows':int(info.get('rows') or 0),
           'source_fingerprint':info.get('source_fingerprint'),
           'artifact_sha256':info.get('sha256'),
           'recipe_sha256':info.get('recipe_sha256'),
           'native_asset_id':info.get('native_asset_id'),
           'sources':pins,'pit':info.get('pit'),
           'requires_pit_join':True,'published':False,
           'preview_only':True,'candidates':[],
           'workflow_status':status,'join':None,'evaluation':None}
    if source_valid:
        for col in _safe_columns(info):
            group['candidates'].append({'candidate_id':'adv_'+digest([job_id,col])[:24],
               'column':col,'display_name':col,'family':'advanced',
               'family_label':'高级算法','engine':info['engine'],
               'status':'review_required','batch_id':group['batch_id'],
               'source_fingerprint':info.get('source_fingerprint'),
               'artifact_sha256':info.get('sha256'),
               'information_status':'not_evaluated','model_status':'not_evaluated',
               'requires_pit_join':True,'included_in_base_selection':False,
               'grain':'prediction_instance_or_event_series',
               'operator_provenance':info.get('recipe_sha256'),
               'source_columns':info.get('input_columns') or [],
               'evaluation_ref':None})
    group['candidate_count']=len(group['candidates'])
    return group


def list_advanced_batches(pid, aid, vid, *, projects, native, semantic, registry, advanced, limit=12, join_service=None):
    """Project + owner scoped listing with per-source byte and contract checks."""
    projects.require_native(pid,aid)
    owner=registry._actor()
    limit=min(30,max(1,int(limit)))
    with projects._db() as db:
        rows=db.execute('''SELECT job_id FROM feature_advanced_artifacts
            WHERE project_id=? AND owner_id=? ORDER BY created_at DESC LIMIT ?''',
            (pid,owner,limit)).fetchall()
    current=semantic.source(pid,aid,vid)
    batches=[]
    joined_by_job={}
    if join_service is not None:
        with projects._db() as db:
            joins=db.execute('''SELECT join_id,advanced_job_id FROM feature_advanced_joins
                WHERE project_id=? AND owner_id=? ORDER BY created_at DESC LIMIT 60''',
                (pid,owner)).fetchall()
        for joined in joins:
            joined_by_job.setdefault(joined['advanced_job_id'],[]).append(joined['join_id'])
    for row in rows:
        # AdvancedService.result enforces owner scope and verifies native bytes.
        try:info=advanced.result(pid,row['job_id'])
        except (KeyError,ValueError):continue
        pins=info.get('sources') or {}
        primary=pins.get('primary') or {}
        if primary.get('asset_id')!=aid or primary.get('version_id')!=vid:continue
        issue=None
        for tag,pin in pins.items():
            try:
                projects.require_native(pid,pin['asset_id'])
                source=semantic.source(pid,pin['asset_id'],pin['version_id'])
                contract=semantic.latest(pid,pin['asset_id'],pin['version_id'],source=source)
                if source['sha256']!=pin['sha256'] or contract.revision!=pin['contract_revision'] or contract.state!='confirmed':
                    issue='上游数据指纹或字段契约已变化'
                elif not native.verify(pin['asset_id']).get('valid'):
                    issue='上游原始数据字节校验失败'
            except (KeyError,ValueError,PermissionError):
                issue='上游来源不可访问或版本失效'
            if issue:break
        if current['sha256']!=primary.get('sha256'):
            issue='当前数据版本来源已变化'
        normalized=normalize_advanced(info,asset_id=aid,version_id=vid,
                                       source_valid=issue is None,issue=issue)
        if normalized:
            if issue is None and join_service is not None:
                for join_id in joined_by_job.get(row['job_id'],[]):
                    try:
                        joined=join_service.result(pid,join_id)
                    except (KeyError,ValueError,PermissionError):
                        continue
                    if joined.get('candidate_job_id')!=row['job_id'] or joined.get('advanced_candidate_sha256')!=info.get('sha256'):
                        continue
                    normalized['join']={'join_id':join_id,'joined_sha256':joined['joined_sha256'],
                        'rows':joined['rows'],'native_asset_id':joined['native_asset_id'],
                        'status':'pit_join_verified'}
                    normalized['workflow_status']='pit_join_verified'
                    with projects._db() as db:
                        ev=db.execute('''SELECT evaluation_id FROM feature_advanced_join_evaluations
                            WHERE project_id=? AND owner_id=? AND join_id=? ORDER BY created_at DESC LIMIT 1''',
                            (pid,owner,join_id)).fetchone()
                    if ev:
                        try:review=join_service.evaluation_result(pid,ev['evaluation_id'])
                        except (KeyError,ValueError,PermissionError):review=None
                        if review and review['report'].get('evaluated_artifact_sha256')==joined['joined_sha256']:
                            normalized['evaluation']={'evaluation_id':ev['evaluation_id'],
                                'joined_sha256':joined['joined_sha256'],
                                'task_type':review['report'].get('task_type'),
                                'paired_comparison':review['report'].get('paired_comparison')}
                            normalized['workflow_status']='model_evaluated'
                    break
                if normalized['join']:
                    for c in normalized['candidates']:
                        c['status']=normalized['workflow_status']
                        c['model_status']='evaluated' if normalized['evaluation'] else 'not_evaluated'
                        c['evaluation_ref']=normalized['evaluation']['evaluation_id'] if normalized['evaluation'] else None
            batches.append(normalized)
    return {'asset_id':aid,'version_id':vid,'source_sha256':current['sha256'],
            'batches':batches,'candidate_count':sum(b['candidate_count'] for b in batches),
            'scope':'advanced_metadata_only','requires_explicit_confirmation':True,
            'warning':'高级候选和普通逐行候选的数据粒度可能不同；须完成 PIT 关联、来源校验及人工确认，才能进入模型评价。'}
