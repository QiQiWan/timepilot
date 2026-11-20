"""Physical chart parity on the frozen synthetic CSV, never silently skipped."""
from datetime import datetime
from pathlib import Path

import pytest

duckdb = pytest.importorskip('duckdb', reason='DuckDB physical QA is not installed')
from timepilot.featurelab361_fixtures import generate_suite
from timepilot.featurelab362_reference import independent_reference
from timepilot.feature_chart_semantics import series_aggregate


def test_source_bound_real_chart_matches_full_interval_reference(tmp_path):
    folder=tmp_path/'qa'
    generate_suite(folder)
    expected=independent_reference(folder)['chart_oracle']['devices']
    con=duckdb.connect(':memory:')
    con.execute('''CREATE TABLE data AS SELECT ROW_NUMBER() OVER () AS __tp_rowid,
        equipment_id,event_timestamp, TRY_CAST(sensor_value_a AS DOUBLE) AS v
        FROM read_csv_auto(?, HEADER=TRUE, ALL_VARCHAR=TRUE)''', [str(folder/'industrial_pit.csv')])
    for device, frozen in expected.items():
        payload=series_aggregate(con,'data',order_column='event_timestamp',
            value_expression='v',original_expression='v',
            entity_column='equipment_id',entity_value=device,resolution=12)
        assert payload['population_count']==360
        assert payload['partial'] is False
        assert len(payload['points'])==12
        for point,oracle in zip(payload['points'],frozen):
            assert point['count']==oracle['n']
            assert point['min']==pytest.approx(oracle['min'],abs=1e-5)
            assert point['max']==pytest.approx(oracle['max'],abs=1e-5)
            assert point['value']==pytest.approx(oracle['mean'],abs=1e-6)
            assert datetime.fromisoformat(point['x']) == datetime.fromisoformat(oracle['first'])
            assert datetime.fromisoformat(point['x_end']) == datetime.fromisoformat(oracle['last'])
