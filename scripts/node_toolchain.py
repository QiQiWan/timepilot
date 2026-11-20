"""Find or stage Node.js 24 for TimePilot's first-run Perspective build.

Windows source builds may download the official Node 24 *ZIP* into the user's
TimePilot tool cache, verify its published SHA256 and use it for child processes.
No administrator rights, PATH persistence, Conda changes or global installers.
A verified BI bundle does not need Node during later startup.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from typing import Callable

INDEX_URL = 'https://nodejs.org/dist/index.json'
DIST = 'https://nodejs.org/dist'
VERSION_RE = re.compile(r'^v24\.\d+\.\d+$')
MAX_INDEX_BYTES = 2 * 1024 * 1024
MAX_SUMS_BYTES = 80 * 1024
MAX_ARCHIVE_BYTES = 110 * 1024 * 1024
MAX_EXTRACTED_BYTES = 420 * 1024 * 1024
MAX_FILES = 15_000


class NodeToolchainError(RuntimeError):
    """A supported Node/npm runtime is not available for a source build."""


def _offline(env: dict[str, str]) -> bool:
    return env.get('TIMEPILOT_OFFLINE', '').strip().lower() in {'1', 'yes', 'true'}


def _is_windows() -> bool:
    return sys.platform == 'win32'


def _download(url: str, maximum: int, *, opener=urllib.request.urlopen) -> bytes:
    """Bounded HTTPS metadata download. No alternate or untrusted host."""
    if not url.startswith('https://nodejs.org/dist/'):
        raise NodeToolchainError('Only official nodejs.org release downloads are permitted')
    try:
        with opener(url, timeout=35) as response:
            data = response.read(maximum + 1)
    except (OSError, urllib.error.URLError, TimeoutError) as exc:
        raise NodeToolchainError('Cannot reach official Node.js downloads via HTTPS: ' + str(exc)) from exc
    if len(data) > maximum:
        raise NodeToolchainError('Node.js metadata exceeds the permitted size')
    return data


def _resolve_release(*, opener=urllib.request.urlopen, machine: str | None = None,
                     requested: str | None = None) -> tuple[str, str]:
    arch = (machine or platform.machine()).lower()
    if arch in ('amd64', 'x86_64', 'x64'):
        target = 'win-x64'
    elif arch in ('arm64', 'aarch64'):
        target = 'win-arm64'
    else:
        raise NodeToolchainError('Unsupported Windows CPU architecture: ' + arch)
    if requested and not VERSION_RE.fullmatch(requested):
        raise NodeToolchainError('TIMEPILOT_NODE_VERSION must be an exact v24.x.y version')
    try:
        items = json.loads(_download(INDEX_URL, MAX_INDEX_BYTES, opener=opener))
    except (ValueError, UnicodeDecodeError) as exc:
        raise NodeToolchainError('Invalid official Node.js version index') from exc
    if not isinstance(items, list):
        raise NodeToolchainError('Invalid official Node.js index format')
    matches = []
    for row in items:
        if not isinstance(row, dict):
            continue
        version = row.get('version')
        if (isinstance(version, str) and VERSION_RE.fullmatch(version)
                and row.get('lts') and target + '-zip' in (row.get('files') or [])):
            if requested is None or requested == version:
                matches.append(version)
    if not matches:
        raise NodeToolchainError('No official Node 24 LTS ' + target + ' ZIP release found')
    return max(matches, key=lambda v: tuple(map(int, v[1:].split('.')))), target


def _official_sha(sums: str, filename: str) -> str:
    for line in sums.splitlines():
        fields = line.strip().split()
        if len(fields) == 2 and fields[1].lstrip('*') == filename and re.fullmatch(r'[0-9a-fA-F]{64}', fields[0]):
            return fields[0].lower()
    raise NodeToolchainError('Official SHA256 manifest does not contain ' + filename)


def _get_archive(url: str, destination: Path, expected_sha: str,
                 *, opener=urllib.request.urlopen) -> None:
    if not url.startswith('https://nodejs.org/dist/'):
        raise NodeToolchainError('Untrusted archive URL')
    digest = hashlib.sha256()
    size = 0
    try:
        with opener(url, timeout=45) as response, destination.open('wb') as output:
            while True:
                block = response.read(256 * 1024)
                if not block:
                    break
                size += len(block)
                if size > MAX_ARCHIVE_BYTES:
                    raise NodeToolchainError('Node ZIP exceeds download safety limit')
                digest.update(block)
                output.write(block)
    except (OSError, urllib.error.URLError, TimeoutError) as exc:
        raise NodeToolchainError('Official Node ZIP download failed: ' + str(exc)) from exc
    if digest.hexdigest() != expected_sha:
        raise NodeToolchainError('Downloaded Node ZIP SHA256 mismatch; refusing execution')


def _extract_verified_zip(archive: Path, stage: Path, folder: str) -> Path:
    """Extract regular files only, below the expected one-directory ZIP root."""
    stage.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(archive) as zf:
            members = zf.infolist()
            if len(members) > MAX_FILES:
                raise NodeToolchainError('Node archive has excessive entries')
            if sum(x.file_size for x in members) > MAX_EXTRACTED_BYTES:
                raise NodeToolchainError('Node archive expands beyond size limit')
            for member in members:
                parts = PurePosixPath(member.filename).parts
                if (not parts or parts[0] != folder or any(x in ('', '.', '..') for x in parts)
                        or '\\' in member.filename or member.filename.startswith('/')):
                    raise NodeToolchainError('Unsafe path in Node archive')
                if stat.S_IFMT(member.external_attr >> 16) == stat.S_IFLNK:
                    raise NodeToolchainError('Node archive contains an unexpected symlink')
                target = stage.joinpath(*parts)
                if member.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(member) as source, target.open('wb') as dest:
                        shutil.copyfileobj(source, dest, length=256 * 1024)
    except (zipfile.BadZipFile, EOFError) as exc:
        raise NodeToolchainError('Corrupted Node ZIP archive') from exc
    return stage / folder


def _probe(root: Path, env: dict[str, str], *, windows: bool,
           runner: Callable = subprocess.run) -> str | None:
    exe = root / ('node.exe' if windows else 'bin/node')
    npm = root / ('npm.cmd' if windows else 'bin/npm')
    if not (exe.is_file() and npm.is_file()):
        return None
    try:
        completed = runner([str(exe), '--version'], capture_output=True,
                           text=True, timeout=10, check=False, env=env)
    except (OSError, subprocess.TimeoutExpired):
        return None
    version = (completed.stdout or '').strip()
    return version if completed.returncode == 0 and VERSION_RE.fullmatch(version) else None


def _with_path(env: dict[str, str], directory: Path) -> dict[str, str]:
    updated = dict(env)
    updated['PATH'] = str(directory) + os.pathsep + updated.get('PATH', '')
    return updated


def _find_existing(env: dict[str, str], cache_root: Path, *, windows: bool,
                   runner: Callable) -> tuple[dict[str, str], str] | None:
    explicit = env.get('TIMEPILOT_NODE_HOME', '').strip()
    if explicit:
        root = Path(explicit).expanduser().resolve()
        if root.name == 'bin' and not windows:
            root = root.parent
        version = _probe(root, env, windows=windows, runner=runner)
        if not version:
            raise NodeToolchainError('TIMEPILOT_NODE_HOME has no working Node 24 and npm: ' + str(root))
        return _with_path(env, root if windows else root / 'bin'), version

    node = shutil.which('node', path=env.get('PATH'))
    npm = shutil.which('npm', path=env.get('PATH'))
    if node and npm:
        try:
            completed = runner([node, '--version'], capture_output=True,
                               text=True, timeout=10, check=False, env=env)
            version = (completed.stdout or '').strip()
            if completed.returncode == 0 and VERSION_RE.fullmatch(version):
                return dict(env), version
        except (OSError, subprocess.TimeoutExpired):
            pass

    if cache_root.is_dir():
        def key(p: Path):
            match = re.match(r'^node-(v24\.\d+\.\d+)-win-(?:x64|arm64)$', p.name)
            return tuple(map(int, match.group(1)[1:].split('.'))) if match else (-1, -1, -1)
        for directory in sorted((p for p in cache_root.iterdir() if p.is_dir()), key=key, reverse=True):
            version = _probe(directory, env, windows=windows, runner=runner)
            if version:
                return _with_path(env, directory if windows else directory / 'bin'), version
    return None



def _same_regular_tree(source: Path, destination: Path) -> bool:
    """Verify fallback-copy bytes, including the official SHA256 provenance note."""
    expected = {p.relative_to(source) for p in source.rglob('*') if p.is_file()}
    actual = {p.relative_to(destination) for p in destination.rglob('*') if p.is_file()}
    if expected != actual:
        return False
    for relative in expected:
        original, copied = source / relative, destination / relative
        if copied.is_symlink() or original.stat().st_size != copied.stat().st_size:
            return False
        digest_a, digest_b = hashlib.sha256(), hashlib.sha256()
        for path, digest in ((original, digest_a), (copied, digest_b)):
            with path.open('rb') as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b''):
                    digest.update(chunk)
        if digest_a.digest() != digest_b.digest():
            return False
    return True


def _publish_verified_node(staged: Path, location: Path, env: dict[str, str],
                           version: str, runner: Callable) -> str:
    """Publish official verified ZIP on Windows, including transient WinError 5.

    On Windows an executable probed from `staged` can remain temporarily held by
    endpoint security. A directory containing it may then refuse MoveFileEx.
    Retry the atomic move, followed by an exclusively claimed and fully verified
    *copy* fallback. Never replace an existing cache or the user's data directory.
    """
    for attempt in range(5):
        if location.exists():
            if _probe(location, env, windows=True, runner=runner) == version:
                return 'concurrent-existing'
            raise NodeToolchainError(
                'Node cache target already exists but cannot be verified: ' + str(location)
                + '. Close Node processes and inspect only this cache directory; '
                  'TimePilot project databases must be kept unchanged.')
        try:
            os.replace(staged, location)
            return 'atomic-rename'
        except (FileExistsError, PermissionError) as exc:
            if location.exists():
                continue  # A concurrent installer may have finished publishing.
            if not isinstance(exc, PermissionError):
                raise
            if attempt < 4:
                print('[NODE 24] Windows temporarily denied folder move; retrying ('
                      + str(attempt + 1) + '/4).', flush=True)
                time.sleep((0.25, 0.5, 1, 2)[attempt])

    # Some Windows endpoint-security products forbid moving a directory whose
    # executable was just launched, while copying files is still permitted.
    print('[NODE 24] Folder move still blocked; trying an exclusive verified copy '
          'inside the same user-local cache.', flush=True)
    try:
        location.mkdir()  # exclusive claim; do not overwrite another installer
    except FileExistsError as exc:
        if _probe(location, env, windows=True, runner=runner) == version:
            return 'concurrent-existing'
        raise NodeToolchainError('Node cache target occupied by another installer: '
                                 + str(location)) from exc
    try:
        shutil.copytree(staged, location, dirs_exist_ok=True)
        if not _same_regular_tree(staged, location):
            raise NodeToolchainError('Node fallback copy did not match the verified archive bytes')
        if _probe(location, env, windows=True, runner=runner) != version:
            raise NodeToolchainError('Copied Node executable did not pass runtime verification')
    except (OSError, NodeToolchainError) as exc:
        try:
            shutil.rmtree(location)
            residue = ''
        except OSError as cleanup_exc:
            residue = ' Incomplete cache could not be cleaned: ' + str(cleanup_exc)
        raise NodeToolchainError(
            'Windows blocked Node directory publication, including verified copy: '
            + str(exc) + '.' + residue + '\n'
            'Workaround: install Node 24 in a user-writable folder and set '
            'TIMEPILOT_NODE_HOME to that folder; then run TIMEPILOT.bat start.') from exc
    return 'verified-copy'


def ensure_node24(env: dict[str, str] | None = None, *, cache_root: Path | None = None,
                  allow_download: bool = False, opener=urllib.request.urlopen,
                  runner: Callable = subprocess.run, windows: bool | None = None,
                  machine: str | None = None) -> tuple[dict[str, str], str, str]:
    """Return (child_env, version, source); never persist global PATH changes."""
    env = dict(env or os.environ)
    windows = _is_windows() if windows is None else windows
    cache = Path(cache_root or Path(env.get('TIMEPILOT_HOME') or Path.home() / '.timepilot') / 'toolchains' / 'node24')
    existing = _find_existing(env, cache, windows=windows, runner=runner)
    if existing:
        result, version = existing
        print('[NODE 24 READY] ' + version + ' (existing installation/cache)', flush=True)
        return result, version, 'existing'
    if _offline(env):
        raise NodeToolchainError('TIMEPILOT_OFFLINE=1 and no cached Node 24/npm is available; use a prebuilt BI bundle')
    if not allow_download or env.get('TIMEPILOT_NODE_AUTO_DOWNLOAD', '1').strip().lower() in {'0', 'false', 'no'}:
        raise NodeToolchainError('Node.js 24/npm not found; automatic local download is disabled')
    if not windows:
        raise NodeToolchainError('Automatic Node download is enabled on Windows only; install/use Node 24 on this platform')

    version, target = _resolve_release(opener=opener, machine=machine,
                                        requested=env.get('TIMEPILOT_NODE_VERSION'))
    folder = 'node-' + version + '-' + target
    cache.mkdir(parents=True, exist_ok=True)
    location = cache / folder
    current = _probe(location, env, windows=windows, runner=runner)
    if current == version:
        return _with_path(env, location), version, 'cached'
    if location.exists():
        raise NodeToolchainError('Existing cached Node folder is damaged; refusing to overwrite: ' + str(location))
    filename = folder + '.zip'
    base_url = DIST + '/' + version
    try:
        sums = _download(base_url + '/SHASUMS256.txt', MAX_SUMS_BYTES, opener=opener).decode('utf-8')
    except UnicodeDecodeError as exc:
        raise NodeToolchainError('Invalid official SHA256 file encoding') from exc
    expected_sha = _official_sha(sums, filename)
    print('[NODE 24] Downloading official ' + filename + ' into user-local cache (first run only)', flush=True)
    with tempfile.TemporaryDirectory(prefix='.node24-', dir=cache) as temp:
        temp_dir = Path(temp)
        archive = temp_dir / filename
        _get_archive(base_url + '/' + filename, archive, expected_sha, opener=opener)
        staged = _extract_verified_zip(archive, temp_dir / 'unpacked', folder)
        if not (staged / 'node.exe').is_file() or not (staged / 'npm.cmd').is_file():
            raise NodeToolchainError('Official ZIP does not contain node.exe and npm.cmd')
        if _probe(staged, env, windows=True, runner=runner) != version:
            raise NodeToolchainError('Downloaded Node executable failed the Node 24 runtime check')
        (staged / '.timepilot-node-sha.json').write_text(json.dumps({
            'version': version, 'archive': filename, 'sha256': expected_sha,
            'origin': base_url + '/' + filename}, indent=2) + '\n', encoding='utf-8')
        publication = _publish_verified_node(staged, location, env, version, runner)
    print('[NODE 24 READY] ' + version + ' (verified local ZIP, '
          + publication + ', no admin rights)', flush=True)
    return _with_path(env, location), version, 'downloaded'
