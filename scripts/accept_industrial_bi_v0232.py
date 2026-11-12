"""REAL DuckDB/Arrow industrial BI capacity and stability acceptance.

No mocks. Isolated temporary runtime; user-provided CSV/Parquet is read only.
For production acceptance use --input real.csv --duration 900, then run the
separate native-Chrome and multi-tenant HTTPS acceptance scripts.  No browser
claim is made here.  Missing runtime libraries produce BLOCKED (exit 2).
"""
from __future__ import annotations
import argparse,csv,io,json,os,statistics,sys,tempfile,time
try:
    import resource
except ImportError:
    resource=None
from datetime import datetime,timedelta
from pathlib import Path
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))


def p95(values):
    if not values:return None
    ordered=sorted(values)
    return round(ordered[min(len(ordered)-1,max(0,int(len(ordered)*0.95)-1))],2)


def generated(path,rows):
    with path.open('w',newline='',encoding='utf-8') as file:
        writer=csv.writer(file)
        writer.writerow(['采集时间','设备编号','温度','压力','维护事件','工况'])
        origin=datetime(2026,10,1)
        for i in range(rows):
            writer.writerow([(origin+timedelta(seconds=i)).strftime('%Y-%m-%d %H:%M:%S'),
                f'M-{i%5:02d}',round(24+(i%1500)*0.014,4),round(1.2+(i%600)*0.0006,4),
                '检修' if i%10000==0 else '', '运行' if i%20000>1000 else '待机'])


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--input',type=Path,help='Real industrial CSV/Parquet, local to this machine')
    parser.add_argument('--rows',type=int,default=100_000)
    parser.add_argument('--duration',type=int,default=10,help='Seconds; 900 recommended for formal endurance')
    parser.add_argument('--time-field',default='采集时间')
    parser.add_argument('--metric-field',default='温度')
    parser.add_argument('--device-field',default='设备编号')
    parser.add_argument('--max-iterations',type=int,default=0)
    parser.add_argument('--output',type=Path,default=ROOT/'reports'/'industrial_bi_live_v0232.json')
    args=parser.parse_args()
    result={'version':'0.23.2','status':'running','source_kind':'real_local_input' if args.input else 'generated_synthetic',
            'checks':{},'started_at':time.time(),'parameters':{'duration_seconds':args.duration,'rows_requested':args.rows},
            'scope':'DuckDB + Arrow + ASGI/TestClient; browser and enterprise HTTPS are separate gates'}
    def finalize(status,reason=None):
        result['status']=status
        if reason:result['reason']=reason
        result['elapsed_seconds']=round(time.time()-result['started_at'],3)
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print(json.dumps({'status':status,'output':str(args.output),'reason':reason},ensure_ascii=False))
    try:
        import duckdb,pyarrow
        from fastapi.testclient import TestClient
        from timepilot.api import create_app
    except ImportError as exc:
        finalize('blocked','缺少真实 DuckDB/Arrow/FastAPI 依赖：'+str(exc))
        return 2
    try:
        with tempfile.TemporaryDirectory(prefix='tp_prod_native_') as tmp:
            root=Path(tmp)
            if args.input:
                source=args.input.resolve(strict=True)
                if source.suffix.lower() not in ('.parquet','.csv'):
                    raise ValueError('实机输入仅支持 CSV/Parquet')
            else:
                source=root/'industrial.csv'
                generated(source,max(1000,args.rows))
            result['input_bytes']=source.stat().st_size
            result['duckdb_version']=duckdb.__version__
            result['pyarrow_version']=pyarrow.__version__
            app=create_app(home=root/'runtime',embedded_worker=False)
            with TestClient(app) as client:
                pid_result=client.post('/api/v1/projects',json={'name':'Industrial BI load acceptance'})
                pid_result.raise_for_status();pid=pid_result.json()['id']
                begin=time.monotonic()
                asset=app.state.native_bi.import_from_path(source,'工艺采集(验收)',
                        {'type':'acceptance_local','project_id':pid,'source_kind':result['source_kind']})
                result['import_seconds']=round(time.monotonic()-begin,3)
                result['asset_rows']=asset['row_count'];result['asset_columns']=len(asset['columns'])
                assert asset['row_count']>0
                assert app.state.native_bi.verify(asset['asset_id'])['valid']
                result['checks']['real_duckdb_parquet_import_and_sha']='passed'
                with app.state.projects._lock,app.state.projects._db() as db:
                    db.execute('''INSERT OR IGNORE INTO project_native_assets VALUES(?,?,?,?)''',
                        (pid,asset['asset_id'],'acceptance_local',datetime.utcnow().isoformat()))
                names=[x['name'] for x in asset['columns']]
                for field in (args.time_field,args.metric_field,args.device_field):
                    if field not in names:raise ValueError('原始数据缺少验收指定的字段：'+field)
                root_api=f'/api/v1/projects/{pid}/bi-agent'
                ws=client.post(root_api+'/workspaces',json={'asset_id':asset['asset_id'],'version_id':'base'})
                ws.raise_for_status();doc=ws.json();wid=doc['workspace_id'];url=root_api+'/workspaces/'+wid
                def action(tool,payload):
                    nonlocal doc
                    response=client.post(url+'/apply',json={'expected_revision':doc['revision'],
                        'request_id':str(uuid4()),'actions':[{'tool':tool,'arguments':payload}]})
                    response.raise_for_status();doc=response.json()['workspace']
                action('bi.chart.create',{'type':'line','x':args.time_field,'y':[args.metric_field]})
                action('bi.chart.add_panel',{'type':'scatter','x':args.metric_field,'y':[args.metric_field]})
                result['checks']['multi_panel_state']='passed'
                query=client.get(url+'/query',params={'limit':200});query.raise_for_status()
                arrow=client.get(url+'/query.arrow',params={'limit':200});arrow.raise_for_status()
                with pyarrow.ipc.open_stream(io.BytesIO(arrow.content)) as reader:table=reader.read_all()
                assert table.num_rows==query.json()['returned']>0
                result['checks']['real_duckdb_arrow_query']='passed'
                view=client.post(url+'/views',json={'name':'工艺多图看板','perspective':{
                    'panels':[{'plugin':'Y Line'},{'plugin':'X/Y Scatter'}]}})
                view.raise_for_status();vid=view.json()['view_id']
                readback=client.get(root_api+'/views/'+vid);readback.raise_for_status()
                assert readback.json()['state']['dashboard']==doc['state']['dashboard']
                result['checks']['saved_dashboard_restoration_contract']='passed'
                latencies=[];errors=[];started=time.monotonic();iterations=0
                while time.monotonic()-started<max(1,args.duration):
                    if args.max_iterations and iterations>=args.max_iterations:break
                    start=time.monotonic()
                    try:
                        check=client.get(url+'/query',params={'limit':100})
                        assert check.status_code==200 and check.json()['returned']>0
                        latencies.append((time.monotonic()-start)*1000)
                    except Exception as exc:errors.append(type(exc).__name__+': '+str(exc)[:150])
                    iterations+=1
                    time.sleep(.15)
                result['duration_measured_seconds']=round(time.monotonic()-started,3)
                result['queries']=iterations;result['errors']=errors[:10]
                result['query_p50_ms']=round(statistics.median(latencies),2) if latencies else None
                result['query_p95_ms']=p95(latencies)
                if resource:
                    peak=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                    result['rss_peak_mb']=round(peak/(1024*1024 if sys.platform=='darwin' else 1024),2)
                else:result['rss_peak_mb']=None
                if errors:raise RuntimeError(f'长时查询失败 {len(errors)} 次')
                result['checks']['long_running_native_queries']='passed'
        finalize('passed');return 0
    except Exception as exc:
        finalize('failed',type(exc).__name__+': '+str(exc));raise

if __name__=='__main__':raise SystemExit(main())
