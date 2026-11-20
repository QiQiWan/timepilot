"""Full-scope, source-pinned DuckDB profiles; expensive statistics are separate."""
from __future__ import annotations
from timepilot.native_bi import quote_identifier
from timepilot.feature_compiler import NUMERIC
from timepilot.feature_contracts import DataContract, digest
from timepilot.projects import now


class FeatureProfiler:
    def __init__(self,semantic,native):
        self.semantic=semantic
        self.native=native

    def compute(self,pid,aid,vid='base',contract=None):
        contract=contract or self.semantic.latest(pid,aid,vid)
        meta=self.semantic.source(pid,aid,vid)
        if contract.source_sha256!=meta['sha256']:raise ValueError('源版本改变，拒绝生成旧画像')
        profile={'schema_version':1,'asset_id':aid,'version_id':vid,
                 'source_sha256':meta['sha256'],'contract_revision':contract.revision,
                 'computed_at':now(),'scope':'full','rows_scanned':0,
                 'fields':[],'time':{'status':'unconfirmed'},'warnings':[]}
        with self.native._connection() as con:
            self.native._install_effective_view(con,aid,vid,meta['columns'])
            total=con.execute('SELECT COUNT(*) FROM __tp_effective').fetchone()[0]
            profile['rows_scanned']=int(total)
            profile['rows_total']=int(total)
            for field in contract.fields:
                col=quote_identifier(field.source_name)
                nulls=con.execute(f'SELECT COUNT(*)-COUNT({col}) FROM __tp_effective').fetchone()[0]
                stats={'field_id':field.field_id,'source_name':field.source_name,
                       'physical_type':field.physical_type,'null_count':int(nulls),
                       'null_rate':(nulls/total if total else None),'scope':'full',
                       'rows_scanned':int(total)}
                if NUMERIC.match(field.physical_type):
                    safe=f'CASE WHEN isfinite(TRY_CAST({col} AS DOUBLE)) THEN TRY_CAST({col} AS DOUBLE) END'
                    row=con.execute(f'''SELECT MIN({safe}),MAX({safe}),AVG({safe}),
                     STDDEV_SAMP({safe}),COUNT(DISTINCT {col}),
                     COUNT(*)-COUNT({safe}) FROM __tp_effective''').fetchone()
                    from timepilot.native_bi import safe_json
                    stats.update(dict(zip(('min','max','mean','std','distinct_count','invalid_numeric_count'),safe_json(row))))
                    # Extended unsupervised evidence supports adaptive feature
                    # planning. Keep every statistic tied to the full source
                    # and make unavailable aggregations explicit, not invented.
                    try:
                        ext=con.execute(f'''SELECT
                            SUM(CASE WHEN {safe}=0 THEN 1 ELSE 0 END),
                            COUNT({safe}),
                            APPROX_QUANTILE({safe}, 0.1),
                            APPROX_QUANTILE({safe}, 0.5),
                            APPROX_QUANTILE({safe}, 0.9)
                            FROM __tp_effective''').fetchone()
                        nzero,nvalid,p10,p50,p90=safe_json(ext)
                        stats.update({'zero_count':int(nzero or 0),
                            'zero_rate':(nzero/nvalid if nvalid else None),
                            'p10':p10,'median':p50,'p90':p90,
                            'extended_evidence':'full_source'})
                    except Exception:
                        stats['extended_evidence']='unavailable'

                if (field.semantic_type=='category' and field.confirmation=='confirmed'
                    and field.role in ('unspecified','value','regime') and field.classification!='restricted'):
                    rows=con.execute(f'''SELECT CAST({col} AS VARCHAR), COUNT(*) AS n
                        FROM __tp_effective WHERE {col} IS NOT NULL
                        GROUP BY 1 ORDER BY n DESC, 1 LIMIT 8''').fetchall()
                    stats['top_categories']=[{'value':str(v)[:120],'count':int(n)}
                        for v,n in rows if v is not None and len(str(v))<=120]
                profile['fields'].append(stats)
            if contract.event_time_field_id:
                field=next(f for f in contract.fields if f.field_id==contract.event_time_field_id)
                col=quote_identifier(field.source_name)
                row=con.execute(f'SELECT MIN({col}),MAX({col}),COUNT(DISTINCT {col}) FROM __tp_effective').fetchone()
                profile['time']={'status':'verified','timezone':contract.timezone,
                                 'min':str(row[0]) if row[0] is not None else None,
                                 'max':str(row[1]) if row[1] is not None else None,
                                 'distinct_times':int(row[2])}
            else:
                profile['warnings'].append('没有已核实的真实日历时间；仅在确认序号且设备分组完成检查后可计算记录窗口')
            from timepilot.feature_temporal_profile import temporal_diagnostics
            profile['temporal_diagnostics']=temporal_diagnostics(con,contract)
            from timepilot.feature_topology import infer_topology
            profile['topology']=infer_topology(contract,profile['temporal_diagnostics'])
            if not profile['topology']['safe_for_windows']:
                profile['warnings'].append('缺少安全窗口排序/设备分组证据，自动分析仅建议逐行特征')
        profile['query_fingerprint']=digest({'source_sha':meta['sha256'],'contract_revision':contract.revision,
                                            'profiler_version':'0.34.1','scope':'full'})
        return profile
