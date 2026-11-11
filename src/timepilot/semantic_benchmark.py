"""Human-reviewed semantic mapping benchmark built from real imported workbook schemas.

Unreviewed model/rule suggestions are NEVER promoted to ground truth. Evaluation
splits by source workbook, keeps references local, and records actual provider
latency/token usage. No fabricated labels, prices or remote-model invocations.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import random
import csv
import io
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from timepilot.data_understanding import _field_roles
from timepilot.smart_import import SmartImportService

ROLES = frozenset({
    'timestamp', 'year', 'target', 'series_id', 'available_at',
    'statistical_scope', 'source_name', 'country', 'brand', 'vehicle',
})
MONTH_VALUE = '__monthly_value__'  # A virtual target of the 1-12 month matrix.


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), sort_keys=True)


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def _today() -> str:
    return datetime.now(timezone.utc).isoformat()


class Annotation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    reviewer: str = Field(min_length=2, max_length=100)
    layout: Literal['wide_monthly', 'long_monthly', 'ordinary_timeseries', 'ordinary_table', 'metadata']
    roles: dict[str, str] = Field(default_factory=dict, max_length=15)
    statistical_scope: str | None = Field(default=None, max_length=100)
    notes: str = Field(default='', max_length=500)
    confirmed_by_human: Literal[True]

    @model_validator(mode='after')
    def check_roles(self):
        if not set(self.roles).issubset(ROLES):
            raise ValueError('Contains unrecognized semantic role')
        return self


class SemanticBenchmark:
    """SQLite local annotation ledger: schemas are candidates, labels are human actions."""

    def __init__(self, home: Path, imports: SmartImportService, gateway: Any = None):
        self.root = Path(home) / 'research_benchmarks'
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / 'semantic_gold.sqlite3'
        self.imports, self.gateway = imports, gateway
        self._lock = threading.RLock()
        with self._db() as con:
            con.execute('PRAGMA journal_mode=WAL')
            con.execute('''CREATE TABLE IF NOT EXISTS cases (
                id TEXT PRIMARY KEY, import_id TEXT NOT NULL, source_sha256 TEXT NOT NULL,
                candidate_id TEXT NOT NULL, source_name TEXT NOT NULL, sheet TEXT NOT NULL,
                schema_json TEXT NOT NULL, baseline_json TEXT NOT NULL,
                UNIQUE(import_id, candidate_id, source_sha256))''')
            con.execute('''CREATE TABLE IF NOT EXISTS annotations (
                case_id TEXT NOT NULL, revision INTEGER NOT NULL,
                annotation_json TEXT NOT NULL, annotation_sha256 TEXT NOT NULL,
                reviewed_at TEXT NOT NULL, PRIMARY KEY(case_id,revision))''')
            con.execute('''CREATE TABLE IF NOT EXISTS evaluations (
                id TEXT PRIMARY KEY, created_at TEXT NOT NULL,
                run_json TEXT NOT NULL, sha256 TEXT NOT NULL)''')
            con.execute('''CREATE TABLE IF NOT EXISTS review_trials (
                id TEXT PRIMARY KEY, case_id TEXT NOT NULL, arm TEXT NOT NULL,
                reviewer TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT,
                proposal_json TEXT NOT NULL, corrected_json TEXT, reference_sha256 TEXT NOT NULL,
                model_status TEXT NOT NULL, model_latency_ms REAL, usage_json TEXT,
                submitted_changes INTEGER, elapsed_seconds REAL, case_pass INTEGER)''')
            con.execute('''CREATE TABLE IF NOT EXISTS trial_protocols (
                id TEXT PRIMARY KEY, created_at TEXT NOT NULL, assignments_json TEXT NOT NULL,
                assignments_sha256 TEXT NOT NULL, method TEXT NOT NULL)''')
            if 'protocol_id' not in {r[1] for r in con.execute('PRAGMA table_info(review_trials)')}:
                con.execute('ALTER TABLE review_trials ADD COLUMN protocol_id TEXT')
            con.execute('CREATE UNIQUE INDEX IF NOT EXISTS trial_protocol_once ON review_trials(protocol_id,case_id,arm) WHERE protocol_id IS NOT NULL')

    from contextlib import contextmanager

    @contextmanager
    def _db(self):
        con = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        con.row_factory = sqlite3.Row
        con.execute('PRAGMA busy_timeout=15000')
        try:
            yield con
        finally:
            con.close()

    @staticmethod
    def _baseline(candidate: dict) -> dict:
        roles = {}
        for role, entry in _field_roles(candidate).items():
            val = entry['suggested']
            if role == 'target' and val == '月度数值列':
                val = MONTH_VALUE
            roles[role] = val
        return {'layout': candidate['kind'], 'roles': roles}

    def seed(self, import_id: str, *, max_cases: int = 200) -> dict:
        source = self.imports.manifest(import_id)
        candidates = source.get('candidates', [])[:min(max_cases, 300)]
        added = 0
        with self._lock, self._db() as con:
            con.execute('BEGIN IMMEDIATE')
            for c in candidates:
                # Metadata only: no row content in the candidate benchmark.
                schema = {'columns': c.get('columns', [])[:128], 'kind': c['kind'],
                          'month_count': len(c.get('month_columns', [])),
                          'year_column': c.get('year_column'),
                          'time_column': c.get('time_column'),
                          'quantity_columns': c.get('quantity_columns', [])[:10],
                          'suggested': bool(c.get('suggested'))}
                case_id = _sha({'source': source['source_sha256'], 'candidate': c['candidate_id']})
                row = con.execute('''INSERT OR IGNORE INTO cases
                    (id,import_id,source_sha256,candidate_id,source_name,sheet,schema_json,baseline_json)
                    VALUES (?,?,?,?,?,?,?,?)''',
                    (case_id,import_id,source['source_sha256'],c['candidate_id'],
                     c.get('logical_file', ''),c.get('sheet', ''),_canonical(schema),
                     _canonical(self._baseline(c))))
                added += row.rowcount
            con.commit()
        return {'import_id': import_id, 'source_sha256': source['source_sha256'],
                'candidate_count': len(candidates), 'new_review_cases': added,
                'ground_truth_status': 'awaiting_human_review'}

    def cases(self, *, status: Literal['all', 'pending', 'confirmed'] = 'all', limit: int = 200) -> list[dict]:
        with self._db() as con:
            rows = con.execute('''SELECT c.*, a.annotation_json, a.reviewed_at, a.revision
                FROM cases c LEFT JOIN annotations a ON a.case_id=c.id AND a.revision=(
                SELECT MAX(revision) FROM annotations x WHERE x.case_id=c.id)
                ORDER BY c.source_name,c.sheet,c.id LIMIT ?''', (min(limit, 500),)).fetchall()
        answer = []
        for row in rows:
            is_gold = row['annotation_json'] is not None
            if (status == 'confirmed' and not is_gold) or (status == 'pending' and is_gold):
                continue
            answer.append({'case_id': row['id'], 'import_id': row['import_id'],
                           'source_sha256': row['source_sha256'], 'source_name': row['source_name'],
                           'sheet': row['sheet'], 'schema': json.loads(row['schema_json']),
                           'baseline': json.loads(row['baseline_json']),
                           'status': 'human_confirmed' if is_gold else 'pending_human_review',
                           'annotation': json.loads(row['annotation_json']) if is_gold else None,
                           'reviewed_at': row['reviewed_at'], 'revision': row['revision']})
        return answer

    def export_review_template(self, *, limit: int = 200) -> str:
        """Export REAL source schemas and hints, leaving human labels empty."""
        out=io.StringIO()
        names=['case_id','source_sha256','source_name','sheet','columns_json',
               'suggested_layout','suggested_roles_json','reviewer',
               'confirmed_by_human','layout','roles_json','statistical_scope','notes']
        writer=csv.DictWriter(out,fieldnames=names,lineterminator='\n')
        writer.writeheader()
        for case in self.cases(status='pending',limit=limit):
            writer.writerow({'case_id':case['case_id'],
                'source_sha256':case['source_sha256'],
                'source_name':case['source_name'],'sheet':case['sheet'],
                'columns_json':_canonical(case['schema']['columns']),
                'suggested_layout':case['baseline']['layout'],
                'suggested_roles_json':_canonical(case['baseline']['roles']),
                'reviewer':'','confirmed_by_human':'','layout':'','roles_json':'',
                'statistical_scope':'','notes':''})
        return out.getvalue()

    def import_human_reviews(self, content: str) -> dict:
        """Validate *all* user-submitted labels before writing any of them.

        Suggested columns are deliberately ignored; only the explicit human
        roles_json and confirmed_by_human=true become reviewed references.
        """
        if not isinstance(content,str) or len(content.encode('utf-8'))>2_000_000:
            raise ValueError('Human label CSV exceeds the 2 MB acceptance limit')
        reader=csv.DictReader(io.StringIO(content.lstrip('\ufeff')))
        if not reader.fieldnames:
            raise ValueError('CSV is missing the header row')
        needed={'case_id','source_sha256','reviewer','confirmed_by_human','layout','roles_json'}
        if not needed.issubset(set(reader.fieldnames)):
            raise ValueError('Missing required manual-review columns')
        items=[]
        seen=set()
        with self._db() as con:
            for index,row in enumerate(reader,1):
                if index>200:
                    raise ValueError('At most 200 reviewed cases per batch')
                case_id=(row.get('case_id') or '').strip()
                if not case_id or case_id in seen:
                    raise ValueError('Missing or repeated case identifier')
                seen.add(case_id)
                rec=con.execute('SELECT source_sha256, schema_json FROM cases WHERE id=?',
                                (case_id,)).fetchone()
                if rec is None or rec['source_sha256'] != (row.get('source_sha256') or '').strip():
                    raise ValueError('Source hash mismatch or unknown case: '+case_id[:16])
                if (row.get('confirmed_by_human') or '').lower().strip() != 'true':
                    raise ValueError('Human confirmation must explicitly be true')
                annotation=Annotation.model_validate({
                    'reviewer':(row.get('reviewer') or '').strip(),
                    'confirmed_by_human':True,
                    'layout':(row.get('layout') or '').strip(),
                    'roles':json.loads(row.get('roles_json') or '{}'),
                    'statistical_scope':(row.get('statistical_scope') or '').strip() or None,
                    'notes':(row.get('notes') or '').strip()})
                columns=set(json.loads(rec['schema_json'])['columns'])
                allowed=columns|({MONTH_VALUE} if annotation.layout=='wide_monthly' else set())
                if any(role not in allowed for role in annotation.roles.values()):
                    raise ValueError('Manual review mentions an unknown column')
                if len(set(annotation.roles.values())) != len(annotation.roles):
                    raise ValueError('Two roles map to the same column')
                if annotation.roles.get('target')==MONTH_VALUE and annotation.layout!='wide_monthly':
                    raise ValueError('Virtual monthly value requires wide monthly layout')
                items.append((case_id,annotation))
        if not items:
            return {'imported_human_labels':0,'status':'no_verified_rows'}
        # All rows were validated against immutable source schemas before any write.
        # Each review revision remains independently auditable.
        results=[self.review(case_id,annotation) for case_id,annotation in items]
        return {'imported_human_labels':len(results),'status':'human_confirmed',
                'revisions':results,'source':'explicit_user_submitted_manual_review'}

    def review(self, case_id: str, review: Annotation | dict) -> dict:
        annotation = Annotation.model_validate(review)
        with self._lock, self._db() as con:
            con.execute('BEGIN IMMEDIATE')
            row = con.execute('SELECT schema_json FROM cases WHERE id=?', (case_id,)).fetchone()
            if row is None:
                raise KeyError('Benchmark case not found')
            columns = set(json.loads(row['schema_json'])['columns'])
            allowed = columns | ({MONTH_VALUE} if annotation.layout == 'wide_monthly' else set())
            if any(value not in allowed for value in annotation.roles.values()):
                raise ValueError('Gold label references a field absent from the source schema')
            if len(annotation.roles.values()) != len(set(annotation.roles.values())):
                raise ValueError('Two roles cannot refer to the same source field')
            if annotation.roles.get('target') == MONTH_VALUE and annotation.layout != 'wide_monthly':
                raise ValueError('Monthly virtual value is only valid for a monthly wide table')
            revision = con.execute('SELECT COALESCE(MAX(revision),0)+1 FROM annotations WHERE case_id=?',
                                   (case_id,)).fetchone()[0]
            payload = annotation.model_dump(mode='json')
            digest = _sha({'case_id':case_id,'annotation':payload,'revision':revision})
            con.execute('''INSERT INTO annotations(case_id,revision,annotation_json,annotation_sha256,reviewed_at)
                VALUES(?,?,?,?,?)''', (case_id,revision,_canonical(payload),digest,_today()))
            con.commit()
        return {'case_id':case_id,'revision':revision,'annotation_sha256':digest,
                'status':'human_confirmed'}

    @staticmethod
    def _normalize_prediction(raw: dict, columns: list[str], kind: str) -> dict:
        if not isinstance(raw,dict):
            raise ValueError('Semantic proposal must be a JSON object')
        layout = raw.get('layout')
        if layout not in {'wide_monthly','long_monthly','ordinary_timeseries','ordinary_table','metadata'}:
            raise ValueError('Model returned unknown layout')
        roles = raw.get('roles')
        if not isinstance(roles,dict) or not set(roles).issubset(ROLES):
            raise ValueError('Model returned unsupported role names')
        allowed = set(columns) | ({MONTH_VALUE} if kind=='wide_monthly' else set())
        if any(not isinstance(v,str) or v not in allowed for v in roles.values()):
            raise ValueError('Model invented a source column')
        if len(set(roles.values())) != len(roles):
            raise ValueError('Model assigned one column to multiple roles')
        return {'layout':layout, 'roles':roles}

    @staticmethod
    def _compare(gold: dict, pred: dict) -> dict:
        expected = gold.get('roles', {})
        actual = pred['roles']
        keys = set(expected) | set(actual)
        wrong = sorted(k for k in keys if expected.get(k) != actual.get(k))
        layout_correct = gold['layout'] == pred['layout']
        return {'case_pass': layout_correct and not wrong,
                'layout_correct': layout_correct,
                'role_total': len(keys), 'role_correct': len(keys)-len(wrong),
                'field_corrections_needed':len(wrong)+int(not layout_correct),
                'wrong_roles':wrong}

    def evaluate(self, *, use_model: bool = False,
                 input_usd_per_million: float | None = None,
                 output_usd_per_million: float | None = None,
                 max_cases: int = 100) -> dict:
        if use_model and (self.gateway is None):
            raise ValueError('No model gateway is configured')
        for amount in (input_usd_per_million,output_usd_per_million):
            if amount is not None and not 0 <= amount <= 1000:
                raise ValueError('Cost rates must be in USD per million tokens, between 0 and 1000')
        cases = self.cases(status='confirmed',limit=500)[:min(max_cases, 200)]
        pending = len(self.cases(status='pending',limit=500))
        if not cases:
            return {'status':'awaiting_human_labels', 'labeled_cases':0,
                    'unreviewed_cases':pending, 'rules':None, 'model':None,
                    'note':'No human-reviewed labels; do not report accuracy or artificial model benefits'}
        scores = {'rules': [], 'model': []}
        observations=[]
        t0=time.monotonic()
        total_tokens = {'input':0,'output':0}
        model_calls = model_success = 0
        usage_complete=True
        for case in cases:
            gold = case['annotation']
            baseline = case['baseline']
            rules = self._compare(gold, baseline)
            scores['rules'].append(rules)
            entry={'case_id':case['case_id'],'rules':rules,'model':None,'provider_latency_ms':None,
                   'usage':None,'model_status':'not_requested'}
            if use_model:
                model_calls+=1
                started=time.monotonic()
                try:
                    model_result=self.gateway.propose_semantic_labels({
                        'layout_hint':case['schema']['kind'],
                        'columns':case['schema']['columns'],
                        'month_count':case['schema']['month_count']})
                    raw = model_result['proposal']
                    predicted=self._normalize_prediction(raw,case['schema']['columns'],case['schema']['kind'])
                    entry['model']=self._compare(gold, predicted)
                    scores['model'].append(entry['model'])
                    model_success+=1
                    entry['model_status']='provider_response_evaluated'
                    usage=model_result.get('usage',{})
                    prompt=usage.get('prompt_tokens',usage.get('input_tokens'))
                    completion=usage.get('completion_tokens',usage.get('output_tokens'))
                    if not isinstance(prompt,int) or not isinstance(completion,int):
                        usage_complete=False
                    else:
                        total_tokens['input']+=prompt;total_tokens['output']+=completion
                    entry['usage']={'input_tokens':prompt,'output_tokens':completion}
                    entry['provider_model']=model_result.get('model')
                except Exception as exc:
                    entry['model_status']='failed_'+type(exc).__name__
                    entry['model_error']=str(exc)[:250]
                    usage_complete=False
                finally:
                    entry['provider_latency_ms']=round((time.monotonic()-started)*1000,2)
            observations.append(entry)
        def aggregate(rows):
            return {'evaluated_cases':len(rows),
                    'exact_task_success_rate':sum(int(x['case_pass']) for x in rows)/len(rows) if rows else None,
                    'field_role_accuracy':sum(x['role_correct'] for x in rows)/max(1,sum(x['role_total'] for x in rows)) if rows else None,
                    'layout_accuracy':sum(int(x['layout_correct']) for x in rows)/len(rows) if rows else None,
                    'corrections_to_gold':sum(x['field_corrections_needed'] for x in rows),
                    'corrections_metric':'estimated_field_operations_to_match_human_gold_not_observed_UI_edits'}
        model_stats=aggregate(scores['model']) if use_model and model_success else None
        cost_known=(use_model and model_calls==model_success and usage_complete and
                    input_usd_per_million is not None and output_usd_per_million is not None)
        cost=round((total_tokens['input']*input_usd_per_million+
                    total_tokens['output']*output_usd_per_million)/1_000_000,8) if cost_known else None
        report={'status':'evaluated','labeled_cases':len(cases),'unreviewed_cases':pending,
                'rules':aggregate(scores['rules']),'model':model_stats,
                'model_requested':use_model,'real_model_calls_attempted':model_calls,
                'model_calls_valid':model_success,
                'total_wall_seconds':round(time.monotonic()-t0,4),
                'provider_latency_mean_ms':round(sum(x['provider_latency_ms'] for x in observations
                                            if x['provider_latency_ms'] is not None)/model_calls,2) if model_calls else None,
                'usage_tokens':total_tokens if usage_complete and model_calls else None,
                'estimated_api_cost_usd':cost,
                'cost_status':'priced_from_reported_tokens' if cost_known else 'not_measurable',
                'model_errors_count':model_calls-model_success,
                'model_end_to_end_task_success_rate':(sum(int(x['case_pass']) for x in scores['model'])/model_calls) if model_calls else None,
                'model_end_to_end_denominator':'all_attempted_model_calls_including_invalid_and_failed',
                'observations':observations,
                'limitations':['Gold labels require deliberate human review',
                               'Field edit counts are distances to gold, not observed reviewer interaction',
                               'Model success excludes failed/invalid responses from its conditional score; track failure count separately']}
        report_id=_sha({'at':_today(),'report':report})
        with self._db() as con:
            con.execute('INSERT INTO evaluations(id,created_at,run_json,sha256) VALUES(?,?,?,?)',
                        (report_id,_today(),_canonical(report),_sha(report)))
        return {'report_id':report_id,**report}

    def create_protocol(self, *, reviewers: list[str], seed: int = 42,
                        max_cases: int = 50) -> dict:
        """Freeze independent human references and allocate paired, balanced A/B trials.

        Each case receives one rule and one model arm, assigned to different
        reviewers. Allocation alternates reviewer roles after a seeded shuffle.
        The reference annotator must be a third person for that case.
        No outcome can be reported before both arms finish against the frozen gold.
        """
        if len(reviewers) != 2 or len(set(reviewers)) != 2 or any(
                not 2 <= len(x.strip()) <= 100 for x in reviewers):
            raise ValueError('Provide two distinct independent reviewers (2-100 characters)')
        if not 1 <= max_cases <= 200 or not 0 <= seed <= 2**31 - 1:
            raise ValueError('Invalid randomized protocol parameters')
        candidates = self.cases(status='confirmed', limit=500)[:max_cases]
        if not candidates:
            raise ValueError('Human-confirmed reference labels are required before allocation')
        # Avoid case-selection order effects; seed affects allocation, never the gold.
        rng = random.Random(seed)
        rng.shuffle(candidates)
        assignments = []
        frozen = []
        for index, case in enumerate(candidates):
            gold = case['annotation']
            if gold['reviewer'] in reviewers:
                raise ValueError('Independent trial reviewers cannot be reference annotators')
            ref = _sha({'case_id':case['case_id'],'annotation':gold,
                        'revision':case['revision']})
            # The frozen reference must match the actually stored hash.
            with self._db() as con:
                row = con.execute('SELECT annotation_sha256 FROM annotations WHERE case_id=? '
                                  'AND revision=?',(case['case_id'],case['revision'])).fetchone()
            if row is None or row['annotation_sha256'] != ref:
                raise ValueError('Reference hash is inconsistent')
            order = (index + seed) % 2
            allocations = [('rules',reviewers[order]),('model',reviewers[1-order])]
            for arm, reviewer in allocations:
                assignments.append({'case_id':case['case_id'],'arm':arm,'reviewer':reviewer,
                                    'source_sha256':case['source_sha256'],
                                    'reference_sha256':ref})
            frozen.append({'case_id':case['case_id'],'reference_sha256':ref})
        snapshot = {'method':'paired_case_balanced_reviewer_allocation_v1',
                    'seed':seed,'reviewers':reviewers,'cases':frozen,
                    'assignments':assignments}
        digest = _sha(snapshot)
        with self._lock, self._db() as con:
            con.execute('INSERT OR IGNORE INTO trial_protocols '
                        '(id,created_at,assignments_json,assignments_sha256,method) '
                        'VALUES (?,?,?,?,?)',
                        (digest,_today(),_canonical(snapshot),digest,snapshot['method']))
        return {'protocol_id':digest,'cases':len(candidates),'assignments':assignments,
                'method':snapshot['method'],'observed_outcomes':0,
                'note':'Allocation is deterministic and paired; no claim of model benefit before real blinded sessions'}

    def protocol(self, protocol_id: str, *, include_assignments: bool = True) -> dict:
        if len(protocol_id) != 64 or any(ch not in '0123456789abcdef' for ch in protocol_id):
            raise ValueError('Invalid trial protocol identifier')
        with self._db() as con:
            row = con.execute('SELECT * FROM trial_protocols WHERE id=?',(protocol_id,)).fetchone()
        if row is None:
            raise KeyError('Trial protocol not found')
        payload = json.loads(row['assignments_json'])
        if _sha(payload) != protocol_id or row['assignments_sha256'] != protocol_id:
            raise ValueError('Trial protocol integrity check failed')
        info = {'protocol_id':protocol_id,'created_at':row['created_at'],
                'method':payload['method'],'seed':payload['seed'],
                'cases':len(payload['cases'])}
        if include_assignments:
            info['assignments'] = payload['assignments']
        return info

    def protocol_summary(self, protocol_id: str, *,
                         input_usd_per_million: float | None = None,
                         output_usd_per_million: float | None = None) -> dict:
        for rate in (input_usd_per_million,output_usd_per_million):
            if rate is not None and not 0 <= rate <= 1000:
                raise ValueError('Cost rate must be USD per million tokens between 0 and 1000')
        record=self.protocol(protocol_id)
        assignments=record['assignments']
        with self._db() as con:
            rows=con.execute('SELECT case_id,arm,reviewer,finished_at,submitted_changes,elapsed_seconds,'
                             'case_pass,model_status,model_latency_ms,usage_json FROM review_trials WHERE protocol_id=?',
                             (protocol_id,)).fetchall()
        actual={(r['case_id'],r['arm'],r['reviewer']):r for r in rows}
        paired=[]
        for case_id in sorted({a['case_id'] for a in assignments}):
            rule=next(a for a in assignments if a['case_id']==case_id and a['arm']=='rules')
            model=next(a for a in assignments if a['case_id']==case_id and a['arm']=='model')
            r=actual.get((case_id,'rules',rule['reviewer']))
            m=actual.get((case_id,'model',model['reviewer']))
            if not (r and m and r['finished_at'] and m['finished_at']):
                continue
            paired.append({'case_id':case_id,'rules_edits':r['submitted_changes'],
                           'model_edits':m['submitted_changes'],
                           'rules_seconds':r['elapsed_seconds'],
                           'model_seconds':m['elapsed_seconds'],
                           'rules_correct':bool(r['case_pass']),
                           'model_correct':bool(m['case_pass'])})
        n=len(paired)
        token_in=token_out=0
        usage_complete=True
        call_latencies=[]
        for case in paired:
            a=next(x for x in assignments if x['case_id']==case['case_id'] and x['arm']=='model')
            r=actual[(case['case_id'],'model',a['reviewer'])]
            if r['model_status']!='model_response_recorded' or not r['usage_json']:
                usage_complete=False
                continue
            payload=json.loads(r['usage_json'])
            usage=(payload or {}).get('usage') or {}
            tin=usage.get('prompt_tokens',usage.get('input_tokens'))
            tout=usage.get('completion_tokens',usage.get('output_tokens'))
            if not isinstance(tin,int) or not isinstance(tout,int) or tin<0 or tout<0:
                usage_complete=False
            else:
                token_in+=tin;token_out+=tout
            if r['model_latency_ms'] is not None:
                call_latencies.append(float(r['model_latency_ms']))
        can_price=(n>0 and usage_complete and input_usd_per_million is not None
                   and output_usd_per_million is not None)
        estimated_cost=(round((token_in*input_usd_per_million+
                               token_out*output_usd_per_million)/1_000_000,8)
                        if can_price else None)
        def mean(values):
            return round(sum(values)/len(values),4) if values else None
        return {'protocol_id':protocol_id,'status':'paired_observations' if n else 'awaiting_real_human_trials',
                'planned_cases':record['cases'],'complete_paired_cases':n,
                'incomplete_cases':record['cases']-n,
                'mean_model_minus_rule_edits':mean([x['model_edits']-x['rules_edits'] for x in paired]),
                'mean_model_minus_rule_seconds':mean([x['model_seconds']-x['rules_seconds'] for x in paired]),
                'model_final_correct_rate':mean([int(x['model_correct']) for x in paired]),
                'rules_final_correct_rate':mean([int(x['rules_correct']) for x in paired]),
                'pairs':paired,
                'model_api':{'calls_in_completed_pairs':len(call_latencies),
                             'mean_latency_ms':mean(call_latencies),
                             'tokens':{'input':token_in,'output':token_out} if usage_complete and n else None,
                             'estimated_cost_usd':estimated_cost,
                             'pricing_status':'estimated_from_provider_tokens' if can_price else 'unknown'},
                'interpretation':'descriptive_paired_A_B_not_causal_or_significant_without_sufficient_real_samples',
                'limitations':['Each case is reviewed twice by independent people; model API calls incur real cost',
                               'Server elapsed time includes idle time; random allocation cannot control all reviewer effects',
                               'Do not report an efficiency gain when paired cases are absent']}

    def start_trial(self, case_id: str, *, reviewer: str, arm: Literal['rules', 'model'],
                    protocol_id: str | None = None) -> dict:
        """Start an observed human-correction session, with real timestamps.

        Gold labels are deliberately excluded from the response.  Model use is
        explicitly selected; a failed model invocation is recorded as a failed
        trial rather than silently reclassified as a rules trial.
        """
        reviewer = str(reviewer).strip()
        if len(reviewer) < 2 or len(reviewer) > 100:
            raise ValueError('Please give a reviewer identifier (2-100 characters)')
        with self._db() as con:
            row = con.execute('SELECT schema_json,baseline_json FROM cases WHERE id=?', (case_id,)).fetchone()
            gold = con.execute('SELECT annotation_sha256 FROM annotations WHERE case_id=? ORDER BY revision DESC LIMIT 1', (case_id,)).fetchone()
        if row is None:
            raise KeyError('Benchmark case not found')
        if gold is None:
            raise ValueError('An independent, human-confirmed reference is required before A/B trials')
        if protocol_id is not None:
            protocol = self.protocol(protocol_id)
            allocation = next((a for a in protocol['assignments'] if a['case_id']==case_id and
                               a['arm']==arm and a['reviewer']==reviewer),None)
            if allocation is None or allocation['reference_sha256']!=gold['annotation_sha256']:
                raise ValueError('Trial does not match the frozen randomized assignment or gold revision')
            with self._db() as con:
                old=con.execute('SELECT id FROM review_trials WHERE protocol_id=? AND case_id=? AND arm=?',
                                (protocol_id,case_id,arm)).fetchone()
            if old:
                raise ValueError('The assigned A/B trial has already started')
        schema = json.loads(row['schema_json'])
        proposed = json.loads(row['baseline_json'])
        model_status = 'not_requested'
        latency_ms = 0.0
        usage = None
        if arm == 'model':
            if self.gateway is None:
                raise ValueError('No real model API configured; cannot start model-arm trial')
            t0 = time.monotonic()
            try:
                response = self.gateway.propose_semantic_labels({
                    'layout_hint': schema['kind'], 'columns': schema['columns'],
                    'month_count': schema['month_count'],
                })
                proposed = self._normalize_prediction(response['proposal'],schema['columns'],schema['kind'])
                usage = {'model': response.get('model'), 'usage':response.get('usage')}
                model_status = 'model_response_recorded'
            except Exception as exc:
                raise ValueError('Model-arm proposal failed; no silent substitution: '+type(exc).__name__) from exc
            finally:
                latency_ms = round((time.monotonic()-t0)*1000, 2)
        started = _today()
        trial_id = _sha({'case_id':case_id,'arm':arm,'reviewer':reviewer,
                         'started_at':started,'proposal':proposed})
        with self._lock, self._db() as con:
            try:
                con.execute('INSERT INTO review_trials (id,case_id,arm,reviewer,started_at,proposal_json,reference_sha256,model_status,model_latency_ms,usage_json,protocol_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                            (trial_id,case_id,arm,reviewer,started,_canonical(proposed),gold['annotation_sha256'],
                             model_status,latency_ms,_canonical(usage),protocol_id))
            except sqlite3.IntegrityError as exc:
                raise ValueError('The assigned A/B trial has already started') from exc
        return {'trial_id':trial_id,'case_id':case_id,'arm':arm,'reviewer':reviewer,
                'schema':schema,'suggestion':proposed,'started_at':started,
                'model_status':model_status,'protocol_id':protocol_id,'provider_latency_ms':latency_ms,
                'usage':usage,'status':'in_progress',
                'note':'Correct the suggested fields yourself; human editing duration includes idle time.'}

    def finish_trial(self, trial_id: str, *, reviewer: str, corrected: dict) -> dict:
        """Persist an actually submitted correction rather than a gold-distance proxy."""
        with self._lock, self._db() as con:
            con.execute('BEGIN IMMEDIATE')
            row = con.execute('SELECT t.*, c.schema_json FROM review_trials t JOIN cases c ON t.case_id=c.id WHERE t.id=?',
                              (trial_id,)).fetchone()
            if row is None:
                raise KeyError('Review trial not found')
            if row['reviewer'] != str(reviewer).strip():
                raise ValueError('Reviewer identity differs from the original trial')
            if row['finished_at']:
                raise ValueError('Review trial is already finalized and immutable')
            schema = json.loads(row['schema_json'])
            final = self._normalize_prediction(corrected,schema['columns'],schema['kind'])
            proposal = json.loads(row['proposal_json'])
            old = proposal.get('roles',{})
            new = final.get('roles',{})
            changed = sum(old.get(k)!=new.get(k) for k in set(old)|set(new))
            changed += int(proposal['layout'] != final['layout'])
            latest = con.execute('SELECT annotation_json,annotation_sha256 FROM annotations WHERE case_id=? ORDER BY revision DESC LIMIT 1',
                                 (row['case_id'],)).fetchone()
            if latest is None or latest['annotation_sha256'] != row['reference_sha256']:
                raise ValueError('Reference annotation changed during the trial; start a new trial')
            quality = self._compare(json.loads(latest['annotation_json']), final)
            end = _today()
            elapsed = max(0.0,(datetime.fromisoformat(end)-datetime.fromisoformat(row['started_at'])).total_seconds())
            con.execute('UPDATE review_trials SET finished_at=?, corrected_json=?, submitted_changes=?, elapsed_seconds=?, case_pass=? WHERE id=?',
                        (end,_canonical(final),changed,elapsed,int(quality['case_pass']),trial_id))
            con.commit()
        return {'trial_id':trial_id,'arm':row['arm'],'status':'completed',
                'submitted_field_changes':changed,'observed_elapsed_seconds':round(elapsed,3),
                'final_mapping_correct':quality['case_pass'],'model_status':row['model_status'],
                'limitations':'Elapsed time includes reviewer idle time; no client keystrokes were collected.'}

    def trial_summary(self, *, limit: int = 200) -> dict:
        with self._db() as con:
            rows = con.execute('SELECT * FROM review_trials ORDER BY started_at DESC LIMIT ?',
                               (max(1,min(limit,1000)),)).fetchall()
        arms = {}
        for arm in ('rules','model'):
            subset = [r for r in rows if r['arm']==arm]
            complete = [r for r in subset if r['finished_at']]
            arms[arm] = {'started':len(subset),'completed':len(complete),
                         'actual_submitted_field_changes':sum(r['submitted_changes'] for r in complete),
                         'mean_submitted_field_changes':(sum(r['submitted_changes'] for r in complete)/len(complete)) if complete else None,
                         'mean_elapsed_seconds':(sum(r['elapsed_seconds'] for r in complete)/len(complete)) if complete else None,
                         'final_success_rate':(sum(r['case_pass'] for r in complete)/len(complete)) if complete else None,
                         'mean_model_latency_ms':(sum(r['model_latency_ms'] for r in complete)/len(complete)) if complete else None}
        return {'status':'observed_human_sessions' if any(v['completed'] for v in arms.values()) else 'awaiting_review_trials',
                'arms':arms,'limitations':['This is a descriptive comparison: randomized/blinded allocation is not enforced',
                'Observed duration includes reviewer idle time; compare similar case difficulty and reviewer groups',
                'Only actual submitted field changes are counted; keystrokes or clicks are not instrumented']}

    def reports(self, *, limit: int = 20) -> list[dict]:
        with self._db() as con:
            rows=con.execute('SELECT id,created_at,run_json,sha256 FROM evaluations ORDER BY created_at DESC LIMIT ?',
                             (min(max(limit,1),100),)).fetchall()
        return [{'report_id':r['id'],'created_at':r['created_at'],
                 'sha256':r['sha256'],'status':json.loads(r['run_json'])['status']} for r in rows]
