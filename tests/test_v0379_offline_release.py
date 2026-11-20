"""Offline supply-chain checks: fail closed without publishing fake wheels."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_builder_refuses_wrong_target_python(monkeypatch):
    m = load('offline_wheels')
    monkeypatch.setattr(m, 'target_python_ok', lambda: False)
    with pytest.raises(m.OfflineWheelError, match='3.12.12'):
        m.require_builder()


def test_hashed_wheel_inventory_and_lock_tamper_fail_closed(tmp_path, monkeypatch):
    m = load('offline_wheels')
    monkeypatch.setattr(m, 'target_python_ok', lambda: True)
    path = tmp_path / 'wheels'; path.mkdir()
    wheel = path / 'example_pkg-1.0-py3-none-any.whl'
    # Deliberately fake wheel bytes; read-only inventory can pass, but a
    # production release MUST also pass the real pip offline resolver.
    wheel.write_bytes(b'not-an-installable-wheel')
    lines = m._lock_lines([wheel]);(path / m.LOCK).write_text('\n'.join(lines)+'\n')
    meta = {'format': 1, 'environment': m._environment(),
            'source_requirements_sha256': m._sha(ROOT / 'requirements.txt'),
            'source_project_sha256': m._sha(ROOT / 'pyproject.toml'),
            'lock_sha256': m._sha(path / m.LOCK),
            'files': {wheel.name: {'bytes':wheel.stat().st_size,'sha256':m._sha(wheel)}}}
    (path / m.MANIFEST).write_text(json.dumps(meta))
    assert m.verify_wheelhouse(path, resolve=False)['wheel_count'] == 1
    with pytest.raises(m.OfflineWheelError):
        m.verify_wheelhouse(path, resolve=True)
    wheel.write_bytes(b'altered-file')
    with pytest.raises(m.OfflineWheelError, match='checksum'):
        m.verify_wheelhouse(path, resolve=False)


def test_platform_mismatch_is_blocker_even_if_files_hash_match(tmp_path, monkeypatch):
    m = load('offline_wheels')
    monkeypatch.setattr(m, 'target_python_ok', lambda: True)
    path = tmp_path / 'wheels';path.mkdir()
    (path / m.MANIFEST).write_text(json.dumps({'format':1,'files':{},'environment':{'python':'0.0.0'}}))
    with pytest.raises(m.OfflineWheelError, match='platform'):
        m.verify_wheelhouse(path, resolve=False)


def test_source_distribution_manifest_never_claims_native_or_python_runtime(tmp_path):
    output=tmp_path/'source.zip'
    result=subprocess.run([sys.executable,'scripts/package_timepilot.py','--source-only','--output',str(output)],
                          cwd=ROOT,capture_output=True,text=True,timeout=90)
    assert result.returncode==0, result.stdout+'\n'+result.stderr
    with zipfile.ZipFile(output) as archive:
        names=archive.namelist()
        assert len(names)==len(set(names))
        manifest=json.loads(archive.read(next(name for name in names if name.endswith('/docs/RELEASE_MANIFEST.json'))))
    assert manifest['distribution_kind']=='source-only'
    assert manifest['python_runtime_wheels_included'] is False
    assert not any('/offline-wheelhouse/' in name for name in names)
    assert not any(name.endswith('.wasm') for name in names)


def test_runtime_mode_requires_both_offline_inputs(tmp_path):
    output=tmp_path/'false_offline.zip'
    result=subprocess.run([sys.executable,'scripts/package_timepilot.py','--complete-offline',
                           '--output',str(output)],cwd=ROOT,capture_output=True,text=True)
    assert result.returncode != 0
    assert not output.exists()
