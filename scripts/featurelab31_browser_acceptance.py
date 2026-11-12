#!/usr/bin/env python3
"""One-project/two-asset *real browser* receipt for a prior retained QA home.

No browser is downloaded, no network resources are mocked, and no test data is
created. Missing Perspective JS/WASM, Chrome, PyArrow or WebSocket => BLOCKED.
Use after scripts/featurelab30_e2e.py with --audit-home and --backend-only.
"""
from __future__ import annotations
import argparse,json,socket,sys,threading,time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
sys.path.insert(0,str(ROOT/'scripts'))

def accept(report:dict,*,timeout=30):
    from timepilot.api import create_app
    from timepilot.native_bi_bundle import bundle_status,frontend_matches_bundle
    from system_chrome import chrome_executable,launch_installed_chrome
    from accept_native_bi_live import wait_workspace
    from playwright.sync_api import sync_playwright
    import uvicorn,httpx
    if report.get('status')!='passed_backend_same_source_browser_pending':
        raise RuntimeError('后端同源 PIT 验收尚未通过，禁止开始浏览器验收')
    folder=Path(report.get('qa_home','')).expanduser().resolve()
    if not (folder/'isolated_application').is_dir():raise RuntimeError('持久化 QA 目录不存在或已被删除')
    manifest=bundle_status(ROOT/'src/timepilot/static/native-bi')
    if not manifest.get('ready') or not frontend_matches_bundle(ROOT/'src/timepilot/static/native-bi',ROOT/'frontend/native-bi'):
        raise RuntimeError('Perspective JS/WASM 缺失、哈希错误或与前端源码不匹配')
    chrome_executable()
    app=create_app(home=folder/'isolated_application',embedded_worker=False)
    pid=report['qa_project_id']
    source_id=report['source_native_asset_id']
    derived_id=report['materialized_native_asset_id']
    for aid in (source_id,derived_id):
        app.state.projects.require_native(pid,aid)
        valid=app.state.native_bi.verify(aid)
        if not valid.get('valid'):raise RuntimeError('QA 数据资产已损坏：'+aid)
    expected=report['artifact']['sha256']
    derivation=app.state.native_bi.verify(derived_id)
    if derivation['actual_sha256']!=expected:
        raise RuntimeError('浏览器将打开的模型产物与后端物化哈希不相同')
    sockets=[];pages=[]
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    srv=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,log_level='error'))
    thread=threading.Thread(target=srv.run,daemon=True)
    thread.start()
    try:
        base=f'http://127.0.0.1:{port}'
        with httpx.Client(timeout=4) as http:
            for _ in range(80):
                try:
                    if http.get(base+'/api/v1/health').status_code==200:break
                except (OSError,httpx.HTTPError):pass
                time.sleep(.2)
            else:raise RuntimeError('FastAPI QA 服务无法启动')
        with sync_playwright() as playwright:
            browser=launch_installed_chrome(playwright)
            try:
                for aid in (source_id,derived_id):
                    page=browser.new_page()
                    page.on('websocket',lambda ws:sockets.append(ws.url))
                    page.goto(base+f'/projects/{pid}/bi/native/{aid}',wait_until='domcontentloaded',timeout=timeout*1000)
                    workspace=wait_workspace(page,timeout=timeout*1000)
                    if not page.locator('#tp-native-perspective perspective-viewer').count():
                        raise RuntimeError('真实 Perspective Viewer 元素未初始化')
                    panels=workspace.get('panels') or {}
                    if not panels:raise RuntimeError('原生 BI 没有真实可交互面板')
                    pages.append((page,aid,len(panels)))
                before=len(sockets)
                for page,aid,_ in pages:
                    page.reload(wait_until='domcontentloaded',timeout=timeout*1000)
                    again=wait_workspace(page,timeout=timeout*1000)
                    if not again.get('panels'):raise RuntimeError('刷新后 Perspective 面板未恢复：'+aid)
                if not sockets or len(sockets)<=before:
                    raise RuntimeError('没有观察到重载后的真实 Perspective WebSocket 连接')
                browser_version=browser.version
            finally:browser.close()
    finally:
        srv.should_exit=True
        thread.join(timeout=10)
    return {'status':'passed','qa_project_id':pid,'source_native_asset_id':source_id,
      'materialized_native_asset_id':derived_id,'source_sha256':report['source']['sha256'],
      'artifact_sha256':expected,'panel_count':sum(n for _,_,n in pages),
      'panel_mode':'two_real_native_asset_tabs','websocket_opened':True,
      'refresh_restored':True,'native_bundle_verified':True,'installed_chrome':True,
      'chrome_version':browser_version,
      'note':'两份同项目 BI 资产各有真实 Viewer 面板；不会把静态页面截图当作回执'}

def main(argv=None):
    p=argparse.ArgumentParser()
    p.add_argument('--backend',type=Path,required=True)
    p.add_argument('--out',type=Path,default=Path('reports/v031/same_source_browser.json'))
    p.add_argument('--timeout',type=int,default=30)
    args=p.parse_args(argv)
    try:
        backend=json.loads(args.backend.read_text(encoding='utf-8'))
        result=accept(backend,timeout=max(10,min(120,args.timeout)))
    except Exception as exc:
        result={'status':'blocked','error':type(exc).__name__+': '+str(exc)[:1500]}
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0 if result['status']=='passed' else 2
if __name__=='__main__':raise SystemExit(main())
