"""v0.35.4 unified, low-cost, source-pinned exploratory FeatureLab journey.

Orchestrates *existing* durable FeatureAutoAnalysis sessions. No new model or
materialization pipeline. The default stage may read the source and compute
candidate quality previews; it never publishes a FeatureSet, trains a supervised
model, or edits original files. Workers still enforce the original owner's rights.
"""
from __future__ import annotations

import hashlib


class FeatureAnalysisJourney:
    def __init__(self, eda, auto):
        self.eda=eda
        self.auto=auto

    def _latest(self, pid, asset_id, version_id):
        session=self.auto.latest(pid,asset_id,version_id)
        if session and session.get('stale'):
            return None
        return session

    def status(self, pid, asset_id, version_id='base'):
        analysis=self.eda.status(pid,asset_id,version_id)
        session=self._latest(pid,asset_id,version_id)
        if session and (session.get('source_sha256')!=analysis['source_sha256']
                        or session.get('contract_revision')!=analysis['contract_revision']):
            session=None
        preview={'status':'not_started','session_id':None,'candidate_count':0,
                 'recommended_count':0,'review_count':0,'progress':0}
        if session:
            report=session.get('report') or {}
            preview.update(status=session.get('status') or 'not_started',
                           session_id=session['session_id'],
                           stage=session.get('stage'),
                           candidate_count=report.get('candidate_count',0),
                           recommended_count=report.get('recommended_count',0),
                           review_count=report.get('review_count',0),
                           progress=session.get('progress',0),
                           warnings=(session.get('warnings') or [])[:3])
        return {'schema_version':1,'asset_id':asset_id,'version_id':version_id,
                'source_sha256':analysis['source_sha256'],
                'contract_revision':analysis['contract_revision'],
                'analysis_status':analysis['status'],
                'feature_preview':preview,
                'requires_model_api':False,
                'requires_human_confirmation_for':['feature_set','materialization','supervised_evaluation','publication']}

    def start(self, pid, asset_id, version_id='base'):
        """Idempotent across browser refreshes, publication events, worker restarts.

        Reuses any current feature session for the exact asset/version. A fixed
        rule-only objective and bounded budget prevent unapproved model use.
        """
        self.auto.registry._editor(pid)
        src=self.eda.semantic.source(pid,asset_id,version_id)
        contract=self.eda.semantic.latest(pid,asset_id,version_id,source=src)
        if contract.state=='stale':
            raise ValueError('字段契约已失效，无法自动分析')
        if self.eda.native.verify(asset_id).get('valid') is not True:
            raise ValueError('源文件 SHA256 校验失败，不能继续自动分析')
        rows=int(src.get('row_count') or 0)
        if rows <= 0 or rows > 2_000_000 or len(contract.fields)>256:
            return {**self.status(pid,asset_id,version_id),
                    'auto_start':'skipped_budget',
                    'message':'数据为空或超过默认预览预算；需要先确认数据范围。'}
        previous=self._latest(pid,asset_id,version_id)
        if previous and previous.get('source_sha256')==src['sha256'] and previous.get('contract_revision')==contract.revision:
            return {**self.status(pid,asset_id,version_id),'auto_start':'reused'}
        key=hashlib.sha256(f'journey-0354:{pid}:{asset_id}:{version_id}:{src["sha256"]}:{contract.revision}'.encode()).hexdigest()
        created=self.auto.create(pid,asset_id,version_id,goal='自动探索数据质量、统计规律与安全候选特征',
                                 task_hint='explore',budget=16,request_id='journey-'+key[:64])
        return {**self.status(pid,asset_id,version_id),
                'auto_start':'queued' if created.get('status')=='profiling' else created.get('status')}
