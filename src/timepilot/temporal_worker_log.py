"""Bounded subprocess stderr/stdout capture for external temporal watchdog.

Logs are private run artifacts, truncated and secret-masked before persistence.
The pipe is always drained; a malicious/noisy plugin cannot exhaust RAM/disk.
"""
from __future__ import annotations

import re
import threading
from collections import deque
from pathlib import Path

from .storage import atomic_write_bytes, sha256_file

MAX_TAIL = 65536
SECRET = re.compile(r'(?i)(\b(?:[a-z0-9_-]{0,40})?(?:api[_-]?key|token|password|secret|authorization)\s*[:=]\s*)([^\s,;]{1,2048})')
BEARER = re.compile(r'(?i)\bBearer\s+[A-Za-z0-9._~+/-]+')


def _scrub(data: bytes) -> bytes:
    text=data.decode('utf-8',errors='replace')
    text=SECRET.sub(r'\1[REDACTED]',text)
    text=BEARER.sub('Bearer [REDACTED]',text)
    return text.encode('utf-8')[-MAX_TAIL:]


class BoundedWorkerLog:
    def __init__(self, stream):
        self.stream=stream
        self._parts=deque()
        self._size=0
        self._total=0
        self._thread=threading.Thread(target=self._capture,daemon=True)
        self._thread.start()

    def _capture(self):
        try:
            while True:
                block=self.stream.read1(4096) if hasattr(self.stream,'read1') else self.stream.read(4096)
                if not block:break
                self._total+=len(block)
                self._parts.append(block)
                self._size+=len(block)
                while self._size>MAX_TAIL and self._parts:
                    chunk=self._parts.popleft()
                    self._size-=len(chunk)
                    if self._size<MAX_TAIL and len(chunk)>MAX_TAIL-self._size:
                        kept=chunk[-(MAX_TAIL-self._size):]
                        self._parts.appendleft(kept)
                        self._size+=len(kept)
        except (OSError,ValueError):
            pass
        finally:
            try:self.stream.close()
            except OSError:pass

    def save(self,run_dir,attempt):
        self._thread.join(timeout=2)
        path=Path(run_dir)/'worker_logs'/f'attempt-{int(attempt)}.txt'
        raw=b''.join(self._parts)
        body=_scrub(raw)
        if self._total>len(raw):body=b'[TRUNCATED worker stdout/stderr; final tail only]\n'+body
        # Publish only redacted text; never retain the raw pipe output.
        atomic_write_bytes(path,body)
        try:path.chmod(0o600)
        except OSError:pass
        return {'attempt':int(attempt),'bytes_observed':self._total,
                'bytes_stored':len(body),'truncated':self._total>len(raw),
                'log_sha256':sha256_file(path),'tail':body.decode('utf-8','replace')[-1800:]}


def read_log(run_dir,attempt):
    path=Path(run_dir)/'worker_logs'/f'attempt-{int(attempt)}.txt'
    if not path.is_file():return None
    raw=path.read_bytes()
    if len(raw)>MAX_TAIL+100:
        raise ValueError('Worker log exceeds bounded log artifact limit')
    return {'attempt':int(attempt),'bytes_stored':len(raw),
            'log_sha256':sha256_file(path),'tail':raw.decode('utf-8','replace')[-1800:]}
