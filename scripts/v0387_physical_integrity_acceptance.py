"""Actual DuckDB+Parquet+SQLite integrity acceptance (separate from source tests).

Always creates an isolated temporary TIMEPILOT_HOME; never touches a user's
runtime database. Requires CPython 3.12.12 with installed physical engines
when --require-target is set. Returns 2 and a receipt if unavailable.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
import tempfile
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'src'))


def accept(out: Path, require_target: bool) -> int:
    report = {'version': '0.38.7', 'mode': 'real_registered_parquet_integrity',
              'python': sys.version.split()[0],
              'timestamp_utc': datetime.now(timezone.utc).isoformat(), 'steps': []}
    missing = []
    if require_target and sys.version_info[:3] != (3, 12, 12):
        missing.append('CPython_3.12.12')
    for module in ('duckdb', 'pyarrow'):
        if importlib.util.find_spec(module) is None:
            missing.append(module)
    if missing:
        report.update(status='blocked', missing=missing)
        code=2
    else:
        try:
            import duckdb
            import pyarrow
            from timepilot.config import Settings
            from timepilot.db import Store
            from timepilot.native_bi import NativeBIService
            from scripts.native_snapshot_audit import inventory
            report.update(duckdb=duckdb.__version__, pyarrow=pyarrow.__version__)
            with tempfile.TemporaryDirectory(prefix='tp-v0387-physical-') as work:
                home = Path(work) / 'home'
                home.mkdir()
                source = Path(work) / 'source.csv'
                source.write_text('sku,temperature,device\n00017,24.5,LineA\n00018,25.0,LineB\n',encoding='utf-8')
                settings=replace(Settings.from_env(home),
                                 db_url=f"sqlite:///{(home / 'metadata.sqlite3').as_posix()}")
                settings.ensure_dirs()
                store = Store(settings.db_url)
                try:
                    native = NativeBIService(store, settings)
                    lineage={'type':'project_generic_table_v016', 'project_id':'physical-project',
                             'import_id':'physical-import','candidate_id':'physical-candidate',
                             'version_id':'physical-version','source_sha256':'a'*64,'data_sha256':'b'*64}
                    asset=native.import_from_path(source, 'physical_integrity', lineage)
                    path=native._path(asset['asset_id'])
                    with duckdb.connect(':memory:') as db:
                        rows=db.execute('SELECT sku, temperature,device FROM read_parquet(?) ORDER BY __tp_rowid',
                                        [str(path)]).fetchall()
                    assert rows == [('00017',24.5,'LineA'),('00018',25.0,'LineB')], rows
                    baseline=inventory(home, verify_registered=True)
                    assert baseline['registered_integrity']['safe'], baseline
                    assert baseline['registered_integrity']['verified']==1
                    report['steps'].append({'stage':'real_parquet_and_sqlite_validated',
                                            'status':'passed','rows':len(rows),
                                            'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
                    # Modify exactly one byte of the immutable *temporary* Parquet
                    # file. The audit must catch a same-length modification.
                    with path.open('r+b') as writer:
                        writer.seek(-1, 2)
                        old=writer.read(1)
                        writer.seek(-1, 2)
                        writer.write(b'X' if old != b'X' else b'Y')
                        writer.flush()
                    tampered=inventory(home, verify_registered=True)
                    assert tampered['registered_integrity']['invalid']==1, tampered
                    assert tampered['registered_integrity']['issues'][0]['reason']=='file_sha256_mismatch', tampered
                    report['steps'].append({'stage':'registered_same_size_tamper_detected', 'status':'passed'})
                finally:
                    store.engine.dispose()
            report['status']='passed'
            code=0
        except (Exception, AssertionError) as exc:
            report.update(status='failed',error_type=type(exc).__name__,detail=str(exc)[:1000])
            code=1
    out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'status':report['status'],'out':str(out)},ensure_ascii=False))
    return code


def main(argv=None):
    parser=argparse.ArgumentParser()
    parser.add_argument('--require-target',action='store_true')
    parser.add_argument('--out',type=Path,default=ROOT/'reports/v0387/physical_integrity.json')
    args=parser.parse_args(argv)
    path=args.out if args.out.is_absolute() else ROOT/args.out
    return accept(path,args.require_target)


if __name__=='__main__':
    raise SystemExit(main())
