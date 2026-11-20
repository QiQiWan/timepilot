"""Deterministic, streaming industrial evaluation fixture for source-only gates.

Synthetic ground truth is explicitly declared; these are NOT observed business
results. No global RNG, external model or network access is required.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .model_prediction_assets import FIELDS, _sha

TELEMETRY_FIELDS = (
    'source_row_id', 'device', 'batch_id', 'event_time_utc',
    'decision_time_utc', 'x_available_at_utc', 'label_ready_at_utc',
    'temperature_c', 'vibration_mm_s', 'machine_load', 'target_next_c',
    'defect_label', 'regime',
)
SCENARIOS = ('clean', 'future_x', 'premature_label')
MODELS = ('baseline', 'linear', 'forest')


def _iso(dt):
    return dt.isoformat(timespec='seconds')


def _row(i, *, seed, devices, step_minutes, scenario):
    if scenario not in SCENARIOS:
        raise ValueError('Unsupported synthetic scenario')
    device_index = i % devices
    slot = i // devices
    decision = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=slot * step_minutes)
    event = decision + timedelta(minutes=step_minutes)
    available = decision - timedelta(minutes=3)
    mature = event + timedelta(minutes=30 + (i % 9) * 15)
    if scenario == 'future_x' and i % 211 == 0:
        available = decision + timedelta(minutes=5)
    if scenario == 'premature_label' and i % 223 == 0:
        mature = event - timedelta(minutes=1)
    device = f'SMT-{device_index:02d}'
    batch = f'LOT-{slot // 30:06d}'
    load = round(0.35 + (i * 17 + seed * 11) % 61 / 100, 4)
    vibration = round(0.12 + (i * 31 + seed) % 90 / 100, 4)
    seasonal = 2.2 * math.sin(2 * math.pi * (slot % 144) / 144)
    late_regime = 1 if slot >= 1800 else 0
    device_bias = (device_index - devices / 2) * 0.18
    temp = round(28 + 7 * load + 2.4 * vibration + seasonal + device_bias, 6)
    target = round(temp + 0.8 * load + late_regime * 3.4 + (i % 11 - 5) * 0.027, 6)
    defect = 'NG' if (vibration > 0.78 or load > 0.89) and ((i + seed) % 3 != 0) else 'OK'
    source_sha = hashlib.sha256(f'{seed}|{i}|{device}|{_iso(event)}|{target}'.encode('ascii')).hexdigest()
    tel = {
        'source_row_id': f'{seed}-{i}', 'device': device, 'batch_id': batch,
        'event_time_utc': _iso(event), 'decision_time_utc': _iso(decision),
        'x_available_at_utc': _iso(available), 'label_ready_at_utc': _iso(mature),
        'temperature_c': temp, 'vibration_mm_s': vibration, 'machine_load': load,
        'target_next_c': target, 'defect_label': defect,
        'regime': 'shifted' if late_regime else 'normal',
    }
    predictions = []
    for model_index, model in enumerate(MODELS):
        base_error = {'baseline': 2.0, 'linear': 0.75, 'forest': 0.32}[model]
        # All models are evaluated on the SAME input row and fold.
        error = round(base_error + (i % 9) * 0.07 + (1.1 if late_regime else 0)
                      + (0.85 if device_index == devices - 1 else 0), 6)
        signed_error = error * (-1 if i % 2 else 1)
        prediction = round(target + signed_error, 6)
        predictions.append({
            '__tp_rowid': i * len(MODELS) + model_index,
            'model': model, 'fold': slot % 3 + 1,
            'source_row_sha256': source_sha, 'source_row_index': i, 'series_id': device,
            'event_time_utc': _iso(event), 'decision_time_utc': _iso(decision),
            'label_mature_utc': _iso(mature), 'month': event.strftime('%Y-%m'),
            'actual': target, 'prediction': prediction,
            'residual': round(prediction - target, 6), 'absolute_error': error,
            'predicted_label': 1 if prediction > 35 else 0,
            'actual_label': 1 if defect == 'NG' else 0,
        })
    return tel, predictions



def generate(output_dir, *, rows=10000, seed=43, devices=8, step_minutes=15,
             scenario='clean', include_telemetry=True):
    """Create signed CSV datasets in O(1) extra memory; return ground truth.

    Every source observation is compared across all candidate models under
    the same temporal fold. The source hash is fixture-specific and does not
    claim equivalence with actual TimePilot upload-row hashing.
    """
    if not isinstance(rows, int) or not 16 <= rows <= 1_000_000 // len(MODELS):
        raise ValueError('Synthetic observations must fit in 1,000,000 prediction rows')
    if not 2 <= devices <= 32 or step_minutes < 1 or step_minutes > 1440:
        raise ValueError('Invalid device count or cadence')
    if scenario not in SCENARIOS or not isinstance(seed, int) or seed < 0:
        raise ValueError('Invalid scenario or seed')
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    telemetry = out / 'industrial_telemetry.csv'
    predictions = out / 'validation_predictions.csv'
    x_late = 0
    label_invalid = 0
    shift_rows = 0
    by_model = {name: 0 for name in MODELS}
    with predictions.open('w', newline='', encoding='utf-8') as dst:
        pw = csv.DictWriter(dst, fieldnames=FIELDS)
        pw.writeheader()
        if include_telemetry:
            src = telemetry.open('w', newline='', encoding='utf-8')
            tw = csv.DictWriter(src, fieldnames=TELEMETRY_FIELDS)
            tw.writeheader()
        else:
            src = None
        try:
            for i in range(rows):
                tel, emitted = _row(i, seed=seed, devices=devices,
                                 step_minutes=step_minutes, scenario=scenario)
                for pred in emitted:
                    pw.writerow(pred)
                    by_model[pred['model']] += 1
                if src is not None:
                    tw.writerow(tel)
                x_late += tel['x_available_at_utc'] > tel['decision_time_utc']
                label_invalid += tel['label_ready_at_utc'] < tel['event_time_utc']
                shift_rows += tel['regime'] == 'shifted'
        finally:
            if src is not None:
                src.close()
    manifest = {
        'schema': 'timepilot-synthetic-industrial-v1', 'synthetic_only': True,
        'ground_truth_not_real_operational_data': True,
        'model_predictions_simulated_not_fitted': True,
        'generator_seed': seed, 'rows': rows, 'prediction_rows': rows * len(MODELS),
        'model_comparison_same_source_rows_and_folds': True, 'devices': devices,
        'step_minutes': step_minutes, 'scenario': scenario,
        'future_x_rows': x_late, 'invalid_label_rows': label_invalid,
        'regime_shift_rows': shift_rows, 'model_rows': by_model,
        'telemetry_csv_sha256': _sha(telemetry) if include_telemetry else None,
        'predictions_csv_sha256': _sha(predictions),
    }
    (out / 'synthetic_manifest.json').write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    return manifest
