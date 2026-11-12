"""Real DuckDB/Arrow conversational BI acceptance. No mocks, no user data changes.

Run: bash TIMEPILOT.sh accept conversational-bi
Creates isolated temporary projects and publishes synthetic industrial CSV assets.
Blocks (exit 2) when DuckDB or PyArrow are absent; never reports a mock as passed.
"""
from __future__ import annotations
import argparse,csv,io,json,sys,tempfile,time
from datetime import datetime,timedelta
from pathlib import Path
from uuid import uuid4
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))


def main():
    p=argparse.ArgumentParser(description='v0.23.1 real DuckDB/Arrow tool/BI/model acceptance')
    p.add_argument('--output',type=Path,default=ROOT/'reports'/'conversational_bi_v0231_real.json')
    args=p.parse_args()
    report={'version':'0.23.1','status':'running','started_at':time.time(),'checks':{},'data':'synthetic_only'}
    def checkpoint(name):report['checks'][name]='passed'
    def emit(status,reason=None):
        report['status']=status
        if reason:report['reason']=reason
        report['elapsed_seconds']=round(time.time()-report['started_at'],3)
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
        print(json.dumps(report,ensure_ascii=False))
    try:
        import duckdb,pyarrow as pa,pyarrow.ipc as ipc
        from fastapi.testclient import TestClient
        from timepilot.api import create_app
    except ImportError as exc:
        emit('blocked','Missing real runtime dependency: '+str(exc))
        return 2
    try:
        with tempfile.TemporaryDirectory(prefix='tp_bi_real_') as directory:
            app=create_app(home=Path(directory)/'runtime',embedded_worker=False)
            def upload(client,pid,name,content):
                root=f'/api/v1/projects/{pid}'
                resp=client.post(root+'/imports',files=[('files',(name,content))]);resp.raise_for_status()
                iid=resp.json()['import_id']
                catalog=client.get(root+f'/imports/{iid}/catalog');catalog.raise_for_status()
                regions=[r['region_id'] for f in catalog.json()['files'] for sh in f['worksheets'] for r in sh['regions']]
                assert len(regions)==1,(name,regions)
                pub=client.post(root+f'/imports/{iid}/publish-batch',json={'region_ids':regions,'confirm':True});pub.raise_for_status()
                result=app.state.project_publication_jobs.work_once()
                assert result['status']=='succeeded',result
                batch=client.get(root+'/publish-batches/'+pub.json()['batch_id']);batch.raise_for_status()
                aid=batch.json()['items'][0]['native_asset_id']
                assert app.state.native_bi.verify(aid)['valid']
                return aid
            temp=io.StringIO(newline='');w=csv.writer(temp)
            w.writerow(['采集时间','批次','设备','温度','压力','检修','工况'])
            origin=datetime(2026,3,1)
            for i in range(80):
                w.writerow([(origin+timedelta(minutes=i)).strftime('%Y-%m-%d %H:%M:%S'),
                            'B%03d'%i,'M1',round(28+i/12,2),round(1.1+i*.01,3),
                            '维修' if i==35 else '', '运行' if i>=20 else '待机'])
            main_csv=temp.getvalue().encode('utf-8')
            temp2=io.StringIO(newline='');w=csv.writer(temp2)
            w.writerow(['批次','是否不良','测量分数'])
            for i in range(80):w.writerow(['B%03d'%i,'NG' if i%8==0 else 'OK',round(i*.3,2)])
            with TestClient(app) as client:
                x=client.post('/api/v1/projects',json={'name':'v0231 integration temp'});x.raise_for_status();pid=x.json()['id']
                aid=upload(client,pid,'设备采集.csv',main_csv)
                checkpoint('published_immutable_sensor_data')
                right=upload(client,pid,'质量标签.csv',temp2.getvalue().encode())
                checkpoint('published_immutable_quality_table')
                root=f'/api/v1/projects/{pid}/bi-agent'
                open_result=client.post(root+'/workspaces',json={'asset_id':aid,'version_id':'base'});open_result.raise_for_status()
                ws=open_result.json();wid=ws['workspace_id'];url=root+'/workspaces/'+wid
                def action(tool,args,revision,confirm=False):
                    response=client.post(url+'/apply',json={'expected_revision':revision,
                        'request_id':str(uuid4()),'confirm':confirm,
                        'actions':[{'tool':tool,'arguments':args}]})
                    response.raise_for_status()
                    return response.json()['workspace']
                ws=action('bi.filter.set',{'column':'温度','op':'gt','value':28.5},ws['revision'])
                ws=action('bi.sort.set',{'field':'采集时间','descending':False},ws['revision'])
                ws=action('bi.chart.create',{'type':'line','x':'采集时间','y':['温度','压力']},ws['revision'])
                actual=client.get(url+'/query');actual.raise_for_status()
                assert actual.json()['returned']>0 and all(float(row['温度'])>28.5 for row in actual.json()['rows'])
                checkpoint('real_filtered_sorted_multivariate_query')
                arrow=client.get(url+'/query.arrow');arrow.raise_for_status()
                with ipc.open_stream(io.BytesIO(arrow.content)) as reader:table=reader.read_all()
                assert table.num_rows==actual.json()['returned']
                checkpoint('real_arrow_perspective_data_source')
                saved=client.post(url+'/views',json={'name':'原生工作台曲线','perspective':{'panels':[{'plugin':'Y Line'}]}})
                saved.raise_for_status();vid=saved.json()['view_id']
                undo=client.post(url+'/undo',json={'expected_revision':ws['revision'],'request_id':str(uuid4())});undo.raise_for_status()
                assert undo.json()['workspace']['state']['chart']['type']=='grid'
                redo=client.post(url+'/redo',json={'expected_revision':undo.json()['workspace']['revision'],'request_id':str(uuid4())});redo.raise_for_status()
                assert redo.json()['workspace']['state']['chart']['type']=='line'
                checkpoint('workspace_undo_redo_and_saved_view')
                ws=redo.json()['workspace']
                selection=client.post(url+'/freeze',json={'confirmed':True});selection.raise_for_status()
                assert selection.json()['row_count']<80
                checkpoint('actual_frozen_row_level_selection')
                # No silent query materialization: model draft must cite the frozen scope.
                model=client.post(url+'/model-drafts',json={'name':'温度趋势模型','confirm_scope':True});model.raise_for_status()
                assert model.json()['draft']['selection']['frozen_selection_id']==model.json()['selection']['selection_id']
                checkpoint('model_draft_from_frozen_selection')
                join={'right_asset_id':right,'right_version_id':'base','left_key':'批次','right_key':'批次','how':'left'}
                preview=client.post(url+'/preview',json={'expected_revision':ws['revision'],
                    'request_id':str(uuid4()),'actions':[{'tool':'bi.join.preview','arguments':join}]})
                preview.raise_for_status();checks=preview.json()['steps'][0]
                assert checks['estimated_rows']==80,checks
                checkpoint('real_join_cardinality_check')
                ws=action('bi.join.apply_virtual',join,ws['revision'],confirm=True)
                joined=client.get(url+'/query');joined.raise_for_status()
                assert joined.json()['returned']>0 and any(c.startswith('right__') for c in joined.json()['columns'])
                checkpoint('real_join_does_not_drop_fields')
                unsupported=client.post(url+'/freeze',json={'confirmed':True});assert unsupported.status_code==422
                checkpoint('no_implicit_modeling_of_unsafe_join')
                ws=action('bi.join.clear',{},ws['revision'])
                ws=action('bi.filter.remove',{},ws['revision'])
                ws=action('bi.timeseries.bucket',{'time_field':'采集时间','metric_field':'温度','interval':'1h',
                     'operation':'avg'},ws['revision'])
                agg=client.get(url+'/query');agg.raise_for_status();assert len(agg.json()['rows'])>=2
                checkpoint('real_duckdb_hourly_time_bucket')
                # Reset the summary view before enabling event overlays on original rows.
                ws=action('bi.timeseries.clear',{},ws['revision'])
                # Industrial overlay must preserve event markers with provenance.
                ws=action('bi.chart.overlay',{'time_field':'采集时间','metric_field':'温度','event_field':'检修',
                    'regime_field':'工况','template':'maintenance_overlay','interval':'1h'},ws['revision'])
                industry=client.get(url+'/query');industry.raise_for_status()
                assert industry.json().get('rows') or industry.json().get('points')
                checkpoint('real_maintenance_regime_overlay')
                report['real_duckdb']=duckdb.__version__
                report['real_arrow']=pa.__version__
        emit('passed');return 0
    except Exception as exc:
        emit('failed',type(exc).__name__+': '+str(exc));raise
if __name__=='__main__':raise SystemExit(main())
