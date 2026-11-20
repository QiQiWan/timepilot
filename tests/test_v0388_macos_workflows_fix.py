"""Regression for Mac Finder/OneDrive overlay copying of hidden CI workflows."""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPAIR = ROOT / 'scripts/repair_macos_workflows_v0388.py'
FILES = ('.github/workflows/ci.yml', '.github/workflows/offline-release.yml')


def _fixture(tmp_path: Path) -> Path:
    target = tmp_path / 'OneDrive-个人' / 'python' / 'research' / 'timepilot'
    target.mkdir(parents=True)
    for path in ('docs/RELEASE_MANIFEST.json', 'pyproject.toml'):
        dest = target / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / path, dest)
    (target / '.github/workflows').mkdir(parents=True)
    (target / FILES[0]).write_text('this is an old CI workflow\n', encoding='utf-8')
    assert not (target / FILES[1]).exists()
    return target


def _run(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(REPAIR), '--root', str(root), *map(str, args)],
        cwd=root, text=True, capture_output=True, timeout=20,
    )


def test_mac_incomplete_overlay_is_safely_repaired(tmp_path):
    target = _fixture(tmp_path)
    runtime = tmp_path / 'runtime data'
    runtime.mkdir()
    database = runtime / 'metadata.sqlite'
    database.write_bytes(b'important old database')
    original_ci = (target / FILES[0]).read_bytes()
    dry = _run(target, '--dry-run')
    assert dry.returncode == 0, dry.stderr
    preview = json.loads(dry.stdout)
    assert preview['would_repair'] == [FILES[0]]
    assert preview['would_restore'] == [FILES[1]]
    assert (target / FILES[0]).read_bytes() == original_ci
    assert not (target / FILES[1]).exists()

    backup_root = tmp_path / 'source backups'
    repaired = _run(target, '--backup-dir', str(backup_root))
    assert repaired.returncode == 0, repaired.stderr
    # CLI appends a human-readable next step after its JSON result.
    outcome = json.loads(repaired.stdout.split('\nNEXT:')[0])
    assert outcome['repaired'] == [FILES[0]]
    assert outcome['restored'] == [FILES[1]]
    assert Path(outcome['backup_dir'], FILES[0]).read_bytes() == original_ci
    manifest = json.loads((target / 'docs/RELEASE_MANIFEST.json').read_text())['files']
    for name in FILES:
        contents = (target / name).read_bytes()
        assert hashlib.sha256(contents).hexdigest() == manifest[name]['sha256']
    assert database.read_bytes() == b'important old database'

    again = _run(target)
    assert again.returncode == 0, again.stderr
    outcome = json.loads(again.stdout.split('\nNEXT:')[0])
    assert not outcome['restored'] and not outcome['repaired']


def test_manifest_mismatch_fails_closed(tmp_path):
    target = _fixture(tmp_path)
    manifest_path = target / 'docs/RELEASE_MANIFEST.json'
    manifest = json.loads(manifest_path.read_text())
    manifest['files'][FILES[1]]['sha256'] = '0' * 64
    manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
    result = _run(target)
    assert result.returncode == 2
    assert 'manifest differs' in result.stderr
    assert (target / FILES[0]).read_text() == 'this is an old CI workflow\n'
    assert not (target / FILES[1]).exists()


def test_symlink_parent_fails_without_writing_outside_tree(tmp_path):
    target = _fixture(tmp_path)
    outside = tmp_path / 'outside'
    outside.mkdir()
    shutil.rmtree(target / '.github')
    (target / '.github').symlink_to(outside, target_is_directory=True)
    result = _run(target)
    assert result.returncode == 2
    assert 'Unsafe parent directory' in result.stderr
    assert not (outside / 'workflows').exists()


def test_source_repair_restores_changed_and_missing_workflows(tmp_path):
    target = tmp_path / 'full copy'
    shutil.copytree(ROOT, target, ignore=shutil.ignore_patterns('__pycache__', '.pytest_cache', 'node_modules'))
    (target / FILES[0]).write_text('old ci.yml from previous checkout\n')
    (target / FILES[1]).unlink()
    subprocess.run([sys.executable, str(target / 'scripts/restore_hidden.py'), '--root', str(target),
                    '--repair-changed', '--backup-dir', str(tmp_path / 'backups')],
                   capture_output=True, text=True, timeout=25, check=True)
    check = subprocess.run([sys.executable, str(target / 'scripts/verify_installation.py'), '--source-only'],
                           capture_output=True, text=True, cwd=target, timeout=25)
    assert check.returncode == 0, check.stdout + check.stderr


def test_launcher_source_repair_on_mac_style_path(tmp_path):
    import os
    target = tmp_path / 'OneDrive-个人' / 'python' / 'research' / 'timepilot'
    shutil.copytree(ROOT, target, ignore=shutil.ignore_patterns('__pycache__', '.pytest_cache', 'node_modules'))
    (target / FILES[0]).write_text('stale CI from the former source\n')
    (target / FILES[1]).unlink()
    runtime = tmp_path / 'TimePilot-runtime-test'
    runtime.mkdir()
    data = runtime / 'metadata.sqlite'
    data.write_bytes(b'KEEP-OLD-DATA')
    env = dict(os.environ, TIMEPILOT_HOME=str(runtime))
    for command in (('source-repair', '--dry-run'), ('source-repair',), ('source-check',)):
        process = subprocess.run(['bash', 'TIMEPILOT.sh', *command], cwd=target, env=env,
                                 capture_output=True, text=True, timeout=30)
        assert process.returncode == 0, process.stdout + process.stderr
    assert data.read_bytes() == b'KEEP-OLD-DATA'
    assert (target / FILES[0]).read_bytes() == (ROOT / FILES[0]).read_bytes()
    assert (target / FILES[1]).read_bytes() == (ROOT / FILES[1]).read_bytes()
