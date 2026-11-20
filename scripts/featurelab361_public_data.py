#!/usr/bin/env python3
"""Explicit optional official UCI dataset download. No installation."""
from pathlib import Path
import sys
import json
import argparse
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from timepilot.featurelab361_public import download_public, PUBLIC

def main(argv=None):
    p=argparse.ArgumentParser(description='Download a public UCI QA dataset on explicit request')
    p.add_argument('--dataset',required=True,choices=sorted(PUBLIC))
    p.add_argument('--out',type=Path,default=Path('reports/featurelab361_public'))
    p.add_argument('--force',action='store_true')
    p.add_argument('--timeout',type=int,default=30)
    a=p.parse_args(argv)
    try:result=download_public(a.dataset,a.out,force=a.force,timeout=a.timeout)
    except Exception as exc:
        print(json.dumps({'status':'blocked','reason':str(exc)},ensure_ascii=False))
        return 2
    print(json.dumps({'status':'passed','metadata':result},ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
