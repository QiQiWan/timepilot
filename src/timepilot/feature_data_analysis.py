"""Standard, source-pinned data understanding before guided feature engineering.

This module reuses FeatureLab's durable profile jobs, contract and DuckDB view.
Read APIs never create work; the publication worker or an explicit UI action
queues one idempotent profile job. No model API is required.
"""
from __future__ import annotations

import hashlib
import math
from timepilot.feature_compiler import NUMERIC
from timepilot.feature_chart_semantics import series_aggregate
from timepilot.feature_chart_insights import explain_chart
from timepilot.native_bi import quote_identifier
from timepilot.feature_dashboard_sql import category_sql


def _findings(profile):
    fields=profile.get('fields') or []
    warnings=list(profile.get('warnings') or [])
    missing=[x for x in fields if x.get('null_rate') is not None and x['null_rate']>=0.1]
    missing.sort(key=lambda x:-x['null_rate'])
    findings=[{'kind':'missing','field_id':x['field_id'],
      'summary':f"{x['source_name']} 缺失率 {100*x['null_rate']:.1f}%"} for x in missing[:6]]
    if not (profile.get('topology') or {}).get('safe_for_windows'):
        findings.append({'kind':'time','summary':'尚未具备安全时序窗口条件；会优先推荐逐行特征'})
    for note in warnings[:5]:findings.append({'kind':'notice','summary':str(note)[:240]})
    return findings


def _recommendations(profile,contract):
    numeric=[f for f in contract.fields if NUMERIC.match(f.physical_type) and
             f.role not in ('label','entity_key','batch_key','record_id','order_index','event_time','available_at', 'prediction_time')
             and f.classification!='restricted']
    result=[{'code':'inspect_quality','title':'检查数据质量','reason':'先检查缺失、无效值和异常字段',
             'action':'view_quality'}]
    if numeric:
        result.append({'code':'basic_features','title':'构造数值特征',
          'reason':f'检测到 {len(numeric)} 个可参与基础特征探索的数值字段；候选需再验证计算质量',
          'action':'feature_engineering'})
    if (profile.get('topology') or {}).get('safe_for_windows'):
        result.append({'code':'temporal_features','title':'分析历史变化',
          'reason':'设备或时间排序检查通过，可以评估滚动统计和变化率',
          'action':'feature_engineering'})
    else:
        result.append({'code':'confirm_time','title':'核实时间与分组字段',
          'reason':'未完成时序结构核实，暂不自动生成历史窗口特征',
          'action':'feature_contract'})
    if any(f.role=='label' and f.confirmation=='confirmed' for f in contract.fields):
        result.append({'code':'evaluate','title':'准备模型效果对照',
          'reason':'已存在确认标签；需选择适合的独立验证协议才能评价预测收益',
          'action':'feature_engineering'})
    return result


class FeatureDataAnalysis:
    def __init__(self,projects,semantic,jobs,native):
        self.projects=projects
        self.semantic=semantic
        self.jobs=jobs
        self.native=native

    def _source(self,pid,aid,vid):
        meta=self.semantic.source(pid,aid,vid)
        if self.native.verify(aid).get('valid') is not True:
            raise ValueError('源文件哈希不匹配，禁止继续分析旧版本')
        contract=self.semantic.latest(pid,aid,vid,source=meta)
        if contract.state=='stale':raise ValueError('字段契约与数据来源不一致')
        return meta,contract

    @staticmethod
    def _request_id(aid,vid,src,revision):
        source=f'eda-v1:{aid}:{vid}:{src}:{revision}'
        return 'eda-'+hashlib.sha256(source.encode()).hexdigest()[:64]

    def start(self,pid,aid,vid='base'):
        self.jobs._editor(pid)
        src,con=self._source(pid,aid,vid)
        n=int(src.get('row_count') or 0)
        if n>2_000_000 or len(con.fields)>256:
            raise ValueError('数据超出自动画像预算，请先确认分区或高级计算范围')
        history=self.jobs.recent_profiles(pid,aid,vid,limit=15)
        for prof in history:
            if prof['source_sha256']==src['sha256'] and prof['contract_revision']==con.revision:
                return self.status(pid,aid,vid)
        jid=self._request_id(aid,vid,src['sha256'],con.revision)
        job=self.jobs.create_profile(pid,aid,vid,jid)
        return {'status':'queued' if job['status']=='queued' else job['status'],
             'job_id':job['job_id'],'asset_id':aid,'version_id':vid,
             'source_sha256':src['sha256'],'contract_revision':con.revision}

    def status(self,pid,aid,vid='base'):
        src,con=self._source(pid,aid,vid)
        result={'status':'not_started','asset_id':aid,'version_id':vid,
          'source_sha256':src['sha256'],'contract_revision':con.revision,
          'row_count':int(src.get('row_count') or 0),'column_count':len(con.fields),
          'profile':None,'findings':[],'recommendations':[],
          'charts':{},'job':None}
        for prof in self.jobs.recent_profiles(pid,aid,vid,limit=30):
            if prof['source_sha256']==src['sha256'] and prof['contract_revision']==con.revision:
                data=self.jobs.get_profile(pid,prof['profile_id'])
                safe={f.field_id for f in con.fields if f.classification!='restricted'}
                data={**data,'fields':[f for f in data.get('fields',[]) if f['field_id'] in safe]}
                if any(f.classification=='restricted' for f in con.fields):
                    # Diagnostics may contain restricted field IDs or device
                    # names even if the per-field table has been filtered.
                    data['temporal_diagnostics']={'status':'redacted'}
                    data['topology']={'safe_for_windows':False,'reasons':['受限字段的分组详情已隐藏']}
                    data['warnings']=['当前数据包含受限字段，相关诊断细节已隐藏']
                result.update(status='ready',profile=data,findings=_findings(data),
                    recommendations=_recommendations(data,con),
                    charts={'missing':[{'label':f['source_name'],
                           'value':round(100*float(f['null_rate']),3)}
                           for f in data['fields'] if f.get('null_rate') is not None][:50]})
                return result
        request=self._request_id(aid,vid,src['sha256'],con.revision)
        with self.projects._db() as db:
            row=db.execute('''SELECT job_id FROM feature_jobs WHERE project_id=? AND kind='profile'
                AND request_id=? ORDER BY created_at DESC LIMIT 1''',(pid,request)).fetchone()
        if row:
            job=self.jobs.get_job(pid,row['job_id'])
            result.update(status=job['status'],job={'job_id':job['job_id'],
                'status':job['status'],'error':job.get('error_text')})
        return result

    def dashboard(self,pid,aid,vid='base',**kwargs):
        from timepilot.feature_dashboard_v0372 import DataOverviewDashboard
        return DataOverviewDashboard(self).overview(pid,aid,vid,**kwargs)

    def dashboard_chart(self,pid,aid,vid='base',**kwargs):
        from timepilot.feature_dashboard_v0372 import DataOverviewDashboard
        return DataOverviewDashboard(self).chart(pid,aid,vid,**kwargs)

    def chart(self,pid,aid,vid,field_id,mode='distribution', *, entity_field_id=None, entity_value=None, start=None, end=None, resolution=120):
        src,con=self._source(pid,aid,vid)
        field=next((f for f in con.fields if f.field_id==field_id),None)
        if field is None or field.classification=='restricted':
            raise ValueError('字段不存在或无权显示')
        if mode not in ('distribution','category','series'):
            raise ValueError('不支持的图表类型')
        if int(src.get('row_count') or 0)>2_000_000:
            raise ValueError('图表扫描超出预算，请先选择分区')
        col=quote_identifier(field.source_name)
        result={'mode':mode,'field_id':field_id,'field_name':field.source_name,
                'asset_id':aid,'version_id':vid,'source_sha256':src['sha256'],
                'contract_revision':con.revision,'scope':'full' if mode!='series' else 'full_selected_range_aggregate',
                'points':[]}
        with self.native._connection() as db:
            self.native._install_effective_view(db,aid,vid,src['columns'])
            if mode=='category':
                rows=db.execute(category_sql(field.source_name, base='FROM __tp_effective WHERE TRUE', limit=15)).fetchall()
                result['points']=[{'label':str(x)[:100],'count':int(n)} for x,n in rows]
            else:
                if not NUMERIC.match(field.physical_type):
                    raise ValueError('数值直方图和折线图需要数值字段')
                safe=f'(CASE WHEN isfinite(TRY_CAST({col} AS DOUBLE)) THEN TRY_CAST({col} AS DOUBLE) END)'
                if mode=='series':
                    time_field=next((f for f in con.fields if f.field_id==con.event_time_field_id
                                    and f.confirmation=='confirmed'),None)
                    if time_field is None:raise ValueError('需要先确认时间字段')
                    time_col=quote_identifier(time_field.source_name)
                    entity_fields=[f for f in con.fields if f.field_id in con.entity_field_ids
                                   and f.confirmation=='confirmed' and f.classification!='restricted']
                    if entity_field_id is not None and entity_field_id not in [f.field_id for f in entity_fields]:
                        raise ValueError('设备分组字段未经确认或无权访问')
                    entity_field=next((f for f in entity_fields if f.field_id==entity_field_id),None) if entity_field_id else (entity_fields[0] if entity_fields else None)
                    result.update(series_aggregate(db,'__tp_effective',
                        order_column=time_field.source_name,value_expression=safe,
                        entity_column=entity_field.source_name if entity_field else None,
                        entity_field_id=entity_field.field_id if entity_field else None,
                        entity_value=entity_value,start=start,end=end,resolution=resolution))
                    result['entity_field_options']=[{'value':f.field_id,'label':f.source_name} for f in entity_fields]
                    result['order_field']=time_field.source_name
                    result['population_rows']=int(src.get('row_count') or 0)
                else:
                    value=db.execute(f'SELECT MIN({safe}),MAX({safe}),COUNT({safe}) FROM __tp_effective').fetchone()
                    low,high,n=value
                    if n and low is not None:
                        if high==low:
                            result['points']=[{'label':format(low,'.4g'),'count':int(n)}]
                        else:
                            span=float(high)-float(low)
                            rows=db.execute(f'''SELECT CAST(GREATEST(0,LEAST(11,FLOOR((v-?)/?*12))) AS INT) bucket,COUNT(*) n
                                FROM (SELECT {safe} v FROM __tp_effective) t
                                WHERE v IS NOT NULL GROUP BY 1 ORDER BY 1''', [float(low),span]).fetchall()
                            buckets={int(i):int(count) for i,count in rows}
                            result['points']=[{'label':f'{low+span*i/12:.4g}–{low+span*(i+1)/12:.4g}',
                                'count':buckets.get(i,0)} for i in range(12)]
                    result['finite_count']=int(n or 0)
        result['explanation']=explain_chart(result,unit=field.unit)
        return result
