"""Explicit and verified backups, with no silent runtime migration or restore."""
from __future__ import annotations
import argparse
import json
import os
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from timepilot.runtime_backup import create_backup,verify_backup


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--home',default=os.environ.get('TIMEPILOT_HOME',str(ROOT/'runtime')))
    p.add_argument('--output',type=Path)
    p.add_argument('--verify',type=Path)
    a=p.parse_args()
    if a.verify:
        print(json.dumps(verify_backup(a.verify),ensure_ascii=False,indent=2));return 0
    if not a.output:
        p.error('please specify --output outside TIMEPILOT_HOME')
    print('[BACKUP] Make sure the TimePilot service is stopped for a stable file snapshot.',flush=True)
    print(json.dumps(create_backup(Path(a.home),a.output),ensure_ascii=False,indent=2))
    return 0

if __name__=='__main__':raise SystemExit(main())
