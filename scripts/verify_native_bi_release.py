"""Strict release gate: same Python env, real DuckDB and offline Perspective bundle."""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from timepilot.native_bi_bundle import bundle_status, frontend_matches_bundle


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--require-python31212', action='store_true')
    p.add_argument('--require-duckdb', action='store_true')
    p.add_argument('--require-build', action='store_true')
    p.add_argument('--require-source-current', action='store_true')
    p.add_argument('--require-virtual-server', action='store_true')
    args = p.parse_args()
    status = bundle_status(ROOT / 'src/timepilot/static/native-bi')
    python_ready = sys.version_info[:2] == (3, 12)
    duck = importlib.util.find_spec('duckdb') is not None
    arrow = importlib.util.find_spec('pyarrow') is not None
    perspective = importlib.util.find_spec('perspective') is not None
    virtual_server_ready = False
    virtual_error = None
    if perspective and duck:
        try:
            from perspective.virtual_servers.duckdb import DuckDBVirtualServer
            from perspective.handlers.starlette import PerspectiveStarletteHandler
            virtual_server_ready = callable(DuckDBVirtualServer) and callable(PerspectiveStarletteHandler)
        except Exception as exc:
            virtual_error = f'{type(exc).__name__}: {exc}'
    source_current = frontend_matches_bundle(ROOT / 'src/timepilot/static/native-bi', ROOT / 'frontend/native-bi')
    result = {
        'frontend_build_matches_source': source_current,
        'python_3_12': python_ready, 'duckdb': duck, 'pyarrow': arrow,
        'perspective_python': perspective, 'perspective_duckdb_virtual_server': virtual_server_ready,
        'perspective_virtual_server_error': virtual_error, 'bundle': status,
        'full_browser_integration': 'requires scripts/accept_native_bi_live.py',
    }
    print(json.dumps(result, indent=2, ensure_ascii=False))
    errors = []
    if args.require_python31212 and not python_ready: errors.append('目标 Python 3.12.12 不匹配')
    if args.require_duckdb and not (duck and arrow): errors.append('缺少 DuckDB 或 PyArrow')
    if args.require_build and not status['ready']: errors.append('Perspective 发布资源校验失败')
    if args.require_source_current and not source_current:
        errors.append('Perspective 构建资源与前端源码版本不一致，请重新执行构建')
    if args.require_virtual_server and not (virtual_server_ready and arrow and status['ready']):
        errors.append('Perspective DuckDB Virtual Server 所需组件未就绪')
    if errors:
        for x in errors: print('FAILED: ' + x, file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
