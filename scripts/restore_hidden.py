"""Restore the five known developer dotfiles from visible, signed backups.

Invoked exclusively through TIMEPILOT.sh restore-hidden or TIMEPILOT.bat restore-hidden.
No installations, environments or network calls. Never overwrites changed user files.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKUPS = {
    '.dockerignore': 'dockerignore.txt',
    '.env.example': 'env_example.txt',
    '.github/workflows/ci.yml': 'github_workflows_ci.yml',
    '.gitignore': 'gitignore.txt',
    '.python-version': 'python_version.txt',
}


def restore(root: Path = ROOT) -> dict:
    manifest = json.loads((root / 'docs/RELEASE_MANIFEST.json').read_text(encoding='utf-8'))
    done = []
    # Validate *all* copies before changing anything.
    for name, backup in BACKUPS.items():
        entry = manifest['files'].get(name)
        src = root / 'bootstrap/visible_hidden_config_backup' / backup
        dst = root / name
        if entry is None or not src.is_file():
            raise ValueError('Missing signed visible recovery source: ' + name)
        contents = src.read_bytes()
        if hashlib.sha256(contents).hexdigest() != entry['sha256']:
            raise ValueError('Recovery source hash mismatch: ' + name)
        if dst.exists() and dst.read_bytes() != contents:
            raise ValueError('Refusing to overwrite changed dotfile: ' + name)
        done.append((dst, contents))
    restored = []
    for dst, contents in done:
        if dst.exists():
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=dst.parent, prefix='.tp-config-')
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(contents)
            os.replace(temporary, dst)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        restored.append(dst.relative_to(root).as_posix())
    return {'restored': restored, 'verified': list(BACKUPS), 'environment_created': False}


if __name__ == '__main__':
    import json as _json
    print(_json.dumps(restore(), ensure_ascii=False, indent=2))
