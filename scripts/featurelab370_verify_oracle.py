"""Fail-closed independent official NAB source, AP and ROC-AUC acceptance."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from timepilot.industrial_oracle_v0370 import verify_oracle


def run(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument('--artifacts', type=Path, required=True)
    p.add_argument('--raw-root', type=Path, required=True, help='Official nine-source NAB directory; no synthetic substitutes')
    p.add_argument('--oracle', type=Path, default=ROOT / 'benchmarks/featurelab_v0370/NAB_INDEPENDENT_ORACLE.json')
    p.add_argument('--tolerance', type=float, default=0.004)
    a = p.parse_args(argv)
    try:
        evidence = verify_oracle(a.artifacts, a.oracle, raw_root=a.raw_root, tolerance=a.tolerance)
    except (ValueError, OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        evidence = {'status': 'failed', 'problems': [type(exc).__name__ + ': ' + str(exc)]}
    out = a.artifacts / 'NAB_ORACLE_QA.json'
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(evidence, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    print(json.dumps({'status': evidence['status'], 'problems': evidence.get('problems', []), 'path': str(out)}, ensure_ascii=False))
    return 0 if evidence['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(run())
