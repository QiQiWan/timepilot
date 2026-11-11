"""Industrial BI templates: version-pinned events, regimes and bounded DuckDB aggregation.

The source remains the full immutable Parquet revision; chart data are explicit,
auditable rollups. Only validated identifiers and finite query parameters can
reach DuckDB. No query is read from an Agent or user supplied SQL string.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import time
from datetime import datetime,timezone
from uuid import UUID,uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from timepilot.native_bi import NativeBIError,NativeBIDependencyError,quote_identifier

INTERVAL_SQL={'1m':'1 minute','5m':'5 minutes','15m':'15 minutes','1h':'1 hour','1d':'1 day'}
TEMPLATES={
    'temperature_trend': {'name':'设备温度趋势','description':'温度或压力的时序趋势、极值与采样覆盖'},
    'maintenance_overlay': {'name':'维护事件叠加','description':'在数值趋势上标记维护、更换和保养事件'},
    'regime_transition': {'name':'工况切换对比','description':'逐时桶检查工况一致性和状态变更'},
    'quality_process': {'name':'工艺过程质量','description':'按工艺时间观察厚度、负压和质量指标'},
    'equipment_comparison': {'name':'设备运行对比','description':'逐设备分析同一指标，避免混合预测实体'},
}


def stable_sha(obj) -> str:
    return hashlib.sha256(json.dumps(obj,sort_keys=True,ensure_ascii=False,default=str,
                                   separators=(',',':')).encode()).hexdigest()


class IndustrialBIError(ValueError):
    pass


class IndustrialSpec(BaseModel):
    model_config=ConfigDict(extra='forbid')
    asset_id:str
    version_id:str='base'
    template:str='temperature_trend'
    time_field:str
    metric_field:str
    device_field:str|None=None
    device_value:str|None=None
    event_field:str|None=None
    regime_field:str|None=None
    interval:str='1h'
    time_from:str|None=None
    time_to:str|None=None
    max_points:int=Field(default=1000,ge=10,le=2000)

    @model_validator(mode='after')
    def validate(self):
        if self.template not in TEMPLATES or self.interval not in INTERVAL_SQL:
            raise ValueError('工业分析模板或时间聚合间隔无效')
        if self.device_field and not self.device_value:
            raise ValueError('选择设备标识字段后必须指定单台设备，避免混合实体')
        if self.device_value and not self.device_field:
            raise ValueError('请先选择设备标识字段')
        if self.time_field==self.metric_field:
            raise ValueError('时间和指标字段不能相同')
        for value in (self.time_from,self.time_to):
            if value is not None:
                try:datetime.fromisoformat(value.replace('Z','+00:00'))
                except ValueError:raise ValueError('时间范围必须为 ISO 8601') from None
        if self.time_from and self.time_to and self.time_from>=self.time_to:
            raise ValueError('起止时间需要递增')
        return self


class SaveIndustrialView(BaseModel):
    model_config=ConfigDict(extra='forbid')
    name:str=Field(min_length=1,max_length=120)
    spec:IndustrialSpec


class IndustrialBI:
    def __init__(self,projects,native):
        self.projects=projects
        self.native=native
        with self.projects._db() as con:
            con.execute('''CREATE TABLE IF NOT EXISTS project_industrial_views(
                view_id TEXT PRIMARY KEY,project_id TEXT NOT NULL,asset_id TEXT NOT NULL,
                version_id TEXT NOT NULL,name TEXT NOT NULL,spec_json TEXT NOT NULL,
                source_sha256 TEXT NOT NULL,definition_sha256 TEXT NOT NULL,
                created_at TEXT NOT NULL)''')
            con.execute('CREATE INDEX IF NOT EXISTS idx_industrial_project ON project_industrial_views(project_id,created_at)')

    def _asset(self,pid,aid,vid='base'):
        self.projects.get(pid)
        try:self.projects.require_generic(pid,aid)
        except KeyError:self.projects.require_native(pid,aid)
        if self.native is None:
            raise IndustrialBIError('DuckDB BI 依赖未就绪')
        asset=self.native.asset(aid)
        self.native._version(aid,vid)
        return asset

    def suggestions(self,pid,asset_id):
        asset=self._asset(pid,asset_id)
        fields=asset['columns'];roles=asset.get('lineage',{}).get('confirmed_roles',{})
        names={item['name'] for item in fields}
        time_field=roles.get('time') if roles.get('time') in names else next(
            (f['name'] for f in fields if re.search('DATE|TIME',f.get('physical_type',''),re.I)),None)
        metric_field=roles.get('target') if roles.get('target') in names else next(
            (f['name'] for f in fields if re.search('DOUBLE|FLOAT|DECIMAL|INTEGER|BIGINT|REAL',f.get('physical_type',''),re.I)),None)
        return {'asset_id':asset_id,'templates':[{'id':key,**value} for key,value in TEMPLATES.items()],
                'suggested_fields':{'time_field':time_field,'metric_field':metric_field,
                                    'device_field':roles.get('device') or roles.get('entity'),
                                    'event_field':roles.get('event'),
                                    'regime_field':roles.get('process') or roles.get('group')},
                'fields':fields,'version_id':asset['head_version'],'evidence':'confirmed_roles_or_physical_types'}

    def _checked(self,pid,spec:IndustrialSpec):
        asset=self._asset(pid,spec.asset_id,spec.version_id)
        names={field['name'] for field in asset['columns']}
        required=[spec.time_field,spec.metric_field,spec.device_field,spec.event_field,spec.regime_field]
        if any(field is not None and field not in names for field in required):
            raise IndustrialBIError('一个或多个字段不属于当前数据版本')
        if spec.template=='maintenance_overlay' and not spec.event_field:
            raise IndustrialBIError('维护事件模板必须指定真实事件字段')
        if spec.template=='regime_transition' and not spec.regime_field:
            raise IndustrialBIError('工况切换模板必须指定工况字段')
        return asset

    def preview(self,pid,request:IndustrialSpec,*,arrow=False):
        asset=self._checked(pid,request)
        tick=time.monotonic()
        q=quote_identifier
        td=f'TRY_CAST({q(request.time_field)} AS TIMESTAMP)'
        metric=f'TRY_CAST({q(request.metric_field)} AS DOUBLE)'
        regime=f'CAST({q(request.regime_field)} AS VARCHAR)' if request.regime_field else 'NULL::VARCHAR'
        event=f'CAST({q(request.event_field)} AS VARCHAR)' if request.event_field else 'NULL::VARCHAR'
        predicate='(event_text IS NOT NULL AND TRIM(event_text)<>\'\' AND lower(TRIM(event_text)) NOT IN (\'0\',\'false\',\'none\',\'normal\',\'no\',\'无\'))'
        source=f'''SELECT {td} AS stamp, {metric} AS measure,
                   {regime} AS operating_mode, {event} AS event_text
                   FROM __tp_effective'''
        # Parameterized filters; no user-authored expression or arbitrary SQL.
        where=[];params=[]
        if request.device_field:
            where.append(f'CAST({q(request.device_field)} AS VARCHAR)=?')
            params.append(request.device_value)
        # Avoid time-zone coercion by comparing the source's parsed timestamp.
        if request.time_from:
            where.append(f'{td}>=TRY_CAST(? AS TIMESTAMP)')
            params.append(request.time_from)
        if request.time_to:
            where.append(f'{td}<TRY_CAST(? AS TIMESTAMP)')
            params.append(request.time_to)
        if where:
            source+=' WHERE '+' AND '.join(where)
        bucket=f"time_bucket(INTERVAL '{INTERVAL_SQL[request.interval]}',stamp)"
        sql=f'''WITH typed AS ({source}), aggreg AS (
            SELECT {bucket} AS timestamp, AVG(measure) AS average,
                MIN(measure) AS minimum,MAX(measure) AS maximum,
                COUNT(*) AS source_rows,COUNT(measure) AS valid_points,
                MODE(operating_mode) AS operating_mode,
                COUNT(DISTINCT operating_mode) AS regime_count,
                SUM(CASE WHEN {predicate} THEN 1 ELSE 0 END) AS maintenance_count
            FROM typed WHERE stamp IS NOT NULL GROUP BY 1)
            SELECT timestamp,average,minimum,maximum,source_rows,valid_points,
                operating_mode,regime_count,maintenance_count,
                CASE WHEN maintenance_count>0 THEN average ELSE NULL END AS maintenance_marker
            FROM aggreg ORDER BY timestamp LIMIT {request.max_points+1}'''
        event_sql=f'''WITH typed AS ({source}) SELECT stamp,event_text,operating_mode,measure
                   FROM typed WHERE stamp IS NOT NULL AND {predicate}
                   ORDER BY stamp LIMIT 201'''
        import os,threading
        with self.native._connection() as con:
            timer=threading.Timer(min(60,max(1,int(os.getenv('TIMEPILOT_BI_QUERY_TIMEOUT','15')))),con.interrupt)
            timer.daemon=True
            try:
                self.native._install_effective_view(con,request.asset_id,request.version_id,asset['columns'])
                timer.start()
                header=con.execute(sql,params)
                cols=[x[0] for x in header.description]
                rows=[dict(zip(cols,record)) for record in header.fetchall()]
                if len(rows)>request.max_points:
                    raise IndustrialBIError('结果时间桶超过 max_points，请选择较大采样间隔或缩小时间范围')
                if request.event_field:
                    events=[{'time':str(r[0]),'event':str(r[1])[:200],
                             'regime':str(r[2]) if r[2] is not None else None}
                            for r in con.execute(event_sql,params).fetchall()]
                else:events=[]
            finally:
                timer.cancel()
        if not arrow:
            return {'project_id':pid,'asset_id':request.asset_id,'version_id':request.version_id,
                    'source_sha256':asset['sha256'],'spec_sha256':stable_sha(request.model_dump()),
                    'template':request.template,'interval':request.interval,'rows':[
                        {k:(v.isoformat() if isinstance(v,datetime) else v) for k,v in row.items()}
                        for row in rows],
                    'events':events[:200],'events_truncated':len(events)>200,
                    'regime_mixed_buckets':sum(x['regime_count']>1 for x in rows),
                    'elapsed_ms':round((time.monotonic()-tick)*1000,2),
                    'is_full_data_aggregation':True,'has_row_truncation':False}
        try:
            import pyarrow as pa
        except ImportError as exc:
            raise NativeBIDependencyError('工业 Perspective 视图需要 pyarrow') from exc
        if not rows:
            raise IndustrialBIError('当前筛选范围没有有效时间/指标数据')
        # Native Perspective expects timestamp and typed columns, not json strings.
        table=pa.Table.from_pylist(rows)
        sink=pa.BufferOutputStream()
        with pa.ipc.new_stream(sink,table.schema) as writer:
            writer.write_table(table)
        return sink.getvalue().to_pybytes()

    def save(self,pid,request:SaveIndustrialView):
        self.projects.writable(pid)
        asset=self._checked(pid,request.spec)
        payload=request.spec.model_dump(mode='json')
        vid=str(uuid4());now=datetime.now(timezone.utc).isoformat()
        signature=stable_sha({'spec':payload,'source_sha256':asset['sha256']})
        with self.projects._lock,self.projects._db() as con:
            con.execute('''INSERT INTO project_industrial_views VALUES(?,?,?,?,?,?,?,?,?)''',
                        (vid,pid,request.spec.asset_id,request.spec.version_id,request.name.strip(),
                         json.dumps(payload,ensure_ascii=False),asset['sha256'],signature,now))
        return self.get(pid,vid)

    def list(self,pid):
        self.projects.get(pid)
        with self.projects._db() as con:
            return [dict(x) for x in con.execute('''SELECT view_id,asset_id,version_id,name,source_sha256,
                definition_sha256,created_at FROM project_industrial_views WHERE project_id=?
                ORDER BY created_at DESC LIMIT 100''',(pid,)).fetchall()]

    def get(self,pid,view_id):
        self.projects.get(pid)
        with self.projects._db() as con:
            row=con.execute('SELECT * FROM project_industrial_views WHERE project_id=? AND view_id=?',
                            (pid,str(UUID(view_id)))).fetchone()
        if row is None:raise KeyError('当前项目没有该工业视图')
        out=dict(row)
        out['spec']=json.loads(out.pop('spec_json'))
        asset=self._asset(pid,out['asset_id'],out['version_id'])
        if asset['sha256']!=out['source_sha256']:
            raise IndustrialBIError('工业视图关联数据源校验失败')
        return out
