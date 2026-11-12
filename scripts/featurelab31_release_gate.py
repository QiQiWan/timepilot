"""v0.31 strict industrial release gate. No Browser receipt -> no release success.

Provenance must come from the same materialized artifact and project opened
in installed Chrome, with real Perspective JS/WASM and WebSocket exchanges.
This is a validation tool; it never invents or signs missing evidence.
"""
from __future__ import annotations
import argparse,json,sys
from pathlib import Path

def check(backend:dict,browser:dict,*,advanced=None,require_advanced=True,
          business=None,require_business=False,min_completion=.80,min_acceptance=.70)->list[str]:
    issues=[]
    if backend.get('status')!='passed_backend_same_source_browser_pending':
        issues.append('same-source backend PIT/materialization/evaluation report not successful')
    if not isinstance(backend.get('source'),dict) or not backend['source'].get('sha256'):
        issues.append('missing source file SHA256')
    a=backend.get('artifact') or {}
    if not a.get('sha256') or a.get('sha256')!=backend.get('evaluation',{}).get('evaluated_artifact_sha256'):
        issues.append('evaluator and materialization artifact hash disagree')
    if not backend.get('qa_project_id') or not backend.get('source_native_asset_id') or not backend.get('materialized_native_asset_id'):
        issues.append('missing persisted QA project/assets')
    if browser.get('status')!='passed':
        issues.append('real same-project Perspective browser receipt absent or failed')
    for key in ('qa_project_id','source_native_asset_id','materialized_native_asset_id'):
        if backend.get(key)!=browser.get(key):issues.append('browser identity mismatch: '+key)
    for key,value in (('source_sha256',backend.get('source',{}).get('sha256')),
                      ('artifact_sha256',a.get('sha256'))):
        if not value or browser.get(key)!=value:issues.append('browser does not match same artifact: '+key)
    if browser.get('panel_count',0)<2 or not browser.get('websocket_opened') or not browser.get('refresh_restored'):
        issues.append('missing 2 real Perspective panels/WS/reload receipt')
    if not browser.get('native_bundle_verified') or not browser.get('installed_chrome'):
        issues.append('Perspective release bundle or installed Chrome not verified')
    if require_advanced:
        done=advanced if advanced is not None else backend.get('advanced_real_algorithms')
        if not isinstance(done,dict) or done.get('status')!='passed' or done.get('source_sha256')!=backend.get('source',{}).get('sha256'):
            issues.append('real tsfresh/Featuretools result is absent or uses a different source SHA256')
        else:
            for engine in ('tsfresh','featuretools'):
                outcome=done.get(engine)
                if not isinstance(outcome,dict) or outcome.get('status')!='passed' or outcome.get('source_sha256')!=done['source_sha256'] or not outcome.get('artifact_sha256'):
                    issues.append('missing independently materialized real '+engine+' result')
    if require_business:
        business=business if isinstance(business,dict) else {}
        if business.get('status')!='passed' or business.get('scope')!='real_user_reviewed_agent_plans':
            issues.append('real human-reviewed business Agent task record is absent or blocked')
        else:
            if int(business.get('eligible_tasks') or 0)<10:
                issues.append('fewer than 10 independent real business Agent tasks reviewed')
            completion=business.get('agent_completion_rate')
            accepted=business.get('business_acceptance_rate')
            if not isinstance(completion,(float,int)) or completion<min_completion:
                issues.append(f'Agent completion rate below declared threshold {min_completion:.1%}')
            if not isinstance(accepted,(float,int)) or accepted<min_acceptance:
                issues.append(f'business acceptance rate below declared threshold {min_acceptance:.1%}')
    return issues

def main(argv=None):
    p=argparse.ArgumentParser()
    p.add_argument('--backend',type=Path,required=True)
    p.add_argument('--browser',type=Path,required=True)
    p.add_argument('--advanced',type=Path,default=None)
    p.add_argument('--business',type=Path,default=None)
    p.add_argument('--min-completion',type=float,default=.8)
    p.add_argument('--min-acceptance',type=float,default=.7)
    p.add_argument('--allow-business-pending',action='store_true',help='Only integration QA; not the full business acceptance gate')
    p.add_argument('--allow-advanced-pending',action='store_true',help='Integration QA only; never a full algorithm release gate')
    p.add_argument('--out',type=Path,default=Path('reports/v031/release_gate.json'))
    args=p.parse_args(argv)
    def read(path):
        try:return json.loads(path.read_text(encoding='utf-8'))
        except (OSError,ValueError):return {}
    backend=read(args.backend);browser=read(args.browser)
    advanced=read(args.advanced) if args.advanced else {}
    business=read(args.business) if args.business else {}
    if not 0<=args.min_completion<=1 or not 0<=args.min_acceptance<=1:
        p.error('acceptance rate thresholds must be within [0, 1]')
    errors=check(backend,browser,advanced=advanced,require_advanced=not args.allow_advanced_pending,
         business=business,require_business=not args.allow_business_pending,
         min_completion=args.min_completion,min_acceptance=args.min_acceptance)
    result={'status':'passed' if not errors else 'blocked','issues':errors,
            'backend_report':str(args.backend),'browser_report':str(args.browser),
            'scope':'all_release_gates' if not(args.allow_advanced_pending or args.allow_business_pending) else 'partial_integration_only',
            'policy':{'min_completion':args.min_completion,'min_acceptance':args.min_acceptance},
            'business_report':str(args.business) if args.business else None}
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0 if not errors else 2
if __name__=='__main__':raise SystemExit(main())
