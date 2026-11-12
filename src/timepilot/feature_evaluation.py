"""FeatureLab v0.29: nested temporal evaluation, selection, stability and budgeted search.

Source/label/PIT gates are enforced before this evaluator reads a verified artifact.
No outer validation observation is used for selecting features or tuning transforms.
"""
from __future__ import annotations
import json
import math
from uuid import uuid4
import numpy as np
import pandas as pd
from timepilot.feature_contracts import EvaluationProtocol,TaskSpecV2,canonical,digest
from timepilot.feature_compiler import NUMERIC
from timepilot.feature_split_protocol import build_folds
from timepilot.projects import now


def _prepare(train,valid):
    # Imputation and standardization statistics come from *training only*.
    median=np.nanmedian(train,axis=0)
    median=np.nan_to_num(median,nan=0.0,posinf=0,neginf=0)
    train=np.where(np.isfinite(train),train,median)
    valid=np.where(np.isfinite(valid),valid,median)
    mean=np.mean(train,axis=0);scale=np.std(train,axis=0)
    scale=np.where(scale<1e-12,1.0,scale)
    return (train-mean)/scale,(valid-mean)/scale


def _predict(train,train_y,valid,kind):
    x=np.column_stack([np.ones(len(train)),train]);v=np.column_stack([np.ones(len(valid)),valid])
    if kind=='regression':
        reg=np.eye(x.shape[1])*0.1;reg[0,0]=0
        beta=np.linalg.solve(x.T@x+reg,x.T@train_y)
        return v@beta
    if not np.isin(np.unique(train_y),[0,1]).all() or len(np.unique(train_y))<2:
        raise ValueError('训练折没有二分类的全部标签')
    w=np.zeros(x.shape[1]);reg=0.005
    lipschitz=max(1.0,np.linalg.norm(x,ord='fro')**2/(4*len(x))+reg)
    for _ in range(300):
        score=np.clip(x@w,-30,30)
        prob=1/(1+np.exp(-score))
        grad=x.T@(prob-train_y)/len(x)+reg*np.r_[0,w[1:]]
        w-=grad/lipschitz
    return 1/(1+np.exp(-np.clip(v@w,-30,30)))


def _metrics(truth,pred,kind):
    if kind=='regression':
        return {'mae':float(np.mean(np.abs(truth-pred))),
                'rmse':float(np.sqrt(np.mean((truth-pred)**2)))}
    truth=np.asarray(truth,dtype=float)
    pred=np.asarray(pred,dtype=float)
    if truth.ndim!=1 or pred.ndim!=1 or len(truth)!=len(pred) or not len(truth):
        raise ValueError('二分类评价需要非空且等长的一维真实标签与预测概率')
    if not np.isfinite(truth).all() or not np.isin(truth,[0,1]).all():
        raise ValueError('二分类标签必须全部属于已确认的 {0,1}，禁止自动二值化异常标签')
    if not np.isfinite(pred).all() or np.any((pred<0)|(pred>1)):
        raise ValueError('二分类预测概率必须为有限的 [0,1] 数值')
    y=truth.astype(bool);h=pred>=0.5
    tp=int(np.sum(y&h));fn=int(np.sum(y&~h));fp=int(np.sum(~y&h));tn=int(np.sum(~y&~h))
    # Ranking-based average precision with explicit positive count.
    order=np.argsort(-pred,kind='stable');sorted_y=y[order];positives=int(np.sum(y))
    ap=float(np.sum(np.cumsum(sorted_y)[sorted_y]/np.arange(1,len(y)+1)[sorted_y])/positives) if positives else None
    return {'average_precision':ap,'balanced_accuracy':((tp/(tp+fn))+(tn/(tn+fp)))/2 if (tp+fn)*(tn+fp)>0 else None,
            'recall':tp/(tp+fn) if tp+fn else None,
            'precision':tp/(tp+fp) if tp+fp else None,
            'false_reject_rate':fp/(fp+tn) if fp+tn else None,'pr_auc':ap,
            'tp':tp,'tn':tn,'fp':fp,'fn':fn}



def _fit_predict(train, train_y, valid, kind, family='linear', *, seed=42):
    """Real sklearn estimator, with imputation and scaling fitted on train only."""
    from sklearn.impute import SimpleImputer
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    from sklearn.linear_model import LogisticRegression, Ridge
    from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
    if kind=='binary_classification' and len(np.unique(train_y))<2:
        raise ValueError('训练折没有二分类的全部标签')
    if train.ndim!=2 or valid.ndim!=2 or train.shape[1]!=valid.shape[1]:
        raise ValueError('训练与验证输入矩阵不匹配')
    if train.shape[1]==0:raise ValueError('模型输入不能没有数值特征')
    a=np.where(np.isfinite(train),train,np.nan)
    b=np.where(np.isfinite(valid),valid,np.nan)
    # Constant/all-null raw columns are retained for comparator fairness. The
    # imputer keeps the dimensionality fixed; no valid-fold median is touched.
    imputer=SimpleImputer(strategy='median',keep_empty_features=True)
    if family=='linear':
        estimator=(Ridge(alpha=10.0) if kind=='regression' else
                   LogisticRegression(solver='liblinear',class_weight='balanced',
                                      max_iter=350,random_state=seed))
        model=make_pipeline(imputer,StandardScaler(),estimator)
    elif family=='forest':
        common=dict(n_estimators=75,max_depth=10,min_samples_leaf=4,
                    n_jobs=1,random_state=seed)
        estimator=(RandomForestRegressor(**common) if kind=='regression' else
                   RandomForestClassifier(class_weight='balanced_subsample',**common))
        model=make_pipeline(imputer,estimator)
    else:raise ValueError('未知模型方案：'+str(family))
    model.fit(a,np.asarray(train_y))
    return (model.predict(b) if kind=='regression' else model.predict_proba(b)[:,1])


def _evaluate_matrix(data,base,outputs,task,protocol,contract,*,source_sha256, spec_sha256):
    """Pure value-level evaluator usable by the live Parquet executor and offline tests.

    `data` contains the source fields and approved generated columns, never
    model predictions. The original recorded row order is not used for folds.
    """
    from timepilot.feature_label_contract import validate_target,fold_quality
    from timepilot.feature_selection import select_training_features
    from timepilot.feature_stability import drift_summary,sliced_metrics,summarize_folds
    from timepilot.feature_search import search_training_fold
    mapping={f.field_id:f.source_name for f in contract.fields}
    time_col=mapping[task.prediction_time_field_id]
    target=mapping[task.label_field_id]
    available_col=mapping[task.label_available_at_field_id]
    group_cols=[mapping[fid] for fid in task.group_field_ids] if task.split_kind=='grouped_time' else []
    if not all(col in data.columns for col in [time_col,target,available_col,*base,*outputs,*group_cols]):
        raise ValueError('物化结果缺少受约束的原字段、标签、时间或派生特征列')
    slices=[]
    by_id={f.field_id:f for f in contract.fields}
    for fid in protocol.slices.field_ids:
        field=by_id.get(fid)
        if field is None or field.confirmation!='confirmed' or field.role not in ('entity_key','batch_key','regime'):
            raise ValueError('稳定性分组必须绑定已确认的设备、批次或工况字段')
        if field.source_name not in data.columns:
            raise ValueError('物化数据缺少稳定性分组字段：'+field.source_name)
        slices.append(field.source_name)
    if task.split_kind=='grouped_time':
        slices=list(dict.fromkeys([*slices,*group_cols]))
    folds,manifest=build_folds(data,time_column=time_col,n_splits=task.n_splits,
           purge_rows=task.purge_rows,group_columns=group_cols)
    y, label_contract=validate_target(data[target],task.task_type)
    available=pd.to_datetime(data[available_col],utc=True,errors='coerce')
    decision=pd.to_datetime(data[time_col],utc=True,errors='coerce')
    if available.isna().any():raise ValueError('标签可用时间含非法或缺失值，无法进行训练时点过滤')
    all_fields=[*base,*outputs]
    if len(all_fields)>1024 or len(data)*len(all_fields)>12_000_000:
        raise ValueError('训练矩阵超过 1024 列或 1200 万单元限额')
    matrix=data[all_fields].apply(pd.to_numeric,errors='coerce').to_numpy(dtype=float)
    matrix[~np.isfinite(matrix)]=np.nan
    nbase=len(base)
    units_by_name={f.source_name:f.unit for f in contract.fields}
    ids_by_name={f.source_name:f.field_id for f in contract.fields}
    model_fold=[];selections=[];search_reports=[]
    for i,(train_idx,valid_idx) in enumerate(folds):
        cutoff=decision.iloc[train_idx].max()
        train_idx=train_idx[(available.iloc[train_idx]<=cutoff).fillna(False).to_numpy()]
        valid_idx=valid_idx[np.isfinite(y[valid_idx])]
        if len(train_idx)<40 or len(valid_idx)<15:
            raise ValueError('标签可用时点过滤后训练/验证记录不足（各需40/15）')
        if task.task_type=='binary_classification' and len(set(y[train_idx]))<2:
            raise ValueError('训练折仅包含一个类别；请调整切分或收集更多缺陷标签')
        selection=select_training_features(matrix[train_idx],y[train_idx],base,outputs,
                         protocol.selection,task_type=task.task_type) if protocol.selection.enabled else {
                            'scope':'outer_training_fold_only','selected_indices':list(range(nbase,len(all_fields))),
                            'selected_features':list(outputs),'ranks':[],'rejected':[],'policy':protocol.selection.model_dump(),
                            'note':'手动关闭特征筛选'}
        selections.append(selection)
        selected=selection['selected_indices']
        xtrain=matrix[train_idx];xvalid=matrix[valid_idx]
        search,search_train,search_valid=search_training_fold(
            xtrain[:,:nbase],xvalid[:,:nbase],y[train_idx],task.task_type,
            protocol.search,base,[ids_by_name[x] for x in base],
            [units_by_name[x] for x in base])
        search_reports.append(search)
        chosen=[*range(nbase),*selected]
        matrices={
          'raw_only':(xtrain[:,:nbase],xvalid[:,:nbase]),
          'with_features':(xtrain,xvalid),
          'selected_features':(xtrain[:,chosen],xvalid[:,chosen])}
        if protocol.search.enabled and search_train.shape[1]:
            matrices['auto_search']=(np.column_stack([xtrain[:,chosen],search_train]),
                                     np.column_stack([xvalid[:,chosen],search_valid]))
        by_model={};slice_rows=[]
        for model in protocol.models:
            by_variant={}
            for variant,(a,b) in matrices.items():
                prediction=_fit_predict(a,y[train_idx],b,task.task_type,model,seed=protocol.seed)
                by_variant[variant]=_metrics(y[valid_idx],prediction,task.task_type)
                if variant in ('raw_only','selected_features') and model==protocol.models[0]:
                    slice_rows.append({'variant':variant,'groups':sliced_metrics(data.iloc[valid_idx],
                        y[valid_idx],prediction,task.task_type,slices,protocol.slices)})
            by_model[model]=by_variant
        stability=drift_summary(xtrain[:,chosen],xvalid[:,chosen],
                        [all_fields[idx] for idx in chosen],max_fields=30)
        quality=(fold_quality(y[valid_idx]) if task.task_type=='binary_classification'
                 else {'sample_count':len(valid_idx)})
        model_fold.append({'fold':i,'train_count':int(len(train_idx)),
            'valid_count':int(len(valid_idx)),'training_cutoff':cutoff.isoformat(),
            'model_metrics':by_model,'metrics':by_model[protocol.models[0]],
            'validation_label_quality':quality,
            'training_feature_selection':selection,'training_feature_diagnostics':selection,
            'feature_stability':stability,'group_stability':slice_rows,
            'search':search,
            'warnings':['验证集只有一个类别，AP/召回率可能不可用'] if quality.get('single_class') else []})
    # Search support is evidence across folds, never computed from pooled val labels.
    selected_counts={name:sum(name in row['selected_features'] for row in selections) for name in outputs}
    search_freq={}
    for row in search_reports:
        for r in row.get('recipes',[]):
            key=canonical({'fields':r['source_fields'],'expression':r['expression']})
            item=search_freq.setdefault(key,{'source_fields':r['source_fields'],
                    'expression':r['expression'],'label':r['name'],'folds':0})
            item['folds']+=1
    candidates=sorted(search_freq.values(),key=lambda r:(-r['folds'],r['label']))
    for recipe in candidates:
        recipe['candidate_id']='autofeat_'+digest(recipe)[:18]
        recipe['status']='requires_manual_review_and_materialization'
    summary=summarize_folds(model_fold,task.task_type)
    objective='mae' if task.task_type=='regression' else 'average_precision'
    objective_sign=-1 if objective=='mae' else 1
    comparison=[]
    for variant in ['with_features','selected_features',*( ['auto_search'] if protocol.search.enabled else [] )]:
        fold_changes=[]
        baseline_magnitudes=[]
        for fold in model_fold:
            metrics=fold['metrics']
            before=metrics['raw_only'].get(objective)
            after=metrics.get(variant,{}).get(objective)
            if before is not None and after is not None:
                fold_changes.append(objective_sign*(after-before))
                baseline_magnitudes.append(abs(float(before)))
        comparisons=len(fold_changes)
        gains=sum(v>1e-9 for v in fold_changes)
        minimum=protocol.min_relative_improvement*(float(np.mean(baseline_magnitudes)) if baseline_magnitudes else 0.0)
        mean_gain=float(np.mean(fold_changes)) if comparisons else None
        consistent=comparisons>=2 and gains>=max(2,math.ceil(comparisons*.6))
        verdict=('insufficient_evidence' if comparisons<len(model_fold) else
                 'review_candidate' if consistent and mean_gain>minimum else
                 'negligible_gain' if consistent and mean_gain>0 else
                 'unstable_or_no_gain')
        comparison.append({'variant':variant,'reference_model':protocol.models[0],
             'objective':objective,'paired_folds':comparisons,'positive_folds':gains,
             'mean_improvement':round(mean_gain,6) if mean_gain is not None else None,
             'minimum_practical_gain':round(minimum,6),'verdict':verdict,
             'interpretation':'仅外层时间验证折的描述性结果，最小实际收益基于原始字段基线的相对阈值；业务成本和上线阈值需单独评价'})
    return {'mode':'supervised' ,'task_type':task.task_type,
            'source_sha256':source_sha256,'spec_sha256':spec_sha256,
            'split_manifest_sha256':digest(manifest),'fold_manifest':manifest,
            'folds':model_fold,'model_families':protocol.models,
            'baseline':'sklearn_linear_or_random_forest','training_only_preprocessing':True,
            'training_fold_feature_selection':True,
            'label_contract':label_contract,'label_availability_checked':True,
            'selection_policy':protocol.selection.model_dump(),
            'search_policy':protocol.search.model_dump(),
            'slice_policy':protocol.slices.model_dump(),
            'screening_recommendations':comparison,
            'selection_frequency':sorted([{'feature':k,'selected_in_folds':v,'total_folds':len(folds)}
                for k,v in selected_counts.items()],key=lambda item:(-item['selected_in_folds'],item['feature'])),
            'searched_candidate_recipes':candidates,
            'stability_summary':summary,
            'paired_comparison':paired_fold_comparison(model_fold,task.task_type),
            'variant_comparison':{
                v:paired_fold_comparison(model_fold,task.task_type,variant=v)
                for v in ('selected_features','auto_search') if v!='auto_search' or protocol.search.enabled},
            'limitations':['训练折内选择与内部时间验证控制泄漏，不能证明因果关系',
                '模型评价使用固定位 0.5 分类阈值；上线前应按业务成本另行进行训练期阈值调优',
                '没有独立企业生产数据和完整模型部署回测时不得声明量产适用']}


def paired_fold_comparison(folds:list[dict],task_type:str,variant='with_features')->dict:
    """Report only same-fold paired deltas; missing metrics remain unavailable.

    Small-N t confidence bounds are deliberately omitted: a five-fold result
    does not provide an independent sampling distribution or causal evidence.
    """
    directions={'mae':-1,'rmse':-1} if task_type=='regression' else {
        'recall':1,'precision':1,'false_reject_rate':-1,'pr_auc':1,'average_precision':1,'balanced_accuracy':1}
    out={}
    for metric,sign in directions.items():
        paired=[]
        for fold in folds:
            values=fold.get('metrics',{})
            left=values.get('raw_only',{}).get(metric)
            right=values.get(variant,{}).get(metric)
            if isinstance(left,(int,float)) and isinstance(right,(int,float)) and math.isfinite(left) and math.isfinite(right):
                paired.append(float(right-left))
        out[metric]={'n_paired_folds':len(paired),'delta_by_fold':paired,
                     'mean_delta':sum(paired)/len(paired) if paired else None,
                     'direction':'lower_better' if sign<0 else 'higher_better',
                     'mean_improvement':sign*sum(paired)/len(paired) if paired else None}
    return {'same_fold_only':True,'variant':variant,'baseline_variant':'raw_only','metrics':out,
        'limitations':['均值是同折差值的描述统计；未报告统计显著性或跨设备因果结论']}


class FeatureEvaluator:
    def __init__(self,projects,native,semantic,registry,jobs):
        self.projects=projects;self.native=native;self.semantic=semantic
        self.registry=registry;self.jobs=jobs

    def _check_supervised_artifact(self,pid,rec,task):
        from timepilot.feature_safety import ensure_pit_evidence
        from timepilot.feature_contracts import FeatureSpecV2
        spec=FeatureSpecV2.model_validate(rec['spec'])
        if spec.task is None or spec.task.mode!='supervised':
            raise ValueError('探索性特征未绑定当前监督任务及PIT证据，禁止事后改变任务用途')
        contract=self.semantic.get_revision(pid,rec['asset_id'],rec['version_id'],spec.contract_revision)
        from timepilot.feature_artifact_policy import eligible_artifact
        valid=eligible_artifact(self.projects,pid,rec,self.semantic)
        if valid is None:
            raise ValueError('监督评价必须先产生来源、任务状态、文件哈希和PIT全部有效的完整物化结果')
        ensure_pit_evidence(spec,task,valid['metadata'],contract)
        return valid['artifact'],valid['metadata']

    def plan(self,pid,protocol:EvaluationProtocol):
        self.registry._editor(pid)
        rec=self.registry.get(pid,protocol.set_id)
        if rec['revision']!=protocol.revision:raise ValueError('待评估的特征配方版本已变化')
        spec=self.registry.pinned(pid,rec)
        if spec.task is not None and canonical(spec.task)!=canonical(protocol.task):
            raise ValueError('评价协议任务与已物化特征集绑定任务不一致')
        task=protocol.task
        if task.mode=='supervised':self._check_supervised_artifact(pid,rec,task)
        split_hash=digest({'source':rec['source_sha256'],'graph':rec['spec_sha256'],
                           'task':task,'protocol':protocol})
        plan_id=str(uuid4())
        with self.projects._lock,self.projects._db() as db:
            db.execute('''INSERT INTO feature_evaluation_plans
                 (plan_id,project_id,set_id,protocol_json,split_sha256,state,created_at)
                 VALUES (?,?,?,?,?,?,?)''',
                 (plan_id,pid,protocol.set_id,canonical(protocol),split_hash,'ready',now()))
        return {'plan_id':plan_id,'state':'ready','mode':task.mode,'split_fingerprint':split_hash,
                'requires_true_engine':True,'pit_gate':'verified' if task.mode=='supervised' else 'not_applicable'}

    def submit(self,pid,plan_id,request_id):
        self.registry._editor(pid)
        with self.projects._db() as db:
            row=db.execute('SELECT * FROM feature_evaluation_plans WHERE project_id=? AND plan_id=?',
                           (pid,plan_id)).fetchone()
        if row is None:raise KeyError('评估计划不存在或无权限')
        protocol=EvaluationProtocol.model_validate_json(row['protocol_json'])
        rec=self.registry.get(pid,protocol.set_id)
        if rec['revision']!=protocol.revision:raise ValueError('实验计划所绑定版本已经失效')
        spec=self.registry.pinned(pid,rec)
        if spec.task is not None and canonical(spec.task)!=canonical(protocol.task):
            raise ValueError('评价协议任务与特征集绑定任务不一致')
        if protocol.task.mode=='supervised':self._check_supervised_artifact(pid,rec,protocol.task)
        job=self.jobs._create_job(pid,'evaluation',plan_id,request_id,{'plan_id':plan_id})
        return job

    def run_plan(self,pid,plan_id,evaluation_id):
        with self.projects._db() as db:
            row=db.execute('SELECT * FROM feature_evaluation_plans WHERE project_id=? AND plan_id=?',
                           (pid,plan_id)).fetchone()
        if row is None:raise KeyError('评估计划不存在')
        protocol=EvaluationProtocol.model_validate_json(row['protocol_json'])
        rec=self.registry.get(pid,protocol.set_id)
        spec=self.registry.pinned(pid,rec)
        if rec['revision']!=protocol.revision:raise ValueError('评估所引用特征集版本已失效')
        if protocol.task.mode=='exploration':
            # Report real available profile evidence; never infer predictive
            # gain or call a feature definition itself a computed statistic.
            with self.projects._db() as db:
                snapshot=db.execute("""SELECT profile_id,result_json FROM feature_profiles
                    WHERE project_id=? AND asset_id=? AND source_sha256=?
                    AND contract_revision=? AND scope='full'
                    ORDER BY created_at DESC LIMIT 1""",
                    (pid,rec['asset_id'],rec['source_sha256'],spec.contract_revision)).fetchone()
                artifact=db.execute("""SELECT a.artifact_id,a.row_count,a.sha256 FROM feature_artifacts a
                    JOIN feature_runs r ON r.run_id=a.run_id JOIN feature_jobs j ON j.job_id=r.job_id
                    WHERE a.project_id=? AND r.set_id=? AND r.revision=? AND r.spec_sha256=?
                      AND r.source_sha256=? AND r.status='succeeded' AND j.status='succeeded'
                    ORDER BY a.created_at DESC LIMIT 1""",
                    (pid,rec['set_id'],rec['revision'],rec['spec_sha256'],rec['source_sha256'])).fetchone()
            real_profile=json.loads(snapshot['result_json']) if snapshot else None
            fields=real_profile.get('fields',[]) if real_profile else []
            report={'mode':'exploration','source_sha256':rec['source_sha256'],
                    'spec_sha256':rec['spec_sha256'],'feature_count':len(spec.features),
                    'feature_definitions':[{'feature_id':f.feature_id,'operator':f.operator_id,
                         'output_name':f.output_name} for f in spec.features],
                    'data_profile':{'profile_id':snapshot['profile_id'],'scope':'full',
                         'rows_scanned':real_profile.get('rows_scanned'),
                         'missing_rates':{f['field_id']:f.get('null_rate') for f in fields},
                         'time':real_profile.get('time')} if real_profile else None,
                    'feature_artifact':dict(artifact) if artifact else None,
                    'feature_values_evaluated':False,
                    'predictive_gain':None,'label_required':False,
                    'limitations':['未设置监督标签，未执行预测性能比较',
                        '缺少完整特征值质量分析，不能据此认定候选特征有效']}
        else:
            artifact,_=self._check_supervised_artifact(pid,rec,protocol.task)
            if artifact['row_count']>protocol.max_rows:
                raise ValueError('评估数据超过明确配置的资源预算，拒绝隐式截样')
            column_count=len(json.loads(artifact['schema_json']).get('columns',[]))
            if artifact['row_count']*column_count>12_000_000:
                raise ValueError('评估内存预算超限，请缩小任务或采用批处理')
            path=self.jobs.artifact_path(pid,artifact['artifact_id'])
            from timepilot.storage import sha256_file
            if sha256_file(path)!=artifact['sha256']:
                raise ValueError('评估数据 SHA256 核对失败')
            with self.native._connection() as con:
                data=con.execute('SELECT * FROM read_parquet(?)',[str(path)]).df()
            task=protocol.task
            contract=self.semantic.get_revision(pid,rec['asset_id'],rec['version_id'],spec.contract_revision)
            from timepilot.feature_operator_registry import compatible_fields
            allowed=set(compatible_fields(contract,'numeric'))
            base=[f.source_name for f in contract.fields if f.field_id in allowed
                  and f.role=='value' and f.confirmation=='confirmed'
                  and f.source_name in data.columns]
            if not base:raise ValueError('缺少已确认的原始数值过程字段，无法构建预测基线')
            output=[f.output_name for f in spec.features]
            report=_evaluate_matrix(data,base,output,task,protocol,contract,
                source_sha256=rec['source_sha256'],spec_sha256=rec['spec_sha256'])
            # Bind the actual physical Parquet consumed by this evaluation.
            # A source/spec fingerprint alone would not distinguish separately
            # materialized files or make a same-artifact E2E receipt auditable.
            report['evaluated_artifact_id']=artifact['artifact_id']
            report['evaluated_artifact_sha256']=artifact['sha256']
            report['evaluated_contract_revision']=spec.contract_revision
        with self.projects._lock,self.projects._db() as db:
            db.execute('''INSERT OR REPLACE INTO feature_evaluations
              (evaluation_id,project_id,plan_id,status,report_json,created_at)
              VALUES (?,?,?,?,?,?)''',
              (evaluation_id,pid,plan_id,'succeeded',canonical(report),now()))
            for fold in report.get('folds',[]):
                m=report['fold_manifest'][fold['fold']]
                db.execute('''INSERT OR REPLACE INTO feature_folds VALUES(?,?,?,?,?,?,?)''',
                   (evaluation_id,fold['fold'],'start',m['train_end'],m['validation_start'],
                    'end',canonical(fold['metrics'])))
        return {'evaluation_id':evaluation_id,'report':report}

    def get(self,pid,evaluation_id):
        self.projects.get(pid)
        with self.projects._db() as db:
            row=db.execute('SELECT * FROM feature_evaluations WHERE project_id=? AND evaluation_id=?',
                           (pid,evaluation_id)).fetchone()
        if row is None:raise KeyError('评估报告不存在或没有权限')
        data=dict(row);data['report']=json.loads(data.pop('report_json')) if data['report_json'] else None
        return data


    def latest(self,pid,sid):
        rec=self.registry.get(pid,sid)
        with self.projects._db() as db:
            rows=db.execute('''SELECT e.evaluation_id FROM feature_evaluations e
               JOIN feature_evaluation_plans p ON p.plan_id=e.plan_id
               WHERE e.project_id=? AND p.set_id=? AND e.status='succeeded'
               ORDER BY e.created_at DESC LIMIT 10''',(pid,sid)).fetchall()
        for row in rows:
            found=self.get(pid,row['evaluation_id'])
            report=found['report'] or {}
            if (report.get('source_sha256')==rec['source_sha256'] and
                report.get('spec_sha256')==rec['spec_sha256']):
                return found
        return None

    def selection(self,pid,evaluation_id):
        evaluated=self.get(pid,evaluation_id)
        if not evaluated['report'] or evaluated['report'].get('mode')!='supervised':
            raise ValueError('仅有监督实验支持结果筛选决策')
        with self.projects._db() as db:
            rec=db.execute('''SELECT * FROM feature_evaluation_selections
                WHERE project_id=? AND evaluation_id=? AND owner_id=?''',
                (pid,evaluation_id,self.registry._actor())).fetchone()
        if not rec:return None
        result=dict(rec);result['selection']=json.loads(result.pop('selection_json'))
        return result

    def save_selection(self,pid,evaluation_id,*,feature_ids,search_candidate_ids,
                       expected_revision=0,note=''):
        self.registry._editor(pid)
        if len(note)>1000:raise ValueError('审阅意见不能超过1000字符')
        evaluated=self.get(pid,evaluation_id)
        report=evaluated['report'] or {}
        if report.get('mode')!='supervised':raise ValueError('仅支持监督模型的结果筛选')
        with self.projects._db() as db:
            plan=db.execute('SELECT set_id FROM feature_evaluation_plans WHERE plan_id=? AND project_id=?',
                            (evaluated['plan_id'],pid)).fetchone()
        if not plan:raise ValueError('评价来源计划不存在')
        record=self.registry.get(pid,plan['set_id'])
        self.registry.pinned(pid,record)  # Check current source, contract, and pinned revision.
        if record['source_sha256']!=report['source_sha256'] or record['spec_sha256']!=report['spec_sha256']:
            raise ValueError('评估特征集来源或修订已过期')
        allowed={f['feature_id'] for f in record['spec']['features']}
        search={r['candidate_id'] for r in report.get('searched_candidate_recipes',[])}
        if len(feature_ids)!=len(set(feature_ids)) or set(feature_ids)-allowed:
            raise ValueError('勾选了评价中不存在的原始派生特征')
        if len(search_candidate_ids)!=len(set(search_candidate_ids)) or set(search_candidate_ids)-search:
            raise ValueError('勾选了当前评价中不存在的搜索公式')
        if not feature_ids and not search_candidate_ids:
            raise ValueError('请至少保留一项派生特征或搜索候选')
        selection={'feature_ids':feature_ids,'search_candidate_ids':search_candidate_ids,
                   'note':note,'evaluation_id':evaluation_id}
        timestamp=now()
        with self.projects._lock,self.projects._db() as db:
            before=db.execute('''SELECT selection_id,revision FROM feature_evaluation_selections
                 WHERE project_id=? AND evaluation_id=? AND owner_id=?''',
                 (pid,evaluation_id,self.registry._actor())).fetchone()
            if before and before['revision']!=expected_revision:
                raise ValueError('审阅决策版本冲突，请刷新后重新保存')
            if not before and expected_revision!=0:
                raise ValueError('审阅决策版本冲突')
            if before:
                cur=db.execute('''UPDATE feature_evaluation_selections
                     SET revision=revision+1,selection_json=?,selection_sha256=?,updated_at=?
                     WHERE selection_id=? AND revision=?''',
                     (canonical(selection),digest(selection),timestamp,before['selection_id'],expected_revision))
                if cur.rowcount!=1:raise ValueError('审阅记录并发版本冲突')
            else:
                db.execute('''INSERT INTO feature_evaluation_selections VALUES
                   (?,?,?,?,?,?,?,?,?,?,?)''',
                   (str(uuid4()),pid,evaluation_id,self.registry._actor(),1,canonical(selection),
                    digest(selection),report['source_sha256'],report['spec_sha256'],timestamp,timestamp))
        return self.selection(pid,evaluation_id)

    def derive_set(self,pid,evaluation_id,*,selection_revision,request_id,name):
        """Create a NEW unreviewed draft. No approval or publication is inherited."""
        self.registry._editor(pid)
        selected=self.selection(pid,evaluation_id)
        if not selected or selected['revision']!=selection_revision:
            raise ValueError('保存的审阅选择已变化')
        evaluated=self.get(pid,evaluation_id)
        report=evaluated['report']
        if selected['source_sha256']!=report['source_sha256'] or selected['spec_sha256']!=report['spec_sha256']:
            raise ValueError('来源指纹不一致')
        with self.projects._db() as db:
            row=db.execute('SELECT set_id FROM feature_evaluation_plans WHERE plan_id=? AND project_id=?',
                (evaluated['plan_id'],pid)).fetchone()
        original=self.registry.get(pid,row['set_id'])
        spec=self.registry.pinned(pid,original)
        from timepilot.feature_custom import validate_custom_definition
        contract=self.semantic.get_revision(pid,original['asset_id'],original['version_id'],spec.contract_revision)
        allowed=set(selected['selection']['feature_ids'])
        retained=[f for f in spec.features if f.feature_id in allowed]
        recipes={r['candidate_id']:r for r in report.get('searched_candidate_recipes',[])}
        for cid in selected['selection']['search_candidate_ids']:
            r=recipes[cid]
            candidate=validate_custom_definition(contract,r['source_fields'],r['expression'],r['label'])
            from timepilot.feature_contracts import FeatureDefinition
            new=FeatureDefinition.model_validate(candidate['definition'])
            if new.output_name not in {f.output_name for f in retained}:
                retained.append(new)
        if not retained:raise ValueError('筛选结果没有可发布的特征定义')
        from timepilot.feature_contracts import FeatureSpecV2
        new_spec=FeatureSpecV2(name=name,source=spec.source,contract_revision=spec.contract_revision,
             task=spec.task,features=retained,selection_policy='full')
        return self.registry.create(pid,new_spec,request_id)
