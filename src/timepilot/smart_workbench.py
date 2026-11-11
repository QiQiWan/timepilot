"""v0.7.0: indexed BI selections -> immutable saved views -> reviewed forecast drafts.

The imported CSV remains immutable. SQLite is a disposable, content-addressed
read cache for local interactive analysis; all writes and training are explicit.
No generated SQL, unsafe identifiers or implicit missing-value imputation.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
import re
import shutil
import sqlite3
import threading
from collections import Counter
from datetime import date, timedelta, datetime
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from timepilot.smart_import import SmartImportError, SmartImportService, _write_json

DIMENSIONS = ('country', 'brand', 'vehicle', 'source_name', 'statistical_scope')
FILTER_FIELDS = frozenset((*DIMENSIONS, 'series_id', 'entity_id'))
INDEX_VERSION = 2
MAX_FACETS = 80
MAX_PREVIEW = 60
MAX_SERIES_DISPLAY = 80
MAX_DATASET_ROWS = 200_000


class ViewSelection(BaseModel):
    model_config = ConfigDict(extra='forbid')
    filters: dict[str, list[str]] = Field(default_factory=dict)
    date_from: str | None = None
    date_to: str | None = None

    @model_validator(mode='after')
    def check(self):
        if not set(self.filters).issubset(FILTER_FIELDS):
            raise ValueError('包含不支持的筛选字段')
        if len(self.filters) > len(FILTER_FIELDS):
            raise ValueError('筛选条件数量超过上限')
        for key, values in self.filters.items():
            if len(values) > (200 if key == 'series_id' else 30) or any(not isinstance(v, str) or len(v) > 180 for v in values):
                raise ValueError('筛选值数量或长度超限')
        for v in (self.date_from, self.date_to):
            if v is not None:
                if not re.fullmatch(r'(19|20)\d{2}-(0[1-9]|1[0-2])', v):
                    raise ValueError('时间范围必须使用 YYYY-MM')
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError('起始月份不能晚于结束月份')
        return self

    def canonical(self) -> dict:
        return {'filters': {k: sorted(set(v)) for k, v in sorted(self.filters.items()) if v},
                'date_from': self.date_from, 'date_to': self.date_to}


class SaveView(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=100)
    selection: ViewSelection
    # The official Perspective workspace and the business filter are one
    # immutable, version-pinned saved view, not separate view registries.
    perspective_workspace: dict | None = None
    base_table_name: str = Field(default='timepilot_active', min_length=1, max_length=128)

    @model_validator(mode='after')
    def check_workspace(self):
        if self.perspective_workspace is not None:
            workspace=self.perspective_workspace
            if not isinstance(workspace.get('panels'),dict) or not workspace['panels']:
                raise ValueError('保存 Perspective 视图需要有效的面板配置')
            if len(workspace['panels'])>24 or len(workspace.get('derived_tables') or {})>32:
                raise ValueError('Perspective 视图包含过多面板或派生表')
            if len(json.dumps(workspace,ensure_ascii=False).encode('utf-8'))>65536:
                raise ValueError('Perspective 视图配置超过 64KB')
        return self


class DraftOptions(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(default='月度业务预测', min_length=2, max_length=120)
    forecast_horizon: int = Field(default=3, ge=1, le=24)
    validation_windows: int = Field(default=3, ge=1, le=12)
    min_history: int = Field(default=12, ge=2, le=240)
    model_candidates: list[Literal['seasonal_naive', 'moving_average']] = Field(default_factory=lambda: ['seasonal_naive', 'moving_average'], min_length=1, max_length=2)
    publication_lag_days: int | None = Field(default=None, ge=0, le=3650)
    series_policy: Literal['all', 'eligible_only'] = 'all'


class SmartWorkbench:
    def __init__(self, imports: SmartImportService, store=None, settings=None, engine=None):
        self.imports = imports
        self.store = store
        self.settings = settings
        self.engine = engine
        self._lock = threading.RLock()
        self._index_jobs: dict[tuple[str,str],dict] = {}
        self._index_jobs_lock = threading.Lock()

    def _folder(self, import_id: str, version_id: str) -> Path:
        self.imports.version(import_id, version_id)
        return self.imports._dir(import_id) / 'workbench' / version_id

    @staticmethod
    def _load_id(value: str) -> str:
        try:
            return str(UUID(value))
        except (TypeError, ValueError):
            raise SmartImportError('视图或任务草稿编号无效') from None

    @staticmethod
    def _stable_digest(obj: dict) -> str:
        return hashlib.sha256(json.dumps(obj, ensure_ascii=False, sort_keys=True,
                                         separators=(',', ':')).encode('utf-8')).hexdigest()

    def index_status(self, import_id: str, version_id: str) -> dict:
        meta = self.imports.version(import_id, version_id)
        root = self._folder(import_id, version_id) / 'cache'
        marker = root / 'index.json'
        db = root / 'observations.sqlite3'
        if db.is_file() and marker.is_file():
            try:
                state = json.loads(marker.read_text(encoding='utf-8'))
                if state.get('schema_version')==INDEX_VERSION and state.get('source_sha256')==meta['sha256']:
                    return {'status':'ready','indexed_rows':state.get('rows',0),'expected_rows':meta['counts']['output_rows']}
            except (OSError,ValueError):
                pass
        with self._index_jobs_lock:
            job = dict(self._index_jobs.get((import_id, version_id), {}))
        return job or {'status':'unbuilt','indexed_rows':0,'expected_rows':meta['counts']['output_rows']}

    def start_index(self, import_id: str, version_id: str) -> dict:
        state=self.index_status(import_id, version_id)
        if state['status'] in {'ready','queued','running'}:return state
        key=(import_id,version_id)
        with self._index_jobs_lock:
            active=sum(v['status'] in {'queued','running'} for v in self._index_jobs.values())
            if active:raise SmartImportError('另一个工作台正在建立分析索引，请稍后再试')
            job={'status':'queued','indexed_rows':0,'expected_rows':self.imports.version(import_id,version_id)['counts']['output_rows']}
            self._index_jobs[key]=job
        def update(**data):
            with self._index_jobs_lock:
                job.update(data)
        def run():
            update(status='running')
            try:
                self._ensure_index(import_id,version_id,progress_callback=lambda n:update(indexed_rows=n))
                update(status='ready',indexed_rows=job['expected_rows'])
            except Exception as exc:
                update(status='failed',error=str(exc)[:600])
        threading.Thread(target=run,name='timepilot-bi-index',daemon=True).start()
        return dict(job)

    def _ensure_index(self, import_id: str, version_id: str, progress_callback=None) -> Path:
        meta = self.imports.version(import_id, version_id)
        root = self._folder(import_id, version_id) / 'cache'
        db = root / 'observations.sqlite3'
        marker = root / 'index.json'
        with self._lock:
            if db.is_file() and marker.is_file():
                try:
                    state = json.loads(marker.read_text(encoding='utf-8'))
                    if state.get('schema_version') == INDEX_VERSION and state.get('source_sha256') == meta['sha256']:
                        self.imports.version_file(import_id, version_id)  # detect damaged immutable source
                        return db
                except (ValueError, OSError):
                    pass
            # Always validate the immutable source when creating an index.
            csv_path = self.imports.version_file(import_id, version_id)
            required_free = max(100 * 1024 * 1024, csv_path.stat().st_size * 2)
            root.mkdir(parents=True, exist_ok=True)
            if shutil.disk_usage(root.parent).free < required_free:
                raise SmartImportError('可用磁盘空间不足，无法建立数据筛选索引；原始版本未修改')
            root.mkdir(parents=True, exist_ok=True)
            tmp = root / f'.building-{uuid4().hex}.sqlite3'
            connection = sqlite3.connect(tmp, timeout=30)
            try:
                connection.execute('PRAGMA journal_mode=DELETE')
                connection.execute('PRAGMA synchronous=FULL')
                connection.execute('PRAGMA temp_store=MEMORY')
                connection.execute('''CREATE TABLE observations (
                    id INTEGER PRIMARY KEY, series_id TEXT, entity_id TEXT, timestamp TEXT,
                    target TEXT, numeric_target REAL, available_at TEXT,
                    statistical_scope TEXT, source_name TEXT, country TEXT,
                    brand TEXT, vehicle TEXT, quality_flags TEXT,
                    source_file TEXT, source_sheet TEXT, source_row TEXT, source_cell TEXT)''')
                sql = 'INSERT INTO observations VALUES (NULL,' + ','.join('?' for _ in range(16)) + ')'
                batch = []
                count = 0
                with csv_path.open('r', encoding='utf-8', newline='') as stream:
                    for r in csv.DictReader(stream):
                        try:
                            value = float(r['target']) if r.get('target') else None
                            if value is not None and not math.isfinite(value): value = None
                        except (ValueError, OverflowError):
                            value = None
                        batch.append((r.get('series_id',''), r.get('entity_id',''), r.get('timestamp',''), r.get('target',''), value,
                                      r.get('available_at',''),r.get('statistical_scope',''),r.get('source_name',''),
                                      r.get('country',''),r.get('brand',''),r.get('vehicle',''),r.get('quality_flags',''),
                                      r.get('source_file',''),r.get('source_sheet',''),r.get('source_row',''),r.get('source_cell','')))
                        if len(batch) == 5000:
                            connection.executemany(sql, batch); count += len(batch); batch.clear()
                            if progress_callback:progress_callback(count)
                    if batch: connection.executemany(sql, batch); count += len(batch)
                    if progress_callback:progress_callback(count)
                for field in (*DIMENSIONS, 'series_id', 'entity_id', 'timestamp'):
                    connection.execute(f'CREATE INDEX idx_{field} ON observations ({field})')
                connection.execute('CREATE INDEX idx_series_period ON observations (series_id, timestamp)')
                connection.commit()
                if count != meta['counts']['output_rows']:
                    raise SmartImportError('分析索引记录数与转换版本不一致，拒绝使用')
                if connection.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                    raise SmartImportError('分析索引自检失败')
                connection.close()
                os.replace(tmp, db)
                _write_json(marker, {'schema_version': INDEX_VERSION, 'source_sha256': meta['sha256'],
                                     'rows': count, 'created_at': datetime.now().astimezone().isoformat()})
                return db
            except Exception:
                connection.close()
                tmp.unlink(missing_ok=True)
                raise

    def _connect(self, import_id: str, version_id: str):
        con = sqlite3.connect(self._ensure_index(import_id, version_id), timeout=30)
        con.row_factory = sqlite3.Row
        con.execute('PRAGMA query_only=ON')
        return con

    @staticmethod
    def _where(selection: ViewSelection, *, skip: str | None = None):
        clauses, params = [], []
        for field, values in selection.canonical()['filters'].items():
            if field == skip: continue
            clauses.append(f'{field} IN (' + ','.join('?' for _ in values) + ')')
            params.extend(values)
        if selection.date_from:
            clauses.append('substr(timestamp, 1, 7) >= ?'); params.append(selection.date_from)
        if selection.date_to:
            clauses.append('substr(timestamp, 1, 7) <= ?'); params.append(selection.date_to)
        return (' WHERE ' + ' AND '.join(clauses) if clauses else ''), params

    def analyze(self, import_id: str, version_id: str, selection: ViewSelection) -> dict:
        meta = self.imports.version(import_id, version_id)
        con = self._connect(import_id, version_id)
        try:
            where, args = self._where(selection)
            stat = con.execute('''SELECT count(*) AS rows, count(DISTINCT series_id) AS series,
                    sum(CASE WHEN target IS NULL OR target='' THEN 1 ELSE 0 END) AS missing,
                    sum(CASE WHEN numeric_target<0 THEN 1 ELSE 0 END) AS negative,
                    sum(CASE WHEN instr(quality_flags,'conflict_review')>0 THEN 1 ELSE 0 END) AS conflict,
                    sum(CASE WHEN instr(quality_flags,'duplicate_identical')>0 THEN 1 ELSE 0 END) AS duplicates,
                    sum(CASE WHEN instr(quality_flags,'scope_unverified')>0 THEN 1 ELSE 0 END) AS scope_review,
                    sum(CASE WHEN available_at='' THEN 1 ELSE 0 END) AS unpublished,
                    count(DISTINCT source_name) AS distinct_sources,
                    count(DISTINCT statistical_scope) AS distinct_scopes,
                    min(timestamp) AS first_month, max(timestamp) AS last_month
                    FROM observations''' + where, args).fetchone()
            summary = {k:(stat[k] or 0 if k not in ('first_month','last_month') else stat[k]) for k in stat.keys()}
            facet = {}
            for field in DIMENSIONS:
                fw, fa = self._where(selection, skip=field)
                found = con.execute(f'SELECT {field} AS label, COUNT(*) AS n FROM observations {fw} '
                                    f'GROUP BY {field} ORDER BY n DESC, label LIMIT ?',
                                    [*fa, MAX_FACETS+1]).fetchall()
                facet[field] = {'items':[{'value':x['label'], 'count':x['n']} for x in found[:MAX_FACETS]],
                                'truncated':len(found)>MAX_FACETS}
            trend = [dict(x) for x in con.execute('''SELECT substr(timestamp,1,7) AS month,
                     SUM(numeric_target) AS total, COUNT(numeric_target) AS valid_count,
                     COUNT(*) AS rows FROM observations '''+where+
                     ' GROUP BY substr(timestamp,1,7) ORDER BY month LIMIT 480', args)]
            series_data = con.execute('''SELECT series_id, min(country) AS country,
                min(brand) AS brand, min(vehicle) AS vehicle,
                min(source_name) AS source_name, min(statistical_scope) AS statistical_scope,
                min(timestamp) AS first_month, max(timestamp) AS last_month,
                count(*) AS records, count(DISTINCT substr(timestamp,1,7)) AS months,
                count(DISTINCT CASE WHEN numeric_target IS NOT NULL THEN substr(timestamp,1,7) END) AS valid_months,
                sum(CASE WHEN target='' THEN 1 ELSE 0 END) AS missing
                FROM observations ''' + where +
                ' GROUP BY series_id ORDER BY records DESC, series_id LIMIT ?', [*args, MAX_SERIES_DISPLAY])
            series = []
            for r in series_data:
                item = dict(r)
                start, end = item['first_month'], item['last_month']
                if start and end:
                    span = (int(end[:4])-int(start[:4]))*12 + int(end[5:7])-int(start[5:7])+1
                    item['coverage'] = round(item['valid_months']/max(1,span),4)
                    item['calendar_coverage'] = round(item['months']/max(1,span),4)
                series.append(item)
            coverage = con.execute('''SELECT sum(months) AS observed, sum(valid_months) AS valid_observed, sum(
                    (CAST(substr(last_month,1,4) AS INT)-CAST(substr(first_month,1,4) AS INT))*12
                     + CAST(substr(last_month,6,2) AS INT)-CAST(substr(first_month,6,2) AS INT)+1
                    ) AS possible FROM (
                    SELECT series_id,count(DISTINCT substr(timestamp,1,7)) AS months,
                    count(DISTINCT CASE WHEN numeric_target IS NOT NULL THEN substr(timestamp,1,7) END) AS valid_months,
                    min(timestamp) AS first_month, max(timestamp) AS last_month
                    FROM observations '''+where+' GROUP BY series_id)', args).fetchone()
            summary['calendar_coverage'] = round((coverage['observed'] or 0)/max(1,coverage['possible'] or 0),4)
            summary['coverage'] = round((coverage['valid_observed'] or 0)/max(1,coverage['possible'] or 0),4)
            samples = [dict(r) for r in con.execute('''SELECT series_id,timestamp,target,statistical_scope,
                    country,brand,vehicle,source_name,quality_flags,
                    source_file,source_sheet,source_row,source_cell FROM observations'''+where+
                    ' ORDER BY timestamp DESC,id DESC LIMIT ?', [*args, MAX_PREVIEW])]
            warnings=[]
            if summary['rows']:
                if summary['distinct_scopes']>1:
                    warnings.append('当前筛选未限定统计口径，趋势合计可能混合销量、产量等不同指标')
                if summary['distinct_sources']>1:
                    warnings.append('当前筛选未限定来源，重合观测可能被重复汇总')
                if summary['missing']: warnings.append('存在缺失值；生成预测草稿时将检查各序列连续性')
                if summary['unpublished']: warnings.append('部分记录缺少可用时间；不能直接用于防泄漏回测')
            return {'import_id':import_id,'version_id':version_id,'source_sha256':meta['sha256'],
                    'selection':selection.canonical(), 'summary':summary,'facets':facet,
                    'trend':trend, 'series':series,'samples':samples, 'warnings':warnings}
        finally:
            con.close()

    def facet_search(self, import_id: str, version_id: str, selection: ViewSelection,
                     field: str, query: str = ''):
        if field not in DIMENSIONS: raise SmartImportError('不支持的筛选维度')
        if len(query)>100: raise SmartImportError('查询关键词过长')
        con = self._connect(import_id, version_id)
        try:
            where, args = self._where(selection, skip=field)
            if query:
                where += (' AND ' if where else ' WHERE ') + f'instr({field},?)>0'
                args.append(query)
            values = con.execute(f'SELECT {field} AS label,count(*) AS n FROM observations{where} '
                                 'GROUP BY '+field+' ORDER BY n DESC,label LIMIT 101', args).fetchall()
            return {'field':field, 'items':[{'value':v['label'],'count':v['n']} for v in values[:100]],
                    'truncated':len(values)>100}
        finally:
            con.close()

    def save_view(self, import_id: str, version_id: str, payload: SaveView):
        name = payload.name.strip()
        if not name: raise SmartImportError('请填写视图名称')
        analysis = self.analyze(import_id, version_id, payload.selection)
        if not analysis['summary']['rows']: raise SmartImportError('筛选后无数据，无法保存视图')
        folder = self._folder(import_id,version_id) / 'views'
        view_id = str(uuid4())
        record = {'view_id':view_id,'name':name,'import_id':import_id,'version_id':version_id,
                  'source_sha256':analysis['source_sha256'], 'selection':payload.selection.canonical(),
                  'created_at':datetime.now().astimezone().isoformat(),
                  'summary_at_save':analysis['summary'],
                  'perspective_workspace':payload.perspective_workspace,
                  'base_table_name':payload.base_table_name}
        record['view_sha256'] = self._stable_digest(record)
        _write_json(folder/f'{view_id}.json',record)
        return record

    def _get_view(self, import_id: str, version_id: str, view_id: str):
        path = self._folder(import_id,version_id)/'views'/f'{self._load_id(view_id)}.json'
        if not path.is_file(): raise KeyError('筛选视图不存在')
        view = json.loads(path.read_text(encoding='utf-8'))
        sha = view.pop('view_sha256', None)
        if sha!=self._stable_digest(view): raise SmartImportError('保存视图内容已被修改')
        view['view_sha256']=sha
        if view['source_sha256'] != self.imports.version(import_id,version_id)['sha256']:
            raise SmartImportError('视图与数据版本不一致')
        return view

    def view(self, import_id,version_id,view_id):
        return self._get_view(import_id,version_id,view_id)

    def views(self, import_id, version_id):
        folder = self._folder(import_id,version_id)/'views'
        if not folder.is_dir():return []
        result=[]
        for item in sorted(folder.glob('*.json')):
            try:result.append(self._get_view(import_id,version_id,item.stem))
            except (SmartImportError,KeyError,ValueError):continue
        return sorted(result, key=lambda x:x['created_at'],reverse=True)

    def _evaluate_series(self, import_id, version_id, selection: ViewSelection, options: DraftOptions):
        con = self._connect(import_id,version_id)
        try:
            where, args = self._where(selection)
            curs = con.execute('''SELECT series_id,timestamp,target,available_at,quality_flags,
                                 statistical_scope,source_name FROM observations '''+where+
                               ' ORDER BY series_id,timestamp,id',args)
            count = 0
            total_by_scope, total_by_source, reasons = Counter(),Counter(),Counter()
            eligible_ids, invalid_examples = [], []
            total_series = 0
            eligible_rows = 0
            current=None; n=0; prev=None; bad=set(); has_assumed=False
            need = max(options.min_history, (12 if 'seasonal_naive' in options.model_candidates else 2)) + 2*options.forecast_horizon + options.validation_windows-1
            def end():
                nonlocal total_series, eligible_rows
                if current is None:return
                total_series += 1
                failures=set(bad)
                if n < need: failures.add('history_short')
                if failures:
                    reasons.update(failures)
                    if len(invalid_examples)<12:invalid_examples.append({'series_id':current,'records':n,'reasons':sorted(failures)})
                else:
                    eligible_ids.append(current)
                    eligible_rows += n
            for r in curs:
                if r['series_id']!=current:
                    end();current=r['series_id'];n=0;prev=None;bad=set()
                n+=1;count+=1
                total_by_scope[r['statistical_scope']]+=1
                total_by_source[r['source_name']]+=1
                period=r['timestamp']
                try:
                    index=int(period[:4])*12+int(period[5:7])-1
                    if len(period)<10 or period[8:10]!='01':bad.add('invalid_period')
                except (ValueError,TypeError):
                    index=None;bad.add('invalid_period')
                if prev is not None and index is not None and index!=prev+1:
                    bad.add('duplicate_period' if index==prev else 'missing_period')
                if index is not None:prev=index
                if not r['target']:
                    bad.add('missing_target')
                else:
                    try:
                        if not math.isfinite(float(r['target'])):bad.add('invalid_target')
                    except (ValueError,TypeError):bad.add('invalid_target')
                flags=set(filter(None,(r['quality_flags'] or '').split(';')))
                if flags & {'conflict_review','duplicate_identical','scope_unverified','non_numeric','publication_timezone_unverified'}:
                    bad.update(flags & {'conflict_review','duplicate_identical','scope_unverified','non_numeric','publication_timezone_unverified'})
                if r['available_at']:
                    try:
                        published=date.fromisoformat(r['available_at'][:10])
                        if published<date.fromisoformat(period[:10]):
                            bad.add('publication_before_observation')
                    except (ValueError,TypeError):
                        bad.add('invalid_available_at')
                if not r['available_at'] and options.publication_lag_days is None:
                    bad.add('missing_available_at')
                if not r['available_at'] and options.publication_lag_days is not None:
                    has_assumed=True
            end()
            blockers=[]
            if not count: blockers.append('筛选视图没有匹配数据')
            if len(total_by_scope)!=1: blockers.append('请限定为一种已确认的统计口径；销量和产量不能合并训练')
            if len(total_by_source)!=1: blockers.append('请限定为一个数据来源；跨来源冲突需要先核对')
            if reasons and options.series_policy=='all':blockers.append('部分序列存在缺失、重复或历史长度不足；可调整筛选条件或明确选择仅使用合格序列')
            selected_count = count if options.series_policy=='all' else None
            if options.series_policy=='eligible_only':
                if not eligible_ids: blockers.append('没有满足历史长度和质量要求的序列')
                # Selected row count is checked again during snapshot materialization.
                selected_count = eligible_rows
            if selected_count > MAX_DATASET_ROWS:
                blockers.append(f'筛选结果超过 {MAX_DATASET_ROWS:,} 条训练集上限，请进一步限定国家、品牌、车型或系列')
            if has_assumed:
                # Explicit lag is a documented assumption; no automated point-in-time claim.
                warning='已按用户声明的延迟推导部分 available_at；应核对真实发布日期后再使用回测结果'
            else:warning=None
            return {'matched_rows':count, 'matched_series':total_series,
                    'eligible_series':len(eligible_ids),'eligible_series_ids':eligible_ids,
                    'invalid_examples':invalid_examples,'reason_counts':dict(reasons),
                    'scopes':dict(total_by_scope),'sources':dict(total_by_source),
                    'min_required_history':need,'blockers':blockers,
                    'warnings':[warning] if warning else [],'ready':not blockers}
        finally:
            con.close()

    def create_draft(self, import_id, version_id, view_id, options: DraftOptions):
        view=self._get_view(import_id,version_id,view_id)
        selection=ViewSelection.model_validate(view['selection'])
        check=self._evaluate_series(import_id,version_id,selection,options)
        draft_id=str(uuid4())
        record={'draft_id':draft_id,'view_id':view_id,'view_sha256':view['view_sha256'],
                'import_id':import_id,'version_id':version_id,'source_sha256':view['source_sha256'],
                'name':options.name.strip(), 'options':options.model_dump(mode='json'),
                'preflight':{k:v for k,v in check.items() if k!='eligible_series_ids'},
                'eligible_series_ids':check['eligible_series_ids'],
                'created_at':datetime.now().astimezone().isoformat(),
                'status':'ready' if check['ready'] else 'needs_review',
                'dataset_id':None}
        _write_json(self._folder(import_id,version_id)/'drafts'/f'{draft_id}.json',record)
        return self._public_draft(record)

    @staticmethod
    def _public_draft(record):
        return {k:v for k,v in record.items() if k!='eligible_series_ids'}

    def draft(self,import_id,version_id,draft_id):
        path=self._folder(import_id,version_id)/'drafts'/f'{self._load_id(draft_id)}.json'
        if not path.is_file():raise KeyError('预测草稿不存在')
        obj=json.loads(path.read_text(encoding='utf-8'))
        if (obj['source_sha256'] != self.imports.version(import_id,version_id)['sha256'] or
            obj['view_sha256'] != self._get_view(import_id,version_id,obj['view_id'])['view_sha256']):
            raise SmartImportError('预测草稿关联的来源版本已变化')
        return obj

    def drafts(self,import_id,version_id):
        root=self._folder(import_id,version_id)/'drafts'
        if not root.is_dir():return []
        result=[]
        for f in root.glob('*.json'):
            try:result.append(self._public_draft(self.draft(import_id,version_id,f.stem)))
            except (ValueError,OSError,KeyError):continue
        return sorted(result,key=lambda x:x['created_at'],reverse=True)

    def materialize(self,import_id,version_id,draft_id):
        if self.store is None or self.settings is None or self.engine is None:
            raise SmartImportError('预测运行组件未就绪')
        with self._lock:
            draft=self.draft(import_id,version_id,draft_id)
            if draft['dataset_id']:
                existing=self.store.dataset_by_name_or_id(draft['dataset_id'])
                if existing:return self._public_draft(draft)
                raise SmartImportError('历史草稿关联的数据集已丢失，需重新生成草稿')
            if draft['status']!='ready':raise SmartImportError('草稿仍有阻断项，不能生成训练数据集')
            view=self._get_view(import_id,version_id,draft['view_id'])
            selection=ViewSelection.model_validate(view['selection'])
            opts=DraftOptions.model_validate(draft['options'])
            check=self._evaluate_series(import_id,version_id,selection,opts)
            if not check['ready'] or check['eligible_series']!=draft['preflight']['eligible_series']:
                raise SmartImportError('数据适用性发生变化，请重新生成草稿')
            include=set(check['eligible_series_ids'])
            con=self._connect(import_id,version_id)
            try:
                where,args=self._where(selection)
                cursor=con.execute('''SELECT series_id,timestamp,target,available_at FROM observations'''+where+
                                   ' ORDER BY series_id,timestamp,id',args)
                buf=io.StringIO(newline='')
                writer=csv.writer(buf)
                writer.writerow(['series_id','timestamp','target','available_at'])
                rows=0
                for item in cursor:
                    if opts.series_policy=='eligible_only' and item['series_id'] not in include:continue
                    availability=item['available_at']
                    if not availability and opts.publication_lag_days is not None:
                        availability=(date.fromisoformat(item['timestamp'][:10])+timedelta(days=opts.publication_lag_days)).isoformat()
                    writer.writerow([item['series_id'],item['timestamp'],item['target'],availability])
                    rows+=1
                    if rows>MAX_DATASET_ROWS:raise SmartImportError('生成数据超过训练集容量限制')
                if rows<1:raise SmartImportError('筛选后没有可以训练的数据')
                csv_data=buf.getvalue().encode('utf-8')
                if len(csv_data)>self.settings.max_upload_bytes:
                    raise SmartImportError('训练数据超过当前标准数据集上传容量；请缩小筛选范围')
            finally:
                con.close()
            from timepilot.data import DataValidationError, create_dataset, get_dataset
            from timepilot.contracts import TaskSpec
            name=f"{draft['name']} · {draft_id[:8]}"
            try:
                manifest=create_dataset(self.store,self.settings,csv_data,'workbench_view.csv',
                                        name=name,frequency='MS',timestamp_col='timestamp',target_col='target',
                                        entity_col='series_id',available_at_col='available_at',
                                        source_timezone='UTC')
                task=TaskSpec(task_id='wb-'+draft_id[:16],name=draft['name'],frequency='MS',
                              forecast_horizon=opts.forecast_horizon,model_candidates=opts.model_candidates,
                              training_budget_seconds=180,approved=False,
                              evaluation={'validation_windows':opts.validation_windows,
                                          'test_horizon':opts.forecast_horizon,'min_history':opts.min_history})
                m,data=get_dataset(self.store,self.settings,manifest.dataset_id)
                self.engine._validate(task,data,m)
            except (DataValidationError,ValueError) as exc:
                raise SmartImportError(f'训练数据验证失败：{exc}') from exc
            from timepilot.storage import atomic_json
            provenance={'import_id':import_id,'version_id':version_id,'source_sha256':draft['source_sha256'],
                        'view_id':draft['view_id'],'view_sha256':draft['view_sha256'],
                        'draft_id':draft_id,'selection':view['selection'],
                        'data_snapshot_sha256':manifest.snapshot_sha256,
                        'publication_lag_days_assumption':opts.publication_lag_days,
                        'materialized_rows':rows,'model_training_started':False}
            atomic_json(self.settings.home/'datasets'/manifest.dataset_id/'smart_import_lineage.json',provenance)
            draft['dataset_id']=manifest.dataset_id
            draft['status']='dataset_ready'
            draft['task_spec']=task.model_dump(mode='json')
            _write_json(self._folder(import_id,version_id)/'drafts'/f'{draft_id}.json',draft)
            return self._public_draft(draft)
