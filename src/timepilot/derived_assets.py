"""Reviewed, immutable derived BI datasets.

The sole input is an authenticated project-scoped BI workspace revision.  The
query AST is compiled by the existing allowlisted BIQueryEngine; no request
accepts SQL, user file paths, or arbitrary asset IDs outside the project.
Publication is a separate explicit action after approval, with repeatable
fingerprints and an append-only audit record.  Charts remain presentation only.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import threading
from pathlib import Path
from uuid import UUID, uuid4

from timepilot.bi_agent_service import signature, timestamp, BIWorkspaceError
from timepilot.bi_query_engine import INTERVALS, BIQueryEngine
from timepilot.native_bi import QuerySpec, FilterSpec, compile_query, quote_identifier, NativeBIError
from timepilot.storage import sha256_file
from timepilot.enterprise_security import CURRENT_PRINCIPAL, SecurityError

MAX_DERIVED_ROWS = max(1000, min(10_000_000, int(os.getenv('TIMEPILOT_DERIVED_MAX_ROWS', '1000000'))))
MAX_DERIVED_COLUMNS = 256


class DerivedAssetError(BIWorkspaceError):
    pass


def _stable_json(document):
    return json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(',', ':'), default=str)


class DerivedAssetService:
    """Transactional metadata + verified files, safe retry after process interruption."""

    def __init__(self, bi):
        self.bi = bi
        self.projects, self.native, self.engine = bi.projects, bi.native, bi.engine
        self.home = self.projects.root / 'derived'
        self.home.mkdir(parents=True, exist_ok=True)
        self._setup()

    def _setup(self):
        with self.projects._lock, self.projects._db() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS bi_derived_requests(
                derivation_id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
                workspace_id TEXT NOT NULL, requested_by TEXT NOT NULL,
                request_id TEXT NOT NULL, name TEXT NOT NULL,
                state_hash TEXT NOT NULL, state_json TEXT NOT NULL,
                source_json TEXT NOT NULL, estimated_rows INTEGER NOT NULL,
                status TEXT NOT NULL, reviewed_by TEXT, reviewed_at TEXT,
                native_asset_id TEXT, data_sha256 TEXT, parquet_sha256 TEXT,
                published_rows INTEGER, columns_json TEXT,
                error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                UNIQUE(project_id,workspace_id,request_id));
            CREATE INDEX IF NOT EXISTS idx_bi_derived_project
                ON bi_derived_requests(project_id,created_at);
            CREATE TABLE IF NOT EXISTS bi_derived_audit(
                event_id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
                derivation_id TEXT NOT NULL, actor_id TEXT NOT NULL,
                action TEXT NOT NULL, detail_json TEXT NOT NULL,
                created_at TEXT NOT NULL);
            ''')

    def _actor(self):
        return self.bi._actor()

    def _editor(self, pid):
        self.projects.writable(pid)
        if self.projects.security is not None:
            principal=CURRENT_PRINCIPAL.get()
            if principal is None:raise SecurityError('派生数据写入要求有效登录会话')
            self.projects.security.allowed_project(principal,pid,required='editor')

    def _reviewer(self,pid,creator):
        self._editor(pid)
        if self.projects.security is not None:
            principal=CURRENT_PRINCIPAL.get()
            self.projects.security.allowed_project(principal,pid,required='project_admin')
            if principal.user_id == creator:
                raise SecurityError('企业模式要求不同于申请人的项目管理员审核派生数据')

    def _record(self,pid,rid):
        self.projects.get(pid)
        try:rid=str(UUID(str(rid)))
        except (ValueError,TypeError):raise DerivedAssetError('派生任务编号无效') from None
        with self.projects._db() as db:
            row=db.execute('SELECT * FROM bi_derived_requests WHERE project_id=? AND derivation_id=?',(pid,rid)).fetchone()
        if not row:raise KeyError('派生任务不存在或不属于当前项目')
        record=dict(row)
        for key in ('state_json','source_json','columns_json'):
            if record.get(key):record[key[:-5] if key.endswith('_json') else key]=json.loads(record[key])
        for key in ('state_json','source_json','columns_json'):record.pop(key,None)
        return record

    def _log(self,pid,rid,action,detail):
        with self.projects._lock,self.projects._db() as db:
            db.execute('INSERT INTO bi_derived_audit VALUES(?,?,?,?,?,?,?)',(
                str(uuid4()),pid,rid,self._actor(),action,_stable_json(detail),timestamp()))

    def audit(self,pid,rid):
        self._record(pid,rid)
        with self.projects._db() as db:
            return [dict(row) for row in db.execute('''SELECT actor_id,action,detail_json,created_at FROM bi_derived_audit
                WHERE project_id=? AND derivation_id=? ORDER BY created_at,rowid''',(pid,rid))]

    def _sources(self,pid,state):
        left=self.engine.checked(pid,state['asset_id'],state['version_id'])
        result=[{'asset_id':state['asset_id'],'version_id':state['version_id'],'sha256':left['sha256'],
                 'rows':left['row_count'],'role':'left'}]
        if state.get('join'):
            join=state['join'];right=self.engine.checked(pid,join['right_asset_id'],join['right_version_id'])
            if right['sha256']!=join['right_source_sha256']:
                raise DerivedAssetError('关联数据版本不匹配，必须重新预检')
            result.append({'asset_id':join['right_asset_id'],'version_id':join['right_version_id'],
                           'sha256':right['sha256'],'rows':right['row_count'],'role':'right'})
        return result

    def _verify(self,pid,doc):
        if signature(doc['state'])!=doc['state_hash']:
            raise DerivedAssetError('建模派生定义摘要不一致')
        if self._sources(pid,doc['state'])!=doc['source']:
            raise DerivedAssetError('上游原始资产发生改变或已失去访问权')
        # Validate the original workspace source even if the workspace has moved on.
        for source in doc['source']:
            if not self.native.verify(source['asset_id']).get('valid'):
                raise DerivedAssetError('上游 Parquet 文件完整性校验失败')

    def _sql(self,pid,state,con):
        """Return fully materialized SELECT with bound args; never use chart limit/offset.

        View-level chart layout, display column subset, sorting and page size are
        intentionally excluded from training.  Explicit row selection, safe
        computed fields, joins, windows, time buckets and aggregation are included.
        """
        if state.get('industrial') and any((state.get('bucket'),state.get('join'),state.get('expressions'),state.get('window'))):
            raise DerivedAssetError('工业图层与复杂派生变换组合需要单独确认，不能默默合并')
        source,fields=self.engine._source(con,pid,state)
        filters=[FilterSpec.model_validate(f) for f in state['query']['filters']]
        if state.get('industrial'):
            overlay=state['industrial'];event=overlay.get('event_field')
            if event and event not in fields:raise DerivedAssetError('维护事件字段不存在')
            regime=overlay.get('regime_field')
            if regime and regime not in fields:raise DerivedAssetError('工况字段不存在')
            if event:
                source=f'(SELECT *, CASE WHEN {quote_identifier(event)} IS NOT NULL AND ' \
                    f'TRIM(CAST({quote_identifier(event)} AS VARCHAR)) <> \'\' THEN 1 ELSE 0 END AS "maintenance_event" FROM {source})'
                if 'maintenance_event' in fields:raise DerivedAssetError('派生维护标识与原有列重名')
                fields=[*fields,'maintenance_event']
            # Regime source column already belongs to the original dataset.
        if state.get('bucket'):
            bucket=state['bucket'];time_col=bucket['time_field'];metric=bucket['metric_field']
            for col in (time_col,metric,bucket.get('entity_field')):
                if col and col not in fields:raise DerivedAssetError('时间聚合包含不存在的字段')
            interval=INTERVALS[bucket['interval']]
            q=quote_identifier
            expr=f"time_bucket(INTERVAL '{interval}', TRY_CAST({q(time_col)} AS TIMESTAMP))"
            value=f'TRY_CAST({q(metric)} AS DOUBLE)'
            agg=f'COUNT({q(metric)})' if bucket['operation']=='count' else f'{bucket["operation"].upper()}({value})'
            entity=bucket.get('entity_field')
            add=f', {q(entity)}' if entity else ''
            spec=QuerySpec(asset_id=state['asset_id'],columns=fields[:1],filters=filters,limit=1)
            typed_sql,params,_=compile_query(spec,fields,source_sql=source)
            clause=typed_sql.split(' FROM '+source,1)[1].split(' ORDER BY ',1)[0].split(' LIMIT ',1)[0]
            sql=(f'SELECT {expr} AS "time_bucket"{add}, {agg} AS "value", COUNT(*) AS "source_rows" '
                 f'FROM {source}{clause} GROUP BY {"1,2" if entity else "1"} '
                 'HAVING "time_bucket" IS NOT NULL ORDER BY 1')
            return sql,params
        grouped=bool(state['query']['metrics'] or state['query']['group_by'])
        q=QuerySpec(asset_id=state['asset_id'],version_id=state['version_id'],filters=filters,
                    columns=[] if grouped else fields,group_by=state['query']['group_by'],
                    metrics=state['query']['metrics'],order_by=state['query']['order_by'],limit=1)
        sql,params,_=compile_query(q,fields,source_sql=source)
        return sql.rsplit(' LIMIT ? OFFSET ?',1)[0],params

    def _inspect(self,pid,state,*,write_to=None):
        # A materialization is a full-dataset operation; enforce a finite total
        # deadline, including the count and COPY, regardless of dashboard limits.
        timeout=min(1800,max(10,int(os.getenv('TIMEPILOT_DERIVED_QUERY_TIMEOUT','120'))))
        with self.native._connection() as con:
            watch=threading.Timer(timeout,con.interrupt)
            watch.daemon=True
            try:
                watch.start()
                source_sql,params=self._sql(pid,state,con)
                count=int(con.execute(f'SELECT COUNT(*) FROM ({source_sql}) AS __tp_total',params).fetchone()[0])
                if not 0<count<=MAX_DERIVED_ROWS:
                    raise DerivedAssetError(f'派生数据 {count:,} 行；限额为 {MAX_DERIVED_ROWS:,}，请缩小筛选')
                con.execute(f'CREATE TEMP TABLE __tp_approved_result AS {source_sql}',params)
                describe=con.execute('DESCRIBE SELECT * FROM __tp_approved_result').fetchall()
                columns=[{'name':str(c[0]),'physical_type':str(c[1])} for c in describe]
                if not columns or len(columns)>MAX_DERIVED_COLUMNS:
                    raise DerivedAssetError('派生数据字段数不合法或超限')
                if len({c['name'] for c in columns})!=len(columns):
                    raise DerivedAssetError('派生数据字段名重复，拒绝发布')
                if write_to:
                    safe=str(write_to).replace("'","''")
                    con.execute(f"COPY __tp_approved_result TO '{safe}' (FORMAT CSV, HEADER TRUE)")
                return {'rows':count,'columns':columns}
            except DerivedAssetError:
                raise
            except Exception as exc:
                raise DerivedAssetError('派生数据计算失败或超过时间预算；请检查来源数据、计算表达式及日志') from exc
            finally:
                watch.cancel()

    def request(self,pid,wid,name,request_id):
        self._editor(pid)
        item=self.bi.workspace(pid,wid)
        state=item['state'];sources=self._sources(pid,state)
        if not 2<=len(name.strip())<=120:raise DerivedAssetError('派生数据名称需要 2–120 个字符')
        if not 8<=len(request_id)<=100:raise DerivedAssetError('幂等请求 ID 长度无效')
        with self.projects._db() as db:
            old=db.execute('SELECT derivation_id FROM bi_derived_requests WHERE project_id=? AND workspace_id=? AND request_id=?',
                           (pid,wid,request_id)).fetchone()
        if old:return self._record(pid,old['derivation_id'])
        preview=self._inspect(pid,state)
        rid=str(uuid4());now=timestamp();owner=self._actor();digest=signature(state)
        with self.projects._lock,self.projects._db() as db:
            db.execute('''INSERT INTO bi_derived_requests
                (derivation_id,project_id,workspace_id,requested_by,request_id,name,state_hash,state_json,
                 source_json,estimated_rows,status,created_at,updated_at)
                 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (rid,pid,wid,owner,request_id,name.strip(),digest,_stable_json(state),
                 _stable_json(sources),preview['rows'],'pending_review',now,now))
        self._log(pid,rid,'request',{'estimated_rows':preview['rows'],'source':sources,
                                     'state_hash':digest,'columns':preview['columns']})
        return self._record(pid,rid)

    def approve(self,pid,rid,confirmed,reason):
        doc=self._record(pid,rid)
        self._reviewer(pid,doc['requested_by'])
        if not confirmed or not reason.strip():raise DerivedAssetError('必须确认行数、字段、关联和用途，并填写审批意见')
        if doc['status']!='pending_review':raise DerivedAssetError('仅待审核派生任务可以批准')
        self._verify(pid,doc)
        preview=self._inspect(pid,doc['state'])
        if preview['rows']!=doc['estimated_rows']:raise DerivedAssetError('预检数据行数已改变，请重新提出申请')
        with self.projects._lock,self.projects._db() as db:
            count=db.execute('''UPDATE bi_derived_requests SET status='approved',reviewed_by=?,reviewed_at=?,
                updated_at=? WHERE project_id=? AND derivation_id=? AND status='pending_review' ''',
                (self._actor(),timestamp(),timestamp(),pid,rid)).rowcount
        if count!=1:raise DerivedAssetError('派生审核状态冲突')
        self._log(pid,rid,'approve',{'reason':reason,'confirmed_rows':preview['rows'],
                                    'column_names':[c['name'] for c in preview['columns']]})
        return self._record(pid,rid)

    def publish(self,pid,rid):
        # One publisher per derivation, across processes. Interrupted jobs can be
        # retried without yielding duplicate native assets or partial visibility.
        from timepilot.build_lock import exclusive_build_lock
        self._editor(pid)
        directory=self.home / str(UUID(pid)) / str(UUID(rid))
        directory.mkdir(parents=True,exist_ok=True)
        with exclusive_build_lock(directory/'.publish.lock', timeout=35):
            return self._publish_once(pid,rid)

    def _find_existing(self,rid):
        # Read the full authoritative native asset catalog; the normal UI list
        # caps at 200 and would produce duplicate versions after enough imports.
        try:
            from sqlalchemy import select
            from timepilot.db import NativeBIAssetRow
            with self.native.store.sessions() as session:
                matched=session.scalars(select(NativeBIAssetRow).where(
                    NativeBIAssetRow.lineage_json.contains(rid))).all()
                for row in matched:
                    meta=self.native._metadata(row)
                    if meta.get('lineage',{}).get('derivation_id')==rid:
                        return meta
        except AttributeError:
            # Tests can use a fake native store. This is not the production path.
            for item in self.native.list_assets(limit=200):
                if item.get('lineage',{}).get('derivation_id')==rid:return item
        return None

    def _publish_once(self,pid,rid):
        self._editor(pid)
        doc=self._record(pid,rid)
        if doc['status']=='published':return doc
        if doc['status'] not in ('approved','publishing'):
            raise DerivedAssetError('只有已批准的派生数据才允许发布')
        self._verify(pid,doc)
        directory=self.home / str(UUID(pid)) / str(UUID(rid))
        directory.mkdir(parents=True,exist_ok=True)
        final=directory/'data.csv'
        with self.projects._lock,self.projects._db() as db:
            db.execute("UPDATE bi_derived_requests SET status='publishing',updated_at=? WHERE project_id=? AND derivation_id=?",
                       (timestamp(),pid,rid))
        if final.is_file():
            # After an interrupted rename, reuse only a previously signed CSV.
            # An unsigned checkpoint could have been tampered with in-place.
            if not doc.get('data_sha256'):
                final.unlink()
            elif sha256_file(final)!=doc['data_sha256']:
                raise DerivedAssetError('发布检查点摘要不一致，拒绝复用，请人工核实数据文件')
        if not final.is_file():
            building=directory/'data.building.csv'
            try:
                actual=self._inspect(pid,doc['state'],write_to=building)
                if actual['rows']!=doc['estimated_rows']:
                    raise DerivedAssetError('物化行数与审核时不同，拒绝发布')
                os.replace(building,final)
                signed_sha=sha256_file(final)
                with self.projects._lock,self.projects._db() as db:
                    db.execute("UPDATE bi_derived_requests SET data_sha256=?,updated_at=? WHERE project_id=? AND derivation_id=? AND status='publishing'",
                               (signed_sha,timestamp(),pid,rid))
            finally:building.unlink(missing_ok=True)
        csv_hash=sha256_file(final)
        # Full verification after hard interruption: reject stale or modified checkpoints.
        with final.open('r',encoding='utf-8-sig',newline='') as handle:
            reader=csv.reader(handle);names=next(reader)
            rows=sum(1 for _ in reader)
        if rows!=doc['estimated_rows'] or not names:
            final.unlink(missing_ok=True)
            raise DerivedAssetError('派生 CSV 行数或表头与审核结果不一致，已丢弃无效检查点')
        lineage={'type':'bi_agent_derived','project_id':pid,'derivation_id':rid,
                 'state_hash':doc['state_hash'],'sources':doc['source'],
                 'csv_sha256':csv_hash,'approved_by':doc['reviewed_by']}
        aid=doc.get('native_asset_id')
        if not aid:
            # Retry reuse: a process may die between native registration and project linkage.
            candidate=self._find_existing(rid)
            if candidate:
                if candidate['lineage'].get('csv_sha256')!=csv_hash:
                    raise DerivedAssetError('此前登记的派生文件与当前文件摘要不一致')
                aid=candidate['asset_id']
        if not aid:
            imported=self.native.import_from_path(final,doc['name'],lineage)
            aid=imported['asset_id']
        native_meta=self.native.asset(aid)
        if native_meta.get('lineage',{})!=lineage or not self.native.verify(aid)['valid']:
            raise DerivedAssetError('派生数据的原生 BI 登记信息或哈希校验不一致')
        with self.projects._lock,self.projects._db() as db:
            # project-native links require project permissions but are valid for derived assets
            db.execute('''INSERT OR IGNORE INTO project_native_assets
                (project_id,native_asset_id,source_asset_id,created_at) VALUES(?,?,?,?)''',
                (pid,aid,doc['state']['asset_id'],timestamp()))
            db.execute('''UPDATE bi_derived_requests SET status='published',native_asset_id=?,
                data_sha256=?,parquet_sha256=?,published_rows=?,columns_json=?,updated_at=?
                WHERE project_id=? AND derivation_id=? AND status='publishing' ''',
                (aid,csv_hash,native_meta['sha256'],rows,_stable_json(native_meta['columns']),timestamp(),pid,rid))
        self._log(pid,rid,'publish',{'asset_id':aid,'rows':rows,'csv_sha256':csv_hash,
                                    'parquet_sha256':native_meta['sha256']})
        return self._record(pid,rid)

    def list(self,pid):
        self.projects.get(pid)
        with self.projects._db() as db:
            rows=db.execute('''SELECT derivation_id FROM bi_derived_requests
                WHERE project_id=? ORDER BY created_at DESC LIMIT 200''',(pid,)).fetchall()
        return [self._record(pid,r['derivation_id']) for r in rows]

    def model_source(self,pid,aid,version='base'):
        """Provide the existing ModelingWorkbench with a signed, immutable CSV source."""
        if version!='base':raise KeyError('派生模型仅支持冻结的 base 版本')
        self.projects.require_native(pid,aid)
        with self.projects._db() as db:
            row=db.execute('''SELECT derivation_id FROM bi_derived_requests
                WHERE project_id=? AND native_asset_id=? AND status='published' ''',(pid,aid)).fetchone()
        if not row:raise KeyError('该资产不是已审核发布的派生数据')
        doc=self._record(pid,row['derivation_id'])
        self._verify(pid,doc)
        path=self.home/str(UUID(pid))/doc['derivation_id']/'data.csv'
        if not path.is_file() or sha256_file(path)!=doc['data_sha256']:
            raise DerivedAssetError('派生训练 CSV 已损坏或被修改')
        meta=self.native.asset(aid)
        if meta['sha256']!=doc['parquet_sha256'] or not self.native.verify(aid)['valid']:
            raise DerivedAssetError('派生 Parquet 完整性检查失败')
        return {'project_id':pid,'asset_id':aid,'version_id':'base',
                'generic_version_id':'derived:'+doc['derivation_id'],
                'original_data_sha256':doc['data_sha256'],'native_sha256':meta['sha256'],
                'source_columns':[x['name'] for x in doc['columns']],
                'row_count':doc['published_rows'],'path':path,'confirmed_roles':{},
                'name':doc['name'],'derived_from':doc['source'],
                'derivation_id':doc['derivation_id']}
