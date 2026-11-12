"""Versioned, project-scoped BI tool contracts. The agent only proposes JSON arguments.

No tool accepts SQL, Python, file paths or an arbitrary asset scope. Authorization and
execution happen in the BIWorkspaceService under the authenticated project context.
"""
from __future__ import annotations
from pydantic import BaseModel, ConfigDict, Field, model_validator
from typing import Any, Literal

class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)

class BIAction(Strict):
    tool: str = Field(min_length=3, max_length=80)
    arguments: dict[str, Any] = Field(default_factory=dict)

class OpenWorkspace(Strict):
    asset_id: str
    version_id: str = 'base'

class ToolBatch(Strict):
    expected_revision: int = Field(ge=1)
    actions: list[BIAction] = Field(min_length=1, max_length=8)
    request_id: str = Field(min_length=8, max_length=100)
    confirm: bool = False

class ChatTurn(Strict):
    message: str = Field(min_length=1, max_length=1200)
    expected_revision: int = Field(ge=1)
    request_id: str = Field(min_length=8, max_length=100)
    execute: bool = True
    confirm: bool = False

class SaveWorkspace(Strict):
    name: str = Field(min_length=1, max_length=120)
    perspective: dict[str,Any] | None = None
    shared: bool = False

class CreateModelDraft(Strict):
    name: str = Field(min_length=2, max_length=120)
    confirm_scope: bool = False

class FilterArgs(Strict):
    column: str
    op: Literal['eq','ne','gt','gte','lt','lte','contains','starts_with','in','is_null','not_null']
    value: Any = None

class SortArgs(Strict):
    field: str
    descending: bool = False

class ColumnsArgs(Strict):
    columns: list[str] = Field(min_length=1, max_length=80)

class AggregateArgs(Strict):
    group_by: list[str] = Field(default_factory=list, max_length=5)
    operation: Literal['count','sum','avg','min','max','count_distinct'] = 'count'
    column: str | None = None
    alias: str = 'value'

class ChartArgs(Strict):
    type: Literal['grid','line','bar','scatter','area','heatmap','pivot']
    x: str | None = None
    y: list[str] = Field(default_factory=list, max_length=8)
    color: str | None = None

class SplitArgs(Strict):
    columns: list[str] = Field(min_length=1,max_length=5)

class ViewerThemeArgs(Strict):
    theme: Literal['Pro Light','Pro Dark']

class ViewerSettingsArgs(Strict):
    enabled: bool

class CatalogSearchArgs(Strict):
    q: str = Field(default='',max_length=160)
    page: int = Field(default=1,ge=1,le=100000)
    page_size: int = Field(default=20,ge=1,le=100)

class HeatmapArgs(Strict):
    analysis_id: str = Field(min_length=36,max_length=36)

class CorrelationArgs(Strict):
    field_ids: list[str] = Field(min_length=2,max_length=40)
    method: Literal['pearson']='pearson'
    null_policy: Literal['pairwise']='pairwise'

class ExpressionNode(Strict):
    # Recursive AST validated by the compiler for max depth and declared-column safety.
    kind: Literal['column','number','binary','function']
    value: Any = None
    op: str | None = None
    left: dict | None = None
    right: dict | None = None
    args: list[dict] | None = None

class ExpressionArgs(Strict):
    name: str = Field(pattern=r'^[A-Za-z_][A-Za-z0-9_]{0,63}$')
    expression: dict

class WindowArgs(Strict):
    column: str
    time_field: str
    window: int = Field(ge=2,le=240)
    entity_field: str | None = None
    name: str = Field(default='rolling_avg',pattern=r'^[A-Za-z_][A-Za-z0-9_]{0,63}$')

class WindowManyArgs(Strict):
    columns: list[str] = Field(min_length=1,max_length=8)
    time_field: str = '__tp_rowid'
    window: int = Field(default=5,ge=2,le=240)
    entity_field: str | None = None
    output_names: list[str] | None = None

    @model_validator(mode='after')
    def validate_names(self):
        if len(set(self.columns))!=len(self.columns):raise ValueError('源字段不能重复')
        if self.output_names is not None:
            if len(self.output_names)!=len(self.columns):raise ValueError('每个源字段须对应一个输出列')
            for name in self.output_names:
                if not __import__('re').fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,63}',name):
                    raise ValueError('输出字段只能使用安全技术标识')
        return self

class BucketArgs(Strict):
    time_field: str
    metric_field: str
    interval: Literal['1m','5m','15m','1h','1d'] = '1h'
    operation: Literal['avg','sum','min','max','count'] = 'avg'
    entity_field: str | None = None

class JoinArgs(Strict):
    right_asset_id: str
    right_version_id: str = 'base'
    left_key: str
    right_key: str
    how: Literal['left','inner'] = 'left'

class OverlayArgs(Strict):
    template: Literal['temperature_trend','maintenance_overlay','regime_transition','quality_process','equipment_comparison'] = 'maintenance_overlay'
    time_field: str
    metric_field: str
    event_field: str | None = None
    regime_field: str | None = None
    device_field: str | None = None
    device_value: str | None = None
    interval: Literal['1m','5m','15m','1h','1d'] = '1h'

# First two versions: 25 actual tools. Execute authorization is separate from schema discovery.
# Read-only tools do not mutate the workspace; reversible tools change only presentation
# and query state. Freezing a selection or drafting a model requires an explicit confirmation.
TOOLS = {
  'bi.stats.correlation': ('statistics','read',CorrelationArgs,'计算受限全量 Pearson 相关矩阵（分析计划 API）'),
  'bi.chart.heatmap': ('chart','reversible',HeatmapArgs,'展示已授权的真实相关矩阵'),
  'bi.catalog.list': ('discover','read',None,'列出当前项目可使用的数据资产'),
  'bi.catalog.search': ('discover','read',CatalogSearchArgs,'在当前授权项目内查询资产目录'),
  'bi.asset.impact': ('discover','read',None,'检查当前资产删除影响，不执行删除'),
  'bi.viewer.capabilities': ('discover','read',None,'读取当前 Perspective 已核验的功能与限制'),
  'bi.asset.describe': ('discover','read',None,'检查绑定的资产版本'),
  'bi.schema.inspect': ('discover','read',None,'读取原始字段与物理类型'),
  'bi.profile.inspect': ('discover','read',None,'读取字段统计画像'),
  'bi.rows.peek': ('query','read',None,'执行安全分页预览'),
  'bi.filter.set': ('query','reversible',FilterArgs,'添加行级筛选'),
  'bi.filter.remove': ('query','reversible',None,'删除指定或全部筛选'),
  'bi.filter.clear': ('query','reversible',None,'清除行级筛选'),
  'bi.sort.set': ('query','reversible',SortArgs,'设置排序字段'),
  'bi.sort.clear': ('query','reversible',None,'清除当前排序'),
  'bi.pivot.split.set': ('query','reversible',SplitArgs,'设置 Perspective 原生 Split By 字段'),
  'bi.pivot.split.clear': ('query','reversible',None,'移除原生 Split By 列透视'),
  'bi.viewer.settings.toggle': ('chart','reversible',ViewerSettingsArgs,'展开或折叠 Perspective 原生配置面板'),
  'bi.viewer.theme.set': ('chart','reversible',ViewerThemeArgs,'设置原生 Viewer 明/暗主题'),
  'bi.columns.set': ('query','reversible',ColumnsArgs,'选择显示字段'),
  'bi.aggregate.run': ('query','reversible',AggregateArgs,'执行分组与统计'),
  'bi.aggregate.clear': ('query','reversible',None,'恢复行级明细'),
  'bi.chart.create': ('chart','reversible',ChartArgs,'绘制原生 Perspective 图表'),
  'bi.chart.configure': ('chart','reversible',ChartArgs,'修改图表坐标与系列'),
  'bi.chart.plugin.set': ('chart','reversible',ChartArgs,'以受验证字段切换已安装原生图表插件'),
  'bi.chart.add_panel': ('chart','reversible',ChartArgs,'在工作区新增 Perspective 面板'),
  'bi.expression.create': ('compute','reversible',ExpressionArgs,'生成只读计算列'),
  'bi.window.compute': ('compute','reversible',WindowArgs,'只读滚动平均窗口'),
  'bi.window.compute_many': ('compute','reversible',WindowManyArgs,'对多个数值字段按明确排序依据计算因果移动平均，生成独立虚拟列'),
  'bi.timeseries.bucket': ('compute','reversible',BucketArgs,'按时间窗口聚合'),
  'bi.timeseries.clear': ('compute','reversible',None,'取消时间桶并恢复原始数据'),
  'bi.expression.clear': ('compute','reversible',None,'取消所有虚拟计算列'),
  'bi.window.clear': ('compute','reversible',None,'取消移动窗口计算'),
  'bi.join.preview': ('join','read',JoinArgs,'检验关联基数与膨胀'),
  'bi.join.apply_virtual': ('join','confirm',JoinArgs,'创建只读双表关联视图'),
  'bi.join.clear': ('join','reversible',None,'移除虚拟关联'),
  'bi.chart.overlay': ('industrial','reversible',OverlayArgs,'维护事件与工况图层'),
  'bi.chart.clear_overlay': ('industrial','reversible',None,'移除事件与工况图层'),
  'bi.workspace.undo': ('history','control',None,'撤销工作区上一步'),
  'bi.workspace.redo': ('history','control',None,'恢复已撤销操作'),
  'bi.view.save': ('history','save',None,'保存来源固定的分析工作区'),
  'bi.selection.freeze': ('model','confirm',None,'冻结完整行级范围供模型复用'),
  'bi.model.draft': ('model','confirm',None,'以冻结选择生成待审核的模型草稿'),
}

def catalog() -> list[dict]:
    result=[]
    for name,(category,effect,model,description) in TOOLS.items():
        result.append({'tool':name,'category':category,'effect':effect,'description':description,
                       'input_schema':model.model_json_schema() if model else ({'type':'object','properties':{'limit':{'type':'integer','minimum':0,'maximum':1000},'offset':{'type':'integer','minimum':0,'maximum':2000000}},'additionalProperties':False} if name=='bi.rows.peek' else {'type':'object','properties':{'index':{'type':'integer','minimum':0,'maximum':12}},'additionalProperties':False} if name=='bi.filter.remove' else {'type':'object','properties':{},'additionalProperties':False})})
    return result

def validate_action(raw: BIAction) -> dict:
    if raw.tool not in TOOLS:
        raise ValueError('未注册的 BI 工具：'+raw.tool)
    model=TOOLS[raw.tool][2]
    if model is not None:
        return model.model_validate(raw.arguments).model_dump(mode='json')
    if raw.tool in {'bi.filter.remove','bi.rows.peek'}:
        keys={'index'} if raw.tool=='bi.filter.remove' else {'limit','offset'}
        if set(raw.arguments)-keys: raise ValueError('该工具包含未知参数')
        if raw.tool=='bi.filter.remove':
            value=raw.arguments.get('index')
            if value is not None and (not isinstance(value,int) or isinstance(value,bool) or value<0 or value>12):
                raise ValueError('筛选编号无效')
        if raw.tool=='bi.rows.peek':
            for key,maxvalue in [('limit',1000),('offset',2_000_000)]:
                value=raw.arguments.get(key)
                if value is not None and (not isinstance(value,int) or isinstance(value,bool) or not 0 <= value<=maxvalue):
                    raise ValueError('分页参数无效')
        return raw.arguments
    if raw.arguments:raise ValueError('该工具不需要参数')
    return {}
