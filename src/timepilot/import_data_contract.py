"""Versioned, evidenced import DataContract with human review gates.

The contract never reinterprets original columns; model descriptions remain
unconfirmed annotations. Every accepted review is tied to the parser fingerprint.
"""
from __future__ import annotations
import json
from datetime import datetime,timezone
from timepilot.import_summary import fingerprint
from timepilot.enterprise_security import CURRENT_PRINCIPAL
from timepilot.smart_import import SmartImportError


def _now():return datetime.now(timezone.utc).isoformat()

class ImportDataContracts:
    def __init__(self,catalog,summary_service):
        self.catalog=catalog;self.summary=summary_service

    def _region(self,pid,iid,rid):
        manifest=self.catalog.require_import(pid,iid)
        region=self.catalog.imports._candidate(manifest,rid)
        if region.get('superseded'):raise SmartImportError('表格区域已被替换，请读取最新解析结果')
        file=next((x for x in manifest['files'] if x['file_id']==region['file_id']),None)
        if not file:raise SmartImportError('未找到区域源文件')
        return region,file,fingerprint(file,region)

    def review(self,pid,iid,rid,decision,reason='',*,confirmed=False):
        self.catalog.writable(pid)
        if not confirmed:raise SmartImportError('需要确认本次区域审阅')
        if decision not in ('accepted','excluded'):raise SmartImportError('只能确认或排除当前区域')
        region,file,key=self._region(pid,iid,rid)
        if decision=='excluded' and not reason.strip():raise SmartImportError('排除区域需填写原因')
        actor=CURRENT_PRINCIPAL.get();user=actor.user_id if actor else 'local'
        with self.catalog._lock,self.catalog._db() as db:
            db.execute('''INSERT INTO import_region_reviews VALUES(?,?,?,?,?,?,?,?)
            ON CONFLICT(project_id,import_id,region_id) DO UPDATE SET
            fingerprint=excluded.fingerprint,decision=excluded.decision,
            actor_id=excluded.actor_id,reason=excluded.reason,updated_at=excluded.updated_at''',
            (pid,iid,rid,key,decision,user,reason[:500],_now()))
        return {'region_id':rid,'decision':decision,'fingerprint':key,'actor_id':user}

    def decision(self,pid,iid,rid):
        region,file,key=self._region(pid,iid,rid)
        with self.catalog._db() as db:
            row=db.execute('''SELECT * FROM import_region_reviews WHERE project_id=?
               AND import_id=? AND region_id=?''',(pid,iid,rid)).fetchone()
        if not row or row['fingerprint']!=key:return {'decision':'pending' if region.get('needs_review') else 'auto_ready',
                    'fingerprint':key,'needs_review':bool(region.get('needs_review'))}
        return {'decision':row['decision'],'fingerprint':key,'needs_review':bool(region.get('needs_review')),
                'updated_at':row['updated_at']}

    def contract(self,pid,iid,rid):
        region,file,key=self._region(pid,iid,rid)
        summary=self.summary.summarize_region(pid,iid,rid)
        if summary['provenance']['fingerprint']!=key:
            raise SmartImportError('数据摘要版本与区域解析指纹不一致')
        source_fields=region.get('source_fields') or []
        fields=[]
        for i,(name,stat) in enumerate(zip(region['columns'],summary['fields'])):
            sf=source_fields[i] if i<len(source_fields) else {}
            fields.append({
                'field_id':f'{rid}:{i}', 'ordinal':i,'technical_name':name,
                'source_name':sf.get('source_name',name),
                'source_cell':sf.get('source_cell'),
                'physical_type':stat['physical_type'], 'physical_type_scope':'sample_inferred',
                'numeric_parse_fraction':stat.get('numeric_parse_fraction'),
                'numeric_parse_scope':'full_nonempty_region',
                'unit':None,'semantic_name':None,'semantic_status':'unconfirmed',
                'availability_time':None, 'business_role':'unknown',
                'evidence_ids':[f'{rid}:field:{i}:full-scan']})
        review=self.decision(pid,iid,rid)
        doc={'contract_version':2,'project_id':pid,'import_id':iid,'region_id':rid,
            'source_sha256':file['sha256'],'fingerprint':key,
            'shape':summary['shape'], 'fields':fields,'review':review,
            'unassigned_regions_in_source':len(self.catalog.require_import(pid,iid).get('unassigned_regions',[])),
            'warnings':summary['quality']['warnings'],'semantic_authority':'human_confirmed_only'}
        with self.catalog._lock,self.catalog._db() as db:
            db.execute('''INSERT INTO import_data_contracts VALUES(?,?,?,?,?,?)
                ON CONFLICT(project_id,import_id,region_id) DO UPDATE SET
                  fingerprint=excluded.fingerprint,contract_json=excluded.contract_json,
                  updated_at=excluded.updated_at''',
                (pid,iid,rid,key,json.dumps(doc,ensure_ascii=False),_now()))
        return doc

    def assert_publishable(self,pid,iid,rids,*,confirm_unassigned=False):
        manifest=self.catalog.require_import(pid,iid)
        unassigned=manifest.get('unassigned_regions') or []
        if unassigned and not confirm_unassigned:
            raise SmartImportError('原文件仍有未归属的非空区域，请核查并明确确认后发布')
        for rid in rids:
            record=self.decision(pid,iid,rid)
            if record['decision']=='excluded':raise SmartImportError('已排除的区域不可发布：'+rid)
            if record['needs_review'] and record['decision']!='accepted':
                raise SmartImportError('表头或边界待核实，请先审阅区域：'+rid)
        return {'publishable':True,'selected_count':len(rids),'unassigned_count':len(unassigned)}
