"""Project-scoped conversational BI workspaces and reversible, audited tool execution.

A model can only propose registered JSON actions. All field/asset identifiers are
resolved from the pinned project catalog; DuckDB does every calculation; no SQL,
Python or filesystem commands are accepted from the model.
"""
from __future__ import annotations
import copy
import hashlib
import json
import re
from datetime import datetime, timezone
from uuid import UUID, uuid4

from timepilot.bi_tool_registry import (BIAction, TOOLS, catalog as tool_catalog,
                                        validate_action, ToolBatch)
from timepilot.bi_query_engine import BIQueryEngine
from timepilot.native_bi import (NativeBIError, NativeBIDependencyError, QuerySpec,
                                  FilterSpec, MetricSpec, OrderSpec, compile_query)
from timepilot.enterprise_security import CURRENT_PRINCIPAL, SecurityError
from timepilot.data_platform import SelectionInput
from timepilot.modeling_workflow import CreateModelingDraft


def timestamp():return datetime.now(timezone.utc).isoformat()
def signature(value):return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,
        separators=(',',':'),default=str).encode('utf-8')).hexdigest()

class BIWorkspaceError(ValueError):pass

class BIWorkspaceService:
    def __init__(self,projects,native,platform,modeling=None,models=None):
        self.projects,self.native,self.platform,self.modeling,self.models=projects,native,platform,modeling,models
        self.engine=BIQueryEngine(projects,native)
        self._setup()

    def _setup(self):
        with self.projects._lock,self.projects._db() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS bi_agent_workspaces(
                workspace_id TEXT PRIMARY KEY,project_id TEXT NOT NULL,owner_id TEXT NOT NULL,
                asset_id TEXT NOT NULL,version_id TEXT NOT NULL,source_sha256 TEXT NOT NULL,
                revision INTEGER NOT NULL,cursor INTEGER NOT NULL,state_json TEXT NOT NULL,
                history_json TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS idx_bi_agent_ws_project ON bi_agent_workspaces(project_id,updated_at);
            CREATE TABLE IF NOT EXISTS bi_agent_events(
                event_id TEXT PRIMARY KEY,project_id TEXT NOT NULL,workspace_id TEXT NOT NULL,
                request_id TEXT NOT NULL,actor_id TEXT NOT NULL,event_type TEXT NOT NULL,
                before_sha256 TEXT NOT NULL,after_sha256 TEXT NOT NULL,
                actions_json TEXT NOT NULL,revision INTEGER NOT NULL,created_at TEXT NOT NULL,
                UNIQUE(workspace_id,request_id));
            CREATE INDEX IF NOT EXISTS idx_bi_agent_events_ws ON bi_agent_events(workspace_id,revision);
            CREATE TABLE IF NOT EXISTS bi_agent_views(
                view_id TEXT PRIMARY KEY,project_id TEXT NOT NULL,workspace_id TEXT NOT NULL,
                asset_id TEXT NOT NULL,version_id TEXT NOT NULL,source_sha256 TEXT NOT NULL,
                name TEXT NOT NULL,owner_id TEXT NOT NULL,shared INTEGER NOT NULL,
                state_json TEXT NOT NULL,perspective_json TEXT,created_at TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS idx_bi_agent_views_asset ON bi_agent_views(project_id,asset_id,created_at);
            CREATE TABLE IF NOT EXISTS bi_agent_turns(
                turn_id TEXT PRIMARY KEY,project_id TEXT NOT NULL,workspace_id TEXT NOT NULL,
                owner_id TEXT NOT NULL,request_id TEXT NOT NULL,role TEXT NOT NULL,
                message TEXT NOT NULL,status TEXT NOT NULL,plan_json TEXT NOT NULL,
                revision INTEGER NOT NULL,created_at TEXT NOT NULL,
                UNIQUE(workspace_id,owner_id,request_id));
            CREATE INDEX IF NOT EXISTS idx_bi_agent_turns_owner ON bi_agent_turns(project_id,workspace_id,owner_id,created_at);
            ''')

    @staticmethod
    def _actor():
        principal=CURRENT_PRINCIPAL.get()
        return principal.user_id if principal else 'local'

    @staticmethod
    def _id(raw):
        try:return str(UUID(str(raw)))
        except (TypeError,ValueError):raise BIWorkspaceError('工作区编号无效') from None

    def _asset(self,pid,aid,vid):
        return self.engine.checked(pid,aid,vid)

    def _source_valid(self,pid,state,source_sha256):
        meta=self._asset(pid,state['asset_id'],state['version_id'])
        if meta['sha256']!=source_sha256:
            raise BIWorkspaceError('工作区引用的数据资产内容发生变化，拒绝套用历史操作')
        return meta

    @staticmethod
    def _basic_state(aid,vid):
        return {'asset_id':aid,'version_id':vid,
                'query':{'columns':[],'filters':[],'group_by':[],'metrics':[],
                         'order_by':[],'limit':300,'offset':0},
                'chart':{'type':'grid','x':None,'y':[],'color':None},
                'expressions':[],'window':None,'windows':[],'bucket':None,'join':None,
                'industrial':None,'dashboard':[],'frozen_selection_id':None,
                'viewer':{'split_by':[],'theme':'Pro Light','settings':True}}

    def open(self,pid,aid,vid='base'):
        self.projects.writable(pid)
        meta=self._asset(pid,aid,vid)
        state=self._basic_state(aid,vid)
        wid=str(uuid4());now=timestamp();history=[copy.deepcopy(state)]
        with self.projects._lock,self.projects._db() as db:
            db.execute('''INSERT INTO bi_agent_workspaces VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',
                       (wid,pid,self._actor(),aid,vid,meta['sha256'],1,0,
                        json.dumps(state,ensure_ascii=False),json.dumps(history,ensure_ascii=False),now,now))
        return self.workspace(pid,wid)

    def workspace(self,pid,wid,*,locked=False):
        self.projects.get(pid)
        with self.projects._db() as db:
            row=db.execute('SELECT * FROM bi_agent_workspaces WHERE project_id=? AND workspace_id=?',
                           (pid,self._id(wid))).fetchone()
        if row is None:raise KeyError('工作区不存在或无项目权限')
        if row['owner_id']!=self._actor():raise KeyError('工作区不存在或不属于当前账户')
        doc=dict(row)
        state=json.loads(doc.pop('state_json'));doc['state']=state
        doc['history']=json.loads(doc.pop('history_json')) if locked else None
        self._source_valid(pid,state,doc['source_sha256'])
        return doc

    def list_workspaces(self,pid,aid=None):
        self.projects.get(pid)
        sql='''SELECT workspace_id,asset_id,version_id,revision,updated_at FROM bi_agent_workspaces
               WHERE project_id=? AND owner_id=?'''
        args=[pid,self._actor()]
        if aid:sql+=' AND asset_id=?';args.append(aid)
        sql+=' ORDER BY updated_at DESC LIMIT 100'
        with self.projects._db() as db:return [dict(x) for x in db.execute(sql,args).fetchall()]

    def tools(self,pid,wid=None):
        self.projects.get(pid)
        if wid:self.workspace(pid,wid)
        return tool_catalog()

    def _columns(self,pid,state):
        meta=self._asset(pid,state['asset_id'],state['version_id'])
        cols=[c['name'] for c in meta['columns']]
        if state.get('join'):
            join=state['join'];right=self._asset(pid,join['right_asset_id'],join['right_version_id'])
            if right['sha256'] != join.get('right_source_sha256'):
                raise BIWorkspaceError('关联表的数据内容或资产指纹已变化，必须重新确认关联')
            cols.extend('right__'+c['name'] for c in right['columns'])
        cols.extend(e['name'] for e in state.get('expressions',[]))
        if state.get('window'):cols.append(state['window']['name'])
        cols.extend(w['name'] for w in state.get('windows',[]))
        return cols

    def _read(self,pid,state,tool,args):
        if tool=='bi.stats.correlation':
            raise BIWorkspaceError('相关性计算需要在当前工作区创建分析计划后执行')
        if tool=='bi.viewer.capabilities':
            from timepilot.perspective_capabilities import discover
            return discover(self,pid)
        if tool=='bi.catalog.search':
            from timepilot.asset_lifecycle import AssetLifecycle
            return AssetLifecycle(self.projects,self.platform).search(pid,**args)
        if tool=='bi.asset.impact':
            from timepilot.asset_lifecycle import AssetLifecycle
            life=AssetLifecycle(self.projects,self.platform)
            items=[a for a in self.platform.list_assets(pid) if a.get('native_asset_id')==state['asset_id']]
            return life.impact(pid,items[0]['asset_id']) if items else {'dependencies':[],'note':'非统一目录资产无逻辑回收操作'}
        if tool=='bi.catalog.list':
            return {'assets':[x for x in self.platform.list_assets(pid) if not x.get('missing')][:100]}
        if tool=='bi.asset.describe':return {'asset':self._asset(pid,state['asset_id'],state['version_id'])}
        if tool=='bi.schema.inspect':return {'fields':self._asset(pid,state['asset_id'],state['version_id'])['columns']}
        if tool=='bi.profile.inspect':return self.native.profile(state['asset_id'],sample_rows=500,version_id=state['version_id'])
        if tool=='bi.rows.peek':
            doc=copy.deepcopy(state)
            doc['query']['limit']=min(1000,max(1,args.get('limit',100)))
            doc['query']['offset']=args.get('offset',0)
            return self.engine.execute(pid,doc,limit=doc['query']['limit'])
        if tool=='bi.join.preview':return self.engine.join_preview(pid,state,args)
        raise BIWorkspaceError('工具需要工作区更新或单独的保存/建模操作')

    def _transition(self,pid,prior,tool,args,*,confirmed=False):
        state=copy.deepcopy(prior)
        # Query and view state is validated even when generated by an LLM.
        cols=self._columns(pid,state)
        q=state['query'];viewer=state.setdefault('viewer',{'split_by':[],'theme':'Pro Light','settings':True});effect=TOOLS[tool][1]
        if effect=='read':return state,self._read(pid,state,tool,args)
        if effect in {'confirm','save'} and not confirmed:
            raise BIWorkspaceError('此操作需要用户明确确认')
        if tool=='bi.filter.set':
            if len(q['filters'])>=12:raise BIWorkspaceError('最多支持 12 条行级筛选')
            FilterSpec.model_validate(args)
            if args['column'] not in cols:raise BIWorkspaceError('筛选字段不存在')
            q['filters'].append(args)
        elif tool=='bi.filter.clear':q['filters']=[]
        elif tool=='bi.filter.remove':
            index=args.get('index')
            if index is None:q['filters']=[]
            elif index>=len(q['filters']):raise BIWorkspaceError('不存在对应的筛选编号')
            else:q['filters'].pop(index)
        elif tool=='bi.sort.set':
            if args['field'] not in cols and args['field'] not in {m['alias'] for m in q['metrics']}:
                raise BIWorkspaceError('排序字段不存在')
            q['order_by']=[args]
        elif tool=='bi.sort.clear':q['order_by']=[]
        elif tool=='bi.pivot.split.set':
            if not set(args['columns'])<=set(cols) or len(set(args['columns']))!=len(args['columns']):
                raise BIWorkspaceError('列透视字段不存在或重复')
            viewer['split_by']=args['columns']
        elif tool=='bi.pivot.split.clear':viewer['split_by']=[]
        elif tool=='bi.viewer.settings.toggle':viewer['settings']=args['enabled']
        elif tool=='bi.viewer.theme.set':viewer['theme']=args['theme']
        elif tool=='bi.columns.set':
            if len(set(args['columns']))!=len(args['columns']) or not set(args['columns'])<=set(cols):
                raise BIWorkspaceError('选择字段不存在或重复')
            q['columns']=args['columns'];q['group_by']=[];q['metrics']=[]
        elif tool=='bi.aggregate.run':
            if not set(args['group_by'])<=set(cols):raise BIWorkspaceError('分组字段不存在')
            metric=MetricSpec(operation=args['operation'],column=args['column'],alias=args['alias'])
            if metric.column and metric.column not in cols:raise BIWorkspaceError('统计字段不存在')
            q['group_by']=args['group_by'];q['metrics']=[metric.model_dump(mode='json')];q['columns']=[];q['order_by']=[]
            state['chart']={'type':'bar','x':args['group_by'][0] if args['group_by'] else None,
                           'y':[args['alias']],'color':None}
        elif tool=='bi.aggregate.clear':
            q['metrics']=[];q['group_by']=[];q['order_by']=[]
        elif tool in ('bi.chart.create','bi.chart.configure','bi.chart.plugin.set','bi.chart.add_panel'):
            permitted=(set(q['group_by'])|{m['alias'] for m in q['metrics']}) if (q['metrics'] or q['group_by']) else set(cols)
            if state.get('bucket'):permitted={'time_bucket','value','source_rows'}|({state['bucket']['entity_field']} if state['bucket'].get('entity_field') else set())
            if args.get('x') and args['x'] not in permitted:raise BIWorkspaceError('图表横轴不存在')
            if not set(args['y'])<=permitted or (args.get('color') and args['color'] not in permitted):
                raise BIWorkspaceError('图表字段不属于当前查询')
            state['chart']=args
            if tool=='bi.chart.add_panel':
                if len(state['dashboard'])>=8:raise BIWorkspaceError('一个工作区最多支持 8 个图表面板')
                state['dashboard'].append(args)
            elif tool=='bi.chart.create':state['dashboard']=[args]
            elif state['dashboard']:state['dashboard'][-1]=args
            else:state['dashboard']=[args]
        elif tool=='bi.chart.heatmap':
            from timepilot.bi_analysis_plan import BIAnalysisPlans
            matrix=BIAnalysisPlans(self).matrix(pid,args['analysis_id'])
            from timepilot.bi_statistics import BICorrelationService
            selected=BICorrelationService.selection_fingerprint(state,self._asset(pid,state['asset_id'],state['version_id'])['sha256'])
            if matrix['selection_fingerprint']!=selected:
                raise BIWorkspaceError('矩阵计算时的筛选行集已改变，必须重新计算')
            state['chart']={'type':'heatmap','x':None,'y':[],'color':None}
            state['dashboard']=[{'type':'heatmap','analysis_id':args['analysis_id']}]
            state['correlation']={'analysis_id':args['analysis_id'],'selection_fingerprint':selected}
        elif tool=='bi.expression.create':
            if len(state['expressions'])>=8:raise BIWorkspaceError('最多允许 8 个计算列')
            if args['name'] in cols:raise BIWorkspaceError('计算列与已有字段重复')
            self.engine._expression(args['expression'],set(cols))
            state['expressions'].append(args)
            # Guarantee the generated column is part of the bounded result, even when
            # the source contains more than the default 30 visible fields.
            q['metrics']=[];q['group_by']=[];q['columns']=self._columns(pid,state)[:29]+[args['name']] if len(self._columns(pid,state))>30 else [];q['order_by']=[]
        elif tool=='bi.window.compute':
            if not set(x for x in [args['column'],args['time_field'],args.get('entity_field')] if x)<=set(cols):
                raise BIWorkspaceError('滚动窗口字段不存在')
            if args['name'] in cols:raise BIWorkspaceError('滚动平均列名重复')
            state['window']=args
            q['metrics']=[];q['group_by']=[];q['columns']=[];q['order_by']=[]
        elif tool=='bi.window.compute_many':
            original=self._asset(pid,state['asset_id'],state['version_id'])
            original_types={c['name']:str(c.get('physical_type','')).upper() for c in original['columns']}
            fields=list(args['columns']);order=args['time_field']
            if order!='__tp_rowid' and order not in cols:
                raise BIWorkspaceError('排序字段不属于当前数据资产')
            if any(f not in cols for f in fields):raise BIWorkspaceError('移动平均引用不存在的字段')
            if any(not any(n in original_types.get(f,'') for n in ('INT','FLOAT','DOUBLE','DECIMAL','REAL')) for f in fields):
                raise BIWorkspaceError('移动平均仅支持已核实的数值列；请先校验源字段类型')
            existing=state.get('windows') or []
            names=args.get('output_names') or [f'{f}_ma{args["window"]}' for f in fields]
            if len(names)!=len(fields) or len(set(names))!=len(names):raise BIWorkspaceError('移动平均输出列名重复')
            if len(existing)+len(fields)>12:raise BIWorkspaceError('最多允许 12 个移动平均计算列')
            for field,name in zip(fields,names):
                if name in cols:raise BIWorkspaceError('派生字段已存在：'+name)
                existing.append({'column':field,'time_field':order,'window':args['window'],
                                 'entity_field':args.get('entity_field'),'name':name})
                cols.append(name)
            state['windows']=existing
            q['columns']=[];q['group_by']=[];q['metrics']=[];q['order_by']=[]
        elif tool=='bi.timeseries.clear':
            state['bucket']=None
            q['columns']=[];q['metrics']=[];q['group_by']=[];q['order_by']=[]
            state['chart']={'type':'grid','x':None,'y':[],'color':None};state['dashboard']=[]
        elif tool=='bi.expression.clear':
            state['expressions']=[]
            q['columns']=[];q['metrics']=[];q['group_by']=[];q['order_by']=[]
        elif tool=='bi.window.clear':
            state['window']=None
            state['windows']=[]
            q['columns']=[];q['metrics']=[];q['group_by']=[];q['order_by']=[]
        elif tool=='bi.timeseries.bucket':
            if not set(x for x in [args['time_field'],args['metric_field'],args.get('entity_field')] if x)<=set(cols):
                raise BIWorkspaceError('时间字段或统计字段不存在')
            meta=self._asset(pid,state['asset_id'],state['version_id'])
            type_of={x['name']:x['physical_type'].upper() for x in meta['columns']}
            if ('DATE' not in type_of.get(args['time_field'],'') and
                'TIME' not in type_of.get(args['time_field'],'')):
                raise BIWorkspaceError('时间桶需要已验证的日期/时间字段，数字索引不能冒充分钟')
            state['bucket']=args
            state['chart']={'type':'line','x':'time_bucket','y':['value'],'color':args.get('entity_field')}
            state['dashboard']=[state['chart']]
            q['metrics']=[];q['group_by']=[];q['columns']=[];q['order_by']=[]
        elif tool=='bi.join.apply_virtual':
            if state['join']:raise BIWorkspaceError('当前工作区最多关联一张额外表')
            self.engine.join_preview(pid,state,args)
            state['join']={**args,'right_source_sha256':self._asset(pid,args['right_asset_id'],args['right_version_id'])['sha256']}
            q['metrics']=[];q['group_by']=[];q['columns']=[];q['order_by']=[]
        elif tool=='bi.join.clear':
            state['join']=None;q['metrics']=[];q['group_by']=[];q['columns']=[];q['order_by']=[]
        elif tool=='bi.chart.clear_overlay':
            state['industrial']=None
            state['chart']={'type':'grid','x':None,'y':[],'color':None};state['dashboard']=[]
        elif tool=='bi.chart.overlay':
            from timepilot.industrial_bi import IndustrialSpec
            if q['filters'] or state['join'] or state['bucket'] or state['expressions'] or state['window']:
                raise BIWorkspaceError('工业事件图层需从明确完整数据版本运行；当前筛选/关联/计算不能静默忽略')
            IndustrialSpec(asset_id=state['asset_id'],version_id=state['version_id'],**args)
            state['industrial']=args
            state['chart']={'type':'line','x':'timestamp','y':['average','maintenance_marker'],'color':None}
            state['dashboard']=[state['chart']]
        else:raise BIWorkspaceError('此工具必须使用专用 API')
        if tool!='bi.chart.overlay':state['industrial']=None
        if tool!='bi.chart.heatmap':state.pop('correlation',None)
        if tool in {'bi.filter.set','bi.filter.remove','bi.join.apply_virtual','bi.join.clear'}:
            state['frozen_selection_id']=None
        if state.get('bucket') is None:
            QuerySpec.model_validate({**q,'asset_id':state['asset_id'],'version_id':state['version_id']})
            compile_query(QuerySpec.model_validate({**q,'asset_id':state['asset_id'],
                'version_id':state['version_id']}),self._columns(pid,state),source_sql='__tp_effective')
        return state,{'tool':tool,'effect':effect}

    def _only_read(self,actions):
        return bool(actions) and all(a.tool in TOOLS and TOOLS[a.tool][1]=='read' for a in actions)

    def _read_batch(self,pid,workspace,actions):
        output=[]
        for action in actions:
            args=validate_action(action)
            output.append({'tool':action.tool,'effect':'read',
                           'data':self._read(pid,workspace['state'],action.tool,args)})
        return {'workspace_id':workspace['workspace_id'], 'revision':workspace['revision'],
                'workspace':workspace,'read_results':output,'result_type':'read',
                'result':output[0]['data'] if len(output)==1 else {'items':output},
                'changed':False,'executed':True}

    def _preview_multi(self,pid,workspace,actions,confirmed=False,*,execute=True):
        state=workspace['state'];results=[]
        for action in actions:
            args=validate_action(action)
            if action.tool.startswith('bi.workspace.') or action.tool in {'bi.view.save','bi.model.draft','bi.selection.freeze'}:
                raise BIWorkspaceError('该操作需要使用独立的历史、视图或建模接口')
            state,info=self._transition(pid,state,action.tool,args,confirmed=confirmed)
            results.append(info)
        rendered=self.engine.execute(pid,state) if execute else None
        return state,rendered,results

    def preview(self,pid,wid,batch:ToolBatch):
        item=self.workspace(pid,wid)
        if item['revision']!=batch.expected_revision:raise BIWorkspaceError('工作区已变化，请刷新后重新计划')
        if self._only_read(batch.actions):
            return {**self._read_batch(pid,item,batch.actions),'executed':False}
        state,rendered,steps=self._preview_multi(pid,item,batch.actions,confirmed=batch.confirm)
        return {'workspace_id':wid,'revision':item['revision'],'state':state,'result':rendered,
                'steps':steps,'would_change':signature(state)!=signature(item['state']),
                'executed':False}

    def _commit(self,pid,workspace,batch,state,result,*,kind='apply'):
        self.projects.writable(pid)
        # Version ownership and source hash checked immediately before compare-and-swap.
        self._source_valid(pid,state,workspace['source_sha256'])
        wid=workspace['workspace_id'];owner=self._actor();moment=timestamp()
        with self.projects._lock,self.projects._db() as db:
            previous=db.execute('SELECT * FROM bi_agent_workspaces WHERE project_id=? AND workspace_id=? AND owner_id=?',
                (pid,wid,owner)).fetchone()
            if not previous:raise KeyError('工作区已失效或权限不足')
            earlier=db.execute('SELECT revision FROM bi_agent_events WHERE workspace_id=? AND request_id=?',
                               (wid,batch.request_id)).fetchone()
            if earlier:
                return {'duplicate':True,'workspace':self.workspace(pid,wid),'result':result}
            if previous['revision']!=batch.expected_revision:
                raise BIWorkspaceError('工作区版本冲突；操作未保存，请刷新')
            history=json.loads(previous['history_json']);cursor=previous['cursor']
            before=json.loads(previous['state_json'])
            if kind=='undo':
                if cursor<=0:raise BIWorkspaceError('没有可以撤销的操作')
                cursor-=1;state=copy.deepcopy(history[cursor])
            elif kind=='redo':
                if cursor>=len(history)-1:raise BIWorkspaceError('没有可以重做的操作')
                cursor+=1;state=copy.deepcopy(history[cursor])
            elif signature(state)!=signature(before):
                history=history[:cursor+1]+[copy.deepcopy(state)]
                if len(history)>100:history=history[-100:]
                cursor=len(history)-1
            version=previous['revision']+1
            db.execute('''UPDATE bi_agent_workspaces SET state_json=?,history_json=?,cursor=?,revision=?,updated_at=?
                          WHERE workspace_id=? AND project_id=? AND revision=?''',
                       (json.dumps(state,ensure_ascii=False),json.dumps(history,ensure_ascii=False),cursor,
                        version,moment,wid,pid,batch.expected_revision))
            db.execute('''INSERT INTO bi_agent_events VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
                (str(uuid4()),pid,wid,batch.request_id,owner,kind,signature(before),signature(state),
                 json.dumps([a.model_dump(mode='json') for a in batch.actions],ensure_ascii=False),version,moment))
        return {'duplicate':False,'workspace':self.workspace(pid,wid), 'result':result,
                'action_count':len(batch.actions),'revision':version}

    def apply(self,pid,wid,batch:ToolBatch):
        workspace=self.workspace(pid,wid)
        if self._only_read(batch.actions):
            if workspace['revision']!=batch.expected_revision:
                raise BIWorkspaceError('工作区状态已改变，请刷新')
            return self._read_batch(pid,workspace,batch.actions)
        if workspace['revision']!=batch.expected_revision:
            with self.projects._db() as db:
                row=db.execute('SELECT 1 FROM bi_agent_events WHERE workspace_id=? AND request_id=?',
                    (wid,batch.request_id)).fetchone()
            if row:return {'duplicate':True,'workspace':workspace}
            raise BIWorkspaceError('工作区状态已改变，请刷新')
        state,rendered,steps=self._preview_multi(pid,workspace,batch.actions,confirmed=batch.confirm)
        result=self._commit(pid,workspace,batch,state,rendered)
        result['steps']=steps
        return result

    def undo_redo(self,pid,wid,revision,request_id,kind):
        item=self.workspace(pid,wid,locked=True)
        if item['revision']!=revision:
            with self.projects._db() as db:
                existing=db.execute('SELECT 1 FROM bi_agent_events WHERE workspace_id=? AND request_id=?',
                    (wid,request_id)).fetchone()
            if existing:return {'duplicate':True,'workspace':item,'result':None}
            raise BIWorkspaceError('工作区版本冲突')
        cursor=item['cursor'];history=item['history']
        if kind=='undo' and cursor==0:raise BIWorkspaceError('无可撤销操作')
        if kind=='redo' and cursor==len(history)-1:raise BIWorkspaceError('无可重做操作')
        state=history[cursor-1 if kind=='undo' else cursor+1]
        rendered=self.engine.execute(pid,state)
        batch=ToolBatch(expected_revision=revision,actions=[BIAction(tool='bi.workspace.'+kind)],request_id=request_id)
        return self._commit(pid,item,batch,state,rendered,kind=kind)

    def query(self,pid,wid,arrow=False,limit=300):
        item=self.workspace(pid,wid)
        return self.engine.execute(pid,item['state'],arrow=arrow,limit=limit)

    def save_view(self,pid,wid,name,perspective=None,shared=False):
        self.projects.writable(pid)
        item=self.workspace(pid,wid)
        if shared and self.projects.security is not None:
            principal=CURRENT_PRINCIPAL.get()
            if principal is None:raise SecurityError('共享视图要求已认证账户')
            self.projects.security.allowed_project(principal,pid,required='editor')
        config=json.dumps(perspective,ensure_ascii=False) if perspective else None
        if config and len(config.encode())>65536:raise BIWorkspaceError('Perspective 布局超过 64KB')
        state=item['state'];vid=str(uuid4());now=timestamp()
        with self.projects._lock,self.projects._db() as db:
            db.execute('''INSERT INTO bi_agent_views VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',
                (vid,pid,wid,state['asset_id'],state['version_id'],item['source_sha256'],name,self._actor(),
                 int(shared),json.dumps(state,ensure_ascii=False),config,now))
        return {'view_id':vid,'name':name,'asset_id':state['asset_id'],'version_id':state['version_id'],
                'shared':shared,'created_at':now}

    def list_views(self,pid,aid=None):
        self.projects.get(pid)
        sql=('SELECT view_id,name,asset_id,version_id,created_at,shared FROM bi_agent_views '
             'WHERE project_id=? AND (owner_id=? OR shared=1)')
        params=[pid,self._actor()]
        if aid:sql+=' AND asset_id=?';params.append(aid)
        sql+=' ORDER BY created_at DESC LIMIT 100'
        with self.projects._db() as db:return [dict(x) for x in db.execute(sql,params).fetchall()]

    def view(self,pid,vid):
        self.projects.get(pid)
        with self.projects._db() as db:
            row=db.execute('''SELECT * FROM bi_agent_views WHERE project_id=? AND view_id=?
                 AND (owner_id=? OR shared=1)''',(pid,self._id(vid),self._actor())).fetchone()
        if row is None:raise KeyError('当前项目或账户没有此视图')
        data=dict(row);data['state']=json.loads(data.pop('state_json'))
        data['perspective']=json.loads(data.pop('perspective_json')) if data['perspective_json'] else None
        self._source_valid(pid,data['state'],data['source_sha256'])
        return data

    def open_view(self,pid,wid,vid,revision,request_id):
        workspace=self.workspace(pid,wid)
        saved=self.view(pid,vid)
        state=saved['state']
        if (state['asset_id']!=workspace['asset_id'] or state['version_id']!=workspace['version_id']):
            raise BIWorkspaceError('保存视图的数据版本与当前工作区不一致')
        result=self.engine.execute(pid,state)
        batch=ToolBatch(expected_revision=revision,actions=[BIAction(tool='bi.view.save')],request_id=request_id)
        output=self._commit(pid,workspace,batch,state,result,kind='open_view')
        output['perspective']=saved['perspective']
        return output

    def freeze(self,pid,wid,confirmed):
        if not confirmed:raise BIWorkspaceError('冻结全部行级范围需用户确认')
        item=self.workspace(pid,wid);state=item['state']
        if state['join'] or state['bucket'] or state['expressions'] or state['window'] or state.get('windows') or state['industrial']:
            raise BIWorkspaceError('当前含虚拟关联/计算/工业图层；冻结仅支持源表行级筛选，请先恢复纯筛选状态')
        from timepilot.data_platform import SelectionInput
        req=SelectionInput(native_asset_id=state['asset_id'],version_id=state['version_id'],
                           filters=[FilterSpec.model_validate(f) for f in state['query']['filters']],confirm=True)
        return self.platform.create_selection(pid,req)

    def model_draft(self,pid,wid,name,confirm_scope):
        if self.modeling is None:raise BIWorkspaceError('建模工作台未配置')
        selected=self.freeze(pid,wid,confirm_scope)
        item=self.workspace(pid,wid)
        doc=CreateModelingDraft(asset_id=item['asset_id'],version_id=item['version_id'],
             selection_id=selected['selection_id'],name=name,confirm_scope=True)
        draft=self.modeling.create_draft(pid,doc)
        return {'draft':draft,'selection':selected,'review_required':True}

    def _deterministic_plan(self,message,columns):
        """Limited offline planner; never pretends to understand arbitrary language."""
        actions=[];text=message.strip()
        # Schema inspection is a complete action: no field name is required.
        # User data remains the only authority for the returned column names.
        from timepilot.bi_intent_resolver import BIIntentResolver
        basic=BIIntentResolver().resolve(text,[{'name':c,'physical_type':'UNKNOWN'} for c in columns])
        kind=basic['intent_candidates'][0]
        if kind=='inspect_schema':return [BIAction(tool='bi.schema.inspect')]
        if kind=='inspect_asset':return [BIAction(tool='bi.asset.describe')]
        if kind=='catalog':return [BIAction(tool='bi.catalog.list')]
        if kind=='inspect_rows':
            found=re.search(r'(?:前|第)\s*(\d{1,4})\s*(?:条|行|个记录)',text)
            count=min(1000,max(1,int(found.group(1)))) if found else 20
            return [BIAction(tool='bi.rows.peek',arguments={'limit':count,'offset':0})]
        if any(w in text for w in ('撤销','上一步')):
            return [BIAction(tool='bi.workspace.undo')]
        if any(w in text for w in ('重做','恢复刚才的操作')):
            return [BIAction(tool='bi.workspace.redo')]
        names=sorted(columns,key=len,reverse=True)
        # Exact column names are always looked up in the current dataset. We only
        # support intentionally narrow offline commands; ambiguous requests remain
        # unexecuted rather than fabricating business roles or inferred units.
        if '恢复明细' in text or '取消时间桶' in text:
            return [BIAction(tool='bi.timeseries.clear')]
        if '取消关联' in text:
            return [BIAction(tool='bi.join.clear')]
        if '取消计算列' in text:
            return [BIAction(tool='bi.expression.clear')]
        if '取消滚动' in text:
            return [BIAction(tool='bi.window.clear')]
        if '移除维修图层' in text or '取消工况图层' in text:
            return [BIAction(tool='bi.chart.clear_overlay')]
        # Optional exact-string aggregate: 按设备分组，计算温度平均值
        if '分组' in text and any(x in text for x in ('平均','求和','最大','最小','计数')):
            group=next((n for n in names if re.search(r'按\s*'+re.escape(n)+r'\s*分组',text)),None)
            metric=next((n for n in names if n!=group and re.search(r'(?:求|计算|统计)\s*'+re.escape(n),text)),None)
            if group and metric:
                op=next((v for key,v in [('平均','avg'),('求和','sum'),('最大','max'),('最小','min'),('计数','count')] if key in text),'avg')
                return [BIAction(tool='bi.aggregate.run',arguments={
                    'group_by':[group],'operation':op,'column':metric,'alias':'result'})]
        # Reversible computed field; no source column is renamed.
        if ('差值' in text or '相减' in text) and len(names)>=2:
            pair=next(((a,b) for a in names for b in names if a!=b and
                re.search(re.escape(a)+r'\s*(?:减|\-|与|和)\s*'+re.escape(b),text)),None)
            if pair:
                return [BIAction(tool='bi.expression.create',arguments={
                    'name':'computed_difference',
                    'expression':{'kind':'binary','op':'-',
                        'left':{'kind':'column','value':pair[0]},
                        'right':{'kind':'column','value':pair[1]}}})]
        # Common Chinese comparators require an EXACT real field + a typed
        # literal. No inferred units, guessed columns or ad-hoc SQL.
        for name in names:
            exact=re.escape(name)
            anchor=r'(?<![A-Za-z0-9_])'+exact+r'(?![A-Za-z0-9_])'
            if re.search(anchor+r'\s*(?:为空|是空值|为NULL|为null)',text) and any(x in text for x in ('排除','去除','剔除','不看')):
                return [BIAction(tool='bi.filter.set',arguments={'column':name,'op':'not_null'})]
            if re.search(anchor+r'\s*(?:为零|等于零)',text) and any(x in text for x in ('排除','去除','剔除','不看')):
                return [BIAction(tool='bi.filter.set',arguments={'column':name,'op':'ne','value':0})]
            if any(x in text for x in ('只看','仅看','筛选','条件','挑出来','高于阈值')):
                match=re.search(anchor+r'\s*(?:高于|超过|低于|小于|高于阈值|超过阈值)\s*(-?\d+(?:\.\d+)?)',text)
                if match:
                    numeric=float(match.group(1));numeric=int(numeric) if numeric.is_integer() else numeric
                    op='lt' if re.search(anchor+r'\s*(?:低于|小于)',text) else 'gt'
                    return [BIAction(tool='bi.filter.set',arguments={'column':name,'op':op,'value':numeric})]
                match=re.search(anchor+r'\s*(?:加一个)?\s*(小于等于|大于等于|大于|小于|等于)\s*(-?\d+(?:\.\d+)?)',text)
                if match:
                    numeric=float(match.group(2));numeric=int(numeric) if numeric.is_integer() else numeric
                    return [BIAction(tool='bi.filter.set',arguments={'column':name,'op':
                        {'小于等于':'lte','大于等于':'gte','大于':'gt','小于':'lt','等于':'eq'}[match.group(1)],'value':numeric})]
                match=re.search(anchor+r'\s*(?:=|等于|为)\s*([\u4e00-\u9fa5A-Za-z_][\u4e00-\u9fa5A-Za-z_0-9]{0,39})(?=$|[，。；\s])',text)
                if match:
                    return [BIAction(tool='bi.filter.set',arguments={'column':name,'op':'eq','value':match.group(1)})]
        # Natural ascending/descending terms are recognized only for explicit
        # column references; never infer that a Time index has date units.
        for name in names:
            anchor=r'(?<![A-Za-z0-9_])'+re.escape(name)+r'(?![A-Za-z0-9_])'
            if re.search(r'(?:按|将|把|以)\s*'+anchor+r'.{0,12}(?:从大到小|从小到大|先后顺序)',text) or \
               re.search(anchor+r'\s*(?:按照先后顺序|由大到小|由小到大)',text):
                return [BIAction(tool='bi.sort.set',arguments={'field':name,
                    'descending':any(x in text for x in ('从大到小','由大到小'))})]
        # Strongly typed numeric filters (e.g. A2 > 0 / A2 大于 0).
        for name in names:
            pattern=re.escape(name)+r'\s*(>=|<=|>|<|==|=|大于等于|小于等于|大于|小于|等于)\s*(-?\d+(?:\.\d+)?)'
            hit=re.search(pattern,text,re.I)
            if hit:
                mapped={'>':'gt','<':'lt','>=':'gte','<=':'lte','==':'eq','=':'eq',
                        '大于等于':'gte','小于等于':'lte','大于':'gt','小于':'lt','等于':'eq'}
                value=float(hit.group(2));value=int(value) if value.is_integer() else value
                actions.append(BIAction(tool='bi.filter.set',arguments={'column':name,'op':mapped[hit.group(1)],'value':value}))
                break
        for name in names:
            if re.search(r'按\s*'+re.escape(name)+r'\s*(?:升序|降序|排序)',text,re.I) or re.search(re.escape(name)+r'\s*(?:升序|降序)',text,re.I):
                actions.append(BIAction(tool='bi.sort.set',arguments={'field':name,'descending':'降序' in text}))
                break
        graph={'折线':'line','曲线':'line','柱状':'bar','散点':'scatter','面积':'area','透视':'pivot','表格':'grid'}
        chart=next((kind for k,kind in graph.items() if k in text),None)
        if chart:
            after=text.split('绘制',1)[-1].split('画出',1)[-1] if ('绘制' in text or '画出' in text) else text
            mentioned=[x for x in columns if re.search(r'(?<![A-Za-z0-9_])'+re.escape(x)+r'(?![A-Za-z0-9_])',after)]
            if not mentioned:
                return actions  # Never fabricate an empty plot for an unknown field.
            x=next((x for x in columns if re.search(r'(?:按|随|横轴|X轴|以)\s*'+re.escape(x),text,re.I)),None)
            if x is None:x=next((n for n in mentioned if n.lower() in ('time','timestamp','date','时间','采集时间')),None)
            if x is None and mentioned:x=mentioned[0]
            ys=[n for n in mentioned if n!=x][:8]
            if not ys and len(mentioned)==1:ys=[mentioned[0]];x=None
            actions.append(BIAction(tool='bi.chart.add_panel' if ('再画' in text or '另画' in text or '新增图表' in text) else 'bi.chart.create',arguments={'type':chart,'x':x,'y':ys}))
        return actions

    def _project_join_suggestion(self,pid,workspace,message):
        # A deterministic, metadata-only join suggestion. Never guess a join key
        # solely because columns happen to share the same name.
        if not any(v in message for v in ('关联','连接','合并表')):return None
        source=self._asset(pid,workspace['asset_id'],workspace['version_id'])
        left={x['name'] for x in source['columns']}
        options=[]
        for item in {x['native_asset_id']:x for x in [
                *self.projects.native_for(pid),*self.projects.generic_for(pid)]}.values():
            rid=item['native_asset_id']
            if rid==workspace['asset_id']:continue
            try:
                obj=self._asset(pid,rid,'base')
            except (KeyError,ValueError):continue
            if obj.get('name') and obj['name'] in message:
                right={x['name'] for x in obj['columns']}
                common=[x for x in left & right if x in message]
                if len(common)==1:
                    options.append((rid,common[0],obj['name']))
        if len(options)!=1:return None
        rid,key,_=options[0]
        return [BIAction(tool='bi.join.preview',arguments={
            'right_asset_id':rid,'right_version_id':'base',
            'left_key':key,'right_key':key,'how':'left'})]

    def plan_turn(self,pid,wid,message):
        workspace=self.workspace(pid,wid)
        columns=self._columns(pid,workspace['state'])
        from timepilot.bi_nl_math import parse_math_request
        math_actions,math_explanation,math_clarification=parse_math_request(
            message,self._asset(pid,workspace['asset_id'],workspace['version_id'])['columns'])
        if math_actions:
            return {'actions':[a.model_dump(mode='json') for a in math_actions],
                    'planner':'本地字段与运算解析','warning':None,'usage':{},
                    'message':math_explanation,'defaults':{'window_basis':'source_row_order'}
                        if any(a.tool=='bi.window.compute_many' for a in math_actions) else {}}
        if math_clarification:
            return {'actions':[],'planner':'本地预检','warning':None,'usage':{},
                    'message':math_clarification,'missing_slots':['numeric_fields']}
        proposed=self._project_join_suggestion(pid,workspace,message);origin='本地关联预检查' if proposed else '规则';usage={};warning=None
        if proposed is None:
            immediate=self._deterministic_plan(message,columns)
            if len(immediate)==1 and TOOLS[immediate[0].tool][1]=='read':
                return {'actions':[x.model_dump(mode='json') for x in immediate],
                        'planner':'本地只读指令','warning':None,'usage':{},
                        'message':'已匹配真实数据的只读查询工具'}
        if self.models is not None and proposed is None:
            try:
                setting=self.models.config()
                if setting.get('enabled') and setting.get('has_api_key'):
                    eligible=[x for x in tool_catalog() if x['effect'] in {'reversible','read'}]
                    prompt=('你是 TimePilot BI 工具规划器。只能输出严格 JSON 对象 {"actions":[{"tool":"...","arguments":{...}}]}。'
                            '不得执行 SQL、Python、文件操作或修改项目和资产 ID。'
                            '只引用已列出的真实字段；最多 8 步，只能选择给定工具。'
                            '未知或歧义时返回空 actions。当前数据来源和项目权限均由服务端控制。')
                    asset_catalog=[]
                    related=list({x['native_asset_id']:x for x in [
                        *self.projects.native_for(pid),*self.projects.generic_for(pid)]}.values())
                    for row in related[:25]:
                        aid=row['native_asset_id']
                        if aid==workspace['asset_id']:continue
                        try:
                            meta=self._asset(pid,aid,'base')
                            asset_catalog.append({'asset_id':aid,'name':meta['name'],
                                'fields':[c['name'] for c in meta['columns'][:40]]})
                        except (KeyError,ValueError):pass
                    payload={'columns':columns,'available_assets':asset_catalog,
                             'tools':[{'name':x['tool'],'schema':x['input_schema']} for x in eligible],
                             'request':message,'current':workspace['state']['chart']}
                    result=self.models._message([{'role':'system','content':prompt},
                        {'role':'user','content':json.dumps(payload,ensure_ascii=False)}],max_tokens=1700)
                    raw=self.models._parse_json(result['content'])
                    proposed=[BIAction.model_validate(x) for x in raw.get('actions',[])]
                    if not proposed:proposed=None  # Empty LLM plan must use the local fallback.
                    if proposed is not None and (len(proposed)>8 or any(TOOLS[a.tool][1] not in {'reversible','read'} for a in proposed)):
                        raise BIWorkspaceError('模型提出了未授权操作')
                    if proposed is not None:
                        for action in proposed:validate_action(action)
                        origin='LLM 工具规划'
                    usage=result.get('usage',{})
            except Exception as exc:
                warning='模型计划不可用，已使用本地确定性规划（'+type(exc).__name__+'）'
        if proposed is None:proposed=self._deterministic_plan(message,columns)
        return {'actions':[x.model_dump(mode='json') for x in proposed],
                'planner':origin,'warning':warning,'usage':usage,
                'message':'尚未匹配到可安全执行的操作。请提供具体字段或要查看的结果。' if not proposed else '已生成受控工具执行计划'}

    def turns_history(self,pid,wid,limit=80):
        self.workspace(pid,wid)
        with self.projects._db() as db:
            rows=db.execute('''SELECT turn_id,message,status,plan_json,revision,created_at
                FROM bi_agent_turns WHERE project_id=? AND workspace_id=? AND owner_id=?
                ORDER BY created_at DESC, rowid DESC LIMIT ?''',
                (pid,wid,self._actor(),max(1,min(200,int(limit))))).fetchall()
        return [{**dict(row),'plan':json.loads(row['plan_json'])} for row in reversed(rows)]

    def _remember_turn(self,pid,wid,message,request_id,plan,status,revision):
        # Never record raw source samples, model prompts, API keys, or a generated SQL string.
        with self.projects._lock,self.projects._db() as db:
            db.execute('''INSERT OR IGNORE INTO bi_agent_turns VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
                (str(uuid4()),pid,wid,self._actor(),request_id,'user',message,status,
                 json.dumps(plan,ensure_ascii=False),revision,timestamp()))
            # Bound the durable transcript; action audit remains permanent.
            db.execute('''DELETE FROM bi_agent_turns WHERE project_id=? AND workspace_id=? AND owner_id=?
                AND turn_id NOT IN (SELECT turn_id FROM bi_agent_turns WHERE project_id=?
                AND workspace_id=? AND owner_id=? ORDER BY created_at DESC,rowid DESC LIMIT 200)''',
                (pid,wid,self._actor(),pid,wid,self._actor()))

    def turn(self,pid,wid,message,revision,request_id,*,execute=True,confirm=False):
        plan=self.plan_turn(pid,wid,message)
        if not plan['actions']:
            self._remember_turn(pid,wid,message,request_id,plan,'no_action',revision)
            return {**plan,'executed':False,'workspace':self.workspace(pid,wid)}
        actions=[BIAction.model_validate(x) for x in plan['actions']]
        if self._only_read(actions):
            workspace=self.workspace(pid,wid)
            if revision!=workspace['revision']:
                raise BIWorkspaceError('工作区状态已改变，请刷新')
            result=self._read_batch(pid,workspace,actions) if execute else {
                'workspace':workspace,'read_results':[],'changed':False,'executed':False}
            self._remember_turn(pid,wid,message,request_id,plan,'read' if execute else 'preview',revision)
            return {**plan,**result,'executed':execute}
        if len(actions)==1 and actions[0].tool in {'bi.workspace.undo','bi.workspace.redo'}:
            if not execute:return {**plan,'executed':False}
            result=self.undo_redo(pid,wid,revision,request_id,actions[0].tool.rsplit('.',1)[-1])
        else:
            batch=ToolBatch(expected_revision=revision,actions=actions,request_id=request_id,confirm=confirm)
            result=self.apply(pid,wid,batch) if execute else self.preview(pid,wid,batch)
        self._remember_turn(pid,wid,message,request_id,plan,'applied' if execute else 'preview',
                             result.get('workspace',{}).get('revision',revision))
        return {**plan,**result,'executed':execute}
