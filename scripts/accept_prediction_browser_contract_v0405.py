"""Actual installed Chromium checks the shipped row-selection JS contract.

No Perspective runtime or real server is simulated as physically validated.
"""
from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / 'frontend/native-bi/src/prediction-selection.mjs'


def check(browser_path):
    from playwright.sync_api import sync_playwright
    evidence = {'status': 'in_progress', 'checks': [], 'mode': 'real_chromium_mock_cell_events',
                'native_perspective_5_websocket_tested': False,
                'physical_native_browser_release_passed': False,
                'browser_path': browser_path}
    html = '''<!doctype html><html><meta charset="utf-8"><body>
      <h1>Synthetic prediction row selection</h1>
      <button id="row">Row 42</button><button id="aggregate">Aggregated cell</button>
      <button id="invalid">Invalid ID</button><output id="choice">none</output>
      </body></html>'''
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, executable_path=browser_path,
            args=['--no-sandbox', '--disable-dev-shm-usage'])
        page = browser.new_page()
        exceptions = []
        page.on('pageerror', lambda x: exceptions.append(str(x)))
        page.set_content(html)
        js = MODULE.read_text(encoding='utf-8')
        page.evaluate('''async (source) => {
          const blob = new Blob([source], {type:'text/javascript'});
          const u = URL.createObjectURL(blob);
          try {
            const mod = await import(u);
            window.__testId = mod.predictionIdFromCell;
            const out = document.querySelector('#choice');
            document.querySelector('#row').onclick = () => {
              out.textContent = String(window.__testId({row:{prediction_row_id:42}}, {}));
            };
            document.querySelector('#aggregate').onclick = () => {
              out.textContent = String(window.__testId({row:{prediction_row_id:42}},
              {group_by:['series_id']}));
            };
            document.querySelector('#invalid').onclick = () => {
              out.textContent = String(window.__testId({row:{prediction_row_id:'0;DROP'}}, {}));
            };
          } finally { URL.revokeObjectURL(u); }
        }''', js)
        for button, expected in (('row', '42'), ('aggregate', 'null'), ('invalid', 'null')):
            page.locator('#' + button).click()
            actual = page.locator('#choice').inner_text()
            evidence['checks'].append({'action': button, 'expected': expected, 'actual': actual,
                                       'passed': actual == expected})
        evidence['checks'].append({'action': 'javascript_exceptions', 'passed': not exceptions,
                                   'exceptions': exceptions})
        evidence['status'] = 'passed_contract_only' if all(c['passed'] for c in evidence['checks']) else 'failed'
        browser.close()
    return evidence


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--chromium', default=shutil.which('chromium') or shutil.which('google-chrome') or '')
    parser.add_argument('--out', type=Path, default=Path('reports/v0405/chromium_row_selection.json'))
    args = parser.parse_args()
    output = args.out if args.out.is_absolute() else ROOT / args.out
    output.parent.mkdir(parents=True, exist_ok=True)
    if not args.chromium or not Path(args.chromium).is_file():
        report = {'status': 'blocked', 'reason': 'No installed Chromium or Chrome',
                  'native_perspective_5_websocket_tested': False}
        code = 2
    else:
        try:
            report = check(args.chromium)
            code = 0 if report['status'] == 'passed_contract_only' else 1
        except Exception as exc:
            report = {'status': 'failed', 'reason': f'{type(exc).__name__}: {exc}',
                      'native_perspective_5_websocket_tested': False}
            code = 1
    report['version'] = '0.40.5'
    report['utc'] = datetime.now(timezone.utc).isoformat()
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'status': report['status'], 'receipt': str(output)}, ensure_ascii=False))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
