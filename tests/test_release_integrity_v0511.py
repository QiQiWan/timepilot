"""Regression suite for the v0.5.10 missing-SDK / mixed-source incident."""
from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import release_integrity as integrity
import repair_installation as repair


@pytest.fixture(scope='module')
def signed_archive(tmp_path_factory):
    folder = tmp_path_factory.mktemp('signed_release')
    archive = folder / 'signed.zip'
    command = [sys.executable, str(ROOT / 'scripts/package_timepilot.py'),
               '--source-only', '--output', str(archive)]
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    return archive


def test_manifest_contains_all_critical_modules_and_consistent_version():
    manifest = integrity.build_manifest(ROOT)
    version = integrity.project_version(ROOT)
    assert manifest['version'] == '0.22.1' == version
    assert manifest['format'] == 2
    assert set(integrity.REQUIRED).issubset(manifest['files'])
    assert 'src/timepilot/sdk.py' in manifest['files']
    assert 'frontend/native-bi/package.json' in manifest['files']
    assert not any(name.startswith('runtime/') or 'node_modules/' in name
                   for name in manifest['files'])


def test_zip_is_complete_and_byte_verified(signed_archive):
    data, manifest = repair.load_verified_archive(signed_archive)
    assert 'src/timepilot/sdk.py' in data
    assert len(data) == len(manifest['files']) + 1
    assert manifest['version'] == '0.22.1'


def test_no_partial_patch_is_accepted(tmp_path):
    path = tmp_path / 'unsafe_delta.zip'
    with ZipFile(path, 'w', compression=ZIP_DEFLATED) as out:
        out.writestr('TimePilot_v0.8.0/src/timepilot/api.py', 'partial patch')
    with pytest.raises(ValueError, match='manifest'):
        repair.load_verified_archive(path)


def test_modified_archive_is_rejected_before_any_install(tmp_path, signed_archive):
    corrupt = tmp_path / 'corrupt.zip'
    with ZipFile(signed_archive) as old, ZipFile(corrupt, 'w', compression=ZIP_DEFLATED) as new:
        for item in old.infolist():
            payload = old.read(item.filename)
            if item.filename.endswith('/src/timepilot/sdk.py'):
                payload = b'print("injected changed SDK")\n'
            new.writestr(item, payload)
    target = tmp_path / 'existing'
    target.mkdir()
    (target / 'pyproject.toml').write_bytes((ROOT / 'pyproject.toml').read_bytes())
    (target / '.env').write_text('TIMEPILOT_TOKEN=keep', encoding='utf-8')
    with pytest.raises(ValueError, match='checksum mismatch'):
        repair.restore(corrupt, target, apply=True)
    assert not (target / 'src/timepilot').exists()
    assert (target / '.env').read_text() == 'TIMEPILOT_TOKEN=keep'


def test_incomplete_mixed_install_repairs_and_preserves_data(tmp_path, signed_archive):
    target = tmp_path / 'damaged_timepilot'
    target.mkdir()
    (target / 'pyproject.toml').write_bytes((ROOT / 'pyproject.toml').read_bytes())
    (target / 'src/timepilot').mkdir(parents=True)
    (target / 'src/timepilot/__init__.py').write_text('from timepilot.sdk import TimePilot\n')
    (target / 'src/timepilot/obsolete_debug_patch.py').write_text('legacy = True\n')
    (target / '.env').write_text('SECRET=keep\n')
    (target / 'runtime').mkdir()
    (target / 'runtime/metadata.sqlite3').write_bytes(b'preserve user database')
    (target / 'frontend/native-bi/node_modules').mkdir(parents=True)
    (target / 'frontend/native-bi/node_modules/local.txt').write_text('preserve installed packages')
    dry = repair.restore(signed_archive, target)
    assert dry['applied'] is False
    assert 'src/timepilot/sdk.py' in dry['changed_files']
    assert 'src/timepilot/obsolete_debug_patch.py' in dry['obsolete_code_quarantine']
    assert not (target / 'src/timepilot/sdk.py').exists()

    done = repair.restore(signed_archive, target, apply=True)
    assert done['applied'] is True
    assert not (target / 'src/timepilot/obsolete_debug_patch.py').exists()
    assert (Path(done['backup']) / 'src/timepilot/obsolete_debug_patch.py').is_file()
    assert (target / 'src/timepilot/sdk.py').is_file()
    assert ((target / 'TIMEPILOT.sh').stat().st_mode & 0o111) != 0
    assert (target / '.env').read_text() == 'SECRET=keep\n'
    assert (target / 'runtime/metadata.sqlite3').read_bytes() == b'preserve user database'
    assert (target / 'frontend/native-bi/node_modules/local.txt').read_text() == 'preserve installed packages'
    issues, _ = integrity.check_tree(target)
    assert issues == []


def test_missing_sdk_is_named_in_single_preflight_error(tmp_path, signed_archive):
    target = tmp_path / 'broken'
    target.mkdir()
    (target / 'pyproject.toml').write_bytes((ROOT / 'pyproject.toml').read_bytes())
    repair.restore(signed_archive, target, apply=True)
    (target / 'src/timepilot/sdk.py').unlink()
    problems, _ = integrity.check_tree(target)
    assert any('MISSING FILE: src/timepilot/sdk.py' in error for error in problems)


def test_paths_reject_zip_slip_and_platform_specific_escapes():
    for name in ('../sdk.py', '/tmp/sdk.py', 'a/../sdk.py', 'a//sdk.py',
                 'C:/sdk.py', 'a\\sdk.py', '', 'a/./sdk.py'):
        assert not integrity.valid_relative_path(name), name
    assert integrity.valid_relative_path('src/timepilot/sdk.py')


def test_acceptance_and_startup_preflight_before_imports():
    launcher=(ROOT/'scripts/launcher.py').read_text()
    start=(ROOT/'TIMEPILOT.sh').read_text()
    windows=(ROOT/'TIMEPILOT.bat').read_text()
    assert 'scripts/verify_installation.py' in launcher
    assert '--require-runtime' in launcher
    assert '--require-bundle' in launcher
    assert 'PYTHONPATH=' in start and 'PYTHONPATH=' in windows
    assert 'scripts/launcher.py' in start and r'scripts\launcher.py' in windows
    assert not list(ROOT.glob('RUN_*.sh'))
    assert not list(ROOT.glob('START_TIMEPILOT.*'))


def test_source_packager_single_version_implementation():
    package_source = (ROOT / 'scripts/package_timepilot.py').read_text()
    assert 'build_manifest(ROOT)' in package_source
    assert 'project_version(ROOT)' in package_source
    assert 'VERSION = ' not in package_source


def test_complete_archive_bootstraps_new_folder_with_hidden_files(tmp_path, signed_archive):
    fresh = tmp_path / 'never_existed_before'
    result = repair.restore(signed_archive, fresh, apply=True)
    assert result['applied'] is True
    for filename in ('.dockerignore', '.env.example', '.gitignore', '.python-version',
                     '.github/workflows/ci.yml', 'src/timepilot/sdk.py',
                     'TIMEPILOT.sh', 'docs/FILE_TREE.md'):
        assert (fresh / filename).is_file(), filename
    issues, _ = integrity.check_tree(fresh)
    assert issues == []
