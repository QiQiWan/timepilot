"""Bounded, redacted server diagnostics. Never inspect source files or secrets.

The log reader only opens fixed rotation filenames in TIMEPILOT_HOME/logs;
enterprise access is restricted by EnterpriseGuard to tenant administrators.
"""
from __future__ import annotations

from collections import deque
from contextvars import ContextVar
import time
import uuid
import traceback
from datetime import datetime, timezone
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import re
import threading
import math

_LOG_NAME = 'timepilot.log'
_LOCK = threading.Lock()
_HANDLER = None
_HOME = None
REQUEST_ID = ContextVar('timepilot_request_id', default='')
_FAILURE_LOCK = threading.Lock()
_FAILURE_WINDOW = {}


def classify_error(error: Exception) -> str:
    """Small, data-free error category intended for UI and service logs."""
    name=type(error).__name__.lower()
    message=str(error).lower()
    if 'date_trunc' in message or ('time' in message and 'binder' in name):
        return 'time_dimension_incompatible'
    if any(x in message for x in ('out of memory','memory limit','failed to allocate')):
        return 'resource_budget_exceeded'
    if 'conversion' in name or 'could not convert' in message:
        return 'field_conversion_failed'
    if 'catalog' in name or 'table does not exist' in message:
        return 'source_view_unavailable'
    if 'binder' in name:
        return 'sql_field_binding_failed'
    if 'parser' in name:
        return 'sql_expression_invalid'
    if 'io' in name or 'permission denied' in message:
        return 'source_read_failed'
    return 'unclassified_execution_error'


def log_failure(error: Exception, *, stage: str, context: dict | None = None) -> str:
    """Write one compact, safe event per occurrence and one stack per incident.

    The increment is durable in JSONL, so 3 identical failures are counted as
    3 even when a power-of-two checkpoint would have been suppressed.
    """
    frames=traceback.extract_tb(error.__traceback__)[-12:]
    signature='|'.join(f'{Path(x.filename).name}:{x.lineno}:{x.name}' for x in frames[-5:])
    import hashlib
    ctx=context if isinstance(context,dict) else {}
    template=str(ctx.get('sql_template_id') or 'unknown')[:60]
    kind=str(ctx.get('kind') or 'unknown')[:40]
    code=classify_error(error)
    fingerprint=hashlib.sha256(f'{stage}|{kind}|{template}|{code}|{type(error).__name__}|{signature}'.encode()).hexdigest()[:20]
    now=time.monotonic()
    with _FAILURE_LOCK:
        record=_FAILURE_WINDOW.get(fingerprint)
        if record and now-record[0]<60:
            _,diagnostic,count=record
            count+=1
            _FAILURE_WINDOW[fingerprint]=(now,diagnostic,count)
        else:
            diagnostic='TP-'+uuid.uuid4().hex[:10].upper()
            count=1
            _FAILURE_WINDOW[fingerprint]=(now,diagnostic,count)
        if len(_FAILURE_WINDOW)>512:
            for k in list(_FAILURE_WINDOW)[:128]:
                _FAILURE_WINDOW.pop(k,None)
    meta={'diagnostic_id':diagnostic,'request_id':REQUEST_ID.get(),
          'operation':stage,'repeat_count':count,'error_code':code,
          'chart_kind':kind,'sql_template_id':template,'error_fingerprint':fingerprint,
          'event_kind':'incident_open' if count==1 else 'incident_occurrence',
          'occurrence_increment':1}
    logger=logging.getLogger('timepilot.dashboard')
    logger.error('图表计算失败：诊断 %s，阶段 %s，异常 %s（累计 %d 次）',
                 diagnostic,stage,type(error).__name__,count,
                 exc_info=(type(error),error,error.__traceback__) if count==1 else None,extra=meta)
    return diagnostic
_SECRETS = re.compile(r"(?i)(\b(?:password|api[_-]?key|access[_-]?token|authorization|secret|csrf|session)\b[\"']?\s*[=:]\s*[\"']?)([^\s,;}\"']+)")
_AUTH = re.compile(r"(?i)(authorization[\"']?\s*[=:]\s*[\"']?)(?:Bearer\s+)?[^\s,;}\"']+")


def redact(value: str) -> str:
    text = str(value).replace('\x00', '')
    text = _AUTH.sub(lambda m: m.group(1)+'[REDACTED]', text)
    text = _SECRETS.sub(lambda m: m.group(1)+'[REDACTED]', text)
    return text[:3200]


class JsonLineFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        message = redact(record.getMessage())
        item = {
            'time': datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec='milliseconds'),
            'level': record.levelname,
            'source': record.name[:100],
            'message': message,
        }
        rid = getattr(record, 'request_id', None)
        if rid:
            item['request_id'] = str(rid)[:64]
        for key in ('diagnostic_id','operation','error_code','repeat_count','chart_kind','chart_field_count','chart_filtered','chart_version','sql_template_id','error_fingerprint','event_kind','occurrence_increment','duration_ms','success'):
            val=getattr(record,key,None)
            if val is not None:item[key]=redact(str(val))[:100]
        # Store only code locations; never serialize exception text, variables,
        # SQL, full local file paths, request payload or raw traceback.
        if record.exc_info:
            item['error_type'] = record.exc_info[0].__name__
            trace=traceback.extract_tb(record.exc_info[2])[-12:]
            item['stack']=[{'file':Path(f.filename).name,'line':f.lineno,'function':f.name[:90]} for f in trace]
        return json.dumps(item, ensure_ascii=False, separators=(',', ':'))


def setup_logging(home: Path) -> Path:
    """Configure TimePilot and worker loggers once per active home; no global root mutation."""
    global _HANDLER, _HOME
    directory = Path(home).resolve() / 'logs'
    directory.mkdir(parents=True, exist_ok=True)
    try:
        directory.chmod(0o700)
    except OSError:
        pass
    with _LOCK:
        logger = logging.getLogger('timepilot')
        if _HANDLER is not None and _HOME == directory:
            return directory
        if _HANDLER is not None:
            logger.removeHandler(_HANDLER)
            _HANDLER.close()
        handler = RotatingFileHandler(directory / _LOG_NAME, maxBytes=5 * 1024 * 1024,
                                      backupCount=5, encoding='utf-8', delay=True)
        handler.setFormatter(JsonLineFormatter())
        handler.setLevel(logging.INFO)
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        _HOME, _HANDLER = directory, handler
    return directory


def _files(directory: Path) -> list[Path]:
    # Avoid arbitrary filename input/path traversal; existing regular files only.
    return [p for p in [directory / _LOG_NAME, *(directory / f'{_LOG_NAME}.{n}' for n in range(1, 6))]
            if p.is_file() and not p.is_symlink()]


def read_logs(home: Path, *, level: str = '', source: str = '', keyword: str = '',
              limit: int = 100, offset: int = 0, since: str = '') -> dict:
    if not 1 <= limit <= 500 or not 0 <= offset <= 5000:
        raise ValueError('分页范围不合法')
    level = level.upper().strip()
    if level not in {'', 'DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'}:
        raise ValueError('日志级别无效')
    if any(len(str(x)) > 100 for x in (source, keyword, since)):
        raise ValueError('筛选条件过长')
    if since:
        try:
            start_time = datetime.fromisoformat(since.replace('Z', '+00:00'))
            if start_time.tzinfo is None:
                raise ValueError('时区必须明确')
            since = start_time.astimezone(timezone.utc).isoformat(timespec='milliseconds')
        except ValueError as exc:
            raise ValueError('开始时间需采用 ISO 8601') from exc
    directory = Path(home).resolve() / 'logs'
    # Rotated files go from most recent to oldest; within each file reverse chronology.
    found = []
    groups = {}
    total = 0
    max_scan = 250000
    scanned = 0
    for path in _files(directory):
        with path.open('r', encoding='utf-8', errors='replace') as stream:
            tail = deque(stream, maxlen=50000)
        for line in reversed(tail):
            scanned += 1
            if scanned > max_scan:
                break
            try:
                item = json.loads(line)
            except (TypeError, ValueError):
                continue
            if not isinstance(item, dict) or not {'time', 'level', 'source', 'message'} <= item.keys():
                continue
            if level and item['level'] != level:
                continue
            if source and source.casefold() not in str(item['source']).casefold():
                continue
            search=' '.join(str(item.get(k,'')) for k in ('message','diagnostic_id','request_id','operation','error_type','error_code'))
            if keyword and keyword.casefold() not in search.casefold():
                continue
            if since and str(item['time']) < since:
                continue
            total += 1
            did=item.get('diagnostic_id')
            if did and item.get('level') in ('ERROR','CRITICAL'):
                if did not in groups:
                    groups[did]={'diagnostic_id':did,'count':0,'first_seen':item['time'],
                                 'last_seen':item['time'],'operation':item.get('operation',''),
                                 'error_type':item.get('error_type') or item.get('error_code') or ''}
                group=groups[did]
                group['first_seen']=min(group['first_seen'],item['time'])
                group['last_seen']=max(group['last_seen'],item['time'])
                # v0.37.5+ emits a durable increment for every occurrence.
                # Historical sampled logs retain their greatest checkpoint.
                if 'occurrence_increment' in item:
                    group['count']+=max(0,int(item['occurrence_increment']))
                else:
                    group['legacy_checkpoint']=max(group.get('legacy_checkpoint',0),int(item.get('repeat_count') or 1))
            if total > offset and len(found) < limit:
                found.append(item)
        if scanned > max_scan:
            break
    for group in groups.values():
        group['count']=max(group['count'],group.pop('legacy_checkpoint',0))
    return {'items': found, 'matched': total, 'limit': limit, 'offset': offset,
            'truncated': scanned > max_scan,
            'error_groups':sorted(groups.values(),key=lambda x:x['last_seen'],reverse=True)[:20],
            'directory': 'TIMEPILOT_HOME/logs',
            'files': [{'name': p.name, 'bytes': p.stat().st_size} for p in _files(directory)]}


def log_dashboard_performance(*, operation: str, chart_kind: str, milliseconds: float,
                              success: bool) -> None:
    """Record bounded, data-free SQL latency; never persist query text/row values."""
    if not isinstance(milliseconds, (int, float)) or not math.isfinite(milliseconds):
        return
    logging.getLogger('timepilot.dashboard.perf').info('仪表盘查询耗时', extra={
        'request_id': REQUEST_ID.get(), 'event_kind':'dashboard_performance',
        'operation':str(operation)[:60], 'chart_kind':str(chart_kind)[:40],
        'duration_ms': round(max(0, milliseconds),3),'success':bool(success)})


def dashboard_performance_summary(home: Path, *, max_scan: int = 100000) -> dict:
    """Recent-rotation SLO evidence; explicitly not a lifetime total.

    Includes only metadata, not freeform log messages or request payloads.
    """
    from collections import defaultdict
    if not 100 <= max_scan <= 250000:
        raise ValueError('诊断扫描预算非法')
    directory = Path(home).resolve() / 'logs'
    groups = defaultdict(lambda:{'durations':[],'errors':0,'count':0})
    seen=0;period=[];truncated=False
    for path in _files(directory):
        with path.open('r',encoding='utf-8',errors='replace') as stream:
            for line in deque(stream,maxlen=50000):
                if seen >= max_scan:
                    truncated=True;break
                seen+=1
                try:item=json.loads(line)
                except (TypeError,ValueError):continue
                if not isinstance(item,dict) or item.get('event_kind')!='dashboard_performance':continue
                ms=item.get('duration_ms')
                try:ms=float(ms)
                except (TypeError,ValueError):continue
                if not math.isfinite(ms) or ms<0:continue
                key=(str(item.get('operation','unknown'))[:60],str(item.get('chart_kind','unknown'))[:40])
                grp=groups[key];grp['count']+=1
                grp['errors']+=int(item.get('success') in (False,'False','false'))
                grp['durations'].append(ms)
                if item.get('time'):period.append(str(item['time']))
        if truncated:break
    rows=[]
    for (operation,kind),group in sorted(groups.items()):
        values=sorted(group.pop('durations'))
        def percentile(p):return round(values[min(len(values)-1,max(0,math.ceil(len(values)*p)-1))],3)
        rows.append({'operation':operation,'chart_kind':kind,
            'count':group['count'],'errors':group['errors'],
            'error_rate':round(group['errors']/group['count'],6),
            'p50_ms':percentile(.5),'p95_ms':percentile(.95),
            'max_ms':round(values[-1],3)})
    return {'scope':'retained_rotated_logs_only','scanned_lines':seen,
            'truncated':truncated,'first_observed':min(period) if period else None,
            'last_observed':max(period) if period else None,
            'groups':rows,'events':sum(r['count'] for r in rows)}
