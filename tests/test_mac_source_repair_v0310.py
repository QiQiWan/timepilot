"""Mac/OneDrive repair regression: preserve local edits and never reset databases."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('timepilot_hidden_repair', ROOT/'scripts/restore_hidden.py')
REPAIR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REPAIR)


def fixture(tmp_path):
    home = tmp_path/'installation'
    home.mkdir()
    assets = {}
    for name, backup in REPAIR.BACKUPS.items():
        src = ROOT/'bootstrap/visible_hidden_config_backup'/backup
        copy = home/'bootstrap/visible_hidden_config_backup'/backup
        copy.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, copy)
        assets[name] = {'sha256': hashlib.sha256(copy.read_bytes()).hexdigest(), 'bytes': copy.stat().st_size}
        dest = home/name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(copy.read_bytes())
    (home/'docs').mkdir()
    (home/'docs/RELEASE_MANIFEST.json').write_text(json.dumps({'format':2,'files': assets}))
    return home


def test_changed_dotfiles_preserved_until_explicit_repair(tmp_path):
    home = fixture(tmp_path)
    (home/'.env.example').write_text('CUSTOM=KEEP\n')
    (home/'.github/workflows/ci.yml').write_text('legacy workflow\n')
    original = (home/'.env.example').read_bytes()
    with pytest.raises(ValueError, match='source-repair'):
        REPAIR.restore(home)
    assert (home/'.env.example').read_bytes() == original
    pending = REPAIR.restore(home, repair_changed=True, dry_run=True)
    assert set(pending['would_repair']) == {'.env.example','.github/workflows/ci.yml'}
    assert pending['backup'] is None
    assert (home/'.env.example').read_bytes() == original

    runtime = tmp_path/'runtime'
    runtime.mkdir()
    (runtime/'metadata.sqlite3').write_bytes(b'keep-the-database')
    fixed = REPAIR.restore(home, repair_changed=True, backup_base=runtime/'repairs')
    assert set(fixed['repaired']) == {'.env.example','.github/workflows/ci.yml'}
    backup = Path(fixed['backup'])
    assert (backup/'.env.example').read_bytes() == original
    assert (backup/'.github/workflows/ci.yml').read_text() == 'legacy workflow\n'
    assert (runtime/'metadata.sqlite3').read_bytes() == b'keep-the-database'
    for name in REPAIR.BACKUPS:
        assert hashlib.sha256((home/name).read_bytes()).hexdigest() == json.loads(
            (home/'docs/RELEASE_MANIFEST.json').read_text())['files'][name]['sha256']
    repeat = REPAIR.restore(home, repair_changed=True)
    assert not repeat['restored'] and not repeat['repaired']


def test_missing_dotfiles_restored_without_creating_environment(tmp_path):
    home = fixture(tmp_path)
    (home/'.gitignore').unlink()
    report = REPAIR.restore(home)
    assert report['restored'] == ['.gitignore']
    assert report['environment_created'] is False


def test_invalid_recovery_source_rejected_before_touching_other_files(tmp_path):
    home = fixture(tmp_path)
    original = (home/'.env.example').read_bytes()
    (home/'bootstrap/visible_hidden_config_backup/github_workflows_ci.yml').write_text('tampered')
    (home/'.env.example').unlink()
    with pytest.raises(ValueError, match='Signed recovery copy'):
        REPAIR.restore(home)
    assert not (home/'.env.example').exists()
    assert original == (ROOT/'.env.example').read_bytes()


def test_symlink_is_never_followed(tmp_path):
    home = fixture(tmp_path)
    outside = tmp_path/'outside'
    outside.write_text('untouched')
    (home/'.env.example').unlink()
    (home/'.env.example').symlink_to(outside)
    with pytest.raises(ValueError, match='symlink'):
        REPAIR.restore(home, repair_changed=True)
    assert outside.read_text() == 'untouched'


def test_launcher_exposes_repair_and_explicit_bi_setup():
    launcher = (ROOT/'scripts/launcher.py').read_text()
    assert "'source-repair'" in launcher
    assert "'bi-setup'" in launcher
    assert "'--repair-changed'" in launcher
    assert 'scripts/setup_native_bi.py' in launcher


def test_build_setup_fails_closed_without_lock_or_node24(tmp_path):
    path = ROOT/'scripts/setup_native_bi.py'
    assert path.is_file()
    source = path.read_text()
    assert 'package-lock.json' in source
    assert 'Node 24' in source
    assert 'scripts/ensure_native_bi.py' in source
    assert '--bootstrap-lock' in source


def test_standalone_cli_can_fix_an_existing_install(tmp_path):
    import subprocess
    import sys
    home = fixture(tmp_path)
    (home/'.env.example').write_text('user edit\n')
    before = subprocess.run([sys.executable, str(ROOT/'scripts/restore_hidden.py'),
                             '--root', str(home), '--repair-changed', '--dry-run'],
                            capture_output=True, text=True, timeout=15)
    assert before.returncode == 0, before.stderr
    assert '.env.example' in json.loads(before.stdout)['would_repair']
    after = subprocess.run([sys.executable, str(ROOT/'scripts/restore_hidden.py'),
                            '--root', str(home), '--repair-changed',
                            '--backup-dir', str(tmp_path/'backups')],
                           capture_output=True, text=True, timeout=15)
    assert after.returncode == 0, after.stderr
    assert '.env.example' in json.loads(after.stdout)['repaired']
