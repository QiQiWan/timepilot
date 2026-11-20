"""Frozen, independent CSV oracles for long-lived TimePilot regression QA.

No app database, DuckDB, sklearn or LLM is used here. The baseline shipped in
benchmarks/featurelab_v0362 is reviewed separately from fixture generation.
Any change to the deterministic corpus requires an explicit reviewed baseline
revision. A generator writing a new manifest alone is never sufficient.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import csv
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
from typing import Any

from .featurelab361_fixtures import SCENARIOS, digest_file

BASELINE_PATH = Path(__file__).resolve().parents[2] / 'benchmarks' / 'featurelab_v0362' / 'BASELINE.json'
BASELINE_SCHEMA = 1


def _read(path: Path) -> list[dict[str, str]]:
    with path.open('r', encoding='utf-8', newline='') as handle:
        return list(csv.DictReader(handle))


def _timed(row: dict, key: str) -> datetime:
    return datetime.fromisoformat(row[key])


def independent_reference(folder: Path) -> dict[str, Any]:
    """Calculate expected values without calling the generator or app engines."""
    folder = Path(folder)
    result: dict[str, Any] = {'schema_version': BASELINE_SCHEMA, 'datasets': {}}
    for name in sorted(SCENARIOS):
        path = folder / (name + '.csv')
        rows = _read(path)
        result['datasets'][name] = {
            'sha256': digest_file(path), 'rows': len(rows),
            'columns': list(rows[0]) if rows else [],
        }
    dirty = _read(folder / 'dirty_import.csv')
    result['quality_oracle'] = {
        'missing_temperature': sum(x['temperature_c'] == '' for x in dirty),
        'invalid_datetime': sum(x['event_timestamp'] == 'NOT_A_TIMESTAMP' for x in dirty),
        'out_of_range_temperature': sum(x['temperature_c'] == '99999.0' for x in dirty),
        'duplicate_sample_ids': len(dirty) - len({x['sample_id'] for x in dirty}),
    }
    series = _read(folder / 'industrial_pit.csv')
    devices = sorted({r['equipment_id'] for r in series})
    groups = defaultdict(list)
    for row in series:
        groups[row['equipment_id']].append(row)
    result['industrial_oracle'] = {
        'devices': devices,
        'positives': sum(int(r['quality_label']) for r in series),
        'valid_measurement_order': all(_timed(r, 'event_timestamp') <= _timed(r, 'measurement_available_at') <=
            _timed(r, 'prediction_timestamp') < _timed(r, 'label_available_at') for r in series),
        'regime_runs': {}, 'maintenance_counts': {}, 'late_measurement_rows': sum(
            _timed(r, 'measurement_available_at') > _timed(r, 'prediction_timestamp')
            for r in _read(folder / 'industrial_late_measurement.csv')),
        'early_label_rows': sum(_timed(r, 'label_available_at') < _timed(r, 'prediction_timestamp')
            for r in _read(folder / 'industrial_early_label.csv')),
    }
    for device, rows in sorted(groups.items()):
        rows.sort(key=lambda r: (r['event_timestamp'], r['row_id']))
        runs = [rows[0]['operating_regime']]
        for row in rows[1:]:
            if row['operating_regime'] != runs[-1]:
                runs.append(row['operating_regime'])
        result['industrial_oracle']['regime_runs'][device] = runs
        result['industrial_oracle']['maintenance_counts'][device] = sum(
            int(r['maintenance_event']) for r in rows)
    events = _read(folder / 'maintenance_events.csv')
    result['industrial_oracle']['late_maintenance_count'] = sum(
        _timed(r, 'available_at') > _timed(r, 'event_timestamp') + timedelta(minutes=2)
        for r in events)
    # Match the feature chart's equal-count bucket contract, independent of SQL.
    # This is a numeric oracle, not evidence that the native chart rendered.
    charts = {}
    for device in (devices[0], devices[-1]):
        rows = groups[device]
        bins = []
        for i in range(12):
            selected = rows[i * len(rows) // 12:(i + 1) * len(rows) // 12]
            readings = [float(r['sensor_value_a']) for r in selected]
            bins.append({'n': len(readings), 'mean': round(sum(readings) / len(readings), 6),
                         'min': round(min(readings), 5), 'max': round(max(readings), 5),
                         'first': selected[0]['event_timestamp'],
                         'last': selected[-1]['event_timestamp']})
        charts[device] = bins
    result['chart_oracle'] = {'resolution': 12, 'measure': 'sensor_value_a', 'devices': charts}
    binary = _read(folder / 'static_binary.csv')
    result['binary_oracle'] = {'positive_count': sum(int(r['defect_label']) for r in binary),
        'leak_trap_equals_label': all(r['defect_label'] == r['post_inspection_rework__LEAKAGE_TRAP'] for r in binary)}
    unlabeled = _read(folder / 'sensor_unlabeled.csv')
    result['unlabeled_oracle'] = {'no_label_column': not any('label' in c.casefold() or 'target' in c.casefold()
        for c in unlabeled[0])}
    return result


def compare_baseline(actual: Any, expected: Any, prefix: str = '') -> list[str]:
    """Exact comparison on pinned oracles; cap diagnostic output for safe reports."""
    errors: list[str] = []
    if isinstance(expected, dict) and isinstance(actual, dict):
        if set(actual) != set(expected):
            errors.append(f'{prefix or "root"}: keys differ ({sorted(set(expected) ^ set(actual))})')
        for key in sorted(set(expected) & set(actual)):
            errors.extend(compare_baseline(actual[key], expected[key], f'{prefix}.{key}' if prefix else key))
    elif isinstance(expected, list) and isinstance(actual, list):
        if len(expected) != len(actual):
            errors.append(f'{prefix}: list length differs ({len(actual)} vs {len(expected)})')
        for i in range(min(len(expected), len(actual))):
            errors.extend(compare_baseline(actual[i], expected[i], f'{prefix}[{i}]'))
    elif actual != expected:
        errors.append(f'{prefix}: expected {expected!r}, observed {actual!r}')
    return errors[:100]


def verify_frozen_baseline(folder: Path, baseline: Path = BASELINE_PATH) -> dict[str, Any]:
    try:
        expected = json.loads(Path(baseline).read_text(encoding='utf-8'))
        actual = independent_reference(Path(folder))
        errors = compare_baseline(actual, expected)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        errors = [f'{type(exc).__name__}: {exc}']
        actual = None
    return {'status': 'passed' if not errors else 'failed',
            'baseline_path': str(baseline),
            'baseline_sha256': digest_file(Path(baseline)) if Path(baseline).is_file() else None,
            'observed': actual, 'errors': errors}
