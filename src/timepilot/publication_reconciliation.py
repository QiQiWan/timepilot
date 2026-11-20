"""Human-reviewed recovery of uncertain project publication side effects.

Read-only evidence uses the immutable CSV SHA and authoritative native-BI
metadata rather than guessing from asset names. Every accepted decision is
project-scoped, idempotent, hash chained, and committed with the queue status.
This hash chain is a tamper-evidence mechanism, not a digital signature.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator
from typing import Literal

from timepilot.enterprise_security import CURRENT_PRINCIPAL, SecurityError
from timepilot.generic_assets import PublishGeneral
from timepilot.publication_identity import region_publication_lock
from timepilot.publication_journal import PublicationJournal
from timepilot.smart_import import SmartImportError


def canonical(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def digest(obj) -> str:
    return hashlib.sha256(canonical(obj).encode('utf-8')).hexdigest()


class ReconciliationDecision(BaseModel):
    model_config = ConfigDict(extra='forbid')
    decision_id: str
    action: Literal['confirm_complete', 'adopt_verified_orphan', 'retry_verified_absence', 'close_without_retry']
    evidence_sha256: str
    reason: str = Field(min_length=12, max_length=500)
    confirm: bool = False

    @field_validator('reason')
    @classmethod
    def reason_requires_substance(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 12:
            raise ValueError('需要至少 12 字的具体人工对账理由')
        return value


class PublicationReconciliation:
    def __init__(self, jobs):
        self.jobs = jobs
        self.catalog = jobs.catalog
        with self.catalog._lock, self.catalog._db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS project_publication_reviews(
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                decision_id TEXT NOT NULL UNIQUE,
                project_id TEXT NOT NULL,
                job_id TEXT NOT NULL,
                event_json TEXT NOT NULL,
                previous_hash TEXT NOT NULL,
                event_hash TEXT NOT NULL,
                created_at REAL NOT NULL)''')
            db.execute('''CREATE INDEX IF NOT EXISTS idx_publication_review_job
                          ON project_publication_reviews(project_id,job_id,sequence)''')
            db.execute('''CREATE TRIGGER IF NOT EXISTS publication_reviews_immutable_update
                          BEFORE UPDATE ON project_publication_reviews
                          BEGIN SELECT RAISE(ABORT,'publication review immutable'); END''')
            db.execute('''CREATE TRIGGER IF NOT EXISTS publication_reviews_immutable_delete
                          BEFORE DELETE ON project_publication_reviews
                          BEGIN SELECT RAISE(ABORT,'publication review immutable'); END''')

    def _authorize(self, pid, *, write=False):
        principal = CURRENT_PRINCIPAL.get()
        if self.catalog.security is not None:
            if principal is None:
                raise SecurityError('发布对账需要登录身份', 401)
            self.catalog.security.allowed_project(principal, pid,
                required='project_admin' if write else 'viewer')
        if write:
            self.catalog.writable(pid)
        else:
            self.catalog.get(pid)
        return principal.user_id if principal else 'local_operator'

    def inspect(self, pid: str, jid: str) -> dict:
        self._authorize(pid)
        job = self.jobs.get(pid, jid)
        with self.catalog._db() as db:
            raw=db.execute('SELECT request_json FROM project_publication_jobs WHERE project_id=? AND job_id=?', (pid,jid)).fetchone()
        request = PublishGeneral.model_validate_json(raw['request_json'])
        source_error = None
        try:
            identity = self.jobs.service.generic.expected_version(job['import_id'],job['candidate_id'],request)
        except (SmartImportError, KeyError, OSError, ValueError) as exc:
            source_error = type(exc).__name__
            identity = {'version_id': None, 'source_sha256': None}
        vid = identity['version_id']
        expected_source = identity['source_sha256']
        meta = None
        local = {'status': 'invalid' if source_error else 'missing',
                 'source_sha256': expected_source, 'version_id': vid,
                 'data_sha256': None, 'rows': None}
        if source_error:
            local['reason'] = 'source_unavailable_or_modified'
        try:
            if source_error:
                raise SmartImportError('源文件无法验证')
            meta = self.jobs.service.generic.version(job['import_id'],job['candidate_id'],vid)
            self.jobs.service.generic.version_file(job['import_id'],job['candidate_id'],vid)
            if meta['source_sha256'] != expected_source:
                raise SmartImportError('原始来源哈希与已发布版本不一致')
            local.update(status='verified',data_sha256=meta['data_sha256'],rows=meta['row_count'])
        except (KeyError, OSError, SmartImportError, ValueError) as exc:
            local['status'] = 'missing' if isinstance(exc, KeyError) else 'invalid'
            local['reason'] = type(exc).__name__

        native = self.jobs.service.native
        inventory = {'complete': False, 'scanned': 0, 'source': 'native_unavailable', 'unregistered_snapshots': None}
        if vid is not None and native is not None and hasattr(native,'find_published_candidates'):
            try:
                inventory = native.find_published_candidates(pid,job['import_id'],job['candidate_id'],vid)
            except Exception as exc:
                inventory = {**inventory, 'reason': type(exc).__name__}
        # Linked records can be inspected even if the broader metadata inventory
        # is incomplete or temporarily offline. Never infer ABSENCE from them.
        linked_ids = {item['native_asset_id'] for item in self.catalog.generic_for(pid)
                      if item['import_id']==job['import_id'] and
                         item['candidate_id']==job['candidate_id'] and item['version_id']==vid}
        ids = sorted(linked_ids | set(inventory.get('asset_ids') or []))
        entries = []
        for aid in ids:
            entry = {'asset_id': aid, 'linked': aid in linked_ids,
                     'valid': False, 'reason': 'not_verified', 'unified_linked': False}
            if native is not None and meta is not None:
                try:
                    stored = native.asset(aid)
                    lineage = stored.get('lineage') or {}
                    expected = {'project_id':pid,'import_id':job['import_id'],
                                'candidate_id':job['candidate_id'], 'version_id':vid,
                                'source_sha256':expected_source,'data_sha256':meta['data_sha256']}
                    correct = all(lineage.get(key)==value for key,value in expected.items())
                    checksum = native.verify(aid,force=True)
                    entry['valid'] = bool(correct and checksum.get('valid') and
                                          stored.get('row_count') == meta['row_count'])
                    entry['reason'] = 'verified' if entry['valid'] else 'lineage_or_snapshot_mismatch'
                    entry['snapshot_sha256'] = stored.get('sha256')
                    with self.catalog._db() as db:
                        exists = db.execute('''SELECT 1 FROM unified_versions WHERE project_id=?
                            AND version_id=? AND native_asset_id=?''',(pid,vid,aid)).fetchone()
                    entry['unified_linked'] = bool(exists)
                except Exception as exc:
                    entry['reason'] = type(exc).__name__
            entries.append(entry)
        good = [x for x in entries if x['valid']]
        complete = bool(inventory.get('complete')) and inventory.get('unregistered_snapshots',0)==0
        if local['status'] != 'verified':
            classification = 'blocked_local_artifact'
        elif len(entries)>1 or (entries and len(good)!=len(entries)):
            classification = 'blocked_ambiguous_or_invalid'
        elif inventory.get('scoped_unregistered_snapshots', 0) > 0:
            classification = 'blocked_unregistered_snapshot'
        elif not complete:
            classification = 'blocked_inventory_incomplete'
        elif len(good)==1 and good[0]['linked'] and good[0]['unified_linked']:
            classification = 'verified_complete'
        elif len(good)==1 and (not good[0]['linked'] or not good[0]['unified_linked']):
            classification = 'verified_incomplete_link'
        elif not entries and complete:
            classification = 'verified_absence'
        else:
            classification = 'blocked_inventory_incomplete'
        choices = {
            'verified_complete':['confirm_complete'],
            'verified_incomplete_link':['adopt_verified_orphan'],
            'verified_absence':['retry_verified_absence'],
        }.get(classification,[])
        # A closure never erases possibly orphaned data; its scope must be
        # documented and the prior job remains accessible in the audit trail.
        choices += ['close_without_retry']
        evidence = {'job_id':jid,'project_id':pid,'status':job['status'],
                    'stage':job['stage'],'updated_at':job['updated_at'],
                    'version_id':vid, 'local':local,
                    'inventory':{k:v for k,v in inventory.items() if k!='asset_ids'},
                    'assets':entries,'classification':classification,
                    'allowed_actions':choices}
        return {**evidence,'evidence_sha256':digest(evidence),
                'requires_human_confirmation':True}

    def history(self,pid,jid):
        # Audit reasons may contain operational notes; restrict the full trail.
        self._authorize(pid,write=True)
        self.jobs.get(pid,jid)
        with self.catalog._db() as db:
            rows = db.execute('''SELECT event_json,previous_hash,event_hash
                FROM project_publication_reviews WHERE project_id=? AND job_id=?
                ORDER BY sequence''',(pid,jid)).fetchall()
        out=[];previous='0'*64;valid=True
        for row in rows:
            event=json.loads(row['event_json'])
            correct = (row['previous_hash']==previous and
                       row['event_hash']==digest({'previous_hash':previous,'event':event}))
            valid=valid and correct
            out.append({**event,'event_hash':row['event_hash'],'verified':correct})
            previous=row['event_hash']
        return {'job_id':jid,'chain_valid':valid,'events':out}

    def decide(self,pid,jid,request:ReconciliationDecision):
        actor=self._authorize(pid,write=True)
        job=self.jobs.get(pid,jid)
        # Hold the same per-region OS lock used by direct/queued/batch writes.
        # Otherwise two reviewers can both mutate the native catalogue before
        # either commits the final decision to the project SQLite database.
        with region_publication_lock(self.catalog,pid,job['import_id'],job['candidate_id']):
            return self._decide_locked(pid,jid,request,actor)

    def _decide_locked(self,pid,jid,request:ReconciliationDecision,actor:str):
        try:
            decision_id=str(UUID(request.decision_id))
        except (ValueError,TypeError):
            raise SmartImportError('对账操作的 decision_id 必须为 UUID') from None
        if not request.confirm or not re.fullmatch('[0-9a-f]{64}',request.evidence_sha256):
            raise SmartImportError('需要确认操作并提交完整的证据 SHA256')
        # Idempotent retries return their original committed audit event.
        with self.catalog._db() as db:
            previous=db.execute('SELECT event_json FROM project_publication_reviews WHERE decision_id=?',
                                (decision_id,)).fetchone()
        if previous:
            event=json.loads(previous['event_json'])
            if (event['project_id']!=pid or event['job_id']!=jid or event['action']!=request.action
                    or event['reason']!=request.reason.strip() or event['evidence_sha256']!=request.evidence_sha256):
                raise SmartImportError('对账操作编号已用于其他项目、任务或决定')
            return {'already_applied':True,'decision':event,'job':self.jobs.get(pid,jid)}

        evidence=self.inspect(pid,jid)
        if evidence['status']!='failed' or evidence['stage']!='needs_reconciliation':
            raise SmartImportError('仅允许处理已阻断的待对账发布任务')
        if request.evidence_sha256!=evidence['evidence_sha256']:
            raise SmartImportError('发布证据已变化，请重新核对并提交最新 SHA256')
        if request.action not in evidence['allowed_actions']:
            raise SmartImportError('当前来源或原生 BI 证据不支持此操作')
        if request.action=='retry_verified_absence':
            with self.catalog._db() as db:
                attempts=db.execute('SELECT attempts FROM project_publication_jobs WHERE project_id=? AND job_id=?',
                                    (pid,jid)).fetchone()['attempts']
            if attempts>=5:
                raise SmartImportError('已达到自动重试次数上限，禁止重新入队')

        if request.action!='adopt_verified_orphan':
            latest=self.inspect(pid,jid)
            if latest['evidence_sha256']!=request.evidence_sha256:
                raise SmartImportError('提交前证据已变化，请重新核对')

        if request.action=='adopt_verified_orphan':
            # Idempotent local association; a crash here leaves the job blocked.
            # The next review observes a linked asset and may confirm completion.
            aid=evidence['assets'][0]['asset_id']
            job=self.jobs.get(pid,jid)
            meta=self.jobs.service.generic.version(job['import_id'],job['candidate_id'],evidence['version_id'])
            self.catalog.attach_generic(pid,job['import_id'],job['candidate_id'],evidence['version_id'],aid)
            link={'import_id':job['import_id'],'candidate_id':job['candidate_id'],
                  'version_id':evidence['version_id'],'native_asset_id':aid}
            registered=self.jobs.service.platform.register(pid,link,meta)
            self.jobs.service.platform.evaluate_quality(pid,registered['asset_id'],meta['version_id'])
            # Another operator may have acted while the above idempotent steps ran.
            evidence=self.inspect(pid,jid)
            if evidence['classification']!='verified_complete':
                raise SmartImportError('关联后原生 BI 证据未通过复核，任务保留待对账')

        status = {'confirm_complete': ('succeeded','complete'),
                  'adopt_verified_orphan': ('succeeded','complete'),
                  'retry_verified_absence': ('queued','queued'),
                  'close_without_retry': ('cancelled','reconciled_closed')}[request.action]
        final_status,final_stage=status
        now=time.time()
        event={'decision_id':decision_id,'project_id':pid,'job_id':jid,'actor_id':actor,
               'action':request.action,'reason':request.reason.strip(),
               'evidence_sha256':request.evidence_sha256,'created_at':now,
               'final_status':final_status,'final_stage':final_stage,
               'verified_native_asset_id':evidence['assets'][0]['asset_id']
                   if request.action in ('confirm_complete','adopt_verified_orphan') else None}
        with self.catalog._lock, self.catalog._db() as db:
            db.execute('BEGIN IMMEDIATE')
            # All state updates and audit inserts are one SQLite transaction.
            old=db.execute('SELECT event_json FROM project_publication_reviews WHERE decision_id=?',
                           (decision_id,)).fetchone()
            if old:
                old_event=json.loads(old['event_json'])
                if (old_event['job_id']!=jid or old_event['project_id']!=pid or old_event['action']!=request.action
                        or old_event['reason']!=request.reason.strip() or old_event['evidence_sha256']!=request.evidence_sha256):
                    raise SmartImportError('对账操作编号冲突')
                return {'already_applied':True,'decision':old_event,'job':self.jobs.get(pid,jid)}
            row=db.execute('SELECT status,stage,updated_at FROM project_publication_jobs WHERE project_id=? AND job_id=?',
                           (pid,jid)).fetchone()
            if row is None or row['status']!='failed' or row['stage']!='needs_reconciliation' or \
                    row['updated_at']!=evidence['updated_at']:
                raise SmartImportError('发布任务已被其他操作修改，请重新核对')
            prior=db.execute('SELECT event_hash FROM project_publication_reviews WHERE project_id=? AND job_id=? ORDER BY sequence DESC LIMIT 1',
                             (pid,jid)).fetchone()
            prev_hash=prior['event_hash'] if prior else '0'*64
            event_hash=digest({'previous_hash':prev_hash,'event':event})
            result={'project_id':pid,'status':'ready','reused':True,
                    'native_asset_id':event['verified_native_asset_id']}
            stored=canonical(result) if final_status=='succeeded' else None
            db.execute('''UPDATE project_publication_jobs SET status=?,stage=?,result_json=?,
                          error=NULL,lease_owner=NULL,lease_until=NULL,updated_at=?
                          WHERE project_id=? AND job_id=?''',
                       (final_status,final_stage,stored,now,pid,jid))
            db.execute('''INSERT INTO project_publication_reviews
                          (decision_id,project_id,job_id,event_json,previous_hash,event_hash,created_at)
                          VALUES (?,?,?,?,?,?,?)''',
                       (decision_id,pid,jid,canonical(event),prev_hash,event_hash,now))
            PublicationJournal.append(db,job_id=jid,project_id=pid,
                phase=final_stage,status=final_status,receipt={
                    'decision_id':decision_id,'action':request.action,
                    'native_asset_id':event['verified_native_asset_id']})
        return {'already_applied':False,'decision':event,'job':self.jobs.get(pid,jid)}
