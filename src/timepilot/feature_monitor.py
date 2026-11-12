"""Lightweight, typed feature monitoring based on comparable full-scope profiles.

No model-generated metrics: every comparison is between actual profile facts.
A distribution warning is descriptive, never a claim of causal model drift.
"""
from __future__ import annotations
import json
from datetime import datetime,timezone
from uuid import uuid4
from timepilot.feature_contracts import canonical,digest
from timepilot.projects import now


def compare_profiles(reference:dict,current:dict,*,missing_rate_delta=0.05,mean_std_threshold=2.0):
    if reference.get('scope')!='full' or current.get('scope')!='full':
        raise ValueError('监控基线与当前画像都必须是全量范围，不能将抽样统计冒充全量')
    if reference.get('asset_id')!=current.get('asset_id'):
        raise ValueError('两个画像必须来自相同逻辑数据资产')
    left={f['field_id']:f for f in reference.get('fields',[])}
    right={f['field_id']:f for f in current.get('fields',[])}
    right_names={f['source_name']:f for f in current.get('fields',[])}
    if not left or not right:raise ValueError('画像缺少可比较字段')
    changes=[]
    for fid,old in left.items():
        new=right.get(fid) or right_names.get(old.get('source_name'))
        if not new:
            changes.append({'field_id':fid,'kind':'field_removed','severity':'warn'})
            continue
        loss = (new.get('null_rate') or 0) - (old.get('null_rate') or 0)
        if abs(loss)>=missing_rate_delta:
            changes.append({'field_id':fid,'kind':'missing_rate_shift','severity':'warn',
                            'reference':old.get('null_rate'),'current':new.get('null_rate'),'delta':loss})
        std=old.get('std')
        if std is not None and std>1e-12 and old.get('mean') is not None and new.get('mean') is not None:
            z=(new['mean']-old['mean'])/std
            if abs(z)>=mean_std_threshold:
                changes.append({'field_id':fid,'kind':'mean_shift_descriptive','severity':'warn',
                                'reference':old['mean'],'current':new['mean'],'z_score':z})
    old_names={f.get('source_name') for f in left.values()}
    for fid,field in right.items():
        if fid not in left and field.get('source_name') not in old_names:
            changes.append({'field_id':fid,'kind':'field_added','severity':'info'})
    return {'status':'compared','scope':'full','baseline_source_sha256':reference.get('source_sha256'),
            'current_source_sha256':current.get('source_sha256'),
            'baseline_rows':reference.get('rows_scanned'),'current_rows':current.get('rows_scanned'),
            'warnings':changes,'warning_count':len(changes),
            'interpretation':'描述性差异，未验证模型性能影响或统计显著性'}


class FeatureMonitor:
    def __init__(self,projects):self.projects=projects

    def record(self,pid,ref_profile,current_profile):
        self.projects.get(pid)
        with self.projects._db() as db:
            rows=db.execute('SELECT profile_id,result_json FROM feature_profiles WHERE project_id=? AND profile_id IN (?,?)',
                            (pid,ref_profile,current_profile)).fetchall()
        mapped={r['profile_id']:json.loads(r['result_json']) for r in rows}
        if ref_profile not in mapped or current_profile not in mapped:raise KeyError('画像不存在或无权限')
        report=compare_profiles(mapped[ref_profile],mapped[current_profile]);sid=str(uuid4())
        with self.projects._lock,self.projects._db() as db:
            db.execute('''INSERT INTO feature_monitor_snapshots
                (snapshot_id,project_id,reference_profile_id,current_profile_id,report_json,created_at)
                VALUES (?,?,?,?,?,?)''',(sid,pid,ref_profile,current_profile,canonical(report),now()))
        return {'snapshot_id':sid,'report':report}

    def get(self,pid,snapshot_id):
        self.projects.get(pid)
        with self.projects._db() as db:
            row=db.execute('SELECT * FROM feature_monitor_snapshots WHERE project_id=? AND snapshot_id=?',
                           (pid,snapshot_id)).fetchone()
        if row is None:raise KeyError('监控记录不存在或无权限')
        return {'snapshot_id':snapshot_id,'report':json.loads(row['report_json']),'created_at':row['created_at']}
