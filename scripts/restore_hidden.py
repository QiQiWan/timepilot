"""Safely restore missing/modified release dotfiles from hashed visible copies.

Default mode remains conservative: changed files cause a failure.  Explicit
--repair-changed keeps an independent byte-for-byte backup of every modified
file before replacing it.  No database, runtime state or private .env is read.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKUPS = {
    '.dockerignore': 'dockerignore.txt',
    '.env.example': 'env_example.txt',
    '.github/workflows/ci.yml': 'github_workflows_ci.yml',
    '.gitignore': 'gitignore.txt',
    '.python-version': 'python_version.txt',
}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _backup_base() -> Path:
    home = Path(os.environ.get('TIMEPILOT_HOME') or (Path.home() / '.timepilot'))
    return home.expanduser().resolve() / 'source_repair_backups'


def restore(root: Path = ROOT, *, repair_changed: bool = False,
            dry_run: bool = False, backup_base: Path | None = None) -> dict:
    root = Path(root).resolve()
    manifest = json.loads((root / 'docs/RELEASE_MANIFEST.json').read_text(encoding='utf-8'))
    if not isinstance(manifest, dict) or not isinstance(manifest.get('files'), dict):
        raise ValueError('Release manifest missing or malformed; use the complete verified archive')
    planned = []
    for name, backup in BACKUPS.items():
        entry = manifest['files'].get(name)
        source = root / 'bootstrap/visible_hidden_config_backup' / backup
        dest = root / name
        if not isinstance(entry, dict) or not source.is_file() or source.is_symlink():
            raise ValueError('Missing recovery entry/source: ' + name)
        contents = source.read_bytes()
        if _sha(contents) != entry.get('sha256') or len(contents) != entry.get('bytes'):
            raise ValueError('Signed recovery copy differs from release manifest: ' + name)
        if dest.is_symlink() or (dest.exists() and not dest.is_file()):
            raise ValueError('Refusing to replace a symlink or non-file: ' + name)
        previous = dest.read_bytes() if dest.exists() else None
        state = 'missing' if previous is None else ('unchanged' if previous == contents else 'changed')
        if state == 'changed' and not repair_changed:
            raise ValueError('Changed developer file: ' + name +
                             '; use `bash TIMEPILOT.sh source-repair` for a backed-up repair')
        planned.append((name, dest, contents, previous, state))

    changed = [p for p in planned if p[-1] == 'changed']
    missing = [p for p in planned if p[-1] == 'missing']
    result = {
        'dry_run': dry_run,
        'restored': [],
        'repaired': [],
        'unchanged': [p[0] for p in planned if p[-1] == 'unchanged'],
        'would_restore': [p[0] for p in missing],
        'would_repair': [p[0] for p in changed],
        'backup': None,
        'verified': list(BACKUPS),
        'environment_created': False,
    }
    if dry_run:
        return result

    backup_dir = None
    if changed:
        base = Path(backup_base).expanduser().resolve() if backup_base else _backup_base()
        # Backups are never put directly into the tracked source tree.
        if base == root or root in base.parents and not (root / 'runtime') in base.parents:
            raise ValueError('Specify a backup directory outside source (or within its runtime/)')
        base.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        backup_dir = base / ('repair_' + stamp + '_' + uuid.uuid4().hex[:8])
        backup_dir.mkdir(mode=0o700)
        saved = {}
        for name, _, _, old, _ in changed:
            dest = backup_dir / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(old)
            if _sha(dest.read_bytes()) != _sha(old):
                raise OSError('Backup integrity failure for ' + name)
            saved[name] = _sha(old)
        (backup_dir / 'original_sha256.json').write_text(
            json.dumps(saved, indent=2) + '\n', encoding='utf-8')
        result['backup'] = str(backup_dir)

    for name, dest, contents, _, state in planned:
        if state == 'unchanged':
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=dest.parent, prefix='.tp-config-')
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(contents)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, 0o644)
            os.replace(temporary, dest)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        if _sha(dest.read_bytes()) != _sha(contents):
            raise OSError('Restored file failed checksum verification: ' + name)
        result['repaired' if state == 'changed' else 'restored'].append(name)
    return result


def main() -> int:
    p = argparse.ArgumentParser(description='Recover release dotfiles from SHA256-verified visible copies')
    p.add_argument('--root', type=Path, default=ROOT, help='TimePilot source directory to repair (default: own release root)')
    p.add_argument('--repair-changed', action='store_true', help='Back up and repair modified template/CI files')
    p.add_argument('--dry-run', action='store_true', help='Show planned actions without changing files')
    p.add_argument('--backup-dir', type=Path, default=None)
    a = p.parse_args()
    try:
        result = restore(root=a.root, repair_changed=a.repair_changed, dry_run=a.dry_run,
                         backup_base=a.backup_dir)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print('[SOURCE REPAIR BLOCKED] ' + str(exc), file=__import__('sys').stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
