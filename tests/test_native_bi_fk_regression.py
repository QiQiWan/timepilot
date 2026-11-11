"""Regression: registering asset+head must pass SQLite real FK enforcement."""
import json
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from timepilot.api import create_app
from timepilot.db import NativeBIAssetHeadRow, NativeBIAssetRow


def _record(asset_id: str) -> NativeBIAssetRow:
    return NativeBIAssetRow(
        id=asset_id, name="fk-regression", row_count=1,
        columns_json=json.dumps([{"name": "value", "physical_type": "INTEGER"}]),
        lineage_json='{"type": "test"}', sha256="0" * 64, file_bytes=10,
    )


def test_asset_head_creation_is_atomic_and_fk_valid(tmp_path):
    app = create_app(home=tmp_path, embedded_worker=False)
    native = app.state.native_bi
    asset_id = str(uuid4())
    with native.store.sessions() as s:
        assert s.execute(text("PRAGMA foreign_keys")).scalar_one() == 1

    native._persist_asset_metadata(_record(asset_id))

    with native.store.sessions() as s:
        parent = s.get(NativeBIAssetRow, asset_id)
        head = s.get(NativeBIAssetHeadRow, asset_id)
        assert parent is not None
        assert head is not None and head.version_id is None
        assert not s.execute(text("PRAGMA foreign_key_check")).fetchall()


def test_asset_head_insert_rolls_back_parent_on_failure(tmp_path):
    app = create_app(home=tmp_path, embedded_worker=False)
    native = app.state.native_bi
    asset_id = str(uuid4())
    native._persist_asset_metadata(_record(asset_id))

    with pytest.raises(IntegrityError):
        native._persist_asset_metadata(_record(asset_id))

    with native.store.sessions() as s:
        assert s.scalar(select(NativeBIAssetRow).where(NativeBIAssetRow.id == asset_id)) is not None
        assert len(s.scalars(select(NativeBIAssetHeadRow).where(NativeBIAssetHeadRow.asset_id == asset_id)).all()) == 1
        assert not s.execute(text("PRAGMA foreign_key_check")).fetchall()
