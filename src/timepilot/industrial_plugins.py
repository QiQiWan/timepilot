"""Industrial forecasting/classification plugins with causal, reproducible inputs."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from timepilot.plugins import Capabilities, ModelPlugin


def _ridge(x: np.ndarray, y: np.ndarray, lam: float = 3.0):
    if len(x) < max(12, x.shape[1]+3):
        raise ValueError('多变量历史样本不足，至少需要满足参数数量和 12 个训练窗口')
    mean=x.mean(axis=0);scale=x.std(axis=0);scale[scale<1e-10]=1.
    design=np.column_stack([np.ones(len(x)),(x-mean)/scale])
    matrix=np.eye(design.shape[1])*lam
    matrix[0,0]=0
    weights=np.linalg.solve(design.T@design+matrix,design.T@y)
    return {'xmean':mean.tolist(),'xscale':scale.tolist(),'coefficients':weights.tolist()}


class HistoricalExogenousRidge(ModelPlugin):
    """Direct multi-horizon ridge using only known prior target/features.

    No future exogenous features are requested. The runtime supplies history
    whose records are available at the prediction origin.
    """
    def capabilities(self):
        return Capabilities('historical_exog_ridge','1.0',
            '多变量历史特征与目标滞后项的直接岭回归（无未来特征）',
            supports_frequency=('MS','D','h'),supports_observed_features=True)

    @staticmethod
    def _row(history: pd.DataFrame, features: list[str]):
        if len(history)<4:raise ValueError('至少需要 4 条历史观测构造滞后输入')
        target=history['target'].to_numpy(dtype=float)
        output=[target[-1],target[-2],target[-3],float(target[-3:].mean())]
        for name in features:
            values=history[name].to_numpy(dtype=float)
            output.extend([values[-1],float(values[-3:].mean())])
        return np.array(output,dtype=float)

    def fit(self,history,horizon,frequency,output_dir,budget_seconds,*,seasonal_period=None):
        feats=[c for c in history.columns if c.startswith(('obs_','regime_','event_'))]
        if not feats:raise ValueError('多变量模型必须提供显式历史特征')
        if len(history)<max(24,horizon+18):raise ValueError('多变量模型历史长度不足')
        design=[];labels=[[] for _ in range(horizon)]
        for origin in range(4,len(history)-horizon+1):
            # Each internal fit sample simulates a historical decision. Its
            # covariates must have been available at that decision instant;
            # training labels must not have been available yet.
            if 'available_at' in history:
                known = pd.to_datetime(history['available_at'].iloc[:origin], utc=True)
                origin_at = known.iloc[-1]
                future_known = pd.to_datetime(history['available_at'].iloc[origin:origin+horizon], utc=True)
                if known.max()>origin_at or (future_known<=origin_at).any():
                    continue
            # An explicit reset boundary prevents fitting across maintenance
            # events when the source contains a real event indicator.
            if 'event_flag' in history and history.attrs.get('reset_policy')=='exclude_cross_event_windows':
                if history['event_flag'].iloc[origin-4:origin+horizon].eq(1).any():continue
            design.append(self._row(history.iloc[:origin],feats))
            for step in range(horizon):labels[step].append(float(history['target'].iloc[origin+step]))
        if not design:raise ValueError('未找到满足机理/事件约束的训练窗口')
        x=np.vstack(design)
        models=[_ridge(x,np.asarray(label,dtype=float)) for label in labels]
        artifact={'features':feats,'horizon':horizon,'models':models,
                  'last_input':self._row(history,feats).tolist(),
                  'feature_policy':'past_only_at_prediction_origin',
                  'training_windows':len(x),'algorithm':'direct_ridge_lambda3'}
        output_dir.mkdir(parents=True,exist_ok=True)
        (output_dir/'model.json').write_text(json.dumps(artifact,ensure_ascii=False),encoding='utf-8')
        return artifact

    def predict(self,artifact,horizon,frequency):
        if horizon>artifact['horizon']:raise ValueError('预测步长不能超过模型训练步长')
        x=np.asarray(artifact['last_input'],dtype=float)
        outputs=[]
        for model in artifact['models'][:horizon]:
            mu=np.asarray(model['xmean']);scale=np.asarray(model['xscale'])
            weight=np.asarray(model['coefficients'])
            outputs.append(float(weight[0]+((x-mu)/scale)@weight[1:]))
        return np.asarray(outputs,dtype=float)

    def predict_from_history(self,artifact,history,horizon,frequency):
        if not set(artifact['features']).issubset(history.columns):
            raise ValueError('推断时历史特征清单与训练模型不一致')
        changed=dict(artifact)
        changed['last_input']=self._row(history,artifact['features']).tolist()
        return self.predict(changed,horizon,frequency)


def confusion_metrics(y:np.ndarray,p:np.ndarray,threshold:float) -> dict:
    target=y.astype(int);pred=(p>=threshold).astype(int)
    tp=int(np.sum((target==1)&(pred==1)));tn=int(np.sum((target==0)&(pred==0)))
    fp=int(np.sum((target==0)&(pred==1)));fn=int(np.sum((target==1)&(pred==0)))
    return {'tp':tp,'fp':fp,'tn':tn,'fn':fn,
            'recall':tp/(tp+fn) if tp+fn else None,
            'precision':tp/(tp+fp) if tp+fp else None,
            'false_positive_rate':fp/(fp+tn) if fp+tn else None,
            'specificity':tn/(fp+tn) if fp+tn else None,
            'accuracy':(tp+tn)/len(target) if len(target) else None,
            'selected_threshold':round(float(threshold),8)}


class NumpyLogisticClassifier(ModelPlugin):
    """L2-regularized binary logistic model; deterministic, no sklearn dependency."""
    def capabilities(self):
        return Capabilities('binary_logistic','1.0',
                            '二分类逻辑回归，训练阶段类别平衡，验证集独立选阈值',
                            supports_frequency=('MS','D','h'),task_kinds=('classification',))

    def fit(self,*args,**kwargs):
        raise ValueError('分类插件需使用分类运行器')

    def predict(self,*args,**kwargs):
        raise ValueError('分类插件需使用概率预测接口')

    def fit_classifier(self,train:pd.DataFrame,feature_columns:list[str],folder:Path):
        x=train[feature_columns].to_numpy(dtype=float)
        y=train['target'].to_numpy(dtype=float)
        if set(np.unique(y))!={0.,1.}:raise ValueError('训练样本必须同时具有正负类')
        if not np.isfinite(x).all():raise ValueError('分类特征必须为有限数值')
        center=x.mean(axis=0);spread=x.std(axis=0);spread[spread<1e-10]=1.
        scaled=(x-center)/spread
        design=np.column_stack([np.ones(len(x)),scaled]);weights=np.zeros(design.shape[1])
        positives=float(np.sum(y==1));negatives=float(np.sum(y==0))
        class_weights=np.where(y==1,len(y)/(2*positives),len(y)/(2*negatives))
        for _ in range(80):
            z=np.clip(design@weights,-40,40)
            p=1/(1+np.exp(-z))
            reg=np.r_[0.,np.repeat(1.0,len(weights)-1)]
            grad=design.T@(class_weights*(p-y))+reg*weights
            hess=design.T@((class_weights*p*(1-p))[:,None]*design)+np.diag(reg)+np.eye(len(weights))*1e-7
            delta=np.linalg.solve(hess,grad)
            # Damp large Newton steps for separable training data.
            delta=np.clip(delta,-3,3)
            weights-=delta
            if np.linalg.norm(delta)<1e-6:break
        model={'model':'binary_logistic','version':'1.0','columns':feature_columns,
               'weights':weights.tolist(),'mean':center.tolist(),'scale':spread.tolist(),
               'train_rows':len(train),'train_positive':int(positives)}
        folder.mkdir(parents=True,exist_ok=True)
        (folder/'model.json').write_text(json.dumps(model,ensure_ascii=False),encoding='utf-8')
        return model

    def predict_proba(self,model:dict,frame:pd.DataFrame) -> np.ndarray:
        columns=model['columns']
        if any(x not in frame for x in columns):raise ValueError('分类预测缺少训练特征')
        x=frame[columns].to_numpy(dtype=float)
        if not np.isfinite(x).all():raise ValueError('分类预测特征包含非有限数值')
        mean=np.asarray(model['mean']);std=np.asarray(model['scale'])
        design=np.column_stack([np.ones(len(x)),(x-mean)/std])
        return 1/(1+np.exp(-np.clip(design@np.asarray(model['weights']),-40,40)))


def choose_threshold(y:np.ndarray,probabilities:np.ndarray,min_recall:float) -> dict:
    """Pick the strictest validation-only threshold satisfying a recall floor.

    If the floor cannot be met due to class/model behavior, return a clear
    unmet status instead of optimizing on the test set.
    """
    if len(y)!=len(probabilities):raise ValueError('概率与标签长度不一致')
    choices=np.unique(np.r_[0.,probabilities,1.])
    valid=[]
    for t in choices:
        stats=confusion_metrics(y,probabilities,float(t))
        if stats['recall'] is not None and stats['recall']+1e-12>=min_recall:
            valid.append((stats['false_positive_rate'], -float(t),stats))
    if not valid:raise ValueError('验证集中无法计算召回率：请检查正例数量')
    valid.sort(key=lambda item:(item[0],item[1]))
    return valid[0][2]


def temporal_classification_split(frame:pd.DataFrame,spec) -> dict:
    """Matured-label temporal split with one global cutoff and batch leakage guard.

    Val/test are never randomly shuffled, and an example whose label has not
    matured by the next decision stage is excluded from training/validation.
    """
    if frame.empty:raise ValueError('分类样本为空')
    if not {'timestamp','label_available_at','target','available_at'}.issubset(frame.columns):
        raise ValueError('分类数据缺少时间、标签时间或目标字段')
    times=pd.to_datetime(frame['timestamp'],utc=True)
    available=pd.to_datetime(frame['available_at'],utc=True)
    maturity=pd.to_datetime(frame['label_available_at'],utc=True)
    if (maturity>pd.Timestamp.now(tz='UTC')).any():
        raise ValueError('存在尚未到达确认时间的未来标签，无法进行离线回测')
    if (available>times).any():raise ValueError('分类特征在决策时刻尚不可用，存在未来泄漏')
    unique=sorted(times.unique())
    if len(unique)<15:raise ValueError('分类至少需要 15 个不同决策时刻以完成时间切分')
    v_index=int(len(unique)*(1-spec.validation_fraction-spec.test_fraction))
    t_index=int(len(unique)*(1-spec.test_fraction))
    if not (0<v_index<t_index<len(unique)):raise ValueError('分类时间切分跨度不足')
    vstart=pd.Timestamp(unique[v_index]);tstart=pd.Timestamp(unique[t_index])
    train=frame.loc[(times<vstart)&(maturity<=vstart)].copy()
    valid=frame.loc[(times>=vstart)&(times<tstart)&(maturity<=tstart)].copy()
    test=frame.loc[times>=tstart].copy()
    dropped_train=int(((times<vstart)&(maturity>vstart)).sum())
    dropped_valid=int(((times>=vstart)&(times<tstart)&(maturity>tstart)).sum())
    if any(len(x)==0 for x in (train,valid,test)):
        raise ValueError('分类训练、验证或测试集中无成熟标签样本')
    positive=[]
    for label,subset,needed in zip(('训练','验证','测试'),(train,valid,test),
                                    (spec.min_positive_train,spec.min_positive_validation,spec.min_positive_test)):
        count=int((subset['target'].astype(int)==1).sum())
        negative=int((subset['target'].astype(int)==0).sum())
        if count<needed or negative<2:
            raise ValueError(f'{label}期正例 {count} / 负例 {negative}；需要至少 {needed} 个正例和 2 个负例')
        positive.append(count)
    if 'batch_key' in frame:
        groups=[set(x['batch_key'].astype(str)) for x in (train,valid,test)]
        if groups[0]&groups[1] or groups[0]&groups[2] or groups[1]&groups[2]:
            raise ValueError('同一批次横跨训练、验证或测试时间段，需调整批次切分/筛选以防泄漏')
    return {'train':train,'validation':valid,'test':test,
            'audit':{'split':'global_temporal_forward','train_rows':len(train),
                     'validation_rows':len(valid),'test_rows':len(test),
                     'train_positive':positive[0],'validation_positive':positive[1],
                     'test_positive':positive[2],
                     'train_label_unmatured_excluded':dropped_train,
                     'validation_label_unmatured_excluded':dropped_valid,
                     'validation_start':vstart.isoformat(),'test_start':tstart.isoformat(),
                     'no_random_shuffle':True,'no_batch_overlap':'batch_key' in frame}}
