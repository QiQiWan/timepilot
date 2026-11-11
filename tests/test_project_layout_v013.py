"""Project import, workbook structure and strict offline BI entry regressions."""
from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient
from openpyxl import Workbook

from timepilot.api import create_app
from timepilot.workbook_intelligence import WorkbookIntelligence


ROOT = Path(__file__).resolve().parents[1]


def complex_xlsx():
    book = Workbook()
    desc = book.active
    desc.title = '说明'
    desc.append(['销售资料说明', '注意：数据不是在此表'])
    ws = book.create_sheet('多层表头')
    ws.merge_cells('A1:B1')
    ws['A1'] = '型号信息'
    ws['C1'] = '销售数据'
    ws.append(['国家', '车型', '销量'])
    ws.append(['中国', 'A2', 10])
    ws.append(['中国', 'A14', '=3+7'])
    ws.row_dimensions[4].hidden = True
    hidden = book.create_sheet('内部记录')
    hidden.sheet_state = 'hidden'
    hidden.append(['private', 'value'])
    with io.BytesIO() as buffer:
        book.save(buffer)
        return buffer.getvalue()


def test_layout_hints_audited_and_bounded(tmp_path):
    app = create_app(home=tmp_path, embedded_worker=False)
    with TestClient(app) as client:
        project = client.post('/api/v1/projects', json={'name': '样本结构评估'}).json()['id']
        response = client.post(f'/api/v1/projects/{project}/imports', files=[
            ('files', ('merges.xlsx', complex_xlsx(), 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'))
        ])
        assert response.status_code == 201, response.text
        iid = response.json()['import_id']
        result = client.get(f'/api/v1/projects/{project}/imports/{iid}/layout')
        assert result.status_code == 200, result.text
        payload = result.json()
        assert payload['scope'] == 'structure_and_sample'
        assert payload['full_statistics_ready'] is False
        sheets = payload['files'][0]['sheets']
        multiple = next(x for x in sheets if x['name'] == '多层表头')
        assert 'A1:B1' in multiple['merged_ranges_sample']
        assert multiple['formula_count_first_85_rows'] >= 1
        assert multiple['review_required']
        assert any(s['name'] == '内部记录' and s['hidden'] for s in sheets)
        assert client.get(f'/api/v1/projects/{project}/imports/{iid}/profile').json()['scope'] == 'structure_sample'
        assert client.get(f'/api/v1/projects/{project}/imports/{iid}/layout').json() == payload


def test_project_import_layout_scope_not_cross_project(tmp_path):
    app = create_app(home=tmp_path, embedded_worker=False)
    with TestClient(app) as client:
        p = [client.post('/api/v1/projects', json={'name': f'项目-{x}'}).json()['id'] for x in range(2)]
        upload = client.post(f'/api/v1/projects/{p[0]}/imports', files=[
            ('files', ('book.xlsx', complex_xlsx(), 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'))
        ]).json()
        assert client.get(f'/api/v1/projects/{p[1]}/imports/{upload["import_id"]}/layout').status_code == 404
        assert client.get(f'/api/v1/projects/{p[1]}/imports/{upload["import_id"]}/profile').status_code == 404


def test_offline_startup_does_not_bootstrap_npm(monkeypatch):
    # The startup gate must never invoke npm installation or reach the network.
    import importlib.util
    spec = importlib.util.spec_from_file_location('ensure_native_bi_test', ROOT/'scripts/ensure_native_bi.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'readiness', lambda: ({'ready': False, 'problems': ['missing built JS/WASM']}, False, ['duckdb'], False))
    monkeypatch.setattr(module.shutil, 'which', lambda x: '/bin/npm' if x == 'npm' else None)
    monkeypatch.setattr(sys, 'argv', ['ensure_native_bi.py', '--auto-build'])
    assert module.main() == 2


def test_standalone_launcher_does_not_provision_env():
    shell = (ROOT/'TIMEPILOT.sh').read_text()
    launcher = (ROOT/'scripts/launcher.py').read_text()
    ensure = (ROOT/'scripts/ensure_native_bi.py').read_text()
    assert 'exec python ' in shell
    assert "'--auto-build'" in launcher
    assert "'TIMEPILOT_SKIP_NPM_INSTALL'" in ensure
    forbidden = ('conda create', 'python -m venv', 'pip install', 'npm install')
    for text in (shell, launcher):
        assert all(term not in text for term in forbidden)
