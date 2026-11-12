"""Spawn-isolated, wall-clock-bounded optional algorithm execution.

No user Python/SQL is evaluated. The worker receives only verified data frames,
resolved column names and bounded primitive settings.
"""
from __future__ import annotations
import multiprocessing as mp
import traceback
import time
from pathlib import Path


def _compute(conn,kind,primary,secondary,spec,path):
    try:
        from timepilot.feature_advanced import run_tsfresh,run_featuretools
        frame,proof=(run_tsfresh(primary,spec) if kind=='tsfresh'
                     else run_featuretools(primary,secondary,spec))
        if frame.empty or len(frame.columns)<2:
            raise ValueError('高成本算法没有生成有效候选')
        frame.to_parquet(path,index=False)
        conn.send({'ok':True,'rows':len(frame),'columns':list(frame.columns),'pit':proof})
    except Exception as exc:
        conn.send({'ok':False,'type':type(exc).__name__,'error':str(exc)[:800]})
    finally:
        conn.close()


def calculate_with_budget(kind,primary,secondary,spec,destination:Path):
    ctx=mp.get_context('spawn')
    recv,send=ctx.Pipe(duplex=False)
    process=ctx.Process(target=_compute,args=(send,kind,primary,secondary,spec,str(destination)),daemon=True)
    process.start();send.close()
    seconds=int(spec['limits']['max_seconds'])
    max_memory=int(spec['limits'].get('max_memory_mb',512))*1024**2
    try:
        import psutil
    except ImportError as exc:
        process.terminate();process.join(timeout=3);recv.close()
        raise ModuleNotFoundError('高级特征运行需要 psutil 才能执行实际进程内存门禁') from exc
    started=time.monotonic();peak=0;failure=None
    while process.is_alive():
        if time.monotonic()-started>seconds:
            failure=f'高级算法超过 {seconds} 秒硬时限';break
        try:
            target=psutil.Process(process.pid)
            resident=target.memory_info().rss+sum(
                child.memory_info().rss for child in target.children(recursive=True))
            peak=max(peak,resident)
            if resident>max_memory:
                failure=f'高级算法 RSS 超出 {max_memory//(1024**2)} MiB 预算';break
        except psutil.Error:
            pass
        process.join(timeout=.12)
    if failure:
        process.terminate();process.join(timeout=3)
        if process.is_alive():process.kill();process.join(timeout=2)
        recv.close();destination.unlink(missing_ok=True)
        raise TimeoutError(failure+'，未保存部分结果')
    try:
        if not recv.poll():
            raise RuntimeError(f'隔离计算进程退出（code={process.exitcode}），未生成证据')
        reply=recv.recv()
    finally:
        recv.close()
    if not reply.get('ok'):
        destination.unlink(missing_ok=True)
        if reply.get('type') in ('ModuleNotFoundError','ImportError'):
            raise ModuleNotFoundError(reply.get('error','缺少可选特征算法依赖'))
        raise ValueError('高级算法失败：'+reply.get('error','未知错误'))
    if not destination.is_file():
        raise RuntimeError('计算进程没有写出可核验的 Parquet')
    reply['peak_rss_bytes']=peak
    return reply
