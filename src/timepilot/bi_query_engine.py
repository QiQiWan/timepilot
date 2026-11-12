"""Bounded DuckDB execution of reviewed BI workspace state, with no user SQL.

The engine always pins immutable asset revisions. Computed columns, joins and time
windows are virtual, do not change the source and cannot leak into a training
DataSelection without an explicit materialization/approval step.
"""
from __future__ import annotations
import math
import os
import threading
import time
from datetime import datetime, date

from timepilot.native_bi import (NativeBIError, NativeBIDependencyError, QuerySpec,
    compile_query, quote_identifier, safe_json)

INTERVALS={'1m':'1 minute','5m':'5 minutes','15m':'15 minutes','1h':'1 hour','1d':'1 day'}
MAX_JOIN_ROWS=250_000
MAX_FANOUT=5
MAX_DISPLAY_ROWS=1000

class BIQueryEngine:
    def __init__(self, projects, native):
        self.projects, self.native = projects, native

    def checked(self,pid,aid,vid):
        self.projects.get(pid)
        try:self.projects.require_generic(pid,aid)
        except KeyError:self.projects.require_native(pid,aid)
        asset=self.native.asset(aid)
        self.native._version(aid,vid)
        return asset

    @staticmethod
    def _expression(node, columns, depth=0):
        if depth>6 or not isinstance(node,dict) or not set(node)<= {'kind','value','op','left','right','args'}:
            raise NativeBIError('计算表达式的结构或深度无效')
        kind=node.get('kind')
        if kind=='column':
            value=node.get('value')
            if value not in columns:raise NativeBIError('计算表达式引用未知字段')
            return f'TRY_CAST({quote_identifier(value)} AS DOUBLE)'
        if kind=='number':
            value=node.get('value')
            if not isinstance(value,(int,float)) or isinstance(value,bool) or not math.isfinite(value):
                raise NativeBIError('计算常量需要有限数值')
            return str(float(value))
        if kind=='binary':
            op=node.get('op')
            if op not in {'+','-','*','/'}:raise NativeBIError('未知计算操作')
            l=BIQueryEngine._expression(node.get('left'),columns,depth+1)
            r=BIQueryEngine._expression(node.get('right'),columns,depth+1)
            return f'({l} / NULLIF({r},0))' if op=='/' else f'({l} {op} {r})'
        if kind=='function':
            fn=node.get('op')
            ar=node.get('args')
            if not isinstance(ar,list) or len(ar)>3:raise NativeBIError('计算函数参数无效')
            if (fn=='abs' and len(ar)==1) or (fn=='round' and len(ar) in (1,2)) or (fn=='coalesce' and len(ar) in (2,3)):
                return f'{fn.upper()}('+','.join(BIQueryEngine._expression(v,columns,depth+1) for v in ar)+')'
        raise NativeBIError('仅支持安全的数值算术和受限函数')

    def join_preview(self,pid,state,join):
        left=self.checked(pid,state['asset_id'],state['version_id'])
        right=self.checked(pid,join['right_asset_id'],join['right_version_id'])
        if join.get('right_source_sha256') and join['right_source_sha256']!=right['sha256']:
            raise NativeBIError('关联表的源文件指纹变化，拒绝复用旧视图')
        left_cols={c['name'] for c in left['columns']}
        right_cols={c['name'] for c in right['columns']}
        if any('right__'+c in left_cols for c in right_cols):
            raise NativeBIError('关联字段安全别名与左表原始字段冲突，需先调整表结构')
        if join['left_key'] not in left_cols or join['right_key'] not in right_cols:
            raise NativeBIError('关联键不存在于对应资产')
        if join['right_asset_id']==state['asset_id'] and join['right_version_id']==state['version_id']:
            raise NativeBIError('自关联需另行明确别名和语义，首版暂不支持')
        l=self.native._effective_sql(state['asset_id'],state['version_id'],left['columns'])
        r=self.native._effective_sql(join['right_asset_id'],join['right_version_id'],right['columns'])
        ql=quote_identifier(join['left_key']);qr=quote_identifier(join['right_key'])
        # Exact output cardinality from GROUPED counts, never perform an exploding raw JOIN.
        sql=(f'WITH a AS (SELECT {ql} k,COUNT(*) n FROM ({l}) GROUP BY 1),'
             f'b AS (SELECT {qr} k,COUNT(*) n FROM ({r}) GROUP BY 1) '
             f'SELECT COALESCE(SUM(a.n * '
             + ('GREATEST(COALESCE(b.n,0),1)' if join['how']=='left' else 'COALESCE(b.n,0)')
             + '),0) FROM a LEFT JOIN b ON a.k=b.k')
        with self.native._connection() as con:
            timeout=min(60,max(1,int(os.getenv('TIMEPILOT_BI_QUERY_TIMEOUT','15'))))
            timer=threading.Timer(timeout,con.interrupt)
            timer.daemon=True
            try:
                timer.start()
                count=int(con.execute(sql).fetchone()[0])
            except Exception as exc:
                if not isinstance(exc,NativeBIError):
                    raise NativeBIError('关联基数扫描中断或失败，请缩小数据范围并重试') from exc
                raise
            finally:
                timer.cancel()
        if count>MAX_JOIN_ROWS or count>max(left['row_count']*MAX_FANOUT,MAX_FANOUT):
            raise NativeBIError(f'关联可能放大为 {count:,} 行，超过安全预算；请先核实键的唯一性或缩小数据范围')
        return {'left_asset_id':state['asset_id'],'right_asset_id':join['right_asset_id'],
                'left_rows':left['row_count'],'right_rows':right['row_count'],
                'estimated_rows':count,'fanout':round(count/max(1,left['row_count']),3),
                'scope':'full_revision_before_row_filters','approved':count<=MAX_JOIN_ROWS}

    def _source(self,con,pid,state):
        left=self.checked(pid,state['asset_id'],state['version_id'])
        self.native._install_effective_view(con,state['asset_id'],state['version_id'],left['columns'])
        fields=[c['name'] for c in left['columns']]
        source='__tp_effective'
        join=state.get('join')
        if join:
            # Recheck security and cardinality at every execution; a saved document is untrusted.
            self.join_preview(pid,state,join)
            right=self.checked(pid,join['right_asset_id'],join['right_version_id'])
            right_sql=self.native._effective_sql(join['right_asset_id'],join['right_version_id'],right['columns'])
            columns=[]
            for col in right['columns']:
                name='right__'+col['name']
                if name in fields or name in columns:raise NativeBIError('关联字段别名存在冲突')
                columns.append(name)
            q=quote_identifier
            extra=','.join('r.'+q(col['name'])+' AS '+q(name) for col,name in zip(right['columns'],columns))
            sql=f'SELECT l.*, {extra} FROM {source} l {join["how"].upper()} JOIN ({right_sql}) r ON l.{q(join["left_key"])}=r.{q(join["right_key"])}'
            con.execute('CREATE TEMP VIEW __tp_bi_join AS '+sql)
            source='__tp_bi_join';fields.extend(columns)
        expression=state.get('expressions') or []
        for idx, item in enumerate(expression):
            if len(fields)>=260:raise NativeBIError('计算列数量超过限制')
            name=item['name']
            if name in fields or name=='__tp_rowid':raise NativeBIError('计算字段名重复或属于系统保留字段')
            fragment=self._expression(item['expression'],set(fields))
            name_view=f'__tp_bi_expr_{idx}'
            con.execute(f'CREATE TEMP VIEW {name_view} AS SELECT *,{fragment} AS {quote_identifier(name)} FROM {source}')
            source=name_view;fields.append(name)
        windows=([state['window']] if state.get('window') else [])+list(state.get('windows') or [])
        for idx,window in enumerate(windows):
            for col in [window['column'],window['time_field'],window.get('entity_field')]:
                if col and col not in fields and col!='__tp_rowid':
                    raise NativeBIError('滚动窗口使用了不存在的字段')
            name=window['name']
            if name in fields or name=='__tp_rowid':raise NativeBIError('滚动窗口输出列名重复')
            q=quote_identifier
            partition='PARTITION BY '+q(window['entity_field'])+' ' if window.get('entity_field') else ''
            order=q(window['time_field'])
            # Every immutable native asset carries __tp_rowid. It resolves equal
            # timestamps without depending on Parquet physical scan order.
            order += '' if window['time_field']=='__tp_rowid' else ', "__tp_rowid"'
            frame=f"ROWS BETWEEN {window['window']-1} PRECEDING AND CURRENT ROW"
            expr=f'AVG(TRY_CAST({q(window["column"])} AS DOUBLE)) OVER ({partition}ORDER BY {order} {frame})'
            view=f'__tp_bi_window_{idx}'
            con.execute(f'CREATE TEMP VIEW {view} AS SELECT *,{expr} AS {q(name)} FROM {source}')
            source=view;fields.append(name)
        return source,fields

    def _bucket(self,con,source,fields,state,*,limit,arrow):
        data=state['bucket'];q=quote_identifier
        for col in [data['time_field'],data['metric_field'],data.get('entity_field')]:
            if col and col not in fields:raise NativeBIError('时间聚合引用了未知字段')
        interval=INTERVALS[data['interval']]
        expr=f"time_bucket(INTERVAL '{interval}',TRY_CAST({q(data['time_field'])} AS TIMESTAMP))"
        metric=data['metric_field'];operation=data['operation']
        numerical=f'TRY_CAST({q(metric)} AS DOUBLE)'
        aggregate=f'COUNT({q(metric)})' if operation=='count' else f'{operation.upper()}({numerical})'
        entity=(', '+q(data['entity_field'])) if data.get('entity_field') else ''
        grouping='1,2' if entity else '1'
        # DataSelection filters are always applied before aggregation; avoid chart sampling.
        from timepilot.native_bi import FilterSpec
        if state['query'].get('filters'):
            query=QuerySpec(asset_id=state['asset_id'],version_id=state['version_id'],
                columns=fields[:1],filters=[FilterSpec.model_validate(f) for f in state['query']['filters']],limit=1)
            sql,p, _=compile_query(query,fields,source_sql=source)
            where=sql.split(' FROM '+source,1)[1].split(' ORDER BY ',1)[0].split(' LIMIT ',1)[0]
            # clause produced exclusively by compile_query, values remain bound
        else:where='';p=[]
        sql=(f'SELECT {expr} AS "time_bucket"{entity}, {aggregate} AS "value",COUNT(*) AS "source_rows" '
             f'FROM {source}{where} GROUP BY {grouping} HAVING "time_bucket" IS NOT NULL '
             f'ORDER BY 1 LIMIT ?')
        return self._run(con,sql,[*p,limit],arrow=arrow)

    @staticmethod
    def _run(con,sql,params,*,arrow=False):
        cursor=con.execute(sql,params)
        if arrow:
            try:
                import pyarrow as pa
            except ImportError as exc:
                raise NativeBIDependencyError('Arrow 可视化需要 pyarrow') from exc
            table=cursor.to_arrow_table()
            sink=pa.BufferOutputStream()
            with pa.ipc.new_stream(sink,table.schema) as out:out.write_table(table)
            return sink.getvalue().to_pybytes()
        names=[x[0] for x in cursor.description]
        return {'columns':names,'rows':[dict(zip(names,safe_json(row))) for row in cursor.fetchall()]}

    def execute(self,pid,state,*,arrow=False,limit=None):
        self.checked(pid,state['asset_id'],state['version_id'])
        if state.get('industrial'):
            from timepilot.industrial_bi import IndustrialBI,IndustrialSpec
            service=IndustrialBI(self.projects,self.native)
            spec=IndustrialSpec(asset_id=state['asset_id'],version_id=state['version_id'],**state['industrial'])
            return service.preview(pid,spec,arrow=arrow)
        start=time.monotonic()
        max_rows=min(MAX_DISPLAY_ROWS,max(1,int(limit or state['query'].get('limit',300))))
        with self.native._connection() as con:
            timeout=min(60,max(1,int(os.getenv('TIMEPILOT_BI_QUERY_TIMEOUT','15'))))
            timer=threading.Timer(timeout,con.interrupt);timer.daemon=True
            try:
                timer.start()
                source,fields=self._source(con,pid,state)
                if state.get('bucket'):
                    result=self._bucket(con,source,fields,state,limit=max_rows,arrow=arrow)
                else:
                    query=QuerySpec.model_validate({**state['query'],'asset_id':state['asset_id'],
                        'version_id':state['version_id'],'limit':max_rows,'offset':0})
                    sql,params,_=compile_query(query,fields,source_sql=source)
                    result=self._run(con,sql,[*params,query.limit,query.offset],arrow=arrow)
            except Exception as exc:
                if time.monotonic()-start>=timeout:raise NativeBIError('BI 查询超时，已停止执行') from exc
                raise
            finally:timer.cancel()
        if arrow:return result
        return {'asset_id':state['asset_id'],'version_id':state['version_id'],
                **result,'returned':len(result.get('rows',[])),
                'elapsed_ms':round((time.monotonic()-start)*1000,2),
                'source':'duckdb_real_immutable_revision','is_sample':True,
                'chart':state['chart'],'filters':state['query'].get('filters',[])}
