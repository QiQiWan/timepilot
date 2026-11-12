"""Construct checked custom formula definitions against one immutable source."""
from __future__ import annotations

from timepilot.feature_contracts import FeatureDefinition, digest
from timepilot.feature_expression import validate_formula, compile_formula
from timepilot.feature_operator_registry import compatible_fields, validate_operator
from timepilot.native_bi import quote_identifier


def validate_custom_definition(contract, input_field_ids: list[str], expression: str,
                               display_name: str | None = None) -> dict:
    if not isinstance(input_field_ids,list) or not 1<=len(input_field_ids)<=8 or len(set(input_field_ids))!=len(input_field_ids):
        raise ValueError('公式须选择 1—8 个不重复的输入字段')
    eligible=set(compatible_fields(contract,'numeric'))
    if any(fid not in eligible for fid in input_field_ids):
        raise ValueError('存在未经核实、非数值、时间、标签或标识符字段，禁止参与公式计算')
    info=validate_formula(expression,len(input_field_ids))
    by_id={f.field_id:f for f in contract.fields}
    selected=[by_id[fid] for fid in input_field_ids]
    compile_formula(expression,[quote_identifier(f.source_name) for f in selected],[f.unit for f in selected])
    seed=digest({'formula':info['expression'],'inputs':input_field_ids})
    title=(display_name or '').strip() or ('自定义：'+('、'.join(f.source_name for f in selected))[:110])
    definition=FeatureDefinition(feature_id='expr_'+seed[:24],output_name='expr_'+seed[:20],
        display_name=title,operator_id='expression.safe',operator_version='2.8',
        input_field_ids=input_field_ids,parameters={'expression':info['expression']})
    validate_operator(definition)
    return {'definition':definition.model_dump(),'formula':info,'field_mapping':[
        {'alias':f'x{i}','field_id':f.field_id,'source_name':f.source_name,'unit':f.unit}
        for i,f in enumerate(selected)],'calculates_now':False,
        'note':'只完成公式静态校验，保存特征集后需执行真实数据预览与物化'}
