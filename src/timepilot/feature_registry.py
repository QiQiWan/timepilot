"""FeatureSet v2 versioning, independent approval and immutable publication."""
from __future__ import annotations
import json
from uuid import uuid4
from timepilot.feature_contracts import FeatureSpecV2, canonical, digest
from timepilot.feature_dag import ordered_features
from timepilot.feature_semantics import FeatureSemanticService
from timepilot.feature_lab import FeatureLab
from timepilot.projects import now
from timepilot.enterprise_security import SecurityError,CURRENT_PRINCIPAL


class FeatureRegistry:
    def __init__(self,projects,native,semantics):
        self.projects=projects;self.native=native;self.semantics=semantics
        self.legacy=FeatureLab(projects,native)

    def _editor(self,pid):self.legacy._editor(pid)
    def _reviewer(self,pid,creator):self.legacy._reviewer(pid,creator)

    @staticmethod
    def _actor():return FeatureLab._actor()

    def _event(self,db,pid,sid,action,evidence):
        db.execute('''INSERT INTO feature_set_events
          (event_id,set_id,project_id,actor_id,event_type,evidence_sha256,created_at)
          VALUES (?,?,?,?,?,?,?)''',
          (str(uuid4()),sid,pid,self._actor(),action,digest(evidence),now()))

    def validate(self,pid,spec:FeatureSpecV2):
        source=self.semantics.source(pid,spec.source.asset_id,spec.source.version_id)
        if source['sha256']!=spec.source.source_sha256:raise ValueError('源数据版本已变化')
        if spec.contract_revision==0:
            contract=self.semantics.latest(pid,spec.source.asset_id,spec.source.version_id,source=source)
            if contract.revision!=0:raise ValueError('已有正式字段契约，请选择其当前修订')
        else:
            contract=self.semantics.get_revision(pid,spec.source.asset_id,spec.source.version_id,spec.contract_revision)
        ordered_features(spec,contract)
        from timepilot.feature_compiler import FeatureCompiler
        FeatureCompiler(contract,spec)
        return {'status':'ready','feature_count':len(spec.features),'source_sha256':source['sha256'],
                'graph_sha256':digest(spec),'contract_revision':contract.revision,
                'time_semantics':'confirmed' if contract.event_time_field_id else 'source_record_order',
                'mode':spec.task.mode if spec.task else 'exploration'}

    def create(self,pid,spec:FeatureSpecV2,request_id):
        self._editor(pid)
        if not request_id or len(request_id)>120:raise ValueError('创建特征集必须提供有效请求 ID')
        with self.projects._db() as db:
            old=db.execute('SELECT set_id,spec_sha256 FROM feature_sets WHERE project_id=? AND owner_id=? AND request_id=?',
                           (pid,self._actor(),request_id)).fetchone()
        if old:
            if old['spec_sha256']!=digest(spec):
                raise ValueError('同一请求 ID 已绑定不同的特征配方')
            return self.get(pid,old['set_id'])
        self.validate(pid,spec)
        sid=str(uuid4());at=now()
        with self.projects._lock,self.projects._db() as db:
            db.execute('''INSERT INTO feature_sets
            (set_id,project_id,owner_id,name,asset_id,version_id,source_sha256,contract_revision,
             revision,spec_json,spec_sha256,request_id,state,created_at,updated_at)
             VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
             (sid,pid,self._actor(),spec.name,spec.source.asset_id,spec.source.version_id,
              spec.source.source_sha256,spec.contract_revision,1,canonical(spec),digest(spec),
              request_id,'draft',at,at))
            self._write_definitions(db,sid,spec)
            self._save_revision(db,pid,sid,1,spec)
            self._event(db,pid,sid,'create',{'spec_sha':digest(spec)})
        return self.get(pid,sid)

    def _save_revision(self,db,pid,sid,revision,spec):
        db.execute('''INSERT INTO feature_set_revisions
             (set_id,project_id,revision,spec_json,spec_sha256,contract_revision,actor_id,created_at)
             VALUES (?,?,?,?,?,?,?,?)''',
             (sid,pid,revision,canonical(spec),digest(spec),spec.contract_revision,self._actor(),now()))

    def list_revisions(self,pid,sid):
        self.get(pid,sid)
        with self.projects._db() as db:
            rows=db.execute('''SELECT revision,spec_sha256,contract_revision,actor_id,created_at
                FROM feature_set_revisions WHERE project_id=? AND set_id=? ORDER BY revision DESC''',
                (pid,sid)).fetchall()
        return {'set_id':sid,'revisions':[dict(r) for r in rows],
                'note':'迁移前被覆盖且没有留存快照的历史修订无法恢复'}

    def get_revision(self,pid,sid,revision):
        self.get(pid,sid)
        with self.projects._db() as db:
            row=db.execute('''SELECT * FROM feature_set_revisions
                WHERE project_id=? AND set_id=? AND revision=?''',(pid,sid,revision)).fetchone()
        if row is None:raise KeyError('该历史修订没有完整快照，不能还原')
        data=dict(row);data['spec']=json.loads(data.pop('spec_json'))
        if digest(data['spec'])!=data['spec_sha256']:
            raise ValueError('历史特征配方快照 SHA-256 不一致')
        return data

    def diff_revisions(self,pid,sid,left,right):
        a=self.get_revision(pid,sid,left);b=self.get_revision(pid,sid,right)
        fa={x['feature_id']:x for x in a['spec']['features']}
        fb={x['feature_id']:x for x in b['spec']['features']}
        return {'set_id':sid,'from_revision':left,'to_revision':right,
          'added':[fb[k] for k in sorted(fb.keys()-fa.keys())],
          'removed':[fa[k] for k in sorted(fa.keys()-fb.keys())],
          'changed':[{'before':fa[k],'after':fb[k]} for k in sorted(fa.keys()&fb.keys()) if fa[k]!=fb[k]],
          'task_changed':a['spec'].get('task')!=b['spec'].get('task')}

    @staticmethod
    def _write_definitions(db,sid,spec):
        db.execute('DELETE FROM feature_dependencies WHERE set_id=?',(sid,))
        db.execute('DELETE FROM feature_definitions WHERE set_id=?',(sid,))
        for f in spec.features:
            db.execute('''INSERT INTO feature_definitions
                 (set_id,feature_id,operator_id,output_name,definition_json)
                 VALUES(?,?,?,?,?)''',(sid,f.feature_id,f.operator_id,f.output_name,canonical(f)))
            for dep in f.input_field_ids:
                db.execute('''INSERT INTO feature_dependencies (set_id,feature_id,dependency_id)
                    VALUES (?,?,?)''',(sid,f.feature_id,dep))

    def get(self,pid,sid):
        self.projects.get(pid)
        with self.projects._db() as db:
            row=db.execute('SELECT * FROM feature_sets WHERE project_id=? AND set_id=?',(pid,sid)).fetchone()
        if row is None:raise KeyError('特征集不存在或无权限')
        result=dict(row);result['spec']=json.loads(result.pop('spec_json'))
        return result

    def list(self,pid,aid=None,state=None):
        self.projects.get(pid)
        query='SELECT set_id FROM feature_sets WHERE project_id=?';params=[pid]
        if aid:query+=' AND asset_id=?';params.append(aid)
        if state:query+=' AND state=?';params.append(state)
        query+=' ORDER BY updated_at DESC LIMIT 200'
        with self.projects._db() as db:
            rows=db.execute(query,params).fetchall()
        return [self.get(pid,r['set_id']) for r in rows]

    def update(self,pid,sid,new:FeatureSpecV2,expected_revision):
        self._editor(pid);record=self.get(pid,sid)
        if record['owner_id']!=self._actor():raise SecurityError('只能修改本人创建的草稿')
        if record['state']!='draft' or record['revision']!=expected_revision:
            raise ValueError('特征集状态或版本冲突')
        if new.source.model_dump()!=FeatureSpecV2.model_validate(record['spec']).source.model_dump():
            raise ValueError('修改特征集不得更换数据来源')
        self.validate(pid,new)
        with self.projects._lock,self.projects._db() as db:
            changed=db.execute('''UPDATE feature_sets SET name=?,revision=revision+1,spec_json=?,
                    spec_sha256=?,updated_at=? WHERE project_id=? AND set_id=? AND state='draft' AND revision=?''',
                (new.name,canonical(new),digest(new),now(),pid,sid,expected_revision))
            if changed.rowcount!=1:raise ValueError('并发更新冲突')
            self._write_definitions(db,sid,new)
            self._save_revision(db,pid,sid,expected_revision+1,new)
            self._event(db,pid,sid,'update',{'revision':expected_revision+1,'sha':digest(new)})
        return self.get(pid,sid)

    def pinned(self,pid,record):
        spec=FeatureSpecV2.model_validate(record['spec'])
        if digest(spec)!=record['spec_sha256']:raise ValueError('特征定义已被篡改')
        self.validate(pid,spec)
        return spec

    def request_review(self,pid,sid):
        self._editor(pid);rec=self.get(pid,sid);self.pinned(pid,rec)
        if rec['owner_id']!=self._actor():raise SecurityError('只能提交本人草稿')
        with self.projects._lock,self.projects._db() as db:
            if db.execute('''UPDATE feature_sets SET state='pending_review',updated_at=?
                  WHERE project_id=? AND set_id=? AND state='draft' ''',(now(),pid,sid)).rowcount!=1:
                raise ValueError('当前状态不能提交审核')
            self._event(db,pid,sid,'request_review',{'revision':rec['revision']})
        return self.get(pid,sid)

    def approve(self,pid,sid,reason,confirm):
        rec=self.get(pid,sid);self._reviewer(pid,rec['owner_id']);self.pinned(pid,rec)
        if not confirm or not reason.strip():raise ValueError('审核必须提供意见与明确确认')
        with self.projects._lock,self.projects._db() as db:
            if db.execute('''UPDATE feature_sets SET state='approved',approval_actor=?,approval_note=?,updated_at=?
                  WHERE project_id=? AND set_id=? AND state='pending_review' ''',
                  (self._actor(),reason.strip(),now(),pid,sid)).rowcount!=1:
                raise ValueError('审核状态发生冲突')
            self._event(db,pid,sid,'approve',{'reason':reason.strip(),'spec_sha':rec['spec_sha256']})
        return self.get(pid,sid)

    def publication_preview(self,pid,sid):
        rec=self.get(pid,sid);self.pinned(pid,rec)
        from timepilot.feature_artifact_policy import eligible_artifact
        candidate=eligible_artifact(self.projects,pid,rec,self.semantics)
        return {'set_id':sid,'state':rec['state'],'approved':rec['state'] in ('approved','published'),
                'artifact_ready':candidate is not None,
                'artifact_id':candidate['artifact']['artifact_id'] if candidate else None,
                'artifact_gate':'verified' if candidate else 'no_successful_verified_artifact',
                'requires_human_confirmation':True,'source_sha256':rec['source_sha256'],
                'feature_spec_sha256':rec['spec_sha256']}

    def publish(self,pid,sid,confirm=False):
        self._editor(pid)
        rec=self.get(pid,sid)
        if rec['state']=='published':return rec
        if not confirm or rec['state']!='approved':raise ValueError('发布要求审核通过并由用户明确确认')
        if FeatureSpecV2.model_validate(rec['spec']).selection_policy!='full':
            raise ValueError('只允许发布已完整核验的全量特征集，不允许发布预览选区')
        self.pinned(pid,rec)
        preview=self.publication_preview(pid,sid)
        if not preview['artifact_ready']:raise ValueError('必须先生成并校验全量特征计算结果')
        from timepilot.feature_execution import FeatureJobEngine
        artifact_id=preview['artifact_id']
        from timepilot.feature_artifact_policy import eligible_artifact
        verified=eligible_artifact(self.projects,pid,rec,self.semantics,artifact_id=artifact_id)
        if verified is None:raise ValueError('物化任务状态或源文件已变化，拒绝发布')
        source_path=verified['path'];a=verified['artifact']
        lineage={'type':'feature_lab_v2','project_id':pid,'feature_set_id':sid,
            'revision':rec['revision'],'source_sha256':rec['source_sha256'],
            'spec_sha256':rec['spec_sha256'],'artifact_sha256':a['sha256']}
        # If import completed before a crash, only reuse verified matching lineage.
        existing=None
        from sqlalchemy import select
        from timepilot.db import NativeBIAssetRow
        with self.native.store.sessions() as session:
            for x in session.scalars(select(NativeBIAssetRow).where(NativeBIAssetRow.lineage_json.contains(sid))).all():
                m=self.native._metadata(x)
                if m.get('lineage',{}).get('feature_set_id')==sid:
                    existing=m;break
        if existing:
            if existing.get('lineage')!=lineage:raise ValueError('已存在不同版本的发布资产，拒绝覆盖')
            asset=existing
        else:
            asset=self.native.import_from_path(source_path,rec['name']+' · 特征集',lineage)
        if not self.native.verify(asset['asset_id']).get('valid'):
            raise ValueError('导出特征资产无法通过完整性验证')
        manifest={'schema_version':1,'set_id':sid,'revision':rec['revision'],'source_sha256':rec['source_sha256'],
                  'feature_spec_sha256':rec['spec_sha256'],'artifact_id':artifact_id,
                  'artifact_sha256':a['sha256'],'published_asset_id':asset['asset_id'],
                  'reviewer':rec['approval_actor'],'review_note':rec['approval_note']}
        with self.projects._lock,self.projects._db() as db:
            db.execute('''INSERT OR IGNORE INTO project_native_assets
                (project_id,native_asset_id,source_asset_id,created_at) VALUES(?,?,?,?)''',
                (pid,asset['asset_id'],rec['asset_id'],now()))
            db.execute('''INSERT OR IGNORE INTO feature_publications
                (publication_id,project_id,set_id,artifact_id,published_asset_id,manifest_sha256,
                 manifest_json,reviewer_id,created_at) VALUES(?,?,?,?,?,?,?,?,?)''',
                (str(uuid4()),pid,sid,artifact_id,asset['asset_id'],digest(manifest),canonical(manifest),
                 rec['approval_actor'],now()))
            if db.execute('''UPDATE feature_sets SET state='published',published_asset_id=?,updated_at=?
                 WHERE project_id=? AND set_id=? AND state='approved' ''',
                 (asset['asset_id'],now(),pid,sid)).rowcount!=1:
                raise ValueError('特征发布状态冲突')
            self._event(db,pid,sid,'publish',manifest)
        return self.get(pid,sid)

    def lineage(self,pid,sid):
        rec=self.get(pid,sid)
        return {'set_id':sid,'asset_id':rec['asset_id'],'version_id':rec['version_id'],
                'source_sha256':rec['source_sha256'],'spec_sha256':rec['spec_sha256'],
                'features':[{'feature_id':x['feature_id'],'operator':x['operator_id'],
                             'inputs':x['input_field_ids']} for x in rec['spec']['features']],
                'published_asset_id':rec['published_asset_id']}
