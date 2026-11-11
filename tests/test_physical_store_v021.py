from pathlib import Path
from uuid import uuid4
import os

import pytest

from timepilot.physical_store import BlobSafetyError,ParquetBlobStore
from timepilot.storage import sha256_file


def create(root,content:bytes):
    path=root/str(uuid4())/'data.parquet'
    path.parent.mkdir(parents=True)
    path.write_bytes(content)
    return path


def test_content_addressable_hardlink_dedup_is_atomic(tmp_path):
    storage=ParquetBlobStore(tmp_path)
    original=b'PAR1'+b'industrial-flow'*128+b'PAR1'
    a=create(tmp_path,original);b=create(tmp_path,original)
    digest=sha256_file(a)
    assert storage.adopt(a,expected=digest)['status']=='new_blob'
    assert storage.adopt(a,expected=digest,apply=True)['status']=='linked'
    estimate=storage.adopt(b,expected=digest)
    assert estimate['status']=='deduplicable' and estimate['bytes_saved']==len(original)
    result=storage.adopt(b,expected=digest,apply=True)
    assert result['status']=='linked' and result['bytes_saved']==len(original)
    assert os.path.samefile(a,b)
    assert a.read_bytes()==b.read_bytes()==original
    assert storage.adopt(b,expected=digest,apply=True)['status']=='already_linked'
    assert storage.scan(expected_by_id={a.parent.name:digest,b.parent.name:digest})['failures']==0


def test_corruption_and_unregistered_assets_fail_closed(tmp_path):
    store=ParquetBlobStore(tmp_path)
    a=create(tmp_path,b'PAR1samplePAR1')
    digest=sha256_file(a)
    with pytest.raises(BlobSafetyError,match='SHA-256'):
        store.adopt(a,expected='f'*64,apply=True)
    assert store.scan(expected_by_id={str(uuid4()):digest})['failures']==1
    assert store.scan(expected_by_id={})['results'][0]['status']=='rejected'
    assert store.scan(expected_by_id={})['apply'] is False
    good=store.adopt(a,expected=digest,apply=True)
    blob=tmp_path/good['blob_path']
    # Stop future adoption if a shared blob is altered.
    a.write_bytes(b'corrupted content')
    with pytest.raises(BlobSafetyError,match='damaged'):
        store.adopt(create(tmp_path,b'PAR1samplePAR1'),apply=True)
    assert blob.exists()


def test_symlink_rejected_and_orphan_gc_explicit(tmp_path):
    store=ParquetBlobStore(tmp_path)
    original=create(tmp_path,b'PAR1samplePAR1')
    linked=create(tmp_path,b'other')
    linked.unlink();linked.symlink_to(original)
    with pytest.raises(BlobSafetyError,match='symbolic'):
        store.adopt(linked,apply=True)
    result=store.adopt(original,apply=True)
    blob=tmp_path/result['blob_path']
    original.unlink()
    assert store.garbage_collect(apply=False)[0]['status']=='orphan'
    assert blob.is_file()
    assert store.garbage_collect(apply=True)[0]['status']=='deleted'
    assert not blob.exists()
