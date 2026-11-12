"""v0.31.0: asset-scoped five-step user workflow, derived from persisted evidence.

Read-only. It does not advance a task, change approvals or mark a model good.
This endpoint can safely be refreshed/resumed without inventing accomplishments.
"""
from __future__ import annotations


class FeatureWorkflow:
    def __init__(self,projects,semantic,registry):
        self.projects=projects
        self.semantic=semantic
        self.registry=registry

    def summary(self,pid,aid,vid='base'):
        self.projects.get(pid)
        # The checked semantic source accepts both original and linked project assets.
        self.registry._editor(pid)
        source=self.semantic.source(pid,aid,vid)
        contract=self.semantic.latest(pid,aid,vid,source=source)
        actor=self.registry._actor()
        with self.projects._db() as db:
            session=db.execute('''SELECT COUNT(1) FROM feature_candidate_sessions
                WHERE project_id=? AND owner_id=? AND asset_id=? AND version_id=? AND source_sha256=?''',
                (pid,actor,aid,vid,source['sha256'])).fetchone()[0]
            sets=db.execute('''SELECT set_id,state FROM feature_sets
                WHERE project_id=? AND owner_id=? AND asset_id=? AND version_id=? AND source_sha256=?''',
                (pid,actor,aid,vid,source['sha256'])).fetchall()
            set_ids=[r['set_id'] for r in sets]
            jobs=db.execute('''SELECT kind,target_id,status FROM feature_jobs
                WHERE project_id=? AND owner_id=? AND kind IN ('materialize','evaluation','advanced_join','advanced_join_evaluation')''',
                (pid,actor)).fetchall()
            materialized=any(r['kind']=='materialize' and r['target_id'] in set_ids and r['status']=='succeeded' for r in jobs)
            # No silent cross-asset success: advanced joins attach the actual decision input.
            join_count=db.execute('''SELECT COUNT(1) FROM feature_advanced_joins
                WHERE project_id=? AND owner_id=? AND decision_asset_id=?''',(pid,actor,aid)).fetchone()[0]
            eval_count=db.execute('''SELECT COUNT(1) FROM feature_advanced_join_evaluations e
                JOIN feature_advanced_joins j ON e.join_id=j.join_id
                WHERE j.project_id=? AND j.owner_id=? AND j.decision_asset_id=?''',
                (pid,actor,aid)).fetchone()[0]
            general_evaluation=bool(set_ids and db.execute('''SELECT 1 FROM feature_evaluations e
                JOIN feature_evaluation_plans p ON e.plan_id=p.plan_id
                WHERE e.project_id=? AND e.status='succeeded' AND p.set_id IN ('''+','.join('?'*len(set_ids))+') LIMIT 1',
                (pid,*set_ids)).fetchone()) if set_ids else False
        profile=contract.state=='confirmed'
        drafted=bool(sets) or session>0
        materialized=materialized or join_count>0
        evaluated=general_evaluation or eval_count>0
        published=any(x['state']=='published' for x in sets)
        specs=[('profile','确认数据','profile',profile),('build','生成候选','build',drafted),
               ('materialize','正式计算','build',materialized),('evaluate','实验评价','evaluate',evaluated),
               ('catalog','审核发布','catalog',published)]
        steps=[{'key':key,'label':label,'tab':tab,'completed':complete} for key,label,tab,complete in specs]
        nextstep=next((s for s in steps if not s['completed']),steps[-1])
        return {'project_id':pid,'asset_id':aid,'version_id':vid,
                'source_sha256':source['sha256'],'contract_revision':contract.revision,
                'contract_state':contract.state,'steps':steps,
                'next_step':nextstep,'status':'requires_review' if not published else 'published',
                'advanced_join_count':join_count,'advanced_evaluation_count':eval_count,
                'evidence':{'candidate_sessions':session,'feature_sets':len(sets),
                            'materialized':materialized,'evaluated':evaluated,'published':published},
                'note':'只依据当前用户、项目、资产及来源哈希中的持久记录，步骤状态不代表业务性能达标'}
