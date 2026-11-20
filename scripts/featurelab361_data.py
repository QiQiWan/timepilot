#!/usr/bin/env python3
"""Generate or independently verify a deterministic TimePilot functional QA corpus.

No data goes into TIMEPILOT_HOME. The suite is always SYNTHETIC.
"""
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from timepilot.featurelab361_fixtures import generate_suite, verify_suite, SEED


def main(argv=None):
    p=argparse.ArgumentParser(description='Generate or verify synthetic TimePilot QA data')
    p.add_argument('--out',type=Path,default=Path('reports/featurelab361_data'))
    p.add_argument('--mode',choices=('generate','verify'),default='generate')
    p.add_argument('--size',choices=('small','standard'),default='standard')
    p.add_argument('--seed',type=int,default=SEED)
    p.add_argument('--force',action='store_true',help='Explicitly regenerate an altered fixture')
    a=p.parse_args(argv)
    try:
        if a.mode=='generate':generate_suite(a.out,seed=a.seed,size=a.size,force=a.force)
        result=verify_suite(a.out)
    except (ValueError,OSError,KeyError,json.JSONDecodeError) as exc:
        print(json.dumps({'status':'failed','error':str(exc)},ensure_ascii=False));return 1
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0 if result['status']=='passed' else 1


if __name__=='__main__':raise SystemExit(main())
