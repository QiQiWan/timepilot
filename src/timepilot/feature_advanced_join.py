"""v0.30.3 / v0.31: reviewed advanced features -> immutable PIT join -> model comparison.

Two separate safeguards apply: the generator has already checked every source
measurement's event/availability cutoff; here its per-instance cutoff is
rechecked against the *actual* supervised decision row. No label is ever read
from an advanced candidate as a source feature.
"""
from __future__ import annotations
import json
import os
import re
from pathlib import Path
from uuid import uuid4
from pydantic import BaseModel, ConfigDict, Field
import pandas as pd
import numpy as np
from timepilot.feature_advanced import _parse_utc
from timepilot.feature_contracts import (TaskSpecV2,EvaluationProtocol,QualitySelectionPolicy,
  AutoSearchPolicy,SlicePolicy,canonical,digest)
from timepilot.projects import now
from timepilot.storage import sha256_file
from timepilot.native_bi import quote_identifier

class Strict(BaseModel):
    model_config=ConfigDict(extra='forbid')

class AdvancedJoinPlan(Strict):
    advanced_job_id: str=Field(min_length=1,max_length=128)
    decisions_asset_id: str=Field(min_length=1,max_length=128)
    decisions_version_id: str='base'
    decision_key_field_id: str=Field(min_length=1,max_length=128)
    prediction_time_field_id: str=Field(min_length=1,max_length=128)
    selected_columns: list[str]=Field(min_length=1,max_length=60)
    max_rows: int=Field(default=30000,ge=100,le=100000)
    max_memory_mb: int=Field(default=512,ge=128,le=2048)

class AdvancedEvaluationRequest(Strict):
    join_job_id:str=Field(min_length=1,max_length=128)
    task:TaskSpecV2
    models:list[str]=Field(default_factory=lambda:['linear','forest'],min_length=1,max_length=2)
    slice_field_ids:list[str]=Field(default_factory=list,max_length=3)
    request_id:str=Field(min_length=1,max_length=120)
    min_relative_improvement:float=Field(default=.01,ge=0,le=.25)


def pit_join(decisions:pd.DataFrame,candidates:pd.DataFrame,*,decision_key:str,
             prediction_time:str,candidate_key:str,chosen:list[str],timezone:str='UTC'):
    """One-to-one join; no future/PIT mismatch accepted, no silent row expansion."""
    needed_d={decision_key,prediction_time};needed_c={candidate_key,'__candidate_cutoff_utc',*chosen}
    if not needed_d.issubset(decisions.columns) or not needed_c.issubset(candidates.columns):
        raise ValueError('预测实例或高级候选缺少指定字段')
    if decision_key==prediction_time or decision_key in chosen:
        raise ValueError('实例键或预测时点不能作为高级特征')
    if len(chosen)!=len(set(chosen)) or not chosen:
        raise ValueError('选择的高级特征列必须唯一且非空')
    if '__join_key' in decisions.columns or '__join_key' in candidates.columns:
        raise ValueError('数据列与系统内部关联键冲突')
    if decisions[decision_key].isna().any() or candidates[candidate_key].isna().any():
        raise ValueError('存在空预测实例主键')
    if decisions[decision_key].astype(str).duplicated().any() or candidates[candidate_key].astype(str).duplicated().any():
        raise ValueError('预测实例或候选主键在字符串规范化后重复')
    left=decisions.copy()
    right=candidates[[candidate_key,'__candidate_cutoff_utc',*chosen]].copy()
    left['__join_key']=left[decision_key].astype(str)
    right['__join_key']=right[candidate_key].astype(str)
    cutoff=_parse_utc(left[prediction_time],prediction_time,timezone)
    right['__verified_candidate_cutoff']=_parse_utc(right['__candidate_cutoff_utc'],'__candidate_cutoff_utc','UTC')
    if any(col in left for col in ('__candidate_cutoff_utc','__verified_candidate_cutoff')):
        raise ValueError('源字段与保留的 PIT 证据列冲突')
    for col in chosen:
        if col.startswith('__') or col in left.columns:
            raise ValueError('禁止覆写原始字段或选择内部时间/主键字段')
        if not pd.api.types.is_numeric_dtype(right[col]):
            raise ValueError('高级候选非数值列不能作为模型特征：'+col)
        if np.isinf(pd.to_numeric(right[col],errors='coerce').to_numpy(dtype=float)).any():
            raise ValueError('高级候选包含无穷值：'+col)
    # Missing candidate rows are not silently imputed; the operator must decide
    # whether to regenerate a candidate set for the same prediction instances.
    joined=left.merge(right.drop(columns=[candidate_key,'__candidate_cutoff_utc']),
                     on='__join_key',how='left',validate='one_to_one',sort=False,indicator=True)
    if len(joined)!=len(left) or (joined['_merge']!='both').any():
        raise ValueError('高级候选与预测实例不完全匹配；拒绝隐式剔除或补齐')
    right_cutoff=joined['__verified_candidate_cutoff'].reset_index(drop=True)
    if (right_cutoff!=cutoff.reset_index(drop=True)).any():
        raise ValueError('高级候选的 PIT 截止时间与真实预测时点不一致，拒绝合并')
    renamed={col:f'adv_{i:02d}_{digest(col)[:8]}' for i,col in enumerate(chosen,1)}
    final=joined.drop(columns=['__join_key','__verified_candidate_cutoff','_merge']).rename(columns=renamed)
    if final.columns.duplicated().any():raise ValueError('合并后字段名重复')
    return final,{'rows':len(final),'advanced_names':renamed,
        'pit':'generator_event_and_availability_then_exact_prediction_cutoff',
        'decision_keys_sha256':digest(left[decision_key].astype(str).tolist())}

class AdvancedJoinService:
    def __init__(self,projects,native,semantic,registry,jobs):
        self.projects=projects;self.native=native;self.semantic=semantic
        self.registry=registry;self.jobs=jobs

    def plan(self,pid,plan:AdvancedJoinPlan):
        self.registry._editor(pid)
        from timepilot.feature_advanced_service import AdvancedService
        source=AdvancedService(self.projects,self.native,self.semantic,self.registry,self.jobs).result(pid,plan.advanced_job_id)
        if source.get('approval')!='review_required' or source.get('published'):
            raise ValueError('高级候选状态不允许直接发布')
        self.projects.require_native(pid,plan.decisions_asset_id)
        origin=self.semantic.source(pid,plan.decisions_asset_id,plan.decisions_version_id)
        if not self.native.verify(plan.decisions_asset_id).get('valid'):
            raise ValueError('预测表来源 SHA256 无法核验')
        contract=self.semantic.latest(pid,plan.decisions_asset_id,plan.decisions_version_id,source=origin)
        if contract.state!='confirmed':raise ValueError('预测表必须先确认数据契约')
        by_id={f.field_id:f for f in contract.fields}
        key=by_id.get(plan.decision_key_field_id)
        cutoff=by_id.get(plan.prediction_time_field_id)
        if not key or key.confirmation!='confirmed' or key.role not in ('record_id','batch_key'):
            raise ValueError('预测实例主键须为已确认的 record_id/batch_key')
        if not cutoff or cutoff.confirmation!='confirmed' or cutoff.role!='prediction_time' or cutoff.semantic_type!='datetime':
            raise ValueError('预测时点须为已确认的真实时间字段')
        if key.source_name==cutoff.source_name:raise ValueError('主键与预测时点不能相同')
        allowed=[name for name in source['columns'] if name not in
                 ('__sample_id','__decision_id','__candidate_cutoff_utc')]
        if not set(plan.selected_columns).issubset(set(allowed)) or len(set(plan.selected_columns))!=len(plan.selected_columns):
            raise ValueError('高级候选列不属于该计算产物，或包含内部/重复列')
        if len(plan.selected_columns)>60 or len(plan.selected_columns)>plan.max_rows:
            raise ValueError('高级候选数量超预算')
        if int(origin['row_count'])>plan.max_rows:
            raise ValueError('预测数据行数超过当前资源上限')
        if source['engine']=='featuretools' and source['sources']['primary']['asset_id']!=plan.decisions_asset_id:
            raise ValueError('Featuretools 候选必须绑定同一预测实例资产')
        return {'status':'review_required','candidate_job_id':plan.advanced_job_id,
            'candidate_asset_id':source['native_asset_id'],'candidate_sha256':source['sha256'],
            'candidate_source_fingerprint':source['source_fingerprint'],
            'decision_asset_id':plan.decisions_asset_id,'decision_sha256':origin['sha256'],
            'decision_contract_revision':contract.revision,
            'decision_key_name':key.source_name,'prediction_time_name':cutoff.source_name,
            'decision_timezone':contract.timezone,'selected_columns':plan.selected_columns,
            'candidate_key_name':'__sample_id' if source['engine']=='tsfresh' else '__decision_id',
            'limit':{'rows':plan.max_rows,'memory_mb':plan.max_memory_mb},
            'plan_sha256':digest(plan),'requires_explicit_confirmation':True}

    def submit(self,pid,plan:AdvancedJoinPlan,request_id:str,*,confirmed=False):
        if not confirmed:raise ValueError('高级候选回接模型必须由用户明确审核确认')
        checked=self.plan(pid,plan)
        return self.jobs._create_job(pid,'advanced_join',plan.advanced_job_id,request_id,{
            'plan':plan.model_dump(mode='json'),'pins':checked})

    def _frame(self,pid,aid,vid,cap_rows,cap_mb):
        self.projects.require_native(pid,aid)
        src=self.semantic.source(pid,aid,vid)
        if int(src['row_count'])>cap_rows:
            raise ValueError('关联表超过任务行数预算')
        # No arbitrary SQL/paths are accepted; effective NativeBI view is generated by host.
        approx=int(src['row_count'])*len(src['columns'])*64
        if approx*3>cap_mb*1024**2:raise ValueError('读取前预估内存超过限制')
        with self.native._connection() as con:
            self.native._install_effective_view(con,aid,vid,src['columns'])
            result=con.execute('SELECT * FROM __tp_effective').df()
        if result.memory_usage(index=True,deep=True).sum()*3>cap_mb*1024**2:
            raise ValueError('实际读入内存超预算')
        return result

    def run_worker(self,row,payload):
        pid=row['project_id']
        plan=AdvancedJoinPlan.model_validate(payload['plan'])
        expected=payload['pins'];current=self.plan(pid,plan)
        if current!=expected:raise ValueError('高级特征关联计划或数据指纹已经变化')
        from timepilot.feature_advanced_service import AdvancedService
        original=AdvancedService(self.projects,self.native,self.semantic,self.registry,self.jobs).result(pid,plan.advanced_job_id)
        if original['sha256']!=expected['candidate_sha256']:
            raise ValueError('高级候选的物化哈希失效')
        maxrows=expected['limit']['rows'];maxmb=expected['limit']['memory_mb']
        # Native copy of generated Parquet is itself SHA-pinned.
        if not self.native.verify(original['native_asset_id']).get('valid'):
            raise ValueError('高级候选 Parquet 资产损坏')
        decisions=self._frame(pid,plan.decisions_asset_id,plan.decisions_version_id,maxrows,maxmb)
        candidate=self._frame(pid,original['native_asset_id'],'base',maxrows,maxmb)
        data,proof=pit_join(decisions,candidate,decision_key=expected['decision_key_name'],
            prediction_time=expected['prediction_time_name'],
            candidate_key=expected['candidate_key_name'],chosen=plan.selected_columns,
            timezone=expected['decision_timezone'])
        outdir=self.jobs.root/pid/'advanced_join'/row['job_id']
        outdir.mkdir(parents=True,exist_ok=True)
        building=outdir/'data.building.parquet';final=outdir/'data.parquet'
        try:
            data.to_parquet(building,index=False)
            sha=sha256_file(building)
            with self.projects._db() as db:
                lease=db.execute('SELECT lease_owner,status,lease_until FROM feature_jobs WHERE job_id=?',
                                 (row['job_id'],)).fetchone()
            if not lease or lease['lease_owner']!=self.jobs.worker_id or lease['status']!='running' or lease['lease_until']<now():
                raise ValueError('计算租约失效，拒绝提交高级合并产物')
            os.replace(building,final)
            native=self.native.import_from_path(final,f'模型输入 · 高级 PIT 特征 · {row["job_id"][:8]}',
                {'type':'reviewed_advanced_join','project_id':pid,'job_id':row['job_id'],
                 'advanced_source_sha256':expected['candidate_sha256'],
                 'decision_source_sha256':expected['decision_sha256'],
                 'joined_sha256':sha,'pit':proof['pit']})
            link=self.projects.require_native(pid,plan.decisions_asset_id)
            self.projects.attach_native(pid,native['asset_id'],link['source_asset_id'])
            result={'job_id':row['job_id'],'project_id':pid,'decision_asset_id':plan.decisions_asset_id,'decision_version_id':plan.decisions_version_id,
                'decision_sha256':expected['decision_sha256'],
                'advanced_candidate_sha256':expected['candidate_sha256'],
                'joined_sha256':sha,'rows':len(data),'native_asset_id':native['asset_id'],
                'output_columns':proof['advanced_names'],'candidate_job_id':plan.advanced_job_id,
                'cutoff_field':expected['prediction_time_name'],'pit':proof['pit'],
                'decision_contract_revision':expected['decision_contract_revision'],
                'status':'draft_model_input_requires_review'}
            with self.projects._lock,self.projects._db() as db:
                db.execute('''INSERT INTO feature_advanced_joins
                  (join_id,project_id,owner_id,advanced_job_id,decision_asset_id,
                   plan_sha256,joined_sha256,native_asset_id,result_json,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?)''',
                   (row['job_id'],pid,row['owner_id'],plan.advanced_job_id,
                    plan.decisions_asset_id,expected['plan_sha256'],sha,native['asset_id'],canonical(result),now()))
            return result
        finally:building.unlink(missing_ok=True)

    def result(self,pid,jid):
        self.projects.get(pid)
        with self.projects._db() as db:
            row=db.execute('SELECT * FROM feature_advanced_joins WHERE join_id=? AND project_id=?',(jid,pid)).fetchone()
        if row is None:raise KeyError('没有找到通过审核回接的高级特征资产')
        if self.projects.security and row['owner_id']!=self.registry._actor():
            raise ValueError('高级特征产物只允许创建者查看')
        doc=json.loads(row['result_json'])
        if not self.native.verify(doc['native_asset_id']).get('valid'):
            raise ValueError('高级特征合并后的 BI 数据失效')
        return doc

    def submit_evaluation(self,pid,request:AdvancedEvaluationRequest):
        self.registry._editor(pid)
        joined=self.result(pid,request.join_job_id)
        if request.task.mode!='supervised' or request.task.task_type not in ('binary_classification','regression'):
            raise ValueError('高级产物模型对照需要监督分类或回归任务')
        if len(set(request.models))!=len(request.models) or not set(request.models)<={'linear','forest'}:
            raise ValueError('只允许线性或随机森林基线模型')
        payload={'request':request.model_dump(mode='json'),'joined_sha256':joined['joined_sha256'],
                 'decision_sha256':joined['decision_sha256']}
        return self.jobs._create_job(pid,'advanced_join_evaluation',request.join_job_id,request.request_id,payload)

    def evaluate_worker(self,row,payload):
        request=AdvancedEvaluationRequest.model_validate(payload['request'])
        pid=row['project_id'];joined=self.result(pid,request.join_job_id)
        if joined['joined_sha256']!=payload['joined_sha256'] or joined['decision_sha256']!=payload['decision_sha256']:
            raise ValueError('评价前物化产物变化，拒绝使用失效报告')
        path=self.jobs.root/pid/'advanced_join'/request.join_job_id/'data.parquet'
        if not path.is_file() or sha256_file(path)!=joined['joined_sha256']:
            raise ValueError('模型读取的 Parquet 物化哈希不一致')
        import pandas as pd
        from timepilot.feature_evaluation import _evaluate_matrix
        contract=self.semantic.get_revision(pid,joined['decision_asset_id'],joined['decision_version_id'],joined['decision_contract_revision'])
        from timepilot.feature_safety import ensure_supervised_contract
        ensure_supervised_contract(contract,request.task)
        selected=list(joined['output_columns'].values())
        data=pd.read_parquet(path)
        from timepilot.feature_operator_registry import compatible_fields
        allowed=set(compatible_fields(contract,'numeric'))
        base=[f.source_name for f in contract.fields if f.field_id in allowed and f.role=='value' and f.source_name in data]
        if not base or not selected:raise ValueError('缺少原始基线或待评价高级特征')
        protocol=EvaluationProtocol(task=request.task,set_id=request.join_job_id,revision=1,
            models=request.models,min_relative_improvement=request.min_relative_improvement,
            selection=QualitySelectionPolicy(max_selected=min(24,len(selected))),
            search=AutoSearchPolicy(enabled=False),
            slices=SlicePolicy(field_ids=request.slice_field_ids,min_rows=10))
        report=_evaluate_matrix(data,base,selected,request.task,protocol,contract,
            source_sha256=joined['decision_sha256'],
            spec_sha256=joined['joined_sha256'])
        report.update(evaluated_artifact_id=request.join_job_id,
                      evaluated_artifact_sha256=joined['joined_sha256'],
                      evaluated_native_asset_id=joined['native_asset_id'],
                      candidate_source_sha256=joined['advanced_candidate_sha256'],
                      evaluation_scope='actual_joined_parquet_same_as_bi')
        with self.projects._lock,self.projects._db() as db:
            db.execute('''INSERT INTO feature_advanced_join_evaluations
              (evaluation_id,project_id,owner_id,join_id,source_sha256,artifact_sha256,report_json,created_at)
              VALUES(?,?,?,?,?,?,?,?)''',
              (row['job_id'],pid,row['owner_id'],request.join_job_id,joined['decision_sha256'],
               joined['joined_sha256'],canonical(report),now()))
        return {'evaluation_id':row['job_id'],'joined_sha256':joined['joined_sha256'],
                'source_sha256':joined['decision_sha256'],'report':report}

    def evaluation_result(self,pid,eid):
        self.projects.get(pid)
        with self.projects._db() as db:
            row=db.execute('SELECT * FROM feature_advanced_join_evaluations WHERE evaluation_id=? AND project_id=?',
                           (eid,pid)).fetchone()
        if row is None:raise KeyError('高级模型评价不存在')
        if self.projects.security and row['owner_id']!=self.registry._actor():
            raise ValueError('只允许查看本人提交的模型对照')
        report=json.loads(row['report_json']);result=self.result(pid,row['join_id'])
        if result['joined_sha256']!=row['artifact_sha256']:
            raise ValueError('高级模型评价引用的物化资产已过期')
        return {'evaluation_id':eid,'join_id':row['join_id'],'report':report}
