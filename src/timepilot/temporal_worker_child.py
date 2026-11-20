"""Trusted local CLI for executing a single already-claimed temporal run.

Never accepts arbitrary paths, Python code, or unapproved model commands. The
outer RuntimeEngine verifies the claim before starting this process.
"""
from __future__ import annotations

import argparse
import os
import threading


def main(argv=None):
    parser=argparse.ArgumentParser()
    parser.add_argument('--home',required=True)
    parser.add_argument('--run',required=True)
    parser.add_argument('--worker',required=True)
    parser.add_argument('--attempt',required=True,type=int)
    args=parser.parse_args(argv)
    from timepilot.config import Settings
    from timepilot.db import Store
    from timepilot.runtime import RuntimeEngine
    settings=Settings.from_env(args.home)
    store=Store(settings.db_url)
    engine=RuntimeEngine(settings,store,worker_id=args.worker)
    engine._active_attempts[args.run]=args.attempt
    stop=threading.Event()
    parent=os.getppid()
    def parent_watch():
        # If the outer watchdog unexpectedly disappears, no orphan training
        # process may keep writing results under an unobserved lease.
        while not stop.wait(.5):
            if os.name=='posix' and os.getppid()!=parent:
                os._exit(89)
            try:
                import psutil
                if not psutil.pid_exists(parent):os._exit(89)
            except ImportError:
                pass
    threading.Thread(target=parent_watch,daemon=True).start()
    try:
        engine._execute_local(args.run)
        return 0
    finally:
        stop.set()
        store.engine.dispose()


if __name__=='__main__':
    raise SystemExit(main())
