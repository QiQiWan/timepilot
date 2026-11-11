"""Real HTTP session, tenant, RBAC, CSRF and raw API/WebSocket isolation."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from timepilot.api import create_app
from timepilot.enterprise_security import EnterpriseSecurity


@pytest.fixture
def enterprise(tmp_path,monkeypatch):
    manager=EnterpriseSecurity(tmp_path)
    a=manager.bootstrap_tenant('factory-a','Factory A','admin-a','A!long-random-passphrase-2026')
    b=manager.bootstrap_tenant('factory-b','Factory B','admin-b','B!long-random-passphrase-2026')
    monkeypatch.setenv('TIMEPILOT_LOCAL_MODE','0')
    monkeypatch.setenv('TIMEPILOT_ENTERPRISE_MODE','1')
    monkeypatch.setenv('TIMEPILOT_PUBLIC_BASE_URL','https://testserver')
    app=create_app(home=tmp_path,embedded_worker=False)
    return app,manager,a,b


def login(c,tenant,user,password):
    result=c.post('/api/v1/auth/login',json={'tenant':tenant,'username':user,'password':password})
    assert result.status_code==200,result.text
    assert c.cookies.get('tp_session')
    return c.cookies.get('tp_csrf')


def write(c,url,payload,csrf,method='post',headers=None):
    return getattr(c,method)(url,json=payload,headers={'X-TimePilot-CSRF':csrf,**(headers or {})})


def test_local_mode_keeps_prior_workflow(tmp_path):
    with TestClient(create_app(home=tmp_path,embedded_worker=False)) as c:
        assert c.get('/api/v1/projects').status_code==200
        assert c.post('/api/v1/projects',json={'name':'Local'}).status_code==201


def test_enterprise_rejects_unbootstrapped_and_insecure_origin(tmp_path,monkeypatch):
    monkeypatch.setenv('TIMEPILOT_LOCAL_MODE','0')
    monkeypatch.setenv('TIMEPILOT_ENTERPRISE_MODE','1')
    monkeypatch.setenv('TIMEPILOT_PUBLIC_BASE_URL','https://testserver')
    with pytest.raises(Exception,match='管理员'):
        create_app(home=tmp_path,embedded_worker=False)
    manager=EnterpriseSecurity(tmp_path)
    manager.bootstrap_tenant('factory-a','Factory A','admin-a','A!long-random-passphrase-2026')
    monkeypatch.setenv('TIMEPILOT_PUBLIC_BASE_URL','http://localhost:8000')
    with pytest.raises(RuntimeError,match='HTTPS'):
        create_app(home=tmp_path,embedded_worker=False)


def test_enterprise_auth_scope_rbac_and_csrf(enterprise):
    app,security,a,b=enterprise
    with TestClient(app,base_url='https://testserver') as admin:
        assert admin.get('/api/v1/projects').status_code==401
        assert admin.get('/projects',follow_redirects=False).status_code==303
        assert admin.get('/login').status_code==200
        assert admin.post('/api/v1/projects',json={'name':'Denied'}).status_code==401
        csrf_a=login(admin,'factory-a','admin-a','A!long-random-passphrase-2026')
        assert admin.get('/api/v1/auth/me').json()['tenant_id']==a
        assert write(admin,'/api/v1/projects',{'name':'Blocked'},'wrong').status_code==403
        assert write(admin,'/api/v1/projects',{'name':'Blocked'},csrf_a,headers={'Origin':'https://other.test'}).status_code==403
        project=write(admin,'/api/v1/projects',{'name':'Station A'},csrf_a)
        assert project.status_code==201,project.text
        pid=project.json()['id']
        assert project.json()['tenant_id']==a
        assert admin.get('/api/v1/projects/'+pid).status_code==200
        assert admin.get('/api/v1/datasets').status_code==403
        assert admin.get('/api/v1/native-bi/assets').status_code==403
        # Global/stream sockets and missing project context are denied by the
        # enterprise ASGI guard even before the application WebSocket handler.
        with pytest.raises(WebSocketDisconnect):
            with admin.websocket_connect('/api/v1/native-bi/streams/'+pid+'/ws',
                                         headers={'origin':'https://testserver'}):pass
        assert admin.post('/api/v1/native-bi/assets/upload',headers={'X-TimePilot-CSRF':csrf_a}).status_code==403
        created=write(admin,'/api/v1/identity/users',{'username':'inspector','password':'Inspector-LONG-Secret-2026'},csrf_a)
        assert created.status_code==201,created.text
        user_id=created.json()['user_id']
        grant=write(admin,f'/api/v1/identity/projects/{pid}/members',{'user_id':user_id,'role':'viewer'},csrf_a)
        assert grant.status_code==201,grant.text
        with TestClient(app,base_url='https://testserver') as user:
            csrf_u=login(user,'factory-a','inspector','Inspector-LONG-Secret-2026')
            assert [x['id'] for x in user.get('/api/v1/projects').json()]==[pid]
            assert user.get('/api/v1/projects/'+pid).status_code==200
            assert write(user,f'/api/v1/projects/{pid}',{'name':'tamper'},csrf_u,method='patch').status_code==403
            assert write(user,'/api/v1/projects',{'name':'New'},csrf_u).status_code==403
            assert user.get('/api/v1/native-bi/assets/'+pid,headers={'X-TimePilot-Project':pid}).status_code==404
            with pytest.raises(WebSocketDisconnect):
                with user.websocket_connect('/api/v1/native-bi/assets/'+pid+'/versions/base/ws?project_id='+pid,
                                            headers={'origin':'https://testserver'}):pass
        with TestClient(app,base_url='https://testserver') as other:
            csrf_b=login(other,'factory-b','admin-b','B!long-random-passphrase-2026')
            assert other.get('/api/v1/projects') .json()==[]
            assert other.get('/api/v1/projects/'+pid).status_code==404
            assert write(other,f'/api/v1/identity/projects/{pid}/members',{'user_id':user_id,'role':'editor'},csrf_b).status_code==404
            assert other.get('/api/v1/data-assets/si_'+('0'*32),headers={'X-TimePilot-Project':pid}).status_code==404
        assert admin.get('/identity').status_code==200
        assert admin.get('/api/v1/identity/users').status_code==200
        assert admin.get(f'/api/v1/identity/projects/{pid}/members').status_code==200
        # Tenant administrator can revoke a project member's sessions; the
        # session cookie remains present client-side but server rejects it.
        with TestClient(app,base_url='https://testserver') as revoked:
            login(revoked,'factory-a','inspector','Inspector-LONG-Secret-2026')
            assert revoked.get('/api/v1/projects').status_code==200
            assert write(admin,f'/api/v1/identity/users/{user_id}',{'active':False},csrf_a,method='patch').status_code==200
            assert revoked.get('/api/v1/projects').status_code==401
            assert write(admin,f'/api/v1/identity/users/{user_id}',{'active':True},csrf_a,method='patch').status_code==200
            assert revoked.get('/api/v1/projects').status_code==401  # revoked session stays dead
        assert write(admin,f'/api/v1/identity/users/{admin.get("/api/v1/auth/me").json()["user_id"]}',
                     {'active':False},csrf_a,method='patch').status_code==403
        audit=admin.get('/api/v1/identity/audit').json()
        assert any(row['event']=='user_deactivated' for row in audit)
        assert write(admin,'/api/v1/auth/logout',{},csrf_a).status_code==200
        assert admin.get('/api/v1/projects').status_code==401


def test_old_projects_not_claimed_implicitly(tmp_path,monkeypatch):
    local=create_app(home=tmp_path,embedded_worker=False)
    with TestClient(local) as c:
        old=c.post('/api/v1/projects',json={'name':'legacy'}).json()['id']
    security=EnterpriseSecurity(tmp_path)
    tenant=security.bootstrap_tenant('factory-c','Factory C','admin-c','C!long-random-passphrase-2026')
    monkeypatch.setenv('TIMEPILOT_LOCAL_MODE','0')
    monkeypatch.setenv('TIMEPILOT_ENTERPRISE_MODE','1')
    monkeypatch.setenv('TIMEPILOT_PUBLIC_BASE_URL','https://testserver')
    with TestClient(create_app(home=tmp_path,embedded_worker=False),base_url='https://testserver') as c:
        login(c,'factory-c','admin-c','C!long-random-passphrase-2026')
        assert c.get('/api/v1/projects').json()==[]
        assert c.get('/api/v1/projects/'+old).status_code==404
        # Pre-existing local-mode projects require an explicit offline tenant migration.
        assert old in {p['id'] for p in security.preview_legacy_projects()}
        assert security.claim_legacy_projects(tenant,project_ids=[old])==1
        assert c.get('/api/v1/projects/'+old).status_code==200
