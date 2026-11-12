"""Package a complete source release or validated JS/WASM offline runtime release.

No npm installation occurs here; prebuilt native assets must pass the release
contract before they are embedded. The source-only bundle contains basic BI.
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


def main() -> int:
    parser = argparse.ArgumentParser(description='Create a complete SHA256-verified source archive')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--source-only', action='store_true', help='Full source with basic BI (no bundled Perspective)')
    parser.add_argument('--with-native-bundle', action='store_true', help='Require and embed verified Perspective JS/CSS/WASM')
    args = parser.parse_args()
    version = project_version(ROOT)
    output = (args.output or ROOT.parent / f'TimePilot_v{version}_fixed_source.zip').expanduser().resolve()
    manifest = build_manifest(ROOT)
    native_files = {}
    if args.with_native_bundle:
        from sys import path as sys_path
        sys_path.insert(0, str(ROOT / 'src'))
        from timepilot.native_bi_bundle import bundle_status, frontend_matches_bundle
        bundle = ROOT / 'src/timepilot/static/native-bi'
        status = bundle_status(bundle)
        lock = ROOT / 'frontend/native-bi/package-lock.json'
        if not lock.is_file():
            raise ValueError('离线运行包缺少 npm 依赖锁文件；先在联网构建机器生成并审核 package-lock.json，使用 npm ci 构建')
        if not status['ready'] or not frontend_matches_bundle(bundle, ROOT / 'frontend/native-bi'):
            raise ValueError('离线 JS/WASM 未通过完整性与源码匹配检查：' + '; '.join(status['problems'][:5]))
        native_files = {p.relative_to(ROOT).as_posix(): {
            'sha256': hashlib.sha256(p.read_bytes()).hexdigest(), 'bytes':p.stat().st_size}
            for p in bundle.rglob('*') if p.is_file()}
        if len(native_files) < 7:
            raise ValueError('编译资源不完整，拒绝发布')
        manifest['native_bi_release_status'] = 'compiled-offline-verified'
        manifest['runtime_assets'] = native_files
    encoded = (json.dumps(manifest, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    prefix = f'TimePilot_v{version}/'
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.timepilot-staging-', suffix='.zip', dir=output.parent)
    os.close(fd)
    try:
        with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=8) as zipped:
            for name in sorted(manifest['files']):
                zipped.write(ROOT / name, prefix + name)
            for name in sorted(native_files):
                zipped.write(ROOT / name, prefix + name)
            zipped.writestr(prefix + MANIFEST_NAME, encoded)
        with zipfile.ZipFile(temporary, 'r') as zipped:
            if zipped.testzip() is not None:
                raise ValueError('Corrupted ZIP member found')
            if len(zipped.namelist()) != len(manifest['files']) + len(native_files) + 1:
                raise ValueError('Unexpected ZIP member count')
            if zipped.read(prefix + MANIFEST_NAME) != encoded:
                raise ValueError('Release manifest differs from ZIP data')
            for name, meta in {**manifest['files'], **native_files}.items():
                contents = zipped.read(prefix + name)
                if len(contents) != meta['bytes'] or hashlib.sha256(contents).hexdigest() != meta['sha256']:
                    raise ValueError('ZIP release SHA256 mismatch: ' + name)
        os.replace(temporary, output)
        os.chmod(output, 0o644)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    print(json.dumps({'archive': str(output), 'version': version,
                      'file_count': len(manifest['files']), 'source_only': not args.with_native_bundle, 'embedded_native_files':len(native_files),
                      'bytes': output.stat().st_size,
                      'sha256': hashlib.sha256(output.read_bytes()).hexdigest()}, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
