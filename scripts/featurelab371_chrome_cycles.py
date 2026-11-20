#!/usr/bin/env python3
"""v0.37.1: real, installed-Google-Chrome same-document BI acceptance.

The isolated v0.36.5 journey creates authentic materialized features first.
Then 30 complete round trips between source BI and formal-feature BI run
within ONE browser document, checking both actual Perspective panels.
This gate never downloads Chrome, substitutes Chromium or fakes a websocket.

Exit 0 passed, 1 failed, 2 blocked; JSON receipt is always written.
"""
from __future__ import annotations
import argparse
from pathlib import Path
from featurelab365_chrome_journey import main as journey_main

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--out',type=Path,default=Path('reports/v0371/chrome_30_cycles.json'))
    parser.add_argument('--timeout',type=int,default=240)
    args=parser.parse_args()
    return journey_main(['--out',str(args.out),'--timeout',str(args.timeout),'--bi-cycles','30'])

if __name__=='__main__':raise SystemExit(main())
