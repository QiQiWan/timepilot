"""Project/asset/actor-scoped BI display preferences; no data-version writes."""
from __future__ import annotations
import json
from timepilot.bi_agent_service import BIWorkspaceService, timestamp

class BIUIPreferences:
    MODES = {'closed','open','minimized','docked'}
    def __init__(self, workspaces: BIWorkspaceService):
        self.service=workspaces

    def get(self, pid: str, asset_id: str) -> dict:
        s=self.service
        s.projects.get(pid)
        # Asset must belong to the same project before disclosing user settings.
        s.engine.checked(pid,asset_id,'base')
        actor=s._actor()
        with s.projects._db() as db:
            r=db.execute('SELECT * FROM bi_ui_preferences WHERE project_id=? AND user_id=? AND asset_id=?',
                         (pid,actor,asset_id)).fetchone()
        if not r:
            return {'asset_id':asset_id,'mode':'closed','width':480,'height':670,'last_workspace_id':None,'dismissed_tips':[]}
        d=dict(r);d['dismissed_tips']=json.loads(d.pop('dismissed_tips'))
        d.pop('user_id',None);d.pop('project_id',None)
        return d

    def patch(self, pid: str, asset_id: str, changes: dict) -> dict:
        s=self.service
        s.projects.get(pid)
        s.engine.checked(pid,asset_id,'base')
        if set(changes)-{'mode','width','height','last_workspace_id','dismissed_tips'}:
            raise ValueError('不支持的 UI 偏好字段')
        doc=self.get(pid,asset_id)
        doc.update(changes)
        if doc['mode'] not in self.MODES:raise ValueError('浮窗模式无效')
        if type(doc['width']) is not int or not 320<=doc['width']<=820:
            raise ValueError('浮窗宽度必须为 320—820 px')
        if type(doc['height']) is not int or not 280<=doc['height']<=900:
            raise ValueError('浮窗高度必须为 280—900 px')
        if (not isinstance(doc['dismissed_tips'],list) or len(doc['dismissed_tips'])>30 or
            any(not isinstance(x,str) or len(x)>80 for x in doc['dismissed_tips'])):
            raise ValueError('提示列表无效')
        if doc['last_workspace_id'] is not None:
            ws=s.workspace(pid,doc['last_workspace_id'])
            if ws['asset_id']!=asset_id:raise ValueError('工作区不属于当前资产')
        with s.projects._lock,s.projects._db() as db:
            db.execute('''INSERT INTO bi_ui_preferences(project_id,user_id,asset_id,mode,width,height,last_workspace_id,dismissed_tips,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(project_id,user_id,asset_id) DO UPDATE SET
                mode=excluded.mode,width=excluded.width,height=excluded.height,last_workspace_id=excluded.last_workspace_id,
                dismissed_tips=excluded.dismissed_tips,updated_at=excluded.updated_at''',
                (pid,s._actor(),asset_id,doc['mode'],doc['width'],doc['height'],doc['last_workspace_id'],
                 json.dumps(doc['dismissed_tips'],ensure_ascii=False),timestamp()))
        return self.get(pid,asset_id)
