"""v0.37.2 source-pinned, DuckDB-only overview dashboard.

All numbers in a response are computed on the same effective immutable version
and the same validated, parameterized row predicate.  Charts never infer
calendar time from a sequence index.  The caller must have project read access.
No browser-supplied identifier is interpolated without contract membership.
"""
from __future__ import annotations

from contextlib import contextmanager
from collections import OrderedDict
from copy import deepcopy
import threading
import time
from datetime import date, datetime
from decimal import Decimal
import hashlib
import json
import math
from timepilot.feature_compiler import NUMERIC
from timepilot.native_bi import quote_identifier
from timepilot.feature_dashboard_sql import category_sql
from timepilot.dashboard_metrics_contract import normalized_metrics

_ALLOWED_OPERATORS = {'eq', 'ne', 'gt', 'gte', 'lt', 'lte', 'between', 'is_null', 'not_null'}
_ALLOWED_KINDS = {'histogram', 'category', 'box', 'scatter', 'trend', 'quantiles'}
_DATE_TYPES = ('DATE', 'TIMESTAMP')  # TIME-only is not valid for calendar DATE_TRUNC


def _number(value):
    if value is None: return None
    if isinstance(value, (datetime, date)): return value.isoformat()
    if isinstance(value, Decimal): value = float(value)
    if isinstance(value, float) and not math.isfinite(value): return None
    return value


def _numeric(field):
    return bool(NUMERIC.match(field.physical_type))


def _time(field):
    return field.physical_type.upper().startswith(_DATE_TYPES)


def _fingerprint(data):
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), default=str).encode()).hexdigest()


class DataOverviewDashboard:
    # Bounded cross-request cache is safe only after source and ACL validation.
    _cache_lock=threading.Lock()
    _overview_cache=OrderedDict()
    CACHE_TTL_SECONDS=75
    CACHE_MAX_ENTRIES=32
    MAX_ROWS=2_000_000
    MAX_FILTERS=5
    MAX_PROFILE_FIELDS=24
    MAX_CORR_FIELDS=10
    MAX_SPEARMAN_FIELDS=6
    MAX_SPEARMAN_ROWS=100_000
    def __init__(self, eda): self.eda=eda

    def _fields(self, contract):
        return {f.field_id: f for f in contract.fields if f.classification!='restricted'}

    def _authorized(self, pid, aid, vid, sha=None, revision=None):
        # _source ensures actual bytes, stale-contract protection, and project scope.
        self.eda.projects.get(pid)
        src, con=self.eda._source(pid,aid,vid)
        if int(src.get('row_count') or 0)>self.MAX_ROWS:
            raise ValueError('数据超出本次仪表盘 200 万行预算，请先选择数据分区')
        if sha is not None and sha != src['sha256']:
            raise ValueError('数据版本已经变化，请刷新后重新查看')
        if revision is not None and revision != con.revision:
            raise ValueError('字段定义已经变化，请重新加载仪表盘')
        return src,con,self._fields(con)

    @staticmethod
    def _typed_value(field, raw):
        if raw is None or isinstance(raw, (dict,list,bool)):
            raise ValueError('筛选值不合法')
        if _numeric(field):
            try: x=float(raw)
            except (TypeError,ValueError,OverflowError): raise ValueError('数值筛选必须填写有效数字') from None
            if not math.isfinite(x):raise ValueError('禁止 NaN 或无穷大作为筛选值')
            return x
        s=str(raw)
        if len(s)>200: raise ValueError('筛选文本过长')
        return s

    def _predicate(self, fields, filters):
        if not isinstance(filters,list) or len(filters)>self.MAX_FILTERS:
            raise ValueError('筛选条件最多 5 项')
        parts=[];params=[];clean=[]
        for x in filters:
            if not isinstance(x,dict) or set(x)-{'field_id','op','value','upper'}:
                raise ValueError('筛选条件格式无效')
            fid=x.get('field_id');op=x.get('op')
            if not isinstance(fid,str) or not 0<len(fid)<=256:
                raise ValueError('筛选字段 ID 必须为有效文本')
            if not isinstance(op,str):raise ValueError('筛选操作类型无效')
            field=fields.get(fid)
            if field is None:raise ValueError('筛选字段不存在或为受限字段')
            if op not in _ALLOWED_OPERATORS:raise ValueError('不支持的筛选运算')
            col=quote_identifier(field.source_name)
            entry={'field_id':field.field_id,'op':op}
            if op in ('is_null','not_null'):
                parts.append(f'{col} IS {"NULL" if op=="is_null" else "NOT NULL"}')
            elif op=='between':
                a=self._typed_value(field,x.get('value'));b=self._typed_value(field,x.get('upper'))
                if a>b:raise ValueError('筛选区间下限大于上限')
                parts.append(f'{col} BETWEEN ? AND ?');params.extend([a,b]);entry.update(value=a,upper=b)
            else:
                value=self._typed_value(field,x.get('value'))
                symbol={'eq':'=','ne':'<>','gt':'>','gte':'>=','lt':'<','lte':'<='}[op]
                parts.append(f'{col} {symbol} ?');params.append(value);entry['value']=value
            clean.append(entry)
        return (' AND '.join(parts) if parts else 'TRUE'), params, clean

    @contextmanager
    def _data(self, aid, vid, src, where, params):
        with self.eda.native._connection() as db:
            self.eda.native._install_effective_view(db,aid,vid,src['columns'])
            # The effective version is evaluated once per request. All charts use
            # this exact row set; there are no UI-side sampling approximations.
            yield db, f' FROM __tp_effective WHERE {where}', list(params)

    def _source_meta(self,aid,vid,src,con,clean):
        return {'asset_id':aid,'version_id':vid,'source_sha256':src['sha256'],
                'contract_revision':con.revision,
                'scope_sha256':_fingerprint([aid,vid,src['sha256'],con.revision,clean]),
                'filters':clean,'population':'all_matching_rows'}

    def overview(self, pid,aid,vid='base',filters=None,field_ids=None,
                 association_ids=None,association_method='pearson',expected_sha256=None,expected_revision=None):
        src,con,fields=self._authorized(pid,aid,vid,expected_sha256,expected_revision)
        where,params,clean=self._predicate(fields,filters or [])
        ids=list(fields)[:self.MAX_PROFILE_FIELDS] if field_ids is None else field_ids
        if not isinstance(ids,list) or not 1<=len(ids)<=self.MAX_PROFILE_FIELDS or any(
                not isinstance(i,str) or not 0<len(i)<=256 for i in ids):
            raise ValueError('请明确选择 1–24 个有效统计字段')
        if len(set(ids))!=len(ids):raise ValueError('统计字段不能重复')
        if any(i not in fields for i in ids):raise ValueError('选择的统计字段不存在或受限')
        if association_method not in ('pearson','spearman'):
            raise ValueError('相关性方法仅支持 Pearson 和 Spearman')
        def association_candidate(f):
            return _numeric(f) and f.role not in ('record_id','order_index','entity_key','event_time','label','prediction_time','available_at') and f.source_name.casefold() not in ('time','index','row_id','row_number','序号')
        corr_ids=association_ids if association_ids is not None else [i for i in ids if association_candidate(fields[i])][:self.MAX_CORR_FIELDS]
        if not isinstance(corr_ids,list) or len(corr_ids)>self.MAX_CORR_FIELDS or any(
                not isinstance(i,str) or not 0<len(i)<=256 for i in corr_ids):
            raise ValueError('关联分析字段 ID 无效或超过 10 个')
        if len(set(corr_ids))!=len(corr_ids):raise ValueError('关联字段不能重复')
        if any(i not in fields or not _numeric(fields[i]) for i in corr_ids):
            raise ValueError('关联矩阵只能使用有权限的数值字段')
        if association_method=='spearman' and len(corr_ids)>self.MAX_SPEARMAN_FIELDS:
            raise ValueError('Spearman 最多一次选择 6 个数值字段，请先缩小范围')
        result=self._source_meta(aid,vid,src,con,clean)
        cache_key=_fingerprint([pid,aid,vid,src['sha256'],con.revision,clean,ids,corr_ids,association_method])
        now=time.monotonic()
        with self._cache_lock:
            cached=self._overview_cache.get(cache_key)
            if cached and now-cached[0]<self.CACHE_TTL_SECONDS:
                self._overview_cache.move_to_end(cache_key)
                ret=deepcopy(cached[1]);ret.setdefault('performance',{})['cache_hit']=True
                return ret
            if cached:self._overview_cache.pop(cache_key,None)
        with self._data(aid,vid,src,where,params) as (db,base,args):
            rows=int(db.execute('SELECT COUNT(*)'+base,args).fetchone()[0])
            if association_method=='spearman' and rows>self.MAX_SPEARMAN_ROWS:
                raise ValueError('Spearman 计算超过 10 万行预算，请先筛选设备/时间区间，或改为 Pearson')
            total=int(db.execute('SELECT COUNT(*) FROM __tp_effective').fetchone()[0])
            field_stats=[];missing=0
            # one bounded aggregate query for all selected columns
            clauses=[]
            for i,fid in enumerate(ids):
                field=fields[fid];col=quote_identifier(field.source_name)
                clauses.extend([f'COUNT({col})', f'COUNT(DISTINCT {col})'])
                if _numeric(field):
                    v=f'CASE WHEN isfinite(TRY_CAST({col} AS DOUBLE)) THEN TRY_CAST({col} AS DOUBLE) END'
                    clauses.extend([f'AVG({v})',f'STDDEV_POP({v})',f'MIN({v})',f'MAX({v})',
                      f'QUANTILE_CONT({v},0.1)',f'QUANTILE_CONT({v},0.25)',
                      f'MEDIAN({v})',f'QUANTILE_CONT({v},0.75)',f'QUANTILE_CONT({v},0.9)',
                      f'COUNT({v})',f'COUNT(TRY_CAST({col} AS DOUBLE))'])
            aggregates=db.execute('SELECT '+(','.join(clauses) if clauses else 'COUNT(*)')+base,args).fetchone()
            pos=0
            for fid in ids:
                f=fields[fid];count=int(aggregates[pos]);unique=int(aggregates[pos+1]);pos+=2
                missing+=rows-count
                entry={'field_id':fid,'name':f.source_name,'type':f.physical_type,
                       'role':f.role,'unit':f.unit,'numeric':_numeric(f),'temporal':_time(f),
                       'non_null':count,'null_count':rows-count,'null_rate':(rows-count)/rows if rows else None,
                       'distinct_count':unique}
                if _numeric(f):
                    names=['mean','stddev','min','max','p10','p25','median','p75','p90','finite_count','numeric_cast_count']
                    entry.update({key:_number(value) for key,value in zip(names,aggregates[pos:pos+11])})
                    pos+=11
                    finite=int(entry.get('finite_count') or 0)
                    cast=int(entry.get('numeric_cast_count') or 0)
                    entry['non_finite_count']=max(0,cast-finite)
                    entry['conversion_failed_count']=max(0,count-cast)
                    entry['invalid_numeric_count']=entry['non_finite_count']+entry['conversion_failed_count']
                    entry['effective_missing_count']=rows-int(entry.get('finite_count') or 0)
                    entry['effective_missing_rate']=(entry['effective_missing_count']/rows if rows else None)
                field_stats.append(entry)
            # Tukey whiskers are the extrema of actual inlier observations;
            # min/max are retained separately for data-quality inspection.
            whisker_exprs=[];whisker_refs=[]
            for entry in field_stats:
                if not entry['numeric'] or entry.get('p25') is None or entry.get('p75') is None:
                    continue
                q1,q3=float(entry['p25']),float(entry['p75'])
                iqr=q3-q1
                lower=q1-1.5*iqr;upper=q3+1.5*iqr
                if not all(math.isfinite(x) for x in (lower,upper,iqr)):
                    entry['tukey_status']='unsupported_numeric_range';continue
                f=fields[entry['field_id']]
                col=quote_identifier(f.source_name)
                val=f'(CASE WHEN isfinite(TRY_CAST({col} AS DOUBLE)) THEN TRY_CAST({col} AS DOUBLE) END)'
                # numeric limits originate solely from server-side DuckDB quantiles.
                bound_lo, bound_hi=format(lower,'.17g'),format(upper,'.17g')
                inlier=f'({val} BETWEEN {bound_lo} AND {bound_hi})'
                whisker_exprs.extend([f'MIN({val}) FILTER (WHERE {inlier})',
                    f'MAX({val}) FILTER (WHERE {inlier})',
                    f'COUNT(*) FILTER (WHERE {val} IS NOT NULL AND NOT {inlier})'])
                whisker_refs.append(entry)
            if whisker_exprs:
                whisker_row=db.execute('SELECT '+','.join(whisker_exprs)+base,args).fetchone()
                for index,entry in enumerate(whisker_refs):
                    entry.update(whisker_low=_number(whisker_row[3*index]),
                        whisker_high=_number(whisker_row[3*index+1]),
                        outlier_count=int(whisker_row[3*index+2]),
                        tukey_status='computed',whisker_method='Tukey 1.5 IQR')
            correlations=[]
            # All Pearson pairs are computed in ONE vectorized DuckDB scan.
            # Strict pairwise finite filtering prevents NaN becoming a value.
            corr_sql=[]; corr_refs=[]
            for i,a in enumerate(corr_ids):
                for b in corr_ids[i+1:]:
                    x=quote_identifier(fields[a].source_name)
                    y=quote_identifier(fields[b].source_name)
                    xx=f'(CASE WHEN isfinite(TRY_CAST({x} AS DOUBLE)) THEN TRY_CAST({x} AS DOUBLE) END)'
                    yy=f'(CASE WHEN isfinite(TRY_CAST({y} AS DOUBLE)) THEN TRY_CAST({y} AS DOUBLE) END)'
                    corr_sql.extend([f'COUNT(*) FILTER (WHERE {xx} IS NOT NULL AND {yy} IS NOT NULL)',
                                     f'CORR({xx},{yy})'])
                    corr_refs.append((a,b))
            if corr_sql and association_method=='pearson':
                values=db.execute('SELECT '+','.join(corr_sql)+base,args).fetchone()
                for i,(a,b) in enumerate(corr_refs):
                    count=int(values[i*2]); val=_number(values[i*2+1]) if count>1 else None
                    correlations.append({'x_field_id':a,'y_field_id':b,
                        'pair_count':count,'pearson':val})
            elif corr_sql and association_method=='spearman':
                # Correct Spearman ranks must be computed on pairwise-complete
                # rows. Ties receive the average rank (RANK + (tie_count-1)/2).
                for a,b in corr_refs:
                    x=quote_identifier(fields[a].source_name)
                    y=quote_identifier(fields[b].source_name)
                    xx=f'(CASE WHEN isfinite(TRY_CAST({x} AS DOUBLE)) THEN TRY_CAST({x} AS DOUBLE) END)'
                    yy=f'(CASE WHEN isfinite(TRY_CAST({y} AS DOUBLE)) THEN TRY_CAST({y} AS DOUBLE) END)'
                    query=(f'WITH finite_pairs AS (SELECT {xx} AS x, {yy} AS y '+base+
                        f' AND {xx} IS NOT NULL AND {yy} IS NOT NULL), '+
                        'ranked AS (SELECT '+
                        'RANK() OVER (ORDER BY x) + (COUNT(*) OVER (PARTITION BY x)-1)/2.0 AS rx, '+
                        'RANK() OVER (ORDER BY y) + (COUNT(*) OVER (PARTITION BY y)-1)/2.0 AS ry '+
                        'FROM finite_pairs) SELECT COUNT(*),CORR(rx,ry) FROM ranked')
                    pair_count,value=db.execute(query,args).fetchone()
                    correlations.append({'x_field_id':a,'y_field_id':b,'pair_count':int(pair_count),
                        'spearman':_number(value) if pair_count>1 else None})

        invalid=sum(int(f.get('invalid_numeric_count') or 0) for f in field_stats)
        non_finite=sum(int(f.get('non_finite_count') or 0) for f in field_stats)
        conversion_failed=sum(int(f.get('conversion_failed_count') or 0) for f in field_stats)
        denominator=rows*len(ids)
        metrics=normalized_metrics({
            'records':rows,'source_records':total,'columns':len(fields),
            'numeric_fields':sum(_numeric(f) for f in fields.values()),
            'selected_fields':len(ids),'missing_cells':missing,
            'invalid_numeric_cells':invalid,'non_finite_numeric_cells':non_finite,
            'conversion_failed_cells':conversion_failed,
            'missing_percent':round(100*(missing+invalid)/denominator,5) if denominator else None,
            'coverage_percent':round(100*rows/total,5) if total else None,
            'confirmed_time':bool(con.event_time_field_id and
                con.event_time_field_id in fields and
                fields[con.event_time_field_id].confirmation=='confirmed' and
                _time(fields[con.event_time_field_id])),
            'metric_scope':{'field_ids':ids,'rows':rows,'denominator_cells':denominator,
                'population':'filtered_version_rows',
                'missing_policy':'sql_null_plus_non_finite_plus_conversion_failed',
                'field_limit':self.MAX_PROFILE_FIELDS}})
        result.update({'rows_total':total,'rows_filtered':rows,
            'selection':{'field_ids':ids,'association_ids':corr_ids,'fields_truncated':len(fields)>len(ids)},
            'metrics':metrics,
            'fields':field_stats,'correlations':correlations,'association_method':association_method,
            'note':'缺失率=(SQL NULL+非有限数值+转换失败)/当前筛选行数×已选字段数；数值异常按互斥类别计数，不代表业务阈值异常。'})
        from timepilot.feature_evidence_v0373 import dashboard_explanations
        result['evidence_analysis']=dashboard_explanations(result)
        result['performance']={'cache_hit':False,'exact_full_scan':True}
        with self._cache_lock:
            self._overview_cache[cache_key]=(time.monotonic(),deepcopy(result))
            self._overview_cache.move_to_end(cache_key)
            while len(self._overview_cache)>self.CACHE_MAX_ENTRIES:
                self._overview_cache.popitem(last=False)
        return result

    def chart(self,pid,aid,vid='base',kind='histogram',field_id=None,
              other_field_id=None,filters=None,bins=16,limit=12,granularity='day',
              expected_sha256=None,expected_revision=None):
        src,con,fields=self._authorized(pid,aid,vid,expected_sha256,expected_revision)
        if not isinstance(kind,str) or kind not in _ALLOWED_KINDS:
            raise ValueError('不支持的仪表盘图表类型')
        if not isinstance(field_id,str) or not 0<len(field_id)<=256:
            raise ValueError('图表字段 ID 无效')
        if other_field_id is not None and (not isinstance(other_field_id,str) or len(other_field_id)>256):
            raise ValueError('对比字段 ID 无效')
        f=fields.get(field_id);o=fields.get(other_field_id) if other_field_id else None
        if not f:raise ValueError('图表字段不存在或受限')
        if kind in ('histogram','box','quantiles','trend','scatter') and not _numeric(f):
            raise ValueError('此图表需要数值字段')
        if kind=='scatter' and (o is None or not _numeric(o) or o.field_id==f.field_id):
            raise ValueError('散点图需要两个不同的数值字段')
        if kind=='trend':
            t=fields.get(con.event_time_field_id)
            if t is None or t.confirmation!='confirmed' or not _time(t):
                raise ValueError('需要确认真实时间字段才可绘制时间趋势')
            if granularity not in ('hour','day','week','month'):
                raise ValueError('不支持的时间粒度')
        if not isinstance(bins,int) or not 4<=bins<=50:raise ValueError('直方图分桶需在 4–50 之间')
        if not isinstance(limit,int) or not 3<=limit<=25:raise ValueError('类别上限需在 3–25 之间')
        where,params,clean=self._predicate(fields,filters or [])
        result=self._source_meta(aid,vid,src,con,clean)
        result.update(kind=kind,field_id=f.field_id,field_name=f.source_name,
                      other_field_id=other_field_id,points=[],sampled=False)
        col=quote_identifier(f.source_name)
        v=f'(CASE WHEN isfinite(TRY_CAST({col} AS DOUBLE)) THEN TRY_CAST({col} AS DOUBLE) END)'
        with self._data(aid,vid,src,where,params) as (db,base,args):
            result['rows_filtered']=int(db.execute('SELECT COUNT(*)'+base,args).fetchone()[0])
            if kind=='category':
                counts=db.execute(category_sql(f.source_name, base=base.strip(), limit=limit), args).fetchall()
                result['points']=[{'label':str(x),'count':int(n)} for x,n in counts]
                counted=sum(p['count'] for p in result['points'])
                non_null=int(db.execute(f'SELECT COUNT({col})'+base,args).fetchone()[0])
                if non_null>counted:result['points'].append({'label':'其他类别','count':non_null-counted,'other':True})
                result['non_null_count']=non_null
            elif kind in ('box','quantiles'):
                x=db.execute('''SELECT MIN(v),MAX(v),QUANTILE_CONT(v,0.1),QUANTILE_CONT(v,0.25),
                    MEDIAN(v),QUANTILE_CONT(v,0.75),QUANTILE_CONT(v,0.9),COUNT(v) FROM
                    (SELECT '''+v+' v'+base+') q',args).fetchone()
                keys=['min','max','p10','p25','p50','p75','p90','count']
                data={key:_number(value) for key,value in zip(keys,x)}
                if data.get('p25') is not None and data.get('p75') is not None:
                    q1,q3=data['p25'],data['p75']
                    low=q1-1.5*(q3-q1);high=q3+1.5*(q3-q1)
                    if math.isfinite(low) and math.isfinite(high):
                        lo_s,hi_s=format(low,'.17g'),format(high,'.17g')
                        bounds=db.execute('SELECT MIN(v) FILTER (WHERE v BETWEEN '+lo_s+' AND '+hi_s+
                            '),MAX(v) FILTER (WHERE v BETWEEN '+lo_s+' AND '+hi_s+
                            '),COUNT(*) FILTER (WHERE v IS NOT NULL AND (v<'+lo_s+' OR v>'+hi_s+')) '+
                            'FROM (SELECT '+v+' AS v'+base+') q',args).fetchone()
                        data.update(whisker_low=_number(bounds[0]),whisker_high=_number(bounds[1]),
                                    outlier_count=int(bounds[2]),whisker_method='Tukey 1.5 IQR')
                result.update(stats=data,points=[{'percentile':p,'value':data[k]} for p,k in
                    [(0,'min'),(10,'p10'),(25,'p25'),(50,'p50'),(75,'p75'),(90,'p90'),(100,'max')] if data[k] is not None])
            elif kind=='histogram':
                low,high,n=db.execute('SELECT MIN(v),MAX(v),COUNT(v) FROM (SELECT '+v+' v'+base+') q',args).fetchone()
                result['finite_count']=int(n)
                if n:
                    low=float(low);high=float(high)
                    if low==high:result['points']=[{'lower':low,'upper':high,'count':int(n)}]
                    else:
                        width=(high-low)/bins
                        if not math.isfinite(width) or width<=0:
                            raise ValueError('字段数值跨度过大，无法可靠划分直方图，请先筛选有限范围')
                        buckets=db.execute('''SELECT CAST(GREATEST(0,LEAST(?,FLOOR((v-?)/?))) AS INTEGER),COUNT(*)
                            FROM (SELECT '''+v+' v'+base+') q WHERE v IS NOT NULL GROUP BY 1 ORDER BY 1',
                            [bins-1,low,width,*args]).fetchall()
                        counts={int(i):int(c) for i,c in buckets}
                        result['points']=[{'lower':low+i*width,'upper':low+(i+1)*width,'count':counts.get(i,0)} for i in range(bins)]
                result['bin_count']=len(result['points'])
            elif kind=='scatter':
                y=quote_identifier(o.source_name)
                vy=f'TRY_CAST({y} AS DOUBLE)'
                rows=db.execute('''SELECT x,y FROM (SELECT '''+v+' x,'+vy+' y'+base+''') t
                    WHERE x IS NOT NULL AND y IS NOT NULL AND isfinite(y)
                    ORDER BY HASH(x::VARCHAR || ':' || y::VARCHAR) LIMIT 400''',args).fetchall()
                result['points']=[{'x':_number(x),'y':_number(y)} for x,y in rows]
                n=int(db.execute('''SELECT COUNT(*) FROM (SELECT '''+v+' x,'+vy+' y'+base+''') t
                    WHERE x IS NOT NULL AND y IS NOT NULL AND isfinite(y)''',args).fetchone()[0])
                result.update(pair_count=n,sampled=n>len(rows),sample_method='deterministic hash up to 400 pairs',
                              other_name=o.source_name)
            elif kind=='trend':
                t=quote_identifier(fields[con.event_time_field_id].source_name)
                results=db.execute(f'''SELECT DATE_TRUNC('{granularity}',{t}) period,
                    AVG({v}) mean, MIN({v}) minimum, MAX({v}) maximum, COUNT({v}) n'''+base+f'''
                    AND {t} IS NOT NULL GROUP BY 1 ORDER BY 1 LIMIT 161''',args).fetchall()
                result['truncated']=len(results)>160
                result['points']=[{'x':_number(p),'value':_number(a),'min':_number(lo),'max':_number(hi),'count':int(n)}
                                  for p,a,lo,hi,n in results[:160]]
                result['granularity']=granularity
        result['evidence']={'source_sha256':src['sha256'],'contract_revision':con.revision,
                            'scope_sha256':result['scope_sha256'],'sql_engine':'DuckDB'}
        return result
