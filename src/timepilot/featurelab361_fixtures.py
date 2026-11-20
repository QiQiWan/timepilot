"""Deterministic, provenance-labeled functional QA datasets (v0.36.1).

Only standard-library CSV/JSON is needed. Synthetic sources NEVER become real
industrial evidence. No installation, network access, DB mutation, or use of
TIMEPILOT_HOME. A positive manifest is an input contract, not a model score.
"""
from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import random
import tempfile
import os
from typing import Any

SUITE_ID = 'timepilot-featurelab-qa-v1'
SEED = 3601007
UTC = timezone.utc
SCENARIOS = {
    'static_regression': ('Static regression with nonlinear interaction and post-outcome leak trap', 'regression'),
    'static_binary': ('Imbalanced binary quality label and downstream leakage trap', 'binary_classification'),
    'industrial_pit': ('Six devices, regimes, maintenance, availability and labels', 'binary_classification'),
    'sensor_unlabeled': ('Sensor exploration without outcome labels', 'exploration'),
    'dirty_import': ('Missing cells, duplicate IDs, invalid time strings and out-of-range values', 'quality'),
    'maintenance_events': ('Multiple events per device, including late-arriving maintenance', 'point_in_time_join'),
    'industrial_late_measurement': ('Intentional PIT violation: measurement arrives after decision', 'negative_pit'),
    'industrial_early_label': ('Intentional PIT violation: target visible before decision', 'negative_label'),
}
FIELD_MAP = {
    'provenance': 'INTERNALLY GENERATED SYNTHETIC DATA - fixed seed TimePilot QA; never production data',
    'task': 'binary_classification', 'timezone': 'UTC', 'models': 'linear',
    'event_time': 'event_timestamp', 'available_at': 'measurement_available_at',
    'prediction_time': 'prediction_timestamp', 'label_available': 'label_available_at',
    'target': 'quality_label', 'device': 'equipment_id',
    'regime': 'operating_regime', 'measure_a': 'sensor_value_a', 'measure_b': 'sensor_value_b',
}
BASE = datetime(2025, 1, 1, tzinfo=UTC)


def digest_file(path: Path) -> str:
    result = hashlib.sha256()
    with path.open('rb') as f:
        for part in iter(lambda: f.read(1 << 20), b''):
            result.update(part)
    return result.hexdigest()


def timestamp(t: datetime) -> str:
    return t.isoformat(timespec='seconds')


def random_for(name: str, seed: int) -> random.Random:
    hashed = hashlib.sha256((str(seed) + ':' + name).encode('ascii')).digest()
    return random.Random(int.from_bytes(hashed[:8], 'big'))


def _industrial(seed: int, count_per_device: int):
    r = random_for('industrial', seed)
    entries = []
    maintenance = []
    for d in range(6):
        device = 'EQ-' + str(d + 1).zfill(2)
        for i in range(count_per_device):
            when = BASE + timedelta(minutes=i * 15 + d)
            regime = ('A', 'B', 'A', 'C')[(i // 90) % 4]
            maintained = i in (75, 195, 315)
            # A→B→A represents different successive epochs.
            wear = i - max((j for j in (0, 75, 195, 315) if j <= i), default=0)
            burst = 14 if i in (113, 238, count_per_device - 3) else 0
            a = 22.0 + 0.075 * wear + (3.4 if regime == 'B' else -1.5 if regime == 'C' else 0) + r.gauss(0, 1.7) + burst
            b = 10.0 + 0.08 * wear + (2 if regime == 'C' else 0) + r.gauss(0, 1.3)
            temp = 35.0 + a / 7 + r.gauss(0, 0.35)
            pressure = 110.0 + 2.5 * b + r.gauss(0, 1.1)
            label = int((a > 42 and b > 20) or (a + 0.6 * b + r.gauss(0, 3.5) > 47) or i % 89 == 0)
            entries.append({
                'row_id': f'{device}-{i:04d}', 'equipment_id': device,
                'operating_regime': regime, 'event_timestamp': timestamp(when),
                'measurement_available_at': timestamp(when + timedelta(seconds=30)),
                'prediction_timestamp': timestamp(when + timedelta(minutes=2)),
                'label_available_at': timestamp(when + timedelta(hours=3)),
                'sensor_value_a': round(a, 5), 'sensor_value_b': round(b, 5),
                'temperature_c': round(temp, 5), 'pressure_kpa': round(pressure, 5),
                'maintenance_event': int(maintained),
                'future_quality_label__LEAKAGE_TRAP': label,
                'quality_label': label,
            })
            if maintained:
                # The 75/315 maintenance reports arrive in time, 195 is late.
                available = when + timedelta(seconds=45 if i != 195 else 1200)
                maintenance.append({'event_id': f'M-{device}-{i}', 'equipment_id': device,
                    'event_timestamp': timestamp(when), 'available_at': timestamp(available),
                    'event_type': 'service', 'reset_wear': 1,
                    'regime_after': regime})
    entries.sort(key=lambda row: (row['event_timestamp'], row['equipment_id']))
    return entries, maintenance


def _static_regression(seed: int, n: int):
    r = random_for('regression', seed)
    for i in range(n):
        area = r.uniform(35, 180)
        age = r.uniform(0, 35)
        rooms = max(1, int(area / 31 + r.gauss(0, .8)))
        district = ('north', 'west', 'east', 'south')[i % 4]
        noise = r.gauss(0, 6)
        price = 17 + area * 2.7 - 0.95 * age + 0.011 * area * area + rooms * 3 + (12 if district == 'north' else 0) + noise
        yield {'sample_id': f'R{i:05d}', 'area_m2': round(area, 4),
               'age_years': round(age, 3), 'rooms': rooms, 'district': district,
               'construction_cost': round(area * 1.3 + r.gauss(0, 6), 3),
               'noise_only': round(r.gauss(0, 1), 5),
               'post_sale_price__LEAKAGE_TRAP': round(price, 4),
               'price_target': round(price, 4)}


def _static_binary(seed: int, n: int):
    r = random_for('binary', seed)
    for i in range(n):
        flux = r.gauss(0, 1)
        offset = r.gauss(0, 1)
        vibration = r.gauss(0, 1)
        temp = 29 + r.gauss(0, 3)
        score = -3.45 + 0.75 * flux + 0.85 * offset + 0.35 * vibration + (0.85 if temp > 32 else 0)
        probability = 1 / (1 + math.exp(-score))
        defect = int(r.random() < probability)
        yield {'sample_id': f'C{i:05d}', 'batch':f'B{i//60:04d}',
               'solder_flux':round(flux, 4), 'placement_offset':round(offset, 4),
               'vibration':round(vibration, 4), 'temperature_c':round(temp, 4),
               'product_type':('A','B','C')[i%3],
               'post_inspection_rework__LEAKAGE_TRAP':defect,
               'defect_label':defect}


def _unlabeled(seed: int, n: int):
    r = random_for('unlabeled', seed)
    for i in range(n):
        when = BASE + timedelta(minutes=5*i)
        yield {'event_timestamp': timestamp(when), 'equipment_id':f'UN-{i%4:02d}',
               'regime':('A','B')[i%2], 'sensor_a': round(10 + math.sin(i/31) * 3 + r.gauss(0, 0.6), 5),
               'sensor_b':round(20 + math.cos(i/22) * 3 + r.gauss(0, 0.75), 5)}


def _dirty(seed: int, n: int):
    r = random_for('dirty', seed)
    rows=[]
    for i in range(n):
        date = timestamp(BASE+timedelta(minutes=5*i))
        v = round(20 + r.gauss(0, 2), 4)
        if i % 20 == 0:v = ''
        if i % 53 == 0: date = 'NOT_A_TIMESTAMP'
        if i % 67 == 0:v = 99999.0
        key = f'Q-{i-1:04d}' if i > 0 and i % 41 == 0 else f'Q-{i:04d}'
        rows.append({'sample_id':key,'event_timestamp':date,
            'equipment_id':f'Q-{i%3}', 'temperature_c':v,
            'line':('L1','L2')[i%2], 'constant_zero':0})
    return rows


def _write_csv(path: Path, rows, *, allow_overwrite: bool):
    iterator=iter(rows)
    first=next(iterator, None)
    if first is None:raise ValueError('empty fixture')
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('w', newline='', encoding='utf-8',
              prefix='.fixture-staging-',suffix='.csv',dir=path.parent,delete=False) as output:
        temporary=Path(output.name)
        try:
            writer=csv.DictWriter(output,fieldnames=list(first),lineterminator='\n')
            writer.writeheader(); writer.writerow(first)
            count=1
            for row in iterator:
                writer.writerow(row)
                count+=1
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
    if path.exists() and not allow_overwrite and digest_file(path)!=digest_file(temporary):
        temporary.unlink(missing_ok=True)
        raise ValueError(f'fixture changed: {path}; do not overwrite without --force')
    os.replace(temporary,path)
    return count, list(first)


def generate_suite(out: Path, *, seed: int = SEED, size: str='standard', force:bool=False) -> dict[str,Any]:
    """Generate deterministic test data, a gold manifest and an E2E field mapping."""
    if size not in ('small','standard'):raise ValueError('size must be small or standard')
    output=out.expanduser().resolve()
    if os.environ.get('TIMEPILOT_HOME') and output.is_relative_to(Path(os.environ['TIMEPILOT_HOME']).resolve()):
        raise ValueError('QA generation must not touch TIMEPILOT_HOME')
    output.mkdir(parents=True,exist_ok=True)
    n=360  # Preserve all three maintenance epochs for invariant tests.
    industrial,maintenance=_industrial(seed,n)
    late=[dict(row) for row in industrial]
    late[27]['measurement_available_at']=timestamp(datetime.fromisoformat(late[27]['prediction_timestamp'])+timedelta(minutes=60))
    early=[dict(row) for row in industrial]
    early[28]['label_available_at']=timestamp(datetime.fromisoformat(early[28]['event_timestamp']))
    fixture_rows={
      'static_regression':_static_regression(seed, 900 if size=='small' else 1800),
      'static_binary':_static_binary(seed, 1200 if size=='small' else 2400),
      'industrial_pit':industrial, 'sensor_unlabeled':_unlabeled(seed, 600 if size=='small' else 1200),
      'dirty_import':_dirty(seed, 250 if size=='small' else 500),
      'maintenance_events':maintenance,
      'industrial_late_measurement':late,
      'industrial_early_label':early,
    }
    cases={}
    for name,rows in fixture_rows.items():
        path=output/(name+'.csv')
        count,columns=_write_csv(path,rows,allow_overwrite=force)
        cases[name]={'file':path.name,'rows':count,'columns':columns,
           'source_sha256':digest_file(path),'task':SCENARIOS[name][1],
           'purpose':SCENARIOS[name][0],'provenance_kind':'synthetic',
           'expected':'reject' if name in ('industrial_late_measurement','industrial_early_label') else 'accept'}
    field_map=output/'industrial_field_map.json'
    field_bytes=(json.dumps(FIELD_MAP,ensure_ascii=False,indent=2)+'\n').encode()
    if field_map.exists() and not force and field_map.read_bytes()!=field_bytes:
        raise ValueError('field map changed; generate to a new output folder or use --force')
    field_map.write_bytes(field_bytes)
    manifest={'suite_id':SUITE_ID,'generator_seed':seed,'size':size,
      'source_type':'deterministic_synthetic','production_truth':False,
      'model_performance_proven':False,
      'recommended_qa':'functional_only',
      'warnings':['Leakage-trap columns MUST NEVER enter features',
          'The synthetic quality target is only a fixture; it is not a plant performance estimate'],
      'cases':cases,'field_map':field_map.name,'field_map_sha256':digest_file(field_map),
      'known_quality':{'dirty_missing_every':20,'dirty_bad_timestamp_every':53,
                       'dirty_outlier_every':67,'dirty_duplicate_every':41},
      'reference_rules':{'maintenance_reset_indices':[75,195,315],
         'late_maintenance_index':195,'regime_change_every':90,
         'measurement_available_before_prediction':True,'label_available_after_prediction':True}}
    # MANIFEST is overwritten only if its value still matches what deterministic
    # generation would produce (prevents silent edits to the QA oracle).
    old=output/'MANIFEST.json'
    new_bytes=(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n').encode()
    if old.exists() and not force and old.read_bytes()!=new_bytes:
        raise ValueError('manifest changed; verify or generate in a new folder')
    old.write_bytes(new_bytes)
    return manifest


def verify_suite(folder: Path) -> dict[str,Any]:
    """Independently verify SHA256, semantic consistency, known negatives and joins."""
    folder=folder.expanduser().resolve()
    manifest=json.loads((folder/'MANIFEST.json').read_text(encoding='utf-8'))
    errors=[];summary={}
    if manifest.get('suite_id')!=SUITE_ID or manifest.get('source_type')!='deterministic_synthetic':
        errors.append('invalid fixture suite provenance')
    field_map=folder/'industrial_field_map.json'
    if not field_map.is_file() or digest_file(field_map)!=manifest.get('field_map_sha256'):
        errors.append('field mapping SHA256 mismatch')
    for name,meta in manifest['cases'].items():
        if name not in SCENARIOS or meta.get('file') != name+'.csv':
            errors.append(f'{name}: unrecognized fixture path');continue
        path=folder/meta['file']
        if not path.is_file():errors.append(f'{name}: missing');continue
        if digest_file(path)!=meta['source_sha256']:
            errors.append(f'{name}: SHA256 mismatch'); continue
        with path.open(encoding='utf-8',newline='') as f:
            reader=csv.DictReader(f)
            rows=list(reader)
            if reader.fieldnames!=meta['columns'] or len(rows)!=meta['rows']:
                errors.append(f'{name}: row count or field schema mismatch')
        summary[name]={'rows':len(rows),'sha256':meta['source_sha256']}
    def load(name):
        with (folder/(name+'.csv')).open(newline='',encoding='utf-8') as f:
            return list(csv.DictReader(f))
    if not errors:
        rows=load('industrial_pit')
        if len(set(x['equipment_id'] for x in rows))!=6:
            errors.append('not six devices')
        if len(set(x['operating_regime'] for x in rows))<3:
            errors.append('missing operating regimes')
        if not all(datetime.fromisoformat(x['event_timestamp'])<=datetime.fromisoformat(x['measurement_available_at'])<=datetime.fromisoformat(x['prediction_timestamp'])<datetime.fromisoformat(x['label_available_at']) for x in rows):
            errors.append('PIT time order violated in valid dataset')
        if any(int(x['quality_label'])!=int(x['future_quality_label__LEAKAGE_TRAP']) for x in rows):
            errors.append('label leakage trap lost')
        if any(not any(int(x['quality_label'])==i for x in rows) for i in (0,1)):
            errors.append('no class balance variation')
        if not any(datetime.fromisoformat(x['measurement_available_at'])>datetime.fromisoformat(x['prediction_timestamp']) for x in load('industrial_late_measurement')):
            errors.append('late measurement fixture fails to violate PIT')
        if not any(datetime.fromisoformat(x['label_available_at'])<datetime.fromisoformat(x['prediction_timestamp']) for x in load('industrial_early_label')):
            errors.append('early label fixture fails to violate label availability')
        maintained=load('maintenance_events')
        if len(maintained)!=18 or not any(datetime.fromisoformat(x['available_at'])>datetime.fromisoformat(x['event_timestamp'])+timedelta(minutes=2) for x in maintained):
            errors.append('maintenance late arrival trap missing')
        dirty=load('dirty_import')
        missing=sum(x['temperature_c']=='' for x in dirty)
        malformed=sum(x['event_timestamp']=='NOT_A_TIMESTAMP' for x in dirty)
        abnormal=sum(x['temperature_c']=='99999.0' for x in dirty)
        duplicates=len(dirty)-len(set(x['sample_id'] for x in dirty))
        for title,value in [('missing',missing),('bad_time',malformed),('out_of_range',abnormal),('duplicates',duplicates)]:
            if value<1:errors.append(f'dirty fixture missing {title}')
        summary['dirty_oracles']={'missing':missing,'bad_time':malformed,'out_of_range':abnormal,'duplicates':duplicates}
        summary['industrial_target_count']=sum(int(x['quality_label']) for x in rows)
        summary['industrial_regime_count']=len(set(x['operating_regime'] for x in rows))
        regression=load('static_regression')
        if not all(x['price_target']==x['post_sale_price__LEAKAGE_TRAP'] for x in regression):
            errors.append('regression leakage trap invalid')
        unlabeled=load('sensor_unlabeled')
        if 'quality_label' in unlabeled[0] or 'target' in unlabeled[0]:
            errors.append('unlabeled data contains a target')
    return {'status':'passed' if not errors else 'failed',
      'suite_id':SUITE_ID,'fixture_kind':'synthetic',
      'production_ready':False,'checked':summary,'errors':errors}
