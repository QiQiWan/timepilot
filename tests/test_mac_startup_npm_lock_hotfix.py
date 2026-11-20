"""v0.37.3 Mac npm EUSAGE regression: real nested optional lock content."""
from __future__ import annotations

import copy
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'scripts'
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from npm_lock_health import lock_issues, repair_lock, LockRepairError


def fixture(tmp_path):
    source = tmp_path / 'example-source'
    frontend = source / 'frontend/native-bi'
    frontend.mkdir(parents=True)
    (source / 'docs').mkdir()
    (source / 'docs/RELEASE_MANIFEST.json').write_text(json.dumps({'format': 2, 'files': {}}))
    pkg = {'name': 'timepilot-perspective-ui', 'version': '0.37.3',
           'dependencies': {}, 'devDependencies': {'vite': '6.3.5'}}
    (frontend / 'package.json').write_text(json.dumps(pkg))
    lock = {'name': pkg['name'], 'version': pkg['version'], 'lockfileVersion': 3,
            'packages': {
                '': {'name': pkg['name'], 'version': pkg['version'],
                     'dependencies': {}, 'devDependencies': pkg['devDependencies']},
                'node_modules/vite': {'version': '6.3.5'},
                'node_modules/rollup': {'version': '4.45.0', 'optionalDependencies': {
                    '@rollup/rollup-darwin-arm64': '4.45.0',
                    '@rollup/rollup-linux-arm64-musl': '4.45.0',
                    '@rollup/rollup-win32-x64-msvc': '4.45.0',
                }},
            }}
    (frontend / 'package-lock.json').write_text(json.dumps(lock))
    return source, frontend, lock


def runner_for_valid_lock(lock, *, fail_on=None, call_log=None):
    def runner(args, **kw):
        cwd = Path(kw['cwd'])
        if call_log is not None:
            call_log.append((args, cwd))
        if args[1] == 'install':
            updated = copy.deepcopy(lock)
            for dep, version in lock['packages']['node_modules/rollup']['optionalDependencies'].items():
                updated['packages']['node_modules/' + dep] = {'version': version, 'optional': True}
            (cwd / 'package-lock.json').write_text(json.dumps(updated))
        if fail_on == args[1]:
            return subprocess.CompletedProcess(args, 1, stdout='', stderr='simulated registry/DNS failure')
        return subprocess.CompletedProcess(args, 0, stdout='ok', stderr='')
    return runner


def test_detects_all_missing_optional_native_rollup_packages(tmp_path):
    _, frontend, lock = fixture(tmp_path)
    problems = lock_issues(frontend)
    assert len([p for p in problems if 'optional platform' in p]) == 3
    assert any('@rollup/rollup-darwin-arm64' in p for p in problems)
    assert any('@rollup/rollup-win32-x64-msvc' in p for p in problems)
    for dep, version in lock['packages']['node_modules/rollup']['optionalDependencies'].items():
        lock['packages']['node_modules/' + dep] = {'version': version}
    (frontend / 'package-lock.json').write_text(json.dumps(lock))
    assert not lock_issues(frontend)


def test_repair_uses_isolated_clean_workspace_and_preserves_old_lock(tmp_path):
    source, frontend, original_lock = fixture(tmp_path)
    before = (frontend / 'package-lock.json').read_bytes()
    calls = []
    result = repair_lock(frontend, root=source,
                         runner=runner_for_valid_lock(original_lock, call_log=calls),
                         backup_base=tmp_path / 'backups')
    assert result['status'] == 'repaired'
    assert not lock_issues(frontend)
    assert len(calls) == 2
    assert calls[0][0][:2] == ['npm', 'install']
    assert calls[1][0][:2] == ['npm', 'ci']
    assert all(cwd != frontend for _, cwd in calls)
    assert calls[0][1] == calls[1][1]
    assert not calls[0][1].exists()  # clean temporary workspace removed
    assert Path(result['backup']).read_bytes() == before
    assert (frontend / 'package-lock.json').read_bytes() != before


@pytest.mark.parametrize('failed_command', ['install', 'ci'])
def test_failed_registry_or_ci_does_not_replace_original_lock(tmp_path, failed_command):
    source, frontend, old_lock = fixture(tmp_path)
    before = (frontend / 'package-lock.json').read_bytes()
    with pytest.raises(LockRepairError, match='simulated registry'):
        repair_lock(frontend, root=source, runner=runner_for_valid_lock(old_lock, fail_on=failed_command),
                    backup_base=tmp_path / 'backups')
    assert (frontend / 'package-lock.json').read_bytes() == before
    assert not (tmp_path / 'backups').exists()


def test_signed_lock_repair_fails_closed(tmp_path):
    source, frontend, lock = fixture(tmp_path)
    (source / 'docs/RELEASE_MANIFEST.json').write_text(json.dumps({
        'format': 2, 'files': {'frontend/native-bi/package-lock.json': {'sha256': 'signed'}}}))
    calls = []
    with pytest.raises(LockRepairError, match='signed'):
        repair_lock(frontend, root=source, runner=runner_for_valid_lock(lock, call_log=calls))
    assert calls == []


def test_offline_lock_repair_does_not_write(tmp_path, monkeypatch):
    source, frontend, lock = fixture(tmp_path)
    old = (frontend / 'package-lock.json').read_bytes()
    monkeypatch.setenv('TIMEPILOT_OFFLINE', '1')
    with pytest.raises(LockRepairError, match='OFFLINE'):
        repair_lock(frontend, root=source, runner=runner_for_valid_lock(lock))
    assert (frontend / 'package-lock.json').read_bytes() == old


def test_official_release_requires_reviewed_lock_and_no_mutable_bootstrap():
    ci = (ROOT / '.github/workflows/ci.yml').read_text()
    build = (ROOT / 'scripts/BUILD_OFFLINE_RELEASE.sh').read_text()
    setup = (ROOT / 'scripts/setup_native_bi.py').read_text()
    assert 'python scripts/npm_lock_health.py' in ci
    assert 'python scripts/npm_lock_health.py' in build
    assert 'scripts/setup_native_bi.py --bootstrap-lock' not in build
    assert 'bash scripts/BUILD_OFFLINE_RELEASE.sh' in (ROOT/'.github/workflows/offline-release.yml').read_text()
    assert '--bootstrap-lock' not in (ROOT/'.github/workflows/offline-release.yml').read_text()
    assert 'repair_lock(FRONTEND, root=ROOT)' in setup
    assert 'npm ci' in ci


def test_signed_hidden_ci_recovery_matches_new_ci_content():
    import importlib.util
    spec = importlib.util.spec_from_file_location('tp_source_restore_hotfix', SCRIPTS / 'restore_hidden.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    state = module.restore(ROOT, dry_run=True)
    assert '.github/workflows/ci.yml' in state['unchanged']


def test_missing_lock_in_source_distribution_is_detected(tmp_path):
    _, frontend, _ = fixture(tmp_path)
    (frontend / 'package-lock.json').unlink()
    assert any('missing' in p for p in lock_issues(frontend))
