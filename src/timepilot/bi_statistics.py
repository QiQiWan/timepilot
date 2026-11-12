"""Full-row Pearson correlation with DuckDB, pairwise finite sample counts.

Only backend-validated, immutable asset columns can become SQL identifiers.
No arbitrary SQL, sampling or unbounded selected field count is accepted.
"""
from __future__ import annotations
import hashlib
import json
import math
import threading
import time
from timepilot.bi_query_engine import BIQueryEngine
from timepilot.native_bi import NativeBIError,QuerySpec,quote_identifier,compile_query

class BICorrelationService:
    def __init__(self, engine: BIQueryEngine):
        self.engine=engine

    def profile_numeric_fields(self,pid,workspace):
        state=workspace['state']
        meta=self.engine.checked(pid,state['asset_id'],state['version_id'])
        return [{'name':c['name'],'physical_type':c.get('physical_type')} for c in meta['columns']
                if any(x in str(c.get('physical_type','')).upper() for x in ('DOUBLE','FLOAT','DECIMAL','INT','REAL'))]

    @staticmethod
    def selection_fingerprint(state, source_sha256):
        doc={'sha':source_sha256,'version_id':state['version_id'],
             'asset_id':state['asset_id'],'filters':state['query'].get('filters',[]),
             'expressions':state.get('expressions',[]),'window':state.get('window'),
             'join':state.get('join'),'bucket':state.get('bucket')}
        return hashlib.sha256(json.dumps(doc,sort_keys=True,ensure_ascii=False,default=str).encode()).hexdigest()

    def compute(self,pid,workspace,*,field_ids,method='pearson',null_policy='pairwise'):
        if method!='pearson' or null_policy!='pairwise':raise ValueError('仅实现 Pearson 与成对非空值法')
        if not 2<=len(field_ids)<=40 or len(set(field_ids))!=len(field_ids):
            raise ValueError('相关矩阵需要 2—40 个不同数值列')
        state=workspace['state'];meta=self.engine.checked(pid,state['asset_id'],state['version_id'])
        if state.get('join') or state.get('bucket') or state.get('industrial'):
            raise ValueError('当前仅支持原表行级筛选的相关分析')
        types={c['name']:str(c.get('physical_type','')).upper() for c in meta['columns']}
        allowed={n for n,t in types.items() if any(x in t for x in ('DOUBLE','FLOAT','DECIMAL','INT','REAL'))}
        if not set(field_ids)<=allowed:raise ValueError('字段不在已验证的数值列清单')
        start=time.monotonic();data={}
        # Each chunk evaluates <=12 pairs, avoids megabyte-long SQL with 40 columns.
        pairs=[(i,j) for i in range(len(field_ids)) for j in range(i,len(field_ids))]
        with self.engine.native._connection() as con:
            source,columns=self.engine._source(con,pid,state)
            spec=QuerySpec(asset_id=state['asset_id'],version_id=state['version_id'],
                columns=field_ids,filters=state['query'].get('filters',[]),limit=1)
            safe_sql,values,_=compile_query(spec,columns,source_sql=source)
            # Strip only the compiler-produced pagination. Its WHERE literals
            # remain prepared values and never enter the raw SQL string.
            filtered=safe_sql.rsplit(' ORDER BY ',1)[0]
            q=quote_identifier
            cast=[f'CASE WHEN ISFINITE(TRY_CAST({q(f)} AS DOUBLE)) THEN TRY_CAST({q(f)} AS DOUBLE) END AS {q("col_"+str(i))}'
                  for i,f in enumerate(field_ids)]
            body=f'WITH typed AS (SELECT {", ".join(cast)} FROM ({filtered}) src) '
            # Bound total per query. If the DB cannot finish, no partial matrix is exposed.
            for chunk_start in range(0,len(pairs),12):
                chunk=pairs[chunk_start:chunk_start+12]
                terms=[]
                for i,j in chunk:
                    x=q('col_'+str(i));y=q('col_'+str(j))
                    terms.extend((f'COUNT(*) FILTER (WHERE {x} IS NOT NULL AND {y} IS NOT NULL)',
                                  f'CORR({x},{y})'))
                sql=body+'SELECT '+', '.join(terms)+' FROM typed'
                timeout=60
                timer=threading.Timer(timeout,con.interrupt);timer.daemon=True;timer.start()
                try:row=con.execute(sql,values).fetchone()
                except Exception as exc:raise NativeBIError('Pearson 统计失败或超时，请减少字段或检查 DuckDB') from exc
                finally:timer.cancel()
                for k,pair in enumerate(chunk):data[pair]=(int(row[2*k] or 0),row[2*k+1])
        cells=[]
        for i,x in enumerate(field_ids):
            for j,y in enumerate(field_ids):
                n,raw=data[(i,j) if i<=j else (j,i)]
                value=float(raw) if raw is not None and math.isfinite(float(raw)) else None
                status='insufficient' if n<3 else 'undefined' if value is None else 'small_sample' if n<30 else 'ok'
                cells.append({'field_x':x,'field_y':y,'coefficient':value if n>=3 else None,
                              'n_pair':n,'status':status})
        return {'field_ids':field_ids,'method':method,'null_policy':null_policy,'matrix':cells,
                'dimension':len(field_ids),'source_sha256':workspace['source_sha256'],
                'selection_fingerprint':self.selection_fingerprint(state,workspace['source_sha256']),
                'scan_scope':'all_filtered_rows','elapsed_ms':round((time.monotonic()-start)*1000,2),
                'warnings':['n_pair 小于 30 的单元格样本较少；无定义/常数列不会输出虚构系数']}
