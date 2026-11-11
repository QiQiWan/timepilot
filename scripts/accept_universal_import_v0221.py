"""Real DuckDB + Arrow acceptance for v0.22.1 workbook regions.

Run against a *temporary local project* on the target machine:
    python scripts/accept_universal_import_v0221.py --output /path/to/report.json
No network, dependency installation, browser download or persistent user data.
The optional Perspective Chrome gate remains in `bash TIMEPILOT.sh accept native`.
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))


def main():
    parser=argparse.ArgumentParser(description='Real v0.22.1 multi-region DuckDB/Arrow acceptance')
    parser.add_argument('--output',type=Path,default=ROOT/'reports'/'universal_import_v0221_real.json')
    args=parser.parse_args()
    report={'version':'0.22.1','status':'running','steps':{},'native_bi':'real_duckdb_arrow',
            'file_type':'synthetic_workbook','started_at':time.time()}
    try:
        import duckdb  # noqa: F401
        import pyarrow  # noqa: F401
        from fastapi.testclient import TestClient
        from openpyxl import Workbook
        from timepilot.api import create_app
        from timepilot.native_bi import QuerySpec
    except ImportError as exc:
        report.update(status='blocked',reason=f'Native dependency missing: {exc}')
        write_report(args.output,report)
        print(json.dumps(report,ensure_ascii=False))
        return 2
    try:
        with tempfile.TemporaryDirectory(prefix='timepilot_real_import_') as folder:
            app=create_app(home=Path(folder)/'runtime',embedded_worker=False)
            w=Workbook();s=w.active;s.title='装配工序'
            for cell,value in {'B2':'Batch','C2':'Qty','F2':'Device','G2':'Temp',
                               'B3':'00123','C3':6,'F3':'M1','G3':18.3,
                               'B4':'00124','C4':7,'F4':'M2','G4':20.2,
                               'B8':'Event','C8':'Status','B9':'repair','C9':'done'}.items():s[cell]=value
            w.create_sheet('空工作表')
            memory=io.BytesIO();w.save(memory)
            with TestClient(app) as client:
                created=client.post('/api/v1/projects',json={'name':'temp-acceptance'});
                created.raise_for_status();pid=created.json()['id']
                uploaded=client.post(f'/api/v1/projects/{pid}/imports',
                                     files=[('files',('regions.xlsx',memory.getvalue()))]);uploaded.raise_for_status()
                iid=uploaded.json()['import_id']
                tree=client.get(f'/api/v1/projects/{pid}/imports/{iid}/catalog');tree.raise_for_status()
                catalog=tree.json();regions=catalog['files'][0]['worksheets'][0]['regions']
                assert len(regions)==3 and catalog['regions_total']==3, catalog
                assert [r['row_count_estimate'] for r in regions]==[2,2,1]
                report['steps']['scan_three_distinct_regions']='passed'
                ids=[r['region_id'] for r in regions]
                sent=client.post(f'/api/v1/projects/{pid}/imports/{iid}/publish-batch',
                                 json={'region_ids':ids,'confirm':True})
                sent.raise_for_status();batch=sent.json()
                for _ in ids:
                    outcome=app.state.project_publication_jobs.work_once()
                    assert outcome and outcome['status']=='succeeded',outcome
                response=client.get(f'/api/v1/projects/{pid}/publish-batches/{batch["batch_id"]}')
                response.raise_for_status();published=response.json()
                assert published['status']=='succeeded' and published['count']==3,published
                report['steps']['real_parquet_publish_all_regions']='passed'
                assets=[entry['native_asset_id'] for entry in published['items']]
                assert len(set(assets))==3
                assert all(app.state.native_bi.verify(a)['valid'] for a in assets)
                native=app.state.native_bi
                first=native.query(QuerySpec(asset_id=assets[0],columns=['Batch','Qty'],include_total=True,limit=100))
                assert first['total']==2 and first['rows'][0]['Batch']=='00123',first
                assert int(first['rows'][0]['Qty'])==6
                report['steps']['real_duckdb_query_and_zero_padding']='passed'
                arrow=native.query_arrow(QuerySpec(asset_id=assets[0],columns=['Batch','Qty'],limit=100))
                assert len(arrow)>100
                import pyarrow.ipc as ipc
                with ipc.open_stream(io.BytesIO(arrow)) as reader:
                    table=reader.read_all()
                assert table.num_rows==2 and table.column('Batch')[0].as_py()=='00123'
                report['steps']['real_arrow_response']='passed'
                duplicate=client.post(f'/api/v1/projects/{pid}/imports/{iid}/publish-batch',
                                      json={'region_ids':list(reversed(ids)),'confirm':True})
                assert duplicate.status_code==202 and duplicate.json()['batch_id']==batch['batch_id']
                report['steps']['repeat_batch_idempotent']='passed'
                # Regression for a padded value appearing only AFTER the first
                # 3,000 sampled rows in the *real* BI importer.
                extra=Path(folder)/'late_codes.csv'
                with extra.open('w',encoding='utf-8',newline='') as output:
                    output.write('code,reading\n')
                    for i in range(3100):output.write(f'{i+10000},{i}\n')
                    output.write('00123,3100\n')
                late=native.import_from_path(extra,'late-zero-padding',{'source':'acceptance'})
                q=native.query(QuerySpec(asset_id=late['asset_id'],columns=['code'],
                                         filters=[{'column':'code','op':'eq','value':'00123'}],limit=5))
                assert len(q['rows'])==1 and q['rows'][0]['code']=='00123',q
                report['steps']['late_zero_padding_full_scan']='passed'
                report.update(status='passed',region_count=3,native_asset_ids=assets,
                              rows=[2,2,1],duckdb_version=duckdb.__version__)
    except Exception as exc:
        report.update(status='failed',reason=f'{type(exc).__name__}: {exc}')
        write_report(args.output,report)
        raise
    report['elapsed_seconds']=round(time.time()-report['started_at'],2)
    write_report(args.output,report)
    print(json.dumps({'status':report['status'],'steps':report['steps'],
                      'report':str(args.output)},ensure_ascii=False))
    return 0


def write_report(path,report):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


if __name__=='__main__':
    raise SystemExit(main())
