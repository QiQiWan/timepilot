"""Determines whether the current registered engine can execute an intent."""
from __future__ import annotations

class BIFeasibility:
    def check(self,intent,metadata,state,requested=None,*,duckdb_ready=True):
        kind=intent['intent_candidates'][0]
        known={c['name'] for c in metadata['columns']}
        if intent.get('unresolved_fields'):
            return self._result('needs_clarification','字段不存在：'+','.join(intent['unresolved_fields']),[])
        if kind!='correlation':
            return self._result('unsupported',f'当前版本尚无 {kind} 的模糊指令完整算子；可继续使用字段明确的原 BI 工具',[])
        if state.get('join') or state.get('industrial') or state.get('bucket'):
            return self._result('unsupported','当前含关联、时间桶或工业图层；先恢复源表行级分析',[])
        if not duckdb_ready:
            return self._result('blocked','DuckDB 未安装或不可用，相关性全量计算无法执行',[])
        numeric=intent['numeric_fields']
        fields=requested if requested is not None else intent['mentioned_fields'] or intent['auto_fields']
        if not isinstance(fields,list) or len(set(fields))!=len(fields) or any(x not in known for x in fields):
            return self._result('blocked','字段引用不属于当前资产',[])
        if not set(fields)<=set(numeric):
            return self._result('needs_clarification','相关性需要数值列，请移除文本或时间字段',[])
        if len(numeric)<2:return self._result('unsupported','当前源表少于两列数值字段，无法计算相关矩阵',[])
        if len(fields)<2:return self._result('needs_clarification','至少选择两个数值字段',[])
        if len(fields)>40:return self._result('needs_clarification','最多 40 列，先缩小选择',[])
        suggest=set(intent.get('index_like_fields',[])) & set(numeric)
        return {'status':'feasible_confirm' if suggest or len(fields)>20 else 'feasible_auto',
                'reasons':[], 'defaults':{'field_ids':fields,'method':'pearson','null_policy':'pairwise'},
                'alternatives':['调整字段集','恢复源表明细'],
                'estimated_cost':{'pair_count':len(fields)*(len(fields)+1)//2,'scan_scope':'full_filtered_revision'},
                'needed_inputs':[], 'index_suggestions':sorted(suggest)}

    @staticmethod
    def _result(status,reason,inputs):
        return {'status':status,'reasons':[reason],'defaults':{},'alternatives':[],
                'estimated_cost':{},'needed_inputs':inputs}
