"""Bounded chronological/plugin model jobs in separate processes (v0.39.6).

No plugin model objects cross process boundaries. The child persists its own
built-in plugin's model.json under the run's server-resolved work directory;
its parent checks complete bounded numeric predictions before publishing.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

from .model_fit_isolation import supervise,RSS_BYTES
from .model_task_store import ModelTaskError

ALLOW = frozenset(('last_value','moving_average','seasonal_naive','lag_ridge',
                   'historical_exog_ridge','autogluon_timeseries','binary_logistic'))


def bounded_plugin(plugin,train,valid,folder,*,task,kind='forecast',test=None,seconds=None,stop_check=None):
    import pandas as pd
    import numpy as np
    if plugin not in ALLOW:
        raise ModelTaskError('Plugin outside isolated built-in model allowlist')
    if kind not in ('forecast','classification','final_forecast'):
        raise ModelTaskError('Unknown isolated temporal model operation')
    # Honor the approved task-specific budget, never longer than the
    # administrator's bounded 180-second maximum per model invocation.
    cap=float(min(180,max(10,task.training_budget_seconds))) if seconds is None else float(seconds)
    folder=Path(folder).resolve()
    folder.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='timepilot-temporal-fit-') as temp:
        root=Path(temp)
        try:root.chmod(0o700)
        except OSError:pass
        train.to_csv(root/'train.csv',index=False)
        if valid is not None:valid.to_csv(root/'valid.csv',index=False)
        if test is not None:test.to_csv(root/'test.csv',index=False)
        payload={'plugin':plugin,'kind':kind,'output_dir':str(folder),
                 'frequency':task.frequency,'horizon':task.forecast_horizon,
                 'seasonal_period':task.seasonal_period,'budget_seconds':task.training_budget_seconds,
                 'features':list(task.feature_columns),
                 'reset_policy':task.modeling_contract.get('event',{}).get('reset_policy')}
        (root/'request.json').write_text(json.dumps(payload),encoding='utf-8')
        env=os.environ.copy()
        for field in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS',
                      'NUMEXPR_NUM_THREADS','VECLIB_MAXIMUM_THREADS'):
            env[field]='1'
        env['PYTHONPATH']=str(Path(__file__).resolve().parent.parent)+os.pathsep+env.get('PYTHONPATH','')
        limits=supervise([sys.executable,'-m','timepilot.model_legacy_isolation',
                          '--child',str(root)],seconds=cap,max_cpu_seconds=cap,
                         max_rss_bytes=2*1024**3,env=env,failure_path=root/'failure.json',
                         stop_check=stop_check)
        artifact=root/'result.json'
        if not artifact.is_file() or artifact.stat().st_size>4*1024**2:
            raise ModelTaskError('Temporal worker produced no bounded numeric result')
        result=json.loads(artifact.read_text(encoding='utf-8'))
        for key in ('prediction','test_prediction'):
            if result.get(key) is not None:
                expected=(task.forecast_horizon if kind in ('forecast','final_forecast') else len(valid)) if key=='prediction' else len(test)
                values=np.asarray(result[key],dtype=float)
                if len(values)!=expected or not np.isfinite(values).all():
                    raise ModelTaskError('Temporal plugin returned invalid probabilities or predictions')
        result['budget_observed']=limits
        return result


def _read(path):
    import pandas as pd
    frame=pd.read_csv(path)
    for field in ('timestamp','available_at','label_available_at'):
        if field in frame:frame[field]=pd.to_datetime(frame[field],utc=True,errors='raise')
    return frame


def _child(root):
    import numpy as np
    from threadpoolctl import threadpool_limits
    from .plugins import PluginRegistry
    request=json.loads((root/'request.json').read_text(encoding='utf-8'))
    if request['plugin'] not in ALLOW:
        raise ValueError('Unsupported plugin')
    plugin=PluginRegistry().get(request['plugin'])
    train=_read(root/'train.csv')
    if request.get('reset_policy')=='exclude_cross_event_windows':
        train.attrs['reset_policy']='exclude_cross_event_windows'
    mode=request['kind'];folder=Path(request['output_dir'])
    with threadpool_limits(limits=1):
        if mode in ('forecast','final_forecast'):
            model=plugin.fit(train,request['horizon'],request['frequency'],folder,
                       request['budget_seconds'],seasonal_period=request['seasonal_period'])
            prediction=(plugin.predict_from_history(model,train,request['horizon'],request['frequency'])
                         if hasattr(plugin,'predict_from_history') else
                         plugin.predict(model,request['horizon'],request['frequency']))
            output={'prediction':[float(x) for x in prediction]}
        elif mode=='classification':
            valid=_read(root/'valid.csv')
            test=_read(root/'test.csv') if (root/'test.csv').exists() else None
            model=plugin.fit_classifier(train,request['features'],folder)
            probability=plugin.predict_proba(model,valid)
            output={'prediction':[float(x) for x in probability]}
            if test is not None:
                output['test_prediction']=[float(x) for x in plugin.predict_proba(model,test)]
        else:raise ValueError('Unknown plugin operation')
    for values in output.values():
        if not np.isfinite(np.asarray(values,dtype=float)).all():
            raise ValueError('Nonfinite plugin output')
    staged=root/'result.pending.json'
    staged.write_text(json.dumps(output,allow_nan=False),encoding='utf-8')
    staged.replace(root/'result.json')


if __name__=='__main__':
    if len(sys.argv)!=3 or sys.argv[1]!='--child':sys.exit(2)
    try:
        _child(Path(sys.argv[2]))
    except Exception as exc:
        try:
            (Path(sys.argv[2])/'failure.json').write_text(json.dumps({
                'type':type(exc).__name__,
                'message':str(exc).replace('\r',' ').replace('\n',' ')[:300]
            },ensure_ascii=False),encoding='utf-8')
        except OSError:
            pass
        sys.exit(3)
