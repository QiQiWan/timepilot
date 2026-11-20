"""Read/download a bounded, filtered server log extract (admin-only in enterprise)."""
from __future__ import annotations

import json
import io
import zipfile
import hashlib
from datetime import datetime,timezone
from pathlib import Path
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from timepilot.system_logs import read_logs, dashboard_performance_summary


def create_logs_router(home: Path) -> APIRouter:
    router = APIRouter(prefix='/api/v1/system/logs', tags=['diagnostics'])

    def lookup(level: str = '', source: str = '', q: str = '', since: str = '',
               limit: int = Query(default=100, ge=1, le=500),
               offset: int = Query(default=0, ge=0, le=5000)):
        try:
            return read_logs(home, level=level, source=source, keyword=q,
                             since=since, limit=limit, offset=offset)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.get('')
    def listing(level: str = '', source: str = '', q: str = '', since: str = '',
                limit: int = Query(default=100, ge=1, le=500),
                offset: int = Query(default=0, ge=0, le=5000)):
        return lookup(level, source, q, since, limit, offset)

    @router.get('/summary')
    def summary():
        return dashboard_performance_summary(home)

    @router.get('/download')
    def download(level: str = '', source: str = '', q: str = '', since: str = '',
                 format: str = 'jsonl'):
        if format not in {'jsonl', 'txt','zip'}:
            raise HTTPException(status_code=422, detail='只支持 jsonl、txt 或 zip')
        result = lookup(level, source, q, since, 500, 0)
        if format=='zip':
            payload=''.join(json.dumps(x,ensure_ascii=False,sort_keys=True)+'\n' for x in result['items'])
            raw=payload.encode('utf-8')
            summary={'generated_at':datetime.now(timezone.utc).isoformat(),
                'matched':result['matched'],'exported':len(result['items']),
                'truncated':result['matched']>len(result['items']),
                'error_groups':result.get('error_groups',[]),
                'jsonl_sha256':hashlib.sha256(raw).hexdigest(),
                'privacy':'safe stack frames only; no raw data, SQL or secrets'}
            buffer=io.BytesIO()
            with zipfile.ZipFile(buffer,'w',zipfile.ZIP_DEFLATED) as z:
                z.writestr('diagnostic_events.jsonl',raw)
                z.writestr('summary.json',json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
            return Response(content=buffer.getvalue(),media_type='application/zip',headers={
                'Cache-Control':'no-store','X-Content-Type-Options':'nosniff',
                'Content-Disposition':'attachment; filename="timepilot-diagnostics.zip"',
                'X-TimePilot-Export-Truncated':str(summary['truncated']).lower()})
        if format == 'jsonl':
            text = ''.join(json.dumps(x, ensure_ascii=False) + '\n' for x in result['items'])
        else:
            text = ''.join(f"{x['time']} [{x['level']}] {x['source']}: {x['message']}\n"
                + (f"  诊断: {x['diagnostic_id']} | 阶段: {x.get('operation', '')} | 类别: {x.get('error_code', '')}\n" if x.get('diagnostic_id') else '')
                + ''.join(f"    {f['file']}:{f['line']} {f['function']}\n" for f in x.get('stack', []) if isinstance(f,dict))
                for x in result['items'])
        return Response(content=text, media_type='text/plain; charset=utf-8', headers={
            'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
            'Content-Disposition': f'attachment; filename="timepilot-logs.{format}"',
            'X-TimePilot-Export-Truncated': str(result['matched'] > len(result['items'])).lower(),
        })

    return router
