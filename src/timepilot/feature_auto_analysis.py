"""Source-pinned, resumable automatic FeatureLab analysis.

A bounded deterministic controller delegates all expensive computations to the
existing FeatureJobEngine. The control plane stores only sanitized plans,
job references, aggregate evidence and review decisions. No source data edits,
no arbitrary model generated SQL, and no implicit publication.
"""
from __future__ import annotations

import json
import hashlib
import re
from uuid import uuid4

from timepilot.feature_candidates import FeatureCandidateSessions
from timepilot.feature_auto_agent import (
    new_agent_state, receipt, reject_unexpected_keys, bounded_ids,
    model_adjust_selection, explain_from_claims, _allow_model,
    UnsafeAgentAction,
)
from timepilot.feature_contracts import canonical, digest
from timepilot.feature_time_inference import infer_time_fields
from timepilot.feature_auto_delivery import (supervision_options, confirmed_task, verified_delivery)
from timepilot.feature_operator_registry import compatible_fields
from timepilot.projects import now

PENDING={'queued','running','cancel_requested'}
ERRORS={'failed_retryable','failed_terminal','cancelled'}
AUTO_PREVIEW_MAX_ROWS=2_000_000
AUTO_FULL_PROFILE_THRESHOLD=200_000
TEMPLATES={'explore':'basic','anomaly':'quality','quality':'industrial',
           'classification':'industrial','regression':'temporal','forecast':'temporal'}


def interpret_goal(goal, task_hint='auto', contract=None):
    """Return a cautious intent with the reasoning, not a keyword-only guess.

    Rate/continuous targets override the generic defect/classification keyword.
    A description of classification *features* is exploratory, not a training
    request. Only an explicit user task_hint has authority to decide a task.
    """
    if task_hint!='auto':
        if task_hint not in TEMPLATES:raise ValueError('Unknown analysis objective')
        return {'objective':task_hint,'confidence':1.0,'source':'user',
                'reason':'用户明确指定分析目标','requires_confirmation':False}
    text=str(goal or '').casefold().strip()
    score={key:0 for key in TEMPLATES}
    # Context excludes descriptions of possible downstream tasks.
    exploratory=(('分析' in text or '查看' in text or 'explore' in text) and
                 any(x in text for x in ('分布','特征','统计','相关性','distribution','statistics')))
    rate=any(word in text for word in ('不良率','故障率','合格率','良率','次品率','比例','连续值','数值预测','rate','ratio','percentage','regression'))
    future=any(word in text for word in ('预测','未来','forecast','predict','下一周期','下个月'))
    defect=any(word in text for word in ('不良品','缺陷','废品','合格/不合格','二分类','类别','classify','classification','ng_flag','故障是否发生'))
    if exploratory:
        score['explore']+=8
    if future:
        score['regression' if rate else 'forecast']+=5
    if rate:
        score['regression']+=5
    if defect and not exploratory and not rate:
        score['classification']+=6
    if ('质量分类' in text or '二分类' in text or 'defect classification' in text) and not exploratory:
        score['classification']+=5
    if any(w in text for w in ('异常','离群','outlier','anomaly')):
        score['anomaly']+=5
    if any(w in text for w in ('工艺质量','过程质量','稳定性','process stability')):
        score['quality']+=5
    if any(w in text for w in ('规律','分布','统计','探索','关联','correlation')):
        score['explore']+=4
    # If the user asks to predict a specific failure event, prefer a label task,
    # but ask for target confirmation before actual supervised evaluation.
    if future and any(w in text for w in ('故障','停机','失效')) and not rate:
        score['classification']+=7
    if not any(score.values()):score['explore']=1
    rank=sorted(score.items(),key=lambda item:(-item[1],item[0]))
    winner=rank[0][0];top=rank[0][1];next_best=rank[1][1]
    confidence=round(min(0.98,0.45+0.08*(top-next_best)+0.03*top),2)
    ambiguous=top-next_best<3 or (winner in ('classification','regression','forecast') and not
               any(f.role=='label' and f.confirmation=='confirmed' for f in (contract.fields if contract else [])))
    return {'objective':winner,'confidence':confidence,'source':'rules',
            'reason':('比例/连续值优先按回归目标理解' if rate else
                      '识别到探索性分析语境' if exploratory else '依据任务词义给出候选方向'),
            'requires_confirmation':ambiguous,
            'recommendation':'监督建模仍需人工确认标签和预测决策时间' if ambiguous else ''}


def classify_goal(goal,task_hint='auto'):
    return interpret_goal(goal,task_hint)['objective']


def choose_template(objective,contract,profile):
    names=[f.source_name.casefold() for f in contract.fields]
    industrial=any(any(x in name for x in ('pressure','temp','rpm','vibration','current','voltage','machine','sensor')) for name in names)
    if objective=='explore' and industrial:return 'industrial'
    return TEMPLATES[objective]




def ranked_numeric_fields(contract,profile,budget):
    """Rank usable fields only by full-source UNSUPERVISED quality.

    A target column or time/ID column can never be promoted by this sorter.
    No validation label or target association is read here.
    """
    usable=compatible_fields(contract,'numeric')
    stats={x['field_id']:x for x in (profile or {}).get('fields',[])}
    quality=[]
    for idx,fid in enumerate(usable):
        evidence=stats.get(fid,{})
        miss=evidence.get('null_rate')
        unique=evidence.get('distinct_count')
        std=evidence.get('std')
        insufficient=bool(miss is not None and miss>0.6 or
                          unique is not None and unique<2 or
                          std is not None and std<1e-12)
        if insufficient:continue
        fraction=1.0 if miss is None else max(0.0,1.0-float(miss))
        variety=0.5 if unique is None else min(1.0,max(0.0,float(unique)-1)/12.0)
        quality.append((-(fraction*(0.7+0.3*variety)),idx,fid))
    quality.sort()
    limit=min(len(quality),max(8,budget))
    return [fid for _,_,fid in quality[:limit]]

def candidate_quality(candidates,quality):
    indexed={x.get('feature_name'):x for x in quality}
    result=[]
    for candidate in candidates:
        stats=indexed.get(candidate['output_name'])
        if stats is None:
            result.append({'candidate_id':candidate['candidate_id'],'output_name':candidate['output_name'],
                           'status':'unverified','reason':'尚无全量计算证据','quality':None})
            continue
        missing=stats.get('null_rate')
        if missing is None or stats.get('rows_scanned',0)<2:
            status='review';reason='有效观测数不足'
        elif stats.get('finite_count',0)<2 or stats.get('constant_on_full_data'):
            status='exclude';reason='常量、全缺失或有效值少于 2'
        elif missing>0.60:
            status='exclude';reason='缺失或非有限值占比超过 60%'
        elif missing>0.25:
            status='review';reason='缺失或非有限值占比超过 25%'
        else:
            status='recommended';reason='通过有效性和覆盖率检查；尚未评价预测价值'
        result.append({'candidate_id':candidate['candidate_id'],'output_name':candidate['output_name'],
                       'display_name':candidate['display_name'],'operator_id':candidate['operator_id'],
                       'status':status,'reason':reason,'quality':stats})
    return result


def aggregate_report(profile,candidates,quality_rows,time_fields,objective,source_sha):
    approved=[x for x in quality_rows if x['status']=='recommended']
    warnings=[]
    if objective in ('classification','regression','forecast'):
        warnings.append('尚未评价预测收益：需要已确认的标签、可用时点和防泄漏验证')
    if not (profile or {}).get('time',{}).get('status')=='verified':
        warnings.append('尚确认日历时间；按行窗口不代表真实时长')
    if any(x['status']=='unverified' for x in quality_rows):
        warnings.append('部分候选缺少实际计算证据，不给出保留建议')
    fields=(profile or {}).get('fields') or []
    missing=[f for f in fields if f.get('null_rate') is not None and f['null_rate']>0.1]
    return {'schema_version':1,'source_sha256':source_sha,'objective':objective,
            'profile_scope':(profile or {}).get('scope','unavailable'),
            'row_count':(profile or {}).get('rows_scanned'),
            'field_count':len(fields), 'high_missing_fields':[
                {'name':f['source_name'],'null_rate':f['null_rate']} for f in missing[:15]],
            'time_interpretations':time_fields,
            'candidate_count':len(candidates),
            'evaluated_count':sum(x['status']!='unverified' for x in quality_rows),
            'recommended_count':len(approved),
            'review_count':sum(x['status']=='review' for x in quality_rows),
            'excluded_count':sum(x['status']=='exclude' for x in quality_rows),
            'quality_rows':quality_rows,
            'recommended_ids':[x['candidate_id'] for x in approved[:8]],
            'predictive_value_evaluated':False,
            'limitations':warnings,
            'next_actions':['查看候选的实测证据','存为可复用的特征集草稿',
                            '正式计算所选特征','标签与时间可用性确认后进行模型对照']}


def advanced_algorithm_advice(contract,profile,objective):
    """Evidence-backed optional algorithm proposals, never auto-launch expensive jobs."""
    topo=(profile or {}).get('topology') or {}
    rows=int((profile or {}).get('rows_scanned') or 0)
    choices=[]
    if topo.get('safe_for_windows') and rows>=100:
        choices.append({'engine':'tsfresh','eligible':True,'requires_confirmation':True,
            'estimated_cost':'high','why':'存在经全量检查的时间/设备分组，可按预算计算时序特征',
            'guard':'正式运行需指定实例级截止时间、最少观测数和算子预算'})
    if topo.get('partition_field_ids') and rows>=100:
        choices.append({'engine':'featuretools','eligible':False,'requires_confirmation':True,
            'estimated_cost':'high','why':'发现已确认实体字段；仍需核实多表关联键和独立来源数据',
            'guard':'必须先提供关系表及逐表available_at，不允许自动发起JOIN'})
    return choices


def next_action_for_user(state:dict) -> dict:
    """Human-readable next step derived only from persisted task evidence."""
    if state.get('stale'):
        return {'code':'rerun_source_changed','title':'数据版本或字段契约已变化',
                'detail':'当前结果不再对应最新来源，请重新发起分析；旧分析仅供审计。',
                'action':'重新分析','can_continue':False}
    status=state.get('status');stage=state.get('stage')
    if status=='needs_input':
        return {'code':'partition_required','title':'需要确定大数据计算范围',
                'detail':'数据超出当前全量预算。请按设备、批次或时间范围建立明确分区，不能将局部结果当作全量结论。',
                'action':'核实分区规则','can_continue':False}
    if status=='failed':
        return {'code':'failed_review','title':'分析任务未完成',
                'detail':'查看任务错误后重试；失败结果不会作为有效特征或模型证据。',
                'action':'查看失败原因','can_continue':False}
    if status in ('profiling','previewing','evaluating') or stage in ('profile','preview','evaluation') and status!='ready':
        return {'code':'running','title':'正在计算实际数据',
                'detail':'可以离开页面。执行状态和来源指纹会保存，返回后继续查看。',
                'action':'查看任务进度','can_continue':True}
    if stage=='review' and not state.get('selection'):
        return {'code':'choose_candidates','title':'候选已计算，尚无拟保留特征',
                'detail':'查看未通过质量检查的原因，选择可靠候选；质量合格不等于提高预测准确率。',
                'action':'复核候选','can_continue':True}
    if stage=='review':
        return {'code':'confirm_materialization','title':'候选质量检查已完成',
                'detail':'确认候选并正式计算。存在可靠标签时，可在保存前绑定监督评价任务。',
                'action':'确认采用并计算','can_continue':True}
    if stage=='materialized':
        return {'code':'evaluation_pending','title':'已形成可核验的特征产物',
                'detail':'正式物化已完成，模型或探索评价仍需复核；请不要推断预测收益。',
                'action':'核查模型评价','can_continue':True}
    if stage=='evaluated':
        return {'code':'review_delivery','title':'已生成分析成果',
                'detail':'查看报告、模型对照和 BI 视图，并核实来源及产物 SHA256；正式发布仍需审核。',
                'action':'查看报告与资产','can_continue':True}
    return {'code':'awaiting_execution','title':'准备分析数据',
            'detail':'选择数据并描述希望理解的问题。系统将提供数据质量和特征建议。',
            'action':'开始自动分析','can_continue':True}


class FeatureAutoAnalysis:
    def __init__(self,projects,native,semantic,registry,jobs,models=None):
        self.projects=projects
        self.semantic=semantic
        self.registry=registry
        self.jobs=jobs
        self.candidates=FeatureCandidateSessions(projects,native,semantic,registry)
        self.models=models

    def _actor(self):return self.registry._actor()

    def _row(self,pid,sid):
        self.projects.get(pid)
        with self.projects._db() as db:
            row=db.execute('''SELECT * FROM feature_analysis_sessions
                WHERE project_id=? AND owner_id=? AND session_id=?''',
                (pid,self._actor(),sid)).fetchone()
        if row is None:raise KeyError('Analysis session unavailable for this project and user')
        state=json.loads(row['state_json'])
        state.update({'session_id':row['session_id'],'asset_id':row['asset_id'],
                      'version_id':row['version_id'],'revision':row['revision'],
                      'source_sha256':row['source_sha256'],
                      'contract_revision':row['contract_revision'],
                      'created_at':row['created_at'],'updated_at':row['updated_at']})
        return state

    def _current(self,pid,state):
        # A removed asset/version is a stale analysis, not an uncaught 404 that
        # prevents the user from seeing the historical task and its audit trail.
        # Authorization errors are intentionally not swallowed.
        try:
            src=self.semantic.source(pid,state['asset_id'],state['version_id'])
            con=self.semantic.latest(pid,state['asset_id'],state['version_id'],source=src)
        except (KeyError, ValueError, FileNotFoundError, OSError):
            return None,None,False
        metadata_matches=(src['sha256']==state['source_sha256']
                          and con.revision==state['contract_revision'] and con.state!='stale')
        # Metadata alone cannot detect an in-place, same-length source edit.
        # Rehash the underlying immutable snapshot for authoritative status.
        if metadata_matches:
            verifier=getattr(self.jobs.native,'verify',None)
            if callable(verifier):
                try:metadata_matches=verifier(state['asset_id']).get('valid') is True
                except KeyError:
                    # Test adapters may use synthetic assets outside the native
                    # store; actual persisted assets are always checked above.
                    pass
                except (OSError,ValueError):metadata_matches=False
        return src,con,metadata_matches

    def get(self,pid,sid):
        state=self._row(pid,sid)
        _,_,valid=self._current(pid,state)
        state['stale']=not valid
        if not valid:state['notice']='Source data or field contract changed; rerun analysis'
        state['next_action']=next_action_for_user(state)
        state['recovery']=self._recovery(pid,state)
        return state

    def _recovery(self,pid,state):
        if state.get('stale'):
            return {'action':'new_analysis','retryable':False,'reason':'stale_source'}
        status,stage=state.get('status'),state.get('stage')
        if status=='failed' or (status=='ready' and stage=='materialized' and state.get('evaluation_error')):
            ids=([state.get('profile_job_id')] if stage=='profile' else
                 list(state.get('preview_job_ids') or []) if stage=='preview' else
                 [state.get('materialize_job_id')] if stage=='materializing' else
                 [state.get('evaluation_job_id')] if stage in ('materialized','evaluation') else [])
            try:
                jobs=[self.jobs.get_job(pid,j) for j in ids if j]
            except (KeyError,ValueError):jobs=[]
            failed=[x for x in jobs if x['status'] in ERRORS and int(x.get('attempts') or 0)<3]
            if failed:
                return {'action':'retry','retryable':True,'reason':'failed_job',
                        'job_ids':[x['job_id'] for x in failed]}
            if stage=='materializing' and jobs and all(x['status']=='succeeded' for x in jobs):
                return {'action':'rebuild','retryable':False,'reason':'invalid_artifact'}
            return {'action':'review','retryable':False,'reason':'no_retryable_job'}
        return {'action':'none','retryable':False,'reason':'not_required'}

    def latest(self,pid,aid,vid='base'):
        self.semantic.source(pid,aid,vid)
        with self.projects._db() as db:
            row=db.execute('''SELECT session_id FROM feature_analysis_sessions WHERE
              project_id=? AND owner_id=? AND asset_id=? AND version_id=?
              ORDER BY created_at DESC LIMIT 1''',(pid,self._actor(),aid,vid)).fetchone()
        return self.get(pid,row['session_id']) if row else None

    def _save(self,pid,state,updates):
        omit={'session_id','asset_id','version_id','revision','source_sha256',
              'contract_revision','created_at','updated_at','stale','next_action','notice'}
        persist={k:v for k,v in state.items() if k not in omit}
        persist.update(updates)
        with self.projects._lock,self.projects._db() as db:
            changed=db.execute('''UPDATE feature_analysis_sessions SET state_json=?,
              revision=revision+1,updated_at=? WHERE project_id=? AND owner_id=?
              AND session_id=? AND revision=?''',
              (canonical(persist),now(),pid,self._actor(),state['session_id'],state['revision']))
            if changed.rowcount!=1:raise ValueError('Analysis session updated elsewhere; refresh')
        return self.get(pid,state['session_id'])

    def _llm_intent(self,goal,contract,default):
        """Optional advisory plan: no source values or execution instructions."""
        if self.models is None:return default,'rules',None
        try:
            cfg=self.models.config()
            if not cfg.get('enabled') or not cfg.get('has_api_key'):
                return default,'rules',None
            if any(f.classification=='restricted' for f in contract.fields):
                return default,'rules','External model planning disabled for restricted fields'
            fields=[{'name':f.source_name if f.classification=='public' else 'column_'+str(i+1),
                     'type':f.physical_type,'role':f.role}
                    for i,f in enumerate(contract.fields[:40])]
            answer=self.models._message([
                {'role':'system','content':('Choose the best feature analysis objective from '
                  'explore, anomaly, quality, classification, regression, forecast. '
                  'Return only JSON: {"objective":"explore","reason":"..."}. '
                  'Do not propose code, SQL, data edits, or unsupported actions.')},
                {'role':'user','content':canonical({'goal':goal,'fields':fields})}],max_tokens=240)
            result=self.models._parse_json(answer.get('content',''))
            reject_unexpected_keys(result,{'objective','reason'})
            if result.get('objective') in TEMPLATES:
                return result['objective'],'llm',None
            return default,'rules','Model objective was invalid; deterministic fallback used'
        except UnsafeAgentAction:
            return default,'rules','模型提出了不支持的任务或工具请求，已使用规则规划'
        except Exception as exc:
            return default,'rules','Model planning unavailable; deterministic fallback used: '+type(exc).__name__

    def _llm_strategy(self,goal,objective,contract,profile,allowlisted,template,budget):
        """Optional LLM proposal, strictly constrained by deterministic role/quality gates.

        Only aggregate field metadata is sent; SQL, Python and tool execution are
        never accepted. The returned ids must be a subset of the safe candidate set.
        """
        result=(allowlisted,template,'rules','Statistical coverage/variability order')
        if not allowlisted or self.models is None:return result
        try:
            config=self.models.config()
            if not config.get('enabled') or not config.get('has_api_key'):return result
            if any(f.classification=='restricted' for f in contract.fields):return result
            stats={f['field_id']:f for f in (profile or {}).get('fields',[])}
            safe=set(allowlisted)
            fields=[]
            for f in contract.fields:
                if f.field_id not in safe:continue
                evidence=stats.get(f.field_id,{})
                fields.append({'id':f.field_id,
                               'name':f.source_name if f.classification=='public' else 'internal_column_'+str(len(fields)+1),
                               'unit':f.unit if f.classification=='public' else None,
                               'missing_fraction':evidence.get('null_rate'),
                               'unique_count_band':('many' if (evidence.get('distinct_count') or 0)>20 else 'few'),
                               'has_variation':bool(evidence.get('std') and evidence['std']>1e-12)})
            prompt={'goal':goal,'objective':objective,'default_template':template,
                    'allowed_templates':list(TEMPLATES.values()),
                    'fields':fields[:32],'max_features':min(len(allowlisted),max(2,budget//2))}
            answer=self.models._message([
                {'role':'system','content':('Recommend safe unsupervised feature analysis fields. '
                   'Return ONLY JSON {"field_ids":["..."],"template":"basic",'
                   '"reason":"..."}. Field ids must be from supplied list, maximum '
                   'features must be obeyed. Do not request raw values, execution '
                   'of code, SQL, unconfirmed labels or unsupported transformations.')},
                {'role':'user','content':canonical(prompt)}],max_tokens=500)
            proposal=self.models._parse_json(answer.get('content',''))
            reject_unexpected_keys(proposal,{'field_ids','template','reason'})
            ids=bounded_ids(proposal.get('field_ids'),allowlisted,max(2,budget//2))
            requested=proposal.get('template',template)
            if requested not in TEMPLATES.values():return result
            return ids,requested,'llm',('模型选择了 '+str(len(ids))+' 个质量允许字段和 '+requested+
               ' 模板，实际效果仍需计算验证')
        except UnsafeAgentAction:
            return (allowlisted,template,'rules','blocked:invalid_model_strategy')
        except Exception:
            return result

    def create(self,pid,aid,vid,*,goal,task_hint,request_id,budget=16):
        self.registry._editor(pid)
        if not request_id or len(request_id)>120:raise ValueError('Invalid request id')
        if not 1<=budget<=64:raise ValueError('Automatic candidate budget must be 1..64')
        if not str(goal).strip():goal='Explore variation, anomalies and useful features'
        if len(goal)>1000:raise ValueError('Analysis goal too long')
        src=self.semantic.source(pid,aid,vid)
        con=self.semantic.latest(pid,aid,vid,source=src)
        intent=interpret_goal(goal,task_hint,con)
        objective=intent['objective']
        if con.state=='stale':raise ValueError('Source contract has expired')
        signature=digest({'asset_id':aid,'version_id':vid,'source':src['sha256'],
                          'contract_revision':con.revision,'goal':goal,'task_hint':task_hint,'budget':budget})
        with self.projects._db() as db:
            old=db.execute('''SELECT session_id,state_json FROM feature_analysis_sessions
                WHERE project_id=? AND owner_id=? AND request_id=?''',
                (pid,self._actor(),request_id)).fetchone()
        if old:
            if json.loads(old['state_json']).get('signature')!=signature:
                raise ValueError('The request id was used for different analysis inputs')
            return self.get(pid,old['session_id'])
        # A strong rule diagnosis guards against an LLM flipping a rate regression
        # into classification solely because the goal mentions defective parts.
        if task_hint == 'auto':
            proposal,planner,planning_warning=self._llm_intent(goal,con,objective)
            if planner=='llm' and intent['confidence']>=0.80 and proposal!=objective:
                planning_warning='模型目标与高置信度规则冲突，已保留可解释的规则目标'
                objective=objective;planner='rules'
            else:
                objective=proposal
        else:
            planner,planning_warning='rules',None
        model_configured,_=_allow_model(self.models,con)
        agent=new_agent_state(model_configured)
        agent['mode']='llm' if planner=='llm' else 'rules'
        agent['model_guided_steps']=int(planner=='llm')
        agent['model_decisions']=[{'phase':'objective','provider':planner,
            'result':'fallback' if planning_warning else 'accepted',
            'code':'model_fallback' if planning_warning else None}]
        agent['plan']={'objective':objective,'budget':budget,
            'source_sha256':src['sha256'],'contract_revision':con.revision,
            'tools':['feature.contract.inspect','feature.profile.compute',
                     'feature.candidate.propose','feature.candidate.preview',
                     'feature.quality.screen','feature.report.explain'],
            'requires_user_confirmation':['feature.set.draft_create',
                                          'feature.run.create','feature.publish']}
        agent=receipt(agent,'feature.contract.inspect','succeeded',
                      {'source_sha256':src['sha256'],'contract_revision':con.revision,
                       'field_count':len(con.fields),'objective':objective})
        sid=str(uuid4())
        initial={'goal':goal,'objective':objective,'intent_advice':intent,
                 'budget':budget,'signature':signature,
                 'status':'profiling','stage':'profile','progress':0.05,
                 'profile_job_id':None,'preview_job_ids':[],
                 'candidate_session_id':None,'selection':[],
                 'report':None,'artifact':None,'feature_set_id':None,'evaluation_job_id':None,'evaluation':None,
                 'supervision':None,'bi_receipt':None,
                 'warnings':([planning_warning] if planning_warning else []), 'planner':planner, 'predictive_value_evaluated':False}
        n=int(src.get('row_count') or 0)
        if n>AUTO_PREVIEW_MAX_ROWS:
            initial.update(status='needs_input',stage='profile',progress=0,
                           warnings=['源数据超过200万行的自动计算预算；请在高级任务中确认增量/分区计算，不会静默抽样或误报全量结果。'])
        elif n>AUTO_FULL_PROFILE_THRESHOLD:
            initial['warnings'].append('大数据后台模式：将执行受限并持久化的全量 SQL 计算；可能需要更长时间。')
        if n==0:
            initial.update(status='needs_input',stage='profile',progress=0,
                           warnings=['The source asset contains no records.'])
        if initial['status']=='profiling':
            profile=self.jobs.create_profile(pid,aid,vid,request_id+'-profile')
            initial['profile_job_id']=profile['job_id']
            agent=receipt(agent,'feature.profile.compute','queued',{'job_id':profile['job_id']})
        initial['agent']=agent

        try:
            with self.projects._lock,self.projects._db() as db:
                db.execute('''INSERT INTO feature_analysis_sessions
                  (session_id,project_id,owner_id,request_id,asset_id,version_id,
                   source_sha256,contract_revision,revision,state_json,created_at,updated_at)
                  VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
                  (sid,pid,self._actor(),request_id,aid,vid,src['sha256'],con.revision,
                   1,canonical(initial),now(),now()))
        except Exception as exc:
            if 'UNIQUE constraint failed' in str(exc):
                with self.projects._db() as db:
                    previous=db.execute('''SELECT session_id,state_json FROM feature_analysis_sessions
                        WHERE project_id=? AND owner_id=? AND request_id=?''',
                        (pid,self._actor(),request_id)).fetchone()
                if previous is not None:
                    if json.loads(previous['state_json']).get('signature')!=signature:
                        raise ValueError('The request id was used for different analysis inputs')
                    return self.get(pid,previous['session_id'])
            raise
        return self.get(pid,sid)

    def advance(self,pid,sid):
        self.registry._editor(pid)
        state=self.get(pid,sid)
        if state['stale']:raise ValueError('Cannot advance stale source/contract')
        if state['status'] in ('ready','failed','needs_input'):
            return state
        source,contract,_=self._current(pid,state)
        if state['stage']=='profile':
            job=self.jobs.get_job(pid,state['profile_job_id'])
            if job['status'] in PENDING:return state
            if job['status'] in ERRORS:
                agent=receipt(state.get('agent') or new_agent_state(),
                              'feature.profile.compute','failed',{'job_id':state['profile_job_id']})
                return self._save(pid,state,{'status':'failed','error':job.get('error_text') or job['status'],
                                             'agent':agent})
            profile=self.jobs.get_profile(pid,job['result']['profile_id'])
            objective=state['objective']
            template=choose_template(objective,contract,profile)
            priority=ranked_numeric_fields(contract,profile,state['budget'])
            priority,template,strategy_source,strategy_reason=self._llm_strategy(
                state['goal'],objective,contract,profile,priority,template,state['budget'])
            topology=(profile or {}).get('topology')
            # The LLM's template/field ranking stays advisory. The executable
            # planner balances safe operator families using source-only evidence.
            suggested_template=template
            template='adaptive'
            candidate_session=self.candidates.create(pid,state['asset_id'],state['version_id'],
                request_id='auto-'+sid,template=template,budget=state['budget'],
                selected_fields=priority,task_mode='exploration',topology=topology,
                profile=profile,objective=objective,
                category_values={f['field_id']:[x['value'] for x in f.get('top_categories',[])[:2]]
                                 for f in profile.get('fields',[]) if f.get('top_categories')})
            candidates=candidate_session['candidates']
            agent_warning=(topology or {}).get('reasons') or []
            # Record server-executed tools; no model-supplied tool name is ever run.
            agent=receipt(state.get('agent') or new_agent_state(),
                          'feature.profile.compute','succeeded',
                          {'profile_id':job['result'].get('profile_id')})
            agent=receipt(agent,'feature.candidate.propose','succeeded',
                          {'candidate_session_id':candidate_session['session_id'],
                           'candidate_count':len(candidates),'template':template,
                           'family_counts':(candidate_session.get('strategy_plan') or {}).get('family_counts',{})})
            agent['plan']['template']=template
            agent['plan']['proposed_fields']=len(priority)
            if str(strategy_reason).startswith('blocked:'):
                agent['rejected_proposals']=int(agent.get('rejected_proposals',0))+1
            agent.setdefault('model_decisions',[]).append({'phase':'candidate_strategy',
                'provider':strategy_source,'result':'blocked' if str(strategy_reason).startswith('blocked:') else 'accepted'})
            agent['strategy']={'provider':strategy_source,'reason':str(strategy_reason)[:180],
                               'field_count':len(priority),'template':template}
            if strategy_source=='llm':
                agent['mode']='llm'
                agent['model_guided_steps']=int(agent.get('model_guided_steps',0))+1
            if not candidates:
                return self._save(pid,state,{'status':'needs_input','stage':'candidates',
                        'profile':profile,'time_fields':infer_time_fields(contract,profile),
                        'agent':agent,
                        'warnings':['No suitable numeric fields; confirm source data roles first']})
            groups=[candidates[i:i+8] for i in range(0,len(candidates),8)]
            ids=[]
            for i,group in enumerate(groups):
                job=self.jobs.create_candidate_preview(pid,candidate_session['session_id'],
                    [x['candidate_id'] for x in group],request_id='auto-'+sid+'-preview-'+str(i),
                    max_rows=30,scan_budget=min(AUTO_PREVIEW_MAX_ROWS,max(200000,int(source.get('row_count') or 0))))
                ids.append(job['job_id'])
            agent=receipt(agent,'feature.candidate.preview','queued',{'preview_jobs':len(ids)})
            return self._save(pid,state,{'status':'previewing','stage':'preview',
                  'agent':agent,'profile':profile,'time_fields':infer_time_fields(contract,profile),
                  'candidate_session_id':candidate_session['session_id'],
                  'preview_job_ids':ids,'template':template,
                  'priority_fields':priority,'strategy_source':strategy_source,
                  'strategy_plan':candidate_session.get('strategy_plan'),
                  'suggested_template':suggested_template,
                  'strategy_reason':strategy_reason,'topology':topology,
                  'warnings':list(state.get('warnings') or [])+agent_warning[:3],
                  'progress':0.4})
        if state['stage']=='preview':
            jobs=[self.jobs.get_job(pid,x) for x in state['preview_job_ids']]
            if any(j['status'] in PENDING for j in jobs):return state
            if any(j['status'] in ERRORS for j in jobs):
                failed=[j for j in jobs if j['status'] in ERRORS]
                return self._save(pid,state,{'status':'failed','error':'; '.join(
                    str(x.get('error_text') or x['status']) for x in failed)[:1500]})
            session=self.candidates.get(pid,state['candidate_session_id'])
            quality=[item for job in jobs for item in (job.get('result') or {}).get('quality',[])]
            ratings=candidate_quality(session['candidates'],quality)
            report=aggregate_report(state.get('profile'),session['candidates'],ratings,
                                     state.get('time_fields',[]),state['objective'],state['source_sha256'])
            report['advanced_options']=advanced_algorithm_advice(contract,state.get('profile'),state['objective'])
            report['strategy_plan']=session.get('strategy_plan')
            from timepilot.feature_search_orchestrator import summarize_search
            report['search_summary']=summarize_search(session['candidates'],ratings,
                budget=state['budget'],source_sha256=state['source_sha256'])
            report['intent_advice']=state.get('intent_advice')
            adjustment=model_adjust_selection(self.models,contract,state['goal'],report,
                                              report['recommended_ids'])
            selected=adjustment['selection']
            explanation=explain_from_claims(self.models,contract,report)
            agent=receipt(state.get('agent') or new_agent_state(),
                          'feature.candidate.preview','succeeded',
                          {'preview_jobs':len(jobs),'evaluated_count':report['evaluated_count']})
            agent=receipt(agent,'feature.quality.screen','succeeded',
                          {'recommended_count':report['recommended_count'],
                           'excluded_count':report['excluded_count'],
                           'review_count':report['review_count'],
                           'selection_count':len(selected)})
            agent=receipt(agent,'feature.report.explain','succeeded',
                          {'selection_count':len(explanation.get('claims',[]))},
                          origin=explanation['provider'])
            agent.setdefault('model_decisions',[]).extend([
                {'phase':'quality_ranking','provider':adjustment.get('provider'),
                 'result':'blocked' if adjustment.get('blocked') else 'accepted',
                 'code':adjustment.get('error_code')},
                {'phase':'evidence_explanation','provider':explanation.get('provider'),
                 'result':'blocked' if explanation.get('blocked') else 'accepted',
                 'code':explanation.get('error_code')}])
            agent['candidate_adjustment']={k:adjustment.get(k) for k in
                ('provider','reason','blocked','error_code')}
            agent['candidate_adjustment']['selected_ids']=selected
            agent['explanation_provider']=explanation['provider']
            guided=int(adjustment['provider']=='llm')+int(explanation['provider']=='llm_curated')
            if guided:
                agent['mode']='llm'
                agent['model_guided_steps']=int(agent.get('model_guided_steps',0))+guided
            agent['rejected_proposals']=int(agent.get('rejected_proposals',0))+sum(
                (bool(adjustment.get('blocked')),bool(explanation.get('blocked'))))
            warnings=list(state.get('warnings') or [])
            if adjustment.get('blocked') or explanation.get('blocked'):
                warnings.append('模型建议未通过工具或证据白名单校验；已使用规则结果。')
            return self._save(pid,state,{'status':'ready','stage':'review',
                'progress':0.7,'report':report,'selection':selected,
                'agent':agent,'explanation':explanation,'warnings':warnings})
        if state['stage']=='materializing':
            job=self.jobs.get_job(pid,state['materialize_job_id'])
            if job['status'] in PENDING:return state
            if job['status'] in ERRORS:
                return self._save(pid,state,{'status':'failed','error':job.get('error_text') or job['status']})
            # Run an actual exploratory evaluation on the source-pinned FeatureSet.
            # No label => no claims about accuracy or feature importance.
            from timepilot.feature_contracts import EvaluationProtocol, TaskSpecV2
            from timepilot.feature_evaluation import FeatureEvaluator
            feature_set=self.registry.get(pid,state['feature_set_id'])
            from timepilot.feature_artifact_policy import eligible_artifact
            materialized=job.get('result') or {}
            valid=eligible_artifact(self.projects,pid,feature_set,self.semantic,
                   artifact_id=materialized.get('artifact_id')) if materialized.get('artifact_id') else None
            if valid is None or valid['artifact']['sha256']!=materialized.get('sha256'):
                return self._save(pid,state,{'status':'failed','stage':'materializing',
                    'error':'计算任务返回的物化文件缺少有效数据库记录或 SHA256 校验失败',
                    'artifact':None})
            bound_task=TaskSpecV2.model_validate(feature_set['spec']['task']) if feature_set['spec'].get('task') else TaskSpecV2(
                task_id='auto-'+sid[:30],mode='exploration',task_type='profile')
            if bound_task.mode=='supervised':
                from timepilot.feature_contracts import SlicePolicy
                contract=self.semantic.get_revision(pid,feature_set['asset_id'],
                                    feature_set['version_id'],feature_set['contract_revision'])
                slice_ids=[f.field_id for f in contract.fields if f.confirmation=='confirmed'
                           and f.role in ('entity_key','batch_key','regime')][:3]
                protocol=EvaluationProtocol(set_id=feature_set['set_id'],
                    revision=feature_set['revision'],task=bound_task,
                    slices=SlicePolicy(field_ids=slice_ids))
            else:
                protocol=EvaluationProtocol(set_id=feature_set['set_id'],
                    revision=feature_set['revision'],task=bound_task)
            service=FeatureEvaluator(self.projects,self.jobs.native,self.semantic,self.registry,self.jobs)
            try:
                plan=service.plan(pid,protocol)
                evaluation=service.submit(pid,plan['plan_id'],'auto-'+sid+'-evaluation')
            except (ValueError, RuntimeError, KeyError) as exc:
                return self._save(pid,state,{'status':'ready','stage':'materialized',
                    'progress':0.88,'artifact':job['result'],
                    'evaluation_error':str(exc)[:500],
                    'warnings':[*(state.get('warnings') or []),
                        '物化完成，但受控模型/探索评价未能启动；产物保留待复核']})
            return self._save(pid,state,{'status':'evaluating','stage':'evaluation','progress':0.92,
                    'artifact':job['result'],'evaluation_job_id':evaluation['job_id']})
        if state['stage']=='evaluation':
            job=self.jobs.get_job(pid,state['evaluation_job_id'])
            if job['status'] in PENDING:return state
            if job['status'] in ERRORS:
                return self._save(pid,state,{'status':'ready','stage':'materialized',
                       'evaluation_error':str(job.get('error_text') or job['status']),
                       'warnings':[*(state.get('warnings') or []),
                           '模型/探索评价未完成；已验证物化产物仍可单独复核']})
            actual=job.get('result') or {}
            report=actual.get('report') or {}
            materialized=state.get('artifact') or {}
            rec=self.registry.get(pid,state['feature_set_id'])
            task=(rec['spec'].get('task') or {})
            if task.get('mode')=='supervised':
                same=(report.get('mode')=='supervised' and
                      report.get('evaluated_artifact_id')==materialized.get('artifact_id') and
                      report.get('evaluated_artifact_sha256')==materialized.get('sha256') and
                      report.get('evaluated_contract_revision')==state['contract_revision'] and
                      report.get('source_sha256')==state['source_sha256'] and
                      report.get('spec_sha256')==rec['spec_sha256'])
            else:
                artifact_report=report.get('feature_artifact') or {}
                same=(report.get('mode')=='exploration' and
                      artifact_report.get('artifact_id')==materialized.get('artifact_id') and
                      artifact_report.get('sha256')==materialized.get('sha256') and
                      report.get('source_sha256')==state['source_sha256'] and
                      report.get('spec_sha256')==rec['spec_sha256'])
            if not same:
                return self._save(pid,state,{'status':'failed','stage':'evaluation',
                  'error':'评价结果与已物化 Parquet 的 ID / SHA256 / 配方不一致，拒绝发布同源结论',
                  'evaluation':None})
            predictive=None
            if task.get('mode')=='supervised':
                frequencies=report.get('selection_frequency') or []
                comparisons=report.get('screening_recommendations') or []
                approved_gain=any(x.get('variant')=='selected_features' and
                                  x.get('verdict')=='review_candidate'
                                  for x in comparisons)
                predictive={'evidence_source':'outer_train_fold_only',
                  'artifact_id':materialized.get('artifact_id'),
                  'artifact_sha256':materialized.get('sha256'),
                  'selection_frequency':frequencies,
                  'comparison':comparisons,
                  'predictive_gain_verified':False,
                  'practical_gain_candidate':bool(approved_gain),
                  'note':'这是已物化特征在训练折中的保留频次，不能自动覆盖人工选择或替代生产验证'}
            return self._save(pid,state,{'status':'ready','stage':'evaluated',
                  'progress':1.0,'evaluation':actual,'model_feedback':predictive})
        return state

    def retry_failed(self,pid,sid,expected_revision):
        """Explicit, owner-authorized retry of a failed leased worker job.

        A successful but invalid artifact cannot be retried as a success: the
        underlying file and database must first be repaired or a new source-
        pinned analysis created. Never recreates or publishes a FeatureSet.
        """
        self.registry._editor(pid)
        state=self.get(pid,sid)
        if state['stale']:
            raise ValueError('分析来源已变化，禁止重试旧任务')
        if expected_revision!=state['revision']:
            raise ValueError('分析会话修订冲突，请刷新后重试')
        stage=state.get('stage')
        if state['status']=='failed' and stage=='profile':
            ids=[state.get('profile_job_id')]
            next_stage='profiling';progress=0.05
        elif state['status']=='failed' and stage=='preview':
            ids=list(state.get('preview_job_ids') or [])
            next_stage='previewing';progress=0.4
        elif state['status']=='failed' and stage=='materializing':
            ids=[state.get('materialize_job_id')]
            next_stage='materializing';progress=0.8
        elif (state['status']=='ready' and stage=='materialized'
              and state.get('evaluation_error')):
            ids=[state.get('evaluation_job_id')]
            next_stage='evaluating';progress=0.92
        else:
            raise ValueError('当前阶段没有可重试的失败计算任务')
        failed=[]
        for jid in ids:
            if not jid:continue
            job=self.jobs.get_job(pid,jid)
            if job['status'] in ('failed_retryable','failed_terminal'):
                failed.append(jid)
            elif job['status']!='succeeded':
                raise ValueError('存在未完成的计算任务，暂不能重试')
        if not failed:
            raise ValueError('失败结果缺少可重试的后台作业；请检查来源或新建分析')
        for jid in failed:
            self.jobs.retry(pid,jid)
        return self._save(pid,state,{'status':next_stage,'progress':progress,
                 'error':None,'evaluation_error':None})

    def select(self,pid,sid,candidate_ids,expected_revision):
        self.registry._editor(pid)
        state=self.get(pid,sid)
        if state['stale'] or state['status']!='ready' or state['stage']!='review':
            raise ValueError('Only a current, unmaterialized analysis review can change feature selection')
        if expected_revision!=state['revision']:raise ValueError('Analysis revision conflict')
        report=state.get('report') or {}
        eligible={x['candidate_id'] for x in report.get('quality_rows',[])
                  if x['status']!='unverified'}
        if len(set(candidate_ids))!=len(candidate_ids) or len(candidate_ids)>64 or not set(candidate_ids)<=eligible:
            raise ValueError('Selected candidates must have actual quality evidence')
        agent=dict(state.get('agent') or new_agent_state())
        agent['human_review']={'selected_count':len(candidate_ids),'accepted':True}
        return self._save(pid,state,{'selection':candidate_ids,
            'feature_set_id':None,'artifact':None,'evaluation':None,'agent':agent})

    def supervision(self,pid,sid):
        state=self.get(pid,sid)
        _,contract,valid=self._current(pid,state)
        if not valid:raise ValueError('分析数据源已失效')
        return supervision_options(contract,state['objective'])

    def save_set(self,pid,sid,name,request_id,*,supervision=None):
        self.registry._editor(pid)
        state=self.get(pid,sid)
        if state['stale'] or state['status']!='ready' or state['stage']!='review':
            raise ValueError('Only an unmaterialized analysis review can create a feature draft')
        if not state['selection']:raise ValueError('Select one or more verified candidates first')
        if not name or len(name)>120:raise ValueError('Invalid feature set name')
        spec=self.candidates.to_spec(pid,state['candidate_session_id'],name,state['selection'])
        task=None
        if supervision is not None:
            if not supervision.get('confirm'):
                raise ValueError('监督标签及时间协议必须由用户明确确认')
            _,contract,valid=self._current(pid,state)
            if not valid:raise ValueError('源数据或字段契约已变更')
            task=confirmed_task(contract, objective=state['objective'],session_id=sid,
                label_field_id=supervision['label_field_id'],
                prediction_time_field_id=supervision['prediction_time_field_id'],
                label_available_at_field_id=supervision['label_available_at_field_id'],
                split_kind=supervision.get('split_kind','time'),
                group_field_ids=supervision.get('group_field_ids') or [],
                static_independence_confirmed=supervision.get('static_independence_confirmed',False))
            spec=spec.model_copy(update={'task':task})
            self.registry.validate(pid,spec)
        if state.get('feature_set_id'):
            if state.get('save_request_id')!=request_id:
                raise ValueError('特征集草稿已保存；修改监督协议需重新创建分析任务')
            old=self.registry.get(pid,state['feature_set_id'])
            if old['spec_sha256']!=digest(spec):raise ValueError('已保存草稿的监督协议不同')
            return state
        rec=self.registry.create(pid,spec,request_id)
        agent=dict(state.get('agent') or new_agent_state())
        actions=list(agent.get('human_actions') or [])
        actions.append({'action':'feature.set.draft_create','confirmed_by_user':True,
                        'set_id':rec['set_id']})
        agent['human_actions']=actions[-8:]
        return self._save(pid,state,{'agent':agent,'feature_set_id':rec['set_id'],
                    'saved_set_revision':rec['revision'],'save_request_id':request_id,'progress':0.75,
                    'supervision':{'mode':task.mode,'task_sha256':digest(task),
                        'task_type':task.task_type} if task else {'mode':'exploration'}})

    def accept_and_run(self,pid,sid,name,request_id,*,confirmed=False,supervision=None):
        """One confirmed user action: save immutable draft and enqueue materialization.

        If the enqueue fails, the draft remains visible and retriable; no
        background worker may publish a feature without separate approval.
        """
        if not confirmed:
            raise ValueError('正式计算必须由用户明确确认，模型不得代为确认')
        if not request_id or len(request_id)>100:
            raise ValueError('请求 ID 无效')
        self.registry._editor(pid)
        state=self.get(pid,sid)
        saved_id=request_id+'-draft'
        running_id=request_id+'-run'
        if state.get('save_request_id')==saved_id and state.get('materialize_job_id'):
            return state  # idempotent retry: never create a second run
        if state.get('stale') or state.get('stage')!='review' or state.get('status')!='ready':
            raise ValueError('只能确认当前已完成质量审阅的分析任务')
        if not state.get('feature_set_id'):
            state=self.save_set(pid,sid,name,saved_id,supervision=supervision)
        elif state.get('save_request_id')!=saved_id:
            raise ValueError('已存在不同的特征集草稿；请使用原草稿执行')
        return self.materialize(pid,sid,running_id)

    def materialize(self,pid,sid,request_id):
        self.registry._editor(pid)
        state=self.get(pid,sid)
        if state['stale'] or state['status']!='ready' or state['stage']!='review' or not state.get('feature_set_id'):
            raise ValueError('A current, unmaterialized FeatureSet draft must be saved first')
        job=self.jobs.create_run(pid,state['feature_set_id'],request_id)
        agent=dict(state.get('agent') or new_agent_state())
        actions=list(agent.get('human_actions') or [])
        actions.append({'action':'feature.run.create','confirmed_by_user':True,
                        'job_id':job['job_id']})
        agent['human_actions']=actions[-8:]
        return self._save(pid,state,{'agent':agent,'materialize_job_id':job['job_id'],
                          'status':'materializing','stage':'materializing','progress':0.8})

    def delivery(self,pid,sid):
        state=self.get(pid,sid)
        return verified_delivery(self.projects,self.semantic,self.registry,self.jobs,pid,state)

    def viewer_receipt(self,pid,sid,*,expected_revision,artifact_id,artifact_sha256,
                       viewer_version,panel_count,workspace):
        self.registry._editor(pid)
        state=self.get(pid,sid)
        if state['revision']!=expected_revision:raise ValueError('分析会话已经更新，请刷新后重试')
        proof=verified_delivery(self.projects,self.semantic,self.registry,self.jobs,pid,state)
        artifact=proof.get('artifact') or {}
        if (proof['status'] in ('blocked','stale','draft_only','analysis_only')
            or artifact.get('artifact_id')!=artifact_id
            or artifact.get('sha256')!=artifact_sha256):
            raise ValueError('BI 回执引用的物化文件未通过同源校验')
        if not isinstance(workspace,dict) or len(canonical(workspace).encode('utf-8'))>262144:
            raise ValueError('无效的 Perspective 布局或工作区超过 256 KB')
        panels=workspace.get('panels') or {}
        if (not isinstance(panels,dict) or len(panels)<2
            or not 2<=panel_count<=min(100,len(panels))):
            raise ValueError('必须在 Perspective 实际加载原始表和特征结果两个面板')
        if not viewer_version or len(viewer_version)>60:
            raise ValueError('无效 Perspective 版本号')
        # A client-side receipt is evidence of its claimed render only; server
        # cannot independently confirm graphics. Independent browser QA remains.
        rec={'artifact_id':artifact_id,'artifact_sha256':artifact_sha256,
             'viewer_version':viewer_version,'panel_count':panel_count,
             'workspace_sha256':digest(workspace),'evidence_type':'client_reported'}
        return self._save(pid,state,{'bi_receipt':rec})

    def report_markdown(self,pid,sid):
        state=self.get(pid,sid)
        report=state.get('report')
        if not report:raise ValueError('Analysis report not available yet')
        proof=self.delivery(pid,sid)
        evaluated=bool(proof['same_artifact_evaluation'])
        supervised=evaluated and (proof.get('evaluation') or {}).get('mode')=='supervised'
        gains='监督模型对照已完成，收益须结合独立验证折解释' if supervised else '尚未完成'
        lines=['# 特征分析报告','',
               '- 结果有效性: '+('历史来源已变化，禁止作为当前产物使用' if state['stale'] else '来源有效'),
               '- 来源 SHA256: `'+state['source_sha256']+'`',
               '- 分析目标: '+state['objective'],
               '- 数据行数: '+str(report.get('row_count')),
               '- 生成候选: '+str(report['candidate_count']),
               '- 实际计算: '+str(report['evaluated_count']),
               '- 建议保留: '+str(report['recommended_count']),
               '- 预测收益验证: '+gains,'',
               '## 数据质量','']
        lines += ['- '+str(x['name']).replace('\n',' ') + ': 缺失 '+
                 f"{100*x['null_rate']:.2f}%" for x in report['high_missing_fields']]
        provenance=['- 源资产 ID: `'+state['asset_id']+'`',
                    '- 源版本: `'+state['version_id']+'`',
                    '- 契约修订号: '+str(state['contract_revision'])]
        if state.get('feature_set_id'):
            provenance.append('- 特征集 ID: `'+state['feature_set_id']+'`')
        provenance.append('- 当前阶段: '+str(state.get('stage') or 'unknown'))
        lines[2:2]=provenance
        # v0.35.5: reuse exactly the same evidence policy as the dashboard.
        # This read-only section cannot add inferred accuracy or model gains.
        from timepilot.feature_insights import build_insight_brief
        _, brief_contract, still_current=self._current(pid,state)
        brief=build_insight_brief(source_sha256=state['source_sha256'],
            contract_revision=state['contract_revision'],session=state,
            delivery=proof,contract=brief_contract if still_current else None)
        lines += ['', '## 自动研判（可核验的观察）','']
        for claim in brief['findings'][:8]:
            evidence='；'.join(str(ref['kind'])+':'+str(ref['path']) for ref in claim['evidence'])
            lines.append('- '+str(claim['title'])+'：'+str(claim['observation'])+'（依据：'+evidence+'）')
        if not brief['findings']:
            lines.append('- 暂无已完成且来源有效的统计研判。')
        lines.append('- 模型价值：'+brief['model']['message'])
        lines.append('- 模型状态：'+str(brief['model']['status']))
        # Only the same source/contract and verified outer-fold evidence can
        # reach this section.  Without labels no metric or gain is exported.
        model_brief=brief['model']
        if model_brief.get('variant_deltas'):
            lines.extend(['','### 特征方案同折对照（仅验证折）','',
                '- 评价指标：'+str(model_brief.get('metric_label')),
                '- 对照折数：'+str(model_brief.get('fold_count')),
                '- 已锁定来源 SHA256：`'+state['source_sha256']+'`',
                '- 已核验产物 SHA256：`'+str((proof.get('artifact') or {}).get('sha256'))+'`'])
            for row in model_brief['variant_deltas']:
                lines.append('- '+str(row['variant'])+' 相对原始字段：平均同折改善 '+
                    f'{row["mean_delta"]:+.6g}'+'；改善折数 '+str(row['positive_folds'])+
                    '/'+str(row['verified_fold_count'])+'；判定 '+row['verdict']+
                    '；逐折改善 '+', '.join(f'{x:+.6g}' for x in row['fold_deltas']))
            lines.append('- 本节不含独立最终测试集收益或真实工业部署收益。')
        elif model_brief.get('status') not in ('no_confirmed_label','awaiting_verified_evaluation'):
            lines.append('- 当前没有足够的同折数值证据进行特征方案比较，无法判断。')
        lines += ['', '## 特征筛选','']
        for x in report['quality_rows']:
            lines.append('- '+x['output_name']+': '+x['status']+'; '+x['reason'])
        agent=state.get('agent') or {}
        explanation=state.get('explanation') or {}
        lines += ['', '## 智能规划与工具回执','',
                  '- 推荐机制: '+('模型辅助（严格白名单）' if agent.get('mode')=='llm' else '确定性规则'),
                  '- 候选调整: '+str((agent.get('candidate_adjustment') or {}).get('reason') or '系统质量规则')]
        for item in agent.get('trace',[]):
            lines.append('- '+str(item['step'])+': '+str(item['status']))
        if explanation.get('claims'):
            lines += ['', '## 有证据支撑的发现','']
            lines += ['- '+x['text']+' (`'+x['id']+'`)' for x in explanation['claims']]
        lines += ['', '## 模型评价、BI 与资产同源核验','',
                  '- 同源证据状态: '+proof['status'],
                  '- 特征集配方 SHA256: `'+str((proof.get('feature_set') or {}).get('spec_sha256') or '待创建')+'`',
                  '- 正式产物 ID: `'+str((proof.get('artifact') or {}).get('artifact_id') or '待物化')+'`',
                  '- 正式产物 SHA256: `'+str((proof.get('artifact') or {}).get('sha256') or '待物化')+'`',
                  '- 模型评价使用同一产物: '+('已校验' if proof['same_artifact_evaluation'] else '尚未校验'),
                  '- Perspective 双面板客户端回执: '+('已记录（客户端声明，仍需独立浏览器验收）'
                     if proof['same_artifact_bi_receipt'] else '尚无回执')]
        ev=state.get('evaluation') or {}
        numerical=ev.get('report') or {}
        if proof['same_artifact_evaluation'] and numerical.get('mode')=='supervised':
            lines += ['', '### 监督评价：同折模型对照','',
                      '- 评价 ID: `'+str(ev.get('evaluation_id'))+'`',
                      '- 任务类型: '+str(numerical.get('task_type')),
                      '- 时间切分与训练折内筛选: 已在受控评价器中执行',
                      '- 以下对照为外层验证折的描述性结果，尚不代表上线效果。']
            for row in numerical.get('screening_recommendations',[]):
                if not isinstance(row,dict):continue
                lines.append('- '+str(row.get('variant'))+': '+str(row.get('verdict'))+
                             '，平均改善='+str(row.get('mean_improvement'))+
                             '，配对验证折='+str(row.get('paired_folds')))
            lines += ['', '### 设备及工况切片','',
                      '- 逐折切片证据保留于关联评价记录，不将局部差异解释为因果关系。',
                      '- 切片评价折数: '+str(len(numerical.get('folds') or []))]
        elif proof['same_artifact_evaluation']:
            lines += ['', '- 当前属于无监督探索评价，尚无预测准确率或模型贡献证据。']
        topology=(state.get('topology') or (state.get('profile') or {}).get('topology') or {})
        lines += ['', '## 时间顺序及分组可靠性','',
                  '- 自动窗口计算: '+('已通过全量键核验' if topology.get('safe_for_windows') else '本次已禁用未确认顺序的窗口特征'),
                  '- 已确认排序字段: `'+str(topology.get('order_field_id') or '未确认')+'`',
                  '- 分组字段: '+(', '.join('`'+str(x)+'`' for x in topology.get('partition_field_ids',[])) or '未确认')]
        for reason in topology.get('reasons',[])[:6]:
            lines.append('- 结构检查提示: '+str(reason).replace('\n',' '))
        feedback=(state.get('model_feedback') or {}) if proof.get('same_artifact_evaluation') else {}
        if feedback and feedback.get('artifact_sha256')==(proof.get('artifact') or {}).get('sha256'):
            lines += ['', '## 监督模型反馈（仅供人工复核）','',
                      '- 证据范围: 外层训练/验证折；未完成生产收益证明',
                      '- 有实际收益的候选: '+('需人工复核' if feedback.get('practical_gain_candidate') else '暂未发现充分证据'),
                      '- 自动修改已物化特征集: 否']
            for item in feedback.get('selection_frequency',[])[:20]:
                if not isinstance(item,dict):continue
                lines.append('- '+str(item.get('feature') or item.get('feature_name') or '特征')+
                             ': 训练折入选 '+str(item.get('selected_in_folds') or 0)+
                             ' / '+str(item.get('total_folds') or '?'))
        lines += ['', '## 适用限制',''] + ['- '+x for x in report['limitations']]
        lines += ['- '+x for x in proof['limitations']]
        lines += ['- 生产级验证仍需真实数据、实际浏览器和发布审批，当前程序不自动确认。']
        markdown='\n'.join(lines)+'\n'
        return {'markdown':markdown,'report_sha256':hashlib.sha256(markdown.encode('utf-8')).hexdigest(),
                'report_encoding':'utf-8','delivery':proof,
                'evaluation_id':(proof.get('evaluation') or {}).get('evaluation_id'),
                'source_sha256':state['source_sha256'],
                'feature_set_id':state.get('feature_set_id'),
                'artifact_sha256':(proof.get('artifact') or {}).get('sha256'),
                'delivery_status':proof['status']}

    def agent_status(self,pid,sid):
        """Read-only, project/actor-scoped audit of bounded tool orchestration."""
        state=self.get(pid,sid)
        return {'session_id':sid,'source_sha256':state['source_sha256'],
                'contract_revision':state['contract_revision'],
                'stale':state['stale'],'status':state['status'],
                'agent':state.get('agent') or new_agent_state(),
                'explanation':state.get('explanation')}

    def explain(self,pid,sid):
        """Only grounded claims from measured statistics; always read-only."""
        state=self.get(pid,sid)
        report=state.get('report')
        if not report:raise ValueError('Run and review the analysis first')
        if state.get('explanation'):
            return state['explanation']
        _,contract,valid=self._current(pid,state)
        if not valid:raise ValueError('Analysis source or contract has changed')
        return explain_from_claims(self.models,contract,report)
