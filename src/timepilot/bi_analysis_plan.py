"""Durable, scope-checked BI analysis plans and real result acknowledgements."""
from __future__ import annotations
import importlib.util
import json
from uuid import uuid4
from timepilot.bi_agent_service import BIWorkspaceError,BIWorkspaceService,timestamp
from timepilot.bi_intent_resolver import BIIntentResolver
from timepilot.bi_feasibility import BIFeasibility
from timepilot.bi_statistics import BICorrelationService
from timepilot.bi_tool_registry import BIAction,ToolBatch

class BIAnalysisPlans:
    def __init__(self,service:BIWorkspaceService):
        self.service=service
        self.resolver=BIIntentResolver()
        self.feasibility=BIFeasibility()
        self.stats=BICorrelationService(service.engine)

    def _load(self,pid,wid,plan_id):
        ws=self.service.workspace(pid,wid)
        with self.service.projects._db() as db:
            row=db.execute('''SELECT * FROM bi_analysis_plans
               WHERE project_id=? AND workspace_id=? AND owner_id=? AND plan_id=?''',
               (pid,wid,self.service._actor(),plan_id)).fetchone()
        if not row:raise KeyError('分析计划不存在或无权限')
        plan=dict(row);plan['plan']=json.loads(plan.pop('plan_json'))
        return ws,plan

    def create(self,pid,wid,message,revision,request_id):
        ws=self.service.workspace(pid,wid)
        with self.service.projects._db() as db:
            old=db.execute('''SELECT plan_id FROM bi_analysis_plans
                 WHERE workspace_id=? AND owner_id=? AND request_id=?''',
                 (wid,self.service._actor(),request_id)).fetchone()
        if old:return self.get(pid,wid,old['plan_id'])
        if ws['revision']!=revision:raise BIWorkspaceError('工作区版本冲突，请刷新后再建立分析计划')
        schema=self.service._asset(pid,ws['asset_id'],ws['version_id'])
        intent=self.resolver.resolve(message,schema['columns'],ws['state'])
        decision=self.feasibility.check(intent,schema,ws['state'],
                            duckdb_ready=importlib.util.find_spec('duckdb') is not None)
        status={'feasible_auto':'ready','feasible_confirm':'awaiting_confirmation',
                'needs_clarification':'needs_clarification','unsupported':'unsupported',
                'blocked':'blocked'}[decision['status']]
        plan={'intent':intent,'feasibility':decision,'steps':[
            {'tool':'bi.stats.correlation','status':'pending'},
            {'tool':'bi.chart.heatmap','status':'pending'}] if status in ('ready','awaiting_confirmation') else [],
            'source_sha256':ws['source_sha256'],'workspace_revision':revision}
        now=timestamp();plan_id=str(uuid4())
        with self.service.projects._lock,self.service.projects._db() as db:
            db.execute('''INSERT INTO bi_analysis_plans VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (plan_id,pid,wid,self.service._actor(),request_id,message,revision,1,
                 ws['source_sha256'],status,json.dumps(plan,ensure_ascii=False),now,now))
            db.execute('''INSERT OR IGNORE INTO bi_agent_turns VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
                (str(uuid4()),pid,wid,self.service._actor(),request_id,'user',message,status,
                 json.dumps({'analysis_plan_id':plan_id,'intent':intent['intent_candidates']},ensure_ascii=False),
                 revision,now))
        return self.get(pid,wid,plan_id)

    def get(self,pid,wid,plan_id):
        ws,plan=self._load(pid,wid,plan_id)
        stale=ws['revision']!=plan['expected_revision'] or ws['source_sha256']!=plan['source_sha256']
        if stale and plan['status'] in {'ready','awaiting_confirmation','previewed','needs_clarification'}:
            self._status(plan_id,'stale')
            plan['status']='stale'
        return plan

    def _status(self,plan_id,status,plan=None):
        with self.service.projects._lock,self.service.projects._db() as db:
            if plan is None:
                db.execute('UPDATE bi_analysis_plans SET status=?,updated_at=? WHERE plan_id=?',
                           (status,timestamp(),plan_id))
            else:
                db.execute('''UPDATE bi_analysis_plans SET status=?,plan_json=?,updated_at=? WHERE plan_id=?''',
                    (status,json.dumps(plan,ensure_ascii=False),timestamp(),plan_id))
            db.execute('''UPDATE bi_agent_turns SET status=? WHERE request_id IN (
              SELECT request_id FROM bi_analysis_plans WHERE plan_id=?) AND workspace_id IN (
              SELECT workspace_id FROM bi_analysis_plans WHERE plan_id=?)''',(status,plan_id,plan_id))

    def update(self,pid,wid,plan_id,plan_revision,*,field_ids,method='pearson'):
        ws,item=self._load(pid,wid,plan_id)
        if ws['revision']!=item['expected_revision'] or ws['source_sha256']!=item['source_sha256']:
            self._status(plan_id,'stale');raise BIWorkspaceError('计划的数据或工作区状态已变化')
        if item['plan_revision']!=plan_revision:raise BIWorkspaceError('计划版本冲突')
        if item['status'] not in ('ready','awaiting_confirmation','needs_clarification'):
            raise BIWorkspaceError('当前状态不允许调整计划')
        if method!='pearson':raise ValueError('当前仅支持 Pearson 相关系数')
        schema=self.service._asset(pid,ws['asset_id'],ws['version_id'])
        permitted={c['name'] for c in schema['columns']}
        if any(field not in permitted for field in field_ids):
            raise ValueError('字段引用不属于当前资产；拒绝未知列或未授权列')
        decision=self.feasibility.check(item['plan']['intent'],schema,ws['state'],field_ids,
                          duckdb_ready=importlib.util.find_spec('duckdb') is not None)
        if decision['defaults']:decision['defaults']['method']=method
        status='awaiting_confirmation' if decision['status']=='feasible_confirm' else (
               'ready' if decision['status']=='feasible_auto' else decision['status'])
        plan=item['plan'];plan['feasibility']=decision
        plan['steps']=[{'tool':'bi.stats.correlation','status':'pending'},
                      {'tool':'bi.chart.heatmap','status':'pending'}] if status in ('ready','awaiting_confirmation') else []
        with self.service.projects._lock,self.service.projects._db() as db:
            changed=db.execute('''UPDATE bi_analysis_plans SET plan_json=?,plan_revision=plan_revision+1,
              status=?,updated_at=? WHERE plan_id=? AND plan_revision=?''',
              (json.dumps(plan,ensure_ascii=False),status,timestamp(),plan_id,plan_revision)).rowcount
        if changed!=1:raise BIWorkspaceError('计划版本冲突')
        return self.get(pid,wid,plan_id)

    def apply(self,pid,wid,plan_id,request_id,plan_revision,*,confirm=False):
        ws,item=self._load(pid,wid,plan_id)
        with self.service.projects._db() as db:
            prev=db.execute('SELECT * FROM bi_analysis_executions WHERE plan_id=? AND request_id=?',
                            (plan_id,request_id)).fetchone()
        if prev:return self.execution(pid,wid,plan_id,prev['execution_id'])
        if item['status'] not in ('ready','awaiting_confirmation','failed_retryable'):
            raise BIWorkspaceError('当前计划不允许执行，请查看澄清项或重新建立计划')
        if ws['revision']!=item['expected_revision'] or ws['source_sha256']!=item['source_sha256']:
            self._status(plan_id,'stale');raise BIWorkspaceError('数据来源或工作区版本已变化')
        if item['plan_revision']!=plan_revision:raise BIWorkspaceError('计划版本冲突')
        if item['status']=='awaiting_confirmation' and not confirm:
            raise BIWorkspaceError('请确认当前字段选择后执行')
        defaults=item['plan']['feasibility']['defaults']
        if not defaults:raise BIWorkspaceError('计划没有可执行的字段和算子')
        eid=str(uuid4());moment=timestamp()
        # Atomically claim the plan so two users/requests cannot run the same
        # revision and append duplicate charts. Request-id retry is checked above.
        with self.service.projects._lock,self.service.projects._db() as db:
            claimed=db.execute('''UPDATE bi_analysis_plans SET status='running',updated_at=?
                WHERE plan_id=? AND plan_revision=? AND status IN ('ready','awaiting_confirmation','failed_retryable')''',
                (moment,plan_id,plan_revision)).rowcount
            if claimed != 1:
                raise BIWorkspaceError('分析计划状态已改变，请先刷新执行状态')
            db.execute('''INSERT INTO bi_analysis_executions VALUES(?,?,?,?,?,?,?,?)''',
                       (eid,plan_id,request_id,'running',None,None,moment,moment))
        try:
            matrix=self.stats.compute(pid,ws,**defaults)
            # Hold the catalogue lock from cancellation check through matrix
            # publication and audited chart mutation to prevent cancellation races.
            with self.service.projects._lock:
                with self.service.projects._db() as db:
                    active=db.execute('SELECT status FROM bi_analysis_plans WHERE plan_id=?',(plan_id,)).fetchone()
                if not active or active['status']!='running':
                    raise BIWorkspaceError('计划已取消或状态已变化，计算结果不予发布')
                analysis_id=str(uuid4())
                with self.service.projects._db() as db:
                    db.execute('''INSERT INTO bi_analysis_matrices VALUES(?,?,?,?,?,?,?,?)''',
                        (analysis_id,plan_id,pid,self.service._actor(),ws['source_sha256'],
                         matrix['selection_fingerprint'],json.dumps(matrix,ensure_ascii=False),timestamp()))
                # Audited and undoable presentation change; no source-data edit.
                batch=ToolBatch(expected_revision=ws['revision'],request_id=request_id,
                    actions=[BIAction(tool='bi.chart.heatmap',arguments={'analysis_id':analysis_id})])
                changed=self.service.apply(pid,wid,batch)
                result={'analysis_id':analysis_id,'matrix_shape':[matrix['dimension'],matrix['dimension']],
                        'workspace_revision':changed['workspace']['revision'],
                        'source_sha256':ws['source_sha256'],'selection_fingerprint':matrix['selection_fingerprint'],
                        'workspace':changed['workspace']}
                with self.service.projects._db() as db:
                    db.execute('''UPDATE bi_analysis_executions SET status=?,result_json=?,updated_at=? WHERE execution_id=?''',
                        ('rendering',json.dumps(result,ensure_ascii=False),timestamp(),eid))
                self._status(plan_id,'rendering')
        except Exception as exc:
            with self.service.projects._lock,self.service.projects._db() as db:
                current=db.execute('SELECT status FROM bi_analysis_plans WHERE plan_id=?',(plan_id,)).fetchone()
                failure_state='cancelled' if current and current['status']=='cancelled' else 'failed_retryable'
                db.execute('''UPDATE bi_analysis_executions SET status=?,error_code=?,updated_at=? WHERE execution_id=?''',
                           (failure_state,type(exc).__name__,timestamp(),eid))
                if failure_state!='cancelled':
                    db.execute('UPDATE bi_analysis_plans SET status=?,updated_at=? WHERE plan_id=?',
                               (failure_state,timestamp(),plan_id))
            raise
        return self.execution(pid,wid,plan_id,eid)

    def execution(self,pid,wid,plan_id,eid):
        self._load(pid,wid,plan_id)
        with self.service.projects._db() as db:
            row=db.execute('SELECT * FROM bi_analysis_executions WHERE plan_id=? AND execution_id=?',
                           (plan_id,eid)).fetchone()
        if row is None:raise KeyError('分析执行不存在')
        output=dict(row);output['result']=json.loads(output.pop('result_json')) if output['result_json'] else None
        return output

    def render_ack(self,pid,wid,plan_id,eid,*,success):
        item=self.execution(pid,wid,plan_id,eid)
        # A failed statistical calculation is not renderable. Success only
        # follows a persisted result still visible in the scoped workspace.
        if item['status'] not in ('rendering','failed_retryable') or not item['result']:
            return item
        if success:
            ws=self.service.workspace(pid,wid)
            if ws['state'].get('correlation',{}).get('analysis_id')!=item['result']['analysis_id']:
                raise BIWorkspaceError('图表与当前工作区不一致，不能确认渲染完成')
            self.matrix(pid,item['result']['analysis_id'])
        status='succeeded' if success else 'failed_retryable'
        with self.service.projects._lock,self.service.projects._db() as db:
            db.execute('UPDATE bi_analysis_executions SET status=?,updated_at=? WHERE execution_id=?',
                       (status,timestamp(),eid))
        self._status(plan_id,status)
        return self.execution(pid,wid,plan_id,eid)

    def cancel(self,pid,wid,plan_id,plan_revision):
        _,item=self._load(pid,wid,plan_id)
        if item['plan_revision']!=plan_revision:
            raise BIWorkspaceError('计划版本冲突')
        if item['status'] in ('succeeded','cancelled'):
            return self.get(pid,wid,plan_id)
        if item['status']=='rendering':
            raise BIWorkspaceError('矩阵已提交到工作区，若要撤销请使用工作区撤销操作')
        with self.service.projects._lock,self.service.projects._db() as db:
            db.execute("UPDATE bi_analysis_plans SET status='cancelled',updated_at=? WHERE plan_id=?",
                       (timestamp(),plan_id))
            db.execute("""UPDATE bi_analysis_executions SET status='cancelled',updated_at=?
                        WHERE plan_id=? AND status='running'""",(timestamp(),plan_id))
        self._status(plan_id,'cancelled')
        return self.get(pid,wid,plan_id)

    def clarify(self,pid,wid,plan_id,plan_revision,answer):
        _,item=self._load(pid,wid,plan_id)
        if item['plan_revision']!=plan_revision:
            raise BIWorkspaceError('计划版本冲突')
        schema=self.service._asset(pid,self.service.workspace(pid,wid)['asset_id'],
                                   self.service.workspace(pid,wid)['version_id'])
        parsed=self.resolver.resolve(answer,schema['columns'])
        # Only exact current-column references are allowed; changing the
        # intent is a separate plan. No untrusted term can become an action.
        selected=parsed['mentioned_fields']
        if parsed['unresolved_fields']:
            raise ValueError('澄清内容包含不存在的源字段')
        if len(selected)<2:
            raise ValueError('请明确指定至少两个现有数值字段；可在字段选择中勾选')
        return self.update(pid,wid,plan_id,plan_revision,field_ids=selected)

    def matrix(self,pid,analysis_id):
        self.service.projects.get(pid)
        with self.service.projects._db() as db:
            row=db.execute('''SELECT * FROM bi_analysis_matrices WHERE project_id=? AND analysis_id=? AND owner_id=?''',
                           (pid,analysis_id,self.service._actor())).fetchone()
        if not row:raise KeyError('相关矩阵不存在或不属于当前用户')
        doc=json.loads(row['matrix_json'])
        with self.service.projects._db() as db:
            ws=db.execute('''SELECT workspace_id FROM bi_analysis_plans WHERE plan_id=? AND project_id=?''',
                           (row['plan_id'],pid)).fetchone()
        if not ws:raise KeyError('相关矩阵已失效')
        current=self.service.workspace(pid,ws['workspace_id'])
        if current['source_sha256']!=row['source_sha256']:
            raise BIWorkspaceError('相关矩阵来源已变化，请重新分析')
        from timepilot.bi_statistics import BICorrelationService
        actual=BICorrelationService.selection_fingerprint(current['state'],current['source_sha256'])
        if actual!=row['selection_fingerprint']:
            raise BIWorkspaceError('相关矩阵所用筛选行集已变化，请重新计算')
        return {'analysis_id':analysis_id,**doc}
