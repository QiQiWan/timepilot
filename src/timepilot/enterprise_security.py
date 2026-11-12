"""Fail-closed enterprise identity and tenant boundary for TimePilot.

This is a self-hosted password/session implementation. It is deliberately opt-in,
with offline tenant bootstrap and explicit legacy-project claiming. It does not
pretend to implement external SSO/SCIM or a distributed authorization service.
Every non-project/legacy API is denied in enterprise mode unless explicitly
scoped by the policy below. WebSockets must pass the same project checks.
"""
from __future__ import annotations

import contextvars
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

SESSION_COOKIE = 'tp_session'
CSRF_COOKIE = 'tp_csrf'
SESSION_SECONDS = 8 * 60 * 60
ROLE_RANK = {'viewer': 0, 'analyst': 1, 'editor': 2, 'project_admin': 3}
CURRENT_PRINCIPAL: contextvars.ContextVar['Principal | None'] = contextvars.ContextVar('timepilot_principal', default=None)


@dataclass(frozen=True)
class Principal:
    user_id: str
    tenant_id: str
    username: str
    tenant_role: str
    session_id: str
    csrf_hash: str


class SecurityError(PermissionError):
    def __init__(self, message: str = '没有权限访问此资源', code: int = 403):
        self.code = code
        super().__init__(message)


def _sha(raw: str) -> str:
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def _password(password: str, salt: bytes) -> str:
    return hashlib.scrypt(password.encode('utf-8'), salt=salt, n=2**14, r=8, p=1, dklen=32).hex()


def _identifier(value: str) -> str:
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError):
        raise SecurityError('资源编号无效', 404) from None


class EnterpriseSecurity:
    def __init__(self, home: Path, *, require_admin: bool = False):
        self.home = Path(home)
        self.root = self.home / 'security'
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.root, 0o700)
        except OSError:
            pass
        self.db_file = self.root / 'identity.sqlite3'
        self.project_db = self.home / 'projects' / 'catalog.sqlite3'
        self._setup()
        if require_admin:
            with self._db() as con:
                if not con.execute("SELECT 1 FROM users WHERE tenant_role='tenant_admin' AND active=1 LIMIT 1").fetchone():
                    raise SecurityError('企业模式没有管理员。请先离线执行 identity bootstrap；拒绝开放服务')

    def _db(self):
        con = sqlite3.connect(self.db_file, timeout=15)
        con.row_factory = sqlite3.Row
        con.execute('PRAGMA foreign_keys=ON')
        con.execute('PRAGMA busy_timeout=15000')
        return con

    def _setup(self):
        with self._db() as con:
            con.execute('PRAGMA journal_mode=WAL')
            con.executescript('''
            CREATE TABLE IF NOT EXISTS tenants(
                tenant_id TEXT PRIMARY KEY,slug TEXT NOT NULL UNIQUE,name TEXT NOT NULL,
                created_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS users(
                user_id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id),
                username TEXT NOT NULL,password_salt TEXT NOT NULL,password_hash TEXT NOT NULL,
                tenant_role TEXT NOT NULL CHECK(tenant_role IN ('tenant_admin','member')),
                active INTEGER NOT NULL DEFAULT 1,failed_logins INTEGER NOT NULL DEFAULT 0,
                locked_until REAL NOT NULL DEFAULT 0,created_at REAL NOT NULL,
                UNIQUE(tenant_id,username));
            CREATE TABLE IF NOT EXISTS project_members(
                tenant_id TEXT NOT NULL,user_id TEXT NOT NULL REFERENCES users(user_id),
                project_id TEXT NOT NULL,role TEXT NOT NULL CHECK(role IN
                    ('viewer','analyst','editor','project_admin')),
                granted_at REAL NOT NULL, PRIMARY KEY(user_id,project_id));
            CREATE INDEX IF NOT EXISTS ix_members_project ON project_members(tenant_id,project_id);
            CREATE TABLE IF NOT EXISTS sessions(
                session_hash TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(user_id),
                csrf_hash TEXT NOT NULL,expires_at REAL NOT NULL,created_at REAL NOT NULL,
                revoked INTEGER NOT NULL DEFAULT 0);
            CREATE INDEX IF NOT EXISTS ix_sessions_expiry ON sessions(expires_at);
            CREATE TABLE IF NOT EXISTS identity_audit(
                event_id TEXT PRIMARY KEY,time REAL NOT NULL,tenant_id TEXT,
                user_id TEXT,event TEXT NOT NULL,resource TEXT,detail TEXT);
            ''')
        try:
            os.chmod(self.db_file, 0o600)
        except OSError:
            pass

    def audit(self, event: str, principal: Principal | None = None, *, tenant_id: str | None = None,
              user_id: str | None = None, resource: str = '', detail: str = ''):
        # Store no passwords, session tokens, CSRF values or user-supplied request bodies.
        with self._db() as con:
            con.execute('INSERT INTO identity_audit VALUES(?,?,?,?,?,?,?)',
                        (str(uuid4()), time.time(), tenant_id or (principal.tenant_id if principal else None),
                         user_id or (principal.user_id if principal else None), event, resource[:200], detail[:200]))

    def bootstrap_tenant(self, slug: str, name: str, username: str, password: str) -> str:
        if not re.fullmatch(r'[a-z][a-z0-9-]{2,48}', slug):
            raise ValueError('租户标识只允许 3-49 位小写英文、数字和连字符')
        if not re.fullmatch(r'[a-zA-Z][a-zA-Z0-9_.-]{2,63}', username):
            raise ValueError('用户名需要 3-64 位英文、数字、点、下划线或连字符')
        if len(password) < 14 or len(password) > 256:
            raise ValueError('管理员口令长度必须为 14-256 位')
        tenant = str(uuid4())
        uid = str(uuid4())
        salt = secrets.token_bytes(16)
        with self._db() as con:
            con.execute('INSERT INTO tenants VALUES(?,?,?,?)', (tenant, slug, name[:120], time.time()))
            con.execute('INSERT INTO users(user_id,tenant_id,username,password_salt,password_hash,tenant_role,created_at) VALUES(?,?,?,?,?,?,?)',
                        (uid, tenant, username.casefold(), salt.hex(), _password(password,salt), 'tenant_admin',time.time()))
        self.audit('tenant_bootstrap',tenant_id=tenant,user_id=uid,resource=slug)
        return tenant

    def create_user(self, actor: Principal, username: str, password: str) -> dict:
        if actor.tenant_role != 'tenant_admin':
            raise SecurityError('仅租户管理员可创建账户')
        if not re.fullmatch(r'[a-zA-Z][a-zA-Z0-9_.-]{2,63}', username) or not 14 <= len(password) <= 256:
            raise ValueError('用户名或口令不符合要求（口令最少 14 位）')
        uid = str(uuid4()); salt = secrets.token_bytes(16)
        with self._db() as con:
            con.execute('INSERT INTO users(user_id,tenant_id,username,password_salt,password_hash,tenant_role,created_at) VALUES(?,?,?,?,?,?,?)',
                        (uid,actor.tenant_id,username.casefold(),salt.hex(),_password(password,salt),'member',time.time()))
        self.audit('user_created',actor,resource=uid)
        return {'user_id':uid,'username':username.casefold(),'tenant_id':actor.tenant_id}

    def set_user_active(self, actor: Principal, user_id: str, active: bool):
        if actor.tenant_role != 'tenant_admin':
            raise SecurityError('仅租户管理员可调整用户状态')
        user_id=_identifier(user_id)
        if user_id==actor.user_id and not active:
            raise SecurityError('不能停用当前已登录的管理员账户')
        with self._db() as con:
            row=con.execute('SELECT user_id,tenant_role,active FROM users WHERE user_id=? AND tenant_id=?',
                            (user_id,actor.tenant_id)).fetchone()
            if row is None:raise SecurityError('用户不存在或不属于当前租户',404)
            if row['tenant_role']=='tenant_admin' and not active:
                count=con.execute("SELECT COUNT(*) FROM users WHERE tenant_id=? AND tenant_role='tenant_admin' AND active=1",
                                  (actor.tenant_id,)).fetchone()[0]
                if count<=1:raise SecurityError('不能停用租户唯一的有效管理员')
            con.execute('UPDATE users SET active=? WHERE user_id=?',(int(active),user_id))
            if not active:
                con.execute('UPDATE sessions SET revoked=1 WHERE user_id=?',(user_id,))
        self.audit('user_activated' if active else 'user_deactivated',actor,resource=user_id)
        return {'user_id':user_id,'active':active,'sessions_revoked':not active}

    def _project_row(self, project_id: str):
        if not self.project_db.is_file():
            raise SecurityError('项目目录不存在',404)
        with sqlite3.connect(self.project_db) as con:
            con.row_factory = sqlite3.Row
            try:
                row = con.execute('SELECT id,tenant_id FROM projects WHERE id=?',(_identifier(project_id),)).fetchone()
            except sqlite3.OperationalError:
                raise SecurityError('项目目录尚未升级租户字段',503) from None
        if row is None:
            raise SecurityError('项目不存在',404)
        return dict(row)

    def allowed_project(self, principal: Principal, project_id: str, *, required: str='viewer') -> str:
        row = self._project_row(project_id)
        if row['tenant_id'] != principal.tenant_id:
            raise SecurityError('项目不存在',404)
        if principal.tenant_role == 'tenant_admin':
            return 'tenant_admin'
        with self._db() as con:
            member = con.execute('SELECT role FROM project_members WHERE user_id=? AND tenant_id=? AND project_id=?',
                                 (principal.user_id,principal.tenant_id,project_id)).fetchone()
        if member is None or ROLE_RANK[member['role']] < ROLE_RANK[required]:
            raise SecurityError('没有此项目的操作权限',403)
        return member['role']

    def visible_project_ids(self, principal: Principal) -> list[str] | None:
        if principal.tenant_role == 'tenant_admin':
            return None
        with self._db() as con:
            return [row[0] for row in con.execute('SELECT project_id FROM project_members WHERE tenant_id=? AND user_id=?',
                                                 (principal.tenant_id,principal.user_id)).fetchall()]

    def attach_owner(self, principal: Principal, project_id: str):
        if principal.tenant_role != 'tenant_admin':
            raise SecurityError('仅租户管理员可以新建项目')
        self.grant_member(principal, project_id, principal.user_id, 'project_admin')

    def grant_member(self, principal: Principal, project_id: str, user_id: str, role: str):
        if role not in ROLE_RANK:
            raise ValueError('未知项目角色')
        current_role = self.allowed_project(principal, project_id, required='project_admin')
        if current_role not in ('tenant_admin','project_admin'):
            raise SecurityError('仅项目管理员可以授权')
        with self._db() as con:
            member = con.execute('SELECT user_id FROM users WHERE user_id=? AND tenant_id=? AND active=1',
                                 (_identifier(user_id),principal.tenant_id)).fetchone()
            if not member:
                raise SecurityError('用户不存在或不在当前租户',404)
            con.execute('INSERT INTO project_members(tenant_id,user_id,project_id,role,granted_at) VALUES(?,?,?,?,?) '
                        'ON CONFLICT(user_id,project_id) DO UPDATE SET role=excluded.role,granted_at=excluded.granted_at',
                        (principal.tenant_id,user_id,project_id,role,time.time()))
        self.audit('member_grant',principal,resource=project_id,detail=role)

    def preview_legacy_projects(self) -> list[dict]:
        """Historical local projects remain invisible until explicitly claimed offline."""
        with sqlite3.connect(self.project_db) as con:
            con.row_factory=sqlite3.Row
            return [dict(row) for row in con.execute(
                "SELECT id,name,tenant_id FROM projects WHERE tenant_id IN ('local','legacy-unassigned') ORDER BY name,id")]

    def claim_legacy_projects(self, tenant_id: str, *, project_ids: list[str] | None = None) -> int:
        """Explicit offline assignment; allow a subset to prevent cross-tenant data grants.

        Existing v0.20.1 local projects carry tenant_id='local'; older migrated
        projects may carry 'legacy-unassigned'. Both are unowned in enterprise.
        """
        tenant_id = _identifier(tenant_id)
        with self._db() as con:
            if not con.execute('SELECT 1 FROM tenants WHERE tenant_id=?',(tenant_id,)).fetchone():
                raise ValueError('租户不存在')
        ids=None if project_ids is None else [_identifier(pid) for pid in project_ids]
        with sqlite3.connect(self.project_db) as con:
            if ids is not None:
                if not ids:return 0
                owned=con.execute('SELECT id,tenant_id FROM projects WHERE id IN ('+
                                  ','.join('?' for _ in ids)+')',ids).fetchall()
                if len(owned)!=len(set(ids)) or any(row[1] not in ('local','legacy-unassigned') for row in owned):
                    raise SecurityError('选择的项目不存在或已有租户归属，拒绝部分迁移',409)
                affected=con.execute('UPDATE projects SET tenant_id=? WHERE id IN ('+
                                     ','.join('?' for _ in ids)+')',(tenant_id,*ids)).rowcount
            else:
                affected=con.execute("UPDATE projects SET tenant_id=? WHERE tenant_id IN ('local','legacy-unassigned')",
                                     (tenant_id,)).rowcount
        self.audit('legacy_projects_claimed',tenant_id=tenant_id,detail=f'count={affected}')
        return affected

    def login(self, tenant: str, username: str, password: str):
        now = time.time()
        with self._db() as con:
            row = con.execute('''SELECT u.*,t.slug FROM users u JOIN tenants t ON t.tenant_id=u.tenant_id
                WHERE t.slug=? AND u.username=?''',(tenant,username.strip().casefold())).fetchone()
            # Run password KDF even when unknown to reduce credential enumeration timing.
            salt = bytes.fromhex(row['password_salt']) if row else b'\0'*16
            valid = bool(row and hmac.compare_digest(_password(password,salt),row['password_hash']))
            if not row or not row['active'] or row['locked_until']>now or not valid:
                if row and row['active']:
                    failures = row['failed_logins']+1
                    con.execute('UPDATE users SET failed_logins=?,locked_until=? WHERE user_id=?',
                                (failures, now+900 if failures>=5 else 0,row['user_id']))
                raise SecurityError('租户、账户或密码无效，或账户暂时锁定',401)
            con.execute('UPDATE users SET failed_logins=0,locked_until=0 WHERE user_id=?',(row['user_id'],))
            session = secrets.token_urlsafe(48); csrf = secrets.token_urlsafe(32)
            con.execute('INSERT INTO sessions VALUES(?,?,?,?,?,0)',
                        (_sha(session),row['user_id'],_sha(csrf),now+SESSION_SECONDS,now))
        principal = Principal(row['user_id'],row['tenant_id'],row['username'],row['tenant_role'],_sha(session),_sha(csrf))
        self.audit('login',principal)
        return principal,session,csrf

    def authenticate(self, raw: str | None) -> Principal | None:
        if not raw or len(raw)>256:
            return None
        now = time.time()
        with self._db() as con:
            row = con.execute('''SELECT s.*,u.tenant_id,u.username,u.tenant_role,u.active
                FROM sessions s JOIN users u ON s.user_id=u.user_id
                WHERE s.session_hash=?''',(_sha(raw),)).fetchone()
        if row is None or row['revoked'] or row['expires_at']<=now or not row['active']:
            return None
        return Principal(row['user_id'],row['tenant_id'],row['username'],row['tenant_role'],row['session_hash'],row['csrf_hash'])

    def revoke(self, principal: Principal):
        with self._db() as con:
            con.execute('UPDATE sessions SET revoked=1 WHERE session_hash=?',(principal.session_id,))
        self.audit('logout',principal)

    def verify_csrf(self, principal: Principal, token: str | None) -> bool:
        return bool(token and len(token)<=128 and hmac.compare_digest(_sha(token),principal.csrf_hash))

    def asset_access(self, principal: Principal, project_id: str, asset_id: str, *, linked: bool=False, required='viewer'):
        self.allowed_project(principal,project_id,required=required)
        asset_id = (str(asset_id) if linked and re.fullmatch(r'si_[a-f0-9]{32}',str(asset_id)) else _identifier(asset_id))
        with sqlite3.connect(self.project_db) as con:
            if linked:
                sql='SELECT 1 FROM project_assets WHERE project_id=? AND asset_id=?'
                params=(project_id,asset_id)
            else:
                sql='''SELECT 1 FROM project_generic_assets WHERE project_id=? AND native_asset_id=?
                    UNION SELECT 1 FROM project_native_assets WHERE project_id=? AND native_asset_id=?'''
                params=(project_id,asset_id,project_id,asset_id)
            row = con.execute(sql,params).fetchone()
        if not row:
            raise SecurityError('项目中不存在该 BI 资产',404)

    def import_access(self, principal: Principal, project_id: str, import_id: str, *, required='viewer'):
        self.allowed_project(principal, project_id, required=required)
        with sqlite3.connect(self.project_db) as con:
            row=con.execute('SELECT 1 FROM project_imports WHERE project_id=? AND import_id=?',
                            (project_id,_identifier(import_id))).fetchone()
        if not row:
            raise SecurityError('项目中不存在该导入记录',404)

    def list_users(self, principal: Principal):
        if principal.tenant_role != 'tenant_admin':
            raise SecurityError('仅租户管理员可查阅成员目录')
        with self._db() as con:
            sql = ('SELECT user_id,username,tenant_role,active,created_at '
                   'FROM users WHERE tenant_id=? ORDER BY username')
            return [dict(x) for x in con.execute(sql,(principal.tenant_id,)).fetchall()]

    def list_members(self, principal: Principal, project_id: str):
        self.allowed_project(principal,project_id,required='project_admin')
        with self._db() as con:
            sql = ('SELECT m.user_id,u.username,m.role,m.granted_at FROM project_members AS m '
                   'JOIN users AS u ON m.user_id=u.user_id WHERE m.tenant_id=? '
                   'AND m.project_id=? ORDER BY u.username')
            return [dict(x) for x in con.execute(sql,(principal.tenant_id,project_id)).fetchall()]

    def list_audit(self, principal: Principal, limit: int=100):
        if principal.tenant_role != 'tenant_admin':
            raise SecurityError('仅租户管理员可查阅账户审计')
        with self._db() as con:
            return [dict(x) for x in con.execute('''SELECT time,user_id,event,resource,detail FROM identity_audit
                WHERE tenant_id=? ORDER BY time DESC LIMIT ?''',(principal.tenant_id,min(200,max(1,limit)))).fetchall()]


class LoginPayload(BaseModel):
    tenant: str = Field(min_length=3,max_length=60)
    username: str = Field(min_length=3,max_length=70)
    password: str = Field(min_length=1,max_length=256)


class UserPayload(BaseModel):
    username: str = Field(min_length=3,max_length=64)
    password: str = Field(min_length=14,max_length=256)


class UserStatusPayload(BaseModel):
    active: bool


class MemberPayload(BaseModel):
    user_id: str
    role: str


def identity_router(security: EnterpriseSecurity, *, secure_cookies=True):
    router = APIRouter(tags=['Identity'])

    def principal_of(request: Request) -> Principal:
        principal = getattr(request.state,'principal',None)
        if principal is None:
            raise HTTPException(401,'请先登录')
        return principal

    @router.post('/api/v1/auth/login')
    def login(data: LoginPayload, response: Response):
        try:
            principal,raw,csrf=security.login(data.tenant,data.username,data.password)
        except SecurityError as exc:
            raise HTTPException(exc.code,str(exc)) from exc
        opts={'path':'/','secure':secure_cookies,'samesite':'strict'}
        response.set_cookie(SESSION_COOKIE,raw,max_age=SESSION_SECONDS,httponly=True,**opts)
        response.set_cookie(CSRF_COOKIE,csrf,max_age=SESSION_SECONDS,httponly=False,**opts)
        response.headers['Cache-Control']='no-store'
        return {'authenticated':True,'user':principal.username,'tenant_id':principal.tenant_id,'tenant_role':principal.tenant_role}

    @router.get('/api/v1/auth/me')
    def me(request: Request):
        principal=principal_of(request)
        return {'user_id':principal.user_id,'username':principal.username,
                'tenant_id':principal.tenant_id,'tenant_role':principal.tenant_role}

    @router.post('/api/v1/auth/logout')
    def logout(request: Request,response: Response):
        principal=principal_of(request)
        security.revoke(principal)
        response.delete_cookie(SESSION_COOKIE,path='/')
        response.delete_cookie(CSRF_COOKIE,path='/')
        return {'authenticated':False}

    @router.get('/api/v1/identity/users')
    def users(request: Request):
        try:return security.list_users(principal_of(request))
        except SecurityError as exc:raise HTTPException(exc.code,str(exc)) from exc

    @router.get('/api/v1/identity/projects/{pid}/members')
    def members(request: Request,pid:str):
        try:return security.list_members(principal_of(request),pid)
        except SecurityError as exc:raise HTTPException(exc.code,str(exc)) from exc

    @router.post('/api/v1/identity/users',status_code=201)
    def create_user(request: Request, payload: UserPayload):
        try:return security.create_user(principal_of(request),payload.username,payload.password)
        except (SecurityError,ValueError,sqlite3.IntegrityError) as exc:
            raise HTTPException(getattr(exc,'code',422),str(exc)) from exc

    @router.patch('/api/v1/identity/users/{uid}')
    def change_user_status(request:Request,uid:str,payload:UserStatusPayload):
        try:return security.set_user_active(principal_of(request),uid,payload.active)
        except SecurityError as exc:raise HTTPException(exc.code,str(exc)) from exc

    @router.post('/api/v1/identity/projects/{pid}/members',status_code=201)
    def grant(request: Request,pid: str,payload: MemberPayload):
        try:
            security.grant_member(principal_of(request),pid,payload.user_id,payload.role)
            return {'project_id':pid,'user_id':payload.user_id,'role':payload.role}
        except (SecurityError,ValueError) as exc:
            raise HTTPException(getattr(exc,'code',422),str(exc)) from exc

    @router.get('/api/v1/identity/audit')
    def audit(request: Request,limit:int=100):
        try:return security.list_audit(principal_of(request),limit)
        except SecurityError as exc:raise HTTPException(exc.code,str(exc)) from exc

    return router


class EnterpriseGuard:
    """ASGI guard covering HTTP *and* WS; no unscoped legacy data in enterprise.

    The explicit allowlist prevents old `/api/v1/datasets`, `/runs`, `/agent`,
    `/settings` and raw `/native-bi/assets` list from leaking across tenants.
    Project-scoped legacy routes require membership and resource ownership.
    """
    def __init__(self, app, security: EnterpriseSecurity, store=None):
        self.app=app
        self.security=security
        self.store=store

    @staticmethod
    def _cookie(headers: dict, name: str):
        import http.cookies
        jar=http.cookies.SimpleCookie()
        try:jar.load(headers.get('cookie',''))
        except Exception:return None
        return jar[name].value if name in jar else None

    @staticmethod
    async def _error(scope,receive,send,code:int,message:str):
        response=JSONResponse({'detail':message},status_code=code,headers={'Cache-Control':'no-store'})
        await response(scope,receive,send)

    async def __call__(self,scope,receive,send):
        kind=scope['type']
        if kind not in {'http','websocket'}:
            return await self.app(scope,receive,send)
        path=scope.get('path','')
        headers={k.decode('latin1').lower():v.decode('latin1') for k,v in scope.get('headers',[])}
        method=scope.get('method','GET').upper()
        public=path in {'/api/v1/health','/api/v1/auth/login','/login','/favicon.ico'} or path.startswith('/assets/')
        if kind=='websocket':
            # Fail closed at the ASGI boundary, including future websocket routers.
            # Native BI will independently re-check the exact asset and version.
            from urllib.parse import urlsplit
            match=re.fullmatch(
                r'/api/v1/native-bi/(assets|linked)/([0-9a-fA-F-]{36}|si_[a-f0-9]{32})/versions/([^/]+)/ws',
                path)
            origin=urlsplit(headers.get('origin',''))
            query=parse_qs(scope.get('query_string',b'').decode('utf-8',errors='replace'))
            project_ids=query.get('project_id',[])
            principal=self.security.authenticate(self._cookie(headers,SESSION_COOKIE))
            permitted=False
            if (match and principal and len(project_ids)==1 and len(project_ids[0])==36
                    and origin.scheme=='https' and origin.netloc==headers.get('host')):
                try:
                    self.security.asset_access(principal,project_ids[0],match.group(2),
                                               linked=(match.group(1)=='linked'))
                    permitted=True
                except (SecurityError,ValueError):
                    pass
            if not permitted:
                await send({'type':'websocket.close','code':1008,'reason':'Authorized project BI session required'})
                return
            return await self.app(scope,receive,send)
        if public:
            return await self.app(scope,receive,send)
        principal=self.security.authenticate(self._cookie(headers,SESSION_COOKIE))
        if principal is None:
            if path.startswith('/api/'):
                return await self._error(scope,receive,send,401,'请先登录')
            response=RedirectResponse('/login',status_code=303)
            return await response(scope,receive,send)
        if method not in {'GET','HEAD','OPTIONS'}:
            csrf=headers.get('x-timepilot-csrf')
            if not self.security.verify_csrf(principal,csrf):
                return await self._error(scope,receive,send,403,'CSRF 验证失败')
            origin=headers.get('origin')
            if origin:
                from urllib.parse import urlsplit
                parsed=urlsplit(origin)
                if parsed.netloc != headers.get('host') or parsed.scheme!='https':
                    return await self._error(scope,receive,send,403,'请求来源验证失败')
        replay=None
        try:
            if (path.startswith('/api/v1/identity/users') or path=='/api/v1/identity/audit') and principal.tenant_role!='tenant_admin':
                raise SecurityError('仅租户管理员允许身份管理')
            if path.startswith('/api/v1/projects'):
                m=re.match(r'^/api/v1/projects/([0-9a-fA-F-]{36})(?:/|$)',path)
                if m:
                    required='viewer' if method in {'GET','HEAD'} or (method=='POST' and (path.endswith('/industrial/preview') or path.endswith('/industrial/arrow') or path.endswith('/selections/preview') or path.endswith('/preflight'))) else 'editor'
                    if method=='POST' and '/bi-agent/' in path and not re.search(
                        r'/(freeze|model-drafts)$',path):
                        required='analyst'
                    if re.search(r'/(archive|restore)$',path) or (method=='DELETE' and re.fullmatch(r'/api/v1/projects/[^/]+',path)):
                        required='project_admin'
                    self.security.allowed_project(principal,m.group(1),required=required)
                elif path.rstrip('/')=='/api/v1/projects':
                    if method=='POST' and principal.tenant_role!='tenant_admin':
                        raise SecurityError('仅租户管理员可以创建项目')
                else:
                    raise SecurityError('项目路由无效',404)
            elif path.startswith('/api/v1/auth/') or path.startswith('/api/v1/identity/'):
                pass
            elif path in {'/api/v1/ready','/api/v1/plugins'}:
                if method not in {'GET','HEAD'}:
                    raise SecurityError('此接口只允许读取')
            elif path.startswith('/api/v1/native-bi/') or path.startswith('/api/v1/smart-import/') or path.startswith('/api/v1/data-assets/'):
                project_id=headers.get('x-timepilot-project')
                if not project_id:
                    raise SecurityError('请通过有权限的项目页面访问该数据接口')
                required='viewer' if method in {'GET','HEAD'} or path in {'/api/v1/native-bi/query','/api/v1/native-bi/query.arrow'} else 'editor'
                self.security.allowed_project(principal,project_id,required=required)
                if path.startswith('/api/v1/native-bi/'):
                    if path=='/api/v1/native-bi/capabilities' and method=='GET':
                        pass
                    elif m:=re.match(r'^/api/v1/native-bi/assets/([0-9a-fA-F-]{36})(?:/|$)',path):
                        self.security.asset_access(principal,project_id,m.group(1),required=required)
                    elif m:=re.match(r'^/api/v1/native-bi/linked/([0-9a-fA-F-]{36})(?:/|$)',path):
                        self.security.asset_access(principal,project_id,m.group(1),linked=True,required=required)
                    elif path in {'/api/v1/native-bi/query','/api/v1/native-bi/query.arrow','/api/v1/native-bi/views','/api/v1/native-bi/edits/preview','/api/v1/native-bi/edits/commit'} and method=='POST':
                        body=b''
                        while True:
                            part=await receive()
                            body+=part.get('body',b'')
                            if len(body)>131072:
                                raise SecurityError('接口请求过大',413)
                            if not part.get('more_body',False):break
                        try:payload=json.loads(body)
                        except (TypeError,ValueError):raise SecurityError('请求 JSON 无效',422) from None
                        asset_id=payload.get('asset_id') or (payload.get('query') or {}).get('asset_id')
                        if not asset_id:
                            raise SecurityError('缺少项目作用域资产编号',403)
                        self.security.asset_access(principal,project_id,asset_id,required=required)
                        used=False
                        async def replay_once():
                            nonlocal used
                            if not used:
                                used=True
                                return {'type':'http.request','body':body,'more_body':False}
                            return {'type':'http.request','body':b'','more_body':False}
                        replay=replay_once
                    elif m:=re.match(r'^/api/v1/native-bi/views/([0-9a-fA-F-]{36})$',path):
                        # Resolve a view to its owner asset before granting read.
                        with self.store.sessions() as session:
                            from timepilot.db import NativeBISavedViewRow
                            row=session.get(NativeBISavedViewRow,m.group(1))
                            if row is None:raise SecurityError('视图不存在',404)
                            aid=row.asset_id
                        self.security.asset_access(principal,project_id,aid)
                    else:
                        raise SecurityError('企业模式禁止不带项目作用域的原生 BI 操作')
                elif path.startswith('/api/v1/smart-import/'):
                    m=re.match(r'^/api/v1/smart-import/imports/([0-9a-fA-F-]{36})(?:/|$)',path)
                    if not m:raise SecurityError('该导入接口必须通过项目访问')
                    self.security.import_access(principal,project_id,m.group(1),required=required)
                else:
                    m=re.match(r'^/api/v1/data-assets/(si_[a-f0-9]{32})(?:/|$)',path)
                    if not m:raise SecurityError('该数据接口必须通过项目访问')
                    self.security.asset_access(principal,project_id,m.group(1),linked=True,required=required)
            elif path.startswith('/api/'):
                raise SecurityError('企业模式已禁用未实现租户隔离的旧接口，请使用项目级接口')
            # All authenticated UI routes are allowed; data access remains server-gated.
        except SecurityError as exc:
            self.security.audit('access_denied',principal,resource=path[:180],detail=str(exc))
            return await self._error(scope,receive,send,exc.code,str(exc))
        token=CURRENT_PRINCIPAL.set(principal)
        scope.setdefault('state',{})['principal']=principal
        final_status=[500]
        async def audited_send(message):
            if message['type']=='http.response.start':
                final_status[0]=message['status']
            await send(message)
        try:
            await self.app(scope,replay or receive,audited_send)
        finally:
            CURRENT_PRINCIPAL.reset(token)
            if method in {'POST','PATCH','PUT','DELETE'} and not path.startswith('/api/v1/auth/'):
                # Record metadata only: no raw rows, credentials or payload values.
                try:self.security.audit('http_write',principal,resource=path[:180],
                                        detail=f'{method} HTTP {final_status[0]}')
                except (OSError,sqlite3.Error):pass
