"""Prevent mixed-version static imports in a direct-copy release."""
from pathlib import Path
import re
import tomllib

def project_version(root):
    return tomllib.loads((root/"pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]

ROOT=Path(__file__).resolve().parents[1]
STATIC=ROOT/'src/timepilot/static'


def test_every_local_asset_resolves_to_an_actual_source_file():
    html=(STATIC/'index.html').read_text(encoding='utf-8')
    assets=re.findall(r'["\'](/assets/[^?"\']+)\?v=([0-9.]+)["\']',html)
    assert assets, 'index.html must declare versioned local JS and CSS assets'
    names=[name for name,_ in assets]
    assert len(names)==len(set(names)), 'duplicate asset imports create event handler duplication'
    for name,version in assets:
        assert (STATIC/name.removeprefix('/assets/')).is_file(),name
        assert version==project_version(ROOT),(name,version)


def test_code_only_release_cannot_claim_a_precompiled_perspective_runtime():
    from timepilot.native_bi_bundle import bundle_status
    root=STATIC/'native-bi'
    if not root.exists():
        assert bundle_status(root).get('ready') is False
