#!/usr/bin/env python3
"""v0.36.4 physical fixture -> DuckDB/Parquet/chart/feature/model gate.

All comparisons use frozen independent Python CSV baselines. Missing physical
packages are BLOCKED, never SKIPPED or PASS. Existing TIMEPILOT_HOME is untouched.
"""
from __future__ import annotations
import argparse
import csv
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'scripts')]
from timepilot.featurelab361_fixtures import generate_suite,verify_suite,digest_file
from timepilot.featurelab362_reference import independent_reference,verify_frozen_baseline


def safe_root(path:Path,home:Path|None)->Path:
    final=path.resolve()
    if final==ROOT or ROOT.is_relative_to(final):raise ValueError('QA directory must not replace source')
    if home and (final==home or final.is_relative_to(home)):
        raise ValueError('QA must not write into production TIMEPILOT_HOME')
    return final


def run(data:Path,out:Path)->dict:
    checks=[]
    def record(name,fn):
        try:
            payload=fn() or {}
            checks.append({'name':name,'status':'passed',**payload})
        except Exception as exc:
            checks.append({'name':name,'status':'failed','reason':type(exc).__name__+': '+str(exc)[:350]})
    missing=[m for m in ('duckdb','pyarrow','sklearn','pandas') if importlib.util.find_spec(m) is None]
    def input_gate():
        generate_suite(data)
        fixture=verify_suite(data)
        frozen=verify_frozen_baseline(data)
        errors=(fixture.get('errors') or [])+(frozen.get('errors') or [])
        if fixture.get('status')!='passed' or errors:raise AssertionError(str(errors[:3]))
        return {'source_sha256':digest_file(data/'industrial_pit.csv'),
                'fixture_manifest_sha256':digest_file(data/'MANIFEST.json'),
                'baseline_sha256':digest_file(ROOT/'benchmarks/featurelab_v0362/BASELINE.json')}
    record('frozen_input',input_gate)
    if checks[-1]['status']!='passed':
        return {'status':'failed','checks':checks,'missing_dependencies':missing}
    if missing:
        for name in ('duckdb_parquet','series_chart','physical_feature_materialization'):
            checks.append({'name':name,'status':'blocked','reason':'Missing actual installed packages: '+', '.join(missing)})
        if all(importlib.util.find_spec(m) for m in ('pandas','sklearn')):
            from featurelab362_model_audit import run as model_audit
            record('model_frozen_evaluation',lambda:{'models':model_audit(data)})
            if checks[-1]['status']=='passed' and checks[-1]['models'].get('status')!='passed':
                checks[-1].update(status='failed',reason='frozen model metrics drift')
        else:
            checks.append({'name':'model_frozen_evaluation','status':'blocked','reason':'Missing scikit-learn or pandas'})
        return {'status':'failed' if any(x['status']=='failed' for x in checks) else 'blocked',
                'checks':checks,'missing_dependencies':missing}

    import duckdb
    import pyarrow.parquet as pq
    csv_file=data/'industrial_pit.csv'
    parquet=out/'industrial_source.parquet'
    artifact=out/'feature_rolling3.parquet'
    # QA reruns may replace only their own prior Parquet artifacts.
    for old in (parquet,artifact):
        if old.exists():old.unlink()
    con=duckdb.connect(':memory:')
    def quoted(path):
        return "'" + str(path).replace("'","''") + "'"
    def physical_parquet():
        con.execute('''CREATE TABLE source AS SELECT ROW_NUMBER() OVER () AS __tp_rowid, equipment_id, row_id, event_timestamp,
            TRY_CAST(sensor_value_a AS DOUBLE) AS sensor_value_a,
            quality_label, measurement_available_at, prediction_timestamp,
            label_available_at
            FROM read_csv_auto(?, HEADER=TRUE, ALL_VARCHAR=TRUE)''',[str(csv_file)])
        n=con.execute('SELECT COUNT(*) FROM source').fetchone()[0]
        baseline=independent_reference(data)
        expected=baseline['datasets']['industrial_pit']['rows']
        assert n==expected,(n,expected)
        con.execute('COPY (SELECT * FROM source ORDER BY equipment_id,event_timestamp,row_id) TO '+quoted(parquet)+' (FORMAT PARQUET)')
        pf=pq.ParquetFile(parquet)
        assert pf.metadata.num_rows==n
        con.execute('CREATE TABLE source_back AS SELECT * FROM read_parquet(?)',[str(parquet)])
        assert con.execute('SELECT COUNT(*) FROM source_back').fetchone()[0]==n
        assert math.isclose(con.execute('SELECT SUM(sensor_value_a) FROM source').fetchone()[0], con.execute('SELECT SUM(sensor_value_a) FROM source_back').fetchone()[0], rel_tol=1e-12)
        h1=digest_file(parquet)
        h2=hashlib.sha256(parquet.read_bytes()).hexdigest()
        assert h1==h2
        return {'records':n,'source_sha256':digest_file(csv_file),'physical_parquet_sha256':h1,'parquet_bytes':parquet.stat().st_size}
    record('duckdb_parquet',physical_parquet)
    if checks[-1]['status']!='passed':
        con.close()
        return {'status':'failed','checks':checks,'missing_dependencies':missing}

    def chart():
        from timepilot.feature_chart_semantics import series_aggregate
        frozen=independent_reference(data)['chart_oracle']['devices']
        comparisons=0
        for device,expected in frozen.items():
            payload=series_aggregate(con,'source_back',order_column='event_timestamp',
                value_expression='sensor_value_a', original_expression='sensor_value_a',
                entity_column='equipment_id',entity_value=device,resolution=12)
            assert payload['population_count']==len(expected)*30
            assert payload['partial'] is False and len(payload['points'])==len(expected)
            for point,oracle in zip(payload['points'],expected):
                assert point['count']==oracle['n']
                for field,reference in (('value',oracle['mean']),('min',oracle['min']),('max',oracle['max'])):
                    assert math.isclose(point[field],reference,abs_tol=1e-5,rel_tol=0),(device,field,point[field],reference)
                assert datetime.fromisoformat(point['x'])==datetime.fromisoformat(oracle['first'])
                assert datetime.fromisoformat(point['x_end'])==datetime.fromisoformat(oracle['last'])
                comparisons+=1
        return {'devices':len(frozen),'verified_time_buckets':comparisons,'comparison':'independent CSV numeric oracle'}
    record('series_chart',chart)
    def materialize():
        con.execute('''COPY (SELECT equipment_id,row_id,event_timestamp,sensor_value_a,
            AVG(sensor_value_a) OVER (PARTITION BY equipment_id ORDER BY event_timestamp,row_id
            ROWS BETWEEN 2 PRECEDING AND CURRENT ROW) AS rolling3
            FROM source_back ORDER BY equipment_id,event_timestamp,row_id)
            TO '''+quoted(artifact)+''' (FORMAT PARQUET)''')
        arrow=pq.read_table(artifact)
        assert arrow.num_rows==con.execute('SELECT count(*) FROM source_back').fetchone()[0]
        measured={r['row_id']:r['rolling3'] for r in arrow.select(['row_id','rolling3']).to_pylist()}
        from collections import defaultdict
        groups=defaultdict(list)
        with csv_file.open(newline='',encoding='utf-8') as handle:
            for item in csv.DictReader(handle):groups[item['equipment_id']].append(item)
        compared=0
        for rows in groups.values():
            rows.sort(key=lambda r:(r['event_timestamp'],r['row_id']))
            values=[float(r['sensor_value_a']) for r in rows]
            for i,row in enumerate(rows):
                expected=sum(values[max(0,i-2):i+1])/min(3,i+1)
                actual=measured[row['row_id']]
                assert actual is not None and math.isclose(actual,expected,abs_tol=1e-7,rel_tol=0),row['row_id']
                compared+=1
        assert compared==arrow.num_rows
        return {'rows_verified':compared,'feature':'rolling.mean(window=3, partition=equipment_id)',
                'materialized_sha256':digest_file(artifact),'bytes':artifact.stat().st_size}
    record('physical_feature_materialization',materialize)
    con.close()
    def models():
        from featurelab362_model_audit import run as model_audit
        report=model_audit(data)
        if report.get('status')!='passed':raise AssertionError(str(report.get('errors',[])[:4]))
        (out/'MODEL_EVIDENCE.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        return {'cases':{k:v['actual']['means'] for k,v in report['cases'].items()},
                'source_and_split_sha256_verified':True,'scope':'synthetic; no enterprise outcome claims'}
    record('model_frozen_evaluation',models)
    return {'status':'passed' if all(x['status']=='passed' for x in checks) else 'failed',
            'checks':checks,'missing_dependencies':missing}


def main(argv=None)->int:
    p=argparse.ArgumentParser()
    p.add_argument('--data-dir',type=Path,default=Path('reports/featurelab364_data'))
    p.add_argument('--out',type=Path,default=Path('reports/featurelab364_physical'))
    a=p.parse_args(argv)
    home=None
    import os
    if os.environ.get('TIMEPILOT_HOME'):home=Path(os.environ['TIMEPILOT_HOME']).expanduser().resolve()
    try:
        data=safe_root(a.data_dir,home)
        out=safe_root(a.out,home)
        if data==out or data.is_relative_to(out) or out.is_relative_to(data):raise ValueError('Evidence and data directories must be distinct')
        data.mkdir(parents=True,exist_ok=True)
        out.mkdir(parents=True,exist_ok=True)
        result=run(data,out)
    except Exception as exc:
        result={'status':'failed','checks':[{'name':'runner','status':'failed','reason':str(exc)}]}
        out=a.out.resolve()
        if not out.exists():out.mkdir(parents=True,exist_ok=True)
    result.update(release='0.36.4',timestamp_utc=datetime.now(timezone.utc).isoformat(),
        data_provenance='frozen synthetic',production_accepted=False,
        native_perspective_browser='not_assessed_by_this_gate',
        note='Real DuckDB/Arrow physical results only when every prerequisite is installed; browser gate is independent')
    (out/'ACCEPTANCE.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    lines=['# TimePilot v0.36.4 physical regression','',
        'Status: **'+result['status']+'**. Synthetic sources; no production claim.','',
        '| Gate | Status | Notes |','|---|---|---|',
        *['| '+r['name']+' | '+r['status']+' | '+str(r.get('reason',r.get('source_sha256',r.get('materialized_sha256','verified')))).replace('|','/')+' |' for r in result['checks']],
        '','Native Perspective requires separate installed Chrome and verified JS/WASM browser acceptance.']
    (out/'ACCEPTANCE.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps({'status':result['status'],'checks':[(x['name'],x['status']) for x in result['checks']],
        'evidence':str(out/'ACCEPTANCE.json')},ensure_ascii=False,indent=2))
    return {'passed':0,'blocked':2,'failed':1}[result['status']]

if __name__=='__main__':raise SystemExit(main())
