"""Package source, validated native BI, or a complete target-platform offline runtime.

No npm or pip network activity is performed by this command. Full offline
includes a SHA-pinned Python wheelhouse resolved in CPython 3.12.12. Published
ZIPs are read back and verified without loading large wheels into RAM.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import zipfile
from pathlib import Path

from release_integrity import MANIFEST_NAME, build_manifest, project_version

ROOT = Path(__file__).resolve().parents[1]


def digest_file(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open('rb') as reader:
        for block in iter(lambda: reader.read(1024 * 1024), b''):
            sha.update(block)
    return sha.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description='Create verified TimePilot release archives')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--source-only', action='store_true', help='Source and basic BI only')
    parser.add_argument('--with-native-bundle', action='store_true', help='Require verified local JS/WASM')
    parser.add_argument('--complete-offline', action='store_true', help='Require native JS/WASM AND target-platform Python wheelhouse')
    parser.add_argument('--wheelhouse', type=Path, help='Required for complete offline mode; made on target OS/CPU')
    args = parser.parse_args()
    if args.source_only and (args.with_native_bundle or args.complete_offline):
        parser.error('--source-only cannot be combined with native/offline')
    if args.complete_offline and (not args.with_native_bundle or not args.wheelhouse):
        parser.error('--complete-offline requires --with-native-bundle and --wheelhouse')
    if args.wheelhouse and not args.complete_offline:
        parser.error('--wheelhouse requires --complete-offline')

    version = project_version(ROOT)
    manifest = build_manifest(ROOT)
    output = (args.output or ROOT.parent / f'TimePilot_v{version}_SOURCE.zip').expanduser().resolve()
    native_files: dict[str, dict] = {}
    runtime_files: dict[str, dict] = {}
    if args.with_native_bundle:
        import sys
        sys.path.insert(0, str(ROOT / 'src'))
        from timepilot.native_bi_bundle import bundle_status, frontend_matches_bundle
        bundle = ROOT / 'src/timepilot/static/native-bi'
        status = bundle_status(bundle)
        lock = ROOT / 'frontend/native-bi/package-lock.json'
        if not lock.is_file():
            raise ValueError('BLOCKED: no reviewed frontend/npm package-lock.json')
        if not status['ready'] or not frontend_matches_bundle(bundle, ROOT / 'frontend/native-bi'):
            raise ValueError('BLOCKED: invalid JS/WASM or source hash mismatch: ' + '; '.join(status['problems'][:5]))
        native_files = {p.relative_to(ROOT).as_posix():
                        {'sha256': digest_file(p), 'bytes': p.stat().st_size}
                        for p in bundle.rglob('*') if p.is_file()}
        if len(native_files) < 7:
            raise ValueError('BLOCKED: incomplete Perspective resources')
        manifest['runtime_assets'] = native_files
        manifest['native_bi_release_status'] = 'compiled-offline-verified'

    if args.complete_offline:
        from offline_wheels import verify_wheelhouse, MANIFEST, LOCK
        wheels = args.wheelhouse.expanduser().resolve(strict=True)
        validation = verify_wheelhouse(wheels, resolve=True)
        for file in sorted(wheels.iterdir()):
            if file.is_file() and (file.suffix == '.whl' or file.name in (MANIFEST, LOCK)):
                name = 'offline-wheelhouse/' + file.name
                runtime_files[name] = {'sha256': digest_file(file), 'bytes': file.stat().st_size,
                                       '_path': file}
        manifest['runtime_wheelhouse'] = {
            name: {key: value for key, value in meta.items() if key != '_path'}
            for name, meta in runtime_files.items()}
        manifest['python_runtime_wheels_included'] = True
        manifest['wheelhouse_platform'] = validation['platform']
        manifest['deployment_note'] = 'Requires existing matching CPython 3.12.12; no environment is created.'
    else:
        manifest['python_runtime_wheels_included'] = False
        if args.with_native_bundle:
            manifest['deployment_note'] = 'Offline JS/WASM only; Python dependencies must be installed separately.'
        else:
            manifest['deployment_note'] = 'Source only; no offline Python or Perspective JS/WASM included.'

    kind = 'complete-offline' if args.complete_offline else 'native-bi-offline' if args.with_native_bundle else 'source-only'
    manifest['distribution_kind'] = kind
    encoded = (json.dumps(manifest, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    prefix = f'TimePilot_v{version}/'
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix='.timepilot-packaging-', suffix='.zip', dir=output.parent)
    os.close(descriptor)
    all_entries = {**manifest['files'], **native_files, **runtime_files}
    try:
        with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_DEFLATED,
                             compresslevel=8, allowZip64=True) as archive:
            for name in sorted(manifest['files']):
                archive.write(ROOT / name, prefix + name)
            for name in sorted(native_files):
                archive.write(ROOT / name, prefix + name)
            for name in sorted(runtime_files):
                archive.write(runtime_files[name]['_path'], prefix + name)
            archive.writestr(prefix + MANIFEST_NAME, encoded)
        with zipfile.ZipFile(temporary, 'r') as archive:
            names = archive.namelist()
            if len(names) != len(set(names)) or len(names) != len(all_entries) + 1:
                raise ValueError('ZIP has duplicate or missing file entries')
            if archive.read(prefix + MANIFEST_NAME) != encoded:
                raise ValueError('ZIP release manifest mismatch')
            for name, meta in all_entries.items():
                sha = hashlib.sha256(); count = 0
                with archive.open(prefix + name) as reader:
                    for block in iter(lambda: reader.read(1024 * 1024), b''):
                        sha.update(block); count += len(block)
                if count != meta['bytes'] or sha.hexdigest() != meta['sha256']:
                    raise ValueError('ZIP content SHA256 mismatch: ' + name)
        os.replace(temporary, output)
        os.chmod(output, 0o644)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    print(json.dumps({'archive': str(output), 'version': version, 'distribution_kind': kind,
                      'source_files': len(manifest['files']), 'native_files': len(native_files),
                      'runtime_wheelhouse_files': len(runtime_files), 'bytes': output.stat().st_size,
                      'sha256': digest_file(output)}, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
