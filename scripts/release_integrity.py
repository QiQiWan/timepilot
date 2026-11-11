"""TimePilot source release contract (stdlib only).

Every distributed source file is signed by RELEASE_MANIFEST.json.  The native
Perspective WASM build is managed by a separate, independently hashed bundle.
Generated assets and user data are deliberately outside the source contract.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tomllib
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_NAME = 'docs/RELEASE_MANIFEST.json'
FORMAT = 2
SKIP_DIRS = {
    '.git', '.pytest_cache', '.venv', '__pycache__', 'node_modules',
    'runtime', 'dist', 'build', '.mypy_cache', '.ruff_cache', '.tox',
    '.timepilot-backup', '.native-bi-previous', '.vscode', '.idea',
    'reports', 'screenshots', 'native_bi_evidence', 'ui_evidence',
    '.coverage', 'logs',
}
SKIP_FILES = {
    '.env', '.DS_Store', 'RELEASE_MANIFEST.json',
    'SHA256SUMS.txt', 'VERIFICATION_RESULT.json', '.coverage',
}
SKIP_SUFFIXES = {'.pyc', '.png', '.jpg', '.jpeg', '.webp', '.gif', '.log', '.zip', '.tmp'}
REQUIRED = (
    'src/timepilot/enterprise_security.py', 'src/timepilot/physical_store.py',
    'src/timepilot/industrial_bi.py',
    'src/timepilot/static/login.html', 'src/timepilot/static/identity.html',
    'src/timepilot/static/security-client.js', 'src/timepilot/static/identity.js',
    'scripts/manage_identity.py', 'scripts/dedup_native_bi.py',
    'scripts/accept_enterprise_browser.py', 'scripts/accept_production_recovery.py',
    'scripts/benchmark_native_bi.py', 'scripts/system_chrome.py',
    'tests/test_enterprise_security_v021.py', 'tests/test_physical_store_v021.py',
    'tests/test_industrial_bi_v021.py', 'tests/test_industrial_duckdb_v021.py',
    'docs/ENTERPRISE_BI_V0210.md',
    # v0.22.1 universal source scanner, region directory and one-click batch
    'src/timepilot/table_region_scanner.py',
    'src/timepilot/project_region_catalog.py',
    'src/timepilot/import_tool_registry.py',
    'src/timepilot/import_semantics.py',
    'src/timepilot/project_publish_batches.py',
    'tests/test_universal_import_v0221.py',
    'scripts/accept_universal_import_v0221.py',
    'docs/UNIVERSAL_IMPORT_BATCH_V0221.md',

    '.dockerignore', '.env.example', '.gitignore', '.github/workflows/ci.yml',
    '.python-version', 'pyproject.toml', 'requirements.txt',
    'src/timepilot/__init__.py', 'src/timepilot/sdk.py',
    'src/timepilot/api.py', 'src/timepilot/cli.py',
    'src/timepilot/native_bi.py', 'src/timepilot/native_bi_api.py',
    'src/timepilot/native_bi_bundle.py', 'src/timepilot/static/app.js',
    'src/timepilot/static/native-bi.js',
    'src/timepilot/modeling_workflow.py', 'src/timepilot/modeling_api.py',
    'src/timepilot/modeling_adapters.py', 'src/timepilot/industrial_plugins.py',
    'src/timepilot/static/modeling-ui.js', 'src/timepilot/static/modeling-ui.css',
    'frontend/native-bi/package.json', 'frontend/native-bi/src/main.js',
    'src/timepilot/enterprise_security.py', 'src/timepilot/physical_store.py',
    'src/timepilot/industrial_bi.py', 'src/timepilot/static/security-client.js',
    'src/timepilot/static/identity.js', 'src/timepilot/static/login.html',
    'src/timepilot/static/identity.html',
    'src/timepilot/data_schema.py', 'src/timepilot/generic_assets.py',
    'src/timepilot/data_platform.py', 'src/timepilot/project_publication.py',
    'src/timepilot/generic_publish_checkpoint.py', 'src/timepilot/build_lock.py',
    'src/timepilot/runtime_backup.py',
    'scripts/preflight_schema.py', 'scripts/backup_runtime.py',
    'docs/DATA_PLATFORM_UNIFICATION_V0201.md',
    'tests/test_data_governance_v020.py', 'tests/test_project_publication_jobs_v020.py',
    'docs/UNIVERSAL_DATA_WORKFLOW_V0160.md', 'tests/test_generic_data_workflow_v016.py',
    'docs/DEVICE_TEMPERATURE_MODELING_V0170.md',
    'docs/INDUSTRIAL_MODELING_V0180.md', 'tests/test_industrial_modeling_v018.py',
    'tests/test_modeling_workflow_v017.py', 'tests/test_modeling_browser_v017.py',
    'src/timepilot/smart_import.py', 'src/timepilot/smart_import_api.py',
    'src/timepilot/static/smart-import-ui.js', 'src/timepilot/static/smart-import-ui.css',
    'src/timepilot/smart_workbench.py', 'src/timepilot/smart_workbench_api.py',
    'src/timepilot/static/smart-workbench-ui.js', 'src/timepilot/static/smart-workbench-ui.css',
    'scripts/accept_workbench_browser.py', 'tests/test_smart_workbench_v070.py',
    'src/timepilot/data_understanding.py', 'src/timepilot/data_understanding_api.py',
    'scripts/accept_data_agent_browser.py', 'tests/test_data_understanding_v080.py',
    'docs/DATA_AGENT_V080.md',
    'docs/P0_UNIFIED_ASSETS_V090.md',
    'src/timepilot/data_assets.py', 'src/timepilot/data_assets_api.py',
    'src/timepilot/static/data-assets-ui.js', 'tests/test_p0_data_assets_v090.py',
    'scripts/accept_native_bi_live.py', 'scripts/build_native_bi.py',
    'scripts/release_integrity.py', 'scripts/package_timepilot.py',
    'scripts/verify_installation.py', 'scripts/repair_installation.py',
    'scripts/restore_hidden.py',
    'TIMEPILOT.sh', 'TIMEPILOT.bat', 'scripts/launcher.py',
    'src/timepilot/agent_tools.py', 'src/timepilot/agent_executor.py',
    'src/timepilot/agent_api.py', 'src/timepilot/static/agent-ui.js',
    'tests/test_unified_agent_v100.py', 'docs/UNIFIED_SKILLS_EXECUTOR_V0100.md',
    'skills/data-understanding/SKILL.md', 'skills/data-quality/SKILL.md',
    'skills/bi-analysis/SKILL.md', 'skills/feature-engineering/SKILL.md',
    'skills/forecast-preparation/SKILL.md',
    'skills/semantic-evaluation/SKILL.md',
    'skills/autonomous-modeling/SKILL.md',
    'skills/mechanism-learning/SKILL.md',
    'src/timepilot/semantic_benchmark.py',
    'tests/test_research_stage4_v0120.py',
    'src/timepilot/auto_experiment.py',
    'src/timepilot/mechanisms/identification.py',
    'src/timepilot/research_api.py',
    'src/timepilot/static/research-ui.js',
    'src/timepilot/table_region_scanner.py', 'src/timepilot/project_region_catalog.py',
    'src/timepilot/import_tool_registry.py', 'src/timepilot/import_semantics.py',
    'src/timepilot/project_publish_batches.py',
    'src/timepilot/projects.py', 'src/timepilot/projects_api.py',
    'src/timepilot/workbook_intelligence.py',
    'src/timepilot/project_import_jobs.py',
    'src/timepilot/import_checkpoints.py',
    'src/timepilot/static/project-bi-ui.js', 'src/timepilot/static/project-bi-ui.css',
    'tests/test_project_bi_recovery_v015.py', 'docs/PROJECT_BI_WORKBENCH_V0150.md',
    'src/timepilot/static/project-ui.js', 'src/timepilot/static/project-ui.css',
    'scripts/ensure_native_bi.py', 'tests/test_projects_v013.py',
    'tests/test_project_layout_v013.py', 'docs/PROJECT_WORKFLOW_V0130.md',
    'tests/test_project_async_import_v014.py',
    'tests/test_project_profile_v014.py',
    'tests/test_model_semantic_proposals_v014.py',
    'docs/PROJECT_WORKFLOW_V0140.md',
    'tests/test_research_stage4_v0110.py',
    'docs/RESEARCH_AUTONOMY_V0110.md',
    'scripts/accept_research_browser.py',

)


# Only files actually loaded by a local service belong to startup validation.
# CI settings, Docker config, docs and frontend build inputs are release artifacts,
# so their absence must not make a working service unstartable.
RUNTIME_REQUIRED = (
    'pyproject.toml',
    'src/timepilot/__init__.py', 'src/timepilot/__main__.py',
    'src/timepilot/sdk.py', 'src/timepilot/api.py', 'src/timepilot/cli.py',
    'src/timepilot/config.py', 'src/timepilot/db.py',
    'src/timepilot/native_bi.py', 'src/timepilot/native_bi_api.py',
    'src/timepilot/static/index.html', 'src/timepilot/static/app.js',
    'src/timepilot/static/native-bi.js',
    'src/timepilot/modeling_workflow.py', 'src/timepilot/modeling_api.py',
    'src/timepilot/modeling_adapters.py', 'src/timepilot/industrial_plugins.py',
    'src/timepilot/static/modeling-ui.js', 'src/timepilot/static/modeling-ui.css',
    'src/timepilot/enterprise_security.py', 'src/timepilot/physical_store.py',
    'src/timepilot/industrial_bi.py', 'src/timepilot/static/security-client.js',
    'src/timepilot/static/identity.js', 'src/timepilot/static/login.html',
    'src/timepilot/static/identity.html',
    'src/timepilot/data_schema.py', 'src/timepilot/generic_assets.py',
    'src/timepilot/data_platform.py', 'src/timepilot/project_publication.py',
    'src/timepilot/generic_publish_checkpoint.py', 'src/timepilot/build_lock.py',
    'src/timepilot/runtime_backup.py',
    'scripts/preflight_schema.py', 'scripts/backup_runtime.py',
    'src/timepilot/smart_import.py', 'src/timepilot/smart_import_api.py',
    'src/timepilot/data_understanding.py', 'src/timepilot/data_understanding_api.py',
    'src/timepilot/static/smart-import-ui.js',
    'src/timepilot/smart_workbench.py', 'src/timepilot/smart_workbench_api.py',
    'src/timepilot/static/smart-workbench-ui.js', 'src/timepilot/static/smart-workbench-ui.css',
    'src/timepilot/data_assets.py', 'src/timepilot/data_assets_api.py',
    'src/timepilot/static/data-assets-ui.js',
    'src/timepilot/agent_tools.py', 'src/timepilot/agent_executor.py',
    'src/timepilot/agent_api.py', 'src/timepilot/static/agent-ui.js',
    'src/timepilot/semantic_benchmark.py',
    'src/timepilot/auto_experiment.py',
    'src/timepilot/research_api.py',
    'src/timepilot/mechanisms/identification.py',
    'src/timepilot/static/research-ui.js',
    'src/timepilot/projects.py', 'src/timepilot/projects_api.py',
    'src/timepilot/workbook_intelligence.py',
    'src/timepilot/project_import_jobs.py', 'src/timepilot/import_checkpoints.py',
    'src/timepilot/static/project-bi-ui.js', 'src/timepilot/static/project-bi-ui.css',
    'src/timepilot/static/project-ui.js', 'src/timepilot/static/project-ui.css',
    'scripts/ensure_native_bi.py',
)


def check_runtime_tree(root: Path) -> tuple[list[str], list[str], dict | None]:
    """Categorize source issues as fatal runtime faults or release-only warnings.

    Runtime does not require a release manifest or development dotfiles; source
    archive verification still uses the strict full-tree contract unchanged.
    """
    all_issues, manifest = check_tree(root)
    critical = set(RUNTIME_REQUIRED)
    errors: list[str] = []
    warnings: list[str] = []
    for filename in sorted(critical):
        if not (root / filename).is_file():
            errors.append(f'MISSING RUNTIME FILE: {filename}')
    for issue in all_issues:
        if issue.startswith('MIXED VERSION:'):
            errors.append(issue)
        elif issue.startswith('MISSING FILE:'):
            filename = issue[len('MISSING FILE:'):].strip()
            # An ordinary installed app may intentionally omit developer files.
            if filename not in critical:
                warnings.append('RELEASE-ONLY: ' + issue)
        elif issue.startswith('CHANGED OR DAMAGED FILE:'):
            filename = issue.split(':', 1)[1].strip()
            if filename in critical:
                errors.append(issue)
            else:
                warnings.append('RELEASE-ONLY: ' + issue)
        else:
            warnings.append('RELEASE-ONLY: ' + issue)
    return sorted(set(errors)), sorted(set(warnings)), manifest


def file_sha256(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            sha.update(block)
    return sha.hexdigest()


def valid_relative_path(value: str) -> bool:
    if not isinstance(value, str) or not value or '\\' in value or '\x00' in value:
        return False
    if any(ord(c) < 32 for c in value) or ':' in value:
        return False
    path = PurePosixPath(value)
    return (not path.is_absolute() and path.as_posix() == value and
            all(part not in {'', '.', '..'} for part in path.parts))


def tracked_path(root: Path, path: Path) -> bool:
    if not path.is_file() or path.is_symlink():
        return False
    relative = path.relative_to(root)
    parts = relative.parts
    if any(part in SKIP_DIRS or part.startswith(('.native-bi-staging-', '.timepilot-staging-'))
           for part in parts):
        return False
    if relative.as_posix().startswith('src/timepilot/static/native-bi/'):
        return False
    if path.name in SKIP_FILES or path.suffix.lower() in SKIP_SUFFIXES:
        return False
    return True


def source_files(root: Path) -> dict[str, Path]:
    """Walk tracked source ONLY; do not enumerate huge runtime/node_modules trees."""
    output: dict[str, Path] = {}
    for folder, directories, names in os.walk(root, followlinks=False):
        current = Path(folder)
        directories[:] = [name for name in directories
                          if name not in SKIP_DIRS
                          and not name.startswith(('.native-bi-staging-', '.timepilot-staging-'))
                          and not (current == root / 'src/timepilot/static' and name == 'native-bi')
                          and not (current / name).is_symlink()]
        for name in names:
            path = current / name
            if tracked_path(root, path):
                output[path.relative_to(root).as_posix()] = path
    return dict(sorted(output.items()))


def project_version(root: Path) -> str:
    payload = tomllib.loads((root / 'pyproject.toml').read_text(encoding='utf-8'))
    return payload['project']['version']


def build_manifest(root: Path) -> dict:
    files = source_files(root)
    missing = sorted(set(REQUIRED) - files.keys())
    if missing:
        raise ValueError('Incomplete source tree, missing: ' + ', '.join(missing))
    version = project_version(root)
    payload = {
        'format': FORMAT,
        'version': version,
        'native_bi_release_status': 'source-only-requires-local-build',
        'files': {name: {'sha256': file_sha256(path), 'bytes': path.stat().st_size}
                  for name, path in files.items()},
    }
    return payload


def write_manifest(root: Path) -> dict:
    payload = build_manifest(root)
    target = root / MANIFEST_NAME
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(target.name + '.tmp')
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    os.replace(temp, target)
    return payload


def validate_manifest(root: Path, payload: object, *, detect_extra: bool = True) -> list[str]:
    if not isinstance(payload, dict) or payload.get('format') != FORMAT:
        return ['Invalid release manifest format; restore the complete current source archive.']
    files = payload.get('files')
    if not isinstance(files, dict):
        return ['Release manifest is missing its file map.']
    issues: list[str] = []
    try:
        version = project_version(root)
        if payload.get('version') != version:
            issues.append(f'MIXED VERSION: manifest={payload.get("version")}, Python={version}')
        init_text = (root / 'src/timepilot/__init__.py').read_text(encoding='utf-8')
        match = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', init_text, re.M)
        if not match or match.group(1) != version:
            issues.append('MIXED VERSION: timepilot.__version__ differs from pyproject.toml')
        for name in ('src/timepilot/api.py', 'src/timepilot/static/index.html'):
            if not (root / name).is_file():
                issues.append(f'MISSING FILE: {name}')
    except (OSError, KeyError, ValueError, tomllib.TOMLDecodeError) as exc:
        issues.append(f'Cannot read Python package version: {exc}')
    missing = sorted(set(REQUIRED) - set(files))
    if missing:
        issues.append('RELEASE INCOMPLETE: manifest omits ' + ', '.join(missing))
    for name, meta in sorted(files.items()):
        if not valid_relative_path(name):
            issues.append(f'UNSAFE PATH: {name!r}')
            continue
        target = root.joinpath(*PurePosixPath(name).parts)
        if not target.resolve().is_relative_to(root.resolve()):
            issues.append(f'UNSAFE SYMLINK: {name}')
            continue
        if target.is_symlink() or not target.is_file():
            issues.append(f'MISSING FILE: {name}')
            continue
        if not isinstance(meta, dict) or type(meta.get('bytes')) is not int or not isinstance(meta.get('sha256'), str):
            issues.append(f'BAD MANIFEST ENTRY: {name}')
            continue
        if target.stat().st_size != meta['bytes'] or file_sha256(target) != meta['sha256']:
            issues.append(f'CHANGED OR DAMAGED FILE: {name}')
    if detect_extra:
        extra = set(source_files(root)) - set(files)
        if extra:
            issues.append('EXTRA SOURCE FILES (possible mixed release): ' + ', '.join(sorted(extra)[:30]))
    return issues


def check_tree(root: Path, *, detect_extra: bool = False) -> tuple[list[str], dict | None]:
    try:
        payload = json.loads((root / MANIFEST_NAME).read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        return [f'Release manifest missing or invalid: {exc}'], None
    return validate_manifest(root, payload, detect_extra=detect_extra), payload
