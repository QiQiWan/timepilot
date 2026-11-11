"""Non-destructive content-addressable Parquet deduplication.

Logical immutable BI snapshots retain their existing UUID/data.parquet paths,
metadata, version IDs and SHA-256. Exact-byte identical snapshots share one
physical inode via hard links to a hash-addressed blob. CSV vs Parquet are
intentionally different byte representations and cannot be hardlinked.

No symlinks, no in-place overwrite, no unverified adoption. If hard links are
not supported (network/cloud volumes), the original snapshot is left intact.
"""
from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path
from uuid import UUID,uuid4

from timepilot.storage import sha256_file


class BlobSafetyError(RuntimeError):
    pass


class ParquetBlobStore:
    def __init__(self,root:Path):
        self.root=Path(root).resolve()
        self.blobs=self.root/'_blobs'/'sha256'
        self.blobs.mkdir(parents=True,exist_ok=True)

    def _path(self,digest:str):
        if len(digest)!=64 or not all(c in '0123456789abcdef' for c in digest):
            raise BlobSafetyError('invalid SHA-256 identifier')
        return self.blobs/digest[:2]/(digest+'.parquet')

    def _validate(self,path:Path,expected:str|None=None):
        path=Path(path)
        if path.is_symlink() or not path.is_file():
            raise BlobSafetyError('snapshot is missing or symbolic link')
        try:
            UUID(path.parent.name)
        except ValueError:
            raise BlobSafetyError('snapshot path must be inside a native asset UUID directory') from None
        if path.name!='data.parquet' or path.parent.parent.resolve()!=self.root:
            raise BlobSafetyError('snapshot path is outside the immutable native BI store')
        if not stat.S_ISREG(path.stat().st_mode):
            raise BlobSafetyError('expected a regular snapshot')
        actual=sha256_file(path)
        if expected is not None and actual!=expected:
            raise BlobSafetyError('snapshot SHA-256 differs from database metadata')
        return actual,path.stat().st_size

    def adopt(self,path:Path,*,expected:str|None=None,apply:bool=False) -> dict:
        actual,size=self._validate(path,expected)
        blob=self._path(actual)
        result={'sha256':actual,'bytes':size,'blob_path':str(blob.relative_to(self.root)),
                'status':'candidate','bytes_saved':0}
        if blob.exists():
            if blob.is_symlink() or not blob.is_file() or blob.stat().st_size!=size or sha256_file(blob)!=actual:
                raise BlobSafetyError('hash-addressed blob is damaged; refusing to link')
            if os.path.samestat(blob.stat(),Path(path).stat()):
                return {**result,'status':'already_linked'}
            result['status']='deduplicable'
            result['bytes_saved']=size
        else:
            result['status']='new_blob'
        if not apply:
            return result
        blob.parent.mkdir(parents=True,exist_ok=True)
        if not blob.exists():
            try:
                os.link(path,blob)  # atomic publication: existing path remains authoritative
            except FileExistsError:
                pass
            except OSError as exc:
                result.update(status='hardlink_unavailable',bytes_saved=0,error=str(exc)[:200])
                return result
            if sha256_file(blob)!=actual:
                raise BlobSafetyError('new blob failed verification')
        if not os.path.samestat(blob.stat(),Path(path).stat()):
            replacement=Path(path).with_name('.dedup-'+uuid4().hex+'.tmp')
            try:
                os.link(blob,replacement)
                if sha256_file(replacement)!=actual:
                    raise BlobSafetyError('hardlinked replacement failed verification')
                os.replace(replacement,path)  # atomic pointer switch, never edits data bytes
            finally:
                replacement.unlink(missing_ok=True)
        assert os.path.samestat(blob.stat(),Path(path).stat())
        return {**result,'status':'linked','bytes_saved':size if result['status']=='deduplicable' else 0}

    def scan(self,*,apply:bool=False,expected_by_id:dict[str,str]|None=None,max_files:int|None=None):
        check_metadata=expected_by_id is not None
        expected_by_id=expected_by_id if expected_by_id is not None else {}
        results=[]
        count=0
        for path in sorted(self.root.glob('*/data.parquet')):
            if max_files is not None and count>=max_files:
                break
            try:
                UUID(path.parent.name)
            except ValueError:
                continue
            count+=1
            try:
                # Metadata must authorize any existing asset when scanning an installed catalog.
                if check_metadata and path.parent.name not in expected_by_id:
                    raise BlobSafetyError('snapshot is unregistered in native BI metadata')
                results.append({'asset_id':path.parent.name,
                    **self.adopt(path,expected=expected_by_id.get(path.parent.name),apply=apply)})
            except (BlobSafetyError,OSError) as exc:
                results.append({'asset_id':path.parent.name,'status':'rejected','error':str(exc)[:220]})
        return {'scan_count':count,'apply':apply,'saved_bytes':sum(int(x.get('bytes_saved',0)) for x in results if x['status'] in ('linked','deduplicable')),
                'failures':sum(x['status'] in ('rejected','hardlink_unavailable') for x in results),
                'results':results}

    def garbage_collect(self,*,apply=False):
        """Only unused blob *names*; never removes an immutable asset snapshot."""
        report=[]
        for blob in sorted(self.blobs.rglob('*.parquet')):
            if blob.is_symlink() or not blob.is_file():
                report.append({'path':str(blob),'status':'suspicious'});continue
            digest=blob.stem
            if digest!=sha256_file(blob):
                report.append({'path':str(blob),'status':'corrupt_kept'});continue
            refs=blob.stat().st_nlink
            # nlink=1 means only the CAS name remains, no logical asset hardlinks.
            if refs==1:
                report.append({'path':str(blob),'status':'deleted' if apply else 'orphan',
                               'bytes':blob.stat().st_size})
                if apply:blob.unlink()
        return report
