"""Current-doc correctness and local-link tests; historical records remain archived."""
from pathlib import Path
import re
import tomllib

ROOT=Path(__file__).resolve().parents[1]
CURRENT=('README.md','docs/API.md','docs/ACCEPTANCE.md','docs/FILE_TREE.md',
         'docs/CURRENT_USER_GUIDE.md','docs/DOCUMENT_INDEX.md',
         'docs/FEATURELAB_V0324_RELEASE_AND_REVIEW.md')


def test_current_docs_are_discoverable_and_versioned():
    for name in CURRENT:
        text=(ROOT/name).read_text(encoding='utf-8')
        expected=('v0.32.4' if 'FEATURELAB_V0324_' in name else 'v'+tomllib.loads((ROOT/'pyproject.toml').read_text())['project']['version'])
        assert expected in text[:1500],name
        assert len(text)>250
    readme=(ROOT/'README.md').read_text(encoding='utf-8')
    assert 'docs/CURRENT_USER_GUIDE.md' in readme
    assert 'scripts/featurelab364_physical_acceptance.py' in readme


def test_docs_have_no_missing_relative_links():
    docs=[ROOT/'README.md',*(ROOT/'docs').rglob('*.md')]
    broken=[]
    for p in docs:
        for match in re.finditer(r'(?<!!)\[[^]\n]+\]\(([^)\n]+)\)',p.read_text(encoding='utf-8')):
            target=match.group(1).strip().split(' ')[0].split('#')[0].split('?')[0]
            if not target or re.match(r'^(?:https?://|mailto:|/|#|data:)',target):
                continue
            if not (p.parent/target).resolve().exists():
                broken.append(f'{p.relative_to(ROOT)}: {target}')
    assert not broken,'\n'.join(broken)


def test_historical_security_and_releases_are_not_presented_as_current():
    api=(ROOT/'docs/API.md').read_text(encoding='utf-8')
    assert api.find('当前 FeatureLab API 概要')<api.find('v0.9.0')
    assert api.find('实际读写权限由当前本地/企业安全模式决定')<api.find('v0.9.0')
    accept=(ROOT/'docs/ACCEPTANCE.md').read_text(encoding='utf-8')
    assert accept.find('当前验收')<accept.find('v0.5.13')
    assert '生产发布状态保持阻断' in accept
