"""Regression for the 0.32.4 runtime preflight importlib shadowing bug.

The source-only path did not import the package and therefore hid this failure.
Use the real source tree while replacing only the SDK's optional dependencies.
"""
from __future__ import annotations

import importlib.util
import sys
import tomllib
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]


def _verifier():
    spec = importlib.util.spec_from_file_location(
        "timepilot_runtime_preflight_hotfix", ROOT / "scripts/verify_installation.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_source_check_still_works():
    report = _verifier().inspect(source_only=True)
    assert report["ok"], report["issues"]


def test_require_runtime_does_not_shadow_importlib(monkeypatch):
    verifier = _verifier()
    # Simulate the user's supported active interpreter without modifying it.
    monkeypatch.setattr(verifier.sys, "version_info", (3, 12, 12))
    # The test covers source/import-path/version checks, not 3rd-party SDK deps.
    sdk = ModuleType("timepilot.sdk")
    sdk.TimePilot = lambda: None
    monkeypatch.setitem(sys.modules, "timepilot.sdk", sdk)
    report = verifier.inspect(require_runtime=True)
    assert report["ok"], report["issues"]
    assert report["manifest_version"] == tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    assert report["python_version"] == "3.12.12"


def test_importlib_stays_global_in_inspect():
    verifier = _verifier()
    assert "importlib" not in verifier.inspect.__code__.co_varnames
    assert hasattr(verifier.importlib, "util")
