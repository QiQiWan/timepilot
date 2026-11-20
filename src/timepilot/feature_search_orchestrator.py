"""Deterministic, unsupervised evidence ledger for bounded candidate search.

All relevance/target-driven searches happen separately inside training folds.
This ledger only summarizes actually computed data-quality evidence and never
ranks candidates on withheld labels, predicts gain, or publishes output.
"""
from __future__ import annotations
from collections import Counter,defaultdict
from timepilot.feature_contracts import digest

TIERS={'quick':16,'standard':32,'deep':64}


def summarize_search(candidates,ratings,*,budget,source_sha256):
    if not 1<=int(budget)<=64:raise ValueError('invalid search budget')
    lookup={x['candidate_id']:x for x in ratings}
    rounds=defaultdict(list)
    for idx,candidate in enumerate(candidates):
        n=int(candidate.get('search_round') or idx//16+1)
        rounds[n].append((candidate,lookup.get(candidate['candidate_id']) or {}))
    summaries=[]
    for idx,records in sorted(rounds.items()):
        states=Counter(r.get('status') or 'unverified' for _,r in records)
        summaries.append({'round':idx,'candidates':len(records),
                          'quality_passed':states['recommended'],
                          'needs_review':states['review'],'excluded':states['exclude'],
                          'unverified':states['unverified'],
                          'input_fields':len({fid for c,_ in records for fid in c.get('input_field_ids',[])}),
                          'families':dict(sorted(Counter(c.get('family','other') for c,_ in records).items())),
                          'scope':'computed_source_quality_only'})
    all_states=Counter(x.get('status') or 'unverified' for x in ratings)
    has_failed=all_states['unverified']>0 or any(c['candidate_id'] not in lookup for c in candidates)
    return {'schema_version':1,'budget':int(budget),'rounds':summaries,
            'quality_verified':not has_failed and len(candidates)==len(ratings),
            'stopping_reason':'unverified_computations' if has_failed else (
                'budget_consumed' if len(candidates)>=budget else 'eligible_proposals_exhausted'),
            'labels_used':False,'model_gain_claimed':False,
            'candidate_count':len(candidates),'source_sha256':source_sha256,
            'search_evidence_sha256':digest({'source':source_sha256,
                'features':[(c.get('fingerprint'),(lookup.get(c['candidate_id']) or {}).get('status'))
                             for c in candidates]}),
            'note':'当前只完成来源安全性和计算质量筛选；监督收益须在训练折内独立验证。'}
