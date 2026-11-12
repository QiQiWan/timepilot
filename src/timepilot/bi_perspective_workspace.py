"""User-scoped, revisioned Perspective v5 workspace snapshots and render receipts.

Persist native viewer tokens without rewriting training/data source. Browser
render receipts are evidence of display only, not evidence of numeric accuracy.
"""
from __future__ import annotations
import hashlib
import json
from uuid import uuid4
from timepilot.bi_agent_service import timestamp,BIWorkspaceError


def canon(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))


class PerspectiveWorkspaceSnapshots:
    def __init__(self, service):self.service=service

    def _token(self, value):
        encoded=canon(value)
        if len(encoded.encode())>512_000:raise BIWorkspaceError('工作区布局超过 512KB 上限')
        if not isinstance(value,dict):raise BIWorkspaceError('工作区布局必须为对象')
        panels=value.get('panels')
        if not isinstance(panels,dict) or not 1<=len(panels)<=12:
            raise BIWorkspaceError('工作区需要 1–12 个合法面板')
        # v5 table names are catalog-qualified on DuckDB; the installed
        # Virtual Server creates exactly the immutable timepilot_active root.
        for config in panels.values():
            if not isinstance(config,dict) or not isinstance(config.get('table'),str) or not (
                 config['table']=='timepilot_active' or config['table'].endswith('.timepilot_active')):
                raise BIWorkspaceError('禁止将 Viewer 面板切换到非当前授权源表')
        if value.get('derived_tables'):
            raise BIWorkspaceError('派生表需要由已授权的后端计算服务登记')
        return encoded,hashlib.sha256(encoded.encode()).hexdigest()

    def get(self,pid,wid):
        workspace=self.service.workspace(pid,wid)
        with self.service.projects._db() as db:
            row=db.execute('SELECT * FROM bi_perspective_snapshots WHERE project_id=? AND workspace_id=?',
                           (pid,wid)).fetchone()
        if row is None:
            return {'workspace_id':wid,'workspace_revision':workspace['revision'],
                    'viewer_revision':0,'source_sha256':workspace['source_sha256'],
                    'viewer_version':'5.6.1','token':None}
        record=dict(row)
        if record['owner_id']!=self.service._actor() or record['asset_id']!=workspace['asset_id'] or (
           record['version_id']!=workspace['version_id'] or record['source_sha256']!=workspace['source_sha256']):
            raise BIWorkspaceError('保存布局与当前资产版本不一致，拒绝恢复')
        record['token']=json.loads(record.pop('token_json'))
        return record

    def save(self,pid,wid,token,workspace_revision,viewer_revision,request_id):
        current=self.service.workspace(pid,wid)
        if current['revision']!=workspace_revision:raise BIWorkspaceError('工作区修订冲突，需重新读取后保存')
        if not request_id or len(request_id)>100:raise BIWorkspaceError('请求 ID 无效')
        raw,digest=self._token(token)
        now=timestamp();actor=self.service._actor()
        with self.service.projects._lock,self.service.projects._db() as db:
            duplicate=db.execute('''SELECT 1 FROM bi_perspective_snapshot_events
                      WHERE workspace_id=? AND request_id=?''',(wid,request_id)).fetchone()
            if duplicate:return self.get(pid,wid)
            old=db.execute('SELECT * FROM bi_perspective_snapshots WHERE workspace_id=? AND project_id=?',
                           (wid,pid)).fetchone()
            actual=old['viewer_revision'] if old else 0
            if actual!=viewer_revision:raise BIWorkspaceError('Viewer 修订冲突，手动操作已更新配置')
            if old and old['owner_id']!=actor:raise BIWorkspaceError('无权更新该工作区')
            changed=actual+1
            db.execute('''INSERT INTO bi_perspective_snapshots(
                 workspace_id,project_id,owner_id,asset_id,version_id,source_sha256,
                 workspace_revision,viewer_revision,viewer_version,token_json,token_sha256,updated_at)
                 VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(workspace_id) DO UPDATE SET
                 workspace_revision=excluded.workspace_revision,viewer_revision=excluded.viewer_revision,
                 token_json=excluded.token_json,token_sha256=excluded.token_sha256,updated_at=excluded.updated_at''',
                (wid,pid,actor,current['asset_id'],current['version_id'],current['source_sha256'],
                 workspace_revision,changed,'5.6.1',raw,digest,now))
            db.execute('''INSERT INTO bi_perspective_snapshot_events VALUES(?,?,?,?,?,?,?,?)''',
                       (str(uuid4()),wid,request_id,actor,changed,old['token_sha256'] if old else '',digest,now))
        return self.get(pid,wid)

    def render_receipt(self,pid,wid,request_id,success,viewer_revision,panel_id,plugin,source_sha256):
        ws=self.service.workspace(pid,wid)
        snap=self.get(pid,wid)
        if source_sha256!=ws['source_sha256'] or viewer_revision!=snap['viewer_revision']:
            raise BIWorkspaceError('渲染回执所指的数据来源或 Viewer 配置已变化')
        if not isinstance(panel_id,str) or len(panel_id)>128 or not isinstance(plugin,str) or len(plugin)>80:
            raise BIWorkspaceError('渲染面板参数无效')
        active=snap.get('token') or {}
        panel=active.get('panels',{}).get(panel_id)
        if not isinstance(panel,dict):
            raise BIWorkspaceError('渲染回执引用不存在的面板或未保存真实 Viewer 配置')
        if plugin!=panel.get('plugin'):
            raise BIWorkspaceError('渲染回执插件与实际 Viewer 配置不一致')
        now=timestamp();actor=self.service._actor()
        evidence={'success':bool(success),'panel_id':panel_id,'plugin':plugin,
                  'viewer_revision':viewer_revision,'source_sha256':source_sha256,
                  'evidence_type':'browser_display_only'}
        raw=canon(evidence)
        with self.service.projects._lock,self.service.projects._db() as db:
            db.execute('''INSERT INTO bi_perspective_tool_receipts VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                  ON CONFLICT(workspace_id,request_id) DO NOTHING''',
              (str(uuid4()),pid,wid,request_id,'bi.viewer.render', 'rendered' if success else 'render_failed',
               ws['revision'],ws['source_sha256'],hashlib.sha256(raw.encode()).hexdigest(),raw,now,now))
        return evidence
