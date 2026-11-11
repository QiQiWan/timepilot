"""Mac/OneDrive invisible-dotfile copy regression: the source must self-heal."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIVE = ('.dockerignore', '.env.example', '.github/workflows/ci.yml',
        '.gitignore', '.python-version')


@pytest.mark.skipif(shutil.which('bash') is None, reason='POSIX bash only')
def test_glob_copy_drops_hidden_files_but_restore_recovers(tmp_path):
    destination=tmp_path/'timepilot'
    destination.mkdir()
    for source in ROOT.iterdir():
        # Emulates `cp -R source/* destination/`, the scenario reproduced on macOS.
        if source.name.startswith('.'):
            continue
        target=destination/source.name
        if source.is_dir():
            shutil.copytree(source,target,ignore=shutil.ignore_patterns('__pycache__','.pytest_cache','node_modules'))
        elif source.is_file():
            shutil.copy2(source,target)
    for name in FIVE:
        assert not (destination/name).exists()
    for i in range(2):
        done=subprocess.run([sys.executable, str(destination/'scripts/restore_hidden.py')],cwd=destination,
                            capture_output=True,text=True,timeout=25)
        assert done.returncode == 0, done.stdout+'\n'+done.stderr
        manifest=json.loads((destination/'docs/RELEASE_MANIFEST.json').read_text())['files']
        for name in FIVE:
            content=(destination/name).read_bytes()
            assert hashlib.sha256(content).hexdigest()==manifest[name]['sha256']
        assert 'environment_created' in done.stdout
    assert (destination/'scripts/verify_installation.py').exists()


def test_visible_recovery_copies_match_original_dotfiles():
    backups={'.dockerignore':'dockerignore.txt','.env.example':'env_example.txt',
             '.github/workflows/ci.yml':'github_workflows_ci.yml','.gitignore':'gitignore.txt',
             '.python-version':'python_version.txt'}
    for name, backup in backups.items():
        assert (ROOT/name).read_bytes()==(ROOT/'bootstrap/visible_hidden_config_backup'/backup).read_bytes()
