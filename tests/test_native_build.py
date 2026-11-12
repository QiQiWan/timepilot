"""Regression tests for Vite WASM emission and transactional publishing."""
from __future__ import annotations

import importlib.util
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_builder():
    path = ROOT / 'scripts/build_native_bi.py'
    spec = importlib.util.spec_from_file_location('tp_native_builder', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_vite_emits_separate_offline_wasm():
    config = (ROOT / 'frontend/native-bi/vite.config.mjs').read_text(encoding='utf-8')
    entry = (ROOT / 'frontend/native-bi/src/main.js').read_text(encoding='utf-8')
    assert 'assetsInlineLimit: 0' in config
    assert 'rollupOptions' in config
    assert "preserveEntrySignatures: 'strict'" in config
    assert 'perspectiveExportContractPlugin()' in config
    assert 'build.lib' not in config and 'lib:' not in config
    assert "base: '/assets/native-bi/'" in config
    assert '.wasm?url' in entry
    assert (ROOT / 'frontend/native-bi/public/README.txt').is_file()


def setup_mock(tmp_path, monkeypatch, succeed):
    builder = load_builder()
    root = tmp_path / 'project'
    output = root / 'src/timepilot/static/native-bi'
    output.mkdir(parents=True)
    (output / 'old-file.txt').write_text('previous usable release', encoding='utf-8')
    frontend = root / 'frontend/native-bi'
    (frontend / 'node_modules').mkdir(parents=True)
    (frontend / 'node_modules/.package-lock.json').write_text('{}')
    (frontend / 'package.json').write_text('{"dependencies":{},"devDependencies":{}}')
    (frontend / 'package-lock.json').write_text('{"name":"test-build","lockfileVersion":3,"packages":{}}')
    monkeypatch.setattr(builder, 'ROOT', root)
    monkeypatch.setattr(builder, 'FRONTEND', frontend)
    monkeypatch.setattr(builder, 'OUTPUT', output)
    monkeypatch.setattr(builder.shutil, 'which', lambda command: '/usr/bin/npm')

    def run(args, **kwargs):
        if args == ['npm', 'ls', '--depth=0', '--json']:
            return subprocess.CompletedProcess(args, 0, stdout='{"dependencies":{}}', stderr='')
        assert args == ['npm', 'run', 'build']
        staging = Path(kwargs['env']['TIMEPILOT_NATIVE_BI_OUTDIR'])
        assert staging.parent == root
        if not succeed:
            raise subprocess.CalledProcessError(7, args)
        (staging / 'assets').mkdir(exist_ok=True)
        (staging / 'README.txt').write_text('source placeholder')
        from timepilot.native_bi_bundle import PUBLIC_EXPORTS
        (staging / 'perspective-entry.js').write_text('\n'.join(
            f'export function {name}(){{}}' for name in PUBLIC_EXPORTS))
        (staging / 'perspective-contract.json').write_text(__import__('json').dumps({
            'format': 1, 'entry': 'perspective-entry.js', 'exports': list(PUBLIC_EXPORTS)}))
        (staging / 'perspective-entry.css').write_text('perspective-viewer{}')
        (staging / 'assets/perspective-server-test.wasm').write_bytes(b'\x00asm123456')
        (staging / 'assets/perspective-viewer-test.wasm').write_bytes(b'\x00asm123456')
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(builder.subprocess, 'run', run)
    return builder, output


def test_failed_build_keeps_last_good_static_bundle(tmp_path, monkeypatch):
    builder, output = setup_mock(tmp_path, monkeypatch, succeed=False)
    assert builder.main() == 1
    assert (output / 'old-file.txt').read_text() == 'previous usable release'
    assert not list(builder.ROOT.glob('.native-bi-staging-*'))


def test_successful_build_replaces_bundle_after_verification(tmp_path, monkeypatch):
    builder, output = setup_mock(tmp_path, monkeypatch, succeed=True)
    assert builder.main() == 0
    assert not (output / 'old-file.txt').exists()
    assert (output / 'README.txt').is_file()
    from timepilot.native_bi_bundle import bundle_status
    assert bundle_status(output)['ready']
    assert not list(builder.ROOT.glob('.native-bi-staging-*'))
    assert not (output.parent / '.native-bi-previous').exists()
