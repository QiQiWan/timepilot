"""One auditable tool manifest for UI, Agent planning and execution validation."""
from __future__ import annotations
from dataclasses import dataclass
from pydantic import BaseModel, ConfigDict, Field
from timepilot.feature_contracts import FeatureSpecV2,EvaluationProtocol


class NoArgs(BaseModel):
    model_config=ConfigDict(extra='forbid')

class SearchInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    query:str=Field(default='',max_length=120)

class ProfileInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    request_id:str=Field(min_length=1,max_length=120)

class DraftInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    spec:FeatureSpecV2
    request_id:str=Field(min_length=1,max_length=120)

class SetInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    set_id:str=Field(min_length=1)
    request_id:str=Field(min_length=1,max_length=120)

class EvaluationPlanInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    protocol:EvaluationProtocol

class ViewerOpenInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    set_id:str=Field(min_length=1)


@dataclass(frozen=True)
class FeatureTool:
    id:str
    title:str
    effect:str
    input_model:type[BaseModel]
    executor:bool=True
    requires_engine:bool=False
    description:str=''


TOOLS={t.id:t for t in (
    FeatureTool('feature.schema.inspect','查看字段清单','read',NoArgs),
    FeatureTool('feature.field.search','字段检索','read',SearchInput),
    FeatureTool('feature.contract.inspect','查看字段与时间契约','read',NoArgs),
    FeatureTool('feature.contract.validate','核实结构与时间含义','read',NoArgs),
    FeatureTool('feature.operator.search','查找特征算子','read',NoArgs),
    FeatureTool('feature.profile.get_or_create','生成完整数据画像','compute',ProfileInput,requires_engine=True),
    FeatureTool('feature.candidate.propose','推荐基础特征','read',NoArgs),
    FeatureTool('feature.advanced.capabilities','查看高级算子可用性与资源限制','read',NoArgs),
    FeatureTool('feature.spec.validate','校验特征定义','read',DraftInput),
    FeatureTool('feature.set.draft_create','保存特征草稿','draft',DraftInput),
    FeatureTool('feature.set.preview','执行特征预览','compute',SetInput,requires_engine=True),
    FeatureTool('feature.run.create','全量特征计算','compute',SetInput,requires_engine=True),
    FeatureTool('feature.run.get','查看运行状态','read',SearchInput),
    FeatureTool('feature.evaluation.plan','规划实验评价','read',EvaluationPlanInput),
    FeatureTool('feature.quality.inspect','检查特征质量','read',SearchInput),
    FeatureTool('feature.lineage.inspect','查看特征血缘','read',SearchInput),
    FeatureTool('feature.publish.preview','查看发布影响','read',SearchInput),
    FeatureTool('feature.viewer.open','在 BI 工作区显示已完成特征','presentation',ViewerOpenInput,
                requires_engine=True,description='申请客户端显示完整物化数据并等待真实 Viewer 回执'),
    FeatureTool('feature.profile.event_effect','维护事件前后统计','compute',NoArgs,executor=False),
    FeatureTool('feature.window.duration','真实时长窗口','compute',NoArgs,executor=False),
    FeatureTool('feature.join.virtual','跨表关联','compute',NoArgs,executor=False),
    FeatureTool('feature.expression.custom','自由计算表达式','compute',NoArgs,executor=False),
)}


def manifest(*,engine_ready=True):
    out=[]
    for tool in TOOLS.values():
        why='尚未实现可审计执行器' if not tool.executor else (
            '缺少真实 DuckDB/Arrow 运行依赖' if tool.requires_engine and not engine_ready else None)
        out.append({'tool_id':tool.id,'display_name':tool.title,'effect':tool.effect,
                    'available':why is None,'disabled_reason':why,
                    'input_schema':tool.input_model.model_json_schema(),
                    'required_confirmation':'none' if tool.effect in ('read','presentation') else 'plan_preview',
                    'description':tool.description})
    return out


def validate(tool_id,args,*,engine_ready=True):
    from timepilot.feature_tool_manifest import TOOLS
    tool=TOOLS.get(tool_id)
    if not tool or not tool.executor:raise ValueError('未知或尚未实施的 Feature Tool：'+str(tool_id))
    if tool.requires_engine and not engine_ready:raise ValueError('缺少真实计算引擎，无法执行此工具')
    return tool.input_model.model_validate(args)


def choose_tools(message:str, available:list[dict], max_tools:int=12)->list[dict]:
    """Rank only executable, authorized tool definitions by user intent.

    This avoids presenting the arbitrary first twelve schemas to the model.
    The output is a proposal subset; execution still revalidates against TOOLS.
    """
    phrase=str(message or '').casefold()
    keywords={
      'feature.schema.inspect':('字段','列名','schema','有哪些列'),
      'feature.field.search':('查找','字段','列','搜索'),
      'feature.contract.inspect':('时间','设备','契约','语义','单位'),
      'feature.contract.validate':('时间','契约','核查','检查'),
      'feature.operator.search':('算子','方法','特征','窗口','移动平均','滞后'),
      'feature.profile.get_or_create':('画像','分布','异常','规律','概览','统计'),
      'feature.candidate.propose':('推荐','建议','候选','发现'),
      'feature.advanced.capabilities':('高级特征','tsfresh','featuretools','多表','自动特征'),
      'feature.spec.validate':('验证','检查','特征','构造','公式'),
      'feature.set.draft_create':('生成','创建','新列','移动平均','滚动','特征','草稿'),
      'feature.set.preview':('预览','看看结果','计算结果'),
      'feature.run.create':('全量','运行','计算','物化'),
      'feature.run.get':('进度','状态','任务','失败'),
      'feature.evaluation.plan':('评估','预测','实验','模型','比较'),
      'feature.quality.inspect':('质量','有效','可靠'),
      'feature.lineage.inspect':('血缘','来源','引用'),
      'feature.publish.preview':('发布','审核','影响'),
      'feature.viewer.open':('展示','显示','BI','图表','绘制','可视化'),
    }
    ranked=[]
    for tool in available:
        if not tool['available'] or tool['effect'] not in ('read','draft','compute','presentation'):
            continue
        terms=keywords.get(tool['tool_id'],())
        hits=sum(2 if len(k)>=2 else 1 for k in terms if k.casefold() in phrase)
        if hits or tool['tool_id'] in ('feature.schema.inspect','feature.field.search',
                                        'feature.contract.inspect','feature.operator.search'):
            ranked.append((hits, tool['tool_id'],tool))
    ranked.sort(key=lambda t:(-t[0],t[1]))
    return [t[-1] for t in ranked[:max(1,min(max_tools,16))]]
