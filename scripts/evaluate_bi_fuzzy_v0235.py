"""Synthetic fuzzy BI intent evaluator. Never reports tool/engine/render quality."""
from __future__ import annotations
import argparse
import json
import sys
from collections import Counter
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from timepilot.bi_intent_resolver import BIIntentResolver
from timepilot.bi_feasibility import BIFeasibility


def evaluate(fixtures,*,engine_ready=True):
    resolver=BIIntentResolver();feasibility=BIFeasibility()
    hits=Counter();details=[]
    for case in fixtures:
        intent=resolver.resolve(case['utterance'],case['available_schema'],case['current_workspace'].get('state'))
        schema={'columns':case['available_schema']}
        result=feasibility.check(intent,schema,case['current_workspace'].get('state',{}),duckdb_ready=engine_ready)
        got_intent=intent['intent_candidates'][0];ok_intent=got_intent in case['expected_intents']
        # Unreviewed synthetic labels are not a field gold-standard; report any mismatch without rewriting cases.
        ok_status=(result['status']==case['feasibility']) if engine_ready else None
        details.append({'case_id':case['case_id'],'category':case['category'],
           'expected_intent':case['expected_intents'],'actual_intent':got_intent,
           'expected_feasibility':case['feasibility'],'actual_feasibility':result['status'],
           'intent_match':ok_intent,'feasibility_match':ok_status,
           'field_ids':result.get('defaults',{}).get('field_ids',[])})
        hits['total']+=1;hits['intent_correct']+=int(ok_intent)
        if ok_status is not None:hits['feasibility_correct']+=int(ok_status)
    return {'benchmark':'bi_fuzzy_v2','annotation_status':'developer_synthetic_unreviewed',
            'engine_ready_simulated_for_planning':engine_ready,'real_statistical_executions':0,
            'actual_browser_renderings':0,'llm_calls':0,'model_cost_usd':None,
            'count':hits['total'],'intent_accuracy':round(hits['intent_correct']/max(1,hits['total']),4),
            'feasibility_accuracy':round(hits['feasibility_correct']/max(1,hits['total']),4) if engine_ready else None,
            'cases':details}


def main():
    p=argparse.ArgumentParser();p.add_argument('--input',default='benchmarks/bi_nl/v2_fuzzy_cases.jsonl')
    p.add_argument('--output',default=None);p.add_argument('--actual-engine-ready',action='store_true');args=p.parse_args()
    path=Path(args.input);path=path if path.is_absolute() else ROOT/path
    fixtures=[json.loads(line) for line in path.read_text('utf-8').splitlines() if line.strip()]
    result=evaluate(fixtures,engine_ready=args.actual_engine_ready)
    body=json.dumps(result,ensure_ascii=False,indent=2)
    if args.output:
        output=Path(args.output);output.parent.mkdir(parents=True,exist_ok=True);output.write_text(body+'\n',encoding='utf8')
    print(json.dumps({k:v for k,v in result.items() if k!='cases'},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
