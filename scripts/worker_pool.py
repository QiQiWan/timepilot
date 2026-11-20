"""Run the five existing durable job types in five isolated OS processes.

Start the API separately with --no-worker. The parent does not restart crashed
workers blindly: existing job leases decide which work may be replayed. A failed
role causes a nonzero exit and an orderly stop of its siblings.
"""
from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROLES = ('forecast', 'agent', 'upload', 'publication', 'feature')


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--poll-seconds', type=float, default=.75)
    parser.add_argument('--max-rss-mib', type=float, default=None)
    args = parser.parse_args(argv)
    if not .1 <= args.poll_seconds <= 60:
        parser.error('poll_seconds must be in [0.1, 60]')
    if args.max_rss_mib is not None and not 128 <= args.max_rss_mib <= 1048576:
        parser.error('max_rss_mib must be >= 128 MiB')
    env = os.environ.copy()
    env['PYTHONPATH'] = str(ROOT / 'src') + os.pathsep + env.get('PYTHONPATH', '')
    workers = []
    stopping = False
    def on_signal(_signum, _frame):
        nonlocal stopping
        stopping = True
    previous = {number: signal.getsignal(number) for number in (signal.SIGINT, signal.SIGTERM)}
    for number in previous:
        signal.signal(number, on_signal)
    code = 0
    try:
        for role in ROLES:
            cmd = [sys.executable, '-m', 'timepilot.worker_entrypoint', '--role', role,
                   '--poll-seconds', str(args.poll_seconds), '--parent-pid', str(os.getpid())]
            if args.max_rss_mib is not None:
                cmd += ['--max-rss-mib', str(args.max_rss_mib)]
            workers.append((role, subprocess.Popen(cmd, cwd=ROOT, env=env)))
        print('TimePilot worker pool: 5 independent processes started', flush=True)
        while not stopping:
            for role, process in workers:
                finished = process.poll()
                if finished is not None:
                    print(f'[WORKER EXIT] {role} exit={finished}; stopping pool', file=sys.stderr, flush=True)
                    code = finished if finished != 0 else 1
                    stopping = True
                    break
            if not stopping:
                time.sleep(.25)
    finally:
        for _role, process in workers:
            if process.poll() is None:
                process.terminate()
        for _role, process in workers:
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        for number, handler in previous.items():
            signal.signal(number, handler)
    return code


if __name__ == '__main__':
    raise SystemExit(main())
