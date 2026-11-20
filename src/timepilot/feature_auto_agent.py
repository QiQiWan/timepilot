"""Bounded model-guided orchestration for automatic FeatureLab sessions.

All tools execute through trusted server services. Models can recommend objectives,
field priorities, candidate order and *evidence IDs* only. No raw rows, Python, SQL,
credentials, feature publishing, or arbitrary tool calls are accepted from a model.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from timepilot.feature_contracts import canonical

# The execution DAG is a server contract. Model responses cannot add, remove or
# re-order steps. Tool receipts are saved in the existing analysis session JSON.
SAFE_TOOLS = (
    'feature.contract.inspect',
    'feature.profile.compute',
    'feature.candidate.propose',
    'feature.candidate.preview',
    'feature.quality.screen',
    'feature.report.explain',
)
HUMAN_TOOLS = ('feature.set.draft_create', 'feature.run.create',
               'feature.publish', 'feature.contract.patch')
MAX_MODEL_REQUESTS = 4
MAX_AGENT_RECEIPTS = 18


class UnsafeAgentAction(ValueError):
    pass


def enforce_autonomous_tool(tool_id: str):
    """Fail closed, including valid existing tools that require human approval."""
    if not isinstance(tool_id, str) or tool_id not in SAFE_TOOLS:
        raise UnsafeAgentAction(f'Autonomous tool blocked: {str(tool_id)[:80]}')
    return tool_id


def reject_unexpected_keys(proposal: Any, allowed: set[str]):
    if not isinstance(proposal, dict) or set(proposal) - allowed:
        raise UnsafeAgentAction('Model response contains unsupported fields or actions')
    return proposal


def bounded_ids(values: Any, allowed: list[str], limit: int, *, nonempty=True):
    if not isinstance(values, list) or (nonempty and not values) or len(values)>limit:
        raise UnsafeAgentAction('Model recommendation exceeds the permitted selection budget')
    if not all(isinstance(x, str) for x in values):
        raise UnsafeAgentAction('Model field and candidate IDs must be strings')
    if len(values)!=len(set(values)) or any(x not in allowed for x in values):
        raise UnsafeAgentAction('Model recommendation refers to an unauthorized field or candidate')
    return list(values)


def new_agent_state(model_configured=False):
    return {'schema_version':1, 'mode':'rules', 'model_configured':bool(model_configured),
            'model_guided_steps':0, 'trace':[], 'rejected_proposals':0,
            'blocked_actions':[], 'last_model_error':None}


def receipt(agent:dict, tool_id:str, status:str, result:dict|None=None, *, origin='server'):
    enforce_autonomous_tool(tool_id)
    if status not in {'queued','succeeded','failed','blocked'}:
        raise ValueError('Unknown tool receipt status')
    updated=deepcopy(agent)
    trace=updated.setdefault('trace',[])
    # Only a compact allowlisted set of evidence references is retained.
    safe={}
    for key, value in (result or {}).items():
        if key in {'job_id','candidate_session_id','profile_id','source_sha256',
                   'contract_revision','template','candidate_count','evaluated_count',
                   'recommended_count','review_count','excluded_count', 'objective',
                   'field_count','preview_jobs','selection_count','stage'}:
            if isinstance(value,(str,int,float,bool)) or value is None:
                safe[key] = value if not isinstance(value,str) else value[:128]
    trace.append({'step':tool_id,'status':status,'origin':origin,'evidence':safe})
    updated['trace']=trace[-MAX_AGENT_RECEIPTS:]
    return updated


def _allow_model(models,contract):
    if models is None:return False,'no_model'
    try:
        cfg=models.config()
        if not cfg.get('enabled') or not cfg.get('has_api_key'):return False,'not_configured'
        if any(f.classification == 'restricted' for f in contract.fields):
            return False,'restricted_field'
        # Never ship source rows. The schema-only safeguard is independent of
        # the global model setting and applies to every request in this module.
        return True,'enabled'
    except Exception:
        return False,'gateway_unavailable'


def _model_json(models, system_message, payload, max_tokens=450):
    answer=models._message([
        {'role':'system','content':system_message},
        {'role':'user','content':canonical(payload)},
    ],max_tokens=max_tokens)
    parsed=models._parse_json(answer.get('content',''))
    if not isinstance(parsed,dict):
        raise UnsafeAgentAction('Model returned a non-object response')
    return parsed


def model_adjust_selection(models, contract, goal, report, defaults, *, limit=8):
    """Return safe auto-selection based solely on full-source quality evidence.

    A model may only *reorder or remove* already recommended IDs. Flagged and
    unverified candidates cannot be promoted. This step never edits a FeatureSet.
    """
    reference={'selection':list(defaults[:limit]),'provider':'rules',
               'reason':'Measured validity and coverage thresholds', 'blocked':False,
               'error_code':None}
    permitted,reason=_allow_model(models,contract)
    if not permitted:
        reference['error_code']=reason
        return reference
    quality=[x for x in report.get('quality_rows',[]) if x.get('status')=='recommended']
    allow=[x['candidate_id'] for x in quality]
    if not allow:
        reference['error_code']='no_eligible_features'
        return reference
    summaries=[{'id':x['candidate_id'],
                'operator':x.get('operator_id'),
                'missing_rate':(x.get('quality') or {}).get('null_rate'),
                'rows_scanned':(x.get('quality') or {}).get('rows_scanned'),
                'constant':(x.get('quality') or {}).get('constant_on_full_data',False)}
               for x in quality[:30]]
    system=('You are a bounded feature quality reviewer. Select up to 8 candidate IDs '
            'from the provided eligible list. You may reorder or omit them, but may not '
            'add others. Return ONLY JSON {"candidate_ids":["..."],"reason":"..."}. '
            'No new metrics, label inference, predictive-performance claims, raw data, '
            'SQL, Python, tool calls, dataset edits or publishing requests.')
    try:
        out=_model_json(models,system,{'goal':goal[:400],
                   'eligible_candidates':summaries,'max_selected':min(limit,len(allow))},400)
        reject_unexpected_keys(out,{'candidate_ids','reason'})
        selected=bounded_ids(out.get('candidate_ids'),allow,min(limit,len(allow)))
        reason=f'在 {len(allow)} 项实测质量合格的候选中建议保留 {len(selected)} 项；尚未验证模型收益'
        return {'selection':selected,'provider':'llm','reason':reason,
                'blocked':False,'error_code':None}
    except Exception as exc:
        reference['blocked']=isinstance(exc,UnsafeAgentAction)
        reference['error_code']=type(exc).__name__
        return reference


def claim_book(report,*,max_claims=12):
    """Grounded, machine-authored claims. No model free-form numbers are shown."""
    claims=[{'id':'overview',
             'text':f"已计算 {int(report.get('evaluated_count') or 0)} 项候选，其中 "
                    f"{int(report.get('recommended_count') or 0)} 项通过有效性与覆盖率检查。",
             'evidence':{'evaluated_count':report.get('evaluated_count'),
                         'recommended_count':report.get('recommended_count')}}]
    if report.get('excluded_count'):
        claims.append({'id':'excluded',
           'text':f"有 {int(report['excluded_count'])} 项候选因常量、有效值或缺失等质量问题被建议排除。",
           'evidence':{'excluded_count':report['excluded_count']}})
    if report.get('review_count'):
        claims.append({'id':'review',
           'text':f"有 {int(report['review_count'])} 项候选仍需人工核实。",
           'evidence':{'review_count':report['review_count']}})
    if report.get('high_missing_fields'):
        names=[str(f.get('name') or '')[:50] for f in report['high_missing_fields'][:3]]
        claims.append({'id':'missing',
                       'text':'存在缺失率较高的字段：'+ '、'.join(names)+'。',
                       'evidence':{'field_names':names}})
    claims.append({'id':'no_predictive_gain',
       'text':'当前特征质量筛选不能证明预测精度改善；有可靠标签后才可进行模型贡献对照。',
       'evidence':{'predictive_value_evaluated':False}})
    if report.get('profile_scope') not in ('full','all_rows'):
        claims.append({'id':'profile_scope',
           'text':'数据画像的统计范围需要核实，不能将抽样结论解释为全量结果。',
           'evidence':{'profile_scope':report.get('profile_scope')}})
    return claims[:max_claims]


def explain_from_claims(models,contract,report):
    """Model can choose claim IDs, never author unsupported scientific claims."""
    claims=claim_book(report)
    selected=[x for x in claims if x['id']=='overview']
    selected += [x for x in claims if x['id'] not in ('overview','no_predictive_gain')][:2]
    caveat=next((x for x in claims if x['id']=='no_predictive_gain'),None)
    if caveat is not None:selected.append(caveat)
    fallback={'provider':'rules','headline':'特征分析结果',
              'summary':' '.join(x['text'] for x in selected),
              'claims':selected,'limitations':list(report.get('limitations',[])),
              'error_code':None, 'blocked':False}
    permitted,reason=_allow_model(models,contract)
    if not permitted:
        fallback['error_code']=reason
        return fallback
    ids=[x['id'] for x in claims]
    try:
        out=_model_json(models,
            'Choose the most relevant measured claims. Respond ONLY with JSON '
            '{"claim_ids":["overview","no_predictive_gain"]}. IDs must come from '
            'the supplied list. You cannot author sentences, invent metrics, ask to '
            'execute tools or approve a feature set. Claims are verified by the server.',
            {'claims':([{'id':x['id'],'category':x['id']} for x in claims]
                        if any(f.classification!='public' for f in contract.fields) else claims),
             'max_claims':min(4,len(claims))},240)
        reject_unexpected_keys(out,{'claim_ids'})
        chosen=bounded_ids(out.get('claim_ids'),ids,min(4,len(ids)))
        # Preserve the prediction caveat even if the model omits it.
        if 'no_predictive_gain' in ids and 'no_predictive_gain' not in chosen:
            if len(chosen)>=4:chosen=chosen[:3]
            chosen.append('no_predictive_gain')
        selected=[next(x for x in claims if x['id']==i) for i in chosen]
        return {'provider':'llm_curated','headline':'特征分析结果',
                'summary':' '.join(x['text'] for x in selected),
                'claims':selected,'limitations':list(report.get('limitations',[])),
                'error_code':None, 'blocked':False}
    except Exception as exc:
        fallback['blocked']=isinstance(exc,UnsafeAgentAction)
        fallback['error_code']=type(exc).__name__
        return fallback
