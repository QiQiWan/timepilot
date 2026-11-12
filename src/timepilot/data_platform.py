"""Project-scoped canonical asset directory, frozen row selections and quality governance.

Additive SQLite schema. Existing importer, Parquet revisions and model runs remain readable.
No user-authored SQL, filesystem path, or raw data is exposed to the Agent API.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel, ConfigDict, Field, model_validator

from timepilot.generic_assets import GenericAssetStore
from timepilot.native_bi import FilterSpec, QuerySpec, compile_query
from timepilot.storage import sha256_file


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def signature(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                      separators=(',', ':'), default=str).encode('utf-8')).hexdigest()


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


class AssetRename(Strict):
    name: str = Field(min_length=1, max_length=100)


class SelectionInput(Strict):
    native_asset_id: str
    version_id: str = 'base'
    filters: list[FilterSpec] = Field(default_factory=list, max_length=12)
    timezone: str = Field(default='Asia/Shanghai', min_length=2, max_length=64)
    null_policy: str = Field(default='preserve', pattern=r'^preserve$')
    confirm: bool = False


class QualityBounds(Strict):
    min: float | None = None
    max: float | None = None

    @model_validator(mode='after')
    def check(self):
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError('工程值域上界必须大于或等于下界')
        return self


class QualityPolicy(Strict):
    # Optional time-quality contract; numeric sequence indices are not dates.
    time_field: str | None = None
    entity_field: str | None = None
    max_gap_seconds: float | None = Field(default=None, gt=0)
    require_monotonic: bool = False
    required_fields: list[str] = Field(default_factory=list, max_length=64)
    unique_key: list[str] = Field(default_factory=list, max_length=4)
    ranges: dict[str, QualityBounds] = Field(default_factory=dict)
    max_missing_fraction: float = Field(default=0, ge=0, le=1)

    @model_validator(mode='after')
    def check(self):
        if len(set(self.required_fields)) != len(self.required_fields):
            raise ValueError('必填字段不可重复')
        if len(set(self.unique_key)) != len(self.unique_key):
            raise ValueError('唯一键不可重复')
        if len(self.ranges) > 32:
            raise ValueError('每份资产最多定义 32 条数值范围规则')
        return self


class QualityDecision(Strict):
    status: str = Field(pattern=r'^(acknowledged|resolved|open)$')
    reason: str = Field(min_length=4, max_length=500)


class DataPlatform:
    def __init__(self, catalog, native=None, generic=None):
        self.catalog = catalog
        self.native = native
        self.generic = generic or GenericAssetStore(catalog.imports)
        self._setup()

    def _setup(self):
        with self.catalog._lock, self.catalog._db() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS timepilot_data_schema_migrations(
                version INTEGER PRIMARY KEY, description TEXT NOT NULL, applied_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS unified_assets(
                asset_id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
                import_id TEXT NOT NULL, candidate_id TEXT NOT NULL,
                name TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(project_id,import_id,candidate_id));
            CREATE INDEX IF NOT EXISTS idx_unified_assets_project ON unified_assets(project_id);
            CREATE TABLE IF NOT EXISTS unified_versions(
                project_id TEXT NOT NULL, asset_id TEXT NOT NULL, version_id TEXT NOT NULL,
                native_asset_id TEXT NOT NULL, data_sha256 TEXT NOT NULL,
                row_count INTEGER NOT NULL, column_count INTEGER NOT NULL,
                created_at TEXT NOT NULL, PRIMARY KEY(project_id,asset_id,version_id));
            CREATE INDEX IF NOT EXISTS idx_unified_versions_native ON unified_versions(project_id,native_asset_id);
            CREATE TABLE IF NOT EXISTS unified_selections(
                selection_id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
                native_asset_id TEXT NOT NULL, version_id TEXT NOT NULL,
                source_sha256 TEXT NOT NULL, selection_hash TEXT NOT NULL,
                row_count INTEGER NOT NULL, document TEXT NOT NULL, created_at TEXT NOT NULL,
                UNIQUE(project_id,selection_hash));
            CREATE INDEX IF NOT EXISTS idx_unified_selections_project ON unified_selections(project_id);
            CREATE TABLE IF NOT EXISTS unified_quality_policies(
                project_id TEXT NOT NULL, asset_id TEXT NOT NULL,
                config_hash TEXT NOT NULL, config_json TEXT NOT NULL, created_at TEXT NOT NULL,
                PRIMARY KEY(project_id,asset_id,config_hash));
            CREATE TABLE IF NOT EXISTS unified_quality_runs(
                run_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, asset_id TEXT NOT NULL,
                version_id TEXT NOT NULL, config_hash TEXT NOT NULL,
                source_sha256 TEXT NOT NULL, document TEXT NOT NULL, created_at TEXT NOT NULL,
                UNIQUE(project_id,asset_id,version_id,config_hash,source_sha256));
            CREATE INDEX IF NOT EXISTS idx_unified_quality_runs ON unified_quality_runs(project_id,asset_id,created_at);
            CREATE TABLE IF NOT EXISTS unified_quality_issues(
                issue_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, run_id TEXT NOT NULL,
                rule TEXT NOT NULL, severity TEXT NOT NULL, affected_count INTEGER NOT NULL,
                sample_rows_json TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open',
                decision_reason TEXT, updated_at TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS idx_unified_quality_issues ON unified_quality_issues(project_id,run_id);
            CREATE TABLE IF NOT EXISTS unified_audit(
                audit_id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
                action TEXT NOT NULL, object_id TEXT NOT NULL, details_json TEXT NOT NULL,
                created_at TEXT NOT NULL);
            ''')
            db.execute('''INSERT OR IGNORE INTO timepilot_data_schema_migrations(version,description,applied_at)
                VALUES(?,?,?)''',(1,'Unified directory, immutable selections and quality ledger',utcnow()))

    def audit(self, pid: str, action: str, object_id: str, details: dict):
        with self.catalog._lock, self.catalog._db() as db:
            db.execute('INSERT INTO unified_audit VALUES(?,?,?,?,?,?)',
                       (str(uuid5(NAMESPACE_URL, f'{pid}:{action}:{object_id}:{utcnow()}')),
                        pid, action, object_id, json.dumps(details,ensure_ascii=False), utcnow()))

    def register(self, pid: str, link: dict, meta: dict) -> dict:
        # Registration is a read-only catalogue refresh. Trashed links remain
        # registered but cannot be opened via require_generic().
        self.catalog.get(pid)
        with self.catalog._db() as db:
            linked=db.execute('''SELECT 1 FROM project_generic_assets WHERE project_id=?
                AND native_asset_id=? AND import_id=? AND candidate_id=?''',
                (pid,link['native_asset_id'],link['import_id'],link['candidate_id'])).fetchone()
        if not linked:raise KeyError('当前项目没有该资产来源')
        stable=str(uuid5(NAMESPACE_URL, 'timepilot-asset:'+pid+':'+link['import_id']+':'+link['candidate_id']))
        stamp=utcnow()
        with self.catalog._lock,self.catalog._db() as db:
            db.execute('''INSERT OR IGNORE INTO unified_assets
                (asset_id,project_id,import_id,candidate_id,name,status,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?)''',
                (stable,pid,link['import_id'],link['candidate_id'],meta['name'],'published',stamp,stamp))
            db.execute('''INSERT OR IGNORE INTO unified_versions
                (project_id,asset_id,version_id,native_asset_id,data_sha256,row_count,column_count,created_at)
                VALUES (?,?,?,?,?,?,?,?)''',
                (pid,stable,link['version_id'],link['native_asset_id'],meta['data_sha256'],
                 meta['row_count'],meta['column_count'],meta['created_at']))
            # Search index is refreshed without changing immutable asset versions.
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='asset_search_index'").fetchone():
                fields=json.dumps([x['name'] for x in meta.get('columns',[])],ensure_ascii=False)
                db.execute('''INSERT INTO asset_search_index(project_id,asset_id,name,source_file,source_sheet,fields_json,updated_at)
                    VALUES(?,?,?,?,?,?,?) ON CONFLICT(project_id,asset_id) DO UPDATE SET
                    source_file=excluded.source_file,source_sheet=excluded.source_sheet,
                    fields_json=excluded.fields_json''',
                    (pid,stable,meta['name'],meta.get('source_file') or '',meta.get('source_sheet') or '',fields,stamp))
            row=db.execute('SELECT * FROM unified_assets WHERE asset_id=? AND project_id=?',(stable,pid)).fetchone()
        return dict(row)

    def _resolve(self, pid: str, aid: str):
        self.catalog.get(pid)
        with self.catalog._db() as db:
            row=db.execute('SELECT * FROM unified_assets WHERE project_id=? AND asset_id=?',(pid,aid)).fetchone()
        if row is None:
            raise KeyError('项目中不存在该统一数据资产')
        with self.catalog._db() as db:
            hidden=db.execute('SELECT state FROM asset_lifecycle WHERE project_id=? AND asset_id=?',(pid,aid)).fetchone()
        if hidden and hidden['state']=='trashed':raise KeyError('该资产已移入回收站，请先恢复')
        return dict(row)

    def _version(self, pid: str, aid: str, version: str | None):
        asset=self._resolve(pid,aid)
        with self.catalog._db() as db:
            row=db.execute('''SELECT * FROM unified_versions WHERE project_id=? AND asset_id=?
                 AND version_id=COALESCE(?,version_id) ORDER BY created_at DESC LIMIT 1''',
                 (pid,aid,version)).fetchone()
        if row is None:raise KeyError('该项目资产版本不存在')
        self.catalog.require_generic(pid,row['native_asset_id'])
        return asset,dict(row)

    def list_assets(self, pid: str, *, include_trashed=False) -> list[dict]:
        self.catalog.get(pid)
        records={}
        with self.catalog._db() as db:
            lifecycle={r['asset_id']:r['state'] for r in db.execute('SELECT asset_id,state FROM asset_lifecycle WHERE project_id=?',(pid,))}
        for link in self.catalog.generic_for(pid):
            try:
                meta=self.generic.version(link['import_id'],link['candidate_id'],link['version_id'])
                record=self.register(pid,link,meta)
                life=lifecycle.get(record['asset_id'],'active')
                if life=='trashed' and not include_trashed:continue
                quality=self.latest_quality(pid,record['asset_id'],link['version_id'])
                item={'asset_id':record['asset_id'],'native_asset_id':link['native_asset_id'],
                               'name':record['name'],'kind':'generic_table','version_id':link['version_id'],
                               'row_count':meta['row_count'],'column_count':meta['column_count'],
                               'source_file':meta['source_file'],'source_sheet':meta['source_sheet'],
                               'source_region':meta.get('source_region'),
                               'source_header_row':meta.get('source_header_row'),
                               'created_at':record['created_at'],'updated_at':record['updated_at'],
                               'quality_status':quality.get('status') if quality else 'unchecked',
                               'quality_issue_count':quality.get('issue_count',0) if quality else 0,
                               'data_sha256':meta['data_sha256'],
                               'missing':False,'lifecycle_state':life}
                prior=records.get(record['asset_id'])
                if prior is None or (meta['created_at'],link['version_id']) > (prior['content_created_at'],prior['version_id']):
                    item['content_created_at']=meta['created_at']
                    records[record['asset_id']]=item
            except (OSError,ValueError,KeyError):
                key='missing:'+link['native_asset_id']
                records[key]={'kind':'generic_table','native_asset_id':link['native_asset_id'],
                              'version_id':link['version_id'],'missing':True,'quality_status':'unavailable'}
        return sorted(records.values(),key=lambda x:x.get('updated_at',x.get('content_created_at','')),reverse=True)

    def versions(self,pid,aid):
        self._resolve(pid,aid)
        with self.catalog._db() as db:
            rows=db.execute('''SELECT version_id,native_asset_id,data_sha256,row_count,column_count,created_at
                FROM unified_versions WHERE project_id=? AND asset_id=? ORDER BY created_at DESC''',(pid,aid)).fetchall()
        return [dict(r) for r in rows]

    def diff_versions(self,pid,aid,left:str,right:str):
        """Compare committed schemas and coverage, never report sampled data as full."""
        a,lv,lmeta,_=self._physical(pid,aid,left)
        _,rv,rmeta,_=self._physical(pid,aid,right)
        before={x['name']:x for x in lmeta['columns']}
        after={x['name']:x for x in rmeta['columns']}
        both=sorted(before.keys()&after.keys())
        return {'asset_id':aid,'left':lv['version_id'],'right':rv['version_id'],
                'source_file':lmeta['source_file'],
                'content_changed':lmeta['data_sha256']!=rmeta['data_sha256'],
                'row_count_change':rmeta['row_count']-lmeta['row_count'],
                'added_fields':sorted(after.keys()-before.keys()),
                'removed_fields':sorted(before.keys()-after.keys()),
                'role_changes':[{'role':role,
                    'field_before':lmeta['confirmed_roles'].get(role),
                    'field_after':rmeta['confirmed_roles'].get(role)}
                    for role in sorted(set(lmeta['confirmed_roles'])|set(rmeta['confirmed_roles']))
                    if lmeta['confirmed_roles'].get(role)!=rmeta['confirmed_roles'].get(role)],
                'field_changes':[{'field':key,
                    'type_before':before[key].get('physical_type'),
                    'type_after':after[key].get('physical_type'),
                    'missing_fraction_before':before[key].get('missing_fraction'),
                    'missing_fraction_after':after[key].get('missing_fraction')}
                    for key in both if (before[key].get('physical_type')!=after[key].get('physical_type')
                    or before[key].get('missing_fraction')!=after[key].get('missing_fraction'))],
                'scope':'full_published_row_count_and_nonempty_rates;schema_type_from_profile_sample'}

    def rename(self,pid,aid,name):
        self.catalog.writable(pid)
        self._resolve(pid,aid)
        with self.catalog._lock,self.catalog._db() as db:
            db.execute('UPDATE unified_assets SET name=?,updated_at=? WHERE project_id=? AND asset_id=?',
                       (name.strip(),utcnow(),pid,aid))
            db.execute('UPDATE asset_search_index SET name=?,updated_at=? WHERE project_id=? AND asset_id=?',
                       (name.strip(),utcnow(),pid,aid))
        self.audit(pid,'asset.rename',aid,{'name':name.strip()})
        return self._resolve(pid,aid)

    def schema(self,pid,aid,version_id=None):
        asset,ver=self._version(pid,aid,version_id)
        link=self.catalog.require_generic(pid,ver['native_asset_id'])
        meta=self.generic.version(link['import_id'],link['candidate_id'],link['version_id'])
        return {'asset_id':aid,'name':asset['name'],'version_id':ver['version_id'],
                'fields':meta['columns'],'roles':meta['confirmed_roles'],'rows':meta['row_count'],
                'source_hash':meta['data_sha256']}

    def _physical(self,pid,aid,version=None):
        asset,ver=self._version(pid,aid,version)
        link=self.catalog.require_generic(pid,ver['native_asset_id'])
        meta=self.generic.version(link['import_id'],link['candidate_id'],link['version_id'])
        return asset,ver,meta,self.generic.version_file(link['import_id'],link['candidate_id'],link['version_id'])

    def create_selection(self,pid,request:SelectionInput,*,save=True):
        try:
            self.catalog.require_generic(pid,request.native_asset_id)
        except KeyError:
            # Only a reviewed, project-scoped derived native asset may bypass
            # the legacy generic-table directory.
            self.catalog.require_native(pid,request.native_asset_id)
            lineage=self.native.asset(request.native_asset_id).get('lineage',{})
            if lineage.get('type')!='bi_agent_derived' or lineage.get('project_id')!=pid:
                raise ValueError('原生 BI 副本未经派生资产审批，不能冻结用于建模')
        asset=self.native.asset(request.native_asset_id) if self.native else None
        if asset is None:
            raise ValueError('原生 BI 尚未准备好，无法冻结完整行集')
        self.native._version(request.native_asset_id,request.version_id)
        cols=[c['name'] for c in asset['columns']]
        spec=QuerySpec(asset_id=request.native_asset_id,version_id=request.version_id,
                       columns=cols[:1],filters=request.filters,include_total=True,limit=1)
        compile_query(spec,cols,source_sql='__tp_effective')
        if not request.confirm and save:
            raise ValueError('请确认冻结数据范围后保存')
        result=self.native.query(spec)
        if result['total'] is None:
            raise ValueError('BI 未返回完整行数，拒绝冻结数据选择')
        payload={'project_id':pid,'native_asset_id':request.native_asset_id,
                 'version_id':request.version_id,'source_sha256':asset['sha256'],
                 'filters':[f.model_dump(mode='json') for f in request.filters],
                 'timezone':request.timezone,'null_policy':request.null_policy,
                 'scope':'full_row_level_no_chart_pagination','row_count':result['total']}
        payload['selection_hash']=signature(payload)
        payload['selection_id']=str(uuid5(NAMESPACE_URL,'timepilot-selection:'+pid+':'+payload['selection_hash']))
        payload['created_at']=utcnow()
        if save:
            self.catalog.writable(pid)
            with self.catalog._lock,self.catalog._db() as db:
                db.execute('''INSERT OR IGNORE INTO unified_selections
                    (selection_id,project_id,native_asset_id,version_id,source_sha256,selection_hash,row_count,document,created_at)
                    VALUES(?,?,?,?,?,?,?,?,?)''',
                    (payload['selection_id'],pid,request.native_asset_id,request.version_id,
                     asset['sha256'],payload['selection_hash'],payload['row_count'],
                     json.dumps(payload,ensure_ascii=False,sort_keys=True),payload['created_at']))
            self.audit(pid,'selection.freeze',payload['selection_id'],
                       {'selection_hash':payload['selection_hash'],'rows':payload['row_count']})
        return payload

    def get_selection(self,pid,sid):
        self.catalog.get(pid)
        with self.catalog._db() as db:
            row=db.execute('SELECT document FROM unified_selections WHERE selection_id=? AND project_id=?',(sid,pid)).fetchone()
        if row is None:raise KeyError('项目中不存在该冻结数据选择')
        payload=json.loads(row['document'])
        try:self.catalog.require_generic(pid,payload['native_asset_id'])
        except KeyError:
            self.catalog.require_native(pid,payload['native_asset_id'])
            lineage=self.native.asset(payload['native_asset_id']).get('lineage',{})
            if lineage.get('type')!='bi_agent_derived' or lineage.get('project_id')!=pid:
                raise ValueError('未审核的原生副本不得复用冻结数据选择')
        if signature({k:v for k,v in payload.items() if k not in ('selection_hash','selection_id','created_at')})!=payload['selection_hash']:
            raise ValueError('冻结选择内容校验失败')
        return payload

    def save_quality_policy(self,pid,aid,request:QualityPolicy):
        self.catalog.writable(pid)
        cols=set(x['name'] for x in self.schema(pid,aid)['fields'])
        claimed=set(request.required_fields)|set(request.unique_key)|set(request.ranges)
        claimed.update(x for x in (request.time_field,request.entity_field) if x)
        if request.max_gap_seconds is not None and not request.time_field:
            raise ValueError('采样缺口阈值需要同时指定实际时间字段')
        if claimed-cols:raise ValueError('质量规则引用不存在的字段：'+', '.join(sorted(claimed-cols)))
        data=request.model_dump(mode='json')
        digest=signature(data)
        with self.catalog._lock,self.catalog._db() as db:
            db.execute('INSERT OR IGNORE INTO unified_quality_policies VALUES(?,?,?,?,?)',
                       (pid,aid,digest,json.dumps(data,ensure_ascii=False),utcnow()))
        self.audit(pid,'quality.policy',aid,{'policy_hash':digest})
        return {'asset_id':aid,'config_hash':digest,'policy':data}

    def _policy(self,pid,aid):
        with self.catalog._db() as db:
            row=db.execute('''SELECT config_hash,config_json FROM unified_quality_policies
                WHERE project_id=? AND asset_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1''',(pid,aid)).fetchone()
        if row is None:return QualityPolicy(),signature(QualityPolicy().model_dump())
        return QualityPolicy.model_validate_json(row['config_json']),row['config_hash']

    def evaluate_quality(self,pid,aid,version_id=None):
        self.catalog.writable(pid)
        asset,version,meta,path=self._physical(pid,aid,version_id)
        policy,config_hash=self._policy(pid,aid)
        sha=meta['data_sha256']
        with self.catalog._db() as db:
            prior=db.execute('''SELECT document FROM unified_quality_runs WHERE project_id=? AND asset_id=?
                AND version_id=? AND config_hash=? AND source_sha256=?''',
                (pid,aid,version['version_id'],config_hash,sha)).fetchone()
        if prior:return {**json.loads(prior['document']),'reused':True}
        columns=meta.get('internal_columns') or meta['source_columns']
        required=policy.required_fields
        key_fields=policy.unique_key
        absent={c:0 for c in required}
        invalid={c:0 for c in policy.ranges}
        outofrange={c:0 for c in policy.ranges}
        samples={c:[] for c in [*required,*key_fields,*policy.ranges]}
        duplicate=0
        seen=set()
        unique_limit=800_000
        incomplete_uniqueness=False
        time_issues={k:0 for k in ('parse','order','gap','duplicate','entity_limit')}
        time_samples={k:[] for k in time_issues}
        time_last={}
        row_count=0
        with path.open('r',encoding='utf-8',newline='') as stream:
            reader=csv.DictReader(stream)
            if reader.fieldnames!=columns:
                raise ValueError('源字段或表头与发布清单不一致')
            for index,row in enumerate(reader,start=2):
                row_count+=1
                for col in required:
                    if row[col] is None or not str(row[col]).strip():
                        absent[col]+=1
                        if len(samples[col])<5:samples[col].append(index)
                for col,bounds in policy.ranges.items():
                    val=row.get(col)
                    if val is None or not str(val).strip():continue
                    try:
                        number=float(val)
                        if not math.isfinite(number):raise ValueError
                    except (ValueError,TypeError):
                        invalid[col]+=1
                        if len(samples[col])<5:samples[col].append(index)
                        continue
                    if (bounds.min is not None and number<bounds.min) or (bounds.max is not None and number>bounds.max):
                        outofrange[col]+=1
                        if len(samples[col])<5:samples[col].append(index)
                if policy.time_field:
                    raw=row.get(policy.time_field)
                    epoch=None
                    try:
                        if raw is None or not str(raw).strip():raise ValueError
                        parsed=datetime.fromisoformat(str(raw).strip().replace('Z','+00:00'))
                        if parsed.tzinfo is None:
                            parsed=parsed.replace(tzinfo=timezone.utc)
                        epoch=parsed.timestamp()
                    except (ValueError,TypeError,OverflowError):
                        time_issues['parse']+=1
                        if len(time_samples['parse'])<5:time_samples['parse'].append(index)
                    if epoch is not None:
                        entity=row.get(policy.entity_field) if policy.entity_field else ''
                        if entity not in time_last and len(time_last)>=100_000:
                            time_issues['entity_limit']+=1
                        else:
                            previous=time_last.get(entity)
                            if previous is not None:
                                gap=epoch-previous
                                if gap<0:
                                    time_issues['order']+=1
                                    if len(time_samples['order'])<5:time_samples['order'].append(index)
                                elif gap==0:
                                    time_issues['duplicate']+=1
                                    if len(time_samples['duplicate'])<5:time_samples['duplicate'].append(index)
                                elif policy.max_gap_seconds is not None and gap>policy.max_gap_seconds:
                                    time_issues['gap']+=1
                                    if len(time_samples['gap'])<5:time_samples['gap'].append(index)
                            time_last[entity]=epoch
                if key_fields:
                    key=tuple(row.get(c) for c in key_fields)
                    if len(seen)>=unique_limit:
                        incomplete_uniqueness=True
                    elif key in seen:
                        duplicate+=1
                        if len(samples[key_fields[0]])<5:samples[key_fields[0]].append(index)
                    else:seen.add(key)
        if row_count!=meta['row_count']:
            raise ValueError('全量质量检查行数与发布清单不一致，拒绝入库')
        issues=[]
        def add(rule,count,severity,lines):
            if count:issues.append({'rule':rule,'affected_count':count,'severity':severity,
                                    'sample_rows':sorted(set(lines))[:5],'status':'open'})
        for field,n in absent.items():
            add('required:'+field,n,'blocking' if n/row_count>policy.max_missing_fraction else 'warning',samples[field])
        for field,n in invalid.items():add('range.type:'+field,n,'blocking',samples[field])
        for field,n in outofrange.items():add('range.bounds:'+field,n,'warning',samples[field])
        add('key.duplicate',duplicate,'blocking',samples[key_fields[0]] if key_fields else [])
        if incomplete_uniqueness:add('key.uniqueness_incomplete',1,'blocking',[])
        for rule,count in time_issues.items():
            severity='blocking' if rule in ('parse','entity_limit') or (rule=='order' and policy.require_monotonic) else 'warning'
            add('time.'+rule,count,severity,time_samples[rule])
        run_id=str(uuid5(NAMESPACE_URL,f'quality:{pid}:{aid}:{version["version_id"]}:{config_hash}:{sha}'))
        status='blocked' if any(x['severity']=='blocking' for x in issues) else 'warning' if issues else 'passed'
        report={'run_id':run_id,'asset_id':aid,'version_id':version['version_id'],
                'source_sha256':sha,'config_hash':config_hash,'status':status,
                'total_rows':row_count,'issue_count':len(issues),'issues':[],
                'scope':'all_published_rows','created_at':utcnow()}
        with self.catalog._lock,self.catalog._db() as db:
            for item in issues:
                issue_id=str(uuid5(NAMESPACE_URL,run_id+':'+item['rule']))
                db.execute('INSERT OR IGNORE INTO unified_quality_issues VALUES(?,?,?,?,?,?,?,?,?,?)',
                           (issue_id,pid,run_id,item['rule'],item['severity'],item['affected_count'],
                            json.dumps(item['sample_rows']), 'open',None,utcnow()))
                report['issues'].append({'issue_id':issue_id,**item})
            db.execute('''INSERT OR IGNORE INTO unified_quality_runs
                (run_id,project_id,asset_id,version_id,config_hash,source_sha256,document,created_at)
                VALUES (?,?,?,?,?,?,?,?)''',
                (run_id,pid,aid,version['version_id'],config_hash,sha,json.dumps(report,ensure_ascii=False),utcnow()))
        self.audit(pid,'quality.evaluate',run_id,{'status':status,'rows':row_count,'issues':len(issues)})
        return report

    def latest_quality(self,pid,aid,version_id=None):
        self.catalog.get(pid)
        with self.catalog._db() as db:
            row=db.execute('''SELECT document FROM unified_quality_runs WHERE project_id=? AND asset_id=?
                  AND (? IS NULL OR version_id=?)
                  ORDER BY created_at DESC,rowid DESC LIMIT 1''',(pid,aid,version_id,version_id)).fetchone()
        if not row:return None
        report=json.loads(row['document'])
        issues=self.issues(pid,report['run_id'])
        report['issues']=issues
        report['open_blockers']=sum(1 for x in issues if x['severity']=='blocking' and x['status']=='open')
        report['status']='blocked' if report['open_blockers'] else 'warning' if any(x['status']=='open' for x in issues) else 'passed'
        return report

    def issues(self,pid,run_id=None):
        self.catalog.get(pid)
        sql='SELECT * FROM unified_quality_issues WHERE project_id=?'
        args=[pid]
        if run_id:sql+=' AND run_id=?';args.append(run_id)
        sql+=' ORDER BY updated_at DESC LIMIT 200'
        with self.catalog._db() as db:
            rows=db.execute(sql,args).fetchall()
        return [{**dict(row),'sample_rows':json.loads(row['sample_rows_json'])} for row in rows]

    def decide_issue(self,pid,issue_id,decision:QualityDecision):
        self.catalog.writable(pid)
        with self.catalog._lock,self.catalog._db() as db:
            row=db.execute('SELECT * FROM unified_quality_issues WHERE project_id=? AND issue_id=?',
                           (pid,issue_id)).fetchone()
            if row is None:raise KeyError('项目不存在该质量问题')
            db.execute('UPDATE unified_quality_issues SET status=?,decision_reason=?,updated_at=? WHERE project_id=? AND issue_id=?',
                       (decision.status,decision.reason,utcnow(),pid,issue_id))
        self.audit(pid,'quality.decision',issue_id,{'status':decision.status,'reason':decision.reason})
        return {'issue_id':issue_id,'status':decision.status,'reason':decision.reason}

    def impact(self,pid,aid):
        versions=self.versions(pid,aid)
        result=[]
        for ver in versions:
            native_id=ver['native_asset_id']
            self.catalog.require_generic(pid,native_id)
            linked_views=self.native.list_views(native_id) if self.native else []
            with self.catalog._db() as db:
                drafts=db.execute('''SELECT draft_id,name,status,run_id FROM project_model_drafts
                  WHERE project_id=? AND asset_id=?''',(pid,native_id)).fetchall() if db.execute(
                    "SELECT name FROM sqlite_master WHERE name='project_model_drafts'").fetchone() else []
            result.append({'version_id':ver['version_id'],'native_asset_id':native_id,
                           'views':[{'view_id':v['view_id'],'name':v['name']} for v in linked_views],
                           'models':[dict(r) for r in drafts]})
        return {'asset_id':aid,'versions':result}

    def storage_scan(self,pid,aid):
        self.catalog.get(pid)
        details=[]
        for ver in self.versions(pid,aid):
            try:
                _,_,meta,path=self._physical(pid,aid,ver['version_id'])
                bi={'valid':None,'reason':'BI 校验服务不可用'}
                if self.native is not None and callable(getattr(self.native,'verify',None)):
                    bi=self.native.verify(ver['native_asset_id'])
                details.append({'version_id':ver['version_id'],
                                'valid':bi.get('valid') is not False,
                                'source_sha256':meta['data_sha256'],
                                'bytes':path.stat().st_size,
                                'native_bi':{'valid':bi.get('valid'),'reason':bi.get('reason')}})
            except (ValueError,OSError,KeyError) as exc:
                details.append({'version_id':ver['version_id'],'valid':False,'reason':str(exc)[:250]})
        return {'asset_id':aid,'files':details,'all_valid':all(x['valid'] for x in details)}

    def audits(self,pid,limit=30):
        self.catalog.get(pid)
        with self.catalog._db() as db:
            rows=db.execute('SELECT * FROM unified_audit WHERE project_id=? ORDER BY created_at DESC LIMIT ?',
                            (pid,max(1,min(100,int(limit))))).fetchall()
        return [dict(r) for r in rows]
