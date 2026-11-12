"""v0.26.1 catalog/bootstrap acceptance with isolated real SQLite databases."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from timepilot.project_migrations import TARGET_SCHEMA, inspect_catalog
from timepilot.projects import ProjectCatalog

TESTS = [
    'tests/test_catalog_bootstrap_v0261.py',
    'tests/test_database_schema_guard_v0511.py',
    'tests/test_projects_v013.py',
    'tests/test_import_summary_v0234.py',
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=('offline', 'configured'), default='offline')
    parser.add_argument('--output')
    args = parser.parse_args()
    output = Path(args.output or ROOT / 'reports' / 'v0261' / f'catalog-{args.mode}.json')
    output.parent.mkdir(parents=True, exist_ok=True)
    home = Path(os.getenv('TIMEPILOT_HOME') or Path.home() / '.timepilot').expanduser().resolve()
    report = {
        'version': '0.26.1', 'target_schema': TARGET_SCHEMA,
        'mode': args.mode, 'time': datetime.now(timezone.utc).isoformat(),
        'runtime_home': str(home), 'source_catalog': inspect_catalog(home / 'projects' / 'catalog.sqlite3'),
        'python': sys.version.split()[0], 'checks': [], 'status': 'running',
    }
    try:
        completed = subprocess.run([sys.executable, '-m', 'pytest', '-q', '-rs', *TESTS],
                                   cwd=ROOT, capture_output=True, text=True, timeout=120)
        report['checks'].append({'name': 'source_and_sqlite_contract',
                                 'status': 'passed' if completed.returncode == 0 else 'failed',
                                 'returncode': completed.returncode,
                                 'output_tail': (completed.stdout + completed.stderr)[-12000:]})
        if completed.returncode:
            report['status'] = 'failed'
        elif args.mode == 'configured' and sys.version_info[:2] != (3, 12):
            report['status'] = 'blocked_environment'
            report['blocker'] = 'The existing Python 3.12 environment is required for configured acceptance.'
        elif args.mode == 'configured' and report['source_catalog']['status'] not in {
            'new', 'empty', 'current', 'upgrade'}:
            report['status'] = 'blocked_catalog'
            report['blocker'] = report['source_catalog']['reason']
        elif args.mode == 'configured':
            with tempfile.TemporaryDirectory(prefix='timepilot-db-accept-') as temp:
                temp_home = Path(temp)
                # An existing production catalog is copied using the SQLite
                # backup API (including WAL). The live home is never upgraded.
                source_path = home / 'projects' / 'catalog.sqlite3'
                if report['source_catalog']['status'] in {'current', 'upgrade'}:
                    target_path = temp_home / 'projects' / 'catalog.sqlite3'
                    target_path.parent.mkdir(parents=True)
                    with sqlite3.connect(source_path) as original, sqlite3.connect(target_path) as replica:
                        original.backup(replica)
                    ProjectCatalog(temp_home, None, None)
                    ProjectCatalog(temp_home, None, None)
                    report['checks'].append({'name': 'copy_of_existing_catalog_reopen',
                                             'status': 'passed',
                                             'version': inspect_catalog(target_path)['current_version']})
                from timepilot.api import create_app
                from fastapi.testclient import TestClient
                isolated = temp_home / 'api-smoke'
                app1 = create_app(home=isolated, embedded_worker=False)
                with TestClient(app1) as client:
                    health = client.get('/api/v1/health')
                    assert health.status_code == 200, health.text
                    assert health.json()['version'] == '0.26.1'
                app2 = create_app(home=isolated, embedded_worker=False)
                assert app2.state.projects.startup_catalog_after['current_version'] == TARGET_SCHEMA
                report['checks'].append({'name': 'real_ASGI_fresh_and_reopen',
                                         'status': 'passed', 'schema': TARGET_SCHEMA})
            report['status'] = 'passed_catalog_runtime_only'
        else:
            report['status'] = 'passed_source_and_sqlite_only'
    except (Exception, OSError) as exc:
        report['status'] = 'failed'
        report['error'] = f'{type(exc).__name__}: {exc}'
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'status': report['status'], 'report': str(output),
                      'checks': [(item['name'], item['status']) for item in report['checks']],
                      'blocker': report.get('blocker')}, ensure_ascii=False, indent=2))
    return 0 if report['status'].startswith('passed') else 2


if __name__ == '__main__':
    raise SystemExit(main())
