"""Bounded Feature Agent: current authorized Schema -> safe tools -> observations.

LLM is optional. All actions are revalidated by the server; no chain-of-thought,
SQL/Python, host paths, tenant IDs or privileged publication operations are sent.
"""
from __future__ import annotations
import json
import re
from time import monotonic
from uuid import uuid4
from timepilot.feature_contracts import FeatureSpecV2,FeatureDefinition,SourceRef,PlanRequest,canonical,digest
from timepilot.feature_operator_registry import discover
from timepilot.feature_tool_manifest import manifest,validate,choose_tools
from timepilot.feature_registry import FeatureRegistry
from timepilot.feature_execution import FeatureJobEngine
from timepilot.projects import now

FIELD_PAIR=re.compile(r'\b([A-Za-z_][A-Za-z_0-9]*)\b\s*(?:列|字段)?\s*(?:和|与|\+|加上)\s*\b([A-Za-z_][A-Za-z_0-9]*)\b',re.I)


class FeatureAgent:
    def __init__(self,projects,native,semantic,registry,jobs,models=None):
        self.projects=projects;self.native=native;self.semantic=semantic
        self.registry=registry;self.jobs=jobs;self.models=models

    def tools(self,pid,aid,vid):
        src=self.semantic.source(pid,aid,vid)
        try:
            with self.native._connection():engine_ready=True
        except Exception:engine_ready=False
        return {'asset_id':aid,'source_sha256':src['sha256'],'tools':manifest(engine_ready=engine_ready)}

    def _local(self,message,contract,request_id,task=None):
        text=message.strip()
        fields={f.source_name.casefold():f for f in contract.fields}
        if any(x in text.casefold() for x in ('tsfresh','featuretools','高级特征可用性','多表特征引擎')):
            return [{'tool_id':'feature.advanced.capabilities','args':{}}],'ready',None
        if any(x in text for x in ('查看字段','查看数据字段','列出字段','字段列表','字段类型','有哪些列')):
            return [{'tool_id':'feature.schema.inspect','args':{}}],'ready',None
        if any(x in text for x in ('画像','分析数据规律','数据质量','分布情况')):
            return [{'tool_id':'feature.profile.get_or_create','args':{'request_id':request_id}}],'ready',None
        if any(x in text for x in ('可用算子','有什么特征','推荐特征','建议特征')):
            return [{'tool_id':'feature.candidate.propose','args':{}}],'ready',None
        if any(x in text for x in ('查看契约','数据契约','确认数据格式')):
            return [{'tool_id':'feature.contract.inspect','args':{}}],'ready',None
        if '移动平均' in text or '滚动平均' in text:
            duration_match=re.search(r'(\d+)\s*(秒|分钟|小时|天)',text)
            if duration_match and not (contract.event_time_field_id and contract.timezone
                    and any(f.field_id==contract.event_time_field_id and f.confirmation=='confirmed'
                        for f in contract.fields)):
                return [],'needs_clarification','时长窗口需要先确认真实时间字段和时区；也可明确改为按最近5条记录。'
            selected=[f for key,f in fields.items() if re.search(r'(?<![\w])'+re.escape(f.source_name)+r'(?![\w])',text,re.I)]
            if not selected:
                return [],'needs_clarification','请选择要计算移动平均的实际数值字段'
            raw=re.search(r'(?:最近|过去|前|按)?\s*(\d{1,3})\s*条',text)
            window=int(raw.group(1)) if raw else 5
            if not duration_match and not 1<=window<=240:
                return [],'needs_clarification','移动平均窗口应为 1—240 条记录'
            seconds=(int(duration_match.group(1))*{'秒':1,'分钟':60,'小时':3600,'天':86400}[duration_match.group(2)]) if duration_match else 0
            if duration_match and not 1<=seconds<=604800:
                return [],'needs_clarification','时长窗口应为1秒到7天'
            if any(f.semantic_type!='numeric' for f in selected):
                return [],'needs_clarification','所选字段包含非数值类型，请先选择数值列'
            definitions=[FeatureDefinition(feature_id='feat_ma_'+f.source_name,
                 output_name=(f.source_name+'_ma'+(str(seconds)+'s' if duration_match else str(window)))[:64],
                 operator_id='rolling.duration.mean' if duration_match else 'rolling.mean',
                 input_field_ids=[f.field_id],parameters=(
                   {'window_seconds':seconds,'order_field_id':contract.event_time_field_id,'min_periods':1}
                   if duration_match else {'window':window,'min_periods':window,
                      **({'order_field_id':contract.event_time_field_id}
                      if task and task.mode=='supervised' else {})}))
                 for f in selected[:8]]
            spec=FeatureSpecV2(name='移动平均特征',source=SourceRef(
                 asset_id=contract.asset_id,version_id=contract.version_id,
                 source_sha256=contract.source_sha256),contract_revision=contract.revision,
                 features=definitions,task=task if task and task.mode=='supervised' else None)
            actions=[{'tool_id':'feature.set.draft_create','args':{'spec':spec.model_dump(mode='json'),
                    'request_id':request_id}}]
            if any(x in text for x in ('在BI显示','在 BI 显示','可视化','图表展示','绘制','画图','显示特征')):
                actions.extend([
                    {'tool_id':'feature.run.create','args':{'set_id':'$step.0.set_id',
                        'request_id':request_id+'-materialize'}},
                    {'tool_id':'feature.viewer.open','args':{'set_id':'$step.0.set_id'}}])
            elif any(x in text for x in ('预览','看看结果','计算结果')):
                actions.append({'tool_id':'feature.set.preview','args':{'set_id':'$step.0.set_id',
                        'request_id':request_id+'-preview'}})
            return actions,'ready',None
        match=FIELD_PAIR.search(text)
        if match and any(x in text for x in ('相加','加到','加法','求和','新列','+')):
            a,b=match.group(1),match.group(2)
            if a.casefold() not in fields or b.casefold() not in fields:
                return [],'needs_clarification','当前数据中找不到指定源字段，请先核对字段列表'
            feature=FeatureDefinition(feature_id='feat_sum_'+a+'_'+b,
                output_name=(a+'_'+b+'_sum')[:64],operator_id='arithmetic.binary',
                input_field_ids=[fields[a.casefold()].field_id,fields[b.casefold()].field_id],
                parameters={'operator':'+'})
            spec=FeatureSpecV2(name=f'{a} 与 {b} 相加',source=SourceRef(
                   asset_id=contract.asset_id,version_id=contract.version_id,
                   source_sha256=contract.source_sha256),contract_revision=contract.revision,features=[feature],
                   task=task if task and task.mode=='supervised' else None)
            actions=[{'tool_id':'feature.set.draft_create','args':{'spec':spec.model_dump(mode='json'),
                    'request_id':request_id}}]
            if any(x in text for x in ('在BI显示','在 BI 显示','可视化','图表展示','绘制','画图','显示特征')):
                actions.extend([
                    {'tool_id':'feature.run.create','args':{'set_id':'$step.0.set_id',
                        'request_id':request_id+'-materialize'}},
                    {'tool_id':'feature.viewer.open','args':{'set_id':'$step.0.set_id'}}])
            return actions,'ready',None
        return [],'needs_clarification','请说明要查看的字段，或选择需要构造的特征与计算方法'

    def _llm(self,message,contract,allowed,task=None):
        if self.models is None:return None,None
        cfg=self.models.config()
        if not (cfg.get('enabled') and cfg.get('has_api_key')):return None,None
        # Only non-restricted field metadata goes to external models.
        fields=[{'field_id':f.field_id,'name':f.source_name,'type':f.physical_type,
                 'role':f.role,'unit':f.unit} for f in contract.fields if f.classification!='restricted']
        selected=choose_tools(message,allowed,max_tools=12)
        meta={'request':message,'fields':fields,'tools':[{'name':t['tool_id'],
            'schema':t['input_schema']} for t in selected],
            'task_contract':task.model_dump(mode='json') if task else None,
            'constraints':['任务中的预测时点、标签和实体键不可被改动',
                           '工具只能引用已授权真实字段与当前资产版本']}
        answer=self.models._message([
           {'role':'system','content':'你是工业特征工程的受限规划器。返回 JSON {"actions":[{"tool_id":"...","args":{...}}]}。'
                '只允许提供列出的工具，不能输出 SQL、代码、操作系统路径、审批或发布指令。'
                '无法确定字段或时序语义时 actions 为空。最多 8 项。不要输出内部推理。'},
           {'role':'user','content':canonical(meta)}],max_tokens=2200)
        parsed=self.models._parse_json(answer['content'])
        if not isinstance(parsed,dict) or not isinstance(parsed.get('actions'),list):
            raise ValueError('模型没有返回合法工具计划')
        return parsed['actions'],answer.get('model') or cfg.get('model')

    def plan(self,pid,request:PlanRequest):
        self.registry._editor(pid)
        contract=self.semantic.latest(pid,request.asset_id,request.version_id)
        if contract.state=='stale':raise ValueError('当前数据契约已过期')
        request_fingerprint=digest({'request':request,'source_sha256':contract.source_sha256,
                                    'contract_revision':contract.revision})
        with self.projects._db() as db:
            old=db.execute('''SELECT plan_id,plan_json FROM feature_agent_plans WHERE
               project_id=? AND actor_id=? AND request_id=?''',
               (pid,self.registry._actor(),request.request_id)).fetchone()
        if old:
            existing=json.loads(old['plan_json'])
            if existing.get('request_fingerprint')!=request_fingerprint:
                raise ValueError('请求 ID 与 TaskSpec、资产版本或契约参数冲突，禁止错误复用')
            return self.get(pid,old['plan_id'])
        actions,status,reason=self._local(request.message,contract,request.request_id,request.task)
        if request.task is not None and request.task.mode=='supervised':
            from timepilot.feature_safety import ensure_supervised_contract
            try:ensure_supervised_contract(contract,request.task)
            except ValueError as exc:
                # An invalid supervised task cannot silently become an exploratory draft.
                actions=[];status='needs_clarification';reason=str(exc)
        planner='deterministic';model_id=None;warnings=[]
        available=self.tools(pid,request.asset_id,request.version_id)
        capable=[t for t in available['tools'] if t['available'] and t['effect'] in ('read','draft','compute','presentation')]
        if not actions and status=='needs_clarification' and not (
                request.task and request.task.mode=='supervised' and reason):
            try:
                proposed,model_id=self._llm(request.message,contract,capable,request.task)
                if proposed:actions=proposed;status='ready';reason=None;planner='model_bounded'
            except Exception as exc:
                warnings.append('模型未能完成规划，已保留本地回退：'+type(exc).__name__)
        approved={t['tool_id'] for t in capable}
        validated=[]
        for item in actions[:9]:
            name=item.get('tool_id');args=item.get('args',{})
            if name not in approved:
                status='unsupported';reason='当前运行环境无法使用工具：'+str(name)
                validated=[];break
            params=validate(name,args,engine_ready=True)
            if name=='feature.set.draft_create':
                spec=params.spec
                if spec.source.asset_id!=request.asset_id or spec.source.version_id!=request.version_id:
                    raise ValueError('模型计划不得选择其他资产')
                if request.task and request.task.mode=='supervised' and (
                        spec.task is None or digest(spec.task)!=digest(request.task)):
                    raise ValueError('监督 Agent 计划必须绑定当前 TaskSpec，不能降级为探索性草稿')
                self.registry.validate(pid,spec)
            validated.append({'tool_id':name,'args':params.model_dump(mode='json')})
        if len(validated)>8:raise ValueError('最多八个步骤')
        if not validated and status=='ready':status='needs_clarification'
        if not validated:reason=reason or '没有形成符合当前数据条件的可执行动作'
        plan={'request':request.message,'request_fingerprint':request_fingerprint,
              'task':request.task.model_dump(mode='json') if request.task else None,
              'task_sha256':digest(request.task) if request.task else None,
              'asset_id':request.asset_id,'version_id':request.version_id,
              'source_sha256':contract.source_sha256,'contract_revision':contract.revision,
              'status':status,'reason':reason,'planner':planner,'model_id':model_id,
              'warnings':warnings,'actions':validated,'requires_review':any(x['tool_id'] in ('feature.set.draft_create',
                  'feature.run.create') for x in validated)}
        at=now();pid_plan=str(uuid4())
        with self.projects._lock,self.projects._db() as db:
            db.execute('''INSERT INTO feature_agent_plans
             (plan_id,project_id,actor_id,request_id,asset_id,source_sha256,plan_json,status,created_at,updated_at)
             VALUES(?,?,?,?,?,?,?,?,?,?)''',
             (pid_plan,pid,self.registry._actor(),request.request_id,request.asset_id,
              contract.source_sha256,canonical(plan),status,at,at))
            for i,call in enumerate(validated):
                db.execute('''INSERT INTO feature_agent_steps
                  (step_id,plan_id,ordinal,tool_id,args_json,status,created_at)
                  VALUES (?,?,?,?,?,?,?)''',
                  (str(uuid4()),pid_plan,i,call['tool_id'],canonical(call['args']),'pending',at))
        return self.get(pid,pid_plan)

    def get(self,pid,plan_id):
        self.projects.get(pid)
        with self.projects._db() as db:
            row=db.execute('''SELECT * FROM feature_agent_plans WHERE project_id=? AND plan_id=?
                 AND actor_id=?''',(pid,plan_id,self.registry._actor())).fetchone()
            steps=db.execute('''SELECT ordinal,tool_id,status,result_json FROM feature_agent_steps
                WHERE plan_id=? ORDER BY ordinal''',(plan_id,)).fetchall() if row else []
        if row is None:raise KeyError('任务计划不存在或无权限')
        data=dict(row);data['plan']=json.loads(data.pop('plan_json'))
        data['steps']=[{'ordinal':r['ordinal'],'tool_id':r['tool_id'],'status':r['status'],
                        'result':json.loads(r['result_json']) if r['result_json'] else None} for r in steps]
        with self.projects._db() as db:
            observations=db.execute('''SELECT ordinal,kind,evidence_json,created_at
                FROM feature_agent_observations WHERE plan_id=? ORDER BY ordinal,created_at''',
                (plan_id,)).fetchall()
        data['observations']=[{'ordinal':r['ordinal'],'kind':r['kind'],
            'evidence':json.loads(r['evidence_json']),'created_at':r['created_at']}
            for r in observations]
        return data

    def _observe(self,db,plan_id,ordinal,kind,evidence):
        db.execute('''INSERT OR REPLACE INTO feature_agent_observations
            (observation_id,plan_id,ordinal,kind,evidence_json,created_at)
            VALUES (?,?,?,?,?,?)''',
            (str(uuid4()),plan_id,ordinal,kind,canonical(evidence),now()))

    @staticmethod
    def _resolve_args(args,prior,ordinal):
        # Only previous-step generated IDs may fill a later tool argument.
        resolved={}
        for key,value in args.items():
            if isinstance(value,str) and value.startswith('$step.'):
                match=re.fullmatch(r'\$step\.(\d+)\.(set_id|revision|job_id)',value)
                if not match or int(match.group(1))>=ordinal:
                    raise ValueError('步骤引用必须来自已完成的前序操作')
                previous=next((x for x in prior if x['ordinal']==int(match.group(1))),None)
                if not previous or previous['status']!='succeeded' or not isinstance(previous['result'],dict):
                    raise ValueError('前序操作尚未生成可用结果')
                value=previous['result'].get(match.group(2))
                if value is None:raise ValueError('前序操作结果缺少所需 ID')
            resolved[key]=value
        return resolved

    def _execute_tool(self,pid,plan,name,args):
        asset_id=plan['asset_id'];vid=plan['version_id']
        if name=='feature.schema.inspect':
            contract=self.semantic.latest(pid,asset_id,vid)
            return {'fields':[f.model_dump(mode='json') for f in contract.fields],
                    'scope':'full_schema','count':len(contract.fields)}
        if name=='feature.field.search':return self.semantic.search(pid,asset_id,vid,args.get('query',''))
        if name=='feature.contract.inspect' or name=='feature.contract.validate':
            contract=self.semantic.latest(pid,asset_id,vid)
            if name=='feature.contract.inspect':return contract.model_dump(mode='json')
            issues=[]
            if not contract.event_time_field_id:issues.append('真实采样时间未确认')
            if not contract.entity_field_ids:issues.append('设备/实体键未确认')
            if not contract.available_at_field_id:issues.append('数据业务可用时间未确认')
            return {'state':contract.state,'source_sha256':contract.source_sha256,'issues':issues}
        if name=='feature.operator.search':
            contract=self.semantic.latest(pid,asset_id,vid)
            return {'operators':discover(physical_types=[f.physical_type for f in contract.fields],
                has_time=bool(contract.event_time_field_id))}
        if name=='feature.profile.get_or_create':
            return self.jobs.create_profile(pid,asset_id,vid,args['request_id'])
        if name=='feature.advanced.capabilities':
            from timepilot.feature_advanced import capabilities
            return capabilities()
        if name=='feature.candidate.propose':
            contract=self.semantic.latest(pid,asset_id,vid)
            candidates=[{'field_id':f.field_id,'source_name':f.source_name,
                        'operator_id':'rolling.mean','window':5,'reason':'用于检验局部波动的候选特征，未经预测增益评价'}
                        for f in contract.fields if f.semantic_type=='numeric'][:10]
            return {'candidates':candidates,'status':'proposed_only','source_sha256':contract.source_sha256}
        if name in ('feature.spec.validate','feature.set.draft_create'):
            spec=FeatureSpecV2.model_validate(args['spec'])
            if spec.source.asset_id!=asset_id or spec.source.version_id!=vid:
                raise ValueError('计划引用其他资产')
            if name=='feature.spec.validate':return self.registry.validate(pid,spec)
            rec=self.registry.create(pid,spec,args['request_id'])
            return {'set_id':rec['set_id'],'revision':rec['revision'],'state':rec['state']}
        if name in ('feature.set.preview','feature.run.create'):
            rec=self.registry.get(pid,args['set_id'])
            if rec['asset_id']!=asset_id or rec['version_id']!=vid:
                raise ValueError('当前计划禁止操作其他资产的特征集')
            return (self.jobs.create_preview(pid,args['set_id'],args['request_id'])
                    if name=='feature.set.preview' else self.jobs.create_run(pid,args['set_id'],args['request_id']))
        if name in ('feature.quality.inspect','feature.lineage.inspect','feature.publish.preview'):
            sid=args.get('query','')
            if not sid:raise ValueError('请指定实际特征集 ID')
            rec=self.registry.get(pid,sid)
            if rec['asset_id']!=asset_id:raise ValueError('特征集不属于当前源资产')
            if name=='feature.lineage.inspect':return self.registry.lineage(pid,sid)
            return self.registry.publication_preview(pid,sid)
        if name=='feature.viewer.open':
            sid=args['set_id']
            rec=self.registry.get(pid,sid)
            if rec['asset_id']!=asset_id or rec['version_id']!=vid:
                raise ValueError('禁止将其他资产的特征结果加入当前图表')
            from timepilot.feature_artifact_policy import eligible_artifact
            valid=eligible_artifact(self.projects,pid,rec,self.semantic)
            if valid is None:
                raise ValueError('只有真实计算成功、PIT与文件哈希均通过的物化结果才能交给 Viewer')
            return {'status':'pending_render','render_request_id':str(uuid4()),
                    'set_id':sid,'artifact_id':valid['artifact']['artifact_id'],
                    'artifact_sha256':valid['artifact']['sha256'],
                    'source_sha256':rec['source_sha256'],'set_revision':rec['revision'],
                    'note':'等待浏览器 Perspective 真正完成渲染并发送状态回执'}
        if name=='feature.run.get':return self.jobs.get_job(pid,args.get('query',''))
        if name=='feature.evaluation.plan':
            from timepilot.feature_evaluation import FeatureEvaluator
            protocol=args['protocol']
            rec=self.registry.get(pid,protocol['set_id'])
            if rec['asset_id']!=asset_id:raise ValueError('实验计划引用其他资产')
            return FeatureEvaluator(self.projects,self.native,self.semantic,self.registry,self.jobs).plan(pid,
                __import__('timepilot.feature_contracts',fromlist=['EvaluationProtocol']).EvaluationProtocol.model_validate(protocol))
        raise ValueError('工具缺少已验证执行器：'+name)

    def execute(self,pid,plan_id):
        self.registry._editor(pid)
        record=self.get(pid,plan_id);plan=record['plan']
        if record['status']=='succeeded':return record
        if record['status']=='awaiting_viewer':
            # Client must mount the actual Perspective workspace, then ACK.
            return record
        if record['status'] not in ('ready','running','awaiting_job'):
            raise ValueError('当前计划不能执行，请先补充信息或刷新')
        contract=self.semantic.latest(pid,plan['asset_id'],plan['version_id'])
        if contract.source_sha256!=plan['source_sha256'] or contract.revision!=plan['contract_revision']:
            raise ValueError('数据或字段契约变化，旧计划失效')
        if record['status']=='awaiting_job':
            last=next((s for s in reversed(record['steps']) if s['status']=='awaiting_job'),None)
            if last and last.get('result',{}).get('job_id'):
                job=self.jobs.get_job(pid,last['result']['job_id'])
                if job['status'] not in ('succeeded','failed_terminal','failed_retryable','cancelled'):
                    return record
                with self.projects._lock,self.projects._db() as db:
                    db.execute('''UPDATE feature_agent_steps SET status=?,result_json=?
                       WHERE plan_id=? AND ordinal=?''',
                       ('succeeded' if job['status']=='succeeded' else 'failed',canonical({'job':job}),plan_id,last['ordinal']))
                    self._observe(db,plan_id,last['ordinal'],'job_receipt',
                        {'status':job['status'],'kind':job['kind'],'job_id':job['job_id'],
                         'result_sha256':digest(job.get('result')) if job.get('result') is not None else None,
                         'error_code':job.get('error_code')})
                if job['status']!='succeeded':
                    with self.projects._lock,self.projects._db() as db:
                        db.execute("UPDATE feature_agent_plans SET status='failed',updated_at=? WHERE plan_id=?",(now(),plan_id))
                    return self.get(pid,plan_id)
        with self.projects._lock,self.projects._db() as db:
            db.execute("UPDATE feature_agent_plans SET status='running',updated_at=? WHERE plan_id=?",(now(),plan_id))
        for step in self.get(pid,plan_id)['steps']:
            if step['status']=='succeeded':continue
            if step['status']!='pending':continue
            name=step['tool_id'];call=plan['actions'][step['ordinal']]
            t0=monotonic()
            try:
                engine_ready=any(t['available'] and t['tool_id']==name for t in
                                 self.tools(pid,plan['asset_id'],plan['version_id'])['tools'])
                resolved=self._resolve_args(call['args'],self.get(pid,plan_id)['steps'],step['ordinal'])
                args=validate(name,resolved,engine_ready=engine_ready).model_dump(mode='json')
                result=self._execute_tool(pid,plan,name,args)
                state=('awaiting_viewer' if isinstance(result,dict) and result.get('status')=='pending_render'
                       else 'awaiting_job' if isinstance(result,dict) and result.get('job_id')
                            and result.get('status') in ('queued','running') else 'succeeded')
            except Exception as exc:
                result={'error_code':type(exc).__name__,'error':str(exc)};state='failed'
            with self.projects._lock,self.projects._db() as db:
                db.execute('''UPDATE feature_agent_steps SET status=?,result_json=?
                   WHERE plan_id=? AND ordinal=?''',(state,canonical(result),plan_id,step['ordinal']))
                self._observe(db,plan_id,step['ordinal'],'tool_receipt',
                    {'tool_id':name,'status':state,'result_sha256':digest(result),
                     'elapsed_ms':round((monotonic()-t0)*1000,3),
                     'job_id':result.get('job_id') if isinstance(result,dict) else None,
                     'error_code':result.get('error_code') if isinstance(result,dict) else None})
                if state!='succeeded':
                    db.execute('UPDATE feature_agent_plans SET status=?,updated_at=? WHERE plan_id=?',
                               (state if state in ('awaiting_job','awaiting_viewer') else 'failed',now(),plan_id))
            if state!='succeeded':return self.get(pid,plan_id)
        with self.projects._lock,self.projects._db() as db:
            db.execute("UPDATE feature_agent_plans SET status='succeeded',updated_at=? WHERE plan_id=?",(now(),plan_id))
        return self.get(pid,plan_id)

    def confirm_render(self,pid,plan_id,*,render_request_id,artifact_id,
                       viewer_version,workspace,panel_count):
        """A UI render ACK, distinct from the already verified numerical result.

        Only the owning project user can ACK an issued render request. Server
        re-checks the published artifact gate. Browser receipt alone never
        authorizes data or establishes numerical correctness.
        """
        self.registry._editor(pid)
        state=self.get(pid,plan_id)
        if state['status']!='awaiting_viewer':
            raise ValueError('当前任务没有等待中的 Perspective 渲染步骤')
        step=next((x for x in state['steps'] if x['status']=='awaiting_viewer'),None)
        if not step or step['tool_id']!='feature.viewer.open':
            raise ValueError('缺少有效的图表渲染请求')
        result=step['result'] or {}
        if (result.get('render_request_id')!=render_request_id
                or result.get('artifact_id')!=artifact_id):
            raise ValueError('图表回执与服务端签发的渲染请求不一致')
        if not isinstance(workspace,dict) or len(canonical(workspace).encode())>256000:
            raise ValueError('Perspective 工作区回执格式不正确')
        panels=workspace.get('panels')
        if (type(panel_count) is not int or not isinstance(panels,dict)
                or panel_count<2 or panel_count>len(panels)):
            raise ValueError('工作区令牌缺少原始数据与特征结果双面板')
        if not viewer_version.startswith('5.6.1') or len(viewer_version)>60:
            raise ValueError('Viewer 运行版本与当前锁定的 Perspective 5.6.1 不一致')
        plan=state['plan']
        latest=self.semantic.latest(pid,plan['asset_id'],plan['version_id'])
        if (latest.source_sha256!=plan['source_sha256']
                or latest.revision!=plan['contract_revision']):
            raise ValueError('图表渲染时数据契约已经变化，旧计划失效')
        rec=self.registry.get(pid,result['set_id'])
        from timepilot.feature_artifact_policy import eligible_artifact
        valid=eligible_artifact(self.projects,pid,rec,self.semantic,artifact_id=artifact_id)
        if valid is None or valid['artifact']['sha256']!=result['artifact_sha256']:
            raise ValueError('图表对应特征产物已失效，拒绝完成计划')
        receipt={'status':'rendered','artifact_id':artifact_id,'viewer_version':viewer_version,
                 'workspace_sha256':digest(workspace),'panel_count':panel_count,
                 'source_sha256':rec['source_sha256'],'render_request_id':render_request_id}
        with self.projects._lock,self.projects._db() as db:
            changed=db.execute("""UPDATE feature_agent_steps SET status='succeeded',result_json=?
                  WHERE plan_id=? AND ordinal=? AND status='awaiting_viewer' """,
                (canonical({'viewer_receipt':receipt}),plan_id,step['ordinal']))
            if changed.rowcount!=1:raise ValueError('图表任务已被更新，请刷新')
            db.execute("UPDATE feature_agent_plans SET status='ready',updated_at=? WHERE plan_id=?",
                       (now(),plan_id))
            db.execute("""INSERT INTO feature_execution_receipts
              (receipt_id,project_id,plan_id,step_id,tool_id,status,result_sha256,
               artifact_id,viewer_receipt_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)""",
               (str(uuid4()),pid,plan_id,str(step['ordinal']),'feature.viewer.open','rendered',
                digest(receipt),artifact_id,canonical(receipt),now()))
            self._observe(db,plan_id,step['ordinal'],'viewer_render_receipt',receipt)
        return self.execute(pid,plan_id)

    def replan(self,pid,plan_id,feedback=''):
        """Explicit bounded retry with observation context, no replay of writes.

        A new plan is created rather than modifying the old audit trail.
        Previously completed draft/compute actions cannot be automatically
        replayed, even if the model suggests doing so.
        """
        record=self.get(pid,plan_id)
        if record['status'] not in ('failed','unsupported','needs_clarification'):
            raise ValueError('当前计划仍可执行，无需重新规划')
        previous=record['plan']
        count=int(previous.get('replan_count') or 0)
        if count>=2:raise ValueError('重新规划次数上限为2次，请创建新的独立任务')
        for step in record['steps']:
            if step['status']=='succeeded' and step['tool_id'] in (
                    'feature.set.draft_create','feature.set.preview',
                    'feature.profile.get_or_create','feature.run.create'):
                raise ValueError('上轮已有实际写入或计算动作，请先检查结果并新建任务，避免重复执行')
        current=self.semantic.latest(pid,previous['asset_id'],previous['version_id'])
        if current.source_sha256!=previous['source_sha256'] or current.revision!=previous['contract_revision']:
            raise ValueError('来源或字段契约已经变化，旧计划失效')
        correction=str(feedback or '').strip()
        if not correction:
            failed=next((s for s in reversed(record['steps']) if s['status']=='failed'),None)
            result=(failed.get('result') or {}) if failed else {}
            correction=(result.get('error') or previous.get('reason') or '重新核对当前可用工具')[:350]
        if len(correction)>500:raise ValueError('重新规划说明过长')
        from timepilot.feature_contracts import PlanRequest
        message=(previous['request']+'；补充条件：'+correction)[:2000]
        request_id='replan-'+digest({'plan':plan_id,'round':count+1,'feedback':correction})[:26]
        from timepilot.feature_contracts import TaskSpecV2
        previous_task=TaskSpecV2.model_validate(previous['task']) if previous.get('task') else None
        new=self.plan(pid,PlanRequest(message=message,asset_id=previous['asset_id'],
                    version_id=previous['version_id'],request_id=request_id,task=previous_task))
        with self.projects._lock,self.projects._db() as db:
            row=db.execute('SELECT plan_json FROM feature_agent_plans WHERE plan_id=?',
                           (new['plan_id'],)).fetchone()
            plan=json.loads(row['plan_json'])
            plan.update({'parent_plan_id':plan_id,'replan_count':count+1,
                'observed_failure_codes':[s.get('result',{}).get('error_code')
                   for s in record['steps'] if s['status']=='failed']})
            db.execute('UPDATE feature_agent_plans SET plan_json=?,updated_at=? WHERE plan_id=?',
                       (canonical(plan),now(),new['plan_id']))
        return self.get(pid,new['plan_id'])
