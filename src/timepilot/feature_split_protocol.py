"""Deterministic, leakage-aware expanding-window split manifests."""
from __future__ import annotations
import numpy as np
import pandas as pd
from timepilot.feature_contracts import digest


def build_folds(data, *, time_column, n_splits=3, purge_rows=0, group_columns=()):
    """Return original row indices. Validation only uses later, disjoint times.

    For grouped mode, whole groups are held out. Train groups are purged if
    they contain any observations at or after the validation's earliest time.
    This conservative rule may reject heavily overlapping batches.
    """
    if time_column not in data:raise ValueError('没有可核实的预测时间列')
    times=pd.to_datetime(data[time_column],utc=True,errors='coerce')
    if times.isna().any():raise ValueError('决策时间存在空值或非法日期')
    if group_columns:
        for col in group_columns:
            if col not in data:raise ValueError('分组字段不存在：'+col)
        table=pd.DataFrame({'__index':np.arange(len(data)),'__time':times})
        keys=pd.Series(list(zip(*(data[c].astype(str) for c in group_columns))),index=data.index)
        table['__group']=keys.to_numpy()
        group_finish=table.groupby('__group')['__time'].max().sort_values()
        groups=list(group_finish.index)
        if len(groups)<n_splits+1:raise ValueError('独立设备/批次组数不足以按组留出')
        chunks=np.array_split(np.arange(len(groups)),n_splits+1)
        folds=[]
        for fold in range(n_splits):
            gtrain={groups[k] for chunk in chunks[:fold+1] for k in chunk}
            gval={groups[k] for k in chunks[fold+1]}
            valid=table.loc[table['__group'].isin(gval),'__index'].to_numpy(dtype=int)
            if not len(valid):continue
            earliest=times.iloc[valid].min()
            train=table.loc[table['__group'].isin(gtrain)&(table['__time']<earliest),'__index'].to_numpy(dtype=int)
            train=train[np.argsort(times.iloc[train].to_numpy())]
            train=train[:-purge_rows] if purge_rows else train
            valid=valid[np.argsort(times.iloc[valid].to_numpy())]
            if not len(train):continue
            assert not (set(keys.iloc[train]) & set(keys.iloc[valid]))
            folds.append((train,valid))
    else:
        sorted_idx=np.argsort(times.to_numpy(),kind='stable')
        # Keep equal-time observations together to avoid one event appearing
        # in both sides. Small numbers of unique times are refused.
        sorted_unique=pd.Series(times).drop_duplicates().sort_values().to_list()
        if len(sorted_unique)<(n_splits+1)*2:raise ValueError('独立预测时点数量不足')
        chunks=np.array_split(np.arange(len(sorted_unique)),n_splits+1)
        folds=[]
        for fold in range(n_splits):
            train_cutoff=sorted_unique[chunks[fold][-1]]
            val_min=sorted_unique[chunks[fold+1][0]]
            val_max=sorted_unique[chunks[fold+1][-1]]
            train=np.where(times<=train_cutoff)[0]
            valid=np.where((times>=val_min)&(times<=val_max))[0]
            train=train[np.argsort(times.iloc[train].to_numpy())]
            valid=valid[np.argsort(times.iloc[valid].to_numpy())]
            if purge_rows:train=train[:-purge_rows]
            if not len(train) or not len(valid):continue
            folds.append((train,valid))
    if len(folds)<n_splits:raise ValueError('组隔离/时间隔离后样本不足，无法形成全部验证折')
    for a,b in folds:
        assert times.iloc[a].max()<times.iloc[b].min()
    manifest=[{'fold':i,'train_count':len(a),'valid_count':len(b),
               'train_end':times.iloc[a].max().isoformat(),'validation_start':times.iloc[b].min().isoformat(),
               'train_rows_sha256':digest(a.tolist()),'valid_rows_sha256':digest(b.tolist())}
              for i,(a,b) in enumerate(folds)]
    return folds,manifest


def build_static_folds(data, labels, *, split_kind, n_splits=3, seed=42,
                       group_columns=()):
    """Deterministic static K folds with explicit group/stratification semantics.

    Rows remain source-position indices; no timestamps are synthesized. All
    returned partitions are disjoint and form full validation coverage.
    """
    from sklearn.model_selection import KFold, StratifiedKFold, GroupKFold, StratifiedGroupKFold
    if split_kind not in ('kfold','stratified','group'):
        raise ValueError('未知静态验证方式')
    if len(data)<n_splits*15:
        raise ValueError('静态数据不足以构建所有验证折（每折至少15条验证记录）')
    if split_kind=='group':
        if not group_columns:
            raise ValueError('分组切分缺少设备或批次字段')
        for col in group_columns:
            if col not in data:
                raise ValueError('分组字段不存在：'+col)
            if data[col].isna().any():
                raise ValueError('设备或批次键存在缺失，不能验证组隔离')
        groups=list(zip(*(data[c].astype(str).to_numpy() for c in group_columns)))
        # Assign deterministic integer identifiers; avoid tuple/np mixed dtypes.
        ids=pd.factorize(pd.Series(groups,dtype=object),sort=True)[0]
        if len(set(ids))<n_splits:
            raise ValueError('独立设备/批次数小于验证折数')
        # Classification benefits from balanced group-disjoint folds. Both
        # train and validation class coverage are checked below.
        if set(np.unique(labels)).issubset({0.,1.}) and len(np.unique(labels))==2:
            cv=StratifiedGroupKFold(n_splits=n_splits,shuffle=True,random_state=seed)
            iterator=cv.split(np.zeros(len(data)),labels,groups=ids)
        else:
            cv=GroupKFold(n_splits=n_splits)
            iterator=cv.split(np.zeros(len(data)),labels,groups=ids)
    elif split_kind=='stratified':
        if group_columns:
            raise ValueError('静态分层切分不能忽略设备或批次分组')
        counts=np.unique(labels,return_counts=True)[1]
        if len(counts)!=2 or min(counts)<n_splits:
            raise ValueError('少数类样本数不足以分配到全部验证折')
        cv=StratifiedKFold(n_splits=n_splits,shuffle=True,random_state=seed)
        iterator=cv.split(np.zeros(len(data)),labels)
        ids=None
    else:
        if group_columns:
            raise ValueError('静态随机 K 折不能忽略已确认分组')
        cv=KFold(n_splits=n_splits,shuffle=True,random_state=seed)
        iterator=cv.split(np.zeros(len(data)))
        ids=None
    folds=[];manifest=[]
    coverage=np.zeros(len(data),dtype=int)
    for fold,(a,b) in enumerate(iterator):
        a=np.asarray(a,dtype=int);b=np.asarray(b,dtype=int)
        if set(a)&set(b) or len(a)<40 or len(b)<15:
            raise ValueError('静态切分训练/验证存在交集或有效样本不足（40/15）')
        if ids is not None and set(ids[a])&set(ids[b]):
            raise ValueError('静态验证的设备或批次发生训练/验证交叉')
        if split_kind in ('stratified','group') and len(np.unique(labels))==2:
            if len(np.unique(np.asarray(labels)[a]))<2 or len(np.unique(np.asarray(labels)[b]))<2:
                raise ValueError('分层/分组验证折无法覆盖正负两类，请调整分组或收集更多标签')
        coverage[b]+=1
        folds.append((a,b))
        manifest.append({'fold':fold,'split_kind':split_kind,'seed':int(seed),
                         'train_count':len(a),'valid_count':len(b),
                         'train_end':None,'validation_start':None,
                         'train_rows_sha256':digest(a.tolist()),
                         'valid_rows_sha256':digest(b.tolist()),
                         'disjoint_verified':True,
                         'group_disjoint_verified':ids is not None})
    if not np.all(coverage==1):
        raise ValueError('静态 K 折验证集必须恰好覆盖每条记录一次')
    return folds,manifest
