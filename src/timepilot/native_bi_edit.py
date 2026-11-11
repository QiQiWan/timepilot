"""TimePilot Native BI sparse revisions (v0.5.2).

Immutable Parquet base + append-only CSV delta files + version graph in SQLAlchemy.
All table reads (ordinary queries, Arrow views, and forecasting) go through the
same effective DuckDB relation. No edit rewrites or mutates the source Parquet.

Local single-process prototype: serialized writes in-process; for multi-process
enterprise use, replace the head update with a DB row lock/CAS and add RBAC.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
import re
import secrets
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator, field_validator
from sqlalchemy import select, update

from timepilot.db import (NativeBIAssetHeadRow, NativeBIEditPreviewRow,
                          NativeBIEditVersionRow)
from timepilot.storage import sha256_file

BASE_VERSION = "base"
MAX_BATCH_CELLS = 5000
MAX_HISTORY_DEPTH = 300
MAX_PREVIEW_BYTES = 2 * 1024 * 1024
SAFE_SQL_TYPE = re.compile(r"^[A-Z][A-Z0-9_,() ]{0,110}$")


def fail(message: str):
    from timepilot.native_bi import NativeBIError
    raise NativeBIError(message)


def _safe_literal(text: str) -> str:
    return "'" + str(text).replace("'", "''") + "'"


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':'), allow_nan=False).encode()).hexdigest()


class CellEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    row_id: int = Field(ge=0)
    column: str = Field(min_length=1, max_length=200)
    value: str | int | float | bool | None

    @field_validator('value')
    @classmethod
    def finite(cls, value):
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError('不能写入 NaN 或无穷大')
        return value


class EditBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    asset_id: str
    expected_version: str = BASE_VERSION
    edits: list[CellEdit] = Field(min_length=1, max_length=MAX_BATCH_CELLS)
    comment: str = Field(default="批量编辑", max_length=240)

    @model_validator(mode='after')
    def no_duplicates(self):
        coords = [(x.row_id, x.column) for x in self.edits]
        if len(coords) != len(set(coords)):
            raise ValueError('同一批次中不能重复修改同一个单元格')
        if len(json.dumps(self.model_dump(mode='json'), ensure_ascii=False).encode()) > MAX_PREVIEW_BYTES:
            raise ValueError('本次编辑超过 2 MB 上限，请分批提交')
        return self


class EditCommit(BaseModel):
    model_config = ConfigDict(extra='forbid')
    batch: EditBatch
    preview_token: str = Field(min_length=20, max_length=200)


class RollbackRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_version: str
    target_version: str
    comment: str = Field(default="回滚数据版本", max_length=240)
    confirmed: bool = False


class UndoRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_version: str
    confirmed: bool = False


class NativeBIEditMixin:
    """Exposes preview/commit/history/undo/rollback without entangling legacy BI."""

    def _version(self, asset_id: str, version_id: str):
        if version_id == BASE_VERSION:
            return None
        try:
            canonical = str(UUID(version_id))
        except (ValueError, TypeError):
            fail('数据版本 ID 无效')
        with self.store.sessions() as session:
            row = session.get(NativeBIEditVersionRow, canonical)
            if row is None or row.asset_id != asset_id:
                fail('指定的数据版本不存在或不属于此数据资产')
            return row

    def _head(self, asset_id: str) -> str:
        with self.store.sessions() as session:
            row = session.get(NativeBIAssetHeadRow, asset_id)
            return row.version_id if row and row.version_id else BASE_VERSION

    def _versions_chain(self, asset_id: str, version_id: str) -> list:
        """Oldest-first ancestry; a rollback points to its target revision."""
        if version_id == BASE_VERSION:
            return []
        chain = []
        visited = set()
        current = version_id
        while current != BASE_VERSION:
            if current in visited or len(chain) >= MAX_HISTORY_DEPTH:
                fail('版本链包含循环或超过最大深度，请先维护数据版本')
            visited.add(current)
            row = self._version(asset_id, current)
            chain.append(row)
            current = row.parent_id or BASE_VERSION
        return list(reversed(chain))

    def _delta_path(self, asset_id: str, version_id: str) -> Path:
        return self.home / str(UUID(asset_id)) / 'patches' / (str(UUID(version_id)) + '.csv')

    def _effective_sql(self, asset_id: str, version_id: str, columns: list[dict]) -> str:
        """Read-only overlay. The base and all delta paths are server-resolved UUID paths."""
        path = self._path(asset_id)
        base_sql = 'read_parquet(' + _safe_literal(str(path)) + ')'
        chain = self._versions_chain(asset_id, version_id)
        patches = [row for row in chain if row.kind == 'edit' and row.delta_sha256]
        if not patches:
            return 'SELECT * FROM ' + base_sql
        all_fields = sorted({name for row in patches for name in json.loads(row.fields_json)})
        allowed = {col['name']: col['physical_type'].upper() for col in columns}
        for field in all_fields:
            if field not in allowed:
                fail('数据修订包含不存在的字段')
        for revision in patches:
            patch_file = self._delta_path(asset_id, revision.id)
            if not patch_file.is_file() or sha256_file(patch_file) != revision.delta_sha256:
                fail(f'版本 {revision.id} 的增量文件缺失或校验失败，已阻止读取')
        # SQL identifiers and literals are separately escaped. No user SQL enters here.
        paths = ', '.join(_safe_literal(str(self._delta_path(asset_id, row.id))) for row in patches)
        delta_source = (f'read_csv([{paths}], header=true, all_varchar=true, '
                        "null_padding=false)")
        ctes = [
            '__tp_ranked AS ('
            'SELECT CAST(row_id AS BIGINT) AS row_id, column_name, value, is_null, '
            'ROW_NUMBER() OVER (PARTITION BY row_id, column_name ORDER BY '
            'CAST(depth AS BIGINT) DESC) AS __rank '
            f'FROM {delta_source})',
            '__tp_latest AS (SELECT row_id, column_name, value, is_null FROM __tp_ranked WHERE __rank=1)',
        ]
        joins = []
        selects = ['b."__tp_rowid"']
        from timepilot.native_bi import quote_identifier
        for ix, col in enumerate(columns):
            name, kind = col['name'], col['physical_type'].upper()
            quoted = quote_identifier(name)
            if name not in all_fields:
                selects.append(f'b.{quoted}')
                continue
            if not SAFE_SQL_TYPE.fullmatch(kind) or any(x in kind for x in ('STRUCT', 'UNION', 'MAP', 'LIST', 'ARRAY')):
                fail(f'复杂类型 {kind} 暂不支持增量编辑')
            alias = f'__edit_{ix}'
            joins.append('LEFT JOIN (SELECT row_id, value, is_null FROM __tp_latest WHERE '
                         f'column_name={_safe_literal(name)}) {alias} '
                         f'ON b."__tp_rowid"={alias}.row_id')
            selects.append(f'CASE WHEN {alias}.row_id IS NULL THEN b.{quoted} '
                           f'WHEN {alias}.is_null=\'1\' THEN CAST(NULL AS {kind}) '
                           f'ELSE CAST({alias}.value AS {kind}) END AS {quoted}')
        return ('WITH ' + ', '.join(ctes) + ' SELECT ' + ', '.join(selects) +
                ' FROM ' + base_sql + ' b ' + ' '.join(joins))

    def _install_effective_view(self, con, asset_id: str, version_id: str, columns: list[dict]) -> None:
        source_sql = self._effective_sql(asset_id, version_id, columns)
        con.execute('CREATE OR REPLACE TEMP VIEW __tp_effective AS ' + source_sql)

    def _validate_batch(self, batch: EditBatch, con=None) -> tuple[dict, list[str]]:
        from timepilot.native_bi import NativeBIError, quote_identifier
        meta = self.asset(batch.asset_id)
        expected = batch.expected_version or BASE_VERSION
        if self._head(batch.asset_id) != expected:
            fail('数据已经被其他操作更新，请刷新后重新预览')
        self._version(batch.asset_id, expected)
        schema = {item['name']: item['physical_type'].upper() for item in meta['columns']}
        fields = sorted({item.column for item in batch.edits})
        for cell in batch.edits:
            if cell.column not in schema:
                fail(f'字段不存在或不可编辑：{cell.column}')
            if cell.row_id >= meta['row_count']:
                fail(f'行编号超出范围：{cell.row_id}')
            kind = schema[cell.column]
            if not SAFE_SQL_TYPE.fullmatch(kind) or any(x in kind for x in ('STRUCT','MAP','LIST','ARRAY','UNION')):
                fail(f'字段 {cell.column} 的类型暂不支持批量编辑：{kind}')
            if cell.value is not None and con is not None:
                # DuckDB TRY_CAST checks actual convertibility before committing a patch.
                parsed = con.execute(f'SELECT TRY_CAST(? AS {kind})', [str(cell.value)]).fetchone()[0]
                if parsed is None:
                    fail(f'第 {cell.row_id+1} 行的 {cell.column} 无法转换为 {kind}')
        return meta, fields

    def preview_edit(self, batch: EditBatch) -> dict:
        from timepilot.native_bi import quote_identifier, safe_json
        with self._lock:
            # Basic field and version failures are reported even if DuckDB is not installed.
            self._validate_batch(batch)
            with self._connection() as con:
                meta, fields = self._validate_batch(batch, con)
                self._install_effective_view(con, batch.asset_id, batch.expected_version, meta['columns'])
                ids = sorted({edit.row_id for edit in batch.edits})
                rows = con.execute('SELECT * FROM __tp_effective WHERE "__tp_rowid" IN ('+
                                   ','.join('?' for _ in ids)+') ORDER BY "__tp_rowid"', ids).fetchall()
                cols = [d[0] for d in con.description]
                old = {int(row[0]): dict(zip(cols, row)) for row in rows}
                examples = [
                    {'row_id': edit.row_id, 'column': edit.column,
                     'before': safe_json(old[edit.row_id][edit.column]), 'after': edit.value}
                    for edit in batch.edits[:20]
                ]
            token = secrets.token_urlsafe(32)
            expires = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=10)
            with self.store.sessions.begin() as session:
                session.add(NativeBIEditPreviewRow(
                    token_digest=hashlib.sha256(token.encode()).hexdigest(), asset_id=batch.asset_id,
                    based_on_version=batch.expected_version, edits_digest=_hash(batch.model_dump(mode='json')),
                    expires_at=expires))
            return {'asset_id': batch.asset_id, 'base_version': batch.expected_version,
                    'affected_rows': len(ids), 'affected_cells': len(batch.edits),
                    'edited_fields': fields, 'examples': examples,
                    'preview_token': token, 'expires_at': expires.isoformat()+'Z',
                    'base_parquet_rewritten': False}

    def commit_edit(self, request: EditCommit) -> dict:
        batch = request.batch
        token_digest = hashlib.sha256(request.preview_token.encode()).hexdigest()
        payload_digest = _hash(batch.model_dump(mode='json'))
        with self._lock:
            # All validation precedes writing; a stale token cannot overwrite a newer head.
            with self.store.sessions() as session:
                preview = session.get(NativeBIEditPreviewRow, token_digest)
                now = datetime.now(timezone.utc).replace(tzinfo=None)
                if (not preview or preview.asset_id != batch.asset_id or preview.used
                    or preview.based_on_version != batch.expected_version
                    or preview.edits_digest != payload_digest or preview.expires_at <= now):
                    fail('预览凭证不存在、已失效或操作内容发生变化，请重新预览')
            if self._head(batch.asset_id) != batch.expected_version:
                fail('源数据版本已经发生变化，请刷新并重新预览')
            with self._connection() as con:
                meta, fields = self._validate_batch(batch, con)
                # Verify each editable cell against the effective version, not an older base snapshot.
                self._install_effective_view(con, batch.asset_id, batch.expected_version, meta['columns'])
                ids = sorted({x.row_id for x in batch.edits})
                got = {row[0] for row in con.execute('SELECT "__tp_rowid" FROM __tp_effective '
                         'WHERE "__tp_rowid" IN ('+','.join('?' for _ in ids)+')', ids).fetchall()}
                if got != set(ids):
                    fail('修改目标行已不存在')
            parent = self._version(batch.asset_id, batch.expected_version)
            depth = parent.depth+1 if parent else 1
            if depth >= MAX_HISTORY_DEPTH:
                fail('数据版本链过长，请先归档或压实历史版本')
            revision_id = str(uuid4())
            file = self._delta_path(batch.asset_id, revision_id)
            file.parent.mkdir(exist_ok=True, parents=True)
            temp_file = file.with_suffix('.building')
            try:
                with temp_file.open('w', encoding='utf-8', newline='') as stream:
                    writer = csv.writer(stream)
                    writer.writerow(['row_id','column_name','value','is_null','depth'])
                    for item in batch.edits:
                        writer.writerow([item.row_id,item.column,'' if item.value is None else str(item.value),
                                         '1' if item.value is None else '0',depth])
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temp_file, file)
                digest = sha256_file(file)
                with self.store.sessions.begin() as session:
                    preview = session.get(NativeBIEditPreviewRow, token_digest)
                    state = session.get(NativeBIAssetHeadRow, batch.asset_id)
                    head = state.version_id if state and state.version_id else BASE_VERSION
                    if not preview or preview.used or head != batch.expected_version:
                        fail('提交期间数据版本已更新或预览凭证已使用')
                    preview.used = True
                    session.add(NativeBIEditVersionRow(
                        id=revision_id, asset_id=batch.asset_id,
                        parent_id=None if batch.expected_version == BASE_VERSION else batch.expected_version,
                        previous_head_id=None if head == BASE_VERSION else head,
                        kind='edit', depth=depth, delta_sha256=digest, edit_count=len(batch.edits),
                        fields_json=json.dumps(fields, ensure_ascii=False), comment=batch.comment))
                    if state:
                        # DB-level optimistic compare-and-swap: protects against another API process.
                        expected_db = None if head == BASE_VERSION else head
                        moved = session.execute(update(NativeBIAssetHeadRow).where(
                            NativeBIAssetHeadRow.asset_id == batch.asset_id,
                            NativeBIAssetHeadRow.version_id == expected_db
                        ).values(version_id=revision_id, updated_at=datetime.now(timezone.utc)))
                        if moved.rowcount != 1:
                            fail('提交期间另一个进程已经更新数据版本')
                    else:
                        # Existing legacy assets without a head row are initialized on first commit.
                        session.add(NativeBIAssetHeadRow(asset_id=batch.asset_id, version_id=revision_id))
                return {'asset_id':batch.asset_id,'version_id':revision_id,
                        'previous_version':batch.expected_version,'changed_cells':len(batch.edits),
                        'base_parquet_rewritten':False,'delta_sha256':digest}
            except Exception:
                temp_file.unlink(missing_ok=True)
                file.unlink(missing_ok=True)
                raise

    def history(self, asset_id: str) -> dict:
        self.asset(asset_id)
        head = self._head(asset_id)
        with self.store.sessions() as session:
            records = list(session.scalars(select(NativeBIEditVersionRow)
                .where(NativeBIEditVersionRow.asset_id == asset_id)
                .order_by(NativeBIEditVersionRow.created_at.desc()).limit(300)))
        return {'asset_id':asset_id,'current_version':head,'versions':[
            {'version_id':v.id,'parent_version':v.parent_id or BASE_VERSION,
             'previous_head':v.previous_head_id or BASE_VERSION,
             'kind':v.kind,'depth':v.depth,'changed_cells':v.edit_count,'comment':v.comment,
             'created_at':v.created_at.isoformat() if v.created_at else None,
             'is_current':v.id==head} for v in records],
             'base_version':BASE_VERSION}

    def _move_head(self, asset_id: str, expected: str, target: str, kind: str, comment: str) -> dict:
        with self._lock:
            self.asset(asset_id)
            if self._head(asset_id) != expected:
                fail('数据版本已经更新，请刷新操作历史')
            if expected == target:
                fail('当前版本与目标版本相同，无需执行回滚')
            parent = self._version(asset_id, target)
            if parent and parent.depth+1 >= MAX_HISTORY_DEPTH:
                fail('版本链过长，拒绝创建新版本')
            new_id = str(uuid4())
            with self.store.sessions.begin() as session:
                state = session.get(NativeBIAssetHeadRow, asset_id)
                head = state.version_id if state and state.version_id else BASE_VERSION
                if head != expected:
                    fail('并发版本冲突，请刷新')
                session.add(NativeBIEditVersionRow(
                    id=new_id, asset_id=asset_id, parent_id=None if target==BASE_VERSION else target,
                    previous_head_id=None if expected==BASE_VERSION else expected,
                    kind=kind, depth=(parent.depth+1 if parent else 1),
                    fields_json='[]', comment=comment))
                if state:
                    expected_db = None if expected == BASE_VERSION else expected
                    moved = session.execute(update(NativeBIAssetHeadRow).where(
                        NativeBIAssetHeadRow.asset_id == asset_id,
                        NativeBIAssetHeadRow.version_id == expected_db
                    ).values(version_id=new_id, updated_at=datetime.now(timezone.utc)))
                    if moved.rowcount != 1:
                        fail('版本已被其他进程更改，回滚已取消')
                else:
                    session.add(NativeBIAssetHeadRow(asset_id=asset_id, version_id=new_id))
            return {'asset_id':asset_id,'version_id':new_id,'restored_version':target,
                    'previous_version':expected,'kind':kind,'base_parquet_rewritten':False}

    def rollback(self, asset_id: str, req: RollbackRequest) -> dict:
        if not req.confirmed:
            fail('需要明确确认回滚操作')
        return self._move_head(asset_id, req.expected_version, req.target_version,
                               'rollback', req.comment)

    def undo(self, asset_id: str, req: UndoRequest) -> dict:
        if not req.confirmed:
            fail('需要明确确认撤销操作')
        if req.expected_version == BASE_VERSION:
            fail('当前为原始数据版本，无可撤销操作')
        latest = self._version(asset_id, req.expected_version)
        target = (latest.previous_head_id or BASE_VERSION) if latest.kind in {'rollback','undo'} \
                 else (latest.parent_id or BASE_VERSION)
        return self._move_head(asset_id, req.expected_version, target, 'undo', '撤销上一步操作')

    def verify_history(self, asset_id: str) -> dict:
        """Recalculate base and *all* recorded patch hashes (including inactive branches)."""
        base = self.verify(asset_id)
        failures = []
        with self.store.sessions() as s:
            revisions = list(s.scalars(select(NativeBIEditVersionRow)
                .where(NativeBIEditVersionRow.asset_id == asset_id)))
        for item in revisions:
            if item.kind == 'edit':
                path = self._delta_path(asset_id, item.id)
                if not path.is_file() or sha256_file(path) != item.delta_sha256:
                    failures.append(item.id)
        return {'asset_id':asset_id,'base_valid':base['valid'],
                'patches_checked':sum(x.kind=='edit' for x in revisions),
                'invalid_versions':failures,'valid':base['valid'] and not failures}
