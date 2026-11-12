"""Audited, project-scoped soft deletion for unified generic data assets.

Only catalogue visibility is changed. Original data, model snapshots, shared
files and old lineage remain immutable. Purging physical bytes is intentionally
handled by the separate storage manager after independent reference checks.
"""
from __future__ import annotations
import json
from datetime import datetime,timedelta,timezone
from uuid import uuid4
from timepilot.enterprise_security import CURRENT_PRINCIPAL


def _now():return datetime.now(timezone.utc).isoformat()

class AssetLifecycleError(ValueError): pass
class AssetLifecycleConflict(AssetLifecycleError): pass


class AssetLifecycle:
    def __init__(self,projects,platform):
        self.projects,self.platform=projects,platform

    def _principal(self,pid,write=False):
        if write:self.projects.writable(pid)
        else:self.projects.get(pid)
        actor=CURRENT_PRINCIPAL.get()
        if write and actor is not None and self.projects.security is not None:
            self.projects.security.allowed_project(actor,pid,required='editor')
        return actor.user_id if actor else 'local'

    def _row(self,db,pid,aid):
        asset=db.execute('SELECT * FROM unified_assets WHERE project_id=? AND asset_id=?',(pid,aid)).fetchone()
        if not asset:raise KeyError('资产不存在或无项目权限')
        lifecycle=db.execute('SELECT * FROM asset_lifecycle WHERE project_id=? AND asset_id=?',(pid,aid)).fetchone()
        return dict(asset),dict(lifecycle) if lifecycle else None

    def status(self,pid,aid):
        self._principal(pid)
        with self.projects._db() as db:
            asset,row=self._row(db,pid,aid)
            return {'asset_id':aid,'name':asset['name'],'state':row['state'] if row else 'active',
                    'revision':row['revision'] if row else 0,
                    'trashed_at':row['trashed_at'] if row else None,
                    'retention_until':row['retention_until'] if row else None}

    @staticmethod
    def _exists(db,table):
        return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone() is not None

    def impact(self,pid,aid):
        self._principal(pid)
        with self.projects._db() as db:
            self._row(db,pid,aid)
            vers=[dict(x) for x in db.execute('SELECT native_asset_id,version_id,data_sha256 FROM unified_versions WHERE project_id=? AND asset_id=?',(pid,aid))]
            nids=[v['native_asset_id'] for v in vers]
            refs=[]
            def collect(table,id_column,kind,extra='',args=()):
                if not self._exists(db,table):return
                for nid in nids:
                    query=f'SELECT {id_column} AS id FROM {table} WHERE project_id=? AND asset_id=?'+extra
                    for row in db.execute(query,(pid,nid,*args)):
                        refs.append({'kind':kind,'id':row['id'],'asset_id':nid})
            collect('project_model_drafts','draft_id','model_draft')
            collect('feature_sets','set_id','feature_set_v2')
            collect('project_bi_views','view_id','saved_view')
            collect('project_forecast_drafts','draft_id','forecast_draft')
            # Also capture unified selections, agent workspaces and task status.
            for nid in nids:
                if self._exists(db,'unified_selections'):
                    refs += [{'kind':'frozen_selection','id':r['selection_id'],'asset_id':nid} for r in db.execute('SELECT selection_id FROM unified_selections WHERE project_id=? AND native_asset_id=?',(pid,nid))]
                if self._exists(db,'bi_agent_workspaces'):
                    refs += [{'kind':'agent_workspace','id':r['workspace_id'],'asset_id':nid} for r in db.execute('SELECT workspace_id FROM bi_agent_workspaces WHERE project_id=? AND asset_id=?',(pid,nid))]
            blockers=[]
            # Pending model jobs may refer to the asset while materializing.
            if self._exists(db,'project_model_drafts'):
                for nid in nids:
                    for r in db.execute("SELECT draft_id,status FROM project_model_drafts WHERE project_id=? AND asset_id=?",(pid,nid)):
                        if r['status'] in {'queued','running','publishing'}:
                            blockers.append({'kind':'model_running','id':r['draft_id']})
            from timepilot.asset_references import blockers as active_references, referenced_imports
            blockers=active_references(db,pid,aid,nids,referenced_imports(db,pid,nids))
            return {'asset_id':aid,'versions':vers,'dependencies':refs,
                    'active_blockers':blockers,'can_trash':not blockers,
                    'note':'移入回收站仅隐藏项目目录；原文件、版本、保存视图与模型引用保持不变'}

    def change(self,pid,aid,action,*,request_id,expected_revision=None,confirm=False,reason=''):
        if action not in {'trash','restore'}:raise AssetLifecycleError('未知资产生命周期操作')
        actor=self._principal(pid,write=True)
        if action=='trash' and not confirm:raise AssetLifecycleError('移入回收站需要明确确认')
        if len(str(request_id))<8:raise AssetLifecycleError('请求 ID 无效')
        # A retry of an already committed request must remain idempotent even
        # when a new model task has begun since its first execution.
        with self.projects._db() as db:
            previous=db.execute('''SELECT * FROM asset_lifecycle_events WHERE project_id=? AND asset_id=? AND request_id=?''',
                                (pid,aid,request_id)).fetchone()
            if previous:
                self._row(db,pid,aid)
                return {'asset_id':aid,'state':previous['after_state'],
                        'current_state':self._row(db,pid,aid)[1]['state'],
                        'event_id':previous['event_id'],'duplicate':True,
                        'files_deleted':False}
        # A dependency snapshot is advisory; the write lock below repeats the
        # same check, and physical data is never removed by this method.
        impact=self.impact(pid,aid)
        if action=='trash' and impact['active_blockers']:
            raise AssetLifecycleConflict('仍有运行中的模型或发布任务引用该资产，无法回收')
        now=_now();next_state='trashed' if action=='trash' else 'active'
        with self.projects._lock,self.projects._db() as db:
            previous=db.execute('SELECT * FROM asset_lifecycle_events WHERE project_id=? AND asset_id=? AND request_id=?',(pid,aid,request_id)).fetchone()
            if previous:
                row=self._row(db,pid,aid)[1]
                return {'asset_id':aid,'state':previous['after_state'],
                        'current_state':row['state'],'revision':row['revision'],
                        'event_id':previous['event_id'],'duplicate':True,'files_deleted':False}
            asset,row=self._row(db,pid,aid)
            old=row['state'] if row else 'active';rev=row['revision'] if row else 0
            if expected_revision is not None and expected_revision!=rev:
                raise AssetLifecycleConflict('资产回收站状态已变化，请刷新后重试')
            if old==next_state:raise AssetLifecycleConflict('资产已处于目标状态，请刷新目录')
            if action=='trash':
                from timepilot.asset_references import blockers as active_references, referenced_imports
                nids=[v['native_asset_id'] for v in impact['versions']]
                active=active_references(db,pid,aid,nids,referenced_imports(db,pid,nids))
                if active:raise AssetLifecycleConflict('当前存在运行中的模型、摘要、发布或分析任务，请先结束任务')
            event_id=str(uuid4());until=(datetime.now(timezone.utc)+timedelta(days=30)).isoformat() if action=='trash' else None
            db.execute('''INSERT INTO asset_lifecycle(project_id,asset_id,state,trashed_at,trashed_by,
               restored_at,restored_by,retention_until,reason,revision,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(project_id,asset_id) DO UPDATE SET
               state=excluded.state,trashed_at=excluded.trashed_at,trashed_by=excluded.trashed_by,
               restored_at=excluded.restored_at,restored_by=excluded.restored_by,
               retention_until=excluded.retention_until,reason=excluded.reason,
               revision=excluded.revision,updated_at=excluded.updated_at''',
               (pid,aid,next_state,now if action=='trash' else None,actor if action=='trash' else None,
                now if action=='restore' else None,actor if action=='restore' else None,
                until,str(reason)[:500],rev+1,now))
            db.execute('''INSERT INTO asset_lifecycle_events VALUES(?,?,?,?,?,?,?,?,?,?)''',
                (event_id,pid,aid,action,actor,request_id,old,next_state,
                 json.dumps(impact,ensure_ascii=False),now))
            db.execute('UPDATE unified_assets SET updated_at=? WHERE project_id=? AND asset_id=?',(now,pid,aid))
        return {'asset_id':aid,'state':next_state,'revision':rev+1,'event_id':event_id,
                'duplicate':False,'files_deleted':False}

    def search(self,pid,*,q='',state='active',page=1,page_size=30,sort='updated_desc'):
        self._principal(pid)
        if state not in {'active','trashed','all'}:raise AssetLifecycleError('未知资产状态')
        if sort not in {'updated_desc','updated_asc','name_asc','name_desc'}:raise AssetLifecycleError('未知排序方式')
        if not 1<=page<=100000 or not 1<=page_size<=100:raise AssetLifecycleError('分页参数超出范围')
        # SQL index is the hot path for 10k+ asset paging. A cold/upgraded
        # catalogue is synchronized only when a linked native version lacks
        # an indexed logical asset. Do not deserialize all Parquet metadata on
        # every search keystroke or page navigation.
        with self.projects._db() as db:
            needs_sync=db.execute('''SELECT 1 FROM project_generic_assets p
                LEFT JOIN unified_versions v ON v.project_id=p.project_id
                    AND v.native_asset_id=p.native_asset_id AND v.version_id=p.version_id
                LEFT JOIN asset_search_index s ON s.project_id=v.project_id AND s.asset_id=v.asset_id
                WHERE p.project_id=? AND (v.asset_id IS NULL OR s.asset_id IS NULL) LIMIT 1''',(pid,)).fetchone()
        if needs_sync:
            self.platform.list_assets(pid,include_trashed=True)
        clause=['a.project_id=?'];args=[pid]
        if state!='all':
            clause.append("COALESCE(l.state,'active')=?");args.append(state)
        if q.strip():
            term='%'+q.strip().lower().replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%'
            clause.append("(LOWER(a.name) LIKE ? ESCAPE '\\' OR LOWER(COALESCE(s.source_file,'')) LIKE ? ESCAPE '\\' OR LOWER(COALESCE(s.source_sheet,'')) LIKE ? ESCAPE '\\' OR LOWER(COALESCE(s.fields_json,'')) LIKE ? ESCAPE '\\')")
            args.extend([term]*4)
        where=' AND '.join(clause)
        ordering={'updated_desc':'a.updated_at DESC,a.asset_id','updated_asc':'a.updated_at ASC,a.asset_id',
                  'name_asc':'a.name COLLATE NOCASE ASC,a.asset_id','name_desc':'a.name COLLATE NOCASE DESC,a.asset_id'}[sort]
        sql=' FROM unified_assets a LEFT JOIN asset_lifecycle l ON l.project_id=a.project_id AND l.asset_id=a.asset_id LEFT JOIN asset_search_index s ON s.project_id=a.project_id AND s.asset_id=a.asset_id WHERE '+where
        with self.projects._db() as db:
            total=db.execute('SELECT COUNT(*)'+sql,args).fetchone()[0]
            rows=[dict(r) for r in db.execute('''SELECT a.asset_id,a.name,a.created_at,a.updated_at,
                 COALESCE(l.state,'active') AS state, COALESCE(l.revision,0) AS lifecycle_revision,
                 COALESCE(s.source_file,'') AS source_file, COALESCE(s.source_sheet,'') AS source_sheet'''+sql+
                 ' ORDER BY '+ordering+' LIMIT ? OFFSET ?',(*args,page_size,(page-1)*page_size))]
            for row in rows:
                ver=db.execute('''SELECT native_asset_id,version_id,row_count,column_count,data_sha256 FROM unified_versions
                    WHERE project_id=? AND asset_id=? ORDER BY created_at DESC LIMIT 1''',(pid,row['asset_id'])).fetchone()
                if ver:row.update(dict(ver))
        return {'items':rows,'total':total,'page':page,'page_size':page_size,
                'next_page':page+1 if page*page_size<total else None,'state':state}

    def list_trash(self,pid,**kwargs):return self.search(pid,state='trashed',**kwargs)
