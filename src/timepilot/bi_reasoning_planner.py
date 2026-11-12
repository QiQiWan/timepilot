"""v0.24.0 bounded tool planning, durable audit and explicit display receipts.

Reasoning-capable models can propose structured tool plans, but only registered,
authorized JSON actions survive service-side validation. No model chain-of-thought,
free SQL/Python, API keys or unapproved source rows are persisted/exposed.
"""
from __future__ import annotations
import hashlib
import json
import re
from uuid import uuid4
from timepilot.bi_agent_service import BIWorkspaceError,timestamp
from timepilot.bi_intent_resolver import BIIntentResolver
from timepilot.bi_tool_registry import BIAction,ToolBatch,TOOLS,validate_action
from timepilot.bi_tool_manifest import for_workspace

MAX_STEPS=8


def dumps(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),default=str)


class BIReasoningPlanner:
    def __init__(self,service):
        self.service=service
        self.intent=BIIntentResolver()

    def _deterministic(self,message,meta):
        names=[c['name'] for c in meta['columns']]
        from timepilot.bi_nl_math import parse_math_request
        math_actions,explanation,clarification=parse_math_request(message,meta['columns'])
        if math_actions:
            return math_actions,'ready',explanation,self.intent.resolve(message,meta['columns'])
        if clarification:
            return [],'needs_clarification',clarification,self.intent.resolve(message,meta['columns'])
        resolved=self.intent.resolve(message,meta['columns'])
        if resolved['unresolved_fields']:
            return [],'blocked',f"数据中不存在字段：{'、'.join(resolved['unresolved_fields'])}",resolved
        intent=resolved['intent_candidates'][0]
        if intent=='correlation':
            return [],'needs_clarification','相关矩阵应使用已存在的相关性分析计划，核对数值字段与范围后执行',resolved
        if '删除' in message and ('资产' in message or '表' in message):
            return [],'needs_clarification','删除需要进入数据管理查看真实引用关系并在确认弹窗中操作',resolved
        # Explicit schema/row/asset commands never require an LLM.
        if intent in {'inspect_schema','inspect_rows','inspect_asset','catalog'}:
            actions=self.service._deterministic_plan(message,names)
            return actions,'ready' if actions else 'needs_clarification',None if actions else '请明确要查看的对象',resolved
        group=re.search(r'按\s*([\u4e00-\u9fa5\w]+)\s*分组.*?([\u4e00-\u9fa5\w]+)\s*(?:求)?(?:平均|均值|求和|总和|最大|最小)',message)
        if group and group.group(1) in names and group.group(2) in names:
            op='avg' if '平均' in message or '均值' in message else (
                'sum' if '求和' in message or '总和' in message else (
                'max' if '最大' in message else 'min'))
            a=[BIAction(tool='bi.aggregate.run',arguments={
                'group_by':[group.group(1)],'operation':op,'column':group.group(2),'alias':'value'})]
            if any(x in message for x in ('柱状图','柱形图')):
                a.append(BIAction(tool='bi.chart.create',arguments={
                    'type':'bar','x':group.group(1),'y':['value']}))
            return a,'ready',None,resolved
        actions=self.service._deterministic_plan(message,names)
        if actions:
            return actions,'ready',None,resolved
        if intent in ('trend','compare','profile'):
            return [],'needs_clarification','请指定需要分析的字段；时间趋势还需确认时间轴或记录顺序',resolved
        return [],'needs_clarification','请明确要查看的字段、操作或结果类型',resolved

    def _propose_model(self,message,workspace,meta,available):
        gateway=self.service.models
        if gateway is None:return None,None,None
        cfg=gateway.config()
        if not (cfg.get('enabled') and cfg.get('has_api_key')):return None,None,None
        prompt=('你是受限 BI 动作规划器。只返回 JSON {"actions":[{"tool":"...","arguments":{}}]}。'
                '最多8步，字段只能来自输入的授权 Schema。不能输出 SQL、Python、资产/项目ID、'
                '越权/写入/删除动作。没有唯一安全的计划时 actions 应为空。'
                '可以在内部充分推理，响应中不要输出推理过程或文本说明。')
        from timepilot.bi_context_builder import select_fields,select_tools
        fields=select_fields(message,meta['columns'])
        if fields['status']!='ready':raise ValueError(fields['reason'])
        tools=select_tools(message,available)
        payload={'request':message,'schema':fields['fields'],
                 'schema_coverage':{'total':fields['total'], 'mentioned':fields['mentioned'],
                                    'omitted_count':fields['omitted_count']},
                 'existing_view':{'chart':workspace['state'].get('chart'),
                                  'filters':workspace['state'].get('query',{}).get('filters',[])},
                 'tools':tools['tools']}
        result=gateway._message([{'role':'system','content':prompt},
                                 {'role':'user','content':dumps(payload)}],max_tokens=2500)
        parsed=gateway._parse_json(result['content'])
        if not isinstance(parsed,dict) or not isinstance(parsed.get('actions',[]),list):
            raise ValueError('模型计划不是合法的工具数组')
        return parsed['actions'],result.get('model') or cfg.get('model'),result.get('usage') or {}

    def build(self,pid,wid,message):
        ws=self.service.workspace(pid,wid)
        meta=self.service._asset(pid,ws['asset_id'],ws['version_id'])
        local,status,reason,intent=self._deterministic(message,meta)
        planner='deterministic';model_id=None;usage={};warnings=[]
        manifests=for_workspace(self.service,pid,wid)
        if not local and status=='needs_clarification' and self.service.models:
            try:
                proposed,model_id,usage=self._propose_model(message,ws,meta,manifests['tools'])
                if proposed:
                    local=[BIAction.model_validate(a) for a in proposed]
                    status='ready';reason=None;planner='constrained_model'
            except Exception as exc:
                warnings.append('模型未能生成合法工具计划，已返回本地可行性结果：'+type(exc).__name__)
        if len(local)>MAX_STEPS:raise BIWorkspaceError('单个分析计划最多 8 个工具步骤')
        permitted={t['tool'] for t in manifests['tools'] if t['available'] and t['effect_class'] in ('read','presentation')}
        validated=[]
        for action in local:
            if action.tool not in TOOLS or action.tool not in permitted:
                status='blocked';reason='当前运行时或权限不支持 '+action.tool;validated=[];break
            args=validate_action(action)
            validated.append({'tool':action.tool,'arguments':args,'effect':TOOLS[action.tool][1]})
        if validated and not any(s['effect']=='read' for s in validated):
            # Execution will perform the final SQL/view state and revision check.
            status='ready'
        if not validated and status=='ready':
            status='needs_clarification';reason=reason or '没有形成可执行的工具步骤'
        return {'intent':intent,'status':status,'reason':reason,'planner':planner,
                'model_id':model_id,'usage':usage,'warnings':warnings,
                'tool_calls':validated,'required_confirmation':False,
                'evidence':{'asset_id':ws['asset_id'],'version_id':ws['version_id'],
                            'source_sha256':ws['source_sha256'],'workspace_revision':ws['revision'],
                            'available_field_count':len(meta['columns']),
                            'tool_manifest_sha256':manifests['manifest_sha256']}}


class BIReasoningRuns:
    def __init__(self,service):self.service=service;self.planner=BIReasoningPlanner(service)

    def _row(self,pid,wid,rid):
        ws=self.service.workspace(pid,wid)
        with self.service.projects._db() as db:
            row=db.execute('''SELECT * FROM bi_reasoning_runs WHERE run_id=? AND project_id=?
              AND workspace_id=? AND owner_id=?''',(rid,pid,wid,self.service._actor())).fetchone()
        if not row:raise KeyError('分析步骤计划不存在或无权限')
        doc=dict(row);doc['plan']=json.loads(doc.pop('plan_json'))
        doc['usage']=json.loads(doc.pop('usage_json'))
        if ws['source_sha256']!=doc['source_sha256'] or (ws['revision']!=doc['expected_revision'] and doc['status'] in ('ready','awaiting_confirmation')):
            doc['status']='stale'
        return ws,doc

    def get(self,pid,wid,rid):
        _,doc=self._row(pid,wid,rid)
        with self.service.projects._db() as db:
            steps=db.execute('''SELECT ordinal,tool_name,status,observation_json FROM bi_reasoning_steps
              WHERE run_id=? ORDER BY ordinal''',(rid,)).fetchall()
        doc['steps']=[{'ordinal':x['ordinal'],'tool':x['tool_name'],'status':x['status'],
                       'observation':json.loads(x['observation_json']) if x['observation_json'] else None} for x in steps]
        return doc

    def create(self,pid,wid,message,revision,request_id):
        ws=self.service.workspace(pid,wid)
        if ws['revision']!=revision:raise BIWorkspaceError('工作区版本冲突')
        if not request_id or len(request_id)>100:raise ValueError('请求 ID 无效')
        with self.service.projects._db() as db:
            old=db.execute('''SELECT run_id FROM bi_reasoning_runs WHERE workspace_id=?
                 AND owner_id=? AND request_id=?''',(wid,self.service._actor(),request_id)).fetchone()
        if old:return self.get(pid,wid,old['run_id'])
        plan=self.planner.build(pid,wid,message)
        now=timestamp();rid=str(uuid4());steps=plan['tool_calls']
        with self.service.projects._lock,self.service.projects._db() as db:
            db.execute('''INSERT INTO bi_reasoning_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
              (rid,pid,wid,request_id,self.service._actor(),message,plan['planner'],plan['status'],
               ws['source_sha256'],revision,dumps(plan),plan['model_id'],dumps(plan['usage']),now,now))
            for i,step in enumerate(steps):
                db.execute('''INSERT INTO bi_reasoning_steps VALUES(?,?,?,?,?,?,?,?)''',
                   (str(uuid4()),rid,i,step['tool'],dumps(step['arguments']),'pending',None,now))
        return self.get(pid,wid,rid)

    def apply(self,pid,wid,rid,request_id,confirm=False):
        ws,doc=self._row(pid,wid,rid)
        if doc['status'] in ('succeeded','computed_awaiting_render','render_failed'):
            # A repeated POST cannot run mutations again. Report the actual state.
            return self.get(pid,wid,rid)
        if doc['status'] not in ('ready','failed_retryable'):raise BIWorkspaceError('当前计划无法执行：'+doc['status'])
        if doc['expected_revision']!=ws['revision']:
            raise BIWorkspaceError('工作区修订冲突')
        steps=doc['plan']['tool_calls']
        if not steps:raise BIWorkspaceError('计划不包含可执行动作')
        actions=[BIAction(tool=x['tool'],arguments=x['arguments']) for x in steps]
        batch=ToolBatch(actions=actions,expected_revision=ws['revision'],
                        request_id=rid+'-apply',confirm=bool(confirm))
        now=timestamp()
        with self.service.projects._lock,self.service.projects._db() as db:
            updated=db.execute("UPDATE bi_reasoning_runs SET status='running',updated_at=? WHERE run_id=? AND status IN ('ready','failed_retryable')",
                              (now,rid)).rowcount
            if updated:
                db.execute("UPDATE bi_reasoning_steps SET status='pending',observation_json=NULL WHERE run_id=? AND status='failed'",(rid,))
        if updated!=1:raise BIWorkspaceError('计划正在被其他请求执行')
        try:
            from timepilot.bi_execution_observer import observe_steps
            observations=observe_steps(self.service,pid,ws,actions,confirmed=bool(confirm))
            # Read-only previews are checked sequentially, then an atomic CAS
            # applies the full validated batch. A failure cannot partly commit.
            result=self.service.apply(pid,wid,batch)
            display_needed=any(x['effect']!='read' for x in steps)
            new_status='computed_awaiting_render' if display_needed else 'succeeded'
            with self.service.projects._lock,self.service.projects._db() as db:
                db.execute('UPDATE bi_reasoning_runs SET status=?,updated_at=? WHERE run_id=?',
                           (new_status,timestamp(),rid))
                for index,observation in enumerate(observations):
                    observation={**observation,
                        'workspace_revision_after_commit':result.get('workspace',{}).get('revision',ws['revision']),
                        'render_proof':'pending' if display_needed else 'not_required'}
                    db.execute("""UPDATE bi_reasoning_steps SET status=?,observation_json=?
                      WHERE run_id=? AND ordinal=?""",
                      (new_status,dumps(observation),rid,index))
                    db.execute("""INSERT INTO bi_reasoning_observations VALUES(?,?,?,?,?,?)
                        ON CONFLICT(run_id,ordinal) DO UPDATE SET observation_sha256=excluded.observation_sha256,
                        status=excluded.status,metadata_json=excluded.metadata_json""",
                        (rid,index,hashlib.sha256(dumps(observation).encode()).hexdigest(),
                         new_status,dumps(observation),timestamp()))
            return {'run':self.get(pid,wid,rid),'execution':result,
                    'render_required':display_needed,'status':new_status}
        except Exception as exc:
            with self.service.projects._lock,self.service.projects._db() as db:
                db.execute("UPDATE bi_reasoning_runs SET status='failed_retryable',updated_at=? WHERE run_id=?",
                           (timestamp(),rid))
                db.execute('''UPDATE bi_reasoning_steps SET status='failed',observation_json=?
                              WHERE run_id=? AND status='pending' ''',
                           (dumps({'error_type':type(exc).__name__,'message':str(exc)[:400]}),rid))
            raise

    def rendered(self,pid,wid,rid,success,viewer_receipt):
        ws,doc=self._row(pid,wid,rid)
        if doc['status'] not in ('computed_awaiting_render','render_failed'):
            raise BIWorkspaceError('计划当前不等待绘图回执')
        if not isinstance(viewer_receipt,dict) or viewer_receipt.get('source_sha256')!=ws['source_sha256']:
            raise BIWorkspaceError('图表回执来源与资产不符')
        if not viewer_receipt.get('panel_id') or not viewer_receipt.get('plugin'):
            raise BIWorkspaceError('未能证明真实 Perspective 面板已渲染')
        from timepilot.bi_perspective_workspace import PerspectiveWorkspaceSnapshots
        snap=PerspectiveWorkspaceSnapshots(self.service)
        # Link the render claim to an immutable audited Viewer token. This is
        # only a display receipt; numerical correctness comes from service.apply.
        snap.render_receipt(pid,wid,str(viewer_receipt.get('request_id') or rid+'-render'),
                            success,int(viewer_receipt.get('viewer_revision',-1)),
                            viewer_receipt['panel_id'],viewer_receipt['plugin'],ws['source_sha256'])
        new_status='succeeded' if success else 'render_failed'
        # Browser receipt is display evidence only; backend success was already
        # determined by the real checked tool executor.
        with self.service.projects._lock,self.service.projects._db() as db:
            db.execute('UPDATE bi_reasoning_runs SET status=?,updated_at=? WHERE run_id=?',
                       (new_status,timestamp(),rid))
            db.execute('UPDATE bi_reasoning_steps SET status=? WHERE run_id=?',
                       (new_status,rid))
        return self.get(pid,wid,rid)
