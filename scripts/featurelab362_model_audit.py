#!/usr/bin/env python3
"""Recompute three supervised QA tasks and compare to frozen numerical goldens."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from timepilot.featurelab362_model_matrix import evaluate_synthetic
from timepilot.featurelab362_reference import BASELINE_PATH


def run(data_dir:Path) ->dict:
    baseline=json.loads(BASELINE_PATH.with_name('MODEL_BASELINE.json').read_text(encoding='utf-8'))
    if baseline.get('schema_version')!=1:raise ValueError('Unsupported model baseline')
    cases={};errors=[]
    for name,expected in baseline['cases'].items():
        actual=evaluate_synthetic(data_dir,name)
        cases[name]={'actual':actual,'expected':expected,'differences':{}}
        for key in ('source_sha256','split_manifest_sha256','split_kind','fold_count',
                    'selection_frequencies','train_selection_scopes','all_folds_disjoint',
                    'label_availability_checked'):
            if actual[key]!=expected[key]:
                errors.append(f'{name}.{key}: source/split/selection baseline drift')
        differences={}
        for variant,exp in expected['means'].items():
            got=actual['means'][variant]
            differences[variant]=round(got-exp,6)
            tolerance=0.05 if name=='static_regression' else 0.005
            if abs(got-exp)>tolerance:
                errors.append(f'{name}.{variant}: metric drift {got} vs {exp} (> {tolerance})')
        cases[name]['differences']=differences
    return {'status':'failed' if errors else 'passed','cases':cases,'errors':errors,
            'metric_interpretation':'MAE lower is better; AP higher is better',
            'evidence_scope':'synthetic outer-fold regression, not production accuracy'}


def main(argv=None):
    p=argparse.ArgumentParser()
    p.add_argument('--data-dir',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    a=p.parse_args(argv)
    try:report=run(a.data_dir)
    except Exception as exc:
        report={'status':'failed','errors':[f'{type(exc).__name__}: {exc}']}
    a.out.parent.mkdir(parents=True,exist_ok=True)
    a.out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print('model_baseline:',report['status'])
    for name,item in report.get('cases',{}).items():
        print(name,item['actual']['means'],item['differences'])
    for issue in report['errors']:print('ERROR:',issue)
    return 0 if report['status']=='passed' else 1

if __name__=='__main__':raise SystemExit(main())
