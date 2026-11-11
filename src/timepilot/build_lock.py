"""Cross-process lock for first-run JS/WASM compilation (macOS/Linux/Windows)."""
from __future__ import annotations
import os
import time
from contextlib import contextmanager
from pathlib import Path

@contextmanager
def exclusive_build_lock(path: Path, timeout: int = 180):
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    started=time.monotonic()
    while True:
        try:
            fd=os.open(str(path), os.O_CREAT|os.O_EXCL|os.O_WRONLY, 0o600)
            break
        except FileExistsError:
            # A dead builder cannot permanently block recovery.
            try:
                if time.time()-path.stat().st_mtime > 900:
                    path.unlink(missing_ok=True)
                    continue
            except FileNotFoundError:
                continue
            if time.monotonic()-started>timeout:
                raise TimeoutError('Another Perspective build is still running')
            time.sleep(0.3)
    try:
        with os.fdopen(fd,'w') as f:
            f.write(f'{os.getpid()} {time.time()}\n')
        yield
    finally:
        path.unlink(missing_ok=True)
