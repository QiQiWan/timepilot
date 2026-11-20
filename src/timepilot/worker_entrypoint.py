"""Small independent worker CLI; avoid importing the Web/interactive CLI."""
from __future__ import annotations

import argparse
import signal
import sys
import threading


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='TimePilot role-scoped worker runtime')
    parser.add_argument('--role', choices=('forecast','agent','upload','publication','feature'), required=True)
    parser.add_argument('--home', default=None)
    parser.add_argument('--poll-seconds', type=float, default=.75)
    parser.add_argument('--max-rss-mib', type=float, default=None)
    parser.add_argument('--parent-pid', type=int, default=None)
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args(argv)
    from timepilot.worker_runtime import build_worker_runtime
    from timepilot.worker_supervisor import WorkerSupervisor, WorkerLeaseBusy, WorkerLeaseLost
    runtime = build_worker_runtime(args.role, home=args.home)
    stop = threading.Event()
    previous = {}
    for number in (signal.SIGINT, signal.SIGTERM):
        previous[number] = signal.getsignal(number)
        signal.signal(number, lambda _sig, _frame: stop.set())
    try:
        runner = WorkerSupervisor(runtime.registry, args.role, runtime.action,
                                  poll_seconds=args.poll_seconds,
                                  max_rss_mib=args.max_rss_mib,
                                  parent_pid=args.parent_pid)
        print(f'Worker {args.role} ready; home={runtime.registry.home}', flush=True)
        runner.run(stop, max_cycles=1 if args.once else None)
        return 0
    except WorkerLeaseBusy as exc:
        print('CONFLICT: ' + str(exc), file=sys.stderr, flush=True)
        return 3
    except WorkerLeaseLost as exc:
        print('LEASE LOST: ' + str(exc), file=sys.stderr, flush=True)
        return 4
    finally:
        for number, previous_handler in previous.items():
            signal.signal(number, previous_handler)
        runtime.close()


if __name__ == '__main__':
    raise SystemExit(main())
