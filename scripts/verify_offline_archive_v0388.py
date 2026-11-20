"""Independently verify a TimePilot release ZIP without extraction or network.

The source and full-offline distributions use different contracts. A source ZIP
can be sound while still being BLOCKED from an offline-production claim.
Every file is streamed through SHA256. No user runtime directory is touched.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import stat
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

SHA = re.compile(r"[0-9a-f]{64}\Z")
ROOT_PREFIX = re.compile(r"TimePilot_v(\d+\.\d+\.\d+)/\Z")
MAX_FILES = 30000
MAX_MANIFEST_BYTES = 8 * 1024 * 1024
MAX_ARCHIVE_UNCOMPRESSED = 16 * 1024**3


class ArchiveProblem(ValueError):
    pass


def valid_path(path: str) -> bool:
    # Zip paths are always POSIX; refuse backslash, alternative separators and
    # any path which a host might normalize outside the intended release root.
    item = PurePosixPath(path)
    return (isinstance(path, str) and path and not path.startswith('/')
            and '\\' not in path and ':' not in path
            and all(bit not in ('', '.', '..') for bit in path.split('/'))
            and item.as_posix() == path)


def _validate_file_map(mapping, name):
    if not isinstance(mapping, dict):
        raise ArchiveProblem(name + ' must be a file map')
    for path, meta in mapping.items():
        if not valid_path(path):
            raise ArchiveProblem(f'Unsafe {name} path: {path!r}')
        if not isinstance(meta, dict) or not isinstance(meta.get('bytes'), int) or isinstance(meta['bytes'], bool):
            raise ArchiveProblem(f'Invalid byte count for {path}')
        if meta['bytes'] < 0 or meta['bytes'] > MAX_ARCHIVE_UNCOMPRESSED:
            raise ArchiveProblem(f'Unreasonable byte count for {path}')
        if not isinstance(meta.get('sha256'), str) or SHA.fullmatch(meta['sha256']) is None:
            raise ArchiveProblem(f'Invalid SHA256 for {path}')


def verify_archive(archive: Path, *, require_complete=False, source_root: Path | None = None) -> dict:
    archive = Path(archive)
    if not archive.is_file() or archive.is_symlink():
        raise ArchiveProblem('Expected a regular ZIP file, not a symlink')
    with zipfile.ZipFile(archive, 'r') as handle:
        infos = handle.infolist()
        if not infos or len(infos) > MAX_FILES:
            raise ArchiveProblem('ZIP entry count is empty or exceeds safety budget')
        names = [entry.filename for entry in infos]
        if len(names) != len(set(names)):
            raise ArchiveProblem('ZIP contains duplicate entry names')
        if any(not valid_path(name) for name in names):
            raise ArchiveProblem('ZIP contains unsafe absolute/relative names')
        if any(entry.is_dir() or stat.S_ISLNK((entry.external_attr >> 16) & 0xffff) for entry in infos):
            raise ArchiveProblem('ZIP contains a directory entry or symbolic link')
        if sum(entry.file_size for entry in infos) > MAX_ARCHIVE_UNCOMPRESSED:
            raise ArchiveProblem('ZIP declares excessive uncompressed bytes')
        candidate_manifests = [name for name in names if name.endswith('/docs/RELEASE_MANIFEST.json')]
        if len(candidate_manifests) != 1:
            raise ArchiveProblem('Exactly one embedded release manifest is required')
        manifest_name = candidate_manifests[0]
        prefix = manifest_name.removesuffix('docs/RELEASE_MANIFEST.json')
        match = ROOT_PREFIX.fullmatch(prefix)
        if not match or any(not name.startswith(prefix) for name in names):
            raise ArchiveProblem('Mixed or invalid release ZIP root directory')
        info = handle.getinfo(manifest_name)
        if info.file_size > MAX_MANIFEST_BYTES:
            raise ArchiveProblem('Release manifest exceeds safety budget')
        try:
            manifest = json.loads(handle.read(manifest_name))
        except (UnicodeDecodeError, ValueError) as exc:
            raise ArchiveProblem('Invalid release manifest JSON') from exc
        if not isinstance(manifest, dict) or manifest.get('format') != 2:
            raise ArchiveProblem('Unsupported release manifest format')
        version = manifest.get('version')
        if version != match.group(1):
            raise ArchiveProblem('Release ZIP name and signed manifest version differ')
        kind = manifest.get('distribution_kind')
        if kind not in ('source-only', 'native-bi-offline', 'complete-offline'):
            raise ArchiveProblem('Unknown distribution kind')
        maps = {'files': manifest.get('files'),
                'runtime_assets': manifest.get('runtime_assets', {}),
                'runtime_wheelhouse': manifest.get('runtime_wheelhouse', {})}
        for name, mapping in maps.items():
            _validate_file_map(mapping, name)
        files = maps['files']
        if source_root is not None:
            # Verify that this archive was built from this exact reviewed source
            # tree, rather than from a different revision with the same version.
            from release_integrity import build_manifest
            current = build_manifest(Path(source_root))
            if current['version'] != version or current['files'] != files:
                raise ArchiveProblem('ZIP source differs from the checked-out source tree')
        native = maps['runtime_assets']
        wheels = maps['runtime_wheelhouse']
        if (set(files) & set(native)) or (set(files) & set(wheels)) or (set(native) & set(wheels)):
            raise ArchiveProblem('Same file appears in multiple manifest sections')
        if set(names) != {prefix + name for section in maps.values() for name in section} | {manifest_name}:
            raise ArchiveProblem('ZIP and manifest entry sets differ')
        if 'pyproject.toml' not in files or 'frontend/native-bi/package.json' not in files:
            raise ArchiveProblem('Incomplete core source files')
        offline_evidence = (
            kind == 'complete-offline'
            and manifest.get('python_runtime_wheels_included') is True
            and manifest.get('native_bi_release_status') == 'compiled-offline-verified'
            and isinstance(manifest.get('wheelhouse_platform'), dict)
            and 'frontend/native-bi/package-lock.json' in files
            and 'src/timepilot/static/native-bi/perspective-build.json' in native
            and sum(name.endswith('.whl') for name in wheels) >= 1
            and 'offline-wheelhouse/OFFLINE_WHEELS.json' in wheels
            and 'offline-wheelhouse/requirements-offline-lock.txt' in wheels
        )
        if kind == 'complete-offline' and not offline_evidence:
            raise ArchiveProblem('Archive claims complete-offline but lacks mandatory runtime evidence')
        if kind == 'source-only' and (native or wheels or manifest.get('python_runtime_wheels_included')):
            raise ArchiveProblem('Source-only archive carries inconsistent runtime metadata')
        scanned = 0
        for path, meta in {**files, **native, **wheels}.items():
            zipped = prefix + path
            if handle.getinfo(zipped).file_size != meta['bytes']:
                raise ArchiveProblem('Size mismatch: ' + path)
            digest = hashlib.sha256()
            with handle.open(zipped) as stream:
                for part in iter(lambda: stream.read(1024 * 1024), b''):
                    digest.update(part)
                    scanned += len(part)
            if digest.hexdigest() != meta['sha256']:
                raise ArchiveProblem('SHA256 mismatch: ' + path)
    result = {'status': 'passed' if not require_complete or offline_evidence else 'blocked',
              'version': version, 'distribution_kind': kind,
              'files_verified': len(files), 'runtime_assets_verified': len(native),
              'wheelhouse_files_verified': len(wheels),
              'bytes_hashed': scanned, 'complete_offline_evidence': bool(offline_evidence)}
    if require_complete and not offline_evidence:
        result['missing'] = ['signed_native_js_wasm_and_target_wheelhouse']
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--require-complete', action='store_true')
    parser.add_argument('--source-root', type=Path, help='Require an identical current source tree')
    parser.add_argument('--out', type=Path)
    args = parser.parse_args(argv)
    receipt = {'timestamp_utc': datetime.now(timezone.utc).isoformat(),
               'archive': str(args.archive), 'require_complete': args.require_complete}
    try:
        receipt.update(verify_archive(args.archive, require_complete=args.require_complete, source_root=args.source_root))
    except (ValueError, zipfile.BadZipFile, OSError, KeyError, TypeError) as exc:
        receipt.update(status='failed', error_type=type(exc).__name__, reason=str(exc)[:1000])
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        temp = args.out.with_suffix(args.out.suffix + '.tmp')
        temp.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        temp.replace(args.out)
    print(json.dumps(receipt, ensure_ascii=False))
    return {'passed': 0, 'blocked': 2, 'failed': 1}[receipt['status']]


if __name__ == '__main__':
    raise SystemExit(main())
