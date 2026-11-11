"""Repair an incomplete or mixed TimePilot install from a COMPLETE signed ZIP.

No network, package install, browser download, or user-data deletion. Every ZIP
member is verified *before* writing. Changed files and obsolete release code
are backed up. The old release is restored if any write/verification fails.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import sys
import tempfile
import tomllib
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from zipfile import ZipFile

from release_integrity import MANIFEST_NAME, REQUIRED, check_tree, source_files, valid_relative_path

MAX_TOTAL_SOURCE_BYTES = 128 * 1024 * 1024
MAX_SOURCE_FILES = 2500
CODE_PREFIXES = (
    'src/timepilot/', 'scripts/', 'tests/',
    'frontend/native-bi/src/', 'frontend/native-bi/tests/',
    'docs/REVIEW_V0',
)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_verified_archive(path: Path) -> tuple[dict[str, bytes], dict]:
    with ZipFile(path) as zipped:
        infos = zipped.infolist()
        names = [info.filename for info in infos]
        if len(names) != len(set(names)) or len(infos) > MAX_SOURCE_FILES + 1:
            raise ValueError('Duplicate/excessive members in archive')
        roots = [name[:-len(MANIFEST_NAME)] for name in names if name.endswith(MANIFEST_NAME)]
        if len(roots) != 1:
            raise ValueError('ZIP must contain exactly one release manifest')
        prefix = roots[0]
        manifest_data = zipped.read(prefix + MANIFEST_NAME)
        manifest = json.loads(manifest_data)
        entries = manifest.get('files')
        if (manifest.get('format') != 2 or not isinstance(entries, dict)
                or sorted(set(REQUIRED) - set(entries))):
            raise ValueError('This is not a complete format-2 TimePilot source release')
        if not isinstance(manifest.get('version'), str):
            raise ValueError('Missing release version')
        if prefix != f'TimePilot_v{manifest["version"]}/':
            raise ValueError('Unexpected release root name/version')
        # A compiled offline release additionally includes SHA256-pinned
        # Perspective JS/CSS/WASM under the native-bi runtime asset directory.
        # These assets are intentionally absent from the source manifest.
        native_assets = manifest.get('runtime_assets', {})
        if not isinstance(native_assets, dict):
            raise ValueError('Invalid compiled runtime asset manifest')
        if native_assets and (manifest.get('native_bi_release_status') != 'compiled-offline-verified'
                              or len(native_assets) < 7):
            raise ValueError('Incomplete or unverified compiled BI release')
        for name in native_assets:
            if (not valid_relative_path(name) or
                    not name.startswith('src/timepilot/static/native-bi/')):
                raise ValueError('Unexpected compiled asset outside BI runtime: ' + name)
        expected = {prefix + name for name in entries} | {prefix + name for name in native_assets} | {prefix + MANIFEST_NAME}
        if set(names) != expected:
            raise ValueError('ZIP contains unknown or missing source/runtime entries')
        if sum(info.file_size for info in infos) > MAX_TOTAL_SOURCE_BYTES:
            raise ValueError('ZIP is too large for a source-only release')
        data = {MANIFEST_NAME: manifest_data}
        for name, meta in {**entries, **native_assets}.items():
            if not valid_relative_path(name):
                raise ValueError(f'Unsafe relative filename: {name!r}')
            info = zipped.getinfo(prefix + name)
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode) or (mode and not stat.S_ISREG(mode)):
                raise ValueError('Non-regular ZIP member: ' + name)
            if type(meta.get('bytes')) is not int or not isinstance(meta.get('sha256'), str):
                raise ValueError('Bad source checksum metadata: ' + name)
            contents = zipped.read(info)
            if len(contents) != meta['bytes'] or digest(contents) != meta['sha256']:
                raise ValueError('Archive content checksum mismatch: ' + name)
            data[name] = contents
        version = tomllib.loads(data['pyproject.toml'].decode('utf-8'))['project']['version']
        if version != manifest['version']:
            raise ValueError('Archive metadata/Python project versions disagree')
        if f'__version__ = "{version}"' not in data['src/timepilot/__init__.py'].decode('utf-8'):
            raise ValueError('Archive Python package version disagrees')
        return data, manifest


def safe_target(root: Path, relative: str) -> Path:
    if not valid_relative_path(relative):
        raise ValueError(f'Unsafe target path: {relative!r}')
    target = root.joinpath(*PurePosixPath(relative).parts)
    if target.is_symlink() or not target.resolve().is_relative_to(root.resolve()):
        raise ValueError(f'Refusing symlink/path escape: {relative}')
    return target


def restore(archive: Path, root: Path, *, apply: bool = False) -> dict:
    root = root.expanduser().resolve()
    # A complete archive can bootstrap a NEW empty project as well as repair
    # an existing installation. Refuse to overwrite an unrelated nonempty dir.
    if not root.exists():
        if apply:
            root.mkdir(parents=True)
        else:
            raise ValueError('Target directory does not exist (dry-run)')
    if not root.is_dir():
        raise ValueError('Target must be a directory')
    if not (root / 'pyproject.toml').is_file() and any(root.iterdir()):
        raise ValueError('Target is neither an existing TimePilot project nor an empty directory')
    if (root / 'pyproject.toml').is_file():
        try:
            existing_name = tomllib.loads((root / 'pyproject.toml').read_text(encoding='utf-8'))['project']['name']
        except (OSError, ValueError, KeyError, tomllib.TOMLDecodeError) as exc:
            raise ValueError('Target pyproject.toml is invalid') from exc
        if existing_name.lower() != 'timepilot':
            raise ValueError('Target pyproject.toml belongs to another project; refusing overwrite')
    data, manifest = load_verified_archive(archive)
    changed = []
    for name, contents in data.items():
        target = safe_target(root, name)
        if not target.is_file() or digest(target.read_bytes()) != digest(contents):
            changed.append(name)
    old_extra = sorted(set(source_files(root)) - set(manifest['files']))
    # Do not remove a user's untracked research models/tools. Quarantine only
    # explicitly named obsolete debug/patch leftovers from earlier releases.
    obsolete_code = [name for name in old_extra if name.startswith(CODE_PREFIXES)
                     and Path(name).name.startswith(('obsolete_debug_', 'legacy_debug_', 'debug_patch_'))]
    result = {'target': str(root), 'release_version': manifest['version'],
              'changed_count': len(changed), 'changed_files': changed,
              'obsolete_code_quarantine': obsolete_code,
              'other_untracked_sources_preserved': [x for x in old_extra if x not in obsolete_code],
              'applied': False, 'backup': None}
    if not apply:
        return result
    if not changed and not obsolete_code:
        issues, _ = check_tree(root)
        result['verification_issues'] = issues
        result['applied'] = not issues
        return result

    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    backup = root / '.timepilot-backup' / f'upgrade_{stamp}_{os.getpid()}'
    backup.mkdir(parents=True, exist_ok=False)
    previous = set()
    for name in changed + obsolete_code:
        source = safe_target(root, name)
        if source.exists():
            destination = backup.joinpath(*PurePosixPath(name).parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            previous.add(name)
    written: list[str] = []
    moved: list[str] = []
    try:
        for name in obsolete_code:
            safe_target(root, name).unlink()
            moved.append(name)
        # Write manifest last. If interrupted the preflight rejects the mixed tree.
        for name in [x for x in changed if x != MANIFEST_NAME] + ([MANIFEST_NAME] if MANIFEST_NAME in changed else []):
            target = safe_target(root, name)
            target.parent.mkdir(parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(prefix='.tp-install-', dir=target.parent)
            try:
                with os.fdopen(fd, 'wb') as handle:
                    handle.write(data[name])
                    handle.flush()
                    os.fsync(handle.fileno())
                # Keep executable entrypoints executable after an atomic upgrade.
                # A new mkstemp file would otherwise be mode 0600 on macOS/Linux.
                if target.exists():
                    mode = stat.S_IMODE(target.stat().st_mode)
                else:
                    mode = 0o755 if name.endswith('.sh') else 0o644
                os.chmod(temporary, mode)
                os.replace(temporary, target)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
            written.append(name)
        issues, _ = check_tree(root)
        if issues:
            raise RuntimeError('Upgrade failed verification: ' + '; '.join(issues[:10]))
        result.update({'applied': True, 'verification_issues': [], 'backup': str(backup)})
        return result
    except BaseException:
        for name in reversed(written + moved):
            target = safe_target(root, name)
            if name in previous:
                original = backup.joinpath(*PurePosixPath(name).parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(original, target)
            else:
                target.unlink(missing_ok=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description='Offline verified TimePilot upgrade and repair')
    parser.add_argument('--archive', required=True, type=Path)
    parser.add_argument('--target', default=Path.cwd(), type=Path)
    parser.add_argument('--apply', action='store_true', help='Write verified files, preserving a backup')
    args = parser.parse_args()
    try:
        result = restore(args.archive, args.target, apply=args.apply)
    except (OSError, RuntimeError, ValueError) as exc:
        print('[FAIL] Source repair rejected: ' + str(exc), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not args.apply:
        print('Dry run only; rerun with --apply to repair the target project.')
    return 0 if args.apply and result.get('applied') else (0 if not args.apply else 2)


if __name__ == '__main__':
    raise SystemExit(main())
