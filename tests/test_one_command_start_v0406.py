"""v0.40.6: one-command startup, Node bootstrap and safety regressions."""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from node_toolchain import (NodeToolchainError, _extract_verified_zip,
                            ensure_node24)


def _load_launcher():
    spec = importlib.util.spec_from_file_location('_tp_one_start_launcher', ROOT / 'scripts/launcher.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_default_windows_start_bootstraps_native_bi(monkeypatch, tmp_path):
    launcher = _load_launcher()
    env = {'PATH': os.environ.get('PATH', ''), 'TIMEPILOT_HOME': str(tmp_path),
           'TIMEPILOT_DATA_HOME_ORIGIN': 'test'}
    commands = []
    monkeypatch.setattr(launcher, 'source_env', lambda: env)
    monkeypatch.setattr(launcher, 'run', lambda *args, **kw: commands.append(args))
    monkeypatch.setattr(sys, 'argv', ['TIMEPILOT.bat', 'start', '--port', '8046'])
    assert launcher.main() == 0
    bi = [cmd for cmd in commands if 'scripts/ensure_native_bi.py' in cmd]
    assert len(bi) == 1
    assert {'--auto-build', '--auto-bootstrap', '--auto-node'} <= set(bi[0])
    assert '--status-only' not in bi[0]
    assert commands[-1][-2:] == ('--port', '8046')
    assert not (tmp_path / 'metadata.sqlite3').exists()  # mock never created data


def test_operator_may_disable_bootstrap(monkeypatch, tmp_path):
    launcher = _load_launcher()
    env = {'TIMEPILOT_HOME': str(tmp_path), 'TIMEPILOT_DATA_HOME_ORIGIN': 'test',
           'TIMEPILOT_AUTO_BUILD_BI': '0'}
    commands = []
    monkeypatch.setattr(launcher, 'source_env', lambda: env)
    monkeypatch.setattr(launcher, 'run', lambda *args, **kw: commands.append(args))
    monkeypatch.setattr(sys, 'argv', ['TIMEPILOT.bat', 'start'])
    assert launcher.main() == 0
    bi = [cmd for cmd in commands if 'scripts/ensure_native_bi.py' in cmd]
    assert len(bi) == 1 and '--status-only' in bi[0]


def test_bi_setup_uses_the_same_node_resolver(monkeypatch, tmp_path):
    launcher = _load_launcher()
    env = {'TIMEPILOT_HOME': str(tmp_path), 'TIMEPILOT_DATA_HOME_ORIGIN': 'test'}
    commands = []
    monkeypatch.setattr(launcher, 'source_env', lambda: env)
    monkeypatch.setattr(launcher, 'run', lambda *args, **kw: commands.append(args))
    monkeypatch.setattr(sys, 'argv', ['TIMEPILOT.bat', 'bi-setup', '--bootstrap-lock'])
    assert launcher.main() == 0
    assert commands[0][-2:] == ('--auto-node', '--bootstrap-lock')


def _runner(args, **kwargs):
    exe = str(args[0])
    if exe.endswith('node.exe'):
        return subprocess.CompletedProcess(args, 0, stdout='v24.7.1\n', stderr='')
    return subprocess.CompletedProcess(args, 0, stdout='v22.0.0\n', stderr='')


def _fixture_download(fake_zip: bytes, *, tamper_digest=False):
    archive_name = 'node-v24.7.1-win-x64.zip'
    digest = hashlib.sha256(fake_zip).hexdigest()
    if tamper_digest:
        digest = '0' * 64
    sources = {
        'https://nodejs.org/dist/index.json': json.dumps([
            {'version': 'v24.7.1', 'lts': 'Krypton', 'files': ['win-x64-zip']},
        ]).encode(),
        'https://nodejs.org/dist/v24.7.1/SHASUMS256.txt':
            (digest + '  ' + archive_name + '\n').encode(),
        'https://nodejs.org/dist/v24.7.1/' + archive_name: fake_zip,
    }
    class Response(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *_): self.close()
    def opener(url: str, timeout: int):
        return Response(sources[url])
    return opener


def _fake_zip(*, traversal=False) -> bytes:
    data = io.BytesIO()
    folder = 'node-v24.7.1-win-x64'
    with zipfile.ZipFile(data, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(folder + '/node.exe', 'valid-executable-stub')
        zf.writestr(folder + '/npm.cmd', 'echo fake-npm')
        if traversal:
            zf.writestr(folder + '/../../escape.txt', 'escape')
    return data.getvalue()


def test_first_bootstrap_verifies_official_sha_and_reuses_cached_node(monkeypatch, tmp_path):
    monkeypatch.setattr(shutil, 'which', lambda name, path=None: None)
    cache = tmp_path / 'node-toolchain'
    env = {'PATH': '', 'TIMEPILOT_HOME': str(tmp_path)}
    opener = _fixture_download(_fake_zip())
    updated, version, source = ensure_node24(env, cache_root=cache, allow_download=True,
                                             opener=opener, runner=_runner,
                                             windows=True, machine='AMD64')
    assert (version, source) == ('v24.7.1', 'downloaded')
    assert Path(updated['PATH'].split(os.pathsep)[0], 'npm.cmd').exists()
    assert not (tmp_path / 'metadata.sqlite3').exists()
    def forbidden(*_args, **_kwargs):
        raise AssertionError('Valid cache must not contact nodejs.org')
    updated2, version2, source2 = ensure_node24(env, cache_root=cache, allow_download=True,
                                               opener=forbidden, runner=_runner, windows=True)
    assert version2 == version and source2 == 'existing'
    assert updated2['PATH'] == updated['PATH']


def test_bad_checksum_refuses_install(monkeypatch, tmp_path):
    monkeypatch.setattr(shutil, 'which', lambda name, path=None: None)
    cache = tmp_path / 'toolchain'
    with pytest.raises(NodeToolchainError, match='SHA256 mismatch'):
        ensure_node24({'PATH': ''}, cache_root=cache, allow_download=True,
                      opener=_fixture_download(_fake_zip(), tamper_digest=True),
                      runner=_runner, windows=True, machine='AMD64')
    assert not list(cache.glob('node-v24*'))


def test_zip_traversal_refused(monkeypatch, tmp_path):
    monkeypatch.setattr(shutil, 'which', lambda name, path=None: None)
    cache = tmp_path / 'toolchain'
    with pytest.raises(NodeToolchainError, match='Unsafe path'):
        ensure_node24({'PATH': ''}, cache_root=cache, allow_download=True,
                      opener=_fixture_download(_fake_zip(traversal=True)),
                      runner=_runner, windows=True, machine='AMD64')
    assert not (tmp_path / 'escape.txt').exists()
    assert not list(cache.glob('node-v24*'))


def test_offline_never_downloads(monkeypatch, tmp_path):
    monkeypatch.setattr(shutil, 'which', lambda name, path=None: None)
    def forbidden(*a, **kw):
        raise AssertionError('Offline must never download')
    with pytest.raises(NodeToolchainError, match='OFFLINE'):
        ensure_node24({'PATH': '', 'TIMEPILOT_OFFLINE': '1'}, cache_root=tmp_path,
                      allow_download=True, opener=forbidden, runner=_runner, windows=True)


def test_explicit_home_is_respected_and_not_overwritten(monkeypatch, tmp_path):
    monkeypatch.setattr(shutil, 'which', lambda name, path=None: None)
    home = tmp_path / 'given-node'
    home.mkdir()
    (home / 'node.exe').write_bytes(b'node')
    (home / 'npm.cmd').write_text('npm')
    updated, version, source = ensure_node24({'PATH': '', 'TIMEPILOT_NODE_HOME': str(home)},
                                              cache_root=tmp_path / 'unused', allow_download=False,
                                              runner=_runner, windows=True)
    assert version == 'v24.7.1' and source == 'existing'
    assert updated['PATH'].split(os.pathsep)[0] == str(home)


def test_node_22_is_not_accepted(monkeypatch, tmp_path):
    monkeypatch.setattr(shutil, 'which', lambda name, path=None: str(tmp_path / name))
    with pytest.raises(NodeToolchainError, match='disabled'):
        ensure_node24({'PATH': ''}, cache_root=tmp_path / 'new', allow_download=False,
                      runner=_runner, windows=True)


def test_ensure_bi_dispatches_automatic_node_bootstrap(monkeypatch, tmp_path):
    import contextlib
    import ensure_native_bi as bi
    import timepilot.build_lock as lock

    answers = iter([
        ({'ready': False, 'problems': ['missing']}, False, [], False),
        ({'ready': False, 'problems': ['missing']}, False, [], False),
        ({'ready': True, 'problems': []}, True, [], True),
    ])
    monkeypatch.setattr(bi, 'readiness', lambda: next(answers))
    monkeypatch.setattr(bi, 'ROOT', tmp_path)
    monkeypatch.setattr(bi, 'FRONTEND', tmp_path / 'frontend')
    monkeypatch.setattr(bi, 'BUNDLE', tmp_path / 'assets')
    monkeypatch.setattr(lock, 'exclusive_build_lock', lambda path: contextlib.nullcontext())
    captured = []
    def fake_run(command, **kwargs):
        captured.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0)
    monkeypatch.setattr(bi.subprocess, 'run', fake_run)
    monkeypatch.setattr(sys, 'argv', ['ensure_native_bi.py', '--auto-build', '--auto-bootstrap', '--auto-node'])
    assert bi.main() == 0
    assert len(captured) == 1
    command, kwargs = captured[0]
    assert '--auto-node' in command and '--bootstrap-lock' in command
    assert 'TIMEPILOT_SKIP_NPM_INSTALL' not in kwargs['env']


def test_existing_signed_bi_does_not_attempt_node_or_build(monkeypatch):
    import ensure_native_bi as bi
    monkeypatch.setattr(bi, 'readiness', lambda: ({'ready': True}, True, [], True))
    monkeypatch.setattr(bi.subprocess, 'run', lambda *a, **kw: pytest.fail('no startup build permitted'))
    monkeypatch.setattr(sys, 'argv', ['ensure_native_bi.py', '--auto-build', '--auto-bootstrap', '--auto-node'])
    assert bi.main() == 0


def test_windows_npm_uses_node_cli_not_implicit_cmd(monkeypatch, tmp_path):
    import npm_runtime as nr
    node = tmp_path / 'node.exe'
    node.write_bytes(b'node')
    cli = tmp_path / 'node_modules/npm/bin/npm-cli.js'
    cli.parent.mkdir(parents=True)
    cli.write_text('/* npm */', encoding='utf-8')
    monkeypatch.setattr(nr.shutil, 'which', lambda name, path=None: str(node) if name == 'node' else None)
    command = nr.npm_argv('ci', '--no-audit', windows=True)
    assert command == [str(node.resolve()), str(cli.resolve()), 'ci', '--no-audit']
    assert '.cmd' not in command[0]


def test_windows_npm_cmd_fallback_when_node_cli_unavailable(monkeypatch, tmp_path):
    import npm_runtime as nr
    node = tmp_path / 'node.exe'
    npm = tmp_path / 'npm.cmd'
    node.write_bytes(b'node')
    npm.write_text('rem npm')
    monkeypatch.setattr(nr.shutil, 'which', lambda name, path=None: str(node if name == 'node' else npm))
    assert nr.npm_argv('--version', windows=True) == [str(npm), '--version']


def _fake_node_stage(path: Path) -> Path:
    path.mkdir(parents=True)
    (path / 'node.exe').write_bytes(b'node-executable-stub')
    (path / 'npm.cmd').write_text('fake npm script', encoding='utf-8')
    (path / '.timepilot-node-sha.json').write_text('{"version": "v24.7.1"}', encoding='utf-8')
    return path


def test_windows_transient_winerror5_retries_directory_publish(monkeypatch, tmp_path):
    import node_toolchain as node
    staged = _fake_node_stage(tmp_path / 'unpacked' / 'node-v24.7.1-win-x64')
    destination = tmp_path / 'node-v24.7.1-win-x64'
    real_replace = os.replace
    calls = []

    def transient(source, target):
        calls.append((source, target))
        if len(calls) <= 2:
            raise PermissionError(13, 'simulated Windows antivirus executable hold')
        return real_replace(source, target)

    monkeypatch.setattr(node.os, 'replace', transient)
    monkeypatch.setattr(node.time, 'sleep', lambda *_: None)
    mode = node._publish_verified_node(staged, destination, {}, 'v24.7.1', _runner)
    assert mode == 'atomic-rename'
    assert len(calls) == 3
    assert (destination / 'node.exe').read_bytes() == b'node-executable-stub'
    assert not staged.exists()


def test_windows_persistent_winerror5_falls_back_to_verified_copy(monkeypatch, tmp_path):
    import node_toolchain as node
    staged = _fake_node_stage(tmp_path / 'unpacked' / 'node-v24.7.1-win-x64')
    destination = tmp_path / 'node-v24.7.1-win-x64'

    def denied(*_):
        raise PermissionError(13, 'simulated Windows denied rename')

    monkeypatch.setattr(node.os, 'replace', denied)
    monkeypatch.setattr(node.time, 'sleep', lambda *_: None)
    assert node._publish_verified_node(staged, destination, {}, 'v24.7.1', _runner) == 'verified-copy'
    assert node._same_regular_tree(staged, destination)
    assert (destination / '.timepilot-node-sha.json').exists()


def test_windows_copy_fallback_refuses_bytes_mismatch(monkeypatch, tmp_path):
    import node_toolchain as node
    staged = _fake_node_stage(tmp_path / 'unpacked' / 'node-v24.7.1-win-x64')
    destination = tmp_path / 'node-v24.7.1-win-x64'
    monkeypatch.setattr(node.os, 'replace', lambda *_: (_ for _ in ()).throw(PermissionError(13, 'deny')))
    monkeypatch.setattr(node.time, 'sleep', lambda *_: None)
    original_copy = node.shutil.copytree

    def corrupt(source, target, **kwargs):
        result = original_copy(source, target, **kwargs)
        (target / 'node.exe').write_bytes(b'altered')
        return result

    monkeypatch.setattr(node.shutil, 'copytree', corrupt)
    with pytest.raises(NodeToolchainError, match='did not match'):
        node._publish_verified_node(staged, destination, {}, 'v24.7.1', _runner)
    assert not destination.exists()


def test_windows_existing_usable_cache_is_never_overwritten(tmp_path):
    from node_toolchain import _publish_verified_node
    staged = _fake_node_stage(tmp_path / 'staging' / 'node-v24.7.1-win-x64')
    destination = _fake_node_stage(tmp_path / 'node-v24.7.1-win-x64')
    assert _publish_verified_node(staged, destination, {}, 'v24.7.1', _runner) == 'concurrent-existing'
    assert staged.exists() and destination.exists()
