"""v0.39.6: bounded, replaceable sklearn fit subprocess.

The server never imports a fitted estimator from the child (no pickle). The
child receives only numeric train/validation arrays through an owner-only temp
folder and returns finite predictions plus optional last-fold permutation
scores. Parent enforces a *real* deadline and process-tree RSS/CPU limits.
"""
from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .model_task_store import ModelTaskError

LOG = logging.getLogger(__name__)
FIT_SECONDS = 45.0
RSS_BYTES = 1536 * 1024 * 1024
CPU_SECONDS = 45.0


class ModelBudgetExceeded(ModelTaskError):
    """The supervisor killed the child before its over-budget result committed."""


def _terminate(proc):
    """Best-effort process-tree kill; no shell, no background survivor."""
    try:
        import psutil
        parent=psutil.Process(proc.pid)
        descendants=parent.children(recursive=True)
        for item in descendants:
            try:item.kill()
            except psutil.Error:pass
        try:parent.kill()
        except psutil.Error:pass
    except Exception:
        pass
    if proc.poll() is None:
        try:
            if os.name=='posix':os.killpg(proc.pid,signal.SIGKILL)
            else:proc.kill()
        except (ProcessLookupError,PermissionError,OSError):
            try:proc.kill()
            except OSError:pass
    try:proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        try:proc.kill();proc.wait(timeout=2)
        except (OSError,subprocess.TimeoutExpired):pass


def supervise(command, *, seconds=FIT_SECONDS, max_cpu_seconds=CPU_SECONDS,
              max_rss_bytes=RSS_BYTES, cwd=None, env=None, failure_path=None,
              stop_check=None):
    """Run only trusted application-built commands; measure the entire tree.

    Children may not produce unrestricted stdout/stderr (user data would leak).
    The parent may reject a completed process when no verifiable result exists.
    """
    import psutil
    if not 0 < seconds <= 180 or not 0 < max_cpu_seconds <= 180 or not 64*1024**2 <= max_rss_bytes <= 8*1024**3:
        raise ModelTaskError('Invalid supervisor budget')
    if stop_check is not None:stop_check()
    proc=subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,cwd=cwd,env=env,close_fds=True,
        **({'start_new_session':True} if os.name=='posix' else
           {'creationflags':subprocess.CREATE_NEW_PROCESS_GROUP}))
    start=time.monotonic();peak_rss=0;peak_cpu=0.;last_check=start-.3
    try:
        while proc.poll() is None:
            tick=time.monotonic()
            if stop_check is not None and tick-last_check>=.25:
                stop_check()
                last_check=tick
            elapsed=tick-start
            if elapsed >= seconds:
                raise ModelBudgetExceeded('MODEL_WALL_TIMEOUT: child training wall-clock limit exceeded')
            try:
                parent=psutil.Process(proc.pid)
                members=[parent,*parent.children(recursive=True)]
                rss=cpu=0.
                for item in members:
                    try:
                        info=item.memory_info()
                        rss+=info.rss
                        times=item.cpu_times()
                        cpu+=times.user+times.system
                    except (psutil.NoSuchProcess,psutil.AccessDenied,psutil.ZombieProcess):
                        continue
                peak_rss=max(peak_rss,rss);peak_cpu=max(peak_cpu,cpu)
                if rss>max_rss_bytes:
                    raise ModelBudgetExceeded('MODEL_RSS_LIMIT: child training resident memory limit exceeded')
                if cpu>max_cpu_seconds:
                    raise ModelBudgetExceeded('MODEL_CPU_LIMIT: child training CPU-time limit exceeded')
            except psutil.NoSuchProcess:
                pass
            time.sleep(min(.05,max(.001,seconds-elapsed)))
        if proc.returncode:
            reason='unknown_child_error'
            if failure_path is not None:
                try:
                    evidence=json.loads(Path(failure_path).read_text(encoding='utf-8'))
                    kind=evidence.get('type','unknown_child_error')
                    if isinstance(kind,str) and kind.isidentifier() and len(kind)<=80:
                        reason=kind
                    message=evidence.get('message','')
                    if isinstance(message,str) and message and len(message)<=300:
                        reason+=': '+message.replace('\n',' ').replace('\r',' ')
                except (OSError,ValueError,TypeError):
                    pass
            raise ModelTaskError('MODEL_CHILD_FAILED: '+reason)
        return {'wall_seconds':round(time.monotonic()-start,5),
                'peak_rss_bytes':int(peak_rss),'cpu_seconds':round(peak_cpu,5),
                'terminated':False}
    except BaseException:
        _terminate(proc)
        raise
    finally:
        if proc.poll() is None:_terminate(proc)


def fit_fold(train_x,train_y,validation_x,validation_y,*,kind,family,features,
             importance=False,seed=42,seconds=FIT_SECONDS,stop_check=None):
    import numpy as np
    if family not in ('linear','forest') or kind not in ('regression','binary_classification'):
        raise ModelTaskError('Estimator family outside fixed training allowlist')
    if len(train_x)!=len(train_y) or len(validation_x)!=len(validation_y):
        raise ModelTaskError('Model fold array lengths differ')
    with tempfile.TemporaryDirectory(prefix='timepilot-fit-') as temp:
        root=Path(temp)
        try:root.chmod(0o700)
        except OSError:pass
        inputs=root/'input.npz';outputs=root/'output.json'
        np.savez_compressed(inputs,train_x=train_x,train_y=train_y,
                            validation_x=validation_x,validation_y=validation_y)
        request={'kind':kind,'family':family,'features':features,'importance':bool(importance),'seed':int(seed)}
        (root/'request.json').write_text(json.dumps(request),encoding='utf-8')
        env=os.environ.copy()
        for key in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS','VECLIB_MAXIMUM_THREADS'):
            env[key]='1'
        source=str(Path(__file__).resolve().parent.parent)
        env['PYTHONPATH']=source+os.pathsep+env.get('PYTHONPATH','')
        cmd=[sys.executable,'-m','timepilot.model_fit_isolation','--child',str(root)]
        cost=supervise(cmd,seconds=seconds,max_cpu_seconds=seconds,max_rss_bytes=RSS_BYTES,
                       env=env,failure_path=root/'failure.json',stop_check=stop_check)
        if not outputs.is_file():raise ModelTaskError('MODEL_CHILD_NO_RESULT: isolated estimator returned no result')
        if outputs.stat().st_size>4*1024*1024:
            raise ModelTaskError('MODEL_CHILD_OVERSIZE: bounded prediction payload exceeded')
        data=json.loads(outputs.read_text(encoding='utf-8'))
        if data.get('rows')!=len(validation_x) or len(data.get('predictions',[]))!=len(validation_x):
            raise ModelTaskError('MODEL_CHILD_LENGTH_MISMATCH: validation output was incomplete')
        predictions=np.asarray(data['predictions'],dtype=float)
        if not np.isfinite(predictions).all():raise ModelTaskError('MODEL_CHILD_NONFINITE: nonfinite predictions')
        return predictions,data.get('importance'),cost


def _child(root):
    import numpy as np
    from threadpoolctl import threadpool_limits
    from .feature_evaluation import _fit_predict
    from .model_task_diagnostics import permutation_importance
    request=json.loads((root/'request.json').read_text(encoding='utf-8'))
    arrays=np.load(root/'input.npz',allow_pickle=False)
    with threadpool_limits(limits=1):
        pred,model=_fit_predict(arrays['train_x'],arrays['train_y'],arrays['validation_x'],
             request['kind'],family=request['family'],seed=request['seed'],return_model=True)
        importance=(permutation_importance(model,arrays['validation_x'],arrays['validation_y'],
                     request['features'],request['kind']=='binary_classification',seed=request['seed'])
                    if request['importance'] else None)
    pred=np.asarray(pred,dtype=float)
    if len(pred)!=len(arrays['validation_x']) or not np.isfinite(pred).all():
        raise ValueError('Invalid isolated predictions')
    # Write a single complete JSON file only after all estimator work succeeded.
    temp=root/'output.tmp'
    with temp.open('w',encoding='utf-8') as stream:
        json.dump({'rows':len(pred),'predictions':[float(x) for x in pred],
                   'importance':importance},stream,allow_nan=False)
    temp.replace(root/'output.json')


if __name__=='__main__':
    if len(sys.argv)!=3 or sys.argv[1]!='--child':sys.exit(2)
    try:
        _child(Path(sys.argv[2]))
    except Exception as exc:
        # Restricted per-worker file; no unbounded stderr or pickle crosses the
        # process boundary. The parent displays a bounded actionable reason.
        try:
            (Path(sys.argv[2])/'failure.json').write_text(json.dumps({
                'type':type(exc).__name__,
                'message':str(exc).replace('\r',' ').replace('\n',' ')[:300]
            },ensure_ascii=False),encoding='utf-8')
        except OSError:
            pass
        sys.exit(3)
