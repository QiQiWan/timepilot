"""Avoid blocking a working app on developer dotfiles or stale import paths."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from release_integrity import check_runtime_tree, check_tree
import repair_installation as repair


def test_missing_development_dotfiles_are_nonfatal_in_runtime(tmp_path):
    target = tmp_path / 'partial'
    data, manifest = repair.load_verified_archive(_release_archive(tmp_path))
    repair.restore(_release_archive(tmp_path), target, apply=True)
    for rel in ('.dockerignore', '.env.example', '.gitignore', '.python-version',
                '.github/workflows/ci.yml'):
        (target / rel).unlink()
    errors, warnings, _ = check_runtime_tree(target)
    strict, _ = check_tree(target)
    assert not errors
    assert len([x for x in warnings if 'MISSING FILE:' in x]) == 5
    assert len([x for x in strict if 'MISSING FILE:' in x]) == 5


def test_missing_python_module_remains_fatal(tmp_path):
    target = tmp_path / 'partial'
    repair.restore(_release_archive(tmp_path), target, apply=True)
    (target / 'src/timepilot/sdk.py').unlink()
    errors, _, _ = check_runtime_tree(target)
    assert any('src/timepilot/sdk.py' in item for item in errors)


def test_not_our_project_is_refused_without_writes(tmp_path):
    target = tmp_path / 'other'
    target.mkdir()
    (target / 'pyproject.toml').write_text('[project]\nname="other-solver"\nversion="1.0"\n')
    with pytest.raises(ValueError, match='another project'):
        repair.restore(_release_archive(tmp_path), target, apply=True)
    assert not (target / 'src').exists()


def test_untracked_custom_code_preserved(tmp_path):
    target = tmp_path / 'custom'
    archive = _release_archive(tmp_path)
    repair.restore(archive, target, apply=True)
    custom = target / 'src/timepilot/models/my_model.py'
    custom.write_text('MODEL = "keep"\n')
    repair.restore(archive, target, apply=True)
    assert custom.read_text() == 'MODEL = "keep"\n'


def test_launcher_forces_local_source_and_never_creates_new_env():
    start = (ROOT / 'TIMEPILOT.sh').read_text()
    accept = (ROOT / 'scripts/launcher.py').read_text()
    assert 'PYTHONPATH=' in start
    assert "env['PYTHONPATH']" in accept
    assert 'exec python' in start and 'scripts/launcher.py' in start
    for content in (start, accept):
        assert 'conda create' not in content
        assert 'python -m venv' not in content
        assert 'virtualenv' not in content
        assert 'python -m pip install' not in content


def _release_archive(tmp_path):
    archive = tmp_path / 'fixture_complete.zip'
    if not archive.exists():
        result = subprocess.run(
            [sys.executable, str(ROOT / 'scripts/package_timepilot.py'), '--output', str(archive)],
            cwd=ROOT, capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, result.stderr
    return archive
