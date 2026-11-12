"""Project-scoped, immutable data-contract revisions for native assets."""
from __future__ import annotations

from timepilot.bi_query_engine import BIQueryEngine
from timepilot.feature_contracts import DataContract, FieldContract, stable_field_id, canonical
from timepilot.enterprise_security import CURRENT_PRINCIPAL, SecurityError
from timepilot.projects import now
from uuid import uuid4
import json


class FeatureSemanticService:
    def __init__(self, projects, native):
        self.projects=projects
        self.native=native
        self.engine=BIQueryEngine(projects,native)

    @staticmethod
    def actor():
        user=CURRENT_PRINCIPAL.get()
        return user.user_id if user else 'local'

    def source(self,pid,aid,vid):
        return self.engine.checked(pid,aid,vid)

    def _guard_write(self,pid):
        self.projects.writable(pid)
        if self.projects.security is not None:
            principal=CURRENT_PRINCIPAL.get()
            if principal is None:raise SecurityError('需要登录后修改数据语义')
            self.projects.security.allowed_project(principal,pid,required='editor')

    def latest(self,pid,aid,vid='base', *, source=None):
        meta=source or self.source(pid,aid,vid)
        with self.projects._db() as db:
            row=db.execute('''SELECT contract_json FROM feature_data_contracts
               WHERE project_id=? AND asset_id=? AND version_id=? ORDER BY revision DESC LIMIT 1''',(pid,aid,vid)).fetchone()
        if row:
            contract=DataContract.model_validate_json(row['contract_json'])
            if contract.source_sha256 != meta['sha256']:
                return contract.model_copy(update={'state':'stale'})
            return contract
        columns=meta['columns']
        # Preserve IDs for unchanged physical columns across asset revisions.
        # Older contracts used version-specific hashes; reuse those exact IDs
        # when source name+position are unchanged. Never copy inferred roles or
        # units automatically to a changed source without a fresh confirmation.
        with self.projects._db() as db:
            historic=db.execute('''SELECT contract_json FROM feature_data_contracts
                WHERE project_id=? AND asset_id=? AND version_id<>?
                ORDER BY created_at DESC,revision DESC LIMIT 40''',(pid,aid,vid)).fetchall()
        aliases={}
        for previous in historic:
            try:
                old_doc=DataContract.model_validate_json(previous['contract_json'])
                for f in old_doc.fields:
                    aliases.setdefault((f.source_column_index,f.source_name),f.field_id)
            except (ValueError,KeyError):
                continue
        fields=[]
        for i,column in enumerate(columns):
            typ=str(column.get('physical_type') or 'VARCHAR').upper()
            semantic='numeric' if typ.startswith(('DOUBLE','FLOAT','DECIMAL','HUGEINT','BIGINT','INT','SMALLINT','TINYINT','REAL','UINTEGER')) else 'unknown'
            source_name=column['name']
            fid=aliases.get((i,source_name)) or stable_field_id(aid,'logical',i,source_name)
            fields.append(FieldContract(field_id=fid,
                 source_name=source_name,source_column_index=i,physical_type=typ,
                 semantic_type=semantic,source_location=column.get('source_location')))
        return DataContract(asset_id=aid,version_id=vid,source_sha256=meta['sha256'],
                            fields=fields,state='detected')

    def patch(self,pid,aid,vid,patch,expected_revision):
        self._guard_write(pid)
        meta=self.source(pid,aid,vid)
        current=self.latest(pid,aid,vid,source=meta)
        if current.state=='stale':raise ValueError('来源数据指纹变化，不能修改过期契约')
        if current.revision!=expected_revision:raise ValueError('数据契约修订冲突，请刷新后重试')
        # The source name, type, position, field id and original source hash are immutable.
        allowed={'display_name','semantic_type','role','unit','timezone','classification','confirmation','confidence','evidence'}
        updates=patch.get('fields') or []
        if len(updates)>len(current.fields):raise ValueError('字段更新数量超过当前源字段')
        mapping={x.field_id:x for x in current.fields}
        for item in updates:
            fid=item.get('field_id')
            if fid not in mapping or set(item)-allowed-{'field_id'}:
                raise ValueError('只允许修改授权字段的语义属性')
            mapping[fid]=FieldContract.model_validate({**mapping[fid].model_dump(mode='json'),
                        **{key:val for key,val in item.items() if key!='field_id'}})
        doc=current.model_dump(mode='json')
        doc['fields']=[mapping[f.field_id].model_dump(mode='json') for f in current.fields]
        for key in ('entity_field_ids','event_time_field_id','available_at_field_id','timezone','duplicate_policy'):
            if key in patch:doc[key]=patch[key]
        if set(patch)-{'fields','entity_field_ids','event_time_field_id','available_at_field_id','timezone','duplicate_policy'}:
            raise ValueError('数据契约包含不允许编辑的属性')
        doc['revision']=expected_revision+1
        doc['state']='needs_review'
        candidate=DataContract.model_validate(doc)
        self._write(pid,candidate)
        return candidate

    def confirm(self,pid,aid,vid,expected_revision):
        self._guard_write(pid)
        current=self.latest(pid,aid,vid)
        if current.state=='stale' or current.revision!=expected_revision:
            raise ValueError('数据契约已经过期，请刷新')
        if current.revision==0:raise ValueError('先确认必要字段含义与时间类型')
        if current.event_time_field_id:
            field=next(f for f in current.fields if f.field_id==current.event_time_field_id)
            if field.confirmation!='confirmed':raise ValueError('真实时间字段仍未被确认')
        if current.available_at_field_id:
            field=next(f for f in current.fields if f.field_id==current.available_at_field_id)
            if field.confirmation!='confirmed':raise ValueError('数据可用时间字段仍未被确认')
        doc=current.model_dump(mode='json')
        doc['revision']+=1;doc['state']='confirmed'
        candidate=DataContract.model_validate(doc)
        self._write(pid,candidate)
        return candidate

    def _write(self,pid,contract):
        with self.projects._lock,self.projects._db() as db:
            latest=db.execute('''SELECT MAX(revision) AS n FROM feature_data_contracts
                 WHERE project_id=? AND asset_id=? AND version_id=?''',
                (pid,contract.asset_id,contract.version_id)).fetchone()['n'] or 0
            if latest!=contract.revision-1:
                raise ValueError('契约已由其他用户修改')
            db.execute('''INSERT INTO feature_data_contracts
              (contract_id,project_id,asset_id,version_id,revision,source_sha256,state,contract_json,actor_id,created_at)
              VALUES (?,?,?,?,?,?,?,?,?,?)''',
              (str(uuid4()),pid,contract.asset_id,contract.version_id,contract.revision,
               contract.source_sha256,contract.state,canonical(contract),self.actor(),now()))

    def get_revision(self,pid,aid,vid,revision):
        self.source(pid,aid,vid)
        with self.projects._db() as db:
            row=db.execute('''SELECT contract_json FROM feature_data_contracts WHERE project_id=?
                AND asset_id=? AND version_id=? AND revision=?''',(pid,aid,vid,revision)).fetchone()
        if row is None:raise KeyError('契约修订号不存在')
        return DataContract.model_validate_json(row['contract_json'])

    def search(self,pid,aid,vid,q='',limit=50):
        contract=self.latest(pid,aid,vid)
        text=q.casefold().strip()
        found=[f for f in contract.fields if text in f.source_name.casefold() or
               text in (f.display_name or '').casefold() or (text.startswith('fld_') and text in f.field_id.casefold())]
        return {'fields':[f.model_dump(mode='json') for f in found[:max(1,min(int(limit),100))]],
                'total':len(found),'contract_revision':contract.revision}
