"""Actual two-table JOIN, computed, bucketed derivation -> model draft integration.
No mocks; writes only to an isolated temporary runtime.
"""
from __future__ import annotations
import csv,io,json,sys,tempfile,time
from pathlib import Path
from uuid import uuid4
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))

def main():
    try:
        import duckdb,pyarrow
        from fastapi.testclient import TestClient
        from timepilot.api import create_app
    except ImportError as exc:
        print(json.dumps({'status':'blocked','reason':str(exc),'requires':'real DuckDB + PyArrow'},ensure_ascii=False))
        return 2
    report={'status':'running','version':'0.23.2','checks':{},'started_at':time.time()}
    def check(name):report['checks'][name]='passed'
    try:
        with tempfile.TemporaryDirectory(prefix='tp_derived_real_') as path:
            app=create_app(home=Path(path)/'runtime',embedded_worker=False)
            with TestClient(app) as c:
                p=c.post('/api/v1/projects',json={'name':'Approved derived BI acceptance'});p.raise_for_status()
                pid=p.json()['id']
                def asset(name,cols,rows):
                    file=Path(path)/(name+'.csv')
                    with file.open('w',newline='',encoding='utf-8') as out:
                        writer=csv.writer(out);writer.writerow(cols);writer.writerows(rows)
                    result=app.state.native_bi.import_from_path(file,name,{'type':'acceptance_local','project_id':pid})
                    with app.state.projects._lock,app.state.projects._db() as db:
                        db.execute('INSERT OR IGNORE INTO project_native_assets VALUES(?,?,?,?)',
                                   (pid,result['asset_id'],'acceptance_local',str(time.time())))
                    return result['asset_id']
                left=asset('设备表',['采集时间','批次','温度','压力'],[
                    [f'2026-10-01 {i:02d}:00:00',f'B{i:02d}',20+i,round(1+i/10,2)] for i in range(24)])
                right=asset('质量表',['批次','是否不良'],[[f'B{i:02d}',int(i%5==0)] for i in range(24)])
                base=f'/api/v1/projects/{pid}'
                ws=c.post(base+'/bi-agent/workspaces',json={'asset_id':left});ws.raise_for_status()
                doc=ws.json();wid=doc['workspace_id'];url=base+'/bi-agent/workspaces/'+wid
                def apply(tool,args,confirmed=False):
                    nonlocal doc
                    x=c.post(url+'/apply',json={'expected_revision':doc['revision'],
                        'request_id':str(uuid4()),'actions':[{'tool':tool,'arguments':args}], 'confirm':confirmed})
                    x.raise_for_status();doc=x.json()['workspace']
                apply('bi.join.apply_virtual',{'right_asset_id':right,'left_key':'批次',
                    'right_key':'批次','right_version_id':'base','how':'left'},True)
                apply('bi.expression.create',{'name':'temp_minus_pressure',
                    'expression':{'kind':'binary','op':'-','left':{'kind':'column','value':'温度'},
                                  'right':{'kind':'column','value':'压力'}}})
                request=c.post(base+'/derived',json={'workspace_id':wid,'name':'设备质量联表派生',
                    'request_id':str(uuid4())});request.raise_for_status()
                rid=request.json()['derivation_id']
                blocked=c.post(base+'/derived/'+rid+'/publish');assert blocked.status_code==422
                check('cannot_publish_without_review')
                approval=c.post(base+'/derived/'+rid+'/approve',json={'confirmed':True,
                    'reason':'已检查字段、关联放大倍数及用途'});approval.raise_for_status()
                publish=c.post(base+'/derived/'+rid+'/publish');publish.raise_for_status()
                published=publish.json();assert published['published_rows']==24
                assert len(published['columns'])==7
                check('real_duckdb_join_expression_publish')
                again=c.post(base+'/derived/'+rid+'/publish');again.raise_for_status()
                assert again.json()['native_asset_id']==published['native_asset_id']
                check('idempotent_publish_and_immutable_native_hash')
                draft=c.post(base+'/derived/'+rid+'/model-drafts',json={'name':'设备派生建模',
                    'confirm_scope':True});draft.raise_for_status()
                assert draft.json()['draft']['asset_id']==published['native_asset_id']
                check('actual_frozen_selection_to_modeling_draft')
                # New workspace, real time aggregation with precise source rows.
                alt=c.post(base+'/bi-agent/workspaces',json={'asset_id':left});alt.raise_for_status()
                agg=alt.json()
                bucket=c.post(base+'/bi-agent/workspaces/'+agg['workspace_id']+'/apply',json={
                    'expected_revision':agg['revision'],'request_id':str(uuid4()),
                    'actions':[{'tool':'bi.timeseries.bucket','arguments':{
                        'time_field':'采集时间','metric_field':'温度','interval':'1h','operation':'avg'}}]})
                bucket.raise_for_status()
                request2=c.post(base+'/derived',json={'workspace_id':agg['workspace_id'],
                    'name':'每小时温度派生','request_id':str(uuid4())});request2.raise_for_status()
                rid2=request2.json()['derivation_id']
                approve=c.post(base+'/derived/'+rid2+'/approve',json={'confirmed':True,
                    'reason':'小时聚合为明确的派生定义'});approve.raise_for_status()
                pub2=c.post(base+'/derived/'+rid2+'/publish');pub2.raise_for_status()
                assert pub2.json()['published_rows']==24
                check('real_time_bucket_aggregation_publish')
        report['status']='passed'
        return 0
    except Exception as exc:
        report.update(status='failed',reason=type(exc).__name__+': '+str(exc))
        raise
    finally:
        report['elapsed_seconds']=round(time.time()-report['started_at'],3)
        output=ROOT/'reports'/'derived_real_v0232.json';output.parent.mkdir(exist_ok=True)
        output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps({'status':report['status'],'checks':report['checks']},ensure_ascii=False))
if __name__=='__main__':raise SystemExit(main())
