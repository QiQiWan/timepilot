"""FeatureLab 0.28: deterministic, source-pinned candidate work sessions.

Every recommendation is a proposal, never a changed DataContract. Candidate
previews use the existing trusted FeatureCompiler on the full ordered source
context before applying LIMIT. No LLM, arbitrary SQL or code execution.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from uuid import uuid4

from timepilot.feature_contracts import FeatureDefinition, FeatureSpecV2, canonical, digest
from timepilot.feature_compiler import FeatureCompiler, NUMERIC
from timepilot.feature_operator_registry import OPERATORS, SUPPORTED, compatible_fields
from timepilot.projects import now
from timepilot.enterprise_security import SecurityError

SUGGESTION_VERSION = '0.28.0'
TEMPLATES = ('basic', 'temporal', 'industrial', 'quality', 'advanced')
MAX_CANDIDATES = 60
MAX_PREVIEW = 8
_SUSPICIOUS_ID = re.compile(r'(^|[_\-\s])(id|uuid|guid|sn|serial|barcode|code|编号|编码|序列号|流水号|工单号|批次号)([_\-\s]|$)', re.I)
_SUSPICIOUS_TIME = re.compile(r'(^|[_\-\s])(time|date|timestamp|datetime|year|month|day|时间|日期|时刻)([_\-\s]|$)', re.I)


def infer_fields(contract):
    """Conservative field-role suggestions; no automatic confirmation."""
    suggestions=[]
    for field in contract.fields:
        typ=str(field.physical_type).upper()
        physical_numeric=bool(NUMERIC.match(typ))
        name=field.source_name
        warnings=[]
        if field.confirmation=='confirmed':
            guess=field.semantic_type
            confidence=1.0
            evidence=['字段角色由用户确认']
        elif _SUSPICIOUS_TIME.search(name):
            guess='datetime' if ('DATE' in typ or 'TIMESTAMP' in typ) else 'unknown'
            confidence=0.6
            evidence=['字段名称含时间含义；仍需确认真实时间类型或数值序号']
            warnings=['需核实时间语义，暂不作为自动数值特征输入']
        elif _SUSPICIOUS_ID.search(name):
            guess='id';confidence=0.75;evidence=['字段名称符合编号/标识符模式']
            warnings=['疑似标识符，不自动做算术和窗口统计']
        elif typ=='BOOLEAN':
            guess='boolean';confidence=0.95;evidence=['物理类型为 BOOLEAN']
        elif physical_numeric:
            guess='numeric';confidence=0.8;evidence=['物理类型可做数值计算']
        elif 'DATE' in typ or 'TIMESTAMP' in typ:
            guess='datetime';confidence=0.95;evidence=['物理类型为 DATE/TIMESTAMP']
            warnings=['日历或时长操作需要确认时间含义和时区']
        else:
            guess='category';confidence=0.55;evidence=['暂按类别/文本字段对待，建议人工核实']
        allowed=compatible_fields(contract, 'numeric')
        suggestions.append({'field_id':field.field_id,'name':name,'physical_type':typ,
            'proposed_semantic_type':guess,'proposed_role':field.role,
            'confidence':confidence,'evidence':evidence,'warnings':warnings,
            'eligible_numeric':field.field_id in allowed,
            'confirmed':field.confirmation=='confirmed'})
    return suggestions


def _slug(name):
    value=re.sub(r'[^a-z0-9_]+','_',name.lower()).strip('_')
    value=re.sub(r'_+','_',value)
    return (value[:26] if value and value[0].isalpha() else 'field')


def make_feature_name(op, fields, params, existing=()):
    """Collision-safe, deterministic technical name; client never needs to type it."""
    names='-'.join(_slug(f.source_name) for f in fields)
    suffix={'arithmetic.binary':{'+' :'sum','-':'diff','*':'product','/':'ratio'}.get(params.get('operator'), 'sum'),
            'lag.rows':'lag','difference.rows':'change','rolling.mean':'mean','rolling.std':'std',
            'rolling.min':'min','rolling.max':'max','rolling.duration.mean':'time_mean',
            'rolling.duration.min':'time_min','rolling.duration.max':'time_max','event.since':'since_event',
            'rolling.sum':'sum','rolling.median':'median','rolling.p90':'p90','rolling.range':'range',
            'rolling.cv':'cv','rolling.zscore':'zscore','relative.change':'pct_change',
            'transform.log1p':'log1p','transform.abs':'abs','transform.sqrt':'sqrt',
            'expression.safe':'custom'}[op]
    window=params.get('lag',params.get('window',params.get('window_seconds')))
    middle=f'_{window}' if window is not None else ''
    seed=f'{names}_{suffix}{middle}'
    safe=re.sub('[^a-zA-Z0-9_]','_',seed)[:58]
    if not re.match(r'[A-Za-z_]',safe):safe='f_'+safe
    if safe in existing:
        tail=digest({'op':op,'names':names,'params':params})[:6]
        safe=safe[:57]+'_'+tail
    return safe[:64]


def propose(contract, *, template='basic', budget=30, selected_fields=None, task_mode='exploration'):
    """Reproducible candidates for existing *executable* operators only."""
    if template not in TEMPLATES:raise ValueError('未知自动特征模板')
    if task_mode not in ('exploration','supervised'):raise ValueError('未知特征任务类型')
    if not 1<=budget<=MAX_CANDIDATES:raise ValueError('候选预算应为 1—60')
    allowed=compatible_fields(contract,'numeric')
    if selected_fields is not None:
        unknown=set(selected_fields)-{f.field_id for f in contract.fields}
        if unknown:raise ValueError('候选指定了不存在的字段 ID')
        allowed=[fid for fid in allowed if fid in selected_fields]
    by_id={f.field_id:f for f in contract.fields}
    numeric=[by_id[fid] for fid in allowed]
    events=[by_id[fid] for fid in compatible_fields(contract,'boolean')]
    time_ok=bool(contract.state=='confirmed' and contract.event_time_field_id and contract.timezone and
                by_id[contract.event_time_field_id].confirmation=='confirmed')
    candidates=[]; seen=set(); names={f.source_name for f in contract.fields}

    def add(op,inputs,params,reason):
        if op not in SUPPORTED or len(candidates)>=budget:return
        if op.startswith('rolling.duration.') or op=='event.since':
            if not time_ok:return
            if op=='event.since' and not contract.entity_field_ids:return
            params={**params,'order_field_id':contract.event_time_field_id}
        elif op!='arithmetic.binary' and time_ok:
            # Once time is confirmed, use real event order rather than import row order.
            params={**params,'order_field_id':contract.event_time_field_id}
        key=digest([op,list(inputs),params])
        if key in seen:return
        fields=[by_id[x] for x in inputs]
        name=make_feature_name(op,fields,params,names)
        title=OPERATORS[op].title
        if op=='arithmetic.binary':
            sign=params['operator']; title=f'{fields[0].source_name} {sign} {fields[1].source_name}'
        else:title=f'{fields[0].source_name} · {title}'
        fid='cand_'+key[:24]
        candidates.append({'candidate_id':fid,'feature_id':'feat_'+key[:24],
            'output_name':name,'display_name':title,'operator_id':op,
            'operator_version':OPERATORS[op].version,'input_field_ids':list(inputs),
            'parameters':params,'fingerprint':key,'reason':reason,'status':'ready',
            'scope':'exploration' if task_mode=='exploration' else 'requires_pit_validation'})
        seen.add(key);names.add(name)

    # Prefer modestly sized, interpretable candidates. Never pair every column.
    if template=='basic':
        for field in numeric:
            add('rolling.mean',[field.field_id],{'window':5,'min_periods':1},'五条记录内的历史平均')
            add('difference.rows',[field.field_id],{'lag':1},'相邻记录变化')
            if len(candidates)>=budget:break
    elif template=='temporal':
        for field in numeric:
            add('lag.rows',[field.field_id],{'lag':1},'上一条记录')
            add('difference.rows',[field.field_id],{'lag':1},'相邻记录变化')
            add('rolling.mean',[field.field_id],{'window':5,'min_periods':2},'短窗口平均')
            add('rolling.std',[field.field_id],{'window':5,'min_periods':2},'短窗口波动')
            if time_ok:add('rolling.duration.mean',[field.field_id],{'window_seconds':1800,'min_periods':1},'过去30分钟平均')
            if len(candidates)>=budget:break
    elif template=='industrial':
        for field in numeric:
            add('rolling.mean',[field.field_id],{'window':5,'min_periods':2},'传感器短窗口均值')
            add('rolling.std',[field.field_id],{'window':5,'min_periods':2},'传感器短窗口波动')
            add('rolling.max',[field.field_id],{'window':5,'min_periods':1},'历史窗口最大值')
            add('difference.rows',[field.field_id],{'lag':1},'传感器相邻变化')
            if len(candidates)>=budget:break
        if time_ok:
            for ev in events:add('event.since',[ev.field_id],{},'距上次已确认维护事件的秒数')
    elif template=='advanced':
        for field in numeric:
            fid=field.field_id
            for op in ('rolling.median','rolling.p90','rolling.range','rolling.cv','rolling.zscore'):
                add(op,[fid],{'window':12,'min_periods':3},'12条记录的稳健统计与状态偏离')
            add('relative.change',[fid],{'lag':1},'与上一条记录的相对变化率，除零置空')
            if len(candidates)>=budget:break
    elif template=='quality':
        for field in numeric:
            add('rolling.min',[field.field_id],{'window':5,'min_periods':1},'历史窗口下限')
            add('rolling.max',[field.field_id],{'window':5,'min_periods':1},'历史窗口上限')
            add('rolling.std',[field.field_id],{'window':5,'min_periods':2},'历史数据波动')
            if len(candidates)>=budget:break
    if len(numeric)>1 and template in ('basic','industrial','quality'):
        # Pair only the first few fields and do not silently combine conflicting units.
        for a,b in zip(numeric[:5],numeric[1:6]):
            if a.unit and b.unit and a.unit!=b.unit:continue
            add('arithmetic.binary',[a.field_id,b.field_id],{'operator':'-'},'相邻候选字段的差值，需业务确认')
    return candidates


class FeatureCandidateSessions:
    def __init__(self,projects,native,semantic,registry):
        self.projects=projects;self.native=native;self.semantic=semantic;self.registry=registry

    def _owner(self):return self.registry._actor()
    def _write(self,pid):self.registry._editor(pid)

    def _live(self,pid,aid,vid):
        source=self.semantic.source(pid,aid,vid)
        contract=self.semantic.latest(pid,aid,vid,source=source)
        if contract.state=='stale':raise ValueError('数据源已变化，请重新分析')
        return source,contract

    def _decode(self,row):
        doc=json.loads(row['state_json'])
        doc.update({'session_id':row['session_id'],'revision':row['revision'],
            'source_sha256':row['source_sha256'],'contract_revision':row['contract_revision'],
            'asset_id':row['asset_id'],'version_id':row['version_id'],
            'created_at':row['created_at'],'updated_at':row['updated_at']})
        return doc

    def _read(self,pid,sid, *, check_live=True):
        self.projects.get(pid)
        with self.projects._db() as db:
            row=db.execute('''SELECT * FROM feature_candidate_sessions
                WHERE project_id=? AND session_id=? AND owner_id=?''',(pid,sid,self._owner())).fetchone()
        if row is None:raise KeyError('候选特征会话不存在或没有权限')
        result=self._decode(row)
        if check_live:
            source=self.semantic.source(pid,result['asset_id'],result['version_id'])
            contract=self.semantic.latest(pid,result['asset_id'],result['version_id'],source=source)
            result['stale']=(source['sha256']!=row['source_sha256']
                or contract.revision!=row['contract_revision'] or contract.state=='stale')
        return result

    def get(self,pid,sid):return self._read(pid,sid)

    def latest(self,pid,aid,vid='base'):
        source=self.semantic.source(pid,aid,vid)
        contract=self.semantic.latest(pid,aid,vid,source=source)
        with self.projects._db() as db:
            row=db.execute('''SELECT * FROM feature_candidate_sessions
                WHERE project_id=? AND asset_id=? AND version_id=? AND owner_id=?
                ORDER BY updated_at DESC LIMIT 1''',(pid,aid,vid,self._owner())).fetchone()
        if not row:return None
        result=self._decode(row)
        result['stale']=(result['source_sha256']!=source['sha256']
            or result['contract_revision']!=contract.revision or contract.state=='stale')
        return result

    def create(self,pid,aid,vid, *, request_id,template='basic',budget=30,selected_fields=None,task_mode='exploration'):
        self._write(pid)
        if not request_id or len(request_id)>120:raise ValueError('请求 ID 无效')
        source,contract=self._live(pid,aid,vid)
        request_spec=digest({'asset_id':aid,'version_id':vid,'template':template,
            'budget':budget,'selected_field_ids':sorted(set(selected_fields or [])),
            'task_mode':task_mode})
        with self.projects._db() as db:
            previous=db.execute('''SELECT * FROM feature_candidate_sessions
                 WHERE project_id=? AND owner_id=? AND request_id=?''',(pid,self._owner(),request_id)).fetchone()
        if previous:
            previous_state=json.loads(previous['state_json'])
            if (previous['asset_id']!=aid or previous['version_id']!=vid
                or previous_state.get('request_spec')!=request_spec):
                raise ValueError('同一个请求 ID 已用于不同的候选生成参数')
            return self._read(pid,previous['session_id'])
        suggestions=infer_fields(contract)
        candidates=propose(contract,template=template,budget=budget,
                           selected_fields=selected_fields,task_mode=task_mode)
        doc={'template':template,'task_mode':task_mode,'budget':budget,
             'request_spec':request_spec,
             'selected_field_ids':selected_fields or [],'field_suggestions':suggestions,
             'candidates':candidates,'selected_candidate_ids':[c['candidate_id'] for c in candidates[:8]],
             'layout':{'mode':'quick','filter':'all','chart_mode':'trend'},'generator_version':SUGGESTION_VERSION,
             'notice':'候选按记录顺序计算；实际时长窗口仅使用已确认的时间字段。'}
        sid=str(uuid4());stamp=now()
        try:
            with self.projects._lock,self.projects._db() as db:
                db.execute('''INSERT INTO feature_candidate_sessions
                    (session_id,project_id,owner_id,request_id,asset_id,version_id,source_sha256,
                     contract_revision,revision,state_json,created_at,updated_at)
                     VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
                     (sid,pid,self._owner(),request_id,aid,vid,source['sha256'],
                      contract.revision,1,canonical(doc),stamp,stamp))
        except Exception as exc:
            if 'UNIQUE constraint failed' in str(exc):
                with self.projects._db() as db:
                    row=db.execute('''SELECT session_id FROM feature_candidate_sessions
                      WHERE project_id=? AND owner_id=? AND request_id=?''',(pid,self._owner(),request_id)).fetchone()
                if row:
                    matched=self._read(pid,row['session_id'])
                    if matched.get('request_spec')!=request_spec:
                        raise ValueError('同一个请求 ID 已用于不同的候选生成参数')
                    return matched
            raise
        return self._read(pid,sid)

    def update(self,pid,sid,revision, *, selected_candidate_ids=None,layout=None):
        self._write(pid)
        current=self._read(pid,sid)
        if current['stale']:raise ValueError('来源或字段契约已变化，旧会话失效')
        if current['revision']!=revision:raise ValueError('候选会话修订冲突，请刷新')
        doc={key:value for key,value in current.items() if key not in (
            'session_id','revision','source_sha256','contract_revision','asset_id',
            'version_id','created_at','updated_at','stale')}
        if selected_candidate_ids is not None:
            ids={c['candidate_id'] for c in doc['candidates']}
            if len(selected_candidate_ids)>100 or len(set(selected_candidate_ids))!=len(selected_candidate_ids):
                raise ValueError('候选选择数量无效或存在重复')
            if not set(selected_candidate_ids)<=ids:raise ValueError('选择的候选不属于当前会话')
            doc['selected_candidate_ids']=selected_candidate_ids
        if layout is not None:
            if not isinstance(layout,dict) or set(layout)-{'mode','filter','focus_candidate_id','chart_mode'}:
                raise ValueError('工作台布局参数无效')
            previous_layout=doc.get('layout',{})
            mode=layout.get('mode',previous_layout.get('mode','quick'))
            if mode not in ('quick','manual','custom'):raise ValueError('未知工作台模式')
            flt=layout.get('filter',previous_layout.get('filter','all'))
            if flt not in ('all','selected'):raise ValueError('未知候选筛选状态')
            chart=layout.get('chart_mode',previous_layout.get('chart_mode','trend'))
            if chart not in ('trend','distribution','scatter'):
                raise ValueError('未知预览图表类型')
            doc['layout']={'mode':mode,'filter':flt,'chart_mode':chart}
            focused=layout.get('focus_candidate_id',previous_layout.get('focus_candidate_id'))
            if focused in {x['candidate_id'] for x in doc['candidates']}:
                doc['layout']['focus_candidate_id']=focused
        with self.projects._lock,self.projects._db() as db:
            updated=db.execute('''UPDATE feature_candidate_sessions SET revision=revision+1,
                state_json=?,updated_at=? WHERE project_id=? AND session_id=? AND owner_id=?
                AND revision=?''',(canonical(doc),now(),pid,sid,self._owner(),revision))
            if updated.rowcount!=1:raise ValueError('候选会话已被并发修改')
        return self._read(pid,sid)

    def definitions(self,session,ids):
        by_id={c['candidate_id']:c for c in session['candidates']}
        if not ids or len(ids)>100 or len(ids)!=len(set(ids)) or not set(ids)<=set(by_id):
            raise ValueError('候选特征数量或标识无效')
        return [FeatureDefinition.model_validate({key:by_id[cid][key] for key in
            ('feature_id','output_name','display_name','operator_id','operator_version',
             'input_field_ids','parameters')}) for cid in ids]

    def to_spec(self,pid,sid,name,ids=None):
        session=self._read(pid,sid)
        if session['stale']:raise ValueError('来源或字段契约已变化，请重新生成候选')
        if session['task_mode']!='exploration':
            raise ValueError('监督候选须先在特征工作台完成预测任务与标签绑定')
        spec=FeatureSpecV2(name=name,source={'asset_id':session['asset_id'],
            'version_id':session['version_id'],'source_sha256':session['source_sha256']},
            contract_revision=session['contract_revision'],
            features=self.definitions(session,ids if ids is not None else session['selected_candidate_ids']))
        self.registry.validate(pid,spec)
        return spec

    def preview(self,pid,sid,ids,*,max_rows=30,scan_budget=200000):
        """Synchronous worker-side operation; the API queues it in durable jobs."""
        session=self._read(pid,sid)
        if session['stale']:raise ValueError('候选会话来源已变化')
        if not 1<=len(ids)<=MAX_PREVIEW:raise ValueError('一次最多预览 8 个特征')
        if not 1<=max_rows<=100:raise ValueError('预览条数必须在 1—100 之间')
        src,contract=self._live(pid,session['asset_id'],session['version_id'])
        if int(src.get('row_count') or 0)>scan_budget:
            raise ValueError('源表超过当前预览扫描预算，请先缩小数据范围')
        spec=FeatureSpecV2(name='candidate_preview',source={'asset_id':session['asset_id'],
           'version_id':session['version_id'],'source_sha256':session['source_sha256']},
           contract_revision=session['contract_revision'],features=self.definitions(session,ids))
        compiler=FeatureCompiler(contract,spec)
        from timepilot.native_bi import quote_identifier,safe_json
        cols=[f.output_name for f in spec.features]
        with self.native._connection() as con:
            self.native._install_effective_view(con,session['asset_id'],session['version_id'],src['columns'])
            table=compiler.compile_views(con)
            base_names=list(dict.fromkeys(contract_field.source_name for contract_field in contract.fields
                if contract_field.field_id in {fid for feat in spec.features for fid in feat.input_field_ids}))
            display_names=base_names+cols
            cur=con.execute('SELECT "__tp_rowid",'+','.join(quote_identifier(n) for n in display_names)+
                            f' FROM {table} ORDER BY "__tp_rowid" LIMIT ?',[max_rows])
            headers=[x[0] for x in cur.description]
            rows=[dict(zip(headers,safe_json(raw))) for raw in cur.fetchall()]
            from timepilot.feature_visualization import summarize_preview
            quality=summarize_preview(con,table,cols,sample_count=len(rows))
        return {'session_id':sid,'source_sha256':session['source_sha256'],
                'contract_revision':session['contract_revision'],
                'scope':'sample','statistics_scope':'full_source_up_to_preview_scan_budget',
                'sample_scope':'first_n_rows_after_full_context',
                'rows_returned':len(rows),'columns':headers,'rows':rows,'quality':quality,
                'feature_values_evaluated':True,'predictive_value_evaluated':False}
