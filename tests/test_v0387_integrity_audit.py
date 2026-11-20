"""Deterministic filesystem+SQLite evidence. Physical Parquet is a separate gate."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest

from timepilot.native_bi_recovery import (
    create_intent, snapshot_ready, scan_orphan_snapshots, audit_registered_snapshots,
)


def _scope(project='A'):
    return {'type': 'project_generic_table_v016', 'project_id': project,
            'import_id': 'batch', 'candidate_id': 'candidate', 'version_id': 'revision'}


def _folder(root, project='A', content=b'PAR1evidence'):
    aid = str(uuid4())
    folder = root / aid
    folder.mkdir(parents=True)
    create_intent(folder, aid, _scope(project))
    data = folder / 'data.parquet'
    data.write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    snapshot_ready(folder, sha256=digest, file_bytes=len(content), row_count=1)
    return folder, digest


def _local_store(tmp_path):
    from timepilot.db import Store, NativeBIAssetRow
    from timepilot.config import Settings
    from dataclasses import replace
    settings = replace(Settings.from_env(tmp_path),
                       db_url=f"sqlite:///{(tmp_path / 'metadata.sqlite3').as_posix()}")
    settings.ensure_dirs()
    store = Store(settings.db_url)
    return store, NativeBIAssetRow


def _register(store, model, folder, digest):
    data = (folder / 'data.parquet')
    with store.sessions.begin() as db:
        db.add(model(id=folder.name, name='registered', row_count=1,
                     columns_json='[]', lineage_json='{}', sha256=digest,
                     file_bytes=data.stat().st_size))


def test_orphan_wrong_size_fails_closed_even_without_full_hash(tmp_path):
    root=tmp_path/'native_bi';root.mkdir()
    folder, sha = _folder(root)
    (folder / 'data.parquet').write_bytes(b'PAR1')
    outcome = scan_orphan_snapshots(root, set(), expected=_scope('B'))
    assert outcome['invalid_orphans'] == 1
    assert outcome['complete'] is False
    assert outcome['unregistered_snapshots'] == 1
    assert outcome['orphan_previews'][0]['reason'] == 'recorded_size_mismatch'


def test_orphan_same_size_tamper_requires_hash_scan(tmp_path):
    root=tmp_path/'native_bi';root.mkdir()
    folder, sha = _folder(root, 'A', b'PAR1aaaaa')
    (folder/'data.parquet').write_bytes(b'PAR1bbbbb')
    cheap = scan_orphan_snapshots(root, set(), expected=_scope('A'))
    assert cheap['complete'] is True  # Cheap size-only scans cannot prove bytes
    full = scan_orphan_snapshots(root, set(), expected=_scope('B'), include_evidence=True)
    assert full['complete'] is False
    assert full['invalid_orphans'] == 1
    assert full['orphan_previews'][0]['hash_matches'] is False


def test_orphan_matching_hash_is_hashed_once(tmp_path, monkeypatch):
    import timepilot.native_bi_recovery as recovery
    root = tmp_path/'native_bi'; root.mkdir()
    _folder(root)
    actual = recovery._digest
    count=[]
    def recorded(path):
        count.append(str(path))
        return actual(path)
    monkeypatch.setattr(recovery, '_digest', recorded)
    result = scan_orphan_snapshots(root, set(), expected=_scope(), include_evidence=True)
    assert result['complete'] and len(count) == 1
    assert result['orphan_previews'][0]['hash_matches'] is True


def test_native_root_symlink_refused(tmp_path):
    actual = tmp_path/'actual';actual.mkdir()
    _folder(actual)
    pointer=tmp_path/'native_bi'
    try:
        pointer.symlink_to(actual, target_is_directory=True)
    except OSError:
        pytest.skip('Symlinks not supported')
    result = scan_orphan_snapshots(pointer, set(), expected=_scope())
    assert not result['complete'] and result['ambiguous_orphans'] == 1
    assert result['orphan_previews'][0]['reason'] == 'unsafe_native_root'
    check = audit_registered_snapshots(pointer, [])
    assert check['safe'] is False


def test_registered_audit_detects_same_length_corruption(tmp_path):
    root=tmp_path/'native_bi';root.mkdir()
    folder, sha=_folder(root, content=b'PAR1aaaaaa')
    good = audit_registered_snapshots(root, [(folder.name, sha, 10)])
    assert good['safe'] and good['verified']==1 and good['hashed_bytes']==10
    (folder/'data.parquet').write_bytes(b'PAR1bbbbbb')
    bad=audit_registered_snapshots(root, [(folder.name,sha,10)])
    assert bad['safe'] is False and bad['invalid']==1
    assert bad['issues'][0]['reason']=='file_sha256_mismatch'


def test_registered_missing_and_symlink_fail(tmp_path):
    root=tmp_path/'native_bi';root.mkdir()
    missing=audit_registered_snapshots(root, [(str(uuid4()),'a'*64,5)])
    assert missing['missing']==1 and not missing['complete']
    real=tmp_path/'real.parquet';real.write_bytes(b'PAR1fake')
    folder=root/str(uuid4());folder.mkdir()
    try:
        (folder/'data.parquet').symlink_to(real)
    except OSError:
        pytest.skip('Symlinks not supported')
    result=audit_registered_snapshots(root, [(folder.name,hashlib.sha256(real.read_bytes()).hexdigest(),len(real.read_bytes()))])
    assert result['issues'][0]['reason']=='unsafe_symlink'


def test_registered_hash_budget_and_invalid_metadata_fail_closed(tmp_path):
    root=tmp_path/'native_bi';root.mkdir()
    folder,sha=_folder(root)
    outcome=audit_registered_snapshots(root, [(folder.name,sha,12)], max_hash_bytes=2)
    assert not outcome['complete'] and outcome['exceeded_budget']
    assert outcome['verified']==0
    bad=audit_registered_snapshots(root, [('../../evil','a'*64,20)])
    assert bad['invalid']==1 and bad['issues'][0]['reason']=='invalid_metadata'
    with pytest.raises(ValueError): audit_registered_snapshots(root, [], max_assets=0)


def test_registered_multi_asset_limit_fail_closed(tmp_path):
    root=tmp_path/'native_bi';root.mkdir()
    a,sha1=_folder(root)
    b,sha2=_folder(root)
    scanned=audit_registered_snapshots(root, [(a.name,sha1,12),(b.name,sha2,12)], max_assets=1)
    assert not scanned['complete'] and scanned['checked']==1
    assert scanned['exceeded_budget']


def test_audit_cli_detects_corrupted_registered_asset_without_duckdb(tmp_path):
    from scripts.native_snapshot_audit import inventory, main
    store, model = _local_store(tmp_path)
    try:
        root=tmp_path/'native_bi';root.mkdir(exist_ok=True)
        folder,sha=_folder(root)
        _register(store, model, folder, sha)
        before=(tmp_path/'metadata.sqlite3').stat().st_mtime_ns
        clean=inventory(tmp_path, verify_registered=True)
        assert clean['registered_integrity']['safe'] and clean['registered_integrity']['verified']==1
        (folder/'data.parquet').write_bytes(b'PAR1wrongxxx')
        broken=inventory(tmp_path, verify_registered=True)
        assert broken['registered_integrity']['invalid']==1
        output=tmp_path/'read_only_result.json'
        code=main(['--home',str(tmp_path),'--verify-registered','--out',str(output)])
        assert code==2
        assert json.loads(output.read_text())['status']=='requires_review'
        assert before==(tmp_path/'metadata.sqlite3').stat().st_mtime_ns
    finally:
        store.engine.dispose()


def test_quarantine_rejects_redirected_parent_and_destination(tmp_path):
    from scripts.native_snapshot_quarantine import quarantine
    store, model=_local_store(tmp_path)
    try:
        root=tmp_path/'native_bi';root.mkdir(exist_ok=True)
        folder,sha=_folder(root)
        outside=tmp_path/'outside';outside.mkdir()
        destination=tmp_path/'native_bi_quarantine'
        try:
            destination.symlink_to(outside, target_is_directory=True)
        except OSError:
            pytest.skip('Symlinks not supported')
        with pytest.raises(RuntimeError,match='Quarantine root'):
            quarantine(tmp_path,folder.name,sha,str(uuid4()),
                       'Do not write to symlink target',services_stopped=True)
        assert (folder/'data.parquet').exists()
    finally:
        store.engine.dispose()


def test_quarantine_blocks_redirected_worker_registry_and_metadata(tmp_path):
    from scripts.native_snapshot_quarantine import quarantine
    store, model=_local_store(tmp_path)
    try:
        root=tmp_path/'native_bi';root.mkdir(exist_ok=True)
        folder,sha=_folder(root)
        registry=tmp_path/'operations'/'worker_status.sqlite3'
        registry.parent.mkdir(exist_ok=True)
        external=tmp_path/'untrusted.sqlite3';external.write_bytes(b'x')
        try:
            registry.symlink_to(external)
        except OSError:
            pytest.skip('Symlinks not supported')
        with pytest.raises(RuntimeError,match='Worker registry path'):
            quarantine(tmp_path,folder.name,sha,str(uuid4()),
                       'Cannot verify redirected worker state',services_stopped=True)
        assert (folder/'data.parquet').exists()
    finally:
        store.engine.dispose()


def test_outdated_intent_never_adopted_by_evidence_audit(tmp_path):
    root=tmp_path/'native_bi';root.mkdir()
    folder,sha=_folder(root)
    path=folder/'publication-intent.json'
    record=json.loads(path.read_text())
    record['format']=999
    path.write_text(json.dumps(record))
    scanned=scan_orphan_snapshots(root,set(),expected=_scope())
    assert not scanned['complete']
    assert scanned['ambiguous_orphans']==1


def test_bounded_orphan_hash_reads_fail_closed(tmp_path):
    root=tmp_path/'native_bi';root.mkdir()
    folder,sha=_folder(root)
    out=scan_orphan_snapshots(root,set(),expected=_scope(),include_evidence=True,max_hash_bytes=2)
    assert not out['complete']
    assert out['hash_budget_exhausted']
    assert out['hashed_bytes']==0
    assert out['orphan_previews'][0]['reason']=='hash_budget_exceeded'


def test_global_hash_budget_is_shared_between_orphans_and_registered(tmp_path):
    from scripts.native_snapshot_audit import inventory
    store, model = _local_store(tmp_path)
    try:
        root = tmp_path/'native_bi';root.mkdir(exist_ok=True)
        registered,sha=_folder(root,content=b'PAR1registered')
        _register(store,model,registered,sha)
        orphan,_ = _folder(root,content=b'PAR1orphan')
        # Orphan hash consumes the entire shared I/O budget, so the
        # registered file cannot be implicitly scanned without notice.
        amount=len((orphan/'data.parquet').read_bytes())
        audit=inventory(tmp_path,full_hash=True, verify_registered=True,
                        max_hash_bytes=amount)
        assert audit['total_hashed_bytes']<=amount
        assert audit['registered_integrity']['safe'] is False
        assert audit['registered_integrity']['exceeded_budget'] is True
    finally:
        store.engine.dispose()


def test_orphan_snapshot_path_replaced_by_directory_is_ambiguous(tmp_path):
    root=tmp_path/'native_bi';root.mkdir()
    folder,sha=_folder(root)
    (folder/'data.parquet').unlink()
    (folder/'data.parquet').mkdir()
    result=scan_orphan_snapshots(root,set(),expected=_scope(),include_evidence=False)
    assert not result['complete']
    assert result['ambiguous_orphans']==1


def test_orphan_modified_during_hash_is_not_accepted(tmp_path, monkeypatch):
    import timepilot.native_bi_recovery as recovery
    root=tmp_path/'native_bi';root.mkdir()
    folder,sha=_folder(root,content=b'PAR1static')
    original=recovery._digest
    def mutate_after_read(path):
        result=original(path)
        path.write_bytes(b'PAR1change')  # preserve size but change ctime
        return result
    monkeypatch.setattr(recovery,'_digest',mutate_after_read)
    result=scan_orphan_snapshots(root,set(),expected=_scope(),include_evidence=True)
    assert not result['complete']
    assert result['orphan_previews'][0]['reason']=='changed_during_hash'
