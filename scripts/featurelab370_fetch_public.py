"""Fetch NAB real operational telemetry without third-party download libraries.

The original CSVs and upstream labels are retained verbatim. A pinned content
manifest is written on first download; subsequent downloads refuse hash drift.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
from urllib.request import Request, urlopen

RAW = 'https://raw.githubusercontent.com/numenta/NAB/master/'
FILES = ['labels/combined_windows.json', *[
    f'data/realAWSCloudwatch/ec2_cpu_utilization_{id}.csv' for id in (
        '24ae8d', '53ea38', '5f5533', '77c1ca', '825cc2',
        'ac20cd', 'c6585a', 'fe7f93')]]
MAX_FILE_BYTES = 10 * 1024 * 1024
PIN = Path(__file__).resolve().parents[1] / 'benchmarks' / 'featurelab_v0370' / 'PUBLIC_NAB_PIN.json'


def verify_upstream_pin(relative: str, payload: bytes) -> str:
    """SHA-1 is used only for Git's immutable blob ID; separate SHA256 records bytes."""
    pinned = json.loads(PIN.read_text(encoding='utf-8'))['git_blob_sha1']
    if relative not in pinned:
        raise ValueError('源文件不在锁定的官方版本清单内：' + relative)
    value = hashlib.sha1(b'blob ' + str(len(payload)).encode() + b'\0' + payload).hexdigest()
    if value != pinned[relative]:
        raise ValueError('上游 NAB 文件与审查时锁定的 Git blob 不一致：' + relative)
    return value


def fetch(out: Path, *, timeout: int = 45) -> dict:
    out = Path(out)
    lock = out / 'SOURCE_SHA256.json'
    previous = json.loads(lock.read_text(encoding='utf-8')) if lock.is_file() else None
    if previous is not None and set(previous.get('files', {})) != set(FILES):
        raise ValueError('已存在不匹配的源文件签名清单，不允许静默覆盖')
    hashes = {}
    for relative in FILES:
        dst = out / relative
        dst.parent.mkdir(parents=True, exist_ok=True)
        if not dst.exists():
            req = Request(RAW + relative, headers={'User-Agent': 'TimePilot-IndustrialQA/0.37.0'})
            try:
                with urlopen(req, timeout=timeout) as response:
                    if response.status != 200:
                        raise OSError(f'HTTP {response.status} for {relative}')
                    tmp = dst.with_suffix(dst.suffix + '.part')
                    count = 0
                    h = hashlib.sha256()
                    try:
                        with tmp.open('wb') as stream:
                            while True:
                                block = response.read(64 * 1024)
                                if not block:
                                    break
                                count += len(block)
                                if count > MAX_FILE_BYTES:
                                    raise ValueError('上游文件超过限定大小：' + relative)
                                h.update(block)
                                stream.write(block)
                        tmp.replace(dst)
                    finally:
                        tmp.unlink(missing_ok=True)
            except Exception:
                # An interrupted download cannot leave an accepted file.
                raise
        payload = dst.read_bytes()
        verify_upstream_pin(relative, payload)
        h = hashlib.sha256(payload).hexdigest()
        if previous is not None and previous['files'].get(relative) != h:
            raise ValueError('真实数据源 SHA256 与锁定来源不同：' + relative)
        hashes[relative] = h
    info = {'source': 'Numenta/NAB master; official original bytes',
            'source_url': RAW, 'version': '0.37.0', 'files': hashes,
            'note': 'The branch is mutable. First download records exact per-file SHA256; future runs fail on drift.'}
    if previous is None:
        lock.write_text(json.dumps(info, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    return {'status': 'passed', 'files': len(hashes), 'source_lock': str(lock),
            'source_sha256': hashlib.sha256(lock.read_bytes()).hexdigest()}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, default=Path('data/public/nab'))
    parser.add_argument('--timeout', type=int, default=45)
    a = parser.parse_args()
    try:
        result = fetch(a.out, timeout=a.timeout)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (OSError, ValueError) as exc:
        print(json.dumps({'status': 'blocked', 'reason': str(exc)}, ensure_ascii=False, indent=2))
        raise SystemExit(2) from exc
