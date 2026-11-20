"""Deterministic native recovery tests without claiming true DuckDB execution.

The physical native Parquet/SIGKILL test is in
scripts/native_bi_physical_crash_acceptance.py and remains an independent P0 gate.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest

from timepilot.native_bi_recovery import (
    INTENT, create_intent, read_intent, scan_orphan_snapshots, snapshot_ready,
)


def scope(project='p1'):
    return {'type':'project_generic_table_v016',
            'project_id':project, 'import_id':'imp',
            'candidate_id':'candidate', 'version_id':'revision',
            'source_sha256':'a'*64, 'data_sha256':'b'*64}


def folder(root, lineage, *, bytes_=b'PAR1test-evidence-only'):
    asset_id = str(uuid4())
    dest = root / asset_id
    dest.mkdir()
    create_intent(dest, asset_id, lineage)
    data = dest / 'data.parquet'
    data.write_bytes(bytes_)
    snapshot_ready(dest, sha256=hashlib.sha256(bytes_).hexdigest(),
                   file_bytes=len(bytes_), row_count=2)
    return dest


def test_scoped_provenance_allows_unrelated_orphans_without_auto_repair(tmp_path):
    one = folder(tmp_path, scope('alpha'))
    two = folder(tmp_path, scope('beta'))
    expected = {k:scope('alpha')[k] for k in ('project_id','import_id','candidate_id','version_id')}
    report = scan_orphan_snapshots(tmp_path, set(), expected=expected, include_evidence=True)
    assert report['complete']
    assert report['unregistered_snapshots'] == report['scoped_unregistered_snapshots'] == 1
    assert report['unregistered_other_regions'] == 1
    assert report['orphan_previews'][0]['hash_matches']
    assert (one / 'data.parquet').exists() and (two / 'data.parquet').exists()
    beta = {**expected,'project_id':'beta'}
    assert scan_orphan_snapshots(tmp_path, set(), expected=beta)['unregistered_snapshots'] == 1
    assert scan_orphan_snapshots(tmp_path, {one.name}, expected=expected)['unregistered_snapshots'] == 0


def test_unknown_legacy_snapshot_fail_closed_globally(tmp_path):
    orphan = tmp_path/str(uuid4())
    orphan.mkdir()
    (orphan / 'data.parquet').write_bytes(b'legacy uncommitted snapshot')
    result = scan_orphan_snapshots(tmp_path, set(), expected=scope('unrelated'))
    assert not result['complete']
    assert result['ambiguous_orphans'] == 1
    assert result['unregistered_snapshots'] == 1
    assert result['orphan_previews'][0]['reason'] == 'missing_or_invalid_intent'


def test_tampered_or_invalid_intent_does_not_prove_absence(tmp_path):
    directory = folder(tmp_path, scope('alpha'))
    intent = directory / INTENT
    saved = json.loads(intent.read_text())
    saved['asset_id'] = str(uuid4())
    intent.write_text(json.dumps(saved))
    report = scan_orphan_snapshots(tmp_path, set(), expected=scope('beta'))
    assert not report['complete'] and report['ambiguous_orphans'] == 1
    assert read_intent(directory) is None


def test_hash_mismatch_visible_in_read_only_forensic_evidence(tmp_path):
    directory = folder(tmp_path, scope('alpha'))
    (directory / 'data.parquet').write_bytes(b'PAR1tampered')
    report = scan_orphan_snapshots(tmp_path, set(), expected={k:scope('alpha')[k]
            for k in ('project_id','import_id','candidate_id','version_id')},include_evidence=True)
    assert report['unregistered_snapshots'] == 1
    assert report['orphan_previews'][0]['hash_matches'] is False
    assert (directory / 'data.parquet').exists()  # never delete evidence


def test_only_incomplete_build_does_not_claim_completed_snapshot(tmp_path):
    directory = tmp_path/str(uuid4())
    directory.mkdir()
    create_intent(directory, directory.name, scope('alpha'))
    (directory / 'data.building').write_bytes(b'PAR1incomplete')
    result = scan_orphan_snapshots(tmp_path, set(), expected=scope('alpha'))
    assert result['complete'] and result['incomplete_builds'] == 1
    assert result['unregistered_snapshots'] == 0


def test_symlink_to_external_path_never_scanned_as_trusted(tmp_path):
    real = tmp_path/'real'
    real.mkdir()
    external = folder(real, scope('alpha'))
    root = tmp_path/'native_bi'
    root.mkdir()
    try:
        (root/external.name).symlink_to(external, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip('Host disallows test symlinks')
    result = scan_orphan_snapshots(root,set(), expected=scope('beta'),include_evidence=True)
    assert not result['complete']
    assert result['ambiguous_orphans'] == 1


def test_real_subprocess_exit_leaves_durable_scope_evidence(tmp_path):
    # The bytes in this source-only test are intentionally not asserted to be
    # valid Parquet. Real physical verification is a separate strict gate.
    root = tmp_path / 'native_bi'
    root.mkdir()
    program = '''import hashlib,os,sys
from pathlib import Path
from uuid import uuid4
from timepilot.native_bi_recovery import create_intent,snapshot_ready
root=Path(sys.argv[1]);asset_id=str(uuid4());folder=root/asset_id;folder.mkdir()
lineage={'type':'project_generic_table_v016','project_id':'p','import_id':'i','candidate_id':'c','version_id':'v'}
create_intent(folder,asset_id,lineage)
data=b'PAR1subprocess-exit';(folder/'data.parquet').write_bytes(data)
snapshot_ready(folder,sha256=hashlib.sha256(data).hexdigest(),file_bytes=len(data),row_count=1)
os._exit(73)
'''
    env={**os.environ,'PYTHONPATH':str(Path(__file__).resolve().parents[1]/'src')}
    child=subprocess.run([sys.executable,'-c',program,str(root)],cwd=tmp_path,
                         env=env,capture_output=True,timeout=25)
    assert child.returncode == 73, child.stderr.decode(errors='replace')
    result = scan_orphan_snapshots(root,set(),expected={
        'project_id':'p','import_id':'i','candidate_id':'c','version_id':'v'},include_evidence=True)
    assert result['complete'] and result['scoped_unregistered_snapshots'] == 1
    assert result['orphan_previews'][0]['hash_matches']


def test_native_inventory_uses_authoritative_sqlite_and_scoped_intents(tmp_path):
    from timepilot.config import Settings
    from timepilot.db import Store
    from timepilot.native_bi import NativeBIService
    settings = Settings.from_env(tmp_path)
    settings.ensure_dirs()
    store = Store(settings.db_url)
    try:
        native = NativeBIService(store,settings)
        folder(native.home,scope('alpha'))
        target = native.find_published_candidates('beta','imp','candidate','revision')
        assert target['complete'] and target['unregistered_snapshots'] == 0
        assert target['unregistered_other_regions'] == 1
        own = native.find_published_candidates('alpha','imp','candidate','revision')
        assert own['complete'] and own['unregistered_snapshots'] == 1
        assert own['asset_ids'] == []
    finally:
        store.engine.dispose()


def test_snapshot_intent_validation_rejects_fake_finalize(tmp_path):
    directory = tmp_path/str(uuid4());directory.mkdir()
    with pytest.raises(ValueError):
        create_intent(directory, directory.name, {'type':'project_generic_table_v016','project_id':'p'})
    create_intent(directory, directory.name, scope())
    with pytest.raises(ValueError):
        snapshot_ready(directory,sha256='a'*64,file_bytes=3,row_count=1)
    assert read_intent(directory)['state'] == 'preparing'


def test_audit_cli_does_not_create_or_modify_database(tmp_path):
    from scripts.native_snapshot_audit import inventory
    with pytest.raises(RuntimeError,match='metadata.sqlite3'):
        inventory(tmp_path)
    assert not (tmp_path/'metadata.sqlite3').exists()


def _local_native_home(tmp_path):
    from timepilot.db import Store
    from timepilot.config import Settings
    from dataclasses import replace
    settings=replace(Settings.from_env(tmp_path),
                     db_url=f"sqlite:///{(tmp_path/'metadata.sqlite3').as_posix()}")
    settings.ensure_dirs()
    store=Store(settings.db_url)
    root=tmp_path/'native_bi'
    root.mkdir(exist_ok=True)
    return store,root


def test_explicit_quarantine_preserves_bytes_and_unblocks_scoped_absence(tmp_path):
    from scripts.native_snapshot_quarantine import quarantine
    from scripts.native_snapshot_audit import inventory
    store,root=_local_native_home(tmp_path)
    try:
        target=folder(root,scope('alpha'))
        original=target/'data.parquet'
        digest=hashlib.sha256(original.read_bytes()).hexdigest()
        with pytest.raises(RuntimeError,match='workers must be stopped'):
            quarantine(tmp_path,target.name,digest,str(uuid4()),
                       'Stop required before quarantine',services_stopped=False)
        with pytest.raises(RuntimeError,match='SHA256 differs'):
            quarantine(tmp_path,target.name,'f'*64,str(uuid4()),
                       'Incorrect SHA should fail',services_stopped=True)
        assert original.is_file()
        before=inventory(tmp_path,expected={k:scope('alpha')[k]
               for k in ('project_id','import_id','candidate_id','version_id')})
        assert before['unregistered_snapshots']==1
        case_id=str(uuid4())
        result=quarantine(tmp_path,target.name,digest,case_id,
                          'Verified orphan will be isolated for recovery',services_stopped=True)
        relocated=Path(result['quarantine_path'])
        assert result['status']=='quarantined_preserved_not_deleted'
        assert relocated.joinpath('data.parquet').read_bytes()==b'PAR1test-evidence-only'
        assert json.loads((relocated/'quarantine-receipt.json').read_text())['sha256']==digest
        assert not target.exists()
        after=inventory(tmp_path,expected={k:scope('alpha')[k]
               for k in ('project_id','import_id','candidate_id','version_id')})
        assert after['complete'] and after['unregistered_snapshots']==0
    finally:
        store.engine.dispose()


def test_registered_asset_cannot_be_quarantined(tmp_path):
    from scripts.native_snapshot_quarantine import quarantine
    from timepilot.db import NativeBIAssetRow
    store,root=_local_native_home(tmp_path)
    try:
        target=folder(root,scope('alpha'))
        digest=hashlib.sha256((target/'data.parquet').read_bytes()).hexdigest()
        with store.sessions.begin() as db:
            db.add(NativeBIAssetRow(id=target.name,name='registered',row_count=2,
                columns_json='[]',lineage_json='{}',sha256=digest,file_bytes=20))
        with pytest.raises(RuntimeError,match='registered in metadata'):
            quarantine(tmp_path,target.name,digest,str(uuid4()),
                        'Registered assets cannot be moved',services_stopped=True)
        assert (target/'data.parquet').is_file()
    finally:
        store.engine.dispose()


def test_legacy_orphan_quarantine_requires_extra_manual_acknowledgement(tmp_path):
    from scripts.native_snapshot_quarantine import quarantine
    store,root=_local_native_home(tmp_path)
    try:
        target=root/str(uuid4());target.mkdir()
        (target/'data.parquet').write_bytes(b'PAR1legacy-native-snapshot')
        digest=hashlib.sha256((target/'data.parquet').read_bytes()).hexdigest()
        with pytest.raises(RuntimeError,match='intent is absent'):
            quarantine(tmp_path,target.name,digest,str(uuid4()),
                       'Legacy snapshot missing its manifest',services_stopped=True)
        result=quarantine(tmp_path,target.name,digest,str(uuid4()),
                          'Manually inspected legacy snapshot hash',services_stopped=True,
                          allow_unknown_intent=True)
        assert not target.exists()
        assert Path(result['quarantine_path']).joinpath('data.parquet').is_file()
        assert result['recovery_intent_verified'] is False
    finally:
        store.engine.dispose()


def test_scan_finite_budget_fails_closed(tmp_path):
    for _ in range(4):
        folder(tmp_path,scope('different'))
    report=scan_orphan_snapshots(tmp_path,set(),expected=scope('target'),max_scan=2)
    assert not report['complete']
    assert report['filesystem_entries_scanned']>2


def test_active_worker_lease_prevents_offline_quarantine(tmp_path):
    from scripts.native_snapshot_quarantine import quarantine
    from timepilot.worker_supervisor import WorkerRegistry
    store,root=_local_native_home(tmp_path)
    try:
        target=folder(root,scope('alpha'))
        digest=hashlib.sha256((target/'data.parquet').read_bytes()).hexdigest()
        registry=WorkerRegistry(tmp_path)
        generation=registry.claim('publication','worker-running')
        assert generation is not None
        with pytest.raises(RuntimeError,match='still hold valid leases'):
            quarantine(tmp_path,target.name,digest,str(uuid4()),
                       'Operator cannot move a live asset',services_stopped=True)
        assert (target/'data.parquet').exists()
        assert registry.release('publication','worker-running',generation)
    finally:
        store.engine.dispose()


def test_release_gate_timeout_kills_subprocess_tree_and_records_failure(tmp_path):
    from scripts.release_gate_v0386 import StageLog, run
    report = {'version':'0.38.6','status':'running','steps':[]}
    steps = StageLog(tmp_path/'stages.json',report)
    report['steps'] = steps
    program = '''import subprocess,sys,time
subprocess.Popen([sys.executable,'-c','import time;time.sleep(50)'])
time.sleep(50)
'''
    import time
    start=time.monotonic()
    result=run([sys.executable,'-c',program],'forced_timeout',steps,timeout=.5)
    assert result is False and time.monotonic()-start < 8
    assert steps[0]['status']=='failed' and steps[0]['reason']=='TimeoutExpired'
    saved=json.loads((tmp_path/'stages.json').read_text())
    assert saved['steps'][0]['gate']=='forced_timeout'
