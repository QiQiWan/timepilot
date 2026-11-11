"""Regression tests for the Perspective 5.6.1 DuckDB table identity + WS lifecycle."""
from __future__ import annotations

import asyncio
import threading
import sys
from types import ModuleType, SimpleNamespace

import pytest

from timepilot.native_bi import NativeBIError
from timepilot.native_bi_api import (
    PERSPECTIVE_ROOT_TABLE, _TimedPerspectiveExecutor,
    create_timepilot_virtual_server, prepare_perspective_source,
)


class _FakeDuckDB:
    def __init__(self, tables):
        self.tables = tables
        self.statements = []
        self.interrupted = 0

    def execute(self, statement):
        self.statements.append(statement)
        return self

    def fetchall(self):
        return self.tables

    def interrupt(self):
        self.interrupted += 1


def test_perspective_view_registry_keeps_lazy_revision_and_returns_qualified_name():
    db = _FakeDuckDB([('memory', 'main', 'timepilot_active', ['sales'], ['DOUBLE'], False)])
    actual = prepare_perspective_source(db, 'SELECT * FROM read_parquet(\'/tmp/data.parquet\')')
    assert actual == 'memory.timepilot_active'
    assert db.statements[0] == 'SET default_null_order=NULLS_FIRST_ON_ASC_LAST_ON_DESC'
    assert db.statements[1].startswith('CREATE VIEW timepilot_active AS SELECT * FROM (')
    assert db.statements[2] == 'SELECT "__tp_rowid" FROM timepilot_active LIMIT 0'
    assert 'CREATE TABLE' not in db.statements[1]
    assert db.statements[-1] == 'SHOW ALL TABLES'


def test_perspective_view_registry_refuses_missing_or_ambiguous_source():
    with pytest.raises(NativeBIError, match='无法唯一识别'):
        prepare_perspective_source(_FakeDuckDB([]), 'SELECT 1')
    with pytest.raises(NativeBIError, match='无法唯一识别'):
        prepare_perspective_source(_FakeDuckDB([
            ('memory', 'main', PERSPECTIVE_ROOT_TABLE),
            ('other', 'main', PERSPECTIVE_ROOT_TABLE),
        ]), 'SELECT 1')


def test_perspective_executor_reports_exception_and_closes():
    db = _FakeDuckDB([])
    executor = _TimedPerspectiveExecutor(db)
    try:
        assert executor.submit(lambda a, b: a + b, 2, 3).result(timeout=2) == 5
        with pytest.raises(ValueError, match='request failed'):
            executor.submit(lambda: (_ for _ in ()).throw(ValueError('request failed'))).result(timeout=2)
    finally:
        executor.shutdown()


def test_perspective_virtual_server_uses_explicit_snapshot_row_key(monkeypatch):
    """Test the SQL builder configuration without substituting for the live test."""
    fake_perspective = ModuleType('perspective')
    fake_virtual_servers = ModuleType('perspective.virtual_servers')
    fake_duckdb = ModuleType('perspective.virtual_servers.duckdb')

    class FakeModel:
        def __init__(self, config):
            self.config = config

    class FakeVirtualServer:
        def __init__(self, handler):
            self.handler = handler

    class FakeDuckDBHandler:
        def __init__(self, db):
            self.db = db
            self.sql_builder = FakeModel({})

    class FakeDuckDBSession:
        def __init__(self, callback, db):
            self.callback = callback
            self.session = FakeVirtualServer(FakeDuckDBHandler(db))

        def close(self):
            self.session = None

    class FakeDuckDBServer:
        def __init__(self, db):
            self.db = db

        def new_session(self, callback):
            return FakeDuckDBSession(callback, self.db)

    fake_perspective.GenericSQLVirtualServerModel = FakeModel
    fake_perspective.VirtualServer = FakeVirtualServer
    fake_duckdb.DuckDBVirtualServer = FakeDuckDBServer
    fake_duckdb.DuckDBVirtualServerHandler = FakeDuckDBHandler
    fake_duckdb.DuckDBVirtualSession = FakeDuckDBSession
    for name, module in (
        ('perspective', fake_perspective),
        ('perspective.virtual_servers', fake_virtual_servers),
        ('perspective.virtual_servers.duckdb', fake_duckdb),
    ):
        monkeypatch.setitem(sys.modules, name, module)
    connection = object()
    server = create_timepilot_virtual_server(connection)
    assert isinstance(server, FakeDuckDBServer)
    session = server.new_session(lambda msg: None)
    assert isinstance(session, FakeDuckDBSession)
    assert session.session.handler.db is connection
    assert session.session.handler.sql_builder.config == {'row_id_expr': '"__tp_rowid"'}
    session.close()
