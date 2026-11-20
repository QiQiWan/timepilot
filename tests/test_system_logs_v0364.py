"""Physical structured diagnostics and admin route behavior."""
from __future__ import annotations
import json
import logging
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from timepilot.system_logs import read_logs, setup_logging, redact
from timepilot.system_logs_api import create_logs_router


def test_redaction_filters_and_pagination(tmp_path):
    setup_logging(tmp_path)
    log=logging.getLogger('timepilot.featurelab364')
    log.info('calculation finished; api_key=DO_NOT_EXPOSE')
    log.warning('materialization failed; access_token=SECRET_VALUE')
    files=list((tmp_path/'logs').iterdir())
    assert any(x.name=='timepilot.log' for x in files)
    raw=(tmp_path/'logs/timepilot.log').read_text()
    assert 'DO_NOT_EXPOSE' not in raw and 'SECRET_VALUE' not in raw
    assert '[REDACTED]' in raw
    result=read_logs(tmp_path,level='WARNING',keyword='materialization',limit=1)
    assert result['matched']==1 and len(result['items'])==1
    assert result['items'][0]['source']=='timepilot.featurelab364'
    assert read_logs(tmp_path,level='WARNING',offset=1)['items']==[]
    with pytest.raises(ValueError):read_logs(tmp_path,level='TRACE')
    with pytest.raises(ValueError):read_logs(tmp_path,since='bad-date')
    with pytest.raises(ValueError):read_logs(tmp_path,since='2026-11-16')


def test_admin_diagnostic_endpoints_filter_and_download(tmp_path):
    setup_logging(tmp_path)
    logging.getLogger('timepilot.featurelab364').error('audit failure; password=SECRET')
    app=FastAPI()
    app.include_router(create_logs_router(tmp_path))
    client=TestClient(app)
    r=client.get('/api/v1/system/logs',params={'level':'ERROR','q':'audit failure'})
    assert r.status_code==200 and r.json()['matched']==1
    assert 'SECRET' not in str(r.json())
    for kind in ('txt','jsonl'):
        r=client.get('/api/v1/system/logs/download',params={'level':'ERROR','format':kind})
        assert r.status_code==200
        assert 'attachment' in r.headers['content-disposition']
        assert 'SECRET' not in r.text
        assert '[REDACTED]' in r.text
    assert client.get('/api/v1/system/logs',params={'limit':1000}).status_code==422
    assert client.get('/api/v1/system/logs/download',params={'format':'csv'}).status_code==422


def test_only_rotation_logs_accessible(tmp_path):
    setup_logging(tmp_path)
    (tmp_path/'logs'/'private.txt').write_text('SECRET')
    report=read_logs(tmp_path)
    assert all(x['name'].startswith('timepilot.log') for x in report['files'])
    assert 'private.txt' not in json.dumps(report)


def test_redact_json_secrets_and_bearer_token():
    raw = '{\"api_key\": \"DO_NOT_LEAK\", \"password\": \"not_public\"} Authorization: Bearer DO_NOT_SHOW'
    redacted = redact(raw)
    assert 'DO_NOT_LEAK' not in redacted and 'not_public' not in redacted and 'DO_NOT_SHOW' not in redacted
    assert '[REDACTED]' in redacted
