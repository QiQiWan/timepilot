"""Bounded workbook structure analysis; no computation delegated to an LLM.

Previews are explicitly SAMPLED. Real row counts, quality rates and model inputs
are only supplied by the existing immutable transformation + full BI index.
"""
from __future__ import annotations

import hashlib
import json
import os
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path

from timepilot.smart_import import SmartImportService, _hash_file, _write_json

INSPECTOR_VERSION='1.1'


def _type(value):
    if value is None or str(value).strip()=='':return 'empty'
    if isinstance(value,bool):return 'bool'
    if isinstance(value,(int,float)):return 'number'
    if hasattr(value,'isoformat'):return 'date'
    if isinstance(value,str) and value.startswith('='):return 'formula'
    return 'text'


class WorkbookIntelligence:
    def __init__(self,imports:SmartImportService):
        self.imports=imports

    def inspect(self, import_id:str) -> dict:
        m=self.imports.manifest(import_id)
        source=self.imports._dir(import_id)
        cache=source/'profile'/'workbook_structure_v11.json'
        if cache.is_file():
            existing=json.loads(cache.read_text(encoding='utf-8'))
            if existing.get('source_sha256')==m['source_sha256'] and existing.get('inspector_version')==INSPECTOR_VERSION:
                # Cache never replaces checking immutable source fingerprints.
                self._verify(m,source)
                return existing
        self._verify(m,source)
        tables=[];file_reports=[]
        by_file={}
        for c in m['candidates']:
            by_file.setdefault(c['file_id'],[]).append(c)
            sample=c.get('sample_rows',[])[:8]
            per_col=[]
            for i,col in enumerate(c['columns'][:128]):
                values=[r[i] if i<len(r) else None for r in sample]
                types=Counter(_type(v) for v in values)
                per_col.append({'name':col,'types':dict(types),'sample_count':len(values),
                                'missing_sample':types.get('empty',0),'scope':'sample'})
            tables.append({'candidate_id':c['candidate_id'],'file_id':c['file_id'],
                           'sheet':c['sheet'],'kind':c['kind'],
                           'header_row':c.get('header_row'),'rows_estimate':c.get('rows_estimate'),
                           'field_profiles':per_col,'scope':'sample','suggested':c.get('suggested',False)})
        for f in m['files']:
            entry={'file_id':f['file_id'],'filename':f['filename'],'format':Path(f['filename']).suffix.lower(),
                   'bytes':f['bytes'],'sha256':f['sha256'],'sheets':[]}
            if entry['format']=='.xlsx':
                entry['sheets']=self._inspect_xlsx(source/f['stored'],by_file.get(f['file_id'],[]))
            file_reports.append(entry)
        result={'inspector_version':INSPECTOR_VERSION,'import_id':import_id,
                'source_sha256':m['source_sha256'],'scope':'structure_and_sample',
                'full_statistics_ready':bool(m.get('versions')),
                'files':file_reports,'candidate_tables':tables,
                'notices':['抽样字段统计不能作为全量缺失率或业务准确率',
                           '合并表头、重复标题、公式无缓存时应人工复核',
                           '原始 Excel 公式不在 TimePilot 中执行']}
        _write_json(cache,result)
        return result

    def profile_region(self, import_id: str, candidate_id: str, *, max_rows: int = 100000) -> dict:
        """Stream a candidate and retain a bounded, exact-or-explicitly-partial profile.

        Source row values are never returned. Numeric statistics are calculated
        locally; every count carries its scan scope and original file SHA.
        """
        if not 1 <= max_rows <= 200000:
            raise ValueError('全量画像行数上限须在 1—200000 之间')
        manifest = self.imports.manifest(import_id)
        source = self.imports._dir(import_id)
        self._verify(manifest, source)
        candidate = self.imports._candidate(manifest, candidate_id)
        cache = source/'profile'/f'candidate_{candidate_id}_limit_{max_rows}_v21.json'
        if cache.is_file():
            old=json.loads(cache.read_text(encoding='utf-8'))
            if old.get('source_sha256') == manifest['source_sha256'] and old.get('profiler_version') == '2.1':
                return old
        columns = candidate['columns'][:256]
        stats=[{'name': col, 'non_empty':0, 'missing':0, 'numeric':0, 'date':0,
                'text':0, 'min':None,'max':None, 'values':Counter(), 'distinct_seen':set()}
               for col in columns]
        read = 0
        truncated = False
        for row_num,row in self.imports._rows(source,manifest,candidate):
            if not any(v is not None and str(v).strip() for v in row[:len(columns)]):
                continue
            if read >= max_rows:
                truncated = True
                break
            read += 1
            for i, col in enumerate(stats):
                value = row[i] if i < len(row) else None
                typ = _type(value)
                if typ=='empty':
                    col['missing']+=1
                    continue
                col['non_empty']+=1
                if typ=='date':
                    col['date']+=1
                else:
                    try:
                        decimal=Decimal(str(value).replace(',','').strip())
                        if not decimal.is_finite():
                            raise InvalidOperation
                        col['numeric']+=1
                        if col['min'] is None or decimal < col['min']:
                            col['min']=decimal
                        if col['max'] is None or decimal > col['max']:
                            col['max']=decimal
                    except (InvalidOperation, ValueError, TypeError):
                        col['text']+=1
                # Approximate high-cardinality distinct values, with explicit
                # upper bound. Never label as exact once the cap is reached.
                label=str(value)[:100]
                if len(col['distinct_seen']) < 1024:
                    col['distinct_seen'].add(label)
                if col['values'].get(label,0) or len(col['values'])<30:
                    col['values'][label]+=1
        from timepilot.data_schema import infer_field
        result=[]
        for col in stats:
            numeric = col['numeric']
            evidence=infer_field(col['name'], [v for v, _ in col['values'].most_common(25)],
                                 source_header_row=candidate['header_row'])
            result.append({**evidence,'name':col['name'],'non_empty':col['non_empty'],
                 'missing':col['missing'], 'missing_fraction':col['missing']/read if read else None,
                 'numeric_count':numeric,'date_count':col['date'],'text_count':col['text'],
                 'numeric_min':str(col['min']) if col['min'] is not None else None,
                 'numeric_max':str(col['max']) if col['max'] is not None else None,
                 'distinct_count_lower_bound':len(col['distinct_seen']),
                 'distinct_count_capped':len(col['distinct_seen'])==1024,
                 'top_values_sample':col['values'].most_common(5)})
        response={'import_id':import_id,'candidate_id':candidate_id,
                  'source_sha256':manifest['source_sha256'],'profiler_version':'2.1',
                  'row_count_scanned':read,'max_rows':max_rows,
                  'scope':'bounded_incomplete' if truncated else 'full_candidate',
                  'truncated':truncated,'fields':result,
                  'notices':['字段唯一值为上限受限的下界；高基数字段不作精确去重',
                             '业务口径、实体身份、真实发布时间仍需确认']}
        _write_json(cache,response)
        return response

    @staticmethod
    def _verify(m,source):
        base=source.resolve()
        for f in m['files']:
            path=(source/f['stored']).resolve()
            if not path.is_relative_to(base) or not path.is_file() or _hash_file(path)!=f['sha256']:
                raise ValueError('上传源文件丢失或哈希不一致，拒绝读取缓存')

    @staticmethod
    def _inspect_xlsx(path:Path,candidates:list[dict]) -> list[dict]:
        from openpyxl import load_workbook
        # Large workbooks are streamed. Complex styles/merge analysis is
        # supplementary for smaller workbooks and may be incomplete otherwise.
        rich=path.stat().st_size<=2*1024*1024
        wb=load_workbook(path,read_only=not rich,data_only=False,keep_links=False)
        output=[]
        try:
            for ws in wb.worksheets:
                known=[c for c in candidates if c['sheet']==ws.title]
                merges=[];hidden_rows=None;hidden_cols=None
                if rich:
                    merges=[str(r) for r in list(ws.merged_cells.ranges)[:25]]
                    hidden_rows=sum(bool(d.hidden) for d in ws.row_dimensions.values())
                    hidden_cols=sum(bool(d.hidden) for d in ws.column_dimensions.values())
                else:
                    ws.reset_dimensions()
                ncols=min(ws.max_column or 80,128)
                formula_count=0;formula_examples=[];repeated_header_rows=[]
                head_candidates=[set(str(x).strip().lower() for x in c['columns'] if x) for c in known]
                # Bounded structural inspection; does not scan millions of rows.
                for ri,row in enumerate(ws.iter_rows(min_row=1,max_row=85,max_col=max(1,ncols),values_only=True),start=1):
                    cells=[str(v).strip().lower() for v in row if v is not None]
                    formula_count+=sum(isinstance(v,str) and v.startswith('=') for v in row)
                    if len(formula_examples)<3:
                        formula_examples.extend(str(v)[:70] for v in row if isinstance(v,str) and v.startswith('='))
                        formula_examples=formula_examples[:3]
                    if len(cells)>=3:
                        for headers in head_candidates:
                            matches=len(set(cells)&headers)
                            if headers and matches>=3 and matches/max(1,len(headers))>=.65:
                                if not any(ri==c.get('header_row') for c in known):repeated_header_rows.append(ri)
                                break
                output.append({'name':ws.title,'candidate_regions':len(known),
                               'physical_row_hint':ws.max_row,'physical_col_hint':ws.max_column,
                               'hidden':ws.sheet_state!='visible',
                               'merged_ranges_sample':merges,'merged_inspection_complete':rich,
                               'hidden_row_count':hidden_rows,'hidden_col_count':hidden_cols,
                               'formula_count_first_85_rows':formula_count,
                               'formula_examples_first_3':formula_examples,
                               'repeated_header_rows_first_85':sorted(set(repeated_header_rows)),
                               'review_required':bool(merges or repeated_header_rows or (formula_count and not known)),
                               'scope':'structural_first_85_rows'})
        finally:
            wb.close()
        return output
