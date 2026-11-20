"""Release ZIP must not masquerade as a tested complete offline distribution."""
from __future__ import annotations

import hashlib
import json
import stat
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from verify_offline_archive_v0388 import ArchiveProblem, verify_archive


def _archive(tmp_path, *, kind='source-only', corrupt=None, symlink=False, foreign=False):
    root = 'TimePilot_v0.38.8/'
    data = {'pyproject.toml': b'[project]\nversion="0.38.8"\n',
            'frontend/native-bi/package.json': b'{"version":"0.38.8"}'}
    meta = {'format':2,'version':'0.38.8','distribution_kind':kind,
            'python_runtime_wheels_included':False,
            'files':{name:{'sha256':hashlib.sha256(content).hexdigest(),'bytes':len(content)}
                     for name, content in data.items()}}
    if kind=='complete-offline':
        meta['python_runtime_wheels_included']=True
    out=tmp_path/'candidate.zip'
    with zipfile.ZipFile(out,'w') as handle:
        for name, content in data.items():
            record=zipfile.ZipInfo(root+name)
            if symlink and name=='pyproject.toml':
                record.create_system=3
                record.external_attr=(stat.S_IFLNK|0o777)<<16
            handle.writestr(record, b'corrupt'+content if corrupt==name else content)
        handle.writestr(root+'docs/RELEASE_MANIFEST.json', json.dumps(meta))
        if foreign:
            handle.writestr('other-root/extra.txt', b'other')
    return out



def test_structurally_complete_fixture_is_not_a_runtime_certificate(tmp_path):
    # Synthetic bytes validate archive integrity logic only; actual dependency
    # resolution and the 30-cycle browser test remain mandatory separate gates.
    root='TimePilot_v0.38.8/'
    source={'pyproject.toml':b'[project]\nversion="0.38.8"\n',
            'frontend/native-bi/package.json':b'{}',
            'frontend/native-bi/package-lock.json':b'{"lockfileVersion":3}'}
    native={'src/timepilot/static/native-bi/perspective-build.json':b'{}'}
    wheels={'offline-wheelhouse/OFFLINE_WHEELS.json':b'{}',
            'offline-wheelhouse/requirements-offline-lock.txt':b'test',
            'offline-wheelhouse/example-0.1-py3-none-any.whl':b'fake-only'}
    def entries(paths):
        return {name:{'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data)}
                for name,data in paths.items()}
    manifest={'format':2,'version':'0.38.8','distribution_kind':'complete-offline',
              'python_runtime_wheels_included':True,
              'native_bi_release_status':'compiled-offline-verified',
              'wheelhouse_platform':{'platform':'test-only'},
              'files':entries(source),'runtime_assets':entries(native),
              'runtime_wheelhouse':entries(wheels)}
    path=tmp_path/'structural-fixture.zip'
    with zipfile.ZipFile(path,'w') as z:
        for name,data in {**source,**native,**wheels}.items():
            z.writestr(root+name,data)
        z.writestr(root+'docs/RELEASE_MANIFEST.json',json.dumps(manifest))
    result=verify_archive(path,require_complete=True)
    assert result['status']=='passed' and result['complete_offline_evidence']
    # A forged but hash-consistent demo wheel cannot pass real resolver tests.
    # Never treat this unit test as evidence of a working offline installation.


def test_source_zip_verified_but_cannot_claim_complete(tmp_path):
    path=_archive(tmp_path)
    assert verify_archive(path)['status']=='passed'
    report=verify_archive(path, require_complete=True)
    assert report['status']=='blocked'
    assert report['complete_offline_evidence'] is False


def test_hashed_source_tamper_fails(tmp_path):
    with pytest.raises(ArchiveProblem,match='mismatch'):
        verify_archive(_archive(tmp_path,corrupt='pyproject.toml'))


def test_claimed_complete_without_bundle_and_wheels_fails(tmp_path):
    with pytest.raises(ArchiveProblem,match='claims complete-offline'):
        verify_archive(_archive(tmp_path,kind='complete-offline'))


def test_zip_symlink_entry_fails(tmp_path):
    with pytest.raises(ArchiveProblem,match='symbolic link'):
        verify_archive(_archive(tmp_path,symlink=True))


def test_mixed_release_roots_fail(tmp_path):
    with pytest.raises(ArchiveProblem,match='root'):
        verify_archive(_archive(tmp_path,foreign=True))


def test_zip_duplicate_entries_fail(tmp_path):
    path=_archive(tmp_path)
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter('ignore',UserWarning)
        with zipfile.ZipFile(path,'a') as handle:
            handle.writestr('TimePilot_v0.38.8/pyproject.toml',b'duplicate')
    with pytest.raises(ArchiveProblem,match='duplicate'):
        verify_archive(path)


def test_no_user_file_write(tmp_path):
    path=_archive(tmp_path)
    before=set(tmp_path.iterdir())
    verify_archive(path)
    assert set(tmp_path.iterdir())==before


def test_offline_gate_does_not_claim_success_when_source_zip_is_missing(tmp_path):
    import subprocess
    root=Path(__file__).resolve().parents[1]
    receipt=tmp_path/'gate.json'
    process=subprocess.run([sys.executable,str(root/'scripts/release_gate_v0388.py'),
                            '--mode','offline','--out',str(receipt)],
                           cwd=root,capture_output=True,text=True,timeout=12)
    assert process.returncode == 2,process.stdout+process.stderr
    payload=json.loads(receipt.read_text())
    assert payload['status']=='blocked' and not payload['complete']
    missing=payload['steps'][0]['missing']
    assert 'complete_offline_archive_not_provided' in missing


def test_offline_policy_executes_physical_then_browser_before_archive(tmp_path,monkeypatch):
    import release_gate_v0388 as gate
    original_is_file=Path.is_file
    special={'package-lock.json','perspective-build.json','OFFLINE_WHEELS.json','candidate.zip'}
    def exists(path):
        if path.name in special:return True
        return original_is_file(path)
    monkeypatch.setattr(Path,'is_file',exists)
    monkeypatch.setattr(gate.importlib.util,'find_spec',lambda _: object())
    monkeypatch.setattr(gate.sys,'version_info',(3,12,12))
    monkeypatch.setattr(gate.sys,'argv',['release_gate_v0388.py','--mode','offline',
                                          '--archive',str(tmp_path/'candidate.zip'),
                                          '--out',str(tmp_path/'receipt.json')])
    called=[]
    def fake_run(command,label,steps,timeout=150):
        called.append((label,command))
        steps.append({'gate':label,'status':'passed','exit_code':0})
        return True
    monkeypatch.setattr(gate,'run',fake_run)
    assert gate.main()==0
    labels=[name for name,_ in called]
    assert labels.index('physical_duckdb') < labels.index('browser_30_cycles')
    assert labels.index('browser_30_cycles') < labels.index('independent_complete_offline_zip_integrity')
    assert labels.index('independent_complete_offline_zip_integrity') < labels.index('extracted_zip_physical_browser_end_to_end')
    assert any('tests/test_v0388_archive_gate.py' in command for _,command in called)
