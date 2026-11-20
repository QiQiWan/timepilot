"""Build-host-only, hashed CPython 3.12.12 wheelhouse per OS and architecture.

This script NEVER provisions an environment. `prepare` explicitly downloads
wheels on a networked build machine. `verify` is read-only and uses pip's
--dry-run resolver in the current environment, with no network access.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import sysconfig
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FORMAT = 1
MANIFEST = 'OFFLINE_WHEELS.json'
LOCK = 'requirements-offline-lock.txt'
TARGET_PY = (3, 12, 12)


class OfflineWheelError(RuntimeError):
    pass


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as reader:
        for block in iter(lambda: reader.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _environment() -> dict:
    return {
        'python': '.'.join(str(n) for n in sys.version_info[:3]),
        'implementation': sys.implementation.name,
        'platform': sysconfig.get_platform(),
        'machine': platform.machine(),
        'system': platform.system(),
    }


def target_python_ok() -> bool:
    return sys.version_info[:3] == TARGET_PY and sys.implementation.name == 'cpython'


def require_builder() -> None:
    if not target_python_ok():
        raise OfflineWheelError('Wheelhouse builder must use existing CPython 3.12.12 (no environment created)')


def _wheel_name_version(filename: str) -> tuple[str, str]:
    from pip._vendor.packaging.utils import parse_wheel_filename
    try:
        name, version, _, _ = parse_wheel_filename(filename)
    except Exception as exc:
        raise OfflineWheelError('Invalid wheel filename: ' + filename) from exc
    return str(name), str(version)


def _all_wheels(directory: Path) -> list[Path]:
    files = sorted(directory.glob('*.whl'))
    if not files:
        raise OfflineWheelError('Wheelhouse is empty; cannot issue an offline distribution')
    if any(p.is_symlink() for p in files):
        raise OfflineWheelError('Symlink wheel is not allowed')
    return files


def _lock_lines(files: list[Path]) -> list[str]:
    grouped = {}
    for path in files:
        name, version = _wheel_name_version(path.name)
        key = name.lower().replace('_', '-')
        if key in grouped:
            raise OfflineWheelError(f'Duplicate wheel distribution: {name}; keep one target-platform build')
        grouped[key] = f'{name}=={version} --hash=sha256:{_sha(path)}'
    return [grouped[k] for k in sorted(grouped)]


def _verify_resolver(directory: Path) -> None:
    cmd = [sys.executable, '-m', 'pip', 'install', '--dry-run', '--ignore-installed',
           '--no-index', '--only-binary=:all:', '--find-links', str(directory),
           '--require-hashes', '-r', str(directory / LOCK)]
    try:
        result = subprocess.run(cmd, text=True, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, timeout=900)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise OfflineWheelError('Offline pip resolver unavailable: ' + str(exc)) from exc
    if result.returncode:
        raise OfflineWheelError('Wheel dependencies not closed or incompatible: ' + (result.stdout or '')[-4500:])


def verify_wheelhouse(directory: Path, *, resolve: bool = True) -> dict:
    directory = Path(directory).expanduser().resolve(strict=True)
    payload = json.loads((directory / MANIFEST).read_text(encoding='utf-8'))
    if payload.get('format') != FORMAT or not isinstance(payload.get('files'), dict):
        raise OfflineWheelError('Invalid wheelhouse manifest')
    original_env = payload.get('environment')
    if original_env != _environment() or not target_python_ok():
        raise OfflineWheelError('Offline wheels do not match current CPython 3.12.12 OS/CPU platform')
    if _sha(ROOT / 'requirements.txt') != payload.get('source_requirements_sha256') or _sha(ROOT / 'pyproject.toml') != payload.get('source_project_sha256'):
        raise OfflineWheelError('Wheelhouse built for different source dependency declarations')
    files = _all_wheels(directory)
    actual = {p.name for p in files}
    if actual != set(payload['files']) or any('/' in name or '\\' in name for name in actual):
        raise OfflineWheelError('Wheel inventory differs from frozen manifest')
    for file in files:
        info = payload['files'][file.name]
        if not isinstance(info, dict) or info.get('bytes') != file.stat().st_size or info.get('sha256') != _sha(file):
            raise OfflineWheelError('Invalid offline wheel checksum: ' + file.name)
    lock = (directory / LOCK)
    if _sha(lock) != payload.get('lock_sha256'):
        raise OfflineWheelError('Offline requirements lock has changed')
    if lock.read_text(encoding='utf-8').splitlines() != _lock_lines(files):
        raise OfflineWheelError('Offline requirements are not an exact SHA-pinned wheel inventory')
    if resolve:
        _verify_resolver(directory)
    return {'ready': True, 'platform': original_env, 'wheel_count': len(files),
            'bytes': sum(p.stat().st_size for p in files),
            'sha256': payload.get('lock_sha256'), 'resolver_verified': resolve}


def _run(args: list[str], *, timeout: int = 3600) -> None:
    try:
        subprocess.run(args, check=True, timeout=timeout)
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise OfflineWheelError('Build-host command failed: ' + ' '.join(args[:6]) +
                                ' (' + type(exc).__name__ + ')') from exc


def prepare(directory: Path) -> dict:
    """Online, explicit builder step. Refuse to modify an existing wheelhouse."""
    require_builder()
    directory = Path(directory).expanduser().absolute()
    if directory.exists():
        raise OfflineWheelError('Refuse to overwrite existing wheelhouse; choose a new directory')
    directory.parent.mkdir(parents=True, exist_ok=True)
    # External packages are resolved together; local editable project builds
    # exactly once into a wheel and is pinned alongside their dependencies.
    with tempfile.TemporaryDirectory(prefix='tp-wheel-', dir=directory.parent) as base:
        stage = Path(base) / 'wheels'
        stage.mkdir()
        requirements = (ROOT / 'requirements.txt').read_text(encoding='utf-8').splitlines()
        externals = [x for x in requirements if x.strip() and not x.lstrip().startswith('#') and
                     x.strip() not in ('-e .', '.')]
        if not externals or '-e .' not in requirements:
            raise OfflineWheelError('Unexpected project dependencies; expected -e . in requirements.txt')
        external_file = Path(base) / 'external-requirements.txt'
        external_file.write_text('\n'.join(externals) + '\n', encoding='utf-8')
        _run([sys.executable, '-m', 'pip', 'download', '--only-binary=:all:',
              '--dest', str(stage), '-r', str(external_file)])
        _run([sys.executable, '-m', 'pip', 'wheel', '--no-deps', '--wheel-dir', str(stage), str(ROOT)])
        files = _all_wheels(stage)
        (stage / LOCK).write_text('\n'.join(_lock_lines(files)) + '\n', encoding='utf-8')
        _verify_resolver(stage)
        manifest = {'format': FORMAT, 'created_at_utc': datetime.now(timezone.utc).isoformat(),
                    'environment': _environment(),
                    'source_requirements_sha256': _sha(ROOT / 'requirements.txt'),
                    'source_project_sha256': _sha(ROOT / 'pyproject.toml'),
                    'lock_sha256': _sha(stage / LOCK),
                    'files': {p.name: {'bytes': p.stat().st_size, 'sha256': _sha(p)} for p in files}}
        (stage / MANIFEST).write_text(json.dumps(manifest, indent=2, ensure_ascii=False)+'\n', encoding='utf-8')
        stage.rename(directory)
    return verify_wheelhouse(directory)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['prepare', 'verify'])
    parser.add_argument('--wheelhouse', type=Path, required=True)
    args = parser.parse_args()
    try:
        result = prepare(args.wheelhouse) if args.action == 'prepare' else verify_wheelhouse(args.wheelhouse)
    except (OfflineWheelError, OSError, ValueError, KeyError) as exc:
        print('[OFFLINE RUNTIME BLOCKED] ' + str(exc), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
