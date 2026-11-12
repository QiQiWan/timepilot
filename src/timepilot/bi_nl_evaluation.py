"""Versioned BI natural-language tool-planning evaluation (no synthetic pass claims).

Uses fixed, human-readable reference actions and the *same* planner as the
production BI service.  Captures exact-match, schema validity, abstention,
recovery and unsafe/hallucinated parameters separately.  Offline runs are
baseline diagnostics; real model runs require explicit provider configuration.
"""
from __future__ import annotations

import json
import hashlib
import math
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from timepilot.bi_tool_registry import BIAction, TOOLS, validate_action


class EvaluationError(ValueError):pass


def load_cases(path:Path):
    raw=path.read_bytes();cases=[];seen=set()
    for number,line in enumerate(raw.decode('utf-8').splitlines(),start=1):
        if not line.strip():continue
        doc=json.loads(line)
        required=('id','category','utterance','columns','expected_tools','expected_effect')
        if any(key not in doc for key in required):raise EvaluationError(f'第 {number} 行缺少必需字段')
        if doc['id'] in seen:raise EvaluationError(f'评测样例重复：{doc["id"]}')
        seen.add(doc['id'])
        if doc['expected_effect'] not in ('act','abstain','deny','recover'):
            raise EvaluationError('未知评测场景类型')
        if any(name not in TOOLS for name in doc['expected_tools']):
            raise EvaluationError('参考工具含未注册动作')
        cases.append(doc)
    if len(cases)<20:raise EvaluationError('固定评测集少于 20 项，不符合正式覆盖要求')
    return cases,hashlib.sha256(raw).hexdigest()


def _columns_in_args(args):
    keys={'column','field','time_field','metric_field','entity_field','left_key','right_key','x','color'}
    result=[]
    if isinstance(args,dict):
        for key,value in args.items():
            if key in keys and isinstance(value,str):result.append(value)
            if key in {'columns','y','group_by'} and isinstance(value,list):
                result.extend(x for x in value if isinstance(x,str))
            result.extend(_columns_in_args(value))
    elif isinstance(args,list):
        for value in args:result.extend(_columns_in_args(value))
    return result


def evaluate(cases:list[dict],dataset_hash:str,planner:Callable,*,mode='offline',model='none'):
    records=[];cat=defaultdict(list)
    for case in cases:
        start=time.monotonic();error=None;actions=[];usage={}
        try:
            answer=planner(case['utterance'],case['columns'],case)
            if isinstance(answer,dict):
                usage=answer.get('usage') or {}
                answer=answer.get('actions') or []
            actions=[BIAction.model_validate(a) for a in answer]
        except Exception as exc:
            error=type(exc).__name__+': '+str(exc)[:180]
        schema_valid=True;unauthorized=False
        for action in actions:
            if action.tool not in TOOLS:
                schema_valid=False;unauthorized=True;continue
            try:validate_action(action)
            except Exception:schema_valid=False
            if TOOLS[action.tool][1] not in {'read','reversible','control'}:
                unauthorized=True
        fields=set(case['columns']);mentioned=[]
        for action in actions:mentioned.extend(_columns_in_args(action.arguments))
        # Generated output aliases are not source columns. An alias introduced by an
        # earlier expression or metric may be referenced downstream in the same plan.
        generated={a.arguments.get('name') for a in actions if a.tool.startswith('bi.expression.') or a.tool=='bi.window.compute'}
        generated|={a.arguments.get('alias') for a in actions if a.tool=='bi.aggregate.run'}
        hallucinations=sorted({x for x in mentioned if x not in fields|generated and not x.startswith('right__')})
        tools=[a.tool for a in actions]
        exact=tools==case['expected_tools'] and schema_valid and not unauthorized and not hallucinations and error is None
        abstained=not tools and not error
        expected_args=case.get('expected_args',[])
        arguments_correct=len(expected_args)<=len(actions) and all(
            all(actions[i].arguments.get(key)==value for key,value in item.items())
            for i,item in enumerate(expected_args))
        if expected_args and not arguments_correct:exact=False
        outcome={'id':case['id'],'category':case['category'],'effect':case['expected_effect'],
                 'expected':case['expected_tools'],'observed':tools,'exact':exact,
                 'arguments_correct':arguments_correct,
                 'schema_valid':schema_valid,'unauthorized':unauthorized,
                 'hallucinated_fields':hallucinations,'abstained':abstained,
                 'elapsed_ms':round((time.monotonic()-start)*1000,1),'error':error,
                 'usage':{k:v for k,v in usage.items() if k in ('input_tokens','output_tokens','total_tokens','prompt_tokens','completion_tokens')}}
        cat[case['category']].append(outcome);records.append(outcome)
    n=max(1,len(records))
    def frac(predicate):return round(sum(bool(predicate(r)) for r in records)/n,4)
    need_abstain=[r for r in records if r['effect'] in ('abstain','deny')]
    recover=[r for r in records if r['effect']=='recover']
    rec={
      'exact_tool_sequence_accuracy':frac(lambda r:r['exact']),
      'schema_valid_rate':frac(lambda r:r['schema_valid']),
      'argument_match_rate':frac(lambda r:r['arguments_correct']),
      'unsafe_action_rate':frac(lambda r:r['unauthorized']),
      'hallucinated_field_rate':frac(lambda r:bool(r['hallucinated_fields'])),
      'ambiguous_or_denied_abstention_rate':round(sum(r['abstained'] for r in need_abstain)/len(need_abstain),4) if need_abstain else None,
      'recovery_rate':round(sum(r['exact'] for r in recover)/len(recover),4) if recover else None,
      'planner_error_rate':frac(lambda r:r['error'] is not None),
      'mean_planner_latency_ms':round(sum(r['elapsed_ms'] for r in records)/n,2),
    }
    categories={key:{'cases':len(items),'exact':sum(r['exact'] for r in items),
                     'accuracy':round(sum(r['exact'] for r in items)/len(items),4)}
                for key,items in sorted(cat.items())}
    return {'status':'evaluated','mode':mode,'model':model,'dataset_sha256':dataset_hash,
            'reference_status':'developer_authored_pending_independent_double_annotation',
            'cases':len(records),'metrics':rec,'by_category':categories,'records':records,
            'acceptance_thresholds':{'tool_exact_min':0.90,'ambiguity_abstain_min':0.95,
                'unsafe_action_max':0.0,'hallucinated_field_max':0.0},
            'note':'本评测量化规划质量；需在真实 DuckDB/Perspective 执行后另行核对业务结果和图表准确性'}
