"""Evidence-only analytical brief for the unified FeatureLab workspace (v0.35.5).

No LLM is needed, no SQL is accepted, and no data is modified. Claims are
constructed exclusively from source-pinned profiles, computed candidate quality
and *verified same-artifact* evaluation records. In particular a label's mere
presence must never manufacture an estimate of predictive improvement.
"""
from __future__ import annotations

from collections import Counter
import math


def _finite(value):
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        return None
    try:
        return float(value) if math.isfinite(float(value)) else None
    except (ValueError, OverflowError):
        return None


def _evidence(kind, path, source, revision, **extra):
    return {'kind': kind, 'path': path, 'source_sha256': source,
            'contract_revision': revision, **extra}


def _finding(code, title, observation, *, evidence, action='review', level='information'):
    return {'code': code, 'title': title, 'observation': observation,
            'evidence': evidence, 'action': action, 'level': level,
            'claim_type': 'descriptive_observation'}


def _paired_model(report, source, revision, evaluation_id, artifact_sha):
    """Summarize persisted per-fold metrics; never infer unreported values."""
    folds = report.get('folds') or []
    model = (report.get('model_families') or [None])[0]
    task = report.get('task_type')
    primary = 'mae' if task == 'regression' else 'average_precision' if task == 'binary_classification' else None
    if not primary or not model or not folds:
        return {'status': 'unavailable', 'message': '模型评价缺少可对照的验证折或主要指标', 'comparisons': []}
    paired = report.get('paired_comparison') or {}
    paired_metric = (paired.get('metrics') or {}).get(primary) or {}
    comparisons = []
    for key, label in [('raw_only', '原始字段'), ('with_features', '全部候选'),
                       ('selected_features', '筛选后特征')]:
        values=[]
        for fold in folds:
            metrics=(fold.get('model_metrics') or {}).get(model) or fold.get('metrics') or {}
            value=_finite((metrics.get(key) or {}).get(primary))
            if value is not None:
                values.append(value)
        comparisons.append({'variant': key, 'label': label,
                            'mean': round(sum(values)/len(values), 8) if values else None,
                            'valid_folds': len(values)})
    # A comparison is publishable only when *all three* variants have valid,
    # aligned metrics for the same outer folds. Recalculate the positive-fold
    # count, and reject summary totals that disagree with the raw fold records.
    delta=_finite(paired_metric.get('mean_improvement'))
    declared_positive=None
    for row in report.get('screening_recommendations') or []:
        if row.get('variant') == 'with_features' and row.get('objective') == primary:
            declared_positive=row.get('positive_folds')
            break
    n=int(paired_metric.get('n_paired_folds') or 0)
    differences=[]
    for fold in folds:
        metrics=(fold.get('model_metrics') or {}).get(model) or fold.get('metrics') or {}
        base=_finite((metrics.get('raw_only') or {}).get(primary))
        enhanced=_finite((metrics.get('with_features') or {}).get(primary))
        if base is None or enhanced is None:
            break
        differences.append(base-enhanced if primary=='mae' else enhanced-base)
    consistent_folds=(len(differences)==len(folds))
    positive=sum(v>0 for v in differences) if consistent_folds else None
    expected=(sum(differences)/n) if consistent_folds and n==len(folds) else None
    consistent_mean=(delta is not None and expected is not None and abs(delta-expected)<1e-5)
    consistent_positive=(declared_positive is None or
                         (type(declared_positive) is int and declared_positive==positive))
    complete=(paired.get('same_fold_only') is True and n == len(folds) and n >= 2 and
              all(x['valid_folds'] == len(folds) for x in comparisons))
    sufficient=complete and consistent_folds and consistent_mean and consistent_positive
    if not sufficient:
        # Never surface partial means as a full three-arm model comparison.
        return {'status':'insufficient_evidence',
                'message':'模型评价存在缺失折、记录冲突或同折证据不足，不展示预测收益。',
                'comparisons':[],
                'limitations':['请核对每折三种方案指标及同折汇总后重新评价。']}
    if delta > 0 and positive >= max(2, math.ceil(n*0.6)):
        status='promising_in_validation'
        message=f'{n} 个同折验证中有 {positive} 折改善，平均改善 {delta:.4g}；仍需独立测试集确认。'
    elif delta > 0:
        status='unstable_or_small_gain'
        message=f'同折平均改善 {delta:.4g}，折间一致性尚不足以确认稳定收益。'
    elif delta < 0:
        status='no_gain'
        message=f'同折平均指标变化为不利方向（改善量 {delta:.4g}）；当前方案未显示收益。'
    else:
        status='no_gain'
        message='同折平均改善为零，当前方案未显示收益。'
    # v0.37.3: three-arm comparison must use the identical verified folds.
    # The selection decision never reads the final test set.
    variant_deltas=[]
    base_mean=comparisons[0]['mean']
    for row in comparisons[1:]:
        values=[]
        for fold in folds:
            metrics=(fold.get('model_metrics') or {}).get(model) or fold.get('metrics') or {}
            base=_finite((metrics.get('raw_only') or {}).get(primary))
            derived=_finite((metrics.get(row['variant']) or {}).get(primary))
            values.append((base-derived) if primary=='mae' else (derived-base))
        improvement=sum(values)/len(values)
        variant_deltas.append({'variant':row['variant'],'baseline_variant':'raw_only',
           'mean_delta':round(improvement,8),'fold_deltas':[round(x,8) for x in values],
           'positive_folds':sum(x>0 for x in values),
           'verified_fold_count':len(values),
           'verdict':'validation_gain' if improvement>0 and sum(x>0 for x in values)>=max(2,math.ceil(len(values)*.6))
              else 'validation_regression' if improvement<0 else 'unstable_or_no_gain',
           'metric':primary})
    supporting=[]
    for support in (('recall','false_reject_rate') if task=='binary_classification' else ('rmse',)):
        rows=[]
        for key in ('raw_only','with_features','selected_features'):
            values=[]
            for fold in folds:
                metrics=(fold.get('model_metrics') or {}).get(model) or fold.get('metrics') or {}
                val=_finite((metrics.get(key) or {}).get(support))
                if val is not None:values.append(val)
            rows.append(round(sum(values)/len(values),8) if len(values)==len(folds) else None)
        if all(x is not None for x in rows):
            supporting.append({'metric':support,'values':rows,'fold_count':len(folds)})
    return {'status':status, 'task_type':task, 'metric':primary,
            'metric_label':'MAE（越低越好）' if primary=='mae' else 'AP（越高越好）',
            'split_kind':report.get('split_kind'), 'fold_count':len(folds),
            'paired_folds':n, 'mean_improvement':round(delta,8) if delta is not None else None,
            'positive_folds':positive, 'comparisons':comparisons,
            'variant_deltas':variant_deltas,
            'test_set_claimed':False,
            'value_scope':'outer_validation_folds_only',
            'supporting_metrics':supporting, 'message':message,
            'evidence':[_evidence('model_evaluation', 'report.paired_comparison.metrics.'+primary,
                     source, revision, evaluation_id=evaluation_id, artifact_sha256=artifact_sha)],
            'limitations':['描述性同折验证，不代表因果关系、统计显著性或生产收益。',
                           '候选生成可能利用全源无监督画像；独立最终测试前不可宣称泛化已证实。']}


def build_insight_brief(*, source_sha256, contract_revision, data_analysis=None,
                        session=None, delivery=None, contract=None):
    """Build a small provenance-pinned JSON response from trusted server records.

    Caller must authorize project/actor, reverify source bytes and call
    verified_delivery before providing evaluation proof. This pure function
    re-checks identities and refuses model results without confirmed labels.
    """
    source=source_sha256
    rev=contract_revision
    brief={'schema_version':1, 'status':'ready', 'source_sha256':source,
           'contract_revision':rev, 'requires_model_api':False,
           'layers':{'data_quality':{'status':'pending','findings':[]},
                     'feature_quality':{'status':'pending','findings':[]},
                     'information_value':{'status':'pending','findings':[]},
                     'model_value':{'status':'not_evaluated','findings':[]}},
           'findings':[], 'model':{'status':'not_evaluated',
                                 'message':'尚无可靠的模型对照证据；不会生成预测收益数字。',
                                 'comparisons':[]},
           'limitations':['统计相关与模型消融不构成因果关系或生产性能证明。']}
    if session and (session.get('stale') or session.get('source_sha256') != source
                    or session.get('contract_revision') != rev):
        brief.update(status='stale', model={'status':'blocked',
                          'message':'来源或字段契约已变化，旧模型结果不可用于当前版本。',
                          'comparisons':[]})
        brief['limitations'].append('旧会话存在来源或契约冲突，已停止当前研判。')
        return brief
    if data_analysis and (data_analysis.get('source_sha256') != source or
                          data_analysis.get('contract_revision') != rev):
        # Stale profile evidence is discarded, without hiding valid session data.
        data_analysis=None
    if data_analysis and data_analysis.get('status')=='ready':
        profile=data_analysis.get('profile') or {}
        fields=profile.get('fields') or []
        misses=[]
        for field in fields:
            missing=_finite(field.get('null_rate'))
            if missing is not None and missing>=0.1 and field.get('field_id'):
                misses.append((missing,field))
        misses.sort(key=lambda x:-x[0])
        quality=brief['layers']['data_quality']
        quality['status']='measured'
        quality['metrics']={'scanned_fields':len(fields),'high_missing_fields':len(misses),
                            'row_count':data_analysis.get('row_count')}
        for missing,f in misses[:3]:
            i=next((i for i,x in enumerate(fields) if x.get('field_id')==f['field_id']),0)
            finding=_finding('missing:'+str(f['field_id']), '检查缺失数据',
                f"{f.get('source_name') or '字段'} 缺失率 {missing*100:.1f}%，建议核查采集和填补规则。",
                evidence=[_evidence('data_profile',f'profile.fields[{i}].null_rate',source,rev,
                                    field_id=f['field_id'], value=missing)],action='quality',level='review')
            quality['findings'].append(finding)
            brief['findings'].append(finding)
        if not misses:
            finding=_finding('profile-complete','基础质量检查已完成',
                f"已读取 {len(fields)} 个允许显示字段的画像，本次未发现缺失率达到 10% 的字段。",
                evidence=[_evidence('data_profile','profile.fields',source,rev)],action='statistics')
            quality['findings'].append(finding)
            brief['findings'].append(finding)
    if session and (session.get('report') or {}).get('quality_rows') is not None:
        report=session['report']
        items=report.get('quality_rows') or []
        counts=Counter(row.get('status') or 'unknown' for row in items)
        quality=brief['layers']['feature_quality']
        quality['status']='measured'
        quality['metrics']={'evaluated_candidates':len(items),
                            'quality_passed':counts['recommended'],
                            'needs_review':counts['review'],
                            'excluded':counts['exclude'],
                            'unverified':counts['unverified']}
        evidence=[_evidence('candidate_quality','report.quality_rows',source,rev,
                           session_id=session.get('session_id'))]
        msg=(f"{len(items)} 项候选已生成质量记录，其中 {counts['recommended']} 项建议保留、"
             f"{counts['review']} 项待复核、{counts['exclude']} 项建议排除。"
             "质量通过只说明可计算与有效覆盖，不能据此推断预测收益。")
        finding=_finding('feature-quality','候选特征质量',msg,evidence=evidence,
                         action='features',level='information')
        quality['findings'].append(finding)
        brief['findings'].append(finding)
        # A full-source variance check describes statistical spread only;
        # it does NOT establish non-redundancy, information gain or target value.
        measured=[(i, _finite((r.get('quality') or {}).get('std'))) for i,r in enumerate(items)]
        measured=[(i,v) for i,v in measured if v is not None]
        info=brief['layers']['information_value']
        info['status']='descriptive_only' if measured else 'not_evaluated'
        info['metrics']={'variance_checked':len(measured),
                         'zero_variance':sum(abs(v)<1e-12 for _,v in measured)}
        if measured:
            nzero=info['metrics']['zero_variance']
            evidence=[_evidence('candidate_statistics','report.quality_rows[*].quality.std',
                                source,rev,session_id=session.get('session_id'))]
            observation=(f'{len(measured)} 项候选具有计算后的标准差证据，其中 {nzero} 项接近常量。'
                         '尚未完成特征之间的冗余或信息增益评价，不能单凭波动大小判断价值。')
            finding=_finding('feature-variance','特征统计变化',observation,
                             evidence=evidence,action='features')
            info['findings'].append(finding)
            brief['findings'].append(finding)
    confirmed_label=bool(contract and any(getattr(f,'role',None)=='label' and
                          getattr(f,'confirmation',None)=='confirmed' and
                          getattr(f,'classification',None)!='restricted'
                          for f in getattr(contract,'fields',[])))
    if not confirmed_label:
        brief['model']={'status':'no_confirmed_label',
            'message':'当前没有可使用的已确认标签。已完成的统计与特征质量分析不包含预测收益评价。',
            'comparisons':[]}
        brief['layers']['model_value']['status']='no_confirmed_label'
        return brief
    proof=(delivery or {})
    ev=proof.get('evaluation') or {}
    report=(((session or {}).get('evaluation') or {}).get('report') or {})
    same=bool(proof.get('same_artifact_evaluation') is True and
        ev.get('mode')=='supervised' and report.get('mode')=='supervised' and
        (proof.get('artifact') or {}).get('sha256') and
        (proof.get('artifact') or {}).get('sha256')==ev.get('artifact_sha256') and
        report.get('source_sha256')==source and
        report.get('evaluated_artifact_sha256')==ev.get('artifact_sha256') and
        report.get('evaluated_contract_revision')==rev and
        (not report.get('evaluated_artifact_id') or
         report.get('evaluated_artifact_id')==(proof.get('artifact') or {}).get('artifact_id')) and
        (not report.get('spec_sha256') or report.get('spec_sha256')==
         (proof.get('feature_set') or {}).get('spec_sha256')) and
        (not (session.get('evaluation') or {}).get('evaluation_id') or
         (session.get('evaluation') or {}).get('evaluation_id')==ev.get('evaluation_id')))
    if not same:
        brief['model']={'status':'awaiting_verified_evaluation',
            'message':'已确认标签，但还没有通过同源 SHA256 校验的监督模型对照；完成正式特征计算后可选择评价。',
            'comparisons':[]}
        brief['layers']['model_value']['status']='awaiting_verified_evaluation'
        return brief
    objective=_paired_model(report,source,rev,ev.get('evaluation_id'),ev['artifact_sha256'])
    brief['model']=objective
    brief['layers']['model_value']['status']='measured' if objective['status']!='unavailable' else 'insufficient_evidence'
    if objective['status'] not in ('insufficient_evidence','unavailable'):
        finding=_finding('paired-model','同折模型对照',objective['message'],
                         evidence=objective['evidence'],action='outputs',
                         level='review' if objective['status'] in ('no_gain','unstable_or_small_gain') else 'information')
        brief['layers']['model_value']['findings'].append(finding)
        brief['findings'].append(finding)
    stability=report.get('selection_stability') or {}
    jaccard=_finite(stability.get('mean_jaccard'))
    if jaccard is not None and stability.get('compared_pairs',0)>0:
        brief['model']['selection_stability']={
            'mean_jaccard':jaccard, 'compared_pairs':stability['compared_pairs'],
            'evidence':[_evidence('model_evaluation','report.selection_stability',source,rev,
                    evaluation_id=ev['evaluation_id'],artifact_sha256=ev['artifact_sha256'])],
            'interpretation':'仅描述训练折间入选特征的一致程度，不代表预测收益。'}
    groups=(report.get('feature_group_ablation') or {}).get('groups') or []
    clean_groups=[]
    for g in groups[:3]:
        loss=_finite(g.get('mean_loss_when_removed'))
        if loss is None:continue
        clean_groups.append({'group':str(g.get('group',''))[:120],
            'metric_loss_when_removed':loss,
            'evaluated_folds':int(g.get('evaluated_folds') or 0),
            'positive_loss_folds':int(g.get('positive_loss_folds') or 0)})
    frequencies=[]
    for item in (report.get('selection_frequency') or [])[:10]:
        if not isinstance(item,dict):continue
        total=int(item.get('total_folds') or 0)
        selected=int(item.get('selected_in_folds') or 0)
        if total<=0 or selected<0 or selected>total:continue
        frequencies.append({'feature':str(item.get('feature') or '')[:120],
                            'selected_in_folds':selected,'total_folds':total})
    if frequencies:
        brief['model']['selection_frequency']={'features':frequencies,
            'evidence':[_evidence('model_evaluation','report.selection_frequency',source,rev,
                evaluation_id=ev['evaluation_id'],artifact_sha256=ev['artifact_sha256'])],
            'interpretation':'每个外层训练折独立选择的次数，不等于单项特征的因果贡献。'}
    if clean_groups:
        brief['model']['ablation']={'groups':clean_groups,
            'evidence':[_evidence('model_evaluation','report.feature_group_ablation.groups',
                  source,rev,evaluation_id=ev['evaluation_id'],artifact_sha256=ev['artifact_sha256'])],
            'interpretation':'重新拟合后的特征组删除敏感性；不能将正值解释为因果作用。'}
    return brief
