"""Append-only, byte-verified Markdown report snapshots for FeatureLab.

A report snapshot is an explicit, authorized user action. No live report is
silently advertised as an archived document. Report bytes and manifest are
pinned to an analysis revision; a changed source cannot create a new snapshot.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import os
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from timepilot.feature_contracts import canonical
from timepilot.projects import now
from timepilot.enterprise_security import SecurityError


class FeatureReportArtifacts:
    def __init__(self, analysis):
        self.analysis = analysis
        self.projects = analysis.projects
        self.root = self.projects.home / 'feature_lab'

    def _row(self, pid, report_id):
        self.projects.get(pid)
        with self.projects._db() as db:
            row = db.execute('''SELECT * FROM feature_report_artifacts
                WHERE project_id=? AND report_id=? AND owner_id=?''',
                (pid, report_id, self.analysis._actor())).fetchone()
        if row is None:
            raise KeyError('报告版本不存在或无权访问')
        return dict(row)

    def _path(self, row):
        path = (self.root / row['body_relpath']).resolve()
        if not path.is_relative_to(self.root.resolve()):
            raise ValueError('报告存储路径越界')
        return path

    def archive(self, pid, sid, expected_revision):
        self.analysis.registry._editor(pid)
        state = self.analysis.get(pid, sid)
        if state['stale']:
            raise ValueError('来源已变化，禁止将过期结果存为新报告')
        if state['revision'] != expected_revision:
            raise ValueError('会话修订已变化，请刷新后再保存报告')
        result = self.analysis.report_markdown(pid, sid)
        proof = result['delivery']
        if proof['status'] in {'blocked', 'stale'}:
            raise ValueError('同源验证失败，禁止归档当前报告')
        data = result['markdown'].encode('utf-8')
        sha = hashlib.sha256(data).hexdigest()
        if sha != result['report_sha256']:
            raise ValueError('报告字节校验失败')
        report_id = str(uuid5(NAMESPACE_URL,
            'timepilot-report:'+pid+':'+sid+':'+str(expected_revision)))
        relative = Path(pid) / 'reports' / (report_id + '.md')
        output = (self.root / relative).resolve()
        if not output.is_relative_to(self.root.resolve()):
            raise ValueError('非法报告目录')
        manifest = {'schema_version': 1, 'report_id': report_id,
            'session_id': sid, 'session_revision': expected_revision,
            'asset_id': state['asset_id'], 'version_id': state['version_id'],
            'source_sha256': state['source_sha256'],
            'contract_revision': state['contract_revision'],
            'feature_set_id': state.get('feature_set_id'),
            'feature_artifact_id': (proof.get('artifact') or {}).get('artifact_id'),
            'feature_artifact_sha256': result['artifact_sha256'],
            'evaluation_id': result['evaluation_id'],
            'bi_workspace_sha256': (proof.get('bi') or {}).get('workspace_sha256'),
            'delivery_status': proof['status'], 'report_sha256': sha}
        with self.projects._lock, self.projects._db() as db:
            found = db.execute('''SELECT report_id,report_sha256,body_relpath
                FROM feature_report_artifacts WHERE project_id=? AND session_id=? AND revision=?''',
                (pid, sid, expected_revision)).fetchone()
            if found:
                if found['report_id'] != report_id or found['report_sha256'] != sha:
                    raise ValueError('报告已归档且字节不同；不可覆盖旧版本')
            else:
                output.parent.mkdir(parents=True, exist_ok=True)
                if output.exists():
                    if hashlib.sha256(output.read_bytes()).hexdigest() != sha:
                        raise ValueError('归档路径存在不同字节，拒绝覆盖')
                else:
                    # Exclusive file creation; no writer may alter an archive.
                    with output.open('xb') as stream:
                        stream.write(data)
                        stream.flush()
                        os.fsync(stream.fileno())
                db.execute('''INSERT INTO feature_report_artifacts
                  (report_id,project_id,session_id,owner_id,revision,source_sha256,
                   artifact_sha256,report_sha256,body_relpath,manifest_json,created_at)
                  VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                  (report_id,pid,sid,self.analysis._actor(),expected_revision,
                   state['source_sha256'],result['artifact_sha256'],sha,
                   str(relative),canonical(manifest),now()))
        return self.get(pid,report_id,include_markdown=False)

    def get(self,pid,report_id,*,include_markdown=True):
        row=self._row(pid,report_id)
        body=self._path(row).read_bytes()
        if hashlib.sha256(body).hexdigest()!=row['report_sha256']:
            raise ValueError('归档报告文件校验失败，禁止作为有效报告使用')
        manifest=json.loads(row['manifest_json'])
        if (manifest['report_sha256']!=row['report_sha256'] or
            manifest['source_sha256']!=row['source_sha256'] or
            manifest['session_revision']!=row['revision']):
            raise ValueError('归档报告证据清单损坏')
        result={'report_id':report_id,'session_id':row['session_id'],
                'revision':row['revision'],'created_at':row['created_at'],
                'report_sha256':row['report_sha256'], 'manifest':manifest,
                'immutable':True,'verified':True}
        if include_markdown:result['markdown']=body.decode('utf-8')
        return result

    def list(self,pid,sid):
        self.analysis.get(pid,sid)  # owner/project boundary
        with self.projects._db() as db:
            rows=db.execute('''SELECT report_id FROM feature_report_artifacts
                WHERE project_id=? AND session_id=? AND owner_id=?
                ORDER BY revision DESC LIMIT 100''',
                (pid,sid,self.analysis._actor())).fetchall()
        return [self.get(pid,row['report_id'],include_markdown=False) for row in rows]

    def diff(self,pid,left_id,right_id):
        left=self.get(pid,left_id)
        right=self.get(pid,right_id)
        if left['session_id']!=right['session_id']:
            raise ValueError('报告差异仅允许同一分析任务的不同版本')
        lines=list(difflib.unified_diff(left['markdown'].splitlines(),
             right['markdown'].splitlines(),fromfile=left_id,tofile=right_id,lineterm=''))
        return {'left_id':left_id,'right_id':right_id,'diff':'\n'.join(lines[:2000]),
                'truncated':len(lines)>2000}
