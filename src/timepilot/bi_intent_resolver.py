"""Conservative local intent and exact-column resolution for ambiguous BI text."""
from __future__ import annotations
import re

class BIIntentResolver:
    def resolve(self,message,columns,workspace_state=None):
        text=message.strip();available=[c['name'] for c in columns]
        names=sorted(available,key=len,reverse=True)
        mentioned=[x for x in names if re.search(r'(?<![A-Za-z0-9_])'+re.escape(x)+r'(?![A-Za-z0-9_])',text)]
        intent=('inspect_schema' if any(t in text for t in ('查看数据字段','查看字段','字段有哪些','有哪些字段','有哪些列','列出字段','字段信息','字段列表','查看列名','字段类型','字段结构','所有字段','数据的列名','表结构'))
                else 'inspect_rows' if re.search(r'(?:查看|显示|看看|预览)?\s*(?:前|第)\s*\d+\s*(?:条|行|个记录)',text) or any(t in text for t in ('查看数据样本','预览数据','看看前几行'))
                else 'inspect_asset' if any(t in text for t in ('多少行','多少列','行数','列数','数据资产信息','这个表的信息','查看资产概览'))
                else 'catalog' if any(t in text for t in ('资产列表','有哪些数据表','所有数据资产','列出资产','项目的表'))
                else 'correlation' if any(t in text for t in ('相关性','相关系数','相关程度','相关矩阵','热力图'))
                else 'trend' if any(t in text for t in ('趋势','波动','走势'))
                else 'compare' if any(t in text for t in ('比较','对比'))
                else 'profile' if any(t in text for t in ('统计','分布','概况'))
                else 'unknown')
        numeric=[c['name'] for c in columns if any(k in str(c.get('physical_type','')).upper()
                 for k in ('DOUBLE','FLOAT','DECIMAL','INT','REAL','HUGEINT'))]
        # Labels/indices are only suggestions. The user can include them explicitly.
        index_like=[n for n in numeric if n.lower() in ('time','index','row_number','序号','行号')]
        guessed_unknown=[]
        for word in re.findall(r'(?<![A-Za-z0-9_])[A-Za-z][A-Za-z0-9_]{1,30}(?![A-Za-z0-9_])',text):
            if re.fullmatch(r'[A-Z]\d+',word) and word not in available:
                guessed_unknown.append(word)
        return {'intent_candidates':[intent], 'mentioned_fields':mentioned,
                'unresolved_fields':guessed_unknown,'confidence':0.94 if intent!='unknown' else 0,
                'evidence':['关键词与原始字段白名单'],'missing_slots':[],
                'numeric_fields':numeric,'index_like_fields':index_like,
                'auto_fields':[n for n in numeric if n not in index_like][:40]}
