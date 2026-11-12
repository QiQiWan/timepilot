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
