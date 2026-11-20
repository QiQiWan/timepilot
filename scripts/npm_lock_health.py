"""Audit and repair the native BI npm lock without modifying Python or runtime data.

A lockfile generated from an existing node_modules tree can omit Rollup/Esbuild
platform-specific optional packages. npm ci then rejects the lock (EUSAGE).
The only permitted remedy is to regenerate in a *clean isolated directory*,
verify the resulting lock with an actual npm ci, and publish it atomically.
If the lock is signed as part of a source release, refuse to overwrite it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

# Direct importlib loading of this script (build QA) does not add scripts/ to sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from npm_runtime import npm_argv

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / 'frontend/native-bi'


class LockRepairError(RuntimeError):
    pass


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def lock_issues(frontend: Path) -> list[str]:
    frontend = Path(frontend)
    package_path, lock_path = frontend / 'package.json', frontend / 'package-lock.json'
    if not package_path.is_file():
        return ['package.json is missing']
    if lock_path.is_symlink():
        return ['package-lock.json is a symlink; refusing to follow']
    if not lock_path.is_file():
        return ['package-lock.json is missing']
    try:
        package = json.loads(package_path.read_text(encoding='utf-8'))
        lock = json.loads(lock_path.read_text(encoding='utf-8'))
    except (OSError, ValueError, TypeError) as exc:
        return ['Unable to parse npm package and lock: ' + str(exc)]
    if not isinstance(package, dict) or not isinstance(lock, dict):
        return ['npm package and lock must be JSON objects']
    problems: list[str] = []
    if lock.get('lockfileVersion') not in (2, 3):
        problems.append('Unsupported npm lockfileVersion (requires 2 or 3)')
    packages = lock.get('packages')
    if not isinstance(packages, dict):
        return problems + ['npm lock has no packages map']
    root = packages.get('')
    if not isinstance(root, dict):
        return problems + ['npm lock lacks root package metadata']
    if lock.get('name') != package.get('name') or root.get('version') != package.get('version'):
        problems.append('Package name or version differs from package.json')
    for group in ('dependencies', 'devDependencies'):
        if (root.get(group) or {}) != (package.get(group) or {}):
            problems.append(f'Root {group} differs from package.json')
        for name, version in (package.get(group) or {}).items():
            meta = packages.get('node_modules/' + name)
            if not isinstance(meta, dict):
                problems.append('Missing resolved package: ' + name)
            elif version and not any(c in str(version) for c in '^~*<>|') and meta.get('version') != version:
                problems.append('Resolved version mismatch: ' + name)
    if (package.get('devDependencies') or {}).get('vite') and 'node_modules/rollup' not in packages:
        problems.append('Missing transitive Rollup dependency')
    # Rollup and esbuild ship *separate* optional binaries for many CPU/OS
    # combinations. All declared records must exist in the lock; npm ci needs
    # them even when a particular native binary will not install on this host.
    for owner in ('rollup', 'esbuild'):
        record = packages.get('node_modules/' + owner)
        if not isinstance(record, dict):
            continue
        for name in (record.get('optionalDependencies') or {}):
            if owner == 'rollup' and not name.startswith('@rollup/rollup-'):
                continue
            if owner == 'esbuild' and not name.startswith('@esbuild/'):
                continue
            if 'node_modules/' + name not in packages:
                problems.append('Missing optional platform package from lock: ' + name)
    return problems


def lock_is_signed(root: Path) -> bool:
    manifest = Path(root) / 'docs/RELEASE_MANIFEST.json'
    try:
        payload = json.loads(manifest.read_text(encoding='utf-8'))
        return 'frontend/native-bi/package-lock.json' in payload.get('files', {})
    except (OSError, ValueError, TypeError):
        # Fail closed: a local source with no verifiable manifest must not have
        # its lock silently overwritten by a repair action.
        raise LockRepairError('No valid source release manifest; refusing automatic npm lock replacement')


def _execute(args: list[str], cwd: Path, timeout: int, runner) -> None:
    print('[NPM LOCK] ' + ' '.join(args) + ' (isolated work directory)', flush=True)
    if args and args[0] == 'npm':
        args = npm_argv(*args[1:])
    try:
        proc = runner(args, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise LockRepairError('Unable to run npm: ' + str(exc)) from exc
    if proc.returncode:
        detail = (proc.stderr or '') + '\n' + (proc.stdout or '')
        raise LockRepairError(f'Command failed (exit={proc.returncode}). '
                              + (detail.strip()[-2200:] or 'See npm registry/network status'))


def repair_lock(frontend: Path = FRONTEND, *, root: Path = ROOT,
                runner=subprocess.run, backup_base: Path | None = None) -> dict:
    frontend, root = Path(frontend).resolve(), Path(root).resolve()
    if lock_is_signed(root):
        raise LockRepairError('The npm lock is signed by this release. Obtain a corrected signed release; '
                              'the runtime must not silently change its SHA256')
    if os.environ.get('TIMEPILOT_OFFLINE', '').lower() in ('1', 'true', 'yes'):
        raise LockRepairError('TIMEPILOT_OFFLINE is set; a clean registry-backed lock cannot be generated')
    source = frontend / 'package.json'
    destination = frontend / 'package-lock.json'
    if not source.is_file() or source.is_symlink() or destination.is_symlink():
        raise LockRepairError('Missing package.json or unsafe symlink in npm frontend')
    old = destination.read_bytes() if destination.is_file() else None
    with tempfile.TemporaryDirectory(prefix='timepilot-npm-lock-clean-') as temp:
        clean = Path(temp)
        shutil.copyfile(source, clean / 'package.json')
        _execute(['npm', 'install', '--package-lock-only', '--ignore-scripts',
                  '--include=optional', '--no-audit', '--no-fund', '--save-exact'],
                 clean, 360, runner)
        candidate = clean / 'package-lock.json'
        if not candidate.is_file():
            raise LockRepairError('npm did not produce a genuine package-lock.json')
        problems = lock_issues(clean)
        if problems:
            raise LockRepairError('Generated npm lock is incomplete: ' + '; '.join(problems[:12]))
        # Important: static JSON validation alone failed to catch the user's
        # EUSAGE error. Actually run npm ci before accepting the replacement.
        _execute(['npm', 'ci', '--no-audit', '--no-fund', '--include=optional'],
                 clean, 600, runner)
        new = candidate.read_bytes()
    if new == old:
        return {'status': 'already_current', 'sha256': sha256(new), 'backup': None}
    backup_location = None
    if old is not None:
        target = (Path(backup_base).expanduser() if backup_base else
                  Path(os.environ.get('TIMEPILOT_HOME') or Path.home() / '.timepilot') /
                  'source_repair_backups').resolve()
        if target.is_relative_to(root):
            raise LockRepairError('Backup directory must be outside the source tree')
        folder = target / ('npm_lock_' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') +
                           '_' + uuid.uuid4().hex[:8])
        folder.mkdir(parents=True, mode=0o700)
        previous = folder / 'package-lock.json.original'
        previous.write_bytes(old)
        if previous.read_bytes() != old:
            raise LockRepairError('Cannot verify original npm lock backup')
        (folder / 'original_sha256.json').write_text(json.dumps({'sha256': sha256(old)}, indent=2) + '\n')
        backup_location = str(previous)
    fd, tmp = tempfile.mkstemp(prefix='.tp-npm-lock-', dir=frontend)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(new)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, destination)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    if destination.read_bytes() != new:
        raise LockRepairError('Published npm lock did not pass byte-for-byte verification')
    return {'status': 'repaired', 'sha256': sha256(new), 'backup': backup_location,
            'previous_sha256': sha256(old) if old is not None else None}


def main() -> int:
    parser = argparse.ArgumentParser(description='Audit or safely repair native BI npm optional platform lock entries')
    parser.add_argument('--repair-if-needed', action='store_true', help='Requires network/Node; validates real npm ci before publish')
    args = parser.parse_args()
    problems = lock_issues(FRONTEND)
    if not problems:
        print(json.dumps({'status': 'passed', 'frontend': str(FRONTEND)}, indent=2))
        return 0
    print('[NPM LOCK] ' + '; '.join(problems[:12]), flush=True)
    if not args.repair_if_needed:
        print('[NPM LOCK BLOCKED] Run `bash TIMEPILOT.sh bi-setup --bootstrap-lock` on a connected Node 24 host.', file=sys.stderr)
        return 2
    try:
        status = repair_lock()
        print(json.dumps(status, ensure_ascii=False, indent=2))
        return 0
    except (LockRepairError, OSError, ValueError) as exc:
        print('[NPM LOCK REPAIR BLOCKED] ' + str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
