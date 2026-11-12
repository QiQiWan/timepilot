"""Release-level regression for UI parity, dependency manifest and data template."""
from pathlib import Path

from fastapi.testclient import TestClient

from timepilot.api import create_app
from timepilot import __version__

ROOT = Path(__file__).resolve().parents[1]


def test_release_bundle_and_requirement_sync():
    import importlib.util
    import subprocess
    import sys

    # The version-manager hint is informational; runtime accepts the active Python 3.12 patch.
    assert '--require-runtime' in (ROOT / 'scripts/launcher.py').read_text(encoding='utf-8')
    hint = ROOT / '.python-version'
    if hint.is_file():
        assert hint.read_text(encoding='utf-8').strip() == '3.12.12'
    assert 'requires-python = ">=3.12,<3.13"' in (ROOT / 'pyproject.toml').read_text(encoding='utf-8')
    assert (ROOT / 'requirements.txt').exists()
    result = subprocess.run([sys.executable, str(ROOT / 'scripts/sync_requirements.py'), '--check'],
                            capture_output=True, text=True, cwd=ROOT)
    assert result.returncode == 0, result.stderr
    text = (ROOT / 'requirements.txt').read_text(encoding='utf-8')
    assert 'fastapi' in text and 'pyarrow' in text and 'openpyxl' in text
    assert '-e .' in text and 'autogluon.timeseries==1.6.1' in text
    assert 'duckdb' in text and 'cryptography' in text
    assert {p.name for p in ROOT.glob('requirements*.txt')} == {'requirements.txt', 'requirements-featurelab-advanced.txt'}
    assert 'tsfresh' in (ROOT / 'requirements-featurelab-advanced.txt').read_text()
    assert (ROOT / 'src/timepilot/static/experience.css').is_file()
    assert f'v={__version__}' in (ROOT / 'src/timepilot/static/index.html').read_text()


def test_ui_static_assets_not_stale_and_template_download(tmp_path, monkeypatch):
    monkeypatch.delenv('TIMEPILOT_DB_URL', raising=False)
    with TestClient(create_app(home=tmp_path / 'sandbox', embedded_worker=False)) as http:
        health = http.get('/api/v1/health')
        assert health.status_code == 200
        assert health.json()['ui_version'] == __version__
        html = http.get('/')
        assert html.status_code == 200
        assert f'/assets/experience.css?v={__version__}' in html.text
        assert f'/assets/app.js?v={__version__}' in html.text
        assert f'/assets/project-bi-ui.js?v={__version__}' in html.text
        assert f'/assets/project-ui.js?v={__version__}' in html.text
        assert '/assets/bi-ui.js' not in html.text
        assert '/assets/data-assets-ui.js' not in html.text
        assert f'/assets/settings-ui.js?v={__version__}' in html.text
        assert html.headers['cache-control'].startswith('no-store')
        for filename in ('styles.css', 'experience.css', 'bi-settings.css', 'project-bi-ui.js', 'project-ui.js', 'settings-ui.js', 'app.js'):
            response = http.get('/assets/' + filename + '?v={__version__}')
            assert response.status_code == 200
            assert response.headers['x-timepilot-ui-version'] == __version__
            assert response.headers['cache-control'].startswith('no-store')
        template = http.get('/api/v1/templates/standard-timeseries.csv')
        assert template.status_code == 200
        assert template.text.lstrip('\ufeff').startswith('series_id,timestamp,target,available_at')
        assert 'filename="timepilot_standard_template.csv"' in template.headers['content-disposition']


def test_real_frontend_contains_connection_error_and_chinese_labels():
    js = (ROOT / 'src/timepilot/static/app.js').read_text(encoding='utf-8')
    assert 'apiError' in js
    assert '连接异常' in js
    assert '重新连接' in js
    assert '季节性基线' in js
    assert '查看更早的' in js
    assert '/api/v1/templates/standard-timeseries.csv' in (ROOT / 'src/timepilot/api.py').read_text(encoding='utf-8')
    assert '/projects/' in js


def test_missing_favicon_does_not_raise_browser_404(tmp_path):
    from fastapi.testclient import TestClient
    from timepilot.api import create_app
    with TestClient(create_app(home=tmp_path, embedded_worker=False)) as client:
        icon=client.get('/favicon.ico')
        assert icon.status_code == 204
        assert 'rel="icon"' in client.get('/bi/native').text


def test_native_bi_missing_virtual_capability_is_explicit(tmp_path):
    from timepilot.api import create_app
    from fastapi.testclient import TestClient
    with TestClient(create_app(home=tmp_path, embedded_worker=False)) as client:
        capabilities=client.get('/api/v1/native-bi/capabilities').json()
        assert isinstance(capabilities['perspective_virtual_server_ready'], bool)
        assert 'perspective_virtual_server_error' in capabilities
        ui=client.get('/assets/native-bi.js').text
        assert 'perspective_virtual_server_error' in ui
        assert "setPerspectiveState(visualization,'error'" in ui
