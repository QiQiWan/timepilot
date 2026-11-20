"""Audited synthetic model comparisons using TimePilot's real fold evaluator.

This runs the numpy/pandas/sklearn evaluation engine without DuckDB. All
leakage-trap fields are restricted in the contract and excluded from inputs.
Results are regression QA only; never a production accuracy assertion.
"""
from __future__ import annotations

from pathlib import Path
import pandas as pd
from .feature_contracts import FieldContract, DataContract, TaskSpecV2, EvaluationProtocol
from .feature_evaluation import _evaluate_matrix
from .featurelab361_fixtures import digest_file


def _contract(frame: pd.DataFrame, source_sha: str, target: str,
              time: bool = False, entity: str | None = None) -> DataContract:
    roles = {}
    for name in frame.columns:
        if name == target:
            roles[name] = 'label'
        elif name == entity:
            roles[name] = 'entity_key'
        elif name == 'prediction_timestamp':
            roles[name] = 'prediction_time'
        elif name in ('measurement_available_at', 'label_available_at'):
            roles[name] = 'available_at'
        elif name == 'event_timestamp':
            roles[name] = 'event_time'
        elif name == 'operating_regime':
            roles[name] = 'regime'
        elif name == 'batch':
            roles[name] = 'batch_key'
        elif name in ('sample_id', 'row_id'):
            roles[name] = 'record_id'
        else:
            roles[name] = 'value'
    fields = []
    for i, name in enumerate(frame.columns):
        is_time = name in ('event_timestamp','prediction_timestamp','measurement_available_at','label_available_at')
        is_entity = name == entity or roles[name] in ('record_id','batch_key')
        is_numeric = pd.api.types.is_numeric_dtype(frame[name])
        classification = 'restricted' if 'LEAKAGE_TRAP' in name else 'internal'
        fields.append(FieldContract(field_id='fld_' + name, source_name=name,
            source_column_index=i, physical_type='TIMESTAMP' if is_time else 'DOUBLE' if is_numeric else 'VARCHAR',
            semantic_type='datetime' if is_time else 'numeric' if is_numeric else 'id' if is_entity else 'category',
            role=roles[name], confirmation='confirmed',classification=classification,
            timezone='UTC' if is_time else None))
    return DataContract(asset_id='qa_synthetic',version_id='base', source_sha256=source_sha,
        revision=1,state='confirmed',fields=fields,
        entity_field_ids=['fld_'+entity] if entity else [],
        event_time_field_id='fld_event_timestamp' if time else None,
        available_at_field_id='fld_measurement_available_at' if time else None,
        timezone='UTC' if time else None)


def evaluate_synthetic(folder: Path, scenario: str) -> dict:
    folder = Path(folder)
    if scenario not in ('static_regression','static_binary','industrial_pit'):
        raise ValueError('unsupported supervised QA case')
    path=folder/(scenario+'.csv')
    frame=pd.read_csv(path)
    if scenario == 'static_regression':
        target='price_target';kind='regression';split='kfold';entity=None;time=False
        frame['area_squared']=frame['area_m2']**2
        frame['area_x_age']=frame['area_m2']*frame['age_years']
        base=['area_m2','age_years','rooms','construction_cost']
        outputs=['area_squared','area_x_age']
    elif scenario == 'static_binary':
        target='defect_label';kind='binary_classification';split='stratified';entity=None;time=False
        frame['flux_x_offset']=frame['solder_flux']*frame['placement_offset']
        frame['temp_high']= (frame['temperature_c']>32).astype(float)
        base=['solder_flux','placement_offset','vibration','temperature_c']
        outputs=['flux_x_offset','temp_high']
    else:
        target='quality_label';kind='binary_classification';split='time';entity='equipment_id';time=True
        # Past-only operations, never shifted across equipment boundaries.
        ordered=frame.sort_values(['equipment_id','event_timestamp'],kind='stable')
        lag=ordered.groupby('equipment_id',sort=False)['sensor_value_a'].shift(1)
        frame['sensor_a_lag_1']=lag.reindex(frame.index)
        frame['sensor_a_delta_1']=frame['sensor_value_a']-frame['sensor_a_lag_1']
        base=['sensor_value_a','sensor_value_b','temperature_c','pressure_kpa']
        outputs=['sensor_a_lag_1','sensor_a_delta_1']
    contract=_contract(frame,digest_file(path),target,time=time,entity=entity)
    task=TaskSpecV2(task_id='qa_'+scenario,mode='supervised',task_type=kind,
        label_field_id='fld_'+target,split_kind=split,static_independence_confirmed=not time,
        entity_field_ids=['fld_'+entity] if entity else [],
        prediction_time_field_id='fld_prediction_timestamp' if time else None,
        label_available_at_field_id='fld_label_available_at' if time else None,
        n_splits=3)
    protocol=EvaluationProtocol(task=task,set_id='qa-'+scenario,revision=1,seed=3602,models=['linear'])
    result=_evaluate_matrix(frame,base,outputs,task,protocol,contract,
        source_sha256=contract.source_sha256,spec_sha256='2'*64)
    metric='mae' if kind=='regression' else 'average_precision'
    folds=result['folds']
    means={variant:round(sum(f['metrics'][variant][metric] for f in folds)/len(folds),6)
        for variant in ('raw_only','with_features','selected_features')}
    return {'scenario':scenario,'metric':metric,'split_kind':split,
        'fold_count':len(folds),'means':means,
        'split_manifest_sha256':result['split_manifest_sha256'],
        'selection_frequencies':[
          {'name':x.get('feature'),
           'folds':x.get('selected_in_folds')} for x in result['selection_frequency']],
        'train_selection_scopes':sorted({f['training_feature_selection']['scope'] for f in folds}),
        'all_folds_disjoint':all(m['disjoint_verified'] for m in result['fold_manifest']),
        'label_availability_checked':result['label_availability_checked'],
        'source_sha256':contract.source_sha256}
