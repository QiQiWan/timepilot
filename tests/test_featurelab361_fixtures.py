"""v0.36.1 synthetic data: reproducibility, independent PIT and data-quality oracles."""
from __future__ import annotations

import csv
from datetime import datetime
import json
from pathlib import Path
import shutil

import pandas as pd
import pytest

from timepilot.featurelab361_fixtures import SEED, generate_suite, verify_suite, digest_file
from timepilot.feature_insights import build_insight_brief


@pytest.fixture(scope='module')
def suite(tmp_path_factory):
    folder=tmp_path_factory.mktemp('featurelab361')
    manifest=generate_suite(folder)
    return folder,manifest


def read(folder,name):
    with (folder/(name+'.csv')).open(newline='',encoding='utf-8') as handle:
        return list(csv.DictReader(handle))


def test_fixture_manifest_and_golden_quality(suite):
    folder,manifest=suite
    check=verify_suite(folder)
    assert check['status']=='passed' and check['production_ready'] is False
    assert check['checked']['dirty_oracles']=={'missing':24,'bad_time':10,'out_of_range':8,'duplicates':12}
    assert manifest['source_type']=='deterministic_synthetic'


def test_reproducible_bytes_on_independent_generation(suite,tmp_path):
    original,manifest=suite
    another=tmp_path/'second'
    other=generate_suite(another,seed=SEED)
    assert set(manifest['cases'])==set(other['cases'])
    for name,case in manifest['cases'].items():
        assert case['source_sha256']==other['cases'][name]['source_sha256']


def test_source_tamper_fails_closed(suite,tmp_path):
    folder,_=suite
    replica=tmp_path/'bad'
    shutil.copytree(folder,replica)
    with (replica/'industrial_pit.csv').open('a',encoding='utf-8') as output:
        output.write('poisoned row\n')
    assert verify_suite(replica)['status']=='failed'
    assert 'industrial_pit: SHA256 mismatch' in verify_suite(replica)['errors']


def test_reject_overwrite_altered_fixture_without_force(suite,tmp_path):
    original,_=suite
    replica=tmp_path/'edited'
    shutil.copytree(original,replica)
    (replica/'static_binary.csv').write_text('changed',encoding='utf-8')
    with pytest.raises(ValueError,match='fixture changed'):
        generate_suite(replica)


def test_static_regression_label_does_not_enter_features(suite):
    folder,_=suite
    rows=read(folder,'static_regression')
    assert len(rows)==1800 and all(r['price_target']==r['post_sale_price__LEAKAGE_TRAP'] for r in rows)
    assert len(set(r['district'] for r in rows))==4


def test_static_binary_is_imbalanced_and_has_deliberate_leakage_trap(suite):
    folder,_=suite
    rows=read(folder,'static_binary')
    prevalence=sum(int(r['defect_label']) for r in rows)/len(rows)
    assert 0.01<prevalence<0.25
    assert all(r['defect_label']==r['post_inspection_rework__LEAKAGE_TRAP'] for r in rows)


def test_industrial_pit_temporal_order_is_valid(suite):
    folder,_=suite
    frame=pd.read_csv(folder/'industrial_pit.csv')
    from importlib import util
    entry=Path(__file__).resolve().parents[1]/'scripts/featurelab30_e2e.py'
    spec=util.spec_from_file_location('featurelab30_e2e_qa',entry)
    module=util.module_from_spec(spec)
    spec.loader.exec_module(module)
    mapping=json.loads((folder/'industrial_field_map.json').read_text())
    argv=['--csv',str(folder/'industrial_pit.csv'),'--rows-limit','300000',
          '--provenance',mapping['provenance']]
    for key in ('event_time','available_at','prediction_time','label_available',
                'target','device','regime','measure_a','measure_b'):
        argv.extend(['--'+key.replace('_','-'),mapping[key]])
    args=module.parse_args(argv)
    result=module.verify_input(frame,args)
    assert len(result)==2160
    assert result[args.device].nunique()==6


@pytest.mark.parametrize('filename,message',[
    ('industrial_late_measurement','PIT'),('industrial_early_label','标签'),
])
def test_real_e2e_precheck_rejects_future_leaks(suite,filename,message):
    folder,_=suite
    from importlib import util
    entry=Path(__file__).resolve().parents[1]/'scripts/featurelab30_e2e.py'
    spec=util.spec_from_file_location('featurelab30_e2e_negative',entry)
    module=util.module_from_spec(spec);spec.loader.exec_module(module)
    mapping=json.loads((folder/'industrial_field_map.json').read_text())
    argv=['--csv',str(folder/(filename+'.csv')),'--provenance',mapping['provenance']]
    for key in ('event_time','available_at','prediction_time','label_available',
                'target','device','regime','measure_a','measure_b'):
        argv.extend(['--'+key.replace('_','-'),mapping[key]])
    args=module.parse_args(argv)
    with pytest.raises(ValueError,match=message):
        module.verify_input(pd.read_csv(folder/(filename+'.csv')),args)


def test_no_label_no_model_gain(suite):
    folder,_=suite
    frame=pd.read_csv(folder/'sensor_unlabeled.csv')
    assert not any('label' in c or 'target' in c for c in frame.columns)
    brief=build_insight_brief(source_sha256='a'*64, contract_revision=1)
    assert brief['model']['status']=='no_confirmed_label'
    assert brief['model']['comparisons']==[]


def test_maintenance_pit_join_respects_availability(suite):
    folder,_=suite
    rows=read(folder,'industrial_pit');events=read(folder,'maintenance_events')
    dev='EQ-01'
    target=next(r for r in rows if r['row_id']==dev+'-0195')
    now=datetime.fromisoformat(target['prediction_timestamp'])
    candidate=[e for e in events if e['equipment_id']==dev and
               datetime.fromisoformat(e['event_timestamp'])<=now]
    eligible=[e for e in candidate if datetime.fromisoformat(e['available_at'])<=now]
    assert len(candidate)==2 and len(eligible)==1
    assert eligible[0]['event_id']==f'M-{dev}-75'


def test_regime_epochs_not_merged_after_return(suite):
    rows=[r for r in read(suite[0],'industrial_pit') if r['equipment_id']=='EQ-01']
    assert rows[0]['operating_regime']=='A'
    assert rows[90]['operating_regime']=='B'
    assert rows[180]['operating_regime']=='A'
    assert rows[270]['operating_regime']=='C'
    assert [rows[j]['maintenance_event'] for j in (75,195,315)]==['1','1','1']


def test_generated_dataset_feature_strategy_excludes_declared_leakage(suite):
    """Actual FeatureLab planner, not a surrogate candidate generator."""
    from timepilot.feature_contracts import DataContract,FieldContract
    from timepilot.feature_strategy import plan_candidates
    folder,_=suite
    frame=pd.read_csv(folder/'industrial_pit.csv')
    selected=('sensor_value_a','sensor_value_b','temperature_c','pressure_kpa',
              'future_quality_label__LEAKAGE_TRAP','quality_label')
    cols=[]
    for i,col in enumerate(selected):
        illegal=col in ('quality_label','future_quality_label__LEAKAGE_TRAP')
        cols.append(FieldContract(field_id='field_'+str(i),source_name=col,
           source_column_index=i,physical_type='DOUBLE',semantic_type='numeric',
           role='label' if col=='quality_label' else 'value',
           confirmation='confirmed',classification='restricted' if illegal else 'internal',
           unit='C' if col=='temperature_c' else 'kPa' if col=='pressure_kpa' else '1'))
    contract=DataContract(asset_id='fixture',version_id='base',source_sha256=digest_file(folder/'industrial_pit.csv'),
           revision=1,state='confirmed',fields=cols)
    profile={'fields':[{'field_id':'field_'+str(i),
       'null_rate':0,'distinct_count':int(frame[col].nunique()),
       'min':float(frame[col].min()),'max':float(frame[col].max()),
       'std':float(frame[col].std()),'zero_rate':float((frame[col]==0).mean())}
       for i,col in enumerate(selected)]}
    planned=plan_candidates(contract,profile=profile,budget=16,task_mode='supervised',
       topology={'can_propose_windows':False,'safe_for_windows':False})
    assert planned['proposals']
    assert all(set(x['input_field_ids']).isdisjoint({'field_4','field_5'})
               for x in planned['proposals'])
    assert all(not x['operator_id'].startswith('rolling.') for x in planned['proposals'])


def test_independent_sql_data_quality_oracle(suite):
    import sqlite3
    rows=read(suite[0],'dirty_import')
    con=sqlite3.connect(':memory:')
    con.execute('CREATE TABLE q(id TEXT, stamp TEXT, temp TEXT)')
    con.executemany('INSERT INTO q VALUES (?,?,?)',
        [(r['sample_id'],r['event_timestamp'],r['temperature_c']) for r in rows])
    missing=con.execute("SELECT COUNT(*) FROM q WHERE temp='' ").fetchone()[0]
    bad=con.execute("SELECT COUNT(*) FROM q WHERE stamp='NOT_A_TIMESTAMP'").fetchone()[0]
    outliers=con.execute("SELECT COUNT(*) FROM q WHERE CAST(temp AS REAL)>1000").fetchone()[0]
    duplicates=con.execute('SELECT COUNT(*)-COUNT(DISTINCT id) FROM q').fetchone()[0]
    assert (missing,bad,outliers,duplicates)==(24,10,8,12)
