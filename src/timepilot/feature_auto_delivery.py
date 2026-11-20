"""V0.32.2: verified, same-source delivery for automatic FeatureLab.

Read-only linkage of a *specific* immutable feature artifact, its evaluation and
an acknowledged Perspective session. The server never accepts a client-supplied
filesystem path, evaluation metrics, feature SHA or arbitrary tool instructions.
"""
from __future__ import annotations

from urllib.parse import quote
import json

from timepilot.feature_contracts import TaskSpecV2, FeatureSpecV2, digest
from timepilot.feature_safety import ensure_supervised_contract
from timepilot.feature_artifact_policy import eligible_artifact


SUPERVISED_OBJECTIVES = {'classification': 'binary_classification',
                         'regression': 'regression', 'forecast': 'regression'}


def supervision_options(contract, objective: str) -> dict:
    """Read-only, evidence-restricted choices; never infer an actual label."""
    fields = contract.fields
    confirmed = [f for f in fields if f.confirmation == 'confirmed']
    labels = [f for f in confirmed if f.role == 'label' and f.semantic_type not in ('datetime', 'id')]
    predictions = [f for f in confirmed if f.role == 'prediction_time' and f.semantic_type == 'datetime']
    available = [f for f in confirmed if f.role == 'available_at' and f.semantic_type == 'datetime'
                 and f.field_id != contract.available_at_field_id]
    reasons = []
    if objective not in SUPERVISED_OBJECTIVES:
        reasons.append('当前分析目标未选择监督分类/回归/预测')
    if contract.state != 'confirmed': reasons.append('必须先核实字段契约')
    if not contract.timezone: reasons.append('必须核实数据时区')
    if not contract.event_time_field_id or not contract.available_at_field_id:
        reasons.append('缺少事件时间或过程数据可用时间')
    if not contract.entity_field_ids: reasons.append('缺少已确认的设备/实体字段')
    if not labels: reasons.append('缺少已确认的标签字段')
    if not predictions: reasons.append('缺少已确认的预测决策时点')
    if not available: reasons.append('缺少独立于过程数据可用时点的标签可用时间')
    # Missing a timestamp does not imply independence. Static evaluation is
    # offered separately and always requires an explicit human attestation.
    static_issues=[]
    if objective not in SUPERVISED_OBJECTIVES:static_issues.append('当前分析目标不是回归或分类')
    if contract.state!='confirmed':static_issues.append('静态监督任务需确认数据契约')
    if not labels:static_issues.append('静态监督任务缺少经确认的数值标签')
    groups=[f for f in confirmed if f.role in ('entity_key','batch_key')]
    if contract.entity_field_ids and any(fid not in {f.field_id for f in groups}
                                          for fid in contract.entity_field_ids):
        static_issues.append('设备实体尚未确认')
    # A same-named timestamp may have different availability semantics. Require
    # user selection; never silently bind label availability to event_time.
    def pick(items):
        return [{'field_id':f.field_id,'name':f.source_name,'role':f.role,
                 'physical_type':f.physical_type} for f in items]
    return {'eligible':not reasons,'issues':reasons,
            'temporal_eligible':not reasons,
            'static_eligible':not static_issues,'static_issues':static_issues,
            'static_default_split':('group' if contract.entity_field_ids else
                                    'stratified' if objective=='classification' else 'kfold'),
            'static_group_fields':pick(groups),
            'task_type':SUPERVISED_OBJECTIVES.get(objective),
            'label_fields':pick(labels),'prediction_time_fields':pick(predictions),
            'label_available_fields':pick(available),
            'entity_field_ids':list(contract.entity_field_ids),
            'contract_revision':contract.revision,'source_sha256':contract.source_sha256,
            'requires_human_confirmation':True}


def confirmed_task(contract, *, objective: str, session_id: str,
                   label_field_id: str, prediction_time_field_id: str | None = None,
                   label_available_at_field_id: str | None = None, split_kind='time',
                   group_field_ids=None, static_independence_confirmed=False) -> TaskSpecV2:
    options = supervision_options(contract, objective)
    static=split_kind in ('kfold','stratified','group')
    if static and not options['static_eligible']:
        raise ValueError('静态监督任务条件不足：'+'；'.join(options['static_issues']))
    if not static and not options['eligible']:
        raise ValueError('监督任务尚未具备可验证的契约：'+'；'.join(options['issues']))
    by_id = {f.field_id:f for f in contract.fields}
    def require(fid, role):
        field = by_id.get(fid)
        if field is None or field.confirmation != 'confirmed' or field.role != role:
            raise ValueError('监督任务字段未完成业务确认：'+str(fid))
        return field
    label=require(label_field_id,'label')
    if label.semantic_type!='numeric':
        raise ValueError('监督任务标签需要确认数值语义（分类标签为0/1）')
    groups=list(group_field_ids or [])
    if split_kind not in {'time','grouped_time','kfold','stratified','group'}:
        raise ValueError('不支持的验证切分协议')
    if static:
        if not static_independence_confirmed:
            raise ValueError('请确认静态样本独立性与预测时点的输入特征可用性')
        if prediction_time_field_id or label_available_at_field_id:
            raise ValueError('静态任务无需填写预测时点与标签可用时间')
        if split_kind=='group' and not groups:
            groups=list(contract.entity_field_ids)
    else:
        require(prediction_time_field_id,'prediction_time')
        require(label_available_at_field_id,'available_at')
        if label_available_at_field_id==contract.available_at_field_id:
            raise ValueError('标签可用时间必须独立于过程数据可用时间')
        if label_field_id in {prediction_time_field_id, label_available_at_field_id}:
            raise ValueError('标签不可与时间字段重复')
    if split_kind in ('grouped_time','group') and not groups:
        raise ValueError('分组验证需要已确认的设备/批次分组字段')
    for fid in groups:
        f=by_id.get(fid)
        if not f or f.confirmation!='confirmed' or f.role not in ('entity_key','batch_key'):
            raise ValueError('分组切分字段必须是已确认的设备/批次键')
    if len(groups)>3 or len(groups)!=len(set(groups)):
        raise ValueError('分组切分字段数量或重复值无效')
    task = TaskSpecV2(task_id='auto-'+session_id[:90],mode='supervised',
       task_type=SUPERVISED_OBJECTIVES[objective],
       entity_field_ids=list(contract.entity_field_ids),
       prediction_time_field_id=prediction_time_field_id,
       label_field_id=label_field_id,
       label_available_at_field_id=label_available_at_field_id,
       split_kind=split_kind,group_field_ids=groups,
       static_independence_confirmed=bool(static))
    ensure_supervised_contract(contract,task)
    return task


def verified_delivery(projects, semantic, registry, jobs, pid: str, state:dict) -> dict:
    """Recheck bytes and all identities every time the deliverable is opened.

    An evaluation's report alone, a queued job, or a client URL cannot establish
    same-artifact evidence. A viewer receipt remains explicitly *client reported*.
    """
    summary={'schema_version':1,'session_id':state['session_id'],
       'source':{'asset_id':state['asset_id'],'version_id':state['version_id'],
                 'sha256':state['source_sha256'],'contract_revision':state['contract_revision']},
       'feature_set':None,'artifact':None,'evaluation':None,'bi':None,
       'status':'analysis_only','same_artifact_evaluation':False,
       'same_artifact_bi_receipt':False,
       'production_verified':False,'limitations':[]}
    if state.get('stale'):
        summary['status']='stale';summary['limitations'].append('数据或字段契约已变化，不能复用旧产物')
        return summary
    set_id=state.get('feature_set_id')
    if not set_id: return summary
    rec=registry.get(pid,set_id)
    if (rec['asset_id']!=state['asset_id'] or rec['version_id']!=state['version_id']
        or rec['source_sha256']!=state['source_sha256']
        or rec['contract_revision']!=state['contract_revision']):
        summary['status']='blocked';summary['limitations'].append('特征集与分析源不一致')
        return summary
    summary['feature_set']={'set_id':set_id,'revision':rec['revision'],
                            'spec_sha256':rec['spec_sha256'],'state':rec['state']}
    expected=state.get('artifact') or {}
    if not expected.get('artifact_id'):
        summary['status']='draft_only';return summary
    valid=eligible_artifact(projects,pid,rec,semantic,artifact_id=expected['artifact_id'])
    if (valid is None or valid['artifact']['sha256']!=expected.get('sha256')
          or valid['metadata'].get('spec_sha256')!=rec['spec_sha256']):
        summary['status']='blocked';summary['limitations'].append('物化文件或来源哈希不匹配')
        return summary
    row=valid['artifact']
    artifact={'artifact_id':row['artifact_id'],'sha256':row['sha256'],
              'row_count':row['row_count'],'source_sha256':row['source_sha256'],
              'spec_sha256':rec['spec_sha256'],
              'pit_status':valid['metadata'].get('pit',{}).get('status','not_applicable')}
    summary['artifact']=artifact
    summary['status']='materialized'
    spec=FeatureSpecV2.model_validate(rec['spec'])
    summary['mode']='supervised' if spec.task and spec.task.mode=='supervised' else 'exploration'
    # Native-BI WS already enforces project/source/set membership and eligible_artifact.
    # The client must mount it and send its own rendering receipt for UI evidence.
    parts=[quote(str(v),safe='') for v in (state['asset_id'],state['version_id'],row['artifact_id'])]
    summary['bi']={
       'status':'not_confirmed',
       'ws_path':f'/api/v1/native-bi/assets/{parts[0]}/versions/{parts[1]}/feature-artifacts/{parts[2]}/ws?project_id={quote(pid,safe="")}',
       'artifact_id':row['artifact_id'],'artifact_sha256':row['sha256'],
       'client_reported':True}
    pending=state.get('bi_receipt') or {}
    if (pending.get('artifact_id')==row['artifact_id']
          and pending.get('artifact_sha256')==row['sha256']
          and pending.get('panel_count',0)>=2):
        summary['bi']['status']='client_reported_rendered'
        summary['bi']['workspace_sha256']=pending.get('workspace_sha256')
        summary['same_artifact_bi_receipt']=True
    else: summary['limitations'].append('尚无同一产物的真实客户端双面板回执')
    ev=state.get('evaluation') or {}
    report=ev.get('report') or {}
    if state.get('evaluation_job_id') and state.get('stage')=='evaluated' and report:
        # Provenance must originate from an actually completed, project-scoped
        # evaluation job and persisted evaluation row for this *exact* FeatureSet.
        job=jobs.get_job(pid,state['evaluation_job_id'])
        with projects._db() as db:
            record=db.execute('''SELECT e.report_json,e.status,p.set_id FROM feature_evaluations e
                 JOIN feature_evaluation_plans p ON p.plan_id=e.plan_id
                 WHERE e.project_id=? AND e.evaluation_id=?''',
                 (pid,ev.get('evaluation_id'))).fetchone()
        if (job.get('status')!='succeeded' or job.get('kind')!='evaluation'
            or (job.get('result') or {}).get('evaluation_id')!=ev.get('evaluation_id')
            or record is None or record['status']!='succeeded' or record['set_id']!=set_id
            or digest(report)!=digest(json.loads(record['report_json']))):
            summary['status']='blocked'
            summary['limitations'].append('评价结果缺少同一任务的持久化执行证据')
            return summary
        same_source=(report.get('source_sha256')==state['source_sha256']
                     and report.get('spec_sha256')==rec['spec_sha256'])
        if summary['mode']=='supervised':
            same=same_source and report.get('mode')=='supervised' and (
                report.get('evaluated_artifact_id')==row['artifact_id'] and
                report.get('evaluated_artifact_sha256')==row['sha256'] and
                report.get('evaluated_contract_revision')==state['contract_revision'])
        else:
            referenced=report.get('feature_artifact') or {}
            same=(same_source and report.get('mode')=='exploration' and
                  referenced.get('artifact_id')==row['artifact_id'] and
                  referenced.get('sha256')==row['sha256'])
        if same:
            summary['evaluation']={'evaluation_id':ev.get('evaluation_id'),
                'mode':report.get('mode'),'artifact_id':row['artifact_id'],
                'artifact_sha256':row['sha256'],
                'summary':report.get('screening_recommendations',[])[:8] if report.get('mode')=='supervised' else [],
                'split_kind':report.get('split_kind') if report.get('mode')=='supervised' else None,
                'fold_count':len(report.get('folds') or []) if report.get('mode')=='supervised' else None,
                'selection_stability':report.get('selection_stability') if report.get('mode')=='supervised' else None,
                'paired_comparison':report.get('paired_comparison') if report.get('mode')=='supervised' else None,
                # Only after the persisted report and artifact hashes have been verified.
                'feature_group_ablation':report.get('feature_group_ablation') if report.get('mode')=='supervised' else None,
                'industrial_transfer_holdout':report.get('industrial_transfer_holdout') if report.get('mode')=='supervised' else None,
                'variant_comparison':report.get('variant_comparison') if report.get('mode')=='supervised' else None,
                'predictive_gain_claimed':False}
            summary['same_artifact_evaluation']=True
            summary['status']='evaluated'
        else:
            summary['status']='blocked'
            summary['limitations'].append('评价报告绑定的物化产物不同，禁止作为同源结果')
    else:summary['limitations'].append('尚无完成并校验同一 Parquet 的评价')
    if summary['same_artifact_bi_receipt'] and summary['same_artifact_evaluation']:
        summary['status']='app_evidence_linked'
    # Product release requires independent real-browser and industrial QA gates.
    summary['production_verified']=False
    return summary
