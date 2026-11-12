"""Fixed, versioned BI Agent instruction benchmark. Stdlib output, no data upload.

Usage: python scripts/evaluate_bi_nl_v0232.py --mode offline --output reports/bi_nl.json
       python scripts/evaluate_bi_nl_v0232.py --mode configured --runtime /path/to/TIMEPILOT_HOME
Configured mode explicitly uses the configured provider and consumes API quota.
"""
from __future__ import annotations
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from timepilot.bi_nl_evaluation import load_cases,evaluate
from timepilot.bi_agent_service import BIWorkspaceService
from timepilot.bi_tool_registry import TOOLS, BIAction, catalog

class SourceCatalog:
    def native_for(self,pid):return []
    def generic_for(self,pid):return []


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--mode',choices=['offline','configured'],default='offline')
    ap.add_argument('--dataset',type=Path,default=ROOT/'benchmarks/bi_nl/v1_fixed_cases.jsonl')
    ap.add_argument('--runtime',type=Path)
    ap.add_argument('--output',type=Path,default=ROOT/'reports/bi_nl_v0232.json')
    ap.add_argument('--limit',type=int,default=0)
    ap.add_argument('--enforce-thresholds',action='store_true',help='Exit 1 when any published quality gate fails')
    args=ap.parse_args()
    cases,digest=load_cases(args.dataset)
    if args.limit:cases=cases[:min(len(cases),max(1,args.limit))]
    service=BIWorkspaceService.__new__(BIWorkspaceService)
    service.projects=SourceCatalog();service.models=None
    service.workspace=lambda pid,wid: {
        'asset_id':'synthetic','version_id':'base',
        'state':{'asset_id':'synthetic','version_id':'base','chart':{'type':'grid'}}}
    service._columns=lambda pid,state: service.__dict__['evaluation_columns']
    # The production planner now validates source field physical types. This
    # isolated benchmark has only field names: assign synthetic, documented
    # test types. A* and Time are numerics; other fields remain strings.
    def benchmark_asset(pid,aid,vid):
        import re
        return {'asset_id':aid,'name':'benchmark synthetic asset',
            'columns':[{'name':name,'physical_type':
                        'DOUBLE' if re.fullmatch(r'A[0-9]+',name,re.I) else
                        'BIGINT' if name.lower()=='time' else 'VARCHAR'}
                       for name in service.__dict__['evaluation_columns']]}
    service._asset=benchmark_asset
    service._project_join_suggestion=lambda pid,workspace,message: None
    name='rule_based_no_model'
    if args.mode=='configured':
        if args.runtime is None:
            print('BLOCKED: configured evaluation requires --runtime to an existing TimePilot home',file=sys.stderr)
            return 2
        from timepilot.sdk import TimePilot
        from timepilot.llm import ModelGateway
        client=TimePilot(home=args.runtime)
        gateway=ModelGateway(client.store,client.settings)
        status=gateway.config()
        if not status.get('enabled') or not status.get('has_api_key'):
            print('BLOCKED: selected TimePilot model API is not configured/enabled',file=sys.stderr)
            return 2
        service.models=gateway;name=status.get('model') or status.get('provider') or 'configured'
    def planner(message,columns,case):
        service.__dict__['evaluation_columns']=columns
        plan=service.plan_turn('benchmark','isolated',message)
        if plan.get('warning') and args.mode=='configured':
            # Unavailable provider must never silently inflate LLM success metrics.
            raise RuntimeError(plan['warning'])
        return plan
    result=evaluate(cases,digest,planner,mode=args.mode,model=name)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'mode':args.mode,'model':name,'cases':result['cases'],'metrics':result['metrics'],
                      'output':str(args.output)},ensure_ascii=False))
    if args.enforce_thresholds:
        values=result['metrics'];thresholds=result['acceptance_thresholds']
        checks={'exact_tool_sequence_accuracy':values['exact_tool_sequence_accuracy']>=thresholds['tool_exact_min'],
                'ambiguous_or_denied_abstention_rate':values['ambiguous_or_denied_abstention_rate'] is not None and values['ambiguous_or_denied_abstention_rate']>=thresholds['ambiguity_abstain_min'],
                'unsafe_action_rate':values['unsafe_action_rate']==0,
                'hallucinated_field_rate':values['hallucinated_field_rate']==0}
        if not all(checks.values()):
            print('QUALITY_GATE_FAILED: '+json.dumps(checks,ensure_ascii=False),file=sys.stderr)
            return 1
    return 0
if __name__=='__main__':raise SystemExit(main())
