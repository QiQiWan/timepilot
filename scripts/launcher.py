"""Single cross-platform CLI for TimePilot. Uses the active interpreter only.

Subcommands reuse internal Python tools. First start may bootstrap pinned npm
packages and compile BI locally; Python and Chrome are never installed.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(*cmd: str, env: dict | None = None, timeout: int | None = None) -> None:
    print('> ' + ' '.join(cmd), flush=True)
    try:
        completed = subprocess.run(cmd, cwd=ROOT, env=env, check=False, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise SystemExit(f'[BLOCKED] Command exceeded {timeout}s; not counted as passing: {cmd[0]}') from None
    if completed.returncode:
        raise SystemExit(completed.returncode)


def source_env() -> dict[str, str]:
    env = os.environ.copy()
    env['PYTHONPATH'] = str(ROOT / 'src') + os.pathsep + env.get('PYTHONPATH', '')
    env.setdefault('TIMEPILOT_HOME', str(ROOT / 'runtime'))
    env['PYTHONUNBUFFERED'] = '1'
    return env


def accept(target: str, env: dict[str,str]) -> None:
    run(sys.executable, 'scripts/verify_installation.py', '--source-only', env=env)
    def pytest(*tests):
        run(sys.executable, '-m', 'pytest', '-q', *tests, env=env)
    if target == 'research':
        pytest('tests/test_research_stage4_v0110.py','tests/test_research_stage4_v0120.py')
    elif target == 'research-browser':
        run(sys.executable, 'scripts/accept_research_browser.py',env=env)
    elif target == 'native':
        # Native-BI acceptance only checks resources; normal start automatically
        # bootstraps the JS/WASM bundle when npm/network are available.
        run(sys.executable,'scripts/verify_installation.py','--require-runtime','--integration','--require-bundle',env=env)
        run(sys.executable,'scripts/verify_native_bi_release.py','--require-python31212',
            '--require-duckdb','--require-build','--require-source-current','--require-virtual-server',env=env)
        run(sys.executable,'scripts/system_chrome.py','--check',env=env)
        run(sys.executable,'-u','scripts/accept_native_bi_live.py',
            '--max-seconds',env.get('TIMEPILOT_ACCEPTANCE_MAX_SECONDS','300'),env=env)
    elif target == 'enterprise':
        pytest('tests/test_enterprise_security_v021.py',
               'tests/test_physical_store_v021.py','tests/test_industrial_bi_v021.py')
        run(sys.executable,'scripts/accept_enterprise_browser.py',env=env)
    elif target == 'production-recovery':
        run(sys.executable,'scripts/accept_production_recovery.py','--rows',
            env.get('TIMEPILOT_PRODUCTION_RECOVERY_ROWS','1000000'),env=env)
    elif target == 'production':
        accept('production-recovery',env)
        # These are real DuckDB queries. Missing binaries stop the acceptance
        # as BLOCKED, never as passing simulated timings.
        run(sys.executable,'scripts/benchmark_native_bi.py','--rows',
            env.get('TIMEPILOT_BENCH_SMALL_ROWS','100000'),
            env.get('TIMEPILOT_BENCH_MID_ROWS','1000000'),
            env.get('TIMEPILOT_BENCH_LARGE_ROWS','10000000'),env=env)
    elif target == 'smart':
        pytest('tests/test_smart_import_v061.py','tests/test_smart_workbench_v070.py')
        if env.get('TIMEPILOT_SMART_BROWSER_TEST') == '1':
            run(sys.executable,'scripts/accept_smart_import_browser.py',env=env)
        if env.get('TIMEPILOT_WORKBENCH_BROWSER_TEST') == '1':
            run(sys.executable,'scripts/accept_workbench_browser.py',env=env)
    elif target == 'agent':
        pytest('tests/test_data_understanding_v080.py','tests/test_unified_agent_v100.py',
               'tests/test_unified_executor_resilience_v0100.py')
        if env.get('TIMEPILOT_AGENT_BROWSER_TEST') == '1':
            run(sys.executable,'scripts/accept_data_agent_browser.py',env=env)
    elif target == 'unified':
        pytest('tests/test_p0_data_assets_v090.py','tests/test_smart_import_v061.py',
               'tests/test_smart_workbench_v070.py','tests/test_data_understanding_v080.py',
               'tests/test_ordered_perspective_session_v0512.py')
    elif target == 'universal-import':
        pytest('tests/test_universal_import_v0221.py',
               'tests/test_project_publication_jobs_v020.py')
        run(sys.executable,'scripts/accept_universal_import_v0221.py',env=env)
    elif target == 'projects':
        pytest('tests/test_projects_v013.py','tests/test_project_layout_v013.py',
               'tests/test_project_async_import_v014.py','tests/test_project_profile_v014.py',
               'tests/test_model_semantic_proposals_v014.py')
    elif target == 'all':
        for name in ('projects','unified','agent','smart','research','native'):
            accept(name,env)
    else:
        raise ValueError('Unknown acceptance target: '+target)


def main() -> int:
    if len(sys.argv)>1 and sys.argv[1]=='identity':
        run(sys.executable,'scripts/manage_identity.py',*sys.argv[2:],env=source_env())
        return 0
    if len(sys.argv)>1 and sys.argv[1]=='dedup':
        run(sys.executable,'scripts/dedup_native_bi.py',*sys.argv[2:],env=source_env())
        return 0
    parser = argparse.ArgumentParser(
        description='TimePilot unified control: start, check, test, accept, build, package. Active Python only.')
    parser.add_argument('command', nargs='?', default='start',choices=[
        'start','check','doctor','source-check','restore-hidden','status','test','accept','build','package','backup'])
    parser.add_argument('target', nargs='?', default='all',
        help='accept: universal-import / native / enterprise / production / smart / agent / projects / all')
    parser.add_argument('--port',type=int,default=None)
    parser.add_argument('--output',default=None)
    parser.add_argument('--with-native',action='store_true')
    parser.add_argument('--bootstrap-lock',action='store_true')
    args = parser.parse_args()
    env = source_env()
    print(f'[TimePilot] Python: {sys.executable} {sys.version.split()[0]}',flush=True)
    print(f'[TimePilot] Source: {ROOT} | Data: {env["TIMEPILOT_HOME"]}',flush=True)
    if args.command == 'restore-hidden':
        run(sys.executable,'scripts/restore_hidden.py',env=env)
        run(sys.executable,'scripts/verify_installation.py','--source-only',env=env)
    elif args.command == 'status':
        run(sys.executable,'scripts/verify_installation.py','--json',env=env)
        run(sys.executable,'scripts/ensure_native_bi.py','--status-only',env=env)
    elif args.command == 'source-check':
        run(sys.executable,'scripts/verify_installation.py','--source-only',env=env)
    elif args.command in ('start','check','doctor'):
        if args.command == 'start' and any(x in env['TIMEPILOT_HOME'].lower() for x in ('onedrive','cloudstorage','dropbox')):
            print('[STORAGE WARNING] SQLite / Parquet runtime is inside a cloud-synced folder. '
                  'Concurrent sync may corrupt active SQLite files. Back up and move the '
                  'TIMEPILOT_HOME directory to local storage before production use.',flush=True)
        run(sys.executable,'scripts/verify_installation.py','--require-runtime',env=env)
        if args.command == 'start':
            run(sys.executable,'scripts/preflight_schema.py','--home',env['TIMEPILOT_HOME'],env=env)
        run(sys.executable,'-m','timepilot','doctor',env=env)
        if args.command == 'start':
            run(sys.executable,'scripts/ensure_native_bi.py','--auto-build','--auto-bootstrap',env=env)
            port = args.port if args.port is not None else int(env.get('TIMEPILOT_PORT','8000'))
            if not 1 <= port <= 65535:
                raise SystemExit('Port must be 1..65535')
            host=env.get('TIMEPILOT_HOST','127.0.0.1')
            print(f'[TimePilot] Starting {host}:{port}',flush=True)
            run(sys.executable,'-m','timepilot','serve','--host',host,'--port',str(port),env=env)
    elif args.command == 'test':
        run(sys.executable,'scripts/verify_installation.py','--source-only',env=env)
        # Isolate tests that start real ASGI/SQLite/Chrome runtimes. A single
        # pytest process across 300+ tests can retain library global state and
        # hanging background workers; each group gets a fresh interpreter.
        test_files=sorted((ROOT/'tests').glob('test_*.py'))
        if not test_files:
            raise SystemExit('No tests/test_*.py files found; refusing a false PASS')
        batch=(len(test_files)+3)//4
        for start in range(0,len(test_files),batch):
            segment=test_files[start:start+batch]
            print(f'[TEST GROUP {start//batch+1}] Python tests: {len(segment)} files',flush=True)
            run(sys.executable,'-m','pytest','-q','-rs',
                *(str(p.relative_to(ROOT)) for p in segment),env=env,
                timeout=int(env.get('TIMEPILOT_TEST_GROUP_TIMEOUT','180')))
        try:
            subprocess.run(['node','--version'],check=True,capture_output=True,env=env)
        except (FileNotFoundError,subprocess.CalledProcessError):
            raise SystemExit('[BLOCKED] Node absent: frontend tests have not been run; start remains available')
        run('node','--test',*(str(x.relative_to(ROOT)) for x in sorted((ROOT/'frontend/native-bi/tests').glob('*.test.mjs'))),env=env)
        for asset in ('agent-ui.js','research-ui.js','smart-import-ui.js','data-assets-ui.js','project-ui.js','app.js','native-bi.js','security-client.js','identity.js'):
            run('node','--check','src/timepilot/static/'+asset,env=env)
    elif args.command == 'accept':
        accept(args.target,env)
    elif args.command == 'build':
        params=['bash','scripts/BUILD_OFFLINE_RELEASE.sh']
        if args.bootstrap_lock:params.append('--bootstrap-lock')
        run(*params,env=env)
    elif args.command == 'backup':
        if not args.output:raise SystemExit('Backup requires --output outside TIMEPILOT_HOME')
        run(sys.executable,'scripts/backup_runtime.py','--home',env['TIMEPILOT_HOME'],
            '--output',str(Path(args.output).expanduser().resolve()),env=env)
    elif args.command == 'package':
        run(sys.executable,'scripts/verify_installation.py','--source-only',env=env)
        params=[sys.executable,'scripts/package_timepilot.py',
                '--with-native-bundle' if args.with_native else '--source-only']
        if args.output:params.extend(['--output',str(Path(args.output).expanduser().resolve())])
        run(*params,env=env)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
