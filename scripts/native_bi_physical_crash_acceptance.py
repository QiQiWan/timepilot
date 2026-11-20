"""Physical DuckDB/Parquet crash-window acceptance using real subprocess SIGKILL-like exit.

All assets are created in a newly allocated TemporaryDirectory. The two
injected exits occur: (1) after Parquet + recovery sidecar but before metadata
transaction, (2) after metadata commit but before returning to caller. This
never modifies TIMEPILOT_HOME or substitutes fake Parquet files.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))


def _hash(path):
    sha = hashlib.sha256()
    with Path(path).open('rb') as reader:
        for data in iter(lambda: reader.read(1024 * 1024), b''):
            sha.update(data)
    return sha.hexdigest()


def _service(home):
    from timepilot.config import Settings
    from timepilot.db import Store
    from timepilot.native_bi import NativeBIService
    # Never honor a production TIMEPILOT_DB_URL from the caller environment.
    # All crash injection operates on a new disposable SQLite database.
    from dataclasses import replace
    settings = replace(Settings.from_env(home),
                       db_url=f"sqlite:///{(Path(home) / 'metadata.sqlite3').as_posix()}")
    settings.ensure_dirs()
    store = Store(settings.db_url)
    return store, NativeBIService(store, settings)


def _lineage(region):
    return {'type': 'project_generic_table_v016', 'project_id': 'acceptance-project-' + region,
            'import_id': 'acceptance-import-' + region,
            'candidate_id': 'acceptance-candidate-' + region,
            'version_id': 'acceptance-version-' + region,
            'source_sha256': 'b' * 64, 'data_sha256': 'c' * 64}


def _child(args):
    store, native = _service(args.home)
    existing = native._persist_asset_metadata
    if args.child == 'before_metadata':
        def crash(_record):
            os._exit(71)
        native._persist_asset_metadata = crash
    elif args.child == 'after_metadata':
        def crash_after(record):
            existing(record)
            os._exit(72)
        native._persist_asset_metadata = crash_after
    native.import_from_path(args.input, 'real_parquet_crash_' + args.region,
                            _lineage(args.region))
    raise RuntimeError('Expected crash hook was not reached')


def run_acceptance(out: Path, require_target: bool) -> int:
    report = {'version': '0.38.6', 'mode': 'real_duckdb_parquet_crash',
              'utc': datetime.now(timezone.utc).isoformat(),
              'python': sys.version.split()[0], 'steps': []}
    missing = []
    if require_target and sys.version_info[:3] != (3, 12, 12):
        missing.append('exact_CPython_3.12.12')
    for name in ('duckdb', 'pyarrow'):
        if importlib.util.find_spec(name) is None:
            missing.append(name)
    if missing:
        report.update(status='blocked', missing=missing)
        code = 2
    else:
        import duckdb
        import pyarrow
        report['duckdb'] = duckdb.__version__
        report['pyarrow'] = pyarrow.__version__
        try:
            with tempfile.TemporaryDirectory(prefix='tp-v0386-real-crash-') as work:
                home = Path(work) / 'home'
                home.mkdir()
                source = Path(work) / 'source.csv'
                source.write_text('sku,temperature\n00017,24.5\n00018,25.0\n', encoding='utf-8')
                store, native = _service(home)
                # Test the true on-disk Parquet reader + source-preserving import.
                baseline = native.import_from_path(source, 'baseline', _lineage('baseline'))
                assert native.verify(baseline['asset_id'], force=True)['valid']
                with duckdb.connect(':memory:') as db:
                    rows = db.execute('SELECT sku,temperature FROM read_parquet(?) ORDER BY __tp_rowid',
                                      [str(native._path(baseline['asset_id']))]).fetchall()
                assert rows == [('00017', 24.5), ('00018', 25.0)], rows
                report['steps'].append({'stage': 'real_parquet_preserves_leading_zero', 'status': 'passed',
                                        'sha256': baseline['sha256'], 'rows': len(rows)})
                store.engine.dispose()
                for stage, exit_expected, region in (
                        ('before_metadata', 71, 'uncommitted'),
                        ('after_metadata', 72, 'committed')):
                    cmd = [sys.executable, str(Path(__file__).resolve()), '--child', stage,
                           '--home', str(home), '--input', str(source), '--region', region]
                    child = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True,
                                           timeout=65, env={**os.environ, 'PYTHONPATH': str(ROOT/'src')})
                    assert child.returncode == exit_expected, (stage, child.returncode,
                                                               child.stderr[-1500:])
                    store, native = _service(home)
                    key = _lineage(region)
                    found = native.find_published_candidates(
                        key['project_id'], key['import_id'], key['candidate_id'], key['version_id'])
                    if stage == 'before_metadata':
                        assert found['complete'] and found['scoped_unregistered_snapshots'] == 1, found
                        assert len(found['asset_ids']) == 0, found
                        evidence = found['orphan_previews'][0]
                        orphan = native.home / evidence['asset_id'] / 'data.parquet'
                        assert _hash(orphan) == evidence['recorded_sha256']
                        with duckdb.connect(':memory:') as db:
                            assert db.execute('SELECT COUNT(*) FROM read_parquet(?)',
                                              [str(orphan)]).fetchone()[0] == 2
                        other = _lineage('independent')
                        unrelated = native.find_published_candidates(other['project_id'], other['import_id'],
                                   other['candidate_id'], other['version_id'])
                        assert unrelated['complete'] and unrelated['unregistered_snapshots'] == 0, unrelated
                        assert unrelated['unregistered_other_regions'] >= 1, unrelated
                    else:
                        assert found['complete'] and len(found['asset_ids']) == 1, found
                        assert found['scoped_unregistered_snapshots'] == 0, found
                        assert native.verify(found['asset_ids'][0], force=True)['valid']
                    report['steps'].append({'stage': stage, 'status': 'passed',
                                            'child_exit': child.returncode,
                                            'registered_assets': len(found['asset_ids']),
                                            'orphan_snapshots': found['scoped_unregistered_snapshots']})
                    store.engine.dispose()
            report['status'] = 'passed'
            code = 0
        except (Exception, AssertionError) as exc:
            report.update(status='failed', reason=type(exc).__name__, detail=str(exc)[:700])
            code = 1
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'status':report['status'], 'report':str(out)},ensure_ascii=False))
    return code


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--home', type=Path)
    parser.add_argument('--input', type=Path)
    parser.add_argument('--region')
    parser.add_argument('--child', choices=['before_metadata','after_metadata'])
    parser.add_argument('--require-target', action='store_true')
    parser.add_argument('--out', type=Path, default=ROOT/'reports/v0386/native_bi_crash.json')
    args = parser.parse_args(argv)
    if args.child:
        if not args.home or not args.input or not args.region:
            parser.error('Child mode requires --home, --input and --region')
        return _child(args)
    return run_acceptance(args.out, args.require_target)


if __name__ == '__main__':
    raise SystemExit(main())
