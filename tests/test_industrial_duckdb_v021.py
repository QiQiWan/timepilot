"""Live DuckDB / Parquet / Arrow integration for TimePilot industrial BI.

Skipped only in source-review environments missing optional compiled dependencies.
The CI/target-machine strict gate requires duckdb + pyarrow to be installed
before running this suite and separately exercises real Perspective in Chrome.
"""
from __future__ import annotations

import io
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient


def test_real_duckdb_industrial_views_and_arrow(tmp_path):
    duckdb = pytest.importorskip('duckdb')
    arrow = pytest.importorskip('pyarrow')
    from timepilot.api import create_app
    source = tmp_path / 'industrial_180.parquet'
    with duckdb.connect() as con:
        con.execute(f"""COPY (SELECT
            TIMESTAMP '2026-11-10 08:00:00' + i * INTERVAL '1 minute' AS observed_at,
            'MACHINE_07' AS equipment,
            23.0 + i * 0.02 AS temperature,
            CASE WHEN i=70 THEN 'filter_replaced' ELSE '' END AS maintenance,
            CASE WHEN i<90 THEN 'startup' ELSE 'production' END AS regime
            FROM range(180) t(i))
            TO '{source.as_posix()}' (FORMAT PARQUET)""")
    app = create_app(home=tmp_path / 'runtime', embedded_worker=False)
    native = app.state.native_bi
    imported = native.import_from_path(source, name='Real industrial data', lineage={'source': 'test_integration'})
    aid = imported['asset_id']
    with TestClient(app) as client:
        pid = client.post('/api/v1/projects', json={'name': 'Real DuckDB project'}).json()['id']
        other = client.post('/api/v1/projects', json={'name': 'Unrelated project'}).json()['id']
        with app.state.projects._db() as con:
            con.execute('INSERT INTO project_native_assets VALUES(?,?,?,?)',
                        (pid, aid, str(uuid4()), datetime.now(timezone.utc).isoformat()))
        spec = dict(asset_id=aid, version_id='base', template='maintenance_overlay',
                    time_field='observed_at', metric_field='temperature',
                    device_field='equipment', device_value='MACHINE_07',
                    event_field='maintenance', regime_field='regime', interval='1h')
        res = client.post(f'/api/v1/projects/{pid}/industrial/preview', json=spec)
        assert res.status_code == 200, res.text
        data = res.json()
        assert len(data['rows']) == 3
        assert sum(row['source_rows'] for row in data['rows']) == 180
        assert data['regime_mixed_buckets'] == 1
        assert data['has_row_truncation'] is False
        assert len(data['events']) == 1
        assert data['events'][0]['event'] == 'filter_replaced'
        assert data['source_sha256'] == imported['sha256']
        response = client.post(f'/api/v1/projects/{pid}/industrial/arrow', json=spec)
        assert response.status_code == 200, response.text[:200] if response.status_code != 200 else ''
        with arrow.ipc.open_stream(io.BytesIO(response.content)) as reader:
            table = reader.read_all()
        assert table.num_rows == 3
        assert table.column('source_rows').to_pylist() == [60, 60, 60]
        saved = client.post(f'/api/v1/projects/{pid}/industrial/views',
                            json={'name': 'maintenance timeseries', 'spec': spec})
        assert saved.status_code == 201, saved.text
        view_id = saved.json()['view_id']
        opened = client.get(f'/api/v1/projects/{pid}/industrial/views/{view_id}')
        assert opened.status_code == 200
        assert opened.json()['spec']['asset_id'] == aid
        assert client.get(f'/api/v1/projects/{other}/industrial/views/{view_id}').status_code == 404
        assert client.post(f'/api/v1/projects/{other}/industrial/preview', json=spec).status_code == 404
        assert native.asset(aid)['sha256'] == imported['sha256']
        assert native.verify(aid)['valid']
