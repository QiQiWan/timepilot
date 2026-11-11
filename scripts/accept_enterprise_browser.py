"""Real local Chromium + HTTPS + session/RBAC browser acceptance.

Runs against a temporary SQLite home and an ephemeral localhost TLS server.
No DuckDB/Perspective/network download required. It does *not* certify external
reverse proxies, identity federation or production TLS configuration.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from ipaddress import ip_address
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
sys.path.insert(0,str(ROOT/'scripts'))
from system_chrome import chrome_launch_options


def localhost_cert(path:Path):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes,serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    private=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    subject=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'TimePilot local acceptance')])
    now=datetime.now(timezone.utc)
    certificate=(x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
        .public_key(private.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now-timedelta(days=1)).not_valid_after(now+timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost'),
            x509.IPAddress(ip_address('127.0.0.1'))]),critical=False)
        .sign(private,hashes.SHA256()))
    cert=path/'testcert.pem';key=path/'testkey.pem'
    key.write_bytes(private.private_bytes(serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,serialization.NoEncryption()))
    cert.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key.chmod(0o600)
    return cert,key


def run(out:Path):
    import httpx
    import uvicorn
    from playwright.sync_api import sync_playwright
    from timepilot.api import create_app
    from timepilot.enterprise_security import EnterpriseSecurity
    from timepilot.projects import ProjectCreate
    from timepilot.enterprise_security import CURRENT_PRINCIPAL

    started=time.perf_counter();events=[]
    report={'status':'running','browser':'local installed Chrome/Chromium','stages':events,
            'source':'real HTTPS FastAPI HTTP+SQLite+browser, isolated temporary home',
            'limitations':['DuckDB/Perspective BI is covered by separate native acceptance',
                           'external TLS reverse proxy and SSO are not exercised']}
    def stage(name,**details):
        events.append({'name':name,'status':'passed',**details});
        out.parent.mkdir(parents=True,exist_ok=True)
        out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    with tempfile.TemporaryDirectory(prefix='tp_enterprise_browser_') as temp:
        work=Path(temp)
        with socket.socket() as server_sock:
            server_sock.bind(('127.0.0.1',0));port=server_sock.getsockname()[1]
        base=f'https://127.0.0.1:{port}'
        old_env={name:os.environ.get(name) for name in ('TIMEPILOT_LOCAL_MODE','TIMEPILOT_ENTERPRISE_MODE',
            'TIMEPILOT_PUBLIC_BASE_URL')}
        os.environ.update(TIMEPILOT_LOCAL_MODE='0',TIMEPILOT_ENTERPRISE_MODE='1',TIMEPILOT_PUBLIC_BASE_URL=base)
        security=EnterpriseSecurity(work/'runtime')
        tenant_a=security.bootstrap_tenant('plant-one','Plant One','admin-one','Plant-One-Secret-2026!')
        tenant_b=security.bootstrap_tenant('plant-two','Plant Two','admin-two','Plant-Two-Secret-2026!')
        app=create_app(home=work/'runtime',embedded_worker=False)
        cert,key=localhost_cert(work)
        server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,
                    ssl_keyfile=str(key),ssl_certfile=str(cert),log_level='error',access_log=False))
        runner=threading.Thread(target=server.run,daemon=True,name='tp_accept_https')
        runner.start()
        try:
            with httpx.Client(base_url=base,verify=False,timeout=15.0) as http:
                for _ in range(100):
                    try:
                        if http.get('/api/v1/health').status_code==200:break
                    except httpx.HTTPError:pass
                    time.sleep(.05)
                else:raise AssertionError('HTTPS server startup timed out')
                stage('real_https_server',url=base)
                with httpx.Client(base_url=base,verify=False,timeout=15) as c1, \
                     httpx.Client(base_url=base,verify=False,timeout=15) as c2:
                    assert c1.get('/api/v1/projects',follow_redirects=False).status_code==401
                    def signin(client,tenant,user,password):
                        response=client.post('/api/v1/auth/login',json={
                            'tenant':tenant,'username':user,'password':password})
                        assert response.status_code==200,response.text
                        assert client.cookies.get('tp_session')
                        return client.cookies.get('tp_csrf')
                    ca=signin(c1,'plant-one','admin-one','Plant-One-Secret-2026!')
                    cb=signin(c2,'plant-two','admin-two','Plant-Two-Secret-2026!')
                    x=c1.post('/api/v1/projects',json={'name':'HTTP Tenant One'},
                        headers={'X-TimePilot-CSRF':ca})
                    y=c2.post('/api/v1/projects',json={'name':'HTTP Tenant Two'},
                        headers={'X-TimePilot-CSRF':cb})
                    assert x.status_code==201 and y.status_code==201,(x.text,y.text)
                    xa=x.json()['id'];yb=y.json()['id']
                    assert c1.get('/api/v1/projects/'+yb).status_code==404
                    assert c2.get('/api/v1/projects/'+xa).status_code==404
                    assert c1.get('/api/v1/native-bi/assets').status_code==403
                    assert c1.get('/identity').status_code==200
                    assert c1.get('/api/v1/identity/users').status_code==200
                    invalid=c1.post('/api/v1/projects',json={'name':'Invalid'},
                        headers={'X-TimePilot-CSRF':'wrong'})
                    assert invalid.status_code==403,invalid.text
                    stage('real_https_cookie_csrf_cross_tenant_and_identity_api',
                          tenant_a_project=xa,tenant_b_project=yb,
                          identity_admin_page=True)
                with sync_playwright() as p:
                    options=chrome_launch_options()
                    options['args']=['--disable-dev-shm-usage','--no-proxy-server','--proxy-bypass-list=*']
                    if hasattr(os,'geteuid') and os.geteuid()==0:
                        options['args'].append('--no-sandbox')  # only for root-owned CI containers
                    browser=p.chromium.launch(**options)
                    try:
                        def login(tenant,username,password):
                            context=browser.new_context(ignore_https_errors=True)
                            page=context.new_page();page.goto(base+'/projects')
                            page.wait_for_url('**/login',timeout=12000)
                            page.locator('input[name="tenant"]').fill(tenant)
                            page.locator('input[name="username"]').fill(username)
                            page.locator('input[name="password"]').fill(password)
                            page.locator('#login-form button').click()
                            page.wait_for_url('**/projects',timeout=20000)
                            assert page.evaluate("() => document.cookie.includes('tp_csrf=')")
                            return context,page
                        ctx_a,page_a=login('plant-one','admin-one','Plant-One-Secret-2026!')
                        stage('login_full_browser_with_secure_session_cookie',browser=browser.version)
                        # All requests originate in the real browser, including writes with
                        # the production CSRF interception. Existing UI is the API client.
                        created_a=page_a.evaluate("""async () => {
                          const r=await fetch('/api/v1/projects',{method:'POST',headers:{'content-type':'application/json'},
                            body:JSON.stringify({name:'Plant One Training'})});
                          return {status:r.status,body:await r.json()};} """)
                        assert created_a['status']==201,created_a
                        id_a=created_a['body']['id']
                        stage('browser_csrf_protected_project_creation',project_id=id_a)
                        ctx_b,page_b=login('plant-two','admin-two','Plant-Two-Secret-2026!')
                        created_b=page_b.evaluate("""async () => {
                          const r=await fetch('/api/v1/projects',{method:'POST',headers:{'content-type':'application/json'},
                            body:JSON.stringify({name:'Plant Two Quality'})});
                          return {status:r.status,body:await r.json()};} """)
                        assert created_b['status']==201,created_b
                        id_b=created_b['body']['id']
                        def check_scope(page,its,other):
                            result=page.evaluate('''async ids => {
                                const own=await(await fetch('/api/v1/projects')).json();
                                const cross=await fetch('/api/v1/projects/'+ids.other);
                                const legacy=await fetch('/api/v1/datasets');
                                const noCsrf=await fetch('/api/v1/projects',{method:'POST',
                                  headers:{'content-type':'application/json'},
                                  body:JSON.stringify({name:'from-ui'})});
                                return {own:own.map(x=>x.id),cross:cross.status,legacy:legacy.status,
                                  same_origin_write:noCsrf.status};
                              }''',{'its':its,'other':other})
                            assert result['own']==[its],result
                            assert result['cross']==404,result
                            assert result['legacy']==403,result
                            assert result['same_origin_write']==201,result
                        check_scope(page_a,id_a,id_b)
                        check_scope(page_b,id_b,id_a)
                        stage('browser_cross_tenant_isolation',tenant_a=tenant_a,tenant_b=tenant_b)
                        wrong_csrf=page_a.evaluate("""async () => {
                           const r=await fetch('/api/v1/projects',{method:'POST',
                              headers:{'content-type':'application/json','X-TimePilot-CSRF':'tampered'},
                              body:JSON.stringify({name:'must-not-create'})});return r.status; }""")
                        # security-client intentionally overwrites the CSRF header with the
                        # expected cookie. Test the actual protection via raw URL fetch in
                        # an isolated browser context without the patched app script.
                        context_raw=browser.new_context(ignore_https_errors=True)
                        for cookie in ctx_a.cookies():context_raw.add_cookies([cookie])
                        raw_page=context_raw.new_page();raw_page.goto(base+'/login')
                        tampered=raw_page.evaluate("""async () => {
                          const r=await fetch('/api/v1/projects',{method:'POST',
                            headers:{'content-type':'application/json','X-TimePilot-CSRF':'wrong'},
                            body:JSON.stringify({name:'invalid'})});return r.status;}""")
                        assert tampered==403,(tampered,wrong_csrf)
                        stage('real_browser_csrf_tamper_rejected')
                        ctx_a.close();ctx_b.close();context_raw.close()
                    finally:browser.close()
            report['status']='passed'
        except Exception as exc:
            message=f'{type(exc).__name__}: {exc}'
            if 'ERR_BLOCKED_BY_ADMINISTRATOR' in message:
                report['status']='blocked'
                report['blocker']='当前受管 Chromium 禁止访问临时 HTTPS 站点；请在目标设备的已安装 Chrome 重新验收'
                report['diagnostic']=message[:900]
            else:
                report['status']='failed';report['exception']=message
                raise
        finally:
            server.should_exit=True;runner.join(timeout=12)
            for name,old in old_env.items():
                if old is None:os.environ.pop(name,None)
                else:os.environ[name]=old
            report['elapsed_seconds']=round(time.perf_counter()-started,3)
            out.parent.mkdir(parents=True,exist_ok=True)
            out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return report


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--out',type=Path,default=ROOT/'reports'/'enterprise_browser_acceptance.json')
    args=parser.parse_args()
    report=run(args.out)
    print(json.dumps({'status':report['status'],'elapsed_seconds':report['elapsed_seconds'],
                      'report':str(args.out.resolve())},ensure_ascii=False))
    return 0 if report['status']=='passed' else (2 if report['status']=='blocked' else 1)

if __name__=='__main__':raise SystemExit(main())
