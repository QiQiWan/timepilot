"""TimePilot FeatureLab: reviewable, causal industrial time-series features.

Recipes are declarative, source-SHA-pinned and project-scoped. All SQL is
compiled from validated identifiers and fixed operation templates; the client
never supplies SQL or an absolute path. Publication creates a NEW immutable
native asset after an independent review, preserving the original bytes.
"""
from __future__ import annotations
import hashlib
import json
import os
import re
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from timepilot.bi_query_engine import BIQueryEngine
from timepilot.enterprise_security import CURRENT_PRINCIPAL, SecurityError
from timepilot.native_bi import NativeBIError, quote_identifier, safe_json
from timepilot.bi_agent_service import timestamp
from timepilot.build_lock import exclusive_build_lock

NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}\Z")
NUMERIC = re.compile(r'^(?:TINYINT|SMALLINT|INTEGER|INT\d*|BIGINT|HUGEINT|UTINYINT|USMALLINT|UINTEGER|UBIGINT|FLOAT|DOUBLE|REAL|DECIMAL|NUMERIC)\b')


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), default=str)


def fingerprint(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


class FeatureStep(BaseModel):
    model_config = ConfigDict(extra='forbid')
    kind: str = Field(pattern=r'^(arithmetic|rolling_mean|rolling_std|lag|difference)$')
    field: str
    name: str = Field(min_length=1, max_length=64)
    other_field: str | None = None
    operator: str = '+'
    window: int = Field(default=5, ge=2, le=240)
    lag: int = Field(default=1, ge=1, le=240)
    order_by: str = '__tp_rowid'
    partition_by: str | None = None

    @model_validator(mode='after')
    def check(self):
        if not NAME.fullmatch(self.name):
            raise ValueError('特征输出名称必须是安全的技术标识')
        if self.name == '__tp_rowid':
            raise ValueError('系统行号不可覆盖')
        if self.kind == 'arithmetic':
            if not self.other_field:
                raise ValueError('算术特征须指定第二个字段')
            if self.operator not in ('+','-','*','/'):
                raise ValueError('只支持加、减、乘、除')
        return self


class FeatureRecipeInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    asset_id: str
    version_id: str = 'base'
    name: str = Field(min_length=1, max_length=120)
    steps: list[FeatureStep] = Field(min_length=1, max_length=30)


class FeatureLab:
    def __init__(self, projects, native):
        self.projects = projects
        self.native = native
        self.engine = BIQueryEngine(projects, native)
        self.root = projects.root / 'feature_lab'
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _actor():
        p = CURRENT_PRINCIPAL.get()
        return p.user_id if p else 'local'

    def _editor(self, pid):
        self.projects.writable(pid)
        if self.projects.security is not None:
            principal = CURRENT_PRINCIPAL.get()
            if principal is None:
                raise SecurityError('特征发布要求已登录')
            self.projects.security.allowed_project(principal, pid, required='editor')

    def _reviewer(self, pid, creator):
        self._editor(pid)
        if self.projects.security is not None:
            principal = CURRENT_PRINCIPAL.get()
            self.projects.security.allowed_project(principal, pid, required='project_admin')
            if principal.user_id == creator:
                raise SecurityError('企业模式下发布审核人不能与申请人相同')

    def _event(self,db,pid,rid,event_type,from_state,to_state,evidence):
        db.execute('''INSERT INTO feature_recipe_events
        (event_id,recipe_id,project_id,actor_id,event_type,from_state,to_state,evidence_sha256,created_at)
        VALUES (?,?,?,?,?,?,?,?,?)''',
        (str(uuid4()),rid,pid,self._actor(),event_type,from_state,to_state,
         fingerprint(evidence),timestamp()))

    def _metadata(self, pid, spec):
        source = self.engine.checked(pid, spec.asset_id, spec.version_id)
        if not self.native.verify(spec.asset_id).get('valid'):
            raise NativeBIError('源数据文件校验失败')
        return source

    @staticmethod
    def compile_step(step: FeatureStep, fields: list[str], types: dict[str,str]):
        if step.name in fields:
            raise ValueError('输出特征与已有字段重名：'+step.name)
        if step.field not in fields:
            raise ValueError('特征不存在源字段：'+step.field)
        if not bool(NUMERIC.match(str(types.get(step.field,'')).upper())):
            raise ValueError('特征操作仅接受已确认的数值字段：'+step.field)
        if step.order_by!='__tp_rowid' and step.order_by not in fields:
            raise ValueError('排序字段不存在：'+step.order_by)
        if step.partition_by and step.partition_by not in fields:
            raise ValueError('设备或工况分组字段不存在：'+step.partition_by)
        f = 'TRY_CAST('+quote_identifier(step.field)+' AS DOUBLE)'
        if step.kind=='arithmetic':
            if step.other_field not in fields:
                raise ValueError('第二个数值字段不存在')
            if not bool(NUMERIC.match(str(types.get(step.other_field,'')).upper())):
                raise ValueError('第二个字段未确认为数值类型')
            r = 'TRY_CAST('+quote_identifier(step.other_field)+' AS DOUBLE)'
            expression = '('+f+' / NULLIF('+r+', 0))' if step.operator=='/' else '('+f+' '+step.operator+' '+r+')'
        else:
            # Every frame is past-only and includes __tp_rowid as stable tie-break.
            order = quote_identifier(step.order_by)
            if step.order_by!='__tp_rowid':order += ', "__tp_rowid"'
            partition = ('PARTITION BY '+quote_identifier(step.partition_by)+' ') if step.partition_by else ''
            over = 'OVER ('+partition+'ORDER BY '+order
            if step.kind=='lag':
                expression=f'LAG({f}, {step.lag}) '+over+')'
            elif step.kind=='difference':
                expression=f'({f} - LAG({f}, {step.lag}) '+over+'))'
            elif step.kind in ('rolling_mean','rolling_std'):
                fn='AVG' if step.kind=='rolling_mean' else 'STDDEV_SAMP'
                expression=f'{fn}({f}) '+over+f' ROWS BETWEEN {step.window-1} PRECEDING AND CURRENT ROW)'
            else:
                raise ValueError('不支持的特征类型')
        return expression

    def _compiled(self, con, pid, spec: FeatureRecipeInput):
        meta = self._metadata(pid, spec)
        self.native._install_effective_view(con, spec.asset_id, spec.version_id, meta['columns'])
        source='__tp_effective'
        fields=[c['name'] for c in meta['columns']]
        types={c['name']:c['physical_type'] for c in meta['columns']}
        for i,step in enumerate(spec.steps):
            expression=self.compile_step(step, fields, types)
            next_source=f'__tp_feature_stage_{i}'
            con.execute(f'CREATE TEMP VIEW {next_source} AS SELECT *, {expression} AS {quote_identifier(step.name)} FROM {source}')
            source=next_source
            fields.append(step.name)
            types[step.name]='DOUBLE'
        return source,fields,meta

    def create(self,pid,spec: FeatureRecipeInput):
        self._editor(pid)
        source=self._metadata(pid,spec)
        # Pure validation: no DuckDB required to verify the full recipe contract.
        columns=[x['name'] for x in source['columns']]
        types={x['name']:x['physical_type'] for x in source['columns']}
        if len(columns)+len(spec.steps)>256:raise ValueError('计算后的字段数超过原生 BI 的 256 列限制')
        for step in spec.steps:
            self.compile_step(step,columns,types)
            columns.append(step.name)
            types[step.name]='DOUBLE'
        rid=str(uuid4());at=timestamp();blob=spec.model_dump(mode='json')
        with self.projects._lock,self.projects._db() as db:
            db.execute('''INSERT INTO feature_recipes VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',
              (rid,pid,self._actor(),spec.asset_id,spec.version_id,source['sha256'],
               spec.name,canonical(blob),fingerprint(blob),'draft',None,at,at))
            self._event(db,pid,rid,'create',None,'draft',{'spec_sha256':fingerprint(blob),'source_sha256':source['sha256']})
        return self.get(pid,rid)

    def get(self,pid,rid):
        self.projects.get(pid)
        with self.projects._db() as db:
            row=db.execute('SELECT * FROM feature_recipes WHERE project_id=? AND recipe_id=?',(pid,rid)).fetchone()
        if row is None:raise KeyError('特征配方不存在或没有项目权限')
        result=dict(row)
        result['spec']=json.loads(result.pop('spec_json'))
        return result

    def list(self,pid,asset_id=None):
        self.projects.get(pid)
        sql='SELECT recipe_id FROM feature_recipes WHERE project_id=?'
        args=[pid]
        if asset_id:
            sql+=' AND asset_id=?';args.append(asset_id)
        sql+=' ORDER BY created_at DESC LIMIT 100'
        with self.projects._db() as db:
            ids=[x[0] for x in db.execute(sql,args)]
        return [self.get(pid,rid) for rid in ids]

    def _pinned(self,pid,record):
        spec=FeatureRecipeInput.model_validate(record['spec'])
        if fingerprint(record['spec'])!=record['spec_sha256']:
            raise ValueError('特征定义内容指纹不一致')
        source=self._metadata(pid,spec)
        if source['sha256']!=record['source_sha256']:
            raise ValueError('特征源数据版本已变化，拒绝执行历史配方')
        return spec,source

    def preview(self,pid,rid,limit=50):
        record=self.get(pid,rid)
        spec,meta=self._pinned(pid,record)
        size=max(1,min(100,int(limit)))
        with self.native._connection() as con:
            table,fields,_=self._compiled(con,pid,spec)
            cur=con.execute(f'SELECT * FROM {table} ORDER BY "__tp_rowid" LIMIT ?', [size])
            keys=[x[0] for x in cur.description]
            rows=[dict(zip(keys,safe_json(x))) for x in cur.fetchall()]
        return {'recipe_id':rid,'preview_rows':rows,'columns':keys,'returned':len(rows),
                'total_rows':meta['row_count'],'is_sample':True,
                'source_sha256':record['source_sha256'],'spec_sha256':record['spec_sha256'],
                'time_semantics':'source_record_order' if all(s.order_by=='__tp_rowid' for s in spec.steps) else 'explicit_field',
                'leakage_policy':'past_or_current_only','original_unchanged':True}

    def request_review(self,pid,rid):
        self._editor(pid)
        record=self.get(pid,rid)
        self._pinned(pid,record)
        if record['owner_id']!=self._actor():
            raise SecurityError('只能提交本人创建的特征配方')
        with self.projects._lock,self.projects._db() as db:
            if db.execute("UPDATE feature_recipes SET state='pending_review',updated_at=? WHERE project_id=? AND recipe_id=? AND state='draft'",
                          (timestamp(),pid,rid)).rowcount!=1:
                raise ValueError('当前配方不能重复提交审核')
            self._event(db,pid,rid,'request_review','draft','pending_review',{'spec_sha256':record['spec_sha256']})
        return self.get(pid,rid)

    def approve(self,pid,rid,reason,confirm=False):
        record=self.get(pid,rid)
        self._reviewer(pid,record['owner_id'])
        self._pinned(pid,record)
        if not confirm or not str(reason).strip():raise ValueError('审核需要明确确认和意见')
        with self.projects._lock,self.projects._db() as db:
            if db.execute("UPDATE feature_recipes SET state='approved',updated_at=? WHERE project_id=? AND recipe_id=? AND state='pending_review'",
                          (timestamp(),pid,rid)).rowcount!=1:
                raise ValueError('配方审核状态冲突')
            self._event(db,pid,rid,'approve','pending_review','approved',{'reason':str(reason).strip(),'source_sha256':record['source_sha256']})
        return self.get(pid,rid)

    def _find_existing(self,rid):
        from sqlalchemy import select
        from timepilot.db import NativeBIAssetRow
        with self.native.store.sessions() as session:
            rows=session.scalars(select(NativeBIAssetRow).where(NativeBIAssetRow.lineage_json.contains(rid))).all()
            for x in rows:
                meta=self.native._metadata(x)
                if meta.get('lineage',{}).get('feature_recipe_id')==rid:
                    return meta
        return None

    def publish(self,pid,rid):
        self._editor(pid)
        lock=self.root / pid / rid
        lock.mkdir(parents=True,exist_ok=True)
        with exclusive_build_lock(lock/'.publish.lock',timeout=45):
            record=self.get(pid,rid)
            if record['state']=='published':return record
            if record['state'] not in ('approved','publishing'):
                raise ValueError('特征配方需要先经过审核')
            spec,meta=self._pinned(pid,record)
            sourcefile=lock/'data.parquet'
            with self.projects._lock,self.projects._db() as db:
                db.execute("UPDATE feature_recipes SET state='publishing',updated_at=? WHERE project_id=? AND recipe_id=? AND state IN ('approved','publishing')",
                           (timestamp(),pid,rid))
            # On retry, do not trust unsigned previous checkpoints: recompute
            # from the immutable, verified source rather than reusing bytes.
            tmp=lock/'data.building.parquet'
            tmp.unlink(missing_ok=True)
            try:
                with self.native._connection() as con:
                    relation,fields,_=self._compiled(con,pid,spec)
                    export_path=str(tmp).replace("'","''")
                    con.execute(f"COPY (SELECT * EXCLUDE (__tp_rowid) FROM {relation} ORDER BY \"__tp_rowid\") TO '{export_path}' (FORMAT PARQUET)")
                os.replace(tmp,sourcefile)
                from timepilot.storage import sha256_file
                hash_parquet=sha256_file(sourcefile)
                lineage={'type':'feature_lab','project_id':pid,'feature_recipe_id':rid,
                         'spec_sha256':record['spec_sha256'],'source_sha256':record['source_sha256'],
                         'artifact_sha256':hash_parquet}
                existing=self._find_existing(rid)
                if existing:
                    if existing['lineage']!=lineage:
                        raise ValueError('历史特征发布记录与当前配方不一致')
                    created=existing
                else:
                    created=self.native.import_from_path(sourcefile,spec.name+' · 特征数据',lineage)
                if not self.native.verify(created['asset_id']).get('valid'):
                    raise ValueError('新特征数据文件未通过完整性验证')
                with self.projects._lock,self.projects._db() as db:
                    db.execute('''INSERT OR IGNORE INTO project_native_assets
                        (project_id,native_asset_id,source_asset_id,created_at) VALUES(?,?,?,?)''',
                        (pid,created['asset_id'],spec.asset_id,timestamp()))
                    updated=db.execute("UPDATE feature_recipes SET state='published',derived_id=?,updated_at=? WHERE project_id=? AND recipe_id=? AND state='publishing'",
                               (created['asset_id'],timestamp(),pid,rid))
                    if updated.rowcount!=1:raise ValueError('特征发布状态冲突')
                    self._event(db,pid,rid,'publish','publishing','published',
                        {'derived_id':created['asset_id'],'source_sha256':record['source_sha256'],'artifact_sha256':hash_parquet})
                return self.get(pid,rid)
            finally:
                tmp.unlink(missing_ok=True)
