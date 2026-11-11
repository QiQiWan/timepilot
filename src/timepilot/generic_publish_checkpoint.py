"""Auditable resumable publish for immutable CSV assets.

Only fully fsync'ed, SHA-verified chunks can be reused. The final manifest is
committed last: a failed or cancelled conversion never appears as published.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
from pathlib import Path

from timepilot.smart_import import _json_cell, _write_json, _hash_file, SmartImportError

CHUNK_ROWS = 20_000


def _verify_parts(root: Path, chunks: list[dict], *, columns: list[str]) -> list[dict]:
    """Recover only the verified prefix. Never trust an uncommitted trailing part."""
    correct=[]
    for index,part in enumerate(chunks):
        path=root / f'{index:06d}.csv'
        if (part.get('index') != index or not path.is_file() or
            part.get('sha256')!=_hash_file(path) or
            len(part.get('nonempty', [])) != len(columns) or
            not isinstance(part.get('rows'),int) or part['rows']<=0 or
            part['rows']>CHUNK_ROWS):
            break
        correct.append(part)
    return correct


def materialize(*, target: Path, identity_hash: str, source_sha256: str,
                columns: list[str], rows, max_rows: int) -> dict:
    """The rows argument is a restartable iterator factory of (source_line, values)."""
    target=Path(target)
    stage=target/'.chunks'
    stage.mkdir(parents=True,exist_ok=True)
    checkpoint=stage/'checkpoint.json'
    stable={'format':1,'identity_hash':identity_hash,'source_sha256':source_sha256,
            'columns':columns,'chunk_rows':CHUNK_ROWS}
    if checkpoint.exists():
        try:
            saved=json.loads(checkpoint.read_text(encoding='utf-8'))
        except (OSError,ValueError) as exc:
            raise SmartImportError('发布检查点损坏，请人工核实后重试') from exc
        if any(saved.get(k)!=v for k,v in stable.items()):
            raise SmartImportError('发布检查点与当前源文件或转换规则不一致，拒绝混用')
        old_chunks=saved.get('chunks',[])
        if not isinstance(old_chunks,list):
            raise SmartImportError('发布检查点格式错误')
        chunks=_verify_parts(stage,old_chunks,columns=columns)
        if len(chunks)!=len(old_chunks):
            _write_json(checkpoint,{**stable,'chunks':chunks})
    else:
        chunks=[]
        _write_json(checkpoint,{**stable,'chunks':[]})
    # A crash after writing a part but before committing the checkpoint must
    # never reuse that part. It will be re-generated from the verified source.
    offset=sum(x['rows'] for x in chunks)
    total=0
    sample=[]
    nonempty=[0]*len(columns)
    for item in chunks:
        for i,num in enumerate(item['nonempty']):nonempty[i]+=num
        if len(sample)<120:sample.extend(item.get('sample',[])[:120-len(sample)])
    index=len(chunks)
    temp=None
    file=None
    writer=None
    chunk_rows=0
    chunk_nonempty=[0]*len(columns)
    chunk_sample=[]

    def begin():
        nonlocal temp,file,writer,chunk_rows,chunk_nonempty,chunk_sample
        temp=stage / f'{index:06d}.incomplete'
        file=temp.open('w',encoding='utf-8',newline='')
        writer=csv.writer(file)
        chunk_rows=0
        chunk_nonempty=[0]*len(columns)
        chunk_sample=[]

    def commit():
        nonlocal index,temp,file,writer
        if file is None:return
        file.flush();os.fsync(file.fileno());file.close();file=None;writer=None
        final=stage / f'{index:06d}.csv'
        os.replace(temp,final)
        entry={'index':index,'rows':chunk_rows,'nonempty':chunk_nonempty,
               'sample':chunk_sample,'sha256':_hash_file(final),
               'bytes':final.stat().st_size}
        chunks.append(entry)
        _write_json(checkpoint,{**stable,'chunks':chunks})
        index+=1
        temp=None

    try:
        for source_line,row in rows():
            if not any(v is not None and str(v).strip() for v in row):continue
            if len(row)>len(columns) and any(v is not None and str(v).strip() for v in row[len(columns):]):
                raise SmartImportError(
                    f'原始第 {source_line} 行包含 {len(row)} 列，表头只识别 {len(columns)} 列；不能静默舍弃源字段')
            total+=1
            if total>max_rows:
                raise SmartImportError('超过通用数据发布上限，请分区发布')
            if total<=offset:continue
            if file is None:begin()
            values=[_json_cell(row[i]) if i<len(row) else None for i in range(len(columns))]
            writer.writerow(['' if v is None else v for v in values])
            chunk_rows+=1
            for j,value in enumerate(values):
                if value is not None and str(value).strip():
                    nonempty[j]+=1
                    chunk_nonempty[j]+=1
            if len(sample)<120:sample.append(values)
            if len(chunk_sample)<120:chunk_sample.append(values)
            if chunk_rows==CHUNK_ROWS:commit()
        commit()
    finally:
        if file is not None:file.close()
        if temp is not None:temp.unlink(missing_ok=True)
    if total!=sum(x['rows'] for x in chunks):
        raise SmartImportError('发布过程行数不一致，拒绝完成')
    if not total:raise SmartImportError('选定区域没有可发布的数据行')
    output=target/'data.csv'
    assembling=target/'data.assembling.csv'
    with assembling.open('wb') as result:
        header=','.join('"'+str(c).replace('"','""')+'"' for c in columns)+'\r\n'
        result.write(header.encode('utf-8'))
        for chunk in chunks:
            piece=stage/f'{chunk["index"]:06d}.csv'
            if not piece.is_file() or _hash_file(piece)!=chunk['sha256']:
                raise SmartImportError('发布完成前的分块文件完整性校验失败')
            with piece.open('rb') as stream:shutil.copyfileobj(stream,result,length=1024*1024)
        result.flush();os.fsync(result.fileno())
    os.replace(assembling,output)
    return {'file':output,'row_count':total,'sample':sample[:120],
            'nonempty_counts':nonempty,'sha256':_hash_file(output),
            'verified_chunks':len(chunks),'stage':stage}


def cleanup(committed: dict):
    """Only after the caller atomically writes the final published manifest."""
    shutil.rmtree(committed['stage'],ignore_errors=True)


def checkpoint_status(target: Path) -> dict:
    checkpoint=Path(target)/'.chunks'/'checkpoint.json'
    if not checkpoint.is_file():return {'status':'none','completed_chunks':0}
    try:
        record=json.loads(checkpoint.read_text(encoding='utf-8'))
        return {'status':'recoverable','completed_chunks':len(record.get('chunks',[])),
                'completed_rows':sum(x['rows'] for x in record.get('chunks',[])),
                'source_sha256':record.get('source_sha256')}
    except (OSError,ValueError,TypeError,KeyError):
        return {'status':'damaged','completed_chunks':0}
