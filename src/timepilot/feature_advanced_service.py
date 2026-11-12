"""Project-scoped advanced feature jobs and audited, non-publishing Agent tool calls."""
from __future__ import annotations
import json
import os
from pathlib import Path
from uuid import uuid4
from pydantic import BaseModel, ConfigDict, Field
from timepilot.feature_contracts import canonical,digest
from timepilot.feature_advanced import (TSFreshRecipe,FeaturetoolsRecipe,resolve_recipe,
    capabilities,run_tsfresh,run_featuretools,advanced_feature_budget)
from timepilot.native_bi import quote_identifier
from timepilot.projects import now
from timepilot.storage import sha256_file


class BoundedToolCall(BaseModel):
    model_config=ConfigDict(extra='forbid')
    tool_id:str
    recipe:TSFreshRecipe|FeaturetoolsRecipe|None=None
    request_id:str|None=Field(default=None,max_length=120)
    confirm:bool=False


class AdvancedService:
    def __init__(self,projects,native,semantic,registry,jobs):
        self.projects=projects;self.native=native;self.semantic=semantic
        self.registry=registry;self.jobs=jobs

    def _resources(self, pid, recipe):
        sources=[('primary',recipe.asset_id,recipe.version_id)] if isinstance(recipe,TSFreshRecipe) else [
            ('primary',recipe.decisions_asset_id,recipe.decisions_version_id),
            ('secondary',recipe.events_asset_id,recipe.events_version_id)]
        pins={};contracts={}
        for tag,aid,vid in sources:
            # Always check project-level authorization; a guessed global native
            # asset UUID must never become an authorized input.
            self.projects.require_native(pid,aid)
            src=self.semantic.source(pid,aid,vid)
            if not self.native.verify(aid).get('valid'):
                raise ValueError('原始数据字节哈希校验失败：'+aid)
            contract=self.semantic.latest(pid,aid,vid,source=src)
            if contract.state!='confirmed' or contract.revision<1:
                raise ValueError('高级特征任务须先核实字段角色与时间契约：'+aid)
            pins[tag]={'asset_id':aid,'version_id':vid,'sha256':src['sha256'],
                       'contract_revision':contract.revision,'row_count':src['row_count']}
            contracts[tag]=contract
        return pins,contracts

    def validate(self,pid,recipe):
        self.registry._editor(pid)
        pins,contracts=self._resources(pid,recipe)
        resolved=resolve_recipe(recipe,contracts)
        total=sum(int(x['row_count']) for x in pins.values())
        if total>recipe.limits.max_rows:
            raise ValueError('数据超出高级引擎单次扫描预算，请先抽取明确的数据版本')
        estimate=advanced_feature_budget(recipe.engine,resolved)
        predicted_features=estimate['estimated_max_features']
        if predicted_features is None:
            raise ModuleNotFoundError('缺少真实 tsfresh 算法包，无法计算准确特征数量；请安装可选依赖后重试')
        if predicted_features>recipe.limits.max_features:
            raise ValueError('当前算法配置将生成 '+str(predicted_features)+' 列，超过 '+str(recipe.limits.max_features)+' 列预算')
        return {'engine':recipe.engine,'sources':pins,'resolved':resolved,
                'source_fingerprint':digest(pins),'recipe_sha256':digest(recipe),
                'estimated_max_features':predicted_features,'estimate_kind':estimate['estimate_kind'],'status':'validated_not_executed',
                'requires_explicit_confirmation':True,'published':False,
                'warnings':['生成结果必须人工审阅；禁止自动用于训练或发布',
                            'PIT 将逐行检查观测时间与业务可用时间']}

    def submit(self,pid,recipe,request_id,*,confirmed=False):
        if not confirmed:raise ValueError('计算型工具需要用户明确确认后才可执行')
        if not request_id or len(request_id)>120:raise ValueError('请求 ID 不合法')
        plan=self.validate(pid,recipe)
        payload={'recipe':recipe.model_dump(mode='json'),'recipe_sha256':plan['recipe_sha256'],
                 'sources':plan['sources'],'source_fingerprint':plan['source_fingerprint'],
                 'resolved':plan['resolved']}
        # Limit queued AND running jobs per project/owner. Preserve idempotency
        # by letting a repeated identical request resolve before quota refusal.
        with self.projects._lock,self.projects._db() as db:
            old=db.execute('''SELECT job_id,payload_json FROM feature_jobs WHERE
                project_id=? AND owner_id=? AND kind=? AND request_id=?''',
                (pid,self.registry._actor(),'advanced_'+recipe.engine,request_id)).fetchone()
            if old:
                if json.loads(old['payload_json'])!=payload:
                    raise ValueError('同一请求 ID 已用于不同的任务参数')
                return self.jobs.get_job(pid,old['job_id'])
            count=db.execute('''SELECT COUNT(*) FROM feature_jobs WHERE project_id=?
                AND kind IN ('advanced_tsfresh','advanced_featuretools')
                AND status IN ('queued','running','cancel_requested')''',(pid,)).fetchone()[0]
            if count>=recipe.limits.max_concurrent_jobs:
                raise ValueError('项目高级计算并发预算已满，请等待或取消已有任务')
            return self.jobs._create_job(pid,'advanced_'+recipe.engine,
                next(iter(plan['sources'].values()))['asset_id'],request_id,payload)

    def _load(self,pid,pin,columns,max_rows,max_memory_mb=512):
        # A single AdvancedService instance may serve multiple worker requests.
        # Keep project scope and budget local to each invocation, never mutable
        # on the service object where concurrent tasks could cross-contaminate.
        src=self.semantic.source(pid,pin['asset_id'],pin['version_id'])
        if src['sha256']!=pin['sha256']:
            raise ValueError('高级特征来源 SHA256 已变化，必须重新计划')
        contract=self.semantic.latest(pid,pin['asset_id'],pin['version_id'],source=src)
        if contract.revision!=pin['contract_revision'] or contract.state!='confirmed':
            raise ValueError('高级特征字段契约已经失效')
        if int(src['row_count'])>max_rows:
            raise ValueError('高级引擎扫描超出配额')
        for name in columns:
            if name not in {c['name'] for c in src['columns']}:
                raise ValueError('预期列不存在：'+name)
        # Conservative pre-load memory cap (the actual subprocess has an RSS
        # watchdog). Strings may exceed this estimate; check after loading too.
        estimate=int(src['row_count'])*len(columns)*64
        # Numeric vectors and Python/pandas process-spawn copies can coexist.
        if estimate*3>max_memory_mb*1024**2:
            raise ValueError('预计输入内存和进程复制成本超过内存预算')
        if estimate>2*1024**3:
            raise ValueError('预估输入超过安全上限，禁止整体读取')
        with self.native._connection() as con:
            self.native._install_effective_view(con,pin['asset_id'],pin['version_id'],src['columns'])
            query='SELECT '+','.join(quote_identifier(x) for x in columns)+' FROM __tp_effective'
            frame=con.execute(query).df()
        if len(frame)>max_rows:raise ValueError('扫描的实际行数超过预算')
        if frame.memory_usage(index=True,deep=True).sum()*3>max_memory_mb*1024**2:
            raise ValueError('实际输入 DataFrame 超过任务内存预算')
        return frame

    def run_worker(self,row,payload):
        """Called only from the authorized leased FeatureJobEngine worker."""
        pid=row['project_id']
        recipe=(TSFreshRecipe if row['kind']=='advanced_tsfresh' else FeaturetoolsRecipe).model_validate(payload['recipe'])
        current=self.validate(pid,recipe)
        if (current['source_fingerprint']!=payload['source_fingerprint']
                or current['recipe_sha256']!=payload['recipe_sha256']
                or current['sources']!=payload['sources']):
            raise ValueError('计算前数据来源或算子契约发生变化，拒绝复用旧计划')
        spec=current['resolved'];max_rows=spec['limits']['max_rows']
        if recipe.engine=='tsfresh':
            cols=list(dict.fromkeys([spec['sample'],spec['event_time'],spec['available_at'],
                                      spec['cutoff'],*spec['values']]))
            frame=self._load(pid,current['sources']['primary'],cols,max_rows,spec['limits'].get('max_memory_mb',512))
            secondary_frame=None
        else:
            dcols=list(dict.fromkeys([spec['decision_id'],spec['decision_entity'],spec['cutoff']]))
            ecols=list(dict.fromkeys([spec['event_entity'],spec['event_time'],spec['available_at'],*spec['values']]))
            decisions=self._load(pid,current['sources']['primary'],dcols,max_rows,spec['limits'].get('max_memory_mb',512))
            events=self._load(pid,current['sources']['secondary'],ecols,max_rows,spec['limits'].get('max_memory_mb',512))
            frame=decisions;secondary_frame=events
        output=self.jobs.root/pid/'advanced'/row['job_id']
        output.mkdir(parents=True,exist_ok=True)
        building=output/'data.building.parquet';published=output/'data.parquet'
        try:
            from timepilot.feature_advanced_isolation import calculate_with_budget
            computed=calculate_with_budget(recipe.engine,frame,secondary_frame,spec,building)
            pit=computed['pit']
            sha=sha256_file(building)
            with self.projects._db() as db:
                lease=db.execute('SELECT lease_owner,status,lease_until FROM feature_jobs WHERE job_id=?',
                                 (row['job_id'],)).fetchone()
            if not lease or lease['lease_owner']!=self.jobs.worker_id or lease['status']!='running' or lease['lease_until']<now():
                raise ValueError('计算结果租约已失效，拒绝持久化')
            os.replace(building,published)
            main=current['sources']['primary']['asset_id']
            # Use the native BI ingestion path so the result is actually
            # queryable through a Perspective native asset, not a demo chart.
            native_asset=self.native.import_from_path(published,
                        f'高级候选 · {recipe.engine} · {row["job_id"][:8]}',
                        {'type':'advanced_candidate','project_id':pid,'job_id':row['job_id'],
                         'sources':current['sources'],'source_fingerprint':current['source_fingerprint']})
            link=self.projects.require_native(pid,main)
            self.projects.attach_native(pid,native_asset['asset_id'],link['source_asset_id'])
            metadata={'job_id':row['job_id'],'project_id':pid,'engine':recipe.engine,
              'source_fingerprint':current['source_fingerprint'],'sources':current['sources'],
              'recipe_sha256':current['recipe_sha256'],'sha256':sha,'rows':computed['rows'],
              'columns':computed['columns'],'pit':pit,'scope':'full_bounded_candidate',
              'native_asset_id':native_asset['asset_id'],
              'approval':'review_required','published':False}
            with self.projects._lock,self.projects._db() as db:
                db.execute('''INSERT INTO feature_advanced_artifacts
                  (job_id,project_id,owner_id,engine,source_fingerprint,recipe_sha256,
                   native_asset_id,artifact_sha256,result_json,created_at)
                  VALUES (?,?,?,?,?,?,?,?,?,?)''',
                  (row['job_id'],pid,row['owner_id'],recipe.engine,
                   current['source_fingerprint'],current['recipe_sha256'],
                   native_asset['asset_id'],sha,canonical(metadata),now()))
            return metadata
        finally:
            building.unlink(missing_ok=True)

    def result(self,pid,jid):
        self.projects.get(pid)
        with self.projects._db() as db:
            r=db.execute('''SELECT * FROM feature_advanced_artifacts WHERE
              job_id=? AND project_id=?''',(jid,pid)).fetchone()
        if not r:raise KeyError('高级计算产物尚未就绪或不存在')
        if self.projects.security is not None and r['owner_id']!=self.registry._actor():
            raise ValueError('只允许查看本人生成的高级候选产物')
        info=json.loads(r['result_json'])
        if not self.native.verify(info['native_asset_id']).get('valid'):
            raise ValueError('BI 数据资产已经损坏或被删除')
        return info

    def tool_call(self,pid,call:BoundedToolCall):
        """Dedicated allowlisted Agent tools; execute requires explicit confirmation."""
        self.registry._editor(pid)
        if call.tool_id=='feature.advanced.capabilities':
            if call.recipe is not None:raise ValueError('能力查询不需要配方')
            return capabilities()
        if call.tool_id not in ('feature.advanced.validate','feature.advanced.submit'):
            raise ValueError('未授权工具；不接受模型生成的 SQL、Python 或任意路径')
        if call.recipe is None:raise ValueError('工具请求缺少经过验证的高级特征配方')
        if call.tool_id=='feature.advanced.validate':
            return self.validate(pid,call.recipe)
        return self.submit(pid,call.recipe,call.request_id,confirmed=call.confirm)
