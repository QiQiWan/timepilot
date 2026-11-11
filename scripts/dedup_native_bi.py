"""Safe offline content dedup; no disk deletion unless explicit gc --apply."""
import argparse
import json
import os
from pathlib import Path
from timepilot.physical_store import ParquetBlobStore


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('command',choices=['scan','gc'],nargs='?',default='scan')
    parser.add_argument('--apply',action='store_true',help='perform atomic hardlink migration after checksums')
    parser.add_argument('--max-files',type=int,default=None)
    args=parser.parse_args()
    home=Path(os.getenv('TIMEPILOT_HOME',Path.home()/'.timepilot')).resolve()
    from timepilot.sdk import TimePilot
    from timepilot.db import NativeBIAssetRow
    from sqlalchemy import select
    # The SQLAlchemy model lists immutable current asset snapshots.
    store=TimePilot(home=home).store
    with store.sessions() as session:
        expected={r.id:r.sha256 for r in session.scalars(select(NativeBIAssetRow)).all()}
    blobs=ParquetBlobStore(home/'native_bi')
    if args.command=='scan':
        print(json.dumps(blobs.scan(apply=args.apply,expected_by_id=expected,max_files=args.max_files),ensure_ascii=False,indent=2))
    else:
        print(json.dumps(blobs.garbage_collect(apply=args.apply),ensure_ascii=False,indent=2))

if __name__=='__main__':main()
