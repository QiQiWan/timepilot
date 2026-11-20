"""v0.36.5 offline acceptance contract; actual browser run is a separate physical gate."""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from featurelab365_chrome_journey import (
    Blocked, Journal, DEPENDENCIES, report_integrity,
    require_actual_google_chrome, verify_fixtures,
)

def test_fixed_frozen_input_is_reproducible(tmp_path):
    first=verify_fixtures(tmp_path / 'first')
    second=verify_fixtures(tmp_path / 'second')
    assert first['row_count']==1200
    assert first['source_csv_sha256']==second['source_csv_sha256']
    assert first['baseline_sha256']==second['baseline_sha256']
    assert 'sensor_a' in first['original_rows'][0]

def test_no_chromium_fallback():
    with pytest.raises(Blocked, match='Google Chrome required'):
        require_actual_google_chrome('/usr/bin/chromium')
    assert require_actual_google_chrome('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome').endswith('Google Chrome')
    assert require_actual_google_chrome('C:/Program Files/Google/Chrome/Application/chrome.exe').endswith('chrome.exe')

def test_report_sha256_rejects_tampering():
    sha=hashlib.sha256('报告已保存'.encode()).hexdigest()
    assert report_integrity('报告已保存',sha)==sha
    with pytest.raises(AssertionError,match='SHA256'):
        report_integrity('报告被修改',sha)

def test_step_journal_fail_closed(tmp_path):
    doc=Journal(tmp_path/'receipt.json')
    with doc.step('known_pass') as row:
        row['rows']=1200
    with pytest.raises(Blocked):
        with doc.step('offline_physical'):
            raise Blocked('Chrome and Parquet needed')
    doc.finish('blocked')
    saved=json.loads(doc.path.read_text())
    assert saved['status']=='blocked' and not saved['browser_acceptance']
    assert [x['status'] for x in saved['steps']]==['passed','blocked']
    assert saved['steps'][0]['rows']==1200

def test_executable_records_environment_failure_not_false_pass(tmp_path):
    from featurelab365_chrome_journey import required_modules
    if not required_modules():
        pytest.skip('Complete physical Chrome journey runs in the dedicated CI browser job')
    out=tmp_path/'chrome.json'
    process=subprocess.run([sys.executable,str(ROOT/'scripts/featurelab365_chrome_journey.py'),
        '--out',str(out)],capture_output=True,text=True,timeout=90)
    doc=json.loads(out.read_text())
    assert doc['status'] in ('blocked','passed')
    assert (process.returncode==0)==(doc['status']=='passed')
    assert doc['browser_acceptance']==(doc['status']=='passed')
    assert doc['steps'][0]['status']=='passed'
    assert (process.returncode==2)==(doc['status']=='blocked')
    assert len(DEPENDENCIES)>=7
