"""Native BI packaging must be independent of forecasting Python dependencies."""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_bundle_status_imports_without_installed_third_party_packages():
    # -S disables site-packages. A source-only build machine can validate the
    # Perspective frontend contract even before installing the Python SDK.
    script = '''import sys
sys.path.insert(0, 'src')
from timepilot.native_bi_bundle import frontend_source_hash, PUBLIC_EXPORTS
assert len(PUBLIC_EXPORTS) == 7
assert frontend_source_hash(__import__('pathlib').Path('frontend/native-bi'))
'''
    result = subprocess.run([sys.executable, '-S', '-c', script], cwd=ROOT,
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr


def test_public_timepilot_client_lazy_import_keeps_api_compatible():
    from timepilot import TimePilot
    from timepilot.sdk import TimePilot as SDKClient
    assert TimePilot is SDKClient
