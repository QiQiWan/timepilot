"""v0.33.1 real, source-pinned feature charts for the guided workbench.

Every value comes from FeatureCompiler's executable DuckDB view. No model-generated
SQL or synthetic plot data. The chart endpoint never mutates source datasets,
FeatureSets or running jobs; it rejects stale contract/source references.
"""
from __future__ import annotations

import math

from timepilot.feature_compiler import FeatureCompiler, NUMERIC
from timepilot.feature_contracts import FeatureSpecV2
from timepilot.feature_visualization import summarize_preview
from timepilot.feature_chart_semantics import series_aggregate
from timepilot.feature_chart_insights import explain_chart, feature_unit
from timepilot.native_bi import quote_identifier, safe_json

MAX_CHART_ROWS=2_000_000
MAX_POINTS=120


def _is_number(value):
    return isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(float(value))


def _row_objects(cursor):
    names=[x[0] for x in cursor.description]
    return [dict(zip(names,safe_json(row))) for row in cursor.fetchall()]


def _safe_field(field):
    return field is not None and field.confirmation=='confirmed' and field.classification!='restricted'


def _clean_label(value):
    return str(value)[:120] if value is not None else '(缺失)'


class FeatureWorkbenchCharts:
    def __init__(self,auto,candidates,semantic,native):
        self.auto=auto
        self.candidates=candidates
        self.semantic=semantic
        self.native=native

    def candidate_chart(self,pid,sid,cid,mode='auto', *, entity_field_id=None, entity_value=None, start=None, end=None, resolution=120):
        state=self.auto.get(pid,sid)  # owner/project + original source validated
        if state.get('stale'):
            raise ValueError('来源或契约已变化；需要重新分析，不能使用旧图表')
        if mode not in ('auto','distribution','series','category','group'):
            raise ValueError('不支持的特征图表类型')
        if not state.get('candidate_session_id') or not state.get('report'):
            raise ValueError('候选尚未完成实际计算')
        cand_session=self.candidates.get(pid,state['candidate_session_id'])
        if cand_session['stale'] or cand_session['asset_id']!=state['asset_id'] or cand_session['version_id']!=state['version_id']:
            raise ValueError('候选来源已经变化')
        feature=next((c for c in cand_session['candidates'] if c['candidate_id']==cid),None)
        if feature is None:
            raise KeyError('候选特征不存在')
        rating=next((r for r in state['report']['quality_rows'] if r['candidate_id']==cid),None)
        if rating is None or rating.get('status')=='unverified':
            raise ValueError('当前候选缺少已验证的计算质量')
        src=self.semantic.source(pid,state['asset_id'],state['version_id'])
        contract=self.semantic.latest(pid,state['asset_id'],state['version_id'],source=src)
        if src['sha256']!=state['source_sha256'] or contract.revision!=state['contract_revision']:
            raise ValueError('数据或契约与候选结果不一致')
        total=int(src.get('row_count') or 0)
        if total>MAX_CHART_ROWS:
            raise ValueError('数据超过图表计算预算，请先分区或在高级工具中设置计算范围')
        by_id={f.field_id:f for f in contract.fields}
        numeric_source=next((by_id[x] for x in feature['input_field_ids']
                             if x in by_id and NUMERIC.match(by_id[x].physical_type)),None)
        categories=[f for f in contract.fields if _safe_field(f) and
                    f.role not in ('label','entity_key','batch_key','event_time','available_at',
                                   'prediction_time','record_id','order_index') and
                    f.semantic_type in ('category','boolean')]
        entities=[by_id[x] for x in contract.entity_field_ids
                  if x in by_id and _safe_field(by_id[x])]
        topology=state.get('topology') or {}
        order=by_id.get(topology.get('order_field_id'))
        if not _safe_field(order):order=None
        available=['distribution']
        if order and topology.get('safe_for_windows'):available.append('series')
        if categories:available.append('category')
        if entities:available.append('group')
        if mode=='auto':
            if feature['operator_id']=='category.is_value' and categories:mode='category'
            elif order and feature['operator_id'].startswith(('rolling.','lag.','difference.','relative.')):mode='series'
            else:mode='distribution'
        if mode not in available:
            return {'schema_version':1,'status':'not_applicable','mode':mode,
                'available_modes':available,'message':'未找到已确认的相关字段；不能生成此类对照图',
                'source_sha256':src['sha256'],'contract_revision':contract.revision,
                'version_id':state['version_id'],'candidate_fingerprint':feature['fingerprint'],
                'feature_name':feature['display_name'],'candidate_id':cid}
        spec=FeatureSpecV2(name='feature_workbench_chart',source={
            'asset_id':state['asset_id'],'version_id':state['version_id'],'source_sha256':src['sha256']},
            contract_revision=contract.revision,features=self.candidates.definitions(cand_session,[cid]))
        compiler=FeatureCompiler(contract,spec)
        name=quote_identifier(feature['output_name'])
        numeric=f'TRY_CAST({name} AS DOUBLE)'
        finite=f'(CASE WHEN ISFINITE({numeric}) THEN {numeric} ELSE NULL END)'
        payload={'schema_version':1,'status':'ready','mode':mode,'available_modes':available,
            'source_sha256':src['sha256'],'version_id':state['version_id'],
            'contract_revision':contract.revision,'candidate_id':cid,
            'candidate_fingerprint':feature['fingerprint'],'feature_name':feature['display_name'],
            'operator_id':feature['operator_id'],'inputs':[by_id[x].source_name for x in feature['input_field_ids']],
            'scope':'full_source_aggregate' if mode!='series' else 'bounded_ordered_preview',
            'population_rows':total,'predictive_value_evaluated':False}
        with self.native._connection() as con:
            self.native._install_effective_view(con,state['asset_id'],state['version_id'],src['columns'])
            table=compiler.compile_views(con)
            evidence=summarize_preview(con,table,[feature['output_name']],sample_count=0)[0]
            payload['quality']=evidence
            if mode=='distribution':
                lo=evidence.get('min');hi=evidence.get('max')
                if lo is None or hi is None:
                    payload['points']=[]
                elif lo==hi:
                    payload['points']=[{'label':str(round(lo,5)),'count':evidence['finite_count']}]
                else:
                    # Aggregate over the full computed view, not the limited chart sample.
                    lo=float(lo);hi=float(hi);span=hi-lo
                    expr=f'LEAST(19, GREATEST(0, CAST(FLOOR(({finite}-?)/?*20) AS INTEGER)))'
                    cursor=con.execute(f'''SELECT {expr} AS b,COUNT(*) AS n FROM {table}
                        WHERE {finite} IS NOT NULL GROUP BY b ORDER BY b''',[lo,span])
                    counts={int(idx):int(n) for idx,n in cursor.fetchall()}
                    payload['points']=[{'label':f'{lo+span*i/20:.4g}–{lo+span*(i+1)/20:.4g}',
                                        'count':counts.get(i,0)} for i in range(20)]
            elif mode in ('category','group'):
                dimension=(categories if mode=='category' else entities)[0]
                col=quote_identifier(dimension.source_name)
                cursor=con.execute(f'''SELECT CAST({col} AS VARCHAR) AS label,
                    COUNT(*) AS row_count,COUNT({finite}) AS finite_count,
                    AVG({finite}) AS mean_value FROM {table}
                    GROUP BY label ORDER BY row_count DESC,label LIMIT 12''')
                payload['dimension']={'field_id':dimension.field_id,'name':dimension.source_name}
                payload['points']=[{'label':_clean_label(row['label']),
                                    'count':row['row_count'],'valid_count':row['finite_count'],
                                    'mean':row['mean_value']} for row in _row_objects(cursor)]
            else:
                entity_field=next((f for f in entities if f.field_id==entity_field_id),None) if entity_field_id else (entities[0] if entities else None)
                if entity_field_id is not None and entity_field is None:
                    raise ValueError('设备分组字段未经确认或无权访问')
                raw=f'TRY_CAST({quote_identifier(numeric_source.source_name)} AS DOUBLE)' if numeric_source else 'NULL'
                raw_safe=f'(CASE WHEN ISFINITE({raw}) THEN {raw} ELSE NULL END)' if numeric_source else 'NULL'
                payload.update(series_aggregate(con,table,
                    order_column=order.source_name,value_expression=finite,
                    original_expression=raw_safe,
                    entity_column=entity_field.source_name if entity_field else None,
                    entity_field_id=entity_field.field_id if entity_field else None,
                    entity_value=entity_value,start=start,end=end,resolution=resolution))
                payload['entity_field_options']=[{'value':f.field_id,'label':f.source_name} for f in entities]
                payload['order_field']=order.source_name
                payload['sample_rows']=len(payload['points'])
        source_unit=numeric_source.unit if numeric_source is not None else None
        output_unit,overlay=feature_unit(feature['operator_id'],source_unit)
        payload['feature_unit']=output_unit
        payload['source_unit']=source_unit
        payload['axis_compatible']=overlay
        payload['explanation']=explain_chart(payload,unit=output_unit,
            original_unit=source_unit,axis_compatible=overlay if mode=='series' else None)
        from timepilot.feature_evidence_v0373 import chart_explanations
        payload['explanation']['evidence_analysis']=chart_explanations(payload)
        return payload
