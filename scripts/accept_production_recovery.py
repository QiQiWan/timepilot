"""Actual process-kill / chunk-recovery acceptance without DuckDB or browser mocks.

Uses deterministic CSV rows, kills the publish worker with os._exit after two
verified fsync'ed chunks, resumes in a new process, compares SHA-256 to an
uninterrupted publication, validates corrupted chunk repair and source-hash
mismatch rejection. Never touches TIMEPILOT_HOME or any production files.

Example: python scripts/accept_production_recovery.py --rows 1000000
"""
from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from timepilot.generic_publish_checkpoint import materialize,checkpoint_status,CHUNK_ROWS
from timepilot.smart_import import SmartImportError,_hash_file

FIELDS = ['observed_at','device_id','temperature_c','pressure_kpa','maintenance_event','regime']
SOURCE_SHA_FINGERPRINT = 'sha256:'


def prepare_csv(source:Path,count:int):
    from datetime import datetime, timedelta
    source.parent.mkdir(parents=True,exist_ok=True)
    base=datetime(2024,1,1,0,0)
    with source.open('w',encoding='utf-8',newline='') as fh:
        writer=csv.writer(fh);writer.writerow(FIELDS)
        for i in range(count):
            # 24 devices, deterministic one-minute observations; events are not dropped.
            moment=base+timedelta(minutes=i//24)
            writer.writerow([moment.strftime('%Y-%m-%dT%H:%M:%S'),f'device-{i%24:02d}',
                f'{22+(i%301)/10:.1f}',f'{98+(i%97)/10:.1f}',
                'filter_change' if i%40441==0 else '', 'high' if i%72000<36000 else 'low'])


def rows_factory(source:Path,kill_after:int|None=None):
    def iterator():
        with source.open(encoding='utf-8',newline='') as fh:
            records=csv.reader(fh);next(records)
            for idx,record in enumerate(records,1):
                if kill_after is not None and idx==kill_after:
                    # Hard process death: no finally, heartbeat or cleanup can run.
                    os._exit(73)
                yield idx+1,record
    return iterator


def publish(source:Path,target:Path,expected:str,count:int,kill_after:int|None=None):
    return materialize(target=target,identity_hash='production-recovery-v021',
        source_sha256=expected,columns=FIELDS,rows=rows_factory(source,kill_after),max_rows=count+1)


def run(rows:int,out:Path,keep=False):
    if rows<CHUNK_ROWS*3 or rows>50_000_000:
        raise ValueError(f'Expected {CHUNK_ROWS*3} <= rows <= 50000000')
    started=time.perf_counter()
    root=Path(tempfile.mkdtemp(prefix='timepilot_recovery_v021_')).resolve()
    stages=[]
    result={'status':'running','rows':rows,'chunk_rows':CHUNK_ROWS,'source':'deterministic synthetic CSV',
            'tmp_root':str(root) if keep else '[deleted after acceptance]',
            'stages':stages,'real_process_kill':True,'actual_hash_comparison':True}
    out.parent.mkdir(parents=True,exist_ok=True)
    def record(stage,passed,**extra):
        stages.append({'stage':stage,'passed':passed,**extra})
        out.write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    try:
        source=root/'source.csv';t=time.perf_counter();prepare_csv(source,rows)
        digest=_hash_file(source)
        record('create_source',True,seconds=round(time.perf_counter()-t,3),bytes=source.stat().st_size,
               source_sha256=digest)
        damaged=root/'interrupted'
        kill_after=CHUNK_ROWS*2+30
        t=time.perf_counter()
        cmd=[sys.executable,str(Path(__file__).resolve()),'--_worker',str(source),str(damaged),digest,
             str(rows),str(kill_after)]
        child=subprocess.run(cmd,capture_output=True,text=True,timeout=max(100,rows//30000),cwd=ROOT)
        status=checkpoint_status(damaged)
        if child.returncode!=73 or status['completed_chunks']!=2 or status['completed_rows']!=2*CHUNK_ROWS:
            raise AssertionError(f'Process-kill checkpoint invalid: exit={child.returncode}, {status}, stderr={child.stderr[:200]}')
        record('hard_kill_two_committed_chunks',True,exit_code=child.returncode,
               completed_rows=status['completed_rows'],seconds=round(time.perf_counter()-t,3))
        copied=root/'corrupted-copy';shutil.copytree(damaged,copied)
        with (copied/'.chunks'/'000000.csv').open('ab') as fh:fh.write(b'CORRUPTED\n')
        t=time.perf_counter()
        resumed=publish(source,damaged,digest,rows)
        if resumed['row_count']!=rows:raise AssertionError('resume lost or duplicated rows')
        record('resume_after_process_kill',True,bytes=damaged.joinpath('data.csv').stat().st_size,
               reused_chunks=2,seconds=round(time.perf_counter()-t,3),sha256=resumed['sha256'])
        t=time.perf_counter()
        good=publish(source,root/'uninterrupted',digest,rows)
        if good['sha256']!=resumed['sha256']:
            raise AssertionError('interrupted vs uninterrupted SHA-256 mismatch')
        record('full_sha256_equals_uninterrupted',True,
               sha256=good['sha256'],seconds=round(time.perf_counter()-t,3))
        t=time.perf_counter()
        repaired=publish(source,copied,digest,rows)
        if repaired['sha256']!=good['sha256']:
            raise AssertionError('corrupted chunk reused or not repaired')
        record('corrupted_part_regenerated',True,seconds=round(time.perf_counter()-t,3))
        try:
            publish(source,damaged,'0'*64,rows)
        except SmartImportError as exc:
            record('source_fingerprint_mismatch_rejected',True,reason=str(exc)[:180])
        else:raise AssertionError('source mismatch was allowed to reuse checkpoint')
        result['status']='passed'
    except Exception as exc:
        result['status']='failed';result['failure']=f'{type(exc).__name__}: {exc}'
        raise
    finally:
        result['elapsed_seconds']=round(time.perf_counter()-started,3)
        out.write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
        if not keep:shutil.rmtree(root,ignore_errors=True)
    return result


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--rows',type=int,default=100000)
    p.add_argument('--out',type=Path,default=ROOT/'reports'/'production_recovery.json')
    p.add_argument('--keep',action='store_true')
    p.add_argument('--_worker',nargs=5,metavar=('SOURCE','TARGET','SOURCE_SHA','ROWS','KILL_AFTER'))
    args=p.parse_args()
    if args._worker:
        s,t,d,n,k=args._worker
        publish(Path(s),Path(t),d,int(n),int(k))
        return 1  # worker must have died; reaching here is an acceptance failure
    result=run(args.rows,args.out,args.keep)
    print(json.dumps({'status':result['status'],'rows':result['rows'],
        'elapsed_seconds':result['elapsed_seconds'],'report':str(args.out.resolve())},ensure_ascii=False))
    return 0 if result['status']=='passed' else 1

if __name__=='__main__':raise SystemExit(main())
