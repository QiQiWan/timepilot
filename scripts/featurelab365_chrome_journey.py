#!/usr/bin/env python3
"""v0.36.5 real Chrome journey: import -> analysis -> human edit -> BI -> report.

This is an acceptance GATE, not a demo. It never downloads a browser, mocks
DuckDB, simulates Perspective, fabricates a UI receipt or edits production data.
The same frozen v0.36.2 synthetic CSV and independent oracles are reused.

    python scripts/featurelab365_chrome_journey.py --out reports/v0365/chrome.json
    TIMEPILOT_CHROME_PATH=".../Google Chrome" python scripts/featurelab365_chrome_journey.py

Exit codes: 0=PASSED, 1=FAILED, 2=BLOCKED (environment/physical dependencies).
When a test cannot run, a named blocked stage is emitted, never a PASS.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import csv
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import socket
import sys
import tempfile
import threading
import time
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'scripts')]
from timepilot.featurelab361_fixtures import generate_suite, verify_suite, digest_file
from timepilot.featurelab362_reference import verify_frozen_baseline

DEPENDENCIES = ('fastapi', 'uvicorn', 'httpx', 'duckdb', 'pyarrow', 'perspective', 'playwright')
TIMEOUT = 150


class Blocked(RuntimeError):
    pass


class Journal:
    def __init__(self, destination: Path):
        self.path = destination
        self.document = {
            'release': '0.36.5', 'status': 'running', 'browser_acceptance': False,
            'production_accepted': False, 'source': 'frozen generated synthetic',
            'timestamp_utc': datetime.now(timezone.utc).isoformat(),
            'steps': [], 'errors': [], 'http_errors': [], 'websockets': [],
        }
        self.save()

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + '.tmp')
        tmp.write_text(json.dumps(self.document, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        tmp.replace(self.path)

    @contextmanager
    def step(self, title):
        entry = {'stage': title, 'status': 'running', 'start_utc': datetime.now(timezone.utc).isoformat()}
        self.document['steps'].append(entry)
        self.save()
        print('[RUN]', title, flush=True)
        clock = time.monotonic()
        try:
            yield entry
            entry['status'] = 'passed'
            print('[PASS]', title, flush=True)
        except Blocked as exc:
            entry['status'] = 'blocked'
            entry['reason'] = str(exc)[:500]
            raise
        except Exception as exc:
            entry['status'] = 'failed'
            entry['reason'] = type(exc).__name__ + ': ' + str(exc)[:1000]
            raise
        finally:
            entry['elapsed_seconds'] = round(time.monotonic() - clock, 2)
            self.save()

    def finish(self, status, error=None):
        self.document['status'] = status
        self.document['browser_acceptance'] = status == 'passed'
        if error:
            self.document['error'] = type(error).__name__ + ': ' + str(error)[:1500]
        self.save()


def required_modules():
    return [name for name in DEPENDENCIES if importlib.util.find_spec(name) is None]


def require_actual_google_chrome(executable):
    """No silent Chromium fallback when an installed Google Chrome is requested."""
    name = Path(executable).name.lower()
    if name not in ('google chrome', 'chrome', 'chrome.exe', 'google-chrome', 'google-chrome-stable'):
        raise Blocked('Google Chrome required; selected browser is ' + str(executable) +
                      '. Set TIMEPILOT_CHROME_PATH to an already-installed Google Chrome binary.')
    return str(executable)


def verify_fixtures(folder: Path):
    generate_suite(folder)
    manifest = verify_suite(folder)
    frozen = verify_frozen_baseline(folder)
    errors = [*(manifest.get('errors') or []), *(frozen.get('errors') or [])]
    if manifest.get('status') != 'passed' or errors:
        raise AssertionError('Frozen synthetic input drifted: ' + '; '.join(errors[:5]))
    src = folder / 'sensor_unlabeled.csv'
    with src.open(newline='', encoding='utf-8') as file:
        originals = list(csv.DictReader(file))
    if not originals or len(originals) != 1200:
        raise AssertionError('Expected 1200 frozen, unlabeled records')
    return {'csv': str(src), 'row_count': len(originals),
            'source_csv_sha256': digest_file(src), 'manifest_sha256': digest_file(folder / 'MANIFEST.json'),
            'baseline_sha256': frozen.get('baseline_sha256'), 'original_rows': originals}


def native_parity(app, asset_id, fixture):
    """Independent CSV-vs-physical-Parquet group statistics and byte integrity."""
    import duckdb
    import pyarrow.parquet as pq
    target = app.state.native_bi._path(asset_id)
    receipt = app.state.native_bi.verify(asset_id)
    assert receipt['valid'] is True, receipt
    assert target.is_file() and receipt['actual_sha256'] == digest_file(target)
    metadata = pq.ParquetFile(target).metadata
    assert metadata.num_rows == fixture['row_count'], (metadata.num_rows, fixture['row_count'])
    oracle = {}
    for row in fixture['original_rows']:
        name = row['equipment_id']
        c, total = oracle.get(name, (0, 0.))
        oracle[name] = (c + 1, total + float(row['sensor_a']))
    con = duckdb.connect(':memory:')
    try:
        actual = con.execute('SELECT equipment_id, COUNT(*) n, SUM(TRY_CAST(sensor_a AS DOUBLE)) s '
                             'FROM read_parquet(?) GROUP BY equipment_id ORDER BY equipment_id',
                             [str(target)]).fetchall()
        assert len(actual) == len(oracle)
        for label, count, total in actual:
            expected_count, expected_total = oracle[label]
            assert count == expected_count, (label, count, expected_count)
            assert abs(float(total) - expected_total) <= 1e-5, (label, total, expected_total)
    finally:
        con.close()
    return {'parquet_sha256': receipt['actual_sha256'], 'row_count': metadata.num_rows,
            'device_count': len(actual), 'oracle': 'independent CSV Python aggregation'}


def await_json(client, endpoint, predicate, *, seconds=TIMEOUT):
    deadline = time.monotonic() + seconds
    last = None
    while time.monotonic() < deadline:
        response = client.get(endpoint)
        if response.status_code == 200:
            last = response.json()
            if predicate(last):
                return last
        else:
            last = {'http_status': response.status_code, 'body': response.text[:200]}
        time.sleep(.5)
    raise AssertionError('Timed out waiting for ' + endpoint + ' last=' + repr(last)[:500])


def http_ok(url):
    import httpx
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        try:
            resp = httpx.get(url + '/api/v1/health', timeout=2)
            if resp.status_code == 200:
                return
        except (OSError, httpx.HTTPError):
            pass
        time.sleep(.15)
    raise RuntimeError('Local FastAPI did not become healthy')


def ui_observers(page, doc):
    """Passive observer only; no mocking responses or installing extensions."""
    def note(error):
        if len(doc['errors']) < 40:
            doc['errors'].append(str(error)[:500])
    page.on('pageerror', lambda err: note('pageerror: ' + str(err)))
    page.on('requestfailed', lambda req: note('requestfailed: ' + req.url))
    def response(resp):
        if resp.status >= 400 and len(doc['http_errors']) < 40 and not resp.url.endswith('/favicon.ico'):
            doc['http_errors'].append({'status': resp.status, 'url': resp.url[:260]})
    page.on('response', response)
    page.on('websocket', lambda ws: doc['websockets'].append(ws.url[:250]) if len(doc['websockets']) < 20 else None)


def report_integrity(markdown, sha):
    computed = hashlib.sha256(markdown.encode('utf-8')).hexdigest()
    if not re.fullmatch(r'[0-9a-f]{64}', str(sha)) or computed != sha:
        raise AssertionError('Report bytes do not match signed SHA256')
    return computed


def run_browser(app, fixture, journal, folder, timeout, bi_cycles=0):
    """Real UI interactions and independent HTTP/Parquet parity checks."""
    import httpx
    import uvicorn
    from playwright.sync_api import sync_playwright
    from system_chrome import launch_installed_chrome
    from accept_native_bi_live import wait_workspace
    # Do not use the caller's HOME / Chrome profile / application database.
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    base = f'http://127.0.0.1:{port}'
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port,
                                          log_level='error', access_log=False))
    worker = threading.Thread(target=server.run, daemon=True)
    worker.start()
    try:
        with journal.step('real_server_health') as item:
            http_ok(base)
            item['base_url'] = 'localhost isolated test'
        with sync_playwright() as play:
            browser = launch_installed_chrome(play)
            try:
                journal.document['chrome_version'] = browser.version
                context = browser.new_context(viewport={'width': 1510, 'height': 980},
                                              accept_downloads=True)
                page = context.new_page()
                page.set_default_timeout(20000)
                ui_observers(page, journal.document)
                page.on('dialog', lambda dialog: dialog.accept())
                def screenshot(name):
                    if os.getenv('TIMEPILOT_QA_SCREENSHOTS') == '1':
                        directory = journal.path.parent / 'screenshots'
                        directory.mkdir(parents=True, exist_ok=True)
                        page.screenshot(path=str(directory / (name + '.png')), full_page=True, timeout=15000)
                with httpx.Client(base_url=base, timeout=20.0) as http:
                    with journal.step('browser_creates_project') as item:
                        page.goto(base + '/projects', wait_until='domcontentloaded', timeout=30000)
                        page.locator('[data-project="new"]').first.click()
                        page.locator('#project-create-form input[name="name"]').fill('v0.36.5 Frozen Chrome QA')
                        page.locator('#project-create-form button[type="submit"]').click()
                        page.wait_for_url(re.compile(r'/projects/[^/?]+$'), timeout=30000)
                        pid = urlsplit(page.url).path.rsplit('/', 1)[-1]
                        data = http.get('/api/v1/projects/' + pid)
                        data.raise_for_status()
                        assert data.json()['project']['id'] == pid
                        item['project_id'] = pid
                        screenshot('01-project')
                    prefix = f'/api/v1/projects/{pid}'
                    with journal.step('real_ui_csv_upload_and_background_scan') as item:
                        page.goto(base + f'/projects/{pid}/data/import', wait_until='domcontentloaded')
                        page.locator('#project-upload-form input[type="file"]').set_input_files(fixture['csv'])
                        page.locator('#project-upload-form button[type="submit"]').click()
                        page.wait_for_url(re.compile(r'/projects/[^/]+/data/import-job/'), timeout=25000)
                        job_id = urlsplit(page.url).path.rsplit('/', 1)[-1]
                        job = await_json(http, prefix + '/imports/jobs/' + job_id,
                                         lambda data: data.get('status') in ('succeeded', 'failed'), seconds=timeout)
                        assert job['status'] == 'succeeded', job
                        iid = job['import_id']
                        page.wait_for_url(re.compile(r'/projects/[^/]+/data/import/[^/]+$'), timeout=30000)
                        catalog = http.get(prefix + '/imports/' + iid + '/catalog').json()
                        assert catalog['regions_total'] >= 1, catalog
                        item.update(import_id=iid, upload_job_id=job_id,
                                    source_csv_sha256=fixture['source_csv_sha256'])
                        screenshot('02-import')
                    with journal.step('publication_resumes_after_browser_refresh') as item:
                        page.locator('[data-project="publish-import-batch"]').click()
                        page.locator('#tp-import-batch-progress').wait_for()
                        batches = http.get(prefix + '/imports/' + iid + '/publish-batches').json()['batches']
                        assert batches, 'No durable batch after UI click'
                        batch_id = batches[0]['batch_id']
                        # Hard refresh during the actual publish. The UI must resume polling.
                        page.reload(wait_until='domcontentloaded')
                        page.locator('#tp-import-batch-progress').wait_for(timeout=30000)
                        batch = await_json(http, prefix + '/publish-batches/' + batch_id,
                                           lambda data: data['status'] not in ('queued', 'running'), seconds=timeout)
                        assert batch['status'] == 'succeeded', batch
                        completed = [x for x in batch['items'] if x.get('native_asset_id')]
                        assert len(completed) == 1, batch
                        aid = completed[0]['native_asset_id']
                        page.locator('#tp-import-batch-progress a[href*="/analysis"]').wait_for(timeout=20000)
                        item.update(batch_id=batch_id, native_asset_id=aid)
                        screenshot('03-publication-restored')
                    with journal.step('same_source_real_parquet_and_independent_csv_oracle') as item:
                        item.update(native_parity(app, aid, fixture))
                    url = base + f'/projects/{pid}/analysis?asset={aid}&version=base'
                    with journal.step('real_auto_profile_data_summary_and_chart') as item:
                        page.goto(url, wait_until='domcontentloaded')
                        page.locator('#tp-analysis-workspace[data-section="overview"]').wait_for(timeout=30000)
                        data_url = prefix + f'/feature-lab/data-analysis?asset_id={aid}&version_id=base'
                        status = await_json(http, data_url,
                                            lambda d: d['status'] in ('ready', 'failed_terminal', 'failed_retryable'),
                                            seconds=timeout)
                        assert status['status'] == 'ready', status
                        assert status['row_count'] == fixture['row_count']
                        page.locator('.tp-da-status strong').get_by_text('数据分析完成').wait_for(timeout=30000)
                        page.locator('[data-analysis-section="statistics"]').click()
                        page.locator('#tp-analysis-workspace[data-section="statistics"]').wait_for()
                        page.locator('#tp-project-data-analysis .tp-da-chart').wait_for(timeout=30000)
                        chart = page.locator('#tp-project-data-analysis .tp-da-chart')
                        assert chart.count() == 1
                        # Avoid selecting an inferred timestamp/category as a numeric histogram.
                        field_select = page.locator('#tp-project-data-analysis [data-da="field"]')
                        field_select.select_option(label='sensor_a')
                        page.locator('#tp-project-data-analysis .tp-da-chart').wait_for(timeout=30000)
                        field_id = field_select.input_value()
                        params = {'asset_id':aid,'version_id':'base','field_id':field_id,'mode':'distribution','resolution':120}
                        chart_api = http.get(prefix+'/feature-lab/data-analysis/chart',params=params)
                        chart_api.raise_for_status();chart_doc = chart_api.json()
                        assert chart_doc['source_sha256'] == status['source_sha256']
                        assert sum(int(x.get('count',0)) for x in chart_doc['points']) == chart_doc['finite_count']
                        item.update(source_sha256=status['source_sha256'], chart_field_id=field_id,
                                    chart_points=len(chart_doc['points']), chart_total=chart_doc['finite_count'])
                        screenshot('04-statistics')
                    auto_url = prefix+'/feature-lab/analysis/latest?asset_id='+aid+'&version_id=base'
                    with journal.step('durable_auto_candidate_generation') as item:
                        latest = await_json(http, auto_url,
                                            lambda d: (d.get('session') or {}).get('status') in
                                             ('ready', 'needs_input', 'failed'), seconds=timeout)
                        session = latest['session']
                        assert session['status'] == 'ready' and session['stage'] == 'review', session
                        sid = session['session_id']
                        assert session['source_sha256'] == status['source_sha256']
                        assert (session.get('report') or {}).get('candidate_count',0) > 0
                        item.update(session_id=sid,candidate_count=session['report']['candidate_count'])
                    with journal.step('manual_candidate_edit_persisted_across_refresh') as item:
                        feature_url = url + '&section=features'
                        page.goto(feature_url, wait_until='domcontentloaded')
                        page.locator('#tp-fl2-auto-select').wait_for(timeout=45000)
                        checkboxes = page.locator('#tp-fl2-auto-select input[name="candidate_id"]:not([disabled])')
                        assert checkboxes.count() >= 1
                        # Change the selection while retaining at least one usable candidate.
                        before = [bool(checkboxes.nth(i).is_checked()) for i in range(checkboxes.count())]
                        unchecked = next((i for i, selected in enumerate(before) if not selected), None)
                        if unchecked is not None:
                            checkboxes.nth(unchecked).check()
                        elif len(before) >= 2 and sum(before) >= 2:
                            checkboxes.nth(0).uncheck()
                        else:
                            raise AssertionError('No editable candidate choice available; actual interaction cannot be certified')
                        page.locator('#tp-fl2-auto-select button[type="submit"]').click()
                        latest_selected = await_json(http,prefix+'/feature-lab/analysis/sessions/'+sid,
                                                     lambda d: d['revision']>session['revision'],seconds=20)
                        expected_selected = sorted(latest_selected['selection'])
                        assert expected_selected, 'Manual choice removed every candidate; cannot materialize'
                        page.reload(wait_until='domcontentloaded')
                        page.locator('#tp-fl2-auto-select').wait_for(timeout=45000)
                        actual_selected = sorted(page.locator('#tp-fl2-auto-select input[name="candidate_id"]:checked').evaluate_all(
                            '(nodes) => nodes.map(n => n.value)'))
                        assert actual_selected == expected_selected, (actual_selected, expected_selected)
                        assert 'section=features' in page.url and 'asset='+aid in page.url
                        item.update(selected_candidate_ids=expected_selected,restored=True)
                        screenshot('05-manual-edit-restored')
                    with journal.step('user_confirmed_materialization_and_sha256') as item:
                        page.locator('#tp-fl2-auto-save').wait_for(timeout=25000)
                        page.locator('#tp-fl2-auto-save input[name="name"]').fill('QA 人工确认特征集')
                        page.locator('#tp-fl2-auto-save input[name="confirm_materialize"]').check()
                        page.locator('#tp-fl2-auto-save button[type="submit"]').click()
                        saved = await_json(http, prefix+'/feature-lab/analysis/sessions/'+sid,
                                           lambda d: d.get('status') in ('ready','failed') and
                                                     d.get('stage') in ('materialized','evaluated','materializing'),
                                           seconds=timeout)
                        assert saved['status'] == 'ready' and saved.get('artifact'), saved
                        proof_resp = http.get(prefix+'/feature-lab/analysis/sessions/'+sid+'/delivery')
                        proof_resp.raise_for_status();proof=proof_resp.json()
                        art = saved['artifact']
                        assert proof['source']['sha256'] == status['source_sha256']
                        assert proof['artifact']['sha256'] == art['sha256']
                        assert re.fullmatch(r'[a-f0-9]{64}',art['sha256'])
                        item.update(materialized_sha256=art['sha256'],feature_set_id=saved['feature_set_id'],
                                    source_sha256=proof['source']['sha256'])
                    if bi_cycles:
                        with journal.step('same_document_bi_feature_switch_cycles') as item:
                            # This stage must use real SPA navigation. page.goto()
                            # creates a fresh CustomElementRegistry on every hop
                            # and would hide the v0.37.0 double-registration bug.
                            feature_target=(f'/projects/{pid}/features/build?asset={aid}'
                                f'&version=base&auto_bi={art["artifact_id"]}'
                                f'&set={saved["feature_set_id"]}&auto_sid={sid}')
                            source_target=f'/projects/{pid}/bi/native/{aid}'
                            page.goto(base + source_target, wait_until='domcontentloaded', timeout=30000)
                            wait_workspace(page, timeout=45000)
                            first_identity=page.evaluate('''() => {window.__tpQAFrozenLayout=customElements.get('regular-layout'); return !!window.__tpQAFrozenLayout;}''')
                            assert first_identity, 'Perspective regular-layout was not registered'
                            errors_before=len(journal.document['errors'])
                            cycle_rows=[]
                            for i in range(bi_cycles):
                                for kind,path in [('feature',feature_target),('source',source_target)]:
                                    # In-page route transition via the application's
                                    # real anchor-interception handler, no reload.
                                    page.evaluate('''target => {
                                      const link=document.createElement('a');link.href=target;
                                      link.textContent='QA SPA navigation';document.body.appendChild(link);
                                      link.click();link.remove();
                                    }''',path)
                                    page.wait_for_url(re.compile(re.escape(path.split('?')[0])),timeout=20000)
                                    if kind=='feature':
                                        page.wait_for_function('''argument =>
                                          window.TimePilotFeaturePerspective?.getState?.()?.ready===true &&
                                          window.TimePilotFeaturePerspective.getState().activeKey?.split('|')[3] === argument &&
                                          !!document.querySelector('#tp-fl2-perspective perspective-viewer')''',
                                          arg=art['artifact_id'],timeout=45000)
                                        receipt=page.evaluate('''async () =>
                                          await window.TimePilotFeaturePerspective.captureReceipt()''')
                                        assert receipt['panel_count']>=2, f'cycle {i}: feature panel missing'
                                        assert page.evaluate('''() =>
                                          customElements.get('regular-layout')''') is not None
                                        cycle_rows.append({'cycle':i+1,'kind':'feature',
                                                           'panels':receipt['panel_count']})
                                    else:
                                        current=wait_workspace(page,timeout=45000)
                                        assert current.get('panels'), f'cycle {i}: source empty'
                                        cycle_rows.append({'cycle':i+1,'kind':'source',
                                                           'panels':len(current['panels'])})
                                    # Identity equality is evaluated inside Chrome:
                                    # Playwright JSHandle equality in Python is not
                                    # a reliable comparison of CustomElement objects.
                                    assert page.evaluate('''() =>
                                        customElements.get('regular-layout') === window.__tpQAFrozenLayout''')
                                if (i+1)%5==0:
                                    item['completed_cycles']=i+1
                                    journal.save()
                            new_errors=journal.document['errors'][errors_before:]
                            critical=[e for e in new_errors if ('already been used with this registry' in e or
                                       'Generic Websocket Error' in e or 'pageerror:' in e)]
                            assert not critical, {'unexpected_bi_errors':critical}
                            item.update(completed_cycles=bi_cycles,steps=cycle_rows,
                                        no_duplicate_custom_elements=True,
                                        checked_same_spa_document=True)
                            screenshot('07-feature-bi-switches')
                    with journal.step('real_perspective_websocket_source_and_refresh') as item:
                        native_url = base+f'/projects/{pid}/bi/native/{aid}'
                        page.goto(native_url, wait_until='domcontentloaded', timeout=30000)
                        before = wait_workspace(page, timeout=45000)
                        assert (before.get('panels') or {}), 'No real Perspective panel'
                        sockets_before = len(journal.document['websockets'])
                        page.reload(wait_until='domcontentloaded', timeout=30000)
                        restored = wait_workspace(page, timeout=45000)
                        assert restored.get('panels'), 'BI state not restored after browser refresh'
                        assert len(journal.document['websockets']) > sockets_before, 'No new WebSocket after refresh'
                        item.update(panel_count=len(restored['panels']),real_websocket=True,
                                    source_parquet_sha256=app.state.native_bi.verify(aid)['actual_sha256'])
                        screenshot('06-native-bi')
                    with journal.step('report_archive_download_and_browser_sha256') as item:
                        report_url = url + '&tool=reports'
                        page.goto(report_url, wait_until='domcontentloaded')
                        page.locator('[data-testid="unified-delivery"]').wait_for(timeout=40000)
                        page.locator('[data-delivery="archive"]').click()
                        reports_url = prefix+'/feature-lab/analysis/sessions/'+sid+'/reports'
                        reports = await_json(http,reports_url,lambda d: len(d['reports']) >= 1,seconds=30)
                        rec = reports['reports'][0]
                        data = http.get(prefix+'/feature-lab/reports/'+rec['report_id'])
                        data.raise_for_status();archived = data.json()
                        digest = report_integrity(archived['markdown'],archived['report_sha256'])
                        assert archived['manifest']['source_sha256'] == status['source_sha256']
                        assert archived['verified'] and archived['immutable']
                        page.locator('[data-delivery="download-archive"]').first.wait_for(timeout=15000)
                        with page.expect_download(timeout=25000) as dl:
                            page.locator('[data-delivery="download-archive"]').first.click()
                        download = dl.value
                        dest = folder / 'verified_report.md'
                        download.save_as(str(dest))
                        assert digest_file(dest) == digest
                        item.update(report_id=rec['report_id'],report_sha256=digest,
                                    downloaded_bytes=dest.stat().st_size)
                        screenshot('07-report')
                    with journal.step('invalid_source_explained_and_can_return_to_data') as item:
                        page.goto(base+f'/projects/{pid}/analysis?tool=reports&asset=missing-v0365&version=base',
                                  wait_until='domcontentloaded')
                        alert = page.locator('#tp-analysis-delivery [role="alert"]')
                        alert.wait_for(timeout=20000)
                        assert '不存在' in alert.inner_text() or '无权限' in alert.inner_text()
                        link = alert.locator('a[href$="/data"]')
                        assert link.count() == 1
                        link.click()
                        page.wait_for_url(re.compile(r'/projects/[^/]+/data$'), timeout=20000)
                        item['recoverable_error_ui'] = True
                        screenshot('08-error-recovery')
                    with journal.step('no_unexpected_frontend_errors') as item:
                        errors = journal.document['errors']
                        severe = [x for x in journal.document['http_errors'] if x['status'] >= 500]
                        assert not errors and not severe, {'errors':errors,'http_5xx':severe}
                        item.update(page_errors=len(errors),http_5xx=len(severe))
                context.close()
            finally:
                browser.close()
    finally:
        server.should_exit = True
        worker.join(timeout=15)


def main(argv=None):
    parser=argparse.ArgumentParser()
    parser.add_argument('--out',type=Path,default=Path('reports/v0365/chrome_journey.json'))
    parser.add_argument('--timeout',type=int,default=TIMEOUT)
    parser.add_argument('--bi-cycles',type=int,default=0,help='Same-document source/feature BI round trips')
    args=parser.parse_args(argv)
    if not 0<=args.bi_cycles<=60:parser.error('--bi-cycles must be between 0 and 60')
    out=args.out.expanduser().resolve()
    journal=Journal(out)
    if args.bi_cycles:journal.document['release']='0.37.1';journal.save()
    try:
        with tempfile.TemporaryDirectory(prefix='timepilot_365_qa_') as scratch:
            scratch=Path(scratch)
            with journal.step('frozen_input_oracle') as step:
                fixture=verify_fixtures(scratch/'fixture')
                step.update({k:v for k,v in fixture.items() if k!='original_rows' and k!='csv'})
            with journal.step('real_physical_environment') as step:
                missing=required_modules()
                if missing:raise Blocked('Missing physical modules: '+', '.join(missing))
                from timepilot.native_bi_bundle import bundle_status,frontend_matches_bundle
                bundle=ROOT/'src/timepilot/static/native-bi'
                info=bundle_status(bundle)
                if not info.get('ready') or not frontend_matches_bundle(bundle,ROOT/'frontend/native-bi'):
                    raise Blocked('Compiled matching Perspective JS/WASM bundle is missing. Build using scripts/build_native_bi.py on a connected builder.')
                from system_chrome import chrome_executable
                try:
                    path=chrome_executable()
                except Exception as exc:
                    raise Blocked(str(exc)) from exc
                step['installed_google_chrome']=require_actual_google_chrome(path)
                step['native_bundle_verified']=True
            with journal.step('isolated_application_bootstrap') as step:
                from timepilot.api import create_app
                runtime=scratch/'application'
                app=create_app(home=runtime,embedded_worker=True)
                step['test_home_isolated']=True
            run_browser(app,fixture,journal,scratch,max(40,args.timeout),bi_cycles=args.bi_cycles)
    except Blocked as exc:
        journal.finish('blocked',exc)
    except Exception as exc:
        journal.finish('failed',exc)
    else:
        journal.finish('passed')
    print(json.dumps({'status':journal.document['status'],'report':str(out),
        'completed':sum(x['status']=='passed' for x in journal.document['steps']),
        'blocked':[x['stage'] for x in journal.document['steps'] if x['status']=='blocked'],
        'failed':[x['stage'] for x in journal.document['steps'] if x['status']=='failed']},ensure_ascii=False),flush=True)
    return 0 if journal.document['status']=='passed' else 2 if journal.document['status']=='blocked' else 1

if __name__=='__main__':
    raise SystemExit(main())
