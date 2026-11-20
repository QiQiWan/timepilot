"""P0: refuse to confuse source tests and physical/browser release."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_inventory_does_not_install_or_modify_runtime(tmp_path):
    target = tmp_path / 'inventory.json'
    proc = subprocess.run([sys.executable, str(ROOT/'scripts/target_mac_acceptance_v03810.py'),
                           '--out', str(target)], cwd=ROOT, capture_output=True, text=True, timeout=15)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    report = json.loads(target.read_text())
    assert report['status'] == 'inventory_only'
    assert report['version'] == __import__('tomllib').loads((ROOT/'pyproject.toml').read_text(encoding='utf-8'))['project']['version']
    assert report['gates'] == {}
    assert 'target_python' in report['environment']
    assert 'duckdb' in report['environment']['modules']


def test_physical_requires_real_interpreter_and_database(tmp_path):
    target = tmp_path / 'acceptance.json'
    proc = subprocess.run([sys.executable, str(ROOT/'scripts/target_mac_acceptance_v03810.py'),
                           '--run', '--mode', 'physical', '--out', str(target)],
                          cwd=ROOT, capture_output=True, text=True, timeout=35)
    data = json.loads(target.read_text())
    assert 'physical' in data['gates']
    assert proc.returncode == {'passed':0, 'blocked':2, 'failed':1}[data['status']]
    if sys.platform == 'darwin':
        assert Path(data['gates']['physical']['receipt']).is_file()
    else:
        assert data['gates']['physical']['reason'] == 'target_macOS_required'
    if sys.version_info[:3] != (3,12,12):
        assert data['gates']['physical']['status'] == 'blocked'
