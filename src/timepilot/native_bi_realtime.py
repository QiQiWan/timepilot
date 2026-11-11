"""Bounded in-process Perspective streaming tables for industrial live telemetry.

This channel is intentionally *ephemeral* and separate from immutable Parquet
assets. A durable ingest/log and multi-worker fan-out are future production work.
"""
from __future__ import annotations

import math
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4
from typing import Any

from pydantic import BaseModel, Field, field_validator

SCHEMA = {"event_time": "datetime", "series_id": "string", "value": "float", "status": "string"}
MAX_STREAMS = 8
MAX_EVENTS_PER_BATCH = 500
MAX_ROWS = 100_000


class StreamCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    limit: int = Field(default=20_000, ge=100, le=MAX_ROWS)


class StreamEvent(BaseModel):
    event_time: datetime
    series_id: str = Field(min_length=1, max_length=120)
    value: float
    status: str = Field(default="ok", max_length=80)

    @field_validator('event_time')
    @classmethod
    def must_have_timezone(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.utcoffset() is None:
            raise ValueError('实时事件时间必须包含时区，如 +08:00 或 Z')
        return v

    @field_validator('value')
    @classmethod
    def finite(cls, v: float) -> float:
        if not math.isfinite(v):
            raise ValueError('实时监测值必须是有限数值')
        return v


class StreamAppend(BaseModel):
    events: list[StreamEvent] = Field(min_length=1, max_length=MAX_EVENTS_PER_BATCH)


@dataclass
class LiveStream:
    stream_id: str
    name: str
    table_name: str
    limit: int
    table: Any
    events_received: int = 0

    def public(self):
        return {"stream_id": self.stream_id, "name": self.name, "table_name": self.table_name,
                "limit": self.limit, "events_received": self.events_received, "mode": "ephemeral_stream",
                "persistence": "memory_only", "schema": SCHEMA}


class RealtimeStreamHub:
    def __init__(self):
        self._lock = threading.RLock()
        self._server = None
        self._client = None
        self._streams: dict[str, LiveStream] = {}

    def _ensure(self):
        if self._server is None:
            try:
                import perspective
            except ImportError as exc:
                raise RuntimeError('当前环境缺少 perspective-python，请安装统一 requirements.txt') from exc
            self._server = perspective.Server()
            self._client = self._server.new_local_client()
        return self._server

    def list(self):
        with self._lock:
            return [item.public() for item in self._streams.values()]

    def create(self, request: StreamCreate):
        with self._lock:
            if len(self._streams) >= MAX_STREAMS:
                raise ValueError(f'当前最多允许同时管理 {MAX_STREAMS} 个实时流')
            self._ensure()
            stream_id = str(uuid4())
            table_name = 'tp_live_' + stream_id.replace('-', '')
            table = self._client.table(SCHEMA, limit=request.limit, name=table_name)
            entry = LiveStream(stream_id, request.name, table_name, request.limit, table)
            self._streams[stream_id] = entry
            return entry.public()

    def get(self, stream_id: str):
        with self._lock:
            entry = self._streams.get(stream_id)
            if entry is None:
                raise KeyError('实时流不存在或服务已经重启')
            return entry.public()

    def append(self, stream_id: str, request: StreamAppend):
        with self._lock:
            entry = self._streams.get(stream_id)
            if entry is None:
                raise KeyError('实时流不存在或服务已经重启')
            rows = [{"event_time": int(event.event_time.astimezone(timezone.utc).timestamp() * 1000),
                     "series_id": event.series_id, "value": event.value, "status": event.status}
                    for event in request.events]
            entry.table.update(rows)
            entry.events_received += len(rows)
            return {**entry.public(), "appended": len(rows),
                    "retained_at_most": min(entry.limit, entry.events_received)}

    def server_and_table(self, stream_id: str):
        with self._lock:
            info = self.get(stream_id)
            return self._ensure(), info['table_name']

    def delete(self, stream_id: str):
        with self._lock:
            item = self._streams.pop(stream_id, None)
            if item is None:
                raise KeyError('实时流不存在')
            item.table.delete()
            return {"deleted": stream_id}
