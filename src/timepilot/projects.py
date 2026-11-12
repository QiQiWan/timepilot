"""Local project catalog: soft deletion, version-pinned links, no copied business data.

Every project owns explicit references to imports/assets/runs.  The catalog is
separate from operational storage, so migrations never rewrite raw datasets.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from timepilot.data_assets import asset_id_for
from timepilot.enterprise_security import CURRENT_PRINCIPAL, SecurityError
from timepilot.project_migrations import (
    TARGET_SCHEMA, _ddl, backup_before_upgrade, catalog_startup_lock,
    migrate, require_supported_catalog,
)


def now():
    return datetime.now(timezone.utc).isoformat()


class ProjectError(ValueError):
    pass


class ProjectCreate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default='', max_length=1500)
    domain: str = Field(default='general', max_length=60)


class ProjectEdit(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=1500)
    domain: str | None = Field(default=None, max_length=60)


class ProjectCatalog:
    """SQLite WAL, project-local scope and a one-time safe legacy linkage."""

    def __init__(self, home: Path, imports, assets, engine=None, experiments=None, security=None):
        self.home = Path(home)
        self.security = security
        self.root = self.home / 'projects'
        self.root.mkdir(parents=True, exist_ok=True)
        self.db_file = self.root / 'catalog.sqlite3'
        self.imports = imports
        self.assets = assets
        self.engine = engine
        self.experiments = experiments
        self._lock = threading.RLock()
        # Verify before any DDL, even when the current release is older than
        # the on-disk catalog. A source copy must never silently reset data.
        with catalog_startup_lock(self.root / '.catalog-init.lock'):
            self.startup_catalog = require_supported_catalog(self.db_file)
            self.upgrade_backup = backup_before_upgrade(self.db_file)
            self._setup()
            self.startup_catalog_after = require_supported_catalog(self.db_file)

    def _db(self):
        db = sqlite3.connect(self.db_file, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA busy_timeout=15000')
        return db

    def _setup(self):
        with self._lock, self._db() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('BEGIN IMMEDIATE')
            _ddl(db, '''
                CREATE TABLE IF NOT EXISTS project_schema(version INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS projects(
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL,
                    domain TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    CHECK(status IN ('active','archived','trash')));
                CREATE TABLE IF NOT EXISTS project_imports(
                    project_id TEXT NOT NULL REFERENCES projects(id),
                    import_id TEXT NOT NULL, created_at TEXT NOT NULL,
                    PRIMARY KEY(project_id,import_id));
                CREATE TABLE IF NOT EXISTS project_assets(
                    project_id TEXT NOT NULL REFERENCES projects(id),
                    asset_id TEXT NOT NULL, version_id TEXT, created_at TEXT NOT NULL,
                    PRIMARY KEY(project_id,asset_id));
                CREATE TABLE IF NOT EXISTS project_native_assets(
                    project_id TEXT NOT NULL REFERENCES projects(id),
                    native_asset_id TEXT NOT NULL, source_asset_id TEXT NOT NULL,
                    created_at TEXT NOT NULL, PRIMARY KEY(project_id,native_asset_id));
                CREATE TABLE IF NOT EXISTS project_generic_assets(
                    project_id TEXT NOT NULL REFERENCES projects(id),
                    import_id TEXT NOT NULL, candidate_id TEXT NOT NULL,
                    version_id TEXT NOT NULL, native_asset_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(project_id,import_id,candidate_id,version_id));
                CREATE INDEX IF NOT EXISTS idx_project_generic_native
                    ON project_generic_assets(project_id,native_asset_id);
                CREATE TABLE IF NOT EXISTS project_runs(
                    project_id TEXT NOT NULL REFERENCES projects(id),
                    run_id TEXT NOT NULL, created_at TEXT NOT NULL,
                    PRIMARY KEY(project_id,run_id));
                CREATE TABLE IF NOT EXISTS project_settings(
                    key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS project_bi_views(
                    project_id TEXT NOT NULL REFERENCES projects(id),
                    asset_id TEXT NOT NULL, version_id TEXT NOT NULL,
                    view_id TEXT NOT NULL, created_at TEXT NOT NULL,
                    PRIMARY KEY(project_id,view_id));
                CREATE INDEX IF NOT EXISTS idx_project_bi_asset ON project_bi_views(project_id,asset_id);
                CREATE TABLE IF NOT EXISTS project_forecast_drafts(
                    project_id TEXT NOT NULL REFERENCES projects(id),
                    draft_id TEXT NOT NULL, asset_id TEXT NOT NULL,
                    version_id TEXT NOT NULL, view_id TEXT NOT NULL,
                    created_at TEXT NOT NULL, PRIMARY KEY(project_id,draft_id));
                CREATE TABLE IF NOT EXISTS project_run_sources(
                    project_id TEXT NOT NULL REFERENCES projects(id),
                    run_id TEXT NOT NULL, draft_id TEXT NOT NULL, view_id TEXT NOT NULL,
                    asset_id TEXT NOT NULL, version_id TEXT NOT NULL,
                    dataset_id TEXT NOT NULL, created_at TEXT NOT NULL,
                    PRIMARY KEY(project_id,run_id));
                CREATE INDEX IF NOT EXISTS idx_proj_imports ON project_imports(import_id);
                CREATE INDEX IF NOT EXISTS idx_proj_assets ON project_assets(asset_id);
            ''')
            if 'tenant_id' not in {x[1] for x in db.execute('PRAGMA table_info(projects)')}:
                # Old projects stay unclaimed until an offline administrator explicitly
                # assigns them to a tenant. A new tenant never inherits legacy data.
                db.execute("ALTER TABLE projects ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'legacy-unassigned'")
            if not db.execute('SELECT 1 FROM project_schema').fetchone():
                db.execute('INSERT INTO project_schema(version) VALUES (1)')
            version = db.execute('SELECT version FROM project_schema').fetchone()[0]
            if version == 1:
                # Additive schema migration: no changes to raw files, asset
                # references, existing versions or historical model runs.
                db.execute('UPDATE project_schema SET version=4 WHERE version=1')
            elif version == 2:
                db.execute('UPDATE project_schema SET version=4 WHERE version=2')
            elif version == 3:
                db.execute('UPDATE project_schema SET version=4 WHERE version=3')
            elif not 4 <= version <= TARGET_SCHEMA:
                raise ProjectError(
                    f'Unsupported project schema v{version}; supported range 1..{TARGET_SCHEMA}. '
                    'No automatic downgrade or database reset is permitted.')
            migrate(db)

    @staticmethod
    def _id(value):
        try:
            return str(UUID(str(value)))
        except (TypeError, ValueError):
            raise ProjectError('项目编号格式无效') from None

    def _project(self, db, pid):
        row = db.execute('SELECT * FROM projects WHERE id=?', (self._id(pid),)).fetchone()
        if row is None:
            raise KeyError('项目不存在')
        return dict(row)

    def get(self, pid):
        if self.security is not None and (principal := CURRENT_PRINCIPAL.get()) is not None:
            self.security.allowed_project(principal,pid)
        with self._db() as db:
            return self._project(db, pid)

    def writable(self, pid):
        row = self.get(pid)
        if row['status'] != 'active':
            raise ProjectError('项目已归档或移入回收站，无法新增或修改数据')
        return row

    def list(self, *, include_trashed=False):
        principal = CURRENT_PRINCIPAL.get() if self.security is not None else None
        with self._db() as db:
            if principal is not None:
                allowed=self.security.visible_project_ids(principal)
                if allowed is not None and not allowed:
                    return []
                q='SELECT * FROM projects WHERE tenant_id=?'
                params=[principal.tenant_id]
                if allowed is not None:
                    q+=' AND id IN ('+','.join('?' for _ in allowed)+')'
                    params.extend(allowed)
                if not include_trashed:q+=" AND status!='trash'"
                q+=' ORDER BY updated_at DESC'
                return [dict(r) for r in db.execute(q,params).fetchall()]
            q='SELECT * FROM projects' + ('' if include_trashed else " WHERE status!='trash'") + ' ORDER BY updated_at DESC'
            return [dict(r) for r in db.execute(q).fetchall()]

    def create(self, payload: ProjectCreate):
        principal = CURRENT_PRINCIPAL.get() if self.security is not None else None
        if principal is not None and principal.tenant_role!='tenant_admin':
            raise SecurityError('只有租户管理员可以创建项目')
        pid = str(uuid4())
        timestamp = now()
        tenant_id=principal.tenant_id if principal is not None else ('legacy-unassigned' if self.security is not None else 'local')
        with self._lock, self._db() as db:
            db.execute('''INSERT INTO projects(id,name,description,domain,status,created_at,updated_at,tenant_id)
                       VALUES(?,?,?,?,?,?,?,?)''',
                       (pid, payload.name.strip(), payload.description.strip(), payload.domain.strip(),
                        'active', timestamp, timestamp,tenant_id))
        if principal is not None:
            self.security.attach_owner(principal,pid)
        return self.get(pid)

    def edit(self, pid, payload: ProjectEdit):
        changes = payload.model_dump(exclude_none=True)
        if not changes:
            return self.get(pid)
        self.writable(pid)
        if 'name' in changes:
            changes['name'] = changes['name'].strip()
            if not changes['name']:
                raise ProjectError('项目名称不能为空')
        keys = sorted(changes)
        with self._lock, self._db() as db:
            db.execute('UPDATE projects SET ' + ','.join(f'{k}=?' for k in keys)
                       + ',updated_at=? WHERE id=?', [*(changes[k] for k in keys), now(), self._id(pid)])
        return self.get(pid)

    def status(self, pid, status: str):
        if status not in {'active','archived','trash'}:
            raise ProjectError('未知的项目状态')
        existing = self.get(pid)
        if existing['status']=='trash' and status=='archived':
            raise ProjectError('请先从回收站恢复项目')
        if status in {'archived','trash'}:
            self._guard_busy(pid)
        with self._lock, self._db() as db:
            db.execute('UPDATE projects SET status=?,updated_at=? WHERE id=?',
                       (status,now(),self._id(pid)))
        return self.get(pid)

    def _guard_busy(self, pid):
        # Upload scans share this project's lifecycle; archiving while they
        # run would orphan an immutable import after its final association.
        with self._db() as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='project_import_jobs'").fetchone():
                active = db.execute("SELECT 1 FROM project_import_jobs WHERE project_id=? AND status IN ('queued','running') LIMIT 1",
                                    (self._id(pid),)).fetchone()
                if active:
                    raise ProjectError('项目仍有数据上传或结构扫描任务，请先完成或取消后再归档')
        with self._db() as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='feature_jobs'").fetchone():
                pending=db.execute('''SELECT 1 FROM feature_jobs WHERE project_id=?
                    AND status IN ('queued','running','cancel_requested') LIMIT 1''',(self._id(pid),)).fetchone()
                if pending:raise ProjectError('项目仍有特征计算任务，请先结束任务再归档')
        if self.engine is None:
            return
        ids = {r['run_id'] for r in self.runs(pid)}
        if not ids:
            return
        if any(r.id in ids and r.status in {'queued','running','cancel_requested'}
               for r in self.engine.store.list_runs()):
            raise ProjectError('项目仍有运行中的模型任务，请先取消或完成任务再归档')

    def attach_import(self, pid, import_id):
        self.writable(pid)
        source = self.imports.manifest(import_id)
        with self._lock, self._db() as db:
            db.execute('INSERT OR IGNORE INTO project_imports VALUES (?,?,?)',
                       (self._id(pid), source['import_id'], now()))
        return {'project_id': pid, 'import_id': source['import_id']}

    def has_import(self, pid, import_id):
        self.get(pid)
        with self._db() as db:
            return bool(db.execute('SELECT 1 FROM project_imports WHERE project_id=? AND import_id=?',
                                   (self._id(pid), import_id)).fetchone())

    def require_import(self, pid, import_id):
        if not self.has_import(pid, import_id):
            raise KeyError('该项目没有此导入文件')
        return self.imports.manifest(import_id)

    def attach_asset(self, pid, asset_id, version_id=None):
        self.writable(pid)
        source = self.assets.asset(asset_id)
        ver = version_id or source['current_version']
        self.assets.version(asset_id, ver)
        # Asset references can be shared explicitly; bytes are never copied.
        with self._lock, self._db() as db:
            db.execute('INSERT INTO project_assets(project_id,asset_id,version_id,created_at) '
                       'VALUES (?,?,?,?) ON CONFLICT(project_id,asset_id) DO UPDATE SET version_id=excluded.version_id',
                       (self._id(pid), asset_id, ver, now()))
        return {'project_id':pid,'asset_id':asset_id,'version_id':ver,'zero_copy':True}

    def has_asset(self, pid, asset_id):
        self.get(pid)
        with self._db() as db:
            return db.execute('SELECT * FROM project_assets WHERE project_id=? AND asset_id=?',
                              (self._id(pid),asset_id)).fetchone()

    def require_asset(self, pid, asset_id):
        record = self.has_asset(pid, asset_id)
        if not record:
            raise KeyError('该项目没有此数据资产')
        return dict(record)

    def save_view(self, pid, asset_id, payload):
        self.writable(pid)
        link=self.require_asset(pid,asset_id)
        # Persist a single canonical workbench view including Perspective state.
        # Project-scoped ownership is stored in this catalog, never inferred
        # from the global DataAsset endpoint.
        saved=self.assets.save_view(asset_id,link['version_id'],payload)
        with self._lock,self._db() as db:
            db.execute('''INSERT INTO project_bi_views
                (project_id,asset_id,version_id,view_id,created_at)
                VALUES (?,?,?,?,?)''',(self._id(pid),asset_id,link['version_id'],
                                    saved['view_id'],now()))
        return {'project_id':pid,**saved}

    def _view_link(self,pid,asset_id,view_id):
        self.require_asset(pid,asset_id)
        with self._db() as db:
            r=db.execute('''SELECT * FROM project_bi_views WHERE project_id=?
                AND asset_id=? AND view_id=?''',(self._id(pid),asset_id,view_id)).fetchone()
        if not r:
            raise KeyError('该项目没有此 BI 视图')
        return dict(r)

    def view(self,pid,asset_id,view_id):
        link=self._view_link(pid,asset_id,view_id)
        return {'project_id':pid,**self.assets.view(asset_id,link['version_id'],view_id)}

    def views_for(self,pid,asset_id):
        self.require_asset(pid,asset_id)
        with self._db() as db:
            rows=[dict(x) for x in db.execute('''SELECT version_id,view_id FROM project_bi_views
                WHERE project_id=? AND asset_id=? ORDER BY created_at DESC''',
                (self._id(pid),asset_id)).fetchall()]
        results=[]
        for row in rows:
            try:results.append(self.view(pid,asset_id,row['view_id']))
            except (KeyError,ValueError,OSError):
                results.append({'view_id':row['view_id'],'version_id':row['version_id'],'missing':True})
        return results

    def create_forecast_draft(self,pid,asset_id,view_id,options):
        self.writable(pid)
        view=self.view(pid,asset_id,view_id)
        import_id,_,_=self.assets._resolve(asset_id,view['version_id'])
        draft=self.assets.workbench.create_draft(import_id,view['version_id'],view_id,options)
        with self._lock,self._db() as db:
            db.execute('''INSERT INTO project_forecast_drafts
                (project_id,draft_id,asset_id,version_id,view_id,created_at)
                VALUES (?,?,?,?,?,?)''',(self._id(pid),draft['draft_id'],asset_id,
                                     view['version_id'],view_id,now()))
        return {'project_id':pid,'asset_id':asset_id,**draft}

    def _draft_link(self,pid,draft_id):
        self.get(pid)
        with self._db() as db:
            row=db.execute('''SELECT * FROM project_forecast_drafts WHERE
                project_id=? AND draft_id=?''',(self._id(pid),draft_id)).fetchone()
        if not row:
            raise KeyError('该项目没有此预测任务草稿')
        return dict(row)

    def forecast_draft(self,pid,draft_id):
        link=self._draft_link(pid,draft_id)
        self._view_link(pid,link['asset_id'],link['view_id'])
        iid,_,_=self.assets._resolve(link['asset_id'],link['version_id'])
        result=self.assets.workbench.draft(iid,link['version_id'],draft_id)
        return {'project_id':pid,'asset_id':link['asset_id'],
                'version_id':link['version_id'],**self.assets.workbench._public_draft(result)}

    def drafts_for(self,pid):
        self.get(pid)
        with self._db() as db:
            ids=[r['draft_id'] for r in db.execute('''SELECT draft_id FROM project_forecast_drafts
                WHERE project_id=? ORDER BY created_at DESC''',(self._id(pid),))]
        output=[]
        for did in ids:
            try:output.append(self.forecast_draft(pid,did))
            except (KeyError,ValueError,OSError):output.append({'draft_id':did,'missing':True})
        return output

    def materialize_draft(self,pid,draft_id):
        self.writable(pid)
        link=self._draft_link(pid,draft_id)
        self.forecast_draft(pid,draft_id)
        iid,_,_=self.assets._resolve(link['asset_id'],link['version_id'])
        result=self.assets.workbench.materialize(iid,link['version_id'],draft_id)
        return {'project_id':pid,'asset_id':link['asset_id'],'version_id':link['version_id'],**result}

    def link_legacy_views_once(self):
        """Backfill old saved views only where the owning project is unambiguous."""
        with self._lock,self._db() as db:
            if db.execute("SELECT 1 FROM project_settings WHERE key='project_views_v2_backfilled'").fetchone():
                return
            rows=db.execute('''SELECT asset_id,MIN(project_id) AS project_id,COUNT(*) AS cnt,
                MAX(version_id) AS version_id FROM project_assets GROUP BY asset_id''').fetchall()
            for row in rows:
                if row['cnt'] != 1:continue
                aid=row['asset_id'];pid=row['project_id']
                try:
                    source=self.assets.asset(aid)
                    for item in source['versions']:
                        vid=item['version_id']
                        for view in self.assets.views(aid,vid):
                            db.execute('''INSERT OR IGNORE INTO project_bi_views
                                (project_id,asset_id,version_id,view_id,created_at)
                                VALUES (?,?,?,?,?)''',(pid,aid,vid,view['view_id'],view['created_at']))
                except (KeyError,ValueError,OSError):continue
            db.execute('''INSERT INTO project_settings(key,value)
                VALUES ('project_views_v2_backfilled','true')''')

    def imports_for(self,pid):
        self.get(pid)
        with self._db() as db:
            links=[r['import_id'] for r in db.execute('SELECT import_id FROM project_imports WHERE project_id=? ORDER BY created_at DESC', (self._id(pid),))]
        results=[]
        for iid in links:
            try:
                item=self.imports.manifest(iid)
                results.append({'import_id':iid,'filename':item['filename'],'files':len(item['files']),
                    'candidate_count':len(item['candidates']),'version_count':len(item['versions']),
                    'created_at':item['created_at']})
            except (KeyError,OSError,ValueError):
                results.append({'import_id':iid,'missing':True})
        return results

    def assets_for(self,pid):
        self.get(pid)
        with self._db() as db:
            rows=list(db.execute('SELECT asset_id,version_id FROM project_assets WHERE project_id=? ORDER BY created_at DESC', (self._id(pid),)))
        output=[]
        for r in rows:
            try:
                item=self.assets.version(r['asset_id'],r['version_id'])
                output.append({'asset_id':r['asset_id'],'version_id':r['version_id'],
                    'name':item['name'],'row_count':item['row_count'],
                    'series_count':item['series_count'],'created_at':item['created_at'],
                    'semantic_profile':item['semantic_profile'],'zero_copy':True})
            except (KeyError,ValueError,OSError) as exc:
                output.append({'asset_id':r['asset_id'],'missing':True,'error':str(exc)[:150]})
        return output

    def generic_for(self,pid):
        self.get(pid)
        with self._db() as db:
            return [dict(row) for row in db.execute(
                'SELECT * FROM project_generic_assets WHERE project_id=? ORDER BY created_at DESC',
                (self._id(pid),))]

    def attach_generic(self,pid,import_id,candidate_id,version_id,native_asset_id):
        self.writable(pid)
        self.require_import(pid,import_id)
        with self._lock,self._db() as db:
            db.execute('INSERT OR IGNORE INTO project_generic_assets VALUES (?,?,?,?,?,?)',
                (self._id(pid),import_id,candidate_id,version_id,native_asset_id,now()))
        return self.generic_for(pid)

    def require_generic(self,pid,native_asset_id):
        with self._db() as db:
            row=db.execute('SELECT * FROM project_generic_assets WHERE project_id=? AND native_asset_id=?',
                           (self._id(pid),native_asset_id)).fetchone()
        if row is None:
            raise KeyError('该项目没有此通用数据资产')
        with self._db() as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='unified_assets'").fetchone():
                hidden=db.execute('''SELECT 1 FROM unified_assets a JOIN asset_lifecycle l
                    ON a.project_id=l.project_id AND a.asset_id=l.asset_id
                    WHERE a.project_id=? AND a.import_id=? AND a.candidate_id=?
                    AND l.state='trashed' LIMIT 1''',(pid,row['import_id'],row['candidate_id'])).fetchone()
                if hidden:raise KeyError('资产已移入回收站，请先恢复再打开')
        return dict(row)

    def attach_native(self, pid, native_asset_id, source_asset_id):
        self.writable(pid)
        self.require_asset(pid,source_asset_id)
        with self._lock, self._db() as db:
            db.execute('INSERT OR IGNORE INTO project_native_assets VALUES (?,?,?,?)',
                       (self._id(pid),native_asset_id,source_asset_id,now()))
        return {'project_id':pid,'native_asset_id':native_asset_id,
                'source_asset_id':source_asset_id,'data_copy_policy':'explicit_editable_snapshot'}

    def native_for(self,pid):
        self.get(pid)
        with self._db() as db:
            return [dict(r) for r in db.execute(
                'SELECT native_asset_id,source_asset_id,created_at FROM project_native_assets WHERE project_id=?',
                (self._id(pid),))]

    def require_native(self,pid,asset_id):
        self.get(pid)
        with self._db() as db:
            result=db.execute('SELECT * FROM project_native_assets WHERE project_id=? AND native_asset_id=?',
                              (self._id(pid),asset_id)).fetchone()
        if not result:raise KeyError('该项目没有此可编辑 BI 资产')
        return dict(result)

    def attach_run(self, pid, run_id, *, draft_id=None):
        self.writable(pid)
        live=next((x for x in self.engine.store.list_runs() if x.id==run_id),None) if self.engine else None
        if self.engine is not None and live is None:
            raise KeyError('模型任务不存在')
        provenance=None
        if draft_id is not None:
            draft=self.forecast_draft(pid,draft_id)
            if not draft.get('dataset_id'):
                raise ProjectError('预测草稿尚未生成可训练数据集')
            if live is None or live.dataset_id!=draft['dataset_id']:
                raise ProjectError('运行记录的数据集与项目预测视图不一致')
            provenance=(self._id(pid),run_id,draft_id,draft['view_id'],
                        draft['asset_id'],draft['version_id'],draft['dataset_id'],now())
        with self._lock, self._db() as db:
            db.execute('INSERT OR IGNORE INTO project_runs VALUES (?,?,?)',
                       (self._id(pid),run_id,now()))
            if provenance:
                db.execute('''INSERT INTO project_run_sources
                    (project_id,run_id,draft_id,view_id,asset_id,version_id,dataset_id,created_at)
                    VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(project_id,run_id)
                    DO UPDATE SET draft_id=excluded.draft_id,view_id=excluded.view_id,
                    asset_id=excluded.asset_id,version_id=excluded.version_id,
                    dataset_id=excluded.dataset_id''',provenance)
        return {'project_id':pid,'run_id':run_id,'draft_id':draft_id}

    def runs(self,pid):
        self.get(pid)
        with self._db() as db:
            rows=[r['run_id'] for r in db.execute('SELECT run_id FROM project_runs WHERE project_id=? ORDER BY created_at DESC', (self._id(pid),))]
        live = {r.id: r for r in self.engine.store.list_runs()} if self.engine else {}
        with self._db() as db:
            sources={r['run_id']:dict(r) for r in db.execute(
                'SELECT * FROM project_run_sources WHERE project_id=?',(self._id(pid),))}
            # v0.17 generic-table modeling runs share project_runs but keep
            # their immutable provenance in an independent, versioned table.
            extra=db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='project_model_run_sources'").fetchone()
            if extra:
                for row in db.execute('''SELECT s.run_id,s.draft_id,s.dataset_id,d.asset_id,d.version_id,d.view_id
                        FROM project_model_run_sources s JOIN project_model_drafts d
                        ON s.draft_id=d.draft_id AND s.project_id=d.project_id
                        WHERE s.project_id=?''',(self._id(pid),)):
                    sources[row['run_id']]=dict(row)
        return [{'run_id':rid,'status':live[rid].status,'task_id':live[rid].task_id,
                 'dataset_id':live[rid].dataset_id,'created_at':live[rid].created_at}
                | ({k:v for k,v in sources[rid].items() if k in
                    {'view_id','asset_id','version_id','draft_id'}} if rid in sources else {})
                if rid in live else {'run_id':rid,'status':'missing'} for rid in rows]

    def models(self,pid):
        # Do not pretend the plugin catalog contains trained weights.
        return [{'run_id':r['run_id'],'task_id':r.get('task_id'),
                 'dataset_id':r.get('dataset_id'),'status':r['status'],
                 'view_id':r.get('view_id'),'draft_id':r.get('draft_id'),
                 'asset_id':r.get('asset_id'),'version_id':r.get('version_id'),
                 'kind':'training_run','weights_verified':False}
                for r in self.runs(pid)]

    def project_summary(self,pid):
        return {'project':self.get(pid),'assets':len(self.assets_for(pid))+len(self.generic_for(pid)),
                'generic_assets':len(self.generic_for(pid)),
                'imports':len(self.imports_for(pid)),'models':len(self.models(pid)),
                'runs':len(self.runs(pid)),'saved_views':sum(len(self.views_for(pid,a['asset_id'])) for a in self.assets_for(pid) if not a.get('missing')),
                'forecast_drafts':len(self.drafts_for(pid)),
                'editable_bi_assets':len(self.native_for(pid))}

    def backfill_legacy_once(self):
        """Link pre-existing assets to default project. No raw data migration."""
        with self._lock, self._db() as db:
            if db.execute("SELECT 1 FROM project_settings WHERE key='legacy_backfill_v1'").fetchone():
                return {'performed':False}
        projects=self.list(include_trashed=True)
        default=next((x for x in projects if x['name']=='默认项目' and x['domain']=='legacy'),None)
        if default is None:
            default=self.create(ProjectCreate(name='默认项目',description='历史数据与任务归档',domain='legacy'))
        # Write all links in a single transaction; never mark migration complete early.
        imports=self.imports.list_imports()
        with self._lock, self._db() as db:
            for item in imports:
                iid=item['import_id']
                db.execute('INSERT OR IGNORE INTO project_imports VALUES (?,?,?)',
                           (default['id'],iid,now()))
                try:
                    source=self.imports.manifest(iid)
                    if source.get('versions'):
                        db.execute('INSERT OR IGNORE INTO project_assets VALUES (?,?,?,?)',
                                   (default['id'],asset_id_for(iid),source['versions'][-1],now()))
                except (OSError,KeyError,ValueError):
                    continue
            db.execute('INSERT INTO project_settings(key,value) VALUES (?,?)',
                       ('legacy_backfill_v1',json.dumps({'at':now(),'default_project':default['id']})))
        return {'performed':True,'project_id':default['id'],'imports':len(imports)}
