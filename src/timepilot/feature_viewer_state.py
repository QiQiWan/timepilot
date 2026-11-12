'Project-scoped FeatureLab Perspective workspace snapshots (layout/config only).'
from __future__ import annotations
from timepilot.feature_contracts import canonical,digest
from timepilot.projects import now


class FeatureViewerState:
    def __init__(self,projects,registry):
        self.projects=projects;self.registry=registry

    def _scoped(self,pid,sid):
        rec=self.registry.get(pid,sid)
        self.registry.pinned(pid,rec)
        return rec

    def get(self,pid,sid):
        rec=self._scoped(pid,sid)
        actor=self.registry._actor()
        with self.projects._db() as db:
            row=db.execute('''SELECT * FROM feature_lab_viewer_state
               WHERE project_id=? AND owner_id=? AND set_id=?''',(pid,actor,sid)).fetchone()
        if row is None:
            return {'set_id':sid,'viewer_revision':0,'source_sha256':rec['source_sha256'],
                    'workspace':None,'status':'empty'}
        import json
        data=dict(row)
        if (data['source_sha256']!=rec['source_sha256']
              or data['set_revision']!=rec['revision']
              or data['spec_sha256']!=rec['spec_sha256']):
            return {'set_id':sid,'viewer_revision':data['viewer_revision'],
                    'status':'stale','reason':'source_or_spec_revision_changed','workspace':None}
        if data['artifact_id']:
            from timepilot.feature_artifact_policy import eligible_artifact
            candidate=eligible_artifact(self.projects,pid,rec,self.registry.semantics,
                                        artifact_id=data['artifact_id'])
            if candidate is None or candidate['artifact']['sha256']!=data['artifact_sha256']:
                return {'set_id':sid,'viewer_revision':data['viewer_revision'],
                        'status':'stale','reason':'feature_artifact_no_longer_verified','workspace':None}
        return {'set_id':sid,'viewer_revision':data['viewer_revision'],
                'status':'ready','source_sha256':data['source_sha256'],
                'workspace':json.loads(data['viewer_json'])}

    def save(self,pid,sid,workspace,expected_revision,source_sha256):
        rec=self._scoped(pid,sid)
        if source_sha256!=rec['source_sha256']:
            raise ValueError('图表保存的来源指纹与特征集不一致')
        if expected_revision<0:raise ValueError('视图修订号非法')
        if not isinstance(workspace,dict) or len(canonical(workspace).encode())>256000:
            raise ValueError('Perspective 工作区快照必须为256KB以内的JSON对象')
        if not set(workspace).intersection(('layout','panels','masters','active')):
            raise ValueError('缺少真实 Perspective 工作区布局或面板字段')
        actor=self.registry._actor()
        from timepilot.feature_artifact_policy import eligible_artifact
        current_artifact=eligible_artifact(self.projects,pid,rec,self.registry.semantics)
        with self.projects._lock,self.projects._db() as db:
            row=db.execute('''SELECT viewer_revision FROM feature_lab_viewer_state
                 WHERE project_id=? AND owner_id=? AND set_id=?''',(pid,actor,sid)).fetchone()
            current=row['viewer_revision'] if row else 0
            if current!=expected_revision:raise ValueError('图表配置版本冲突，请读取最新视图后再保存')
            if row:
                db.execute('''UPDATE feature_lab_viewer_state SET viewer_revision=?,source_sha256=?,
                      viewer_json=?,updated_at=?,set_revision=?,spec_sha256=?,artifact_id=?,artifact_sha256=?
                      WHERE project_id=? AND owner_id=? AND set_id=?''',
                      (current+1,source_sha256,canonical(workspace),now(),rec['revision'],
                       rec['spec_sha256'],current_artifact['artifact']['artifact_id'] if current_artifact else None,
                       current_artifact['artifact']['sha256'] if current_artifact else None,pid,actor,sid))
            else:
                db.execute('''INSERT INTO feature_lab_viewer_state
                      (project_id,owner_id,set_id,viewer_revision,source_sha256,viewer_json,updated_at,
                      set_revision,spec_sha256,artifact_id,artifact_sha256)
                      VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                      (pid,actor,sid,1,source_sha256,canonical(workspace),now(),rec['revision'],
                       rec['spec_sha256'],current_artifact['artifact']['artifact_id'] if current_artifact else None,
                       current_artifact['artifact']['sha256'] if current_artifact else None))
        return {'set_id':sid,'viewer_revision':current+1,'status':'saved','workspace_sha256':digest(workspace)}
