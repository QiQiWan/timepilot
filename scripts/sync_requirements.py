"""Synchronize the ONE requirements.txt file from pyproject.toml (no extra lists)."""
from __future__ import annotations
import argparse
import sys
import tomllib
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
HEADER = "# TimePilot unified dependencies - Python 3.12.12 target.\n# Version ranges are NOT a transitive lock; freeze after target-machine verification.\n"
def desired():
    manifest = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return HEADER + "\n".join(manifest["project"]["dependencies"]) + "\n-e .\n"
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    out=ROOT/"requirements.txt"
    if args.check:
        if not out.exists() or out.read_text(encoding="utf-8") != desired():
            print("requirements.txt is not synchronized",file=sys.stderr)
            return 1
        print("Single requirements.txt is synchronized")
    else:
        out.write_text(desired(),encoding="utf-8")
        print("Updated requirements.txt")
    return 0
if __name__ == "__main__":
    raise SystemExit(main())
