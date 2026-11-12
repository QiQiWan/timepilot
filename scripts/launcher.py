"""Single cross-platform CLI for TimePilot. Uses the active interpreter only.

Subcommands reuse internal Python tools. Launch never installs npm packages or
builds frontend assets; a reviewed offline bundle is built on the build host.
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


def choose_data_home(root: Path = ROOT, *, configured: str | None = None) -> tuple[Path, str]:
    """Keep existing source-local data; use a stable user home on fresh installs.

    Never move, merge, or delete a pre-existing runtime automatically.
    """
    if configured and configured.strip():
        return Path(configured).expanduser().resolve(), 'explicit TIMEPILOT_HOME'
    legacy = root / 'runtime'
    if legacy.is_dir() and any(legacy.iterdir()):
        return legacy.resolve(), 'existing source-local runtime (compatibility)'
    return (Path.home() / '.timepilot').resolve(), 'stable user-local default'


def source_env() -> dict[str, str]:
    env = os.environ.copy()
    env['PYTHONPATH'] = str(ROOT / 'src') + os.pathsep + env.get('PYTHONPATH', '')
    home, origin = choose_data_home(ROOT, configured=env.get('TIMEPILOT_HOME'))
    env['TIMEPILOT_HOME'] = str(home)
    env['TIMEPILOT_DATA_HOME_ORIGIN'] = origin
    env['PYTHONUNBUFFERED'] = '1'
    return env


def accept(target: str, env: dict[str,str], *, mode: str = 'offline') -> None:
    run(sys.executable, 'scripts/verify_installation.py', '--source-only', env=env)
    def pytest(*tests):
        run(sys.executable, '-m', 'pytest', '-q', *tests, env=env)
    if target == 'feature-lab-v031':
        if mode == 'configured':
            required_fields=('CSV','PROVENANCE','EVENT_TIME','AVAILABLE_AT','PREDICTION_TIME',
                'LABEL_AVAILABLE','LABEL','DEVICE','REGIME','MEASURE_A','MEASURE_B',
                'AGENT_CATALOG','AGENT_PROJECT','AGENT_ANNOTATIONS')
            missing=[f'TIMEPILOT_FEATURELAB_QA_{x}' for x in required_fields if
                not env.get(f'TIMEPILOT_FEATURELAB_QA_{x}','').strip()]
            if missing:
                print('[BLOCKED] Missing real industrial data and human-reviewed business inputs: '+', '.join(missing),file=sys.stderr)
                raise SystemExit(2)
        # Offline source QA: no external data and no model/BI/browser release claim.
        pytest('tests/test_featurelab31_join_release.py',
               'tests/test_featurelab31_business_metrics.py',
               'tests/test_featurelab30_advanced.py',
               'tests/test_featurelab30_optional_engines.py',
               'tests/test_featurelab30_acceptance_contract.py',
               'tests/test_featurelab29_review_and_schema.py')
        for asset in ('feature-advanced-ui.js','feature-shell.js','feature-evaluation-ui.js'):
            run('node','--check','src/timepilot/static/'+asset,env=env)
        run('node','--test',*(str(x.relative_to(ROOT)) for x in
            sorted((ROOT/'frontend/native-bi/tests').glob('*.test.mjs'))),env=env,timeout=100)
        if mode != 'configured':
            print('[SOURCE QA ONLY] Advanced algorithms, real industry quality metrics, '
                  'Perspective browser and human business acceptance have NOT been verified.',flush=True)
            return
        root=Path(env.get('TIMEPILOT_FEATURELAB_QA_OUTPUT','reports/v031')).expanduser().resolve()
        if root==ROOT or root==Path.home().resolve() or root==Path(env['TIMEPILOT_HOME']).resolve():
            raise SystemExit('[BLOCKED] QA output must be a separate disposable directory')
        root.mkdir(parents=True,exist_ok=True)
        audit_home=root/'retained_qa_home'
        if audit_home.exists() and any(audit_home.iterdir()):
            raise SystemExit('[BLOCKED] QA home must be empty; never overwrite existing application data')
        qa_vars={
            'EVENT_TIME':'event-time','AVAILABLE_AT':'available-at','PREDICTION_TIME':'prediction-time',
            'LABEL_AVAILABLE':'label-available','LABEL':'target','DEVICE':'device',
            'REGIME':'regime','MEASURE_A':'measure-a','MEASURE_B':'measure-b'}
        column_flags=sum((['--'+flag,env['TIMEPILOT_FEATURELAB_QA_'+name]]
            for name,flag in qa_vars.items()),[])
        shared=['--csv',env['TIMEPILOT_FEATURELAB_QA_CSV']]+column_flags+[
            '--timezone',env.get('TIMEPILOT_FEATURELAB_QA_TIMEZONE','Asia/Shanghai')]
        backend=root/'backend.json'
        run(sys.executable,'scripts/featurelab30_e2e.py',*shared,
            '--provenance',env['TIMEPILOT_FEATURELAB_QA_PROVENANCE'],
            '--backend-only','--audit-home',str(audit_home), '--report',str(backend),
            env=env,timeout=1200)
        advanced=root/'advanced.json'
        run(sys.executable,'scripts/featurelab31_real_advanced_e2e.py',*shared,
            '--backend',str(backend),'--out',str(advanced),env=env,timeout=900)
        browser=root/'browser.json'
        run(sys.executable,'scripts/featurelab31_browser_acceptance.py',
            '--backend',str(backend),'--out',str(browser),env=env,timeout=300)
        business=root/'business.json'
        run(sys.executable,'scripts/featurelab31_agent_business_acceptance.py',
            '--catalog',env['TIMEPILOT_FEATURELAB_QA_AGENT_CATALOG'],
            '--project',env['TIMEPILOT_FEATURELAB_QA_AGENT_PROJECT'],
            '--annotations',env['TIMEPILOT_FEATURELAB_QA_AGENT_ANNOTATIONS'],
            '--out',str(business),env=env,timeout=180)
        run(sys.executable,'scripts/featurelab31_release_gate.py',
            '--backend',str(backend),'--advanced',str(advanced),
            '--browser',str(browser),'--business',str(business),
            '--out',str(root/'release_gate.json'),env=env,timeout=60)
    elif target == 'feature-lab-v030':
        # Contract, PIT and migration tests execute without optional dependencies.
        pytest('tests/test_featurelab30_advanced.py',
               'tests/test_featurelab30_acceptance_contract.py',
               'tests/test_featurelab30_optional_engines.py')
        pytest('tests/test_featurelab29_selection_and_search.py',
               'tests/test_featurelab29_review_and_schema.py')
        pytest('tests/test_featurelab27_candidates.py')
        for asset in ('feature-advanced-ui.js','feature-shell.js','feature-evaluation-ui.js'):
            run('node','--check','src/timepilot/static/'+asset,env=env)
        run('node','--test','frontend/native-bi/tests/build-contract.test.mjs',
            'frontend/native-bi/tests/hosted-table.test.mjs',
            'frontend/native-bi/tests/workspace-state.test.mjs',env=env,timeout=90)
        if mode == 'configured':
            required=['CSV','PROVENANCE','EVENT_TIME','AVAILABLE_AT','PREDICTION_TIME',
                      'LABEL_AVAILABLE','LABEL','DEVICE','REGIME','MEASURE_A','MEASURE_B']
            missing=[v for v in required if not env.get('TIMEPILOT_FEATURELAB_QA_'+v,'').strip()]
            if missing:raise SystemExit('[BLOCKED] Same-source actual data validation requires '+
                ', '.join('TIMEPILOT_FEATURELAB_QA_'+v for v in missing))
            opts=['--csv',env['TIMEPILOT_FEATURELAB_QA_CSV'],
                '--provenance',env['TIMEPILOT_FEATURELAB_QA_PROVENANCE'],
                '--report','reports/v030/same_source_e2e.json']
            keys={'EVENT_TIME':'event-time','AVAILABLE_AT':'available-at',
              'PREDICTION_TIME':'prediction-time','LABEL_AVAILABLE':'label-available',
              'LABEL':'target','DEVICE':'device','REGIME':'regime',
              'MEASURE_A':'measure-a','MEASURE_B':'measure-b'}
            for envkey,flag in keys.items():
                opts+=['--'+flag,env['TIMEPILOT_FEATURELAB_QA_'+envkey]]
            opts+=['--timezone',env.get('TIMEPILOT_FEATURELAB_QA_TIMEZONE','Asia/Shanghai')]
            run(sys.executable,'scripts/featurelab30_e2e.py',*opts,env=env,timeout=600)
            print('[PARTIAL] Same-source PIT materialization, evaluation and SQL BI '
                  'views passed; actual Perspective browser/WS receipt and separate '
                  'installed optional-engine tests are additional mandatory release gates.',flush=True)
        else:
            print('[SOURCE ONLY] Tests used mocks, fixtures and sklearn numeric data. '
                  'No independently verified plant data or Perspective browser receipt.',flush=True)
    elif target == 'feature-lab-v029':
        # Run in separate interpreters: the older ASGI tests can retain worker
        # threads when bundled with historical test modules.
        pytest('tests/test_featurelab29_selection_and_search.py',
               'tests/test_featurelab29_review_and_schema.py')
        pytest('tests/test_featurelab28_advanced.py')
        pytest('tests/test_featurelab27_candidates.py')
        pytest('tests/test_featurelab2_temporal_and_evaluation_v0250.py')
        for asset in ('feature-evaluation-ui.js','feature-shell.js'):
            run('node','--check','src/timepilot/static/'+asset,env=env)
        run('node','--test','frontend/native-bi/tests/build-contract.test.mjs',
            'frontend/native-bi/tests/hosted-table.test.mjs',
            'frontend/native-bi/tests/workspace-state.test.mjs',env=env,timeout=90)
        if mode == 'configured':
            csv_path=env.get('TIMEPILOT_FEATURELAB_QA_CSV','').strip()
            required=('TIME','LABEL','LABEL_AVAILABLE','PROCESS','FEATURES')
            missing=[key for key in required if not env.get('TIMEPILOT_FEATURELAB_QA_'+key,'').strip()]
            if not csv_path or not Path(csv_path).expanduser().is_file() or missing:
                raise SystemExit('[BLOCKED] A real CSV path and confirmed field mapping are required: '
                    'TIMEPILOT_FEATURELAB_QA_CSV plus TIME/LABEL/LABEL_AVAILABLE/PROCESS/FEATURES. '
                    'No generated fixture is accepted as industrial evidence. Missing: '+','.join(missing))
            csv_path=str(Path(csv_path).expanduser().resolve())
            # First verify native CSV -> candidate preview -> materialized Parquet
            # -> SQL BI hosted views with actual source bytes in an isolated home.
            run(sys.executable,'scripts/featurelab28_acceptance.py',
                '--csv',csv_path,'--report','reports/v029/native_materialization.json',
                env=env,timeout=300)
            # Then independently verify supplied, already computed, confirmed
            # as-of feature columns through real sklearn fold comparisons.
            opts=['--csv',csv_path,'--time',env['TIMEPILOT_FEATURELAB_QA_TIME'],
                  '--target',env['TIMEPILOT_FEATURELAB_QA_LABEL'],
                  '--label-available',env['TIMEPILOT_FEATURELAB_QA_LABEL_AVAILABLE'],
                  '--process',env['TIMEPILOT_FEATURELAB_QA_PROCESS'],
                  '--features',env['TIMEPILOT_FEATURELAB_QA_FEATURES'],
                  '--models',env.get('TIMEPILOT_FEATURELAB_QA_MODELS','both'),
                  '--timezone',env.get('TIMEPILOT_FEATURELAB_QA_TIMEZONE','UTC'),
                  '--report','reports/v029/industrial_numerical.json']
            if env.get('TIMEPILOT_FEATURELAB_QA_GROUP','').strip():
                opts+=['--group',env['TIMEPILOT_FEATURELAB_QA_GROUP']]
            else:opts+=['--group','']
            if env.get('TIMEPILOT_FEATURELAB_QA_SEARCH','0')=='1':opts+=['--search']
            run(sys.executable,'scripts/featurelab29_benchmark.py',*opts,env=env,timeout=360)
            print('[PARTIAL] Native materialization and independent numerical replay passed. '
                  'The numerical CSV replay is NOT a PIT-verified evaluation job '
                  'on the same materialized supervised asset. Perspective browser '
                  'interaction still requires its own live acceptance.',flush=True)
        else:
            print('[SOURCE / FIXTURE ONLY] No verified production-time source or '
                  'native Perspective render evaluated in this mode.',flush=True)
    elif target == 'feature-lab-v028':
        pytest('tests/test_featurelab28_advanced.py',
               'tests/test_featurelab27_candidates.py',
               'tests/test_featurelab4_industrial_v0259.py',
               'tests/test_featurelab28_real_duckdb.py')
        run('node','--test','frontend/native-bi/tests/build-contract.test.mjs',
            'frontend/native-bi/tests/hosted-table.test.mjs',
            'frontend/native-bi/tests/workspace-state.test.mjs',env=env,timeout=90)
        if mode == 'configured':
            csv_path=env.get('TIMEPILOT_FEATURELAB_QA_CSV','').strip()
            if not csv_path or not Path(csv_path).expanduser().is_file():
                raise SystemExit('[BLOCKED] TIMEPILOT_FEATURELAB_QA_CSV must point to an existing industrial CSV; '
                                 'no generated fixture will be claimed as actual production data')
            run(sys.executable,'scripts/featurelab28_acceptance.py',
                '--csv',str(Path(csv_path).expanduser().resolve()),
                '--report','reports/v028/featurelab_actual_data.json',env=env,timeout=300)
            print('[PARTIAL] Candidate -> preview -> Parquet -> BI SQL views checked. '
                  'Interactive feature-side Perspective panel still requires manual browser acceptance.',flush=True)
        else:
            print('[SOURCE / FIXTURE ONLY] Real DuckDB/Arrow may be skipped. '
                  'Use --mode configured with TIMEPILOT_FEATURELAB_QA_CSV for real file checks.',flush=True)
    elif target == 'catalog-bootstrap-v0261':
        run(sys.executable, 'scripts/accept_catalog_v0261.py', '--mode', mode, env=env,
            timeout=180)
    elif target in ('feature-audit-v0257','feature-engine-v0258',
                  'feature-industrial-v0259','feature-agent-e2e-v0260'):
        run(sys.executable,'scripts/accept_featurelab4_stages.py',
            '--stage',target,'--mode',mode,env=env,timeout=300)
    elif target in ('feature-safety-v0253','feature-preview-v0254',
                  'feature-perspective-v0255','feature-agent-v0256'):
        run(sys.executable,'scripts/accept_featurelab3_stages.py',
            '--stage',target,'--mode',mode,env=env,timeout=300)
    elif target in ('feature-contract-v0247','feature-profile-v0248',
                  'feature-execution-v0249','feature-evaluation-v0250',
                  'feature-registry-v0251','feature-agent-v0252'):
        run(sys.executable,'scripts/accept_featurelab2_stages.py',
            '--stage',target,'--mode',mode,env=env,timeout=300)
    elif target == 'bi-ux-v0246':
        pytest('tests/test_bi_ux_preferences_v0246.py',
               'tests/test_bi_ux_static_v0246.py',
               'tests/test_ui_release.py')
        run('node', '--test', 'frontend/native-bi/tests/series-preservation-v0246.test.mjs', env=env)
        run(sys.executable, 'scripts/accept_bi_ux_v0246.py',
            '--output', 'reports/v0246/bi-ux-chromium.json', env=env)
        if mode == 'configured':
            # The widget smoke test does not replace a real native JS/WASM,
            # DuckDB, Arrow and browser integration acceptance.
            accept('native', env, mode='configured')
        else:
            print('[COMPONENT / SOURCE ONLY] Real Perspective 5.6.1 E2E is still required.',flush=True)
    elif target in ('bi-recovery-v0241','import-contract-v0242','bi-workspace-v0243','feature-lab-v0244','bi-reasoning-v0245'):
        run(sys.executable,'scripts/accept_bi_stages_v0245.py','--stage',target,'--mode',mode,env=env)
    elif target in ('assets-v0236','bi-fullscreen-v0237','perspective-parity-v0238','bi-tools-v0239','bi-agent-v0240'):
        run(sys.executable,'scripts/accept_bi_stages_v0240.py','--stage',target,'--mode',mode,env=env)
    elif target == 'bi-ui-v0233':
        run(sys.executable,'scripts/accept_bi_workspace_ui_v0233.py',env=env)
    elif target == 'import-summary-v0234':
        run(sys.executable,'scripts/accept_import_summary_v0234.py',env=env)
    elif target == 'bi-fuzzy-v0235':
        pytest('tests/test_bi_analysis_plans_v0235.py',
               'tests/test_bi_correlation_duckdb_v0235.py',
               'tests/test_bi_feasibility_security_v0235.py',
               'tests/test_bi_fuzzy_v0235.py')
        run(sys.executable,'scripts/evaluate_bi_fuzzy_v0235.py','--actual-engine-ready',env=env)
        if mode == 'configured':
            run(sys.executable,'scripts/accept_bi_fuzzy_live_v0235.py',env=env)
        else:
            print('[SOURCE / SYNTHETIC ONLY] Real DuckDB/Arrow/Chrome chained acceptance is not claimed.',flush=True)
    elif target == 'research':
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
    elif target == 'derived-real':
        pytest('tests/test_derived_and_nl_v0232.py')
        run(sys.executable,'scripts/accept_derived_real_v0232.py',env=env)
    elif target == 'bi-nl':
        mode=env.get('TIMEPILOT_BI_NL_MODE','offline')
        params=[sys.executable,'scripts/evaluate_bi_nl_v0232.py','--mode',mode,'--enforce-thresholds']
        if mode=='configured':params+=['--runtime',env['TIMEPILOT_HOME']]
        run(*params,env=env)
    elif target == 'industrial-bi':
        params=[sys.executable,'scripts/accept_industrial_bi_v0232.py',
                '--duration',env.get('TIMEPILOT_INDUSTRIAL_DURATION_SECONDS','900'),
                '--rows',env.get('TIMEPILOT_INDUSTRIAL_ROWS','1000000')]
        if env.get('TIMEPILOT_INDUSTRIAL_DATA_PATH'):
            params+=['--input',env['TIMEPILOT_INDUSTRIAL_DATA_PATH']]
        for field,option in (('TIMEPILOT_INDUSTRIAL_TIME_FIELD','--time-field'),
                             ('TIMEPILOT_INDUSTRIAL_METRIC_FIELD','--metric-field'),
                             ('TIMEPILOT_INDUSTRIAL_DEVICE_FIELD','--device-field')):
            if env.get(field):params.extend([option,env[field]])
        run(*params,env=env)
    elif target == 'conversational-bi-ui':
        run(sys.executable,'scripts/accept_bi_conversation_browser_v0231.py',env=env)
    elif target == 'conversational-bi':
        pytest('tests/test_conversational_bi_v0231.py')
        run(sys.executable,'scripts/accept_conversational_bi_v0231.py',env=env)
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
        'start','check','doctor','db-check','source-check','source-repair','restore-hidden','bi-setup','status','test','accept','build','package','backup'])
    parser.add_argument('target', nargs='?', default='all',
        help='accept: feature-lab-v031 / feature-lab-v030 / feature-lab-v029 / native / all')
    parser.add_argument('--mode',choices=['offline','configured'],default='offline',help='accept: configured requires real DuckDB/Arrow/Perspective/Chrome E2E')
    parser.add_argument('--port',type=int,default=None)
    parser.add_argument('--output',default=None)
    parser.add_argument('--with-native',action='store_true')
    parser.add_argument('--bootstrap-lock',action='store_true')
    parser.add_argument('--dry-run', action='store_true', help='source-repair: inspect changes without modifying files')
    args = parser.parse_args()
    env = source_env()
    print(f'[TimePilot] Python: {sys.executable} {sys.version.split()[0]}',flush=True)
    print(f'[TimePilot] Source: {ROOT} | Data: {env["TIMEPILOT_HOME"]} '
          f'({env["TIMEPILOT_DATA_HOME_ORIGIN"]})',flush=True)
    if args.command == 'db-check':
        run(sys.executable, 'scripts/inspect_catalog.py', '--home', env['TIMEPILOT_HOME'], env=env)
    elif args.command == 'source-repair':
        params = [sys.executable, 'scripts/restore_hidden.py', '--repair-changed']
        if args.dry_run:
            params.append('--dry-run')
        run(*params, env=env)
        if not args.dry_run:
            run(sys.executable, 'scripts/verify_installation.py', '--source-only', env=env)
    elif args.command == 'restore-hidden':
        run(sys.executable,'scripts/restore_hidden.py',env=env)
        run(sys.executable,'scripts/verify_installation.py','--source-only',env=env)
    elif args.command == 'status':
        run(sys.executable, 'scripts/inspect_catalog.py', '--home', env['TIMEPILOT_HOME'], env=env)
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
            run(sys.executable, 'scripts/inspect_catalog.py', '--home', env['TIMEPILOT_HOME'], env=env)
            run(sys.executable,'scripts/preflight_schema.py' ,'--home',env['TIMEPILOT_HOME'],env=env)
        run(sys.executable,'-m','timepilot','doctor',env=env)
        if args.command == 'start':
            run(sys.executable,'scripts/ensure_native_bi.py','--status-only',env=env)
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
        accept(args.target,env,mode=args.mode)
    elif args.command == 'bi-setup':
        params = [sys.executable, 'scripts/setup_native_bi.py']
        if args.bootstrap_lock:
            params.append('--bootstrap-lock')
        run(*params, env=env)
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
