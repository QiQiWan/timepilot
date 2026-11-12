"""TimePilot offline source diagnostics for the already active Python interpreter.

Startup uses the runtime contract; --source-only and --strict-source enforce the
complete signed release, including dotfiles. No environments are created.
"""
from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from release_integrity import check_tree, check_runtime_tree, project_version


def inspect(*, require_runtime: bool = False, integration: bool = False,
            require_bundle: bool = False, strict_extra: bool = False,
            source_only: bool = False, strict_source: bool = False) -> dict:
    if source_only or strict_source or strict_extra:
        issues, manifest = check_tree(ROOT, detect_extra=strict_extra)
        warnings = []
    else:
        issues, warnings, manifest = check_runtime_tree(ROOT)
    report = {
        'project': str(ROOT), 'manifest_version': manifest.get('version') if manifest else None,
        'issues': issues, 'warnings': warnings,
        'python_executable': sys.executable,
        'python_version': '.'.join(map(str, sys.version_info[:3])),
        'dependencies': {}, 'bundle': None,
    }
    if require_runtime and sys.version_info[:2] != (3, 12):
        issues.append(f'Existing Python 3.12 is required for this release; active: {report["python_version"]}')
    if not issues:
        try:
            version = project_version(ROOT)
            if not (ROOT / 'requirements.txt').is_file():
                warnings.append('RELEASE-ONLY: requirements.txt is absent; do not install packages during startup')
            else:
                from sync_requirements import desired
                if (ROOT / 'requirements.txt').read_text(encoding='utf-8') != desired():
                    if strict_source or source_only:
                        issues.append('MIXED DEPENDENCIES: requirements.txt differs from pyproject.toml')
                    else:
                        warnings.append('RELEASE-ONLY: requirements.txt differs from pyproject.toml')
            api_source = (ROOT / 'src/timepilot/api.py').read_text(encoding='utf-8')
            # The API can reference the package's version dynamically. Requiring
            # a duplicate hard-coded literal makes legitimate incremental releases
            # fail even when FastAPI and the package report the same version.
            api_version_matches = ('version=__version__' in api_source or
                                   f'version="{version}"' in api_source)
            ui_version_matches = ('"ui_version": __version__' in api_source or
                                  f'"ui_version": "{version}"' in api_source)
            if not (api_version_matches and ui_version_matches):
                issues.append('MIXED VERSION: FastAPI advertised version differs from Python package')
            package_file = ROOT / 'frontend/native-bi/package.json'
            if package_file.is_file():
                frontend_version = json.loads(package_file.read_text(encoding='utf-8'))['version']
                if frontend_version != version:
                    issues.append(f'MIXED VERSION: frontend package={frontend_version}, Python={version}')
            elif strict_source or source_only:
                issues.append('MISSING FRONTEND SOURCE: frontend/native-bi/package.json')
            else:
                warnings.append('RELEASE-ONLY: frontend/native-bi/package.json missing (build will be unavailable)')
            index = (ROOT / 'src/timepilot/static/index.html').read_text(encoding='utf-8')
            if f'TimePilot v{version}' not in index or f'/assets/app.js?v={version}' not in index:
                issues.append('MIXED VERSION: HTML/JS cache version does not match Python release')
            if not source_only:
                sys.path.insert(0, str(ROOT / 'src'))
                # This source path takes precedence over a stale pip installation.
                module = importlib.import_module('timepilot')
                origin = Path(module.__file__).resolve()
                expected = (ROOT / 'src/timepilot/__init__.py').resolve()
                if origin != expected:
                    issues.append(f'WRONG IMPORT PATH: {origin} (expected {expected})')
                if getattr(module, '__version__', '') != version:
                    issues.append('MIXED VERSION: imported TimePilot differs from project source')
                from timepilot.sdk import TimePilot
                if not callable(TimePilot):
                    issues.append('Broken SDK: timepilot.sdk.TimePilot is not callable')
        except Exception as exc:
            issues.append(f'Python package import failed: {type(exc).__name__}: {exc}')
    if integration and not issues:
        for mod, package in (('duckdb', 'duckdb'), ('pyarrow', 'pyarrow'),
                             ('perspective', 'perspective-python'), ('playwright', 'playwright')):
            try:
                ver = importlib.metadata.version(package)
                report['dependencies'][mod] = ver
                if mod == 'perspective' and ver != '5.6.1':
                    issues.append(f'perspective-python==5.6.1 is required; found {ver}')
                importlib.import_module(mod)
            except importlib.metadata.PackageNotFoundError:
                report['dependencies'][mod] = None
                issues.append('MISSING DEPENDENCY in current Python: ' + package)
            except Exception as exc:
                issues.append(f'BROKEN DEPENDENCY: {package} ({type(exc).__name__}: {exc})')
    if require_bundle and not issues:
        sys.path.insert(0, str(ROOT / 'src'))
        try:
            from timepilot.native_bi_bundle import bundle_status, frontend_matches_bundle
            target = ROOT / 'src/timepilot/static/native-bi'
            bundle = bundle_status(target)
            report['bundle'] = bundle
            if not bundle['ready']:
                issues.append('Missing/damaged native BI bundle: ' + '; '.join(bundle['problems'][:4]))
            elif not frontend_matches_bundle(target, ROOT / 'frontend/native-bi'):
                issues.append('Native BI JS/WASM build differs from frontend source; rebuild required')
        except Exception as exc:
            issues.append(f'Cannot verify native BI bundle: {type(exc).__name__}: {exc}')
    report['ok'] = not issues
    return report


def main() -> int:
    p = argparse.ArgumentParser(description='Inspect the current TimePilot source and active Python environment')
    p.add_argument('--require-runtime', action='store_true')
    p.add_argument('--integration', action='store_true')
    p.add_argument('--require-bundle', action='store_true')
    p.add_argument('--strict-extra', action='store_true')
    p.add_argument('--strict-source', action='store_true')
    p.add_argument('--source-only', action='store_true')
    p.add_argument('--json', action='store_true')
    args = p.parse_args()
    report = inspect(require_runtime=args.require_runtime, integration=args.integration,
                     require_bundle=args.require_bundle, strict_extra=args.strict_extra,
                     source_only=args.source_only, strict_source=args.strict_source)
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print('[PASS] TimePilot preflight' if report['ok'] else '[FAIL] TimePilot preflight')
        print('Active Python:', report['python_version'], report['python_executable'])
        print('Project:', report['project'], '| Release manifest:', report['manifest_version'] or 'absent')
        for warning in report['warnings'][:15]:
            print((' [INFO] ' if warning.startswith('RELEASE-ONLY:') else ' [WARN] ') + warning)
        if len(report['warnings']) > 15:
            print(' [WARN] Additional release-only diagnostics:', len(report['warnings'])-15)
        for error in report['issues']:
            print(' [ERROR] ' + error)
        if report['issues']:
            print('Repair the source files if necessary; no Python environment is created or changed.')
    return 0 if report['ok'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
