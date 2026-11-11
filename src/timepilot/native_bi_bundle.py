"""Fail-closed validation of offline Perspective JS/CSS/WASM release assets.

No remote CDN is ever used. A Vite build is not considered deployable until a
local manifest with SHA256 hashes for all required artifacts has been generated.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

PERSPECTIVE_VERSION = '5.6.1'
BUILD_MANIFEST = 'perspective-build.json'
CONTRACT_FILE = 'perspective-contract.json'
PUBLIC_EXPORTS = (
    'mountPerspective', 'mountPerspectiveRemote', 'getActiveTableName',
    'setPerspectivePreset', 'savePerspectiveState', 'restorePerspectiveState',
    'disposePerspective',
)
REQUIRED_FILES = ('perspective-entry.js', 'perspective-entry.css', CONTRACT_FILE)
ASSET_SUFFIXES = frozenset({'.js', '.css', '.wasm', '.woff', '.woff2', '.ttf', '.png', '.svg'})


def validate_public_api_contract(root: Path) -> list[str]:
    """Check the *compiled Rollup entry* contract emitted by the build plugin.

    Source exports alone are insufficient: Vite application builds can drop
    every export even though the JS/CSS/WASM files pass their SHA checks.
    """
    root = Path(root)
    try:
        contract = json.loads((root / CONTRACT_FILE).read_text(encoding='utf-8'))
        if not isinstance(contract, dict) or contract.get('format') != 1:
            return ['Perspective ESM 导出契约格式错误']
        if contract.get('entry') != 'perspective-entry.js':
            return ['Perspective ESM 契约指定了错误的入口文件']
        actual = contract.get('exports')
        if not isinstance(actual, list) or len(actual) != len(set(str(x) for x in actual)):
            return ['Perspective ESM 导出契约缺少合法的函数列表']
        missing = set(PUBLIC_EXPORTS) - set(str(x) for x in actual)
        if missing:
            return ['Perspective 编译模块缺少导出：' + ', '.join(sorted(missing))]
        # Release manifests are generated from Vite's output chunk.exports.
        # This secondary text check catches obviously inconsistent artifacts.
        source = (root / 'perspective-entry.js').read_text(encoding='utf-8')
        absent = [name for name in PUBLIC_EXPORTS if not re.search(r'\b' + re.escape(name) + r'\b', source)]
        if absent:
            return ['JS 文件中缺少已声明的 API 名称：' + ', '.join(absent)]
        return []
    except (OSError, ValueError, TypeError) as exc:
        return [f'无法确认 Perspective ESM 模块导出：{exc}']


def valid_wasm_header(path: Path) -> bool:
    with path.open('rb') as stream:
        return stream.read(4) == b'\x00asm'


def digest(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            sha.update(chunk)
    return sha.hexdigest()


def frontend_source_hash(frontend: Path) -> str:
    """Fingerprint exactly the frontend inputs that affect the production bundle.

    No Node or npm invocation is required; a normal runtime install can ship
    precompiled JS/CSS/WASM and omit the frontend sources entirely.
    """
    frontend = Path(frontend)
    files = [frontend / name for name in ('package.json', 'package-lock.json', 'vite.config.mjs')]
    source = frontend / 'src'
    if source.is_dir():
        files.extend(x for x in source.rglob('*') if x.is_file() and x.suffix in {'.js', '.mjs', '.ts'})
    sha = hashlib.sha256()
    found = False
    for path in sorted((p for p in files if p.is_file()), key=lambda p: p.relative_to(frontend).as_posix()):
        found = True
        sha.update(path.relative_to(frontend).as_posix().encode('utf-8'))
        sha.update(bytes.fromhex(digest(path)))
    return sha.hexdigest() if found else ''


def frontend_matches_bundle(bundle: Path, frontend: Path) -> bool:
    """Check release provenance; legacy bundles without source hashes rebuild once."""
    try:
        payload = json.loads((Path(bundle) / BUILD_MANIFEST).read_text(encoding='utf-8'))
        source_sha = frontend_source_hash(frontend)
        return bool(source_sha and source_sha == payload.get('frontend_source_sha256'))
    except (OSError, ValueError, TypeError):
        return False


def generate_manifest(root: Path, *, frontend_sha256: str | None = None) -> dict:
    root = Path(root)
    resources = sorted(p for p in root.rglob('*') if p.is_file() and p.suffix.lower() in ASSET_SUFFIXES)
    missing = [f for f in REQUIRED_FILES if not (root / f).is_file()]
    if not missing:
        problems = validate_public_api_contract(root)
        if problems:
            raise ValueError('；'.join(problems))
        resources.append(root / CONTRACT_FILE)
        resources = sorted(set(resources))
    wasm = [path for path in resources if path.suffix == '.wasm']
    wasm_names = {path.name for path in wasm}
    required_wasm = ('perspective-server', 'perspective-viewer')
    missing_wasm = [name for name in required_wasm if not any(p.startswith(name) for p in wasm_names)]
    if missing or missing_wasm or len(wasm) < 2 or any(not x.is_file() or x.stat().st_size == 0 for x in resources):
        raise ValueError('Perspective 构建不完整：需要 JS、CSS 和至少两个非空 WASM 文件')
    if any(not valid_wasm_header(p) for p in wasm):
        raise ValueError('WASM 文件头无效，构建可能损坏')
    entries = {p.relative_to(root).as_posix(): {'sha256': digest(p), 'bytes': p.stat().st_size}
               for p in resources}
    result = {'perspective_version': PERSPECTIVE_VERSION, 'files': entries}
    if frontend_sha256:
        result['frontend_source_sha256'] = frontend_sha256
    (root / BUILD_MANIFEST).write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return result


def bundle_status(root: Path) -> dict:
    root = Path(root)
    reasons = []
    path = root / BUILD_MANIFEST
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
        if payload.get('perspective_version') != PERSPECTIVE_VERSION:
            reasons.append('Perspective 版本不匹配')
        entries = payload.get('files', {})
        if not isinstance(entries, dict):
            raise ValueError('文件清单格式无效')
        for filename in REQUIRED_FILES:
            if filename not in entries:
                reasons.append(f'缺少 {filename}')
        reasons.extend(validate_public_api_contract(root))
        wasm_names = [name for name in entries if name.endswith('.wasm')]
        if len(wasm_names) < 2:
            reasons.append('WASM 构建产物不足两个')
        for stem in ('perspective-server', 'perspective-viewer'):
            if not any(Path(name).name.startswith(stem) and name.endswith('.wasm') for name in wasm_names):
                reasons.append(f'缺少 {stem}.wasm 浏览器资源')
        untracked = {p.relative_to(root).as_posix() for p in root.rglob('*')
                     if p.is_file() and p.suffix.lower() in ASSET_SUFFIXES} - set(entries)
        for name in sorted(untracked):
            reasons.append(f'未登记的构建资源：{name}')
        for name, meta in entries.items():
            dest = (root / name).resolve()
            if not dest.is_relative_to(root.resolve()):
                reasons.append(f'资源路径越界：{name}')
                continue
            if not dest.is_file() or dest.stat().st_size != meta.get('bytes'):
                reasons.append(f'缺失或长度不一致：{name}')
            elif name.endswith('.wasm') and not valid_wasm_header(dest):
                reasons.append(f'WASM 文件头无效：{name}')
            elif digest(dest) != meta.get('sha256'):
                reasons.append(f'校验和不一致：{name}')
    except (OSError, ValueError, KeyError, TypeError) as exc:
        reasons.append(f'构建清单无效或不存在：{exc}')
    return {'ready': not reasons, 'version': PERSPECTIVE_VERSION, 'problems': reasons}
