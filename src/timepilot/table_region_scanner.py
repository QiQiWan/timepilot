"""Bounded-memory, source-coordinate-aware discovery of independent table regions.

Discovery is deterministic; it never invents a field's business meaning. Two
streaming passes discover vertical bands and horizontal separation, then
identify local header/data boundaries. Unassigned nonempty regions remain
visible in the import catalog instead of being silently discarded.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Callable, Iterable

SCANNER_VERSION = '2.2.1'
MAX_REGIONS = 500
MAX_SOURCE_COLUMNS = 16_384
MAX_TABLE_COLUMNS = 256


def _filled(value: Any) -> bool:
    return value is not None and not (isinstance(value, str) and not value.strip())


def _kind(value: Any) -> str:
    if not _filled(value): return 'empty'
    if isinstance(value, bool): return 'boolean'
    if isinstance(value, (date, datetime)): return 'datetime'
    if isinstance(value, (int, float)): return 'number'
    if isinstance(value, str):
        text=value.strip()
        if text and len(text)<40:
            try:
                float(text.replace(',', ''))
                return 'numeric_text'
            except ValueError:
                pass
        return 'text'
    return 'text'


def _letter(index: int) -> str:
    """One-based Excel column index without depending on openpyxl."""
    result=''
    while index:
        index, remainder=divmod(index-1,26)
        result=chr(65+remainder)+result
    return result


def _serializable(value):
    if isinstance(value, (date, datetime)): return value.isoformat()
    if isinstance(value, (int,float,str,bool)) or value is None: return value
    return str(value)


@dataclass
class _Band:
    first: int
    last: int
    col_count: Counter = field(default_factory=Counter)


@dataclass
class _Run:
    first: int
    last: int
    left: int
    right: int
    examples: list = field(default_factory=list)
    nonempty_rows: int = 0


def _columns(values: list[Any], left: int, header_row: int | None, kind: str) -> tuple[list[str],list[dict]]:
    """Separate authentic source_name from unique query alias; never rename raw."""
    aliases=[]; raw_fields=[]; used=set()
    for idx, raw in enumerate(values):
        source='' if not _filled(raw) else str(raw)
        base=source if source and len(source)<=256 else f'_field_{idx+1}'
        alias=base
        suffix=2
        while alias in used:
            alias=f'{base}__{suffix}'
            suffix+=1
        used.add(alias)
        cell=f'{_letter(left+idx)}{header_row}' if header_row is not None and kind!='Parquet' else None
        aliases.append(alias)
        raw_fields.append({'ordinal':idx,'source_name':source,'internal_alias':alias,
                           'source_cell':cell,'missing_header':not bool(source),
                           'duplicate_source_name':False})
    counts=Counter(x['source_name'] for x in raw_fields if x['source_name'])
    for item in raw_fields:
        item['duplicate_source_name']=bool(item['source_name'] and counts[item['source_name']]>1)
    return aliases,raw_fields


def _header(examples: list[tuple[int,list[Any]]], left: int, right: int) -> tuple[int|None, int, list[Any],list[dict],list[dict]]:
    """Choose a local label row, preserving numeric-only matrices as headerless."""
    first=examples[0][0]
    width=right-left+1
    best_score=-1
    best_idx=None
    # Bound search to the first 16 occupied rows; no business-name heuristics.
    for i,(row_idx, row) in enumerate(examples[:16]):
        cells=row[left-1:right]
        if len(cells)<width:cells=cells+[None]*(width-len(cells))
        occupied=[v for v in cells if _filled(v)]
        if not occupied:continue
        text=sum(_kind(x)=='text' for x in occupied)
        if text==0:continue
        # A singleton note or title is never a table header when the region
        # has several columns and a fuller following row.
        fullness=len(occupied)/width
        distinct=len(set(str(v) for v in occupied))
        next_values=examples[i+1][1][left-1:right] if i+1<len(examples) else []
        next_filled=[v for v in next_values if _filled(v)]
        next_numeric=sum(_kind(v) in ('number','numeric_text','datetime') for v in next_filled)
        current_numeric=sum(_kind(v) in ('number','numeric_text') for v in occupied)
        score=(text*4 + min(distinct,12) + 3*fullness +
               (min(next_numeric,10)*1.3 if next_filled else 0) -
               current_numeric*.8 - i*.6)
        if width>2 and len(occupied)<2:score-=20
        if score>best_score:
            best_score=score;best_idx=i
    # A single string in one-column file is still a valid header.
    if best_idx is None or best_score<2:
        headers=[None]*width
        data_first=first
        header_row=None
        extra=[]
    else:
        header_row=examples[best_idx][0]
        values=examples[best_idx][1][left-1:right]
        headers=(values+[None]*width)[:width]
        data_first=header_row+1
        extra=[{'row':r,'kind':'leading_annotation','nonempty_cells':sum(_filled(x) for x in a[left-1:right])}
               for r,a in examples[:best_idx]]
    aliases,fields=_columns(headers,left,header_row,'Excel')
    return header_row,data_first,aliases,fields,extra


def _ranges(counts: Counter) -> list[tuple[int,int]]:
    segments=[];begin=last=None
    for col in sorted(counts):
        if begin is None:begin=last=col
        elif col==last+1:last=col
        else:segments.append((begin,last));begin=last=col
    if begin is not None:segments.append((begin,last))
    return segments


def discover_regions(rows: Callable[[], Iterable[tuple[int,list[Any]]]], *,
                     file_id: str, logical_file: str, sheet: str,
                     max_table_columns: int = MAX_TABLE_COLUMNS,
                     max_regions: int = MAX_REGIONS) -> dict:
    """Discover horizontal/vertical blocks using two sequential reads.

    Args: rows is a restartable iterator factory yielding (1-based row number,
    source row values). Space scales with bands/column occupancy and at most 20
    sample rows per region, never with the number of source records.
    """
    bands=[];open_band=None;max_col=0
    for row_no,values in rows():
        occupied=[i+1 for i,v in enumerate(values) if _filled(v)]
        if occupied:
            if len(values)>MAX_SOURCE_COLUMNS or occupied[-1]>MAX_SOURCE_COLUMNS:
                raise ValueError('表格超过来源列坐标安全上限，拒绝截断')
            max_col=max(max_col,occupied[-1])
            if open_band is None:open_band=_Band(row_no,row_no)
            open_band.last=row_no
            open_band.col_count.update(occupied)
        elif open_band is not None:
            bands.append(open_band);open_band=None
            if len(bands)>max_regions*8:
                raise ValueError('来源包含过多相互分隔的数据块，已停止扫描以限制内存；请拆分来源文件')
    if open_band is not None:bands.append(open_band)
    if len(bands)>max_regions*8:
        raise ValueError('来源包含过多相互分隔的数据块，已停止扫描以限制内存；请拆分来源文件')
    layouts=[]
    for band in bands:
        for left,right in _ranges(band.col_count):
            if right-left+1>max_table_columns:
                raise ValueError(f'{sheet} { _letter(left) }:{ _letter(right) } 为 {right-left+1} 列，超过每区域 {max_table_columns} 列上限；未截断')
            layouts.append({'band':band,'left':left,'right':right,'run':None})
    if len(layouts)>max_regions:
        raise ValueError(f'检测到 {len(layouts)} 个候选区域，超过安全上限；未截断')
    regions=[];unassigned=[]

    def finish(layout):
        run=layout['run']
        if run is None:return
        layout['run']=None
        # Single-cell annotations must be reported, never silently dropped.
        if run.nonempty_rows==1 and run.right>run.left and sum(_filled(v) for v in run.examples[0][1][run.left-1:run.right])==1:
            unassigned.append({'sheet':sheet,'bbox':{'row_start':run.first,'row_end':run.last,
                   'col_start':run.left,'col_end':run.right},'kind':'annotation_or_ambiguous',
                   'reason':'独立的单行注释或表标题，未作为数据表发布'})
            return
        header_row,data_start,aliases,fields,notes=_header(run.examples,run.left,run.right)
        if run.nonempty_rows==1 and header_row is not None:
            # One-row header-only table has no data. Keep visible as unresolved.
            unassigned.append({'sheet':sheet,'bbox':{'row_start':run.first,'row_end':run.last,
                 'col_start':run.left,'col_end':run.right},'kind':'header_only',
                 'reason':'只有表头，没有可发布记录'})
            return
        if data_start>run.last:
            unassigned.append({'sheet':sheet,'bbox':{'row_start':run.first,'row_end':run.last,
                 'col_start':run.left,'col_end':run.right},'kind':'no_data',
                 'reason':'区域没有有效数据行'})
            return
        bounds={'row_start':run.first,'row_end':run.last,'col_start':run.left,'col_end':run.right}
        payload={'file_id':file_id,'sheet':sheet,'bbox':bounds,'header_row':header_row,
                 'data_start_row':data_start,'scanner':SCANNER_VERSION}
        ident=hashlib.sha256(json.dumps(payload,ensure_ascii=False,sort_keys=True).encode()).hexdigest()[:20]
        examples=[]
        for row_no,row in run.examples:
            if row_no<data_start:continue
            examples.append([_serializable(x) for x in (row[run.left-1:run.right]+[None]*(run.right-run.left+1))[:run.right-run.left+1]])
            if len(examples)>=6:break
        raw_header_names=[x['source_name'] for x in fields]
        regions.append({'candidate_id':ident,'region_id':ident,'file_id':file_id,
             'logical_file':logical_file,'sheet':sheet,'kind':'generic_table',
             'bbox':bounds,'start_col':run.left,'end_col':run.right,
             'row_start':run.first,'row_end':run.last,
             'header_row':header_row,'header_rows':[header_row] if header_row else [],
             'data_start_row':data_start,'columns':aliases,'source_columns':raw_header_names,
             'source_fields':fields,'rows_estimate':max(0,run.last-data_start+1),
             'sample_rows':examples,'suggested':True,'header_confidence':'review' if notes or any(f['missing_header'] for f in fields) else 'structural',
             'annotations':notes,'scope':'structure_sample', 'scanner_version':SCANNER_VERSION,
             'needs_review':bool(notes or any(f['missing_header'] for f in fields))})

    # Streaming second pass: each band only sees rows in its own range.
    layouts.sort(key=lambda x:(x['band'].first,x['left']))
    positions=0
    for row_no,values in rows():
        while positions<len(layouts) and row_no>layouts[positions]['band'].last:
            # There can be concurrent layouts with the same band. A completed
            # layout is closed once its band ends.
            finish(layouts[positions]);positions+=1
        if positions==len(layouts):break
        # Iterate all layouts whose vertical range includes the row. A typical
        # source has only one layout; wide parallel tables are bounded by 256.
        for entry in layouts[positions:]:
            band=entry['band']
            if band.first>row_no:break
            if row_no>band.last:continue
            left,right=entry['left'],entry['right']
            active=any(_filled(v) for v in values[left-1:right])
            run=entry['run']
            if not active:
                finish(entry)
                continue
            if run is not None and run.nonempty_rows>=3 and run.examples:
                # A second, exact source header without a separator indicates a
                # new section. Compare only the original header text; avoid
                # business-name guesses or implicit aggregation.
                header=run.examples[0][1][left-1:right]
                current=values[left-1:right]
                if (len(current)==len(header) and all(_filled(v) for v in header)
                        and all(_kind(v)=='text' for v in header)
                        and [str(x) for x in current]==[str(x) for x in header]):
                    finish(entry)
                    run=None
            if run is None:
                run=_Run(row_no,row_no,left,right)
                entry['run']=run
            run.last=row_no;run.nonempty_rows+=1
            if len(run.examples)<20:run.examples.append((row_no,list(values[:right])))
    for entry in layouts[positions:]:finish(entry)
    regions.sort(key=lambda x:(x['file_id'],x['sheet'],x['row_start'],x['start_col']))
    if len(regions)>max_regions:
        raise ValueError('发现的数据子表超过安全上限，请按文件分批导入')
    return {'regions':regions,'unassigned_regions':unassigned,
            'sheet':sheet,'bands':len(bands),'max_column':max_col,
            'parser_version':SCANNER_VERSION}
