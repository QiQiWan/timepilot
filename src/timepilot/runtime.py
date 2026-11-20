"""Durable run engine, optimistic leasing and per-model-per-fold checkpoints."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import platform
import shutil
import sys
import threading
import time
import traceback
from datetime import timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pandas as pd
import numpy as np
from sqlalchemy import and_, or_, select, update

from timepilot.config import Settings
from timepilot.contracts import ForecastRecord, RunManifest, RunStatus, TaskSpec
from timepilot.data import DataValidationError, get_dataset
from timepilot.db import RunRow, Store, utcnow
from timepilot.temporal_experiment_control import (TemporalExperimentControl,
    TemporalBudgetExpired, TemporalLeaseLost)
from timepilot.metrics import calculate, ranking_score
from timepilot.plugins import PluginRegistry
from timepilot.industrial_plugins import temporal_classification_split, confusion_metrics, choose_threshold
from timepilot.modeling_adapters import ClassificationSpec
from timepilot.quality import make_quality
from timepilot.storage import atomic_json, load_json, sha256_file, sha256_bytes

logger = logging.getLogger(__name__)


def _live_lease(until):
    if until is None:return False
    # SQLite returns naive UTC even for DateTime(timezone=True).
    if until.tzinfo is None:until=until.replace(tzinfo=timezone.utc)
    return until>utcnow()



class RunCancelled(Exception):
    pass


class InsufficientHistory(ValueError):
    pass


class RuntimeEngine:
    def __init__(self, settings: Settings, store: Store, plugins: PluginRegistry | None = None, worker_id: str | None = None):
        self.settings = settings
        self.store = store
        self.plugins = plugins or PluginRegistry()
        self.worker_id = worker_id or f"worker-{str(uuid4())[:12]}"
        settings.ensure_dirs()
        self.budgets=TemporalExperimentControl(store)
        self._active_attempts={}

    def run_dir(self, run_id: str) -> Path:
        from timepilot.storage import safe_uuid
        return self.settings.home / "runs" / safe_uuid(run_id)

    def queue(self, task: TaskSpec, dataset: str, *, budget_profile: str = '15m') -> str:
        row = self.store.dataset_by_name_or_id(dataset)
        if row is None:
            raise KeyError(f"Unknown dataset: {dataset}")
        for candidate in task.model_candidates:
            self.plugins.get(candidate)
        if not task.approved:
            raise ValueError("Task requires approval before entering the execution queue.")
        # Queue + immutable budget are one transaction; a fast worker cannot
        # claim a run before its budget has been frozen.
        run_id = str(uuid4())
        with self.store.sessions.begin() as session:
            session.add(RunRow(id=run_id,task_json=task.model_dump_json(),
                               dataset_id=row.id,task_id=task.task_id))
            self.budgets.freeze(session,run_id,budget_profile)
        root = self.run_dir(run_id)
        task_bytes = task.model_dump_json().encode("utf-8")
        atomic_json(root / "task.json", task.model_dump(mode="json"))
        dataset_manifest = json.loads(row.manifest_json)
        model_versions = {name: self.plugins.get(name).capabilities().version for name in task.model_candidates}
        manifest = RunManifest(run_id=run_id, task_id=task.task_id, dataset_id=row.id,
                               dataset_snapshot_sha256=dataset_manifest["snapshot_sha256"],
                               task_snapshot_sha256=sha256_bytes(task_bytes), created_at=utcnow(),
                               python_version=".".join(str(x) for x in sys.version_info[:3]),
                               platform=platform.platform(), package_versions={"pandas": pd.__version__},
                               plugin_versions=model_versions)
        atomic_json(root / "run_manifest.json", manifest.model_dump(mode="json"))
        self._event(run_id, "queued", "Task accepted; awaiting worker")
        return run_id

    def _event(self, run_id: str, kind: str, message: str, **details):
        target = self.run_dir(run_id) / "events.jsonl"
        target.parent.mkdir(parents=True, exist_ok=True)
        entry = {"timestamp": utcnow().isoformat(), "event": kind, "message": message, **details}
        with target.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")

    def events(self, run_id: str, tail: int = 100) -> list[dict]:
        from collections import deque
        path = self.run_dir(run_id) / "events.jsonl"
        if not path.exists():
            return []
        with path.open(encoding="utf-8") as f:
            return [json.loads(x) for x in deque(f, maxlen=min(tail, 1000)) if x.strip()]

    def claim(self, run_id: str | None = None) -> str | None:
        now = utcnow()
        expires = now + timedelta(seconds=self.settings.lease_seconds)
        with self.store.sessions.begin() as session:
            due = or_(RunRow.status == RunStatus.QUEUED, and_(RunRow.status == RunStatus.RUNNING, RunRow.lease_until < now))
            if run_id:
                ids = [run_id]
            else:
                ids = list(session.scalars(select(RunRow.id).where(due).order_by(RunRow.created_at).limit(8)))
            for candidate in ids:
                result = session.execute(
                    update(RunRow).where(RunRow.id == candidate, due, RunRow.cancel_requested.is_(False))
                    .values(status=RunStatus.RUNNING, stage="starting", worker_id=self.worker_id,
                            lease_until=expires, started_at=now, attempts=RunRow.attempts + 1)
                )
                if result.rowcount == 1:
                    claimed=session.get(RunRow,candidate)
                    if not self.budgets.on_claim(session,candidate,claimed.attempts):
                        session.execute(update(RunRow).where(RunRow.id==candidate,
                            RunRow.worker_id==self.worker_id,RunRow.attempts==claimed.attempts)
                            .values(status=RunStatus.FAILED,stage='budget_exhausted',
                                    finished_at=utcnow(),lease_until=None,
                                    error_code='EXPERIMENT_BUDGET_EXHAUSTED',
                                    error_message='Frozen temporal experiment deadline expired'))
                        continue
                    self._active_attempts[candidate]=claimed.attempts
                    return candidate
        return None

    def _heartbeat(self, run_id: str, stop: threading.Event):
        interval = min(5.0,max(1.0,self.settings.lease_seconds / 3.0))
        while not stop.wait(interval):
            with self.store.sessions.begin() as session:
                result = session.execute(update(RunRow).where(RunRow.id == run_id,
                                                RunRow.worker_id == self.worker_id,
                                                RunRow.attempts == self._active_attempts.get(run_id,-1),
                                                RunRow.lease_until > utcnow(),
                                                RunRow.status == RunStatus.RUNNING)
                                         .values(lease_until=utcnow() + timedelta(seconds=self.settings.lease_seconds)))
                if result.rowcount != 1:
                    return

    def _update_owned(self, run_id: str, **fields):
        with self.store.sessions.begin() as session:
            result = session.execute(update(RunRow).where(RunRow.id == run_id,
                RunRow.worker_id == self.worker_id,
                RunRow.attempts == self._active_attempts.get(run_id,-1),
                RunRow.lease_until > utcnow(),
                RunRow.status == RunStatus.RUNNING).values(**fields))
            if result.rowcount != 1:
                raise RuntimeError("Worker lease was lost; stopping to avoid stale writes")

    def _cancel_check(self, run_id: str):
        row = self.store.run(run_id)
        if row is None or row.cancel_requested:
            raise RunCancelled("Run cancellation requested")
        if (row.status!=RunStatus.RUNNING or row.worker_id!=self.worker_id or
            row.attempts!=self._active_attempts.get(run_id,-1) or
            not _live_lease(row.lease_until)):
            raise TemporalLeaseLost('TEMPORAL_LEASE_LOST: run ownership changed')
        self.budgets.assert_active(run_id)

    def cancel(self, run_id: str):
        row = self.store.run(run_id)
        if row is None:
            raise KeyError(run_id)
        if row.status in (RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED):
            return row.status
        if row.status == RunStatus.QUEUED:
            self.store.update_run(run_id, status=RunStatus.CANCELLED, cancel_requested=True, finished_at=utcnow())
        else:
            self.store.update_run(run_id, cancel_requested=True)
        self._event(run_id, "cancel_requested", "Cancellation requested by user")
        return "cancel_requested"

    def _stage(self, run_id: str, stage: str, progress: int, fn):
        self._cancel_check(run_id)
        stage_dir = self.run_dir(run_id) / 'stages'
        receipt = stage_dir / f'{stage}.json'
        attempt=self._active_attempts.get(run_id,-1)
        self.budgets.started(run_id,stage,attempt)
        try:
            if receipt.exists():
                meta=load_json(receipt)
                output=self.run_dir(run_id)/meta['artifact']
                if (output.exists() and sha256_file(output)==meta['sha256']):
                    self._event(run_id,'stage_skipped',f'Verified checkpoint: {stage}')
                    self._update_owned(run_id,stage=stage,progress=progress)
                    self._cancel_check(run_id)
                    self.budgets.finished(run_id,stage,attempt,'succeeded')
                    return load_json(output)
            self._update_owned(run_id,stage=stage,progress=progress)
            self._event(run_id,'stage_started',stage)
            value=fn()
            self._cancel_check(run_id)
            relative=f'stages/{stage}_result.json'
            output=self.run_dir(run_id)/relative
            atomic_json(output,value)
            atomic_json(receipt,{'stage':stage,'artifact':relative,'sha256':sha256_file(output)})
            self._cancel_check(run_id)
            self.budgets.finished(run_id,stage,attempt,'succeeded')
            self._event(run_id,'stage_completed',stage)
            return value
        except BaseException as exc:
            self.budgets.finished(run_id,stage,attempt,'failed',type(exc).__name__)
            raise

    @staticmethod
    def _window(series: pd.DataFrame, start: int, horizon: int, frequency: str) -> tuple[pd.DataFrame, pd.DataFrame, pd.Timestamp]:
        train = series.iloc[:start].copy()
        target = series.iloc[start:start + horizon].copy()
        if len(target) < horizon or train.empty:
            raise InsufficientHistory("Not enough history to evaluate forecasting window")
        origin = train["available_at"].max()
        # Data known after forecast origin must never enter the historical model context.
        train = train.loc[train["available_at"] <= origin].copy()
        if train.empty:
            raise InsufficientHistory("No data available at forecast origin")
        expected_train = pd.date_range(start=train["timestamp"].iloc[0], periods=len(train), freq=frequency, tz="UTC")
        if not pd.DatetimeIndex(train["timestamp"]).equals(expected_train):
            raise ValueError("Point-in-time filtering creates gaps in training history; explicit gap policy required")
        if (target["available_at"] <= origin).any():
            raise ValueError("Future target is already known at prediction origin; invalid holdout")
        expected = pd.date_range(start=train["timestamp"].max(), periods=horizon + 1, freq=frequency, tz="UTC")[1:]
        actual_dates = pd.DatetimeIndex(target["timestamp"])
        if not actual_dates.equals(expected):
            raise ValueError("Irregular frequency or missing periods within forecast horizon")
        return train, target, origin

    def _forecast_one(self, run_id: str, task: TaskSpec, plugin_name: str,
                      series_id: str, history: pd.DataFrame, target: pd.DataFrame, origin: pd.Timestamp,
                      *, split: str, cache_key: str, unit: str) -> list[dict]:
        self._cancel_check(run_id)
        safe_series = hashlib.sha256(series_id.encode()).hexdigest()[:16]
        folder = self.run_dir(run_id) / "work" / plugin_name / safe_series / cache_key
        cache = folder / "forecast.json"
        digest = folder / "forecast.sha256"
        if cache.exists() and digest.exists() and sha256_file(cache) == digest.read_text(encoding="utf-8").strip():
            cached = load_json(cache)
            if (cached.get("plugin_version") == self.plugins.get(plugin_name).capabilities().version
                    and cached.get('hard_budget_enforced') is True):
                return cached["records"]
        plugin = self.plugins.get(plugin_name)
        if task.modeling_contract.get('event',{}).get('reset_policy')=='exclude_cross_event_windows':
            history=history.copy()
            history.attrs['reset_policy']='exclude_cross_event_windows'
        from .model_legacy_isolation import bounded_plugin
        isolated = bounded_plugin(plugin_name,history,None,folder,task=task,kind='forecast',
                                  stop_check=lambda: self._cancel_check(run_id))
        predictions = isolated['prediction']
        if len(predictions) != task.forecast_horizon:
            raise ValueError("Plugin prediction length does not match task horizon")
        constraint=task.modeling_contract.get('mechanism') or {}
        if constraint.get('enforcement')=='block':
            lower=constraint.get('target_min');upper=constraint.get('target_max')
            if lower is not None and any(v<lower for v in predictions):
                raise ValueError('预测结果低于已确认机理下界')
            if upper is not None and any(v>upper for v in predictions):
                raise ValueError('预测结果高于已确认机理上界')
            if constraint.get('max_step_change') is not None:
                steps=[float(history['target'].iloc[-1]),*map(float,predictions)]
                if any(abs(b-a)>constraint['max_step_change'] for a,b in zip(steps,steps[1:])):
                    raise ValueError('预测结果超过已确认最大单步变化率')
        values = history["target"].to_numpy(dtype=float)
        lag = task.seasonal_period or (12 if task.frequency == "MS" else 7 if task.frequency == "D" else 24)
        scale = float(abs(values[lag:] - values[:-lag]).mean()) if len(values) > lag else None
        if scale is not None and scale <= 0:
            scale = None
        records = [ForecastRecord(run_id=run_id, model_id=plugin_name, series_id=series_id,
                                  prediction_origin=origin.to_pydatetime(),
                                  target_timestamp=row["timestamp"].to_pydatetime(), horizon_step=i + 1,
                                  mean=float(predictions[i]), actual=float(row["target"]), mase_scale=scale, unit=unit, split=split).model_dump(mode="json")
                   for i, (_, row) in enumerate(target.iterrows())]
        atomic_json(cache, {"plugin_version": plugin.capabilities().version,
                            "hard_budget_enforced":True, "budget_observed":isolated['budget_observed'],
                            "records": records})
        from timepilot.storage import atomic_write_bytes
        atomic_write_bytes(digest, (sha256_file(cache) + "\n").encode())
        return records

    def _validate(self, task: TaskSpec, df: pd.DataFrame, manifest):
        free = shutil.disk_usage(self.settings.home).free / (1024 ** 3)
        if free < self.settings.min_free_gb:
            raise OSError(f"Insufficient free disk space: {free:.2f} GB available, requires {self.settings.min_free_gb:.2f} GB")
        if task.task_kind != manifest.task_kind:
            raise ValueError('任务类型与冻结数据版本不一致')
        if task.feature_columns != manifest.feature_columns:
            raise ValueError('任务所声明特征与训练数据清单不一致')
        if task.modeling_contract != manifest.modeling_contract:
            raise ValueError('模型契约与已审核的数据版本不一致')
        if task.frequency != manifest.frequency:
            raise ValueError(f"Task frequency {task.frequency} does not match dataset frequency {manifest.frequency}")
        if task.target != "target" or task.entity_keys != ["series_id"]:
            raise ValueError("v0.1 supports normalized target and series_id only")
        for plugin_name in task.model_candidates:
            plugin = self.plugins.get(plugin_name)
            cap=plugin.capabilities()
            if task.task_kind not in cap.task_kinds:
                raise ValueError(f'{plugin_name} 插件不支持任务类型 {task.task_kind}')
            if task.feature_columns and task.task_kind=='continuous_forecast' and not cap.supports_observed_features:
                raise ValueError(f'{plugin_name} 不支持多变量历史特征')
            if task.frequency not in plugin.capabilities().supports_frequency:
                raise ValueError(f"{plugin_name} does not support frequency {task.frequency}")
        if task.task_kind=='classification':
            if not task.feature_columns:raise ValueError('分类缺少数值特征')
            if 'label_available_at' not in df:raise ValueError('分类数据缺少标签成熟时刻')
            if not set(df['target'].unique()).issubset({0,1}):raise ValueError('分类标签必须为 0/1')
            return
        h = task.forecast_horizon
        model_min = (task.seasonal_period or (12 if task.frequency == "MS" else 7 if task.frequency == "D" else 24)) if "seasonal_naive" in task.model_candidates else 2
        need = max(task.evaluation.min_history, model_min) + 2 * h + task.evaluation.validation_windows - 1
        for series_id, group in df.groupby("series_id"):
            if len(group) < need:
                raise InsufficientHistory(f"Series '{series_id}' needs >= {need} observations, got {len(group)}")
            sorted_group = group.sort_values("timestamp")
            expected = pd.date_range(start=sorted_group["timestamp"].iloc[0], periods=len(group), freq=task.frequency, tz="UTC")
            if not pd.DatetimeIndex(sorted_group["timestamp"]).equals(expected):
                raise ValueError(f"Series '{series_id}' has gaps or off-frequency timestamps")
        return {"validated_series": int(df.series_id.nunique()), "records": int(len(df)),
                "forecast_horizon": h, "protocol_id": task.evaluation.protocol_id}

    def _backtest(self, run_id: str, task: TaskSpec, df: pd.DataFrame, *, unit: str):
        horizon = task.forecast_horizon
        windows = task.evaluation.validation_windows
        from .model_temporal_evidence import fold_manifest,prediction_row
        from .model_task_spec import canonical_sha
        val_all: dict[str, list[dict]] = {}
        selected: dict[str, str] = {}
        manifests=[];fold_scores={name:[] for name in task.model_candidates}
        validation_predictions=[];fit_cost={name:[] for name in task.model_candidates}
        for series_id, group in df.groupby("series_id", sort=True):
            self._cancel_check(run_id)
            group = group.sort_values("timestamp").reset_index(drop=True)
            n = len(group)
            model_scores: list[tuple[float, str]] = []
            for name in task.model_candidates:
                rows: list[dict] = []
                for fold in range(windows):
                    start = n - 2 * horizon - (windows - 1) + fold
                    history, target, origin = self._window(group, start, horizon, task.frequency)
                    # A single frozen fold manifest is shared by all candidates.
                    if name==task.model_candidates[0]:
                        manifests.append(fold_manifest(history,target,fold=fold+1,
                            series=str(series_id),cutoff=origin,protocol='rolling_origin',
                            extra={'validation_label_maturity_scope':'retrospective_after_origin'}))
                    self._event(run_id, "fold_started", "Validation fold", series=series_id, model=name, fold=fold + 1)
                    started=time.monotonic()
                    predicted = self._forecast_one(run_id, task, name, str(series_id), history, target, origin,
                                               split="validation", cache_key=f"validation-{fold}", unit=unit)
                    fit_cost[name].append(round(time.monotonic()-started,5))
                    fold_scores[name].append({'series_id':str(series_id),'fold':fold+1,**calculate(predicted)})
                    for j,rec in enumerate(predicted):
                        validation_predictions.append(prediction_row(model=name,fold=fold+1,
                            series=str(series_id),row_index=start+j,truth=rec['actual'],
                            prediction=rec['mean'],timestamp=rec['target_timestamp'],origin=origin,
                            source_available_at=target.iloc[j]['available_at'],classification=False))
                    rows+=predicted
                val_all[f"{series_id}::{name}"] = rows
                score = ranking_score(calculate(rows), task.evaluation.primary_metric)
                model_scores.append((score, name))
            selected[str(series_id)] = min(model_scores, key=lambda v: (v[0], v[1]))[1]
            self._event(run_id, "model_selected", f"Selected model {selected[str(series_id)]}", series=series_id)
        selected_validation = [record for key, records in val_all.items()
                               if key.rsplit("::", 1)[1] == selected[key.rsplit("::", 1)[0]] for record in records]
        if len(validation_predictions)>240000:
            raise ValueError('Temporal prediction artifact exceeds the 240,000-row bound')
        protocol={'schema':'model-fold-evidence-v2','engine':'existing_temporal_pit',
            'split_kind':'rolling_origin','folds':[{'train_index_sha256':x['train_index_sha256'],
            'validation_index_sha256':x['validation_index_sha256']} for x in manifests],
            'models':list(task.model_candidates),'uniform_model_budget_seconds':task.training_budget_seconds}
        return {"selected": selected, "validation_selected": selected_validation,
                "candidate_metrics": {key: calculate(rows) for key, rows in val_all.items()},
                'folds':manifests,'fold_scores':fold_scores,
                'validation_predictions':validation_predictions,
                'comparison_protocol':{'schema_version':'model-fold-evidence-v2',
                    'split_kind':'rolling_origin',
                    'same_validation_rows_and_fold_indices':True,'same_source':True,
                    'protocol_sha256':canonical_sha(protocol),
                    'equal_realized_compute':False,'scope':'paired chronological retrospective validation'},
                'fit_seconds_per_fold':fit_cost}

    def _test_and_future(self, run_id: str, task: TaskSpec, df: pd.DataFrame, selected: dict[str, str], *, unit: str):
        test_rows = []
        future_rows = []
        model_manifests = []
        for series_id, group in df.groupby("series_id", sort=True):
            self._cancel_check(run_id)
            group = group.sort_values("timestamp").reset_index(drop=True)
            n = len(group)
            name = selected[str(series_id)]
            history, target, origin = self._window(group, n - task.forecast_horizon,
                                                   task.forecast_horizon, task.frequency)
            test_rows.extend(self._forecast_one(run_id, task, name, str(series_id), history, target, origin,
                                                split="test", cache_key="test", unit=unit))
            # Full refit after independent test evaluation for future model delivery.
            plugin = self.plugins.get(name)
            path = self.run_dir(run_id) / "final_models" / hashlib.sha256(str(series_id).encode()).hexdigest()[:16]
            final_history = group.copy()
            if task.modeling_contract.get('event', {}).get('reset_policy') == 'exclude_cross_event_windows':
                final_history.attrs['reset_policy'] = 'exclude_cross_event_windows'
            from .model_legacy_isolation import bounded_plugin
            final_run=bounded_plugin(name,final_history,None,path,task=task,kind='final_forecast',
                                     stop_check=lambda: self._cancel_check(run_id))
            from timepilot.storage import atomic_write_bytes
            history_path = path / "history.csv"
            atomic_write_bytes(history_path, group.to_csv(index=False).encode("utf-8"))
            model_manifests.append({"series_id": str(series_id), "plugin_id": name,
                                    "plugin_version": plugin.capabilities().version,
                                    "artifact": str(path.relative_to(self.run_dir(run_id)) / "model.json"),
                                    "history": str(history_path.relative_to(self.run_dir(run_id))),
                                    "history_sha256": sha256_file(history_path)})
            future = final_run['prediction']
            physical=task.modeling_contract.get('mechanism') or {}
            if physical.get('enforcement')=='block':
                if physical.get('target_min') is not None and any(x<physical['target_min'] for x in future):
                    raise ValueError('最终未来温度预测超出物理下界')
                if physical.get('target_max') is not None and any(x>physical['target_max'] for x in future):
                    raise ValueError('最终未来温度预测超出物理上界')
                if physical.get('max_step_change') is not None:
                    sequence = [float(group['target'].iloc[-1]), *map(float, future)]
                    if any(abs(b-a)>physical['max_step_change'] for a,b in zip(sequence,sequence[1:])):
                        raise ValueError('最终未来预测违反最大单步变化率约束')
            origin = group["available_at"].max()
            future_times = pd.date_range(start=group["timestamp"].iloc[-1], periods=task.forecast_horizon + 1,
                                         freq=task.frequency, tz="UTC")[1:]
            for i, (ts, value) in enumerate(zip(future_times, future)):
                future_rows.append(ForecastRecord(run_id=run_id, model_id=name, series_id=str(series_id),
                                                  prediction_origin=origin.to_pydatetime(),
                                                  target_timestamp=ts.to_pydatetime(), horizon_step=i + 1,
                                                  mean=float(value), actual=None, unit=unit, split="future").model_dump(mode="json"))
        return {"test": test_rows, "future": future_rows, "models": model_manifests}

    def _classify(self,run_id:str,task:TaskSpec,df:pd.DataFrame):
        cfg=task.modeling_contract.get('classification') or {}
        spec=ClassificationSpec.model_validate(cfg)
        partitions=temporal_classification_split(df,spec)
        split_audit=partitions['audit']
        from .model_temporal_evidence import fold_manifest,prediction_row
        from .model_task_spec import canonical_sha
        # Roll forward within the historical validation segment. The independent
        # test period is never used for model/threshold selection.
        historical=pd.concat([partitions['train'],partitions['validation']]).sort_values('timestamp')
        valid_times=sorted(pd.to_datetime(partitions['validation']['timestamp'],utc=True).unique())
        requested=task.evaluation.validation_windows
        device_ood=task.evaluation.protocol_id=='device_forward_holdout_v1'
        if device_ood:
            entities=sorted(str(x) for x in historical['series_id'].unique())
            if len(entities)<requested:
                raise ValueError('Device OOD requires at least one distinct device per fold')
            blocks=np.array_split(np.array(entities,dtype=object),requested)
        else:
            if len(valid_times)<requested*2:
                raise ValueError('Insufficient unique validation timestamps for declared temporal folds')
            blocks=np.array_split(np.array(valid_times,dtype=object),requested)
        folds=[];fold_scores={name:[] for name in task.model_candidates}
        validation_predictions=[];fit_cost={name:[] for name in task.model_candidates}
        for number,block in enumerate(blocks,1):
            times=pd.to_datetime(historical['timestamp'],utc=True)
            matured=pd.to_datetime(historical['label_available_at'],utc=True)
            extra={}
            if device_ood:
                heldout={str(x) for x in block.tolist()}
                group=historical['series_id'].astype(str)
                validation_clock=pd.to_datetime(partitions['validation']['timestamp'],utc=True)
                cutoff=validation_clock.min()
                train=historical.loc[(times<cutoff)&(matured<=cutoff)&(~group.isin(heldout))].copy()
                valid=partitions['validation'].loc[
                    partitions['validation']['series_id'].astype(str).isin(heldout)].copy()
                # Do not allow held-out device labels or samples in train, even
                # when the same device appeared in earlier historical periods.
                if set(train['series_id'].astype(str)) & heldout:
                    raise ValueError('Held-out device leaked into cross-device training')
                if set(valid['series_id'].astype(str))!=heldout:
                    raise ValueError('Each held-out device must have validation observations')
                from .model_task_spec import canonical_sha
                extra={'heldout_device_count':len(heldout),
                       'train_device_sha256':canonical_sha(sorted(set(train['series_id'].astype(str)))),
                       'heldout_device_sha256':canonical_sha(sorted(heldout)),
                       'device_disjoint':True,'device_context_policy':'zero_shot_no_labels_or_history_in_fit'}
                protocol='leave_device_out'
            else:
                begin=pd.Timestamp(block[0]);end=pd.Timestamp(block[-1])
                cutoff=begin
                train=historical.loc[(times<cutoff)&(matured<=cutoff)].copy()
                valid=historical.loc[(times>=begin)&(times<=end)].copy()
                protocol='temporal_holdout'
            if train.empty or valid.empty:
                raise ValueError('Temporal classification fold has no usable matured train/validation rows')
            if train['target'].nunique()!=2 or valid['target'].nunique()!=2:
                raise ValueError('Temporal classification fold requires both labels')
            if 'batch_key' in historical and set(train['batch_key'].astype(str))&set(valid['batch_key'].astype(str)):
                raise ValueError('Classification fold has a batch leakage')
            folds.append(fold_manifest(train,valid,fold=number,series='all_devices',cutoff=cutoff,
                    protocol=protocol,extra={'positive_validation':int(valid['target'].sum()),
                    'test_boundary_utc':split_audit['test_start'],**extra}))
            for name in task.model_candidates:
                self._cancel_check(run_id)
                plugin=self.plugins.get(name)
                folder=self.run_dir(run_id)/'classification_folds'/f'fold-{number}'/name
                started=time.monotonic()
                from .model_legacy_isolation import bounded_plugin
                isolation=bounded_plugin(name,train,valid,folder,task=task,kind='classification',
                                         stop_check=lambda: self._cancel_check(run_id))
                probs=np.asarray(isolation['prediction'],dtype=float)
                fit_cost[name].append(round(time.monotonic()-started,5))
                score=confusion_metrics(valid['target'].to_numpy(dtype=int),probs,.5)
                fold_scores[name].append({'fold':number,**score})
                for j,(_,record) in enumerate(valid.iterrows()):
                    validation_predictions.append(prediction_row(model=name,fold=number,
                      series=str(record['series_id']),row_index=int(j),truth=record['target'],
                      prediction=float(probs[j]),timestamp=record['timestamp'],origin=cutoff,
                      label_known_at=record['label_available_at'],
                      source_available_at=record['available_at'],classification=True))
        if len(validation_predictions)>240000:raise ValueError('Classification prediction artifact exceeds limit')
        candidates=[]
        for name in task.model_candidates:
            self._cancel_check(run_id)
            plugin=self.plugins.get(name)
            folder=self.run_dir(run_id)/'classification_models'/name
            from .model_legacy_isolation import bounded_plugin
            isolation=bounded_plugin(name,partitions['train'],partitions['validation'],folder,
                                      task=task,kind='classification',test=partitions['test'],
                                      stop_check=lambda: self._cancel_check(run_id))
            fitted={'isolated_model_folder':str(folder)}
            pvalid=np.asarray(isolation['prediction'],dtype=float)
            selected=choose_threshold(partitions['validation']['target'].to_numpy(dtype=int),pvalid,
                                      task.classification_min_recall)
            candidates.append((selected['false_positive_rate'],-selected['precision'] if selected['precision'] is not None else 0.,name,fitted,selected,isolation['test_prediction']))
        candidates.sort(key=lambda item:(item[0],item[1],item[2]))
        _,_,chosen,model,validation,ptest=candidates[0]
        plugin=self.plugins.get(chosen)
        ptest=np.asarray(ptest,dtype=float)
        actual=partitions['test']['target'].to_numpy(dtype=int)
        metrics=confusion_metrics(actual,ptest,validation['selected_threshold'])
        predictions=[{'series_id':str(series),'timestamp':str(time),'actual':int(y),
                      'probability':round(float(p),8),'predicted':int(p>=validation['selected_threshold'])}
                     for series,time,y,p in zip(partitions['test']['series_id'],
                           partitions['test']['timestamp'],actual,ptest)]
        import hashlib
        artifact_path=self.run_dir(run_id)/'classification_models'/chosen/'model.json'
        return {'task_kind':'classification','model_id':chosen,
                'plugin_version':plugin.capabilities().version,
                'model_sha256':sha256_file(artifact_path),'threshold_source':'validation_only',
                'validation':validation,'test':metrics,'required_recall':spec.min_recall,
                'test_recall_floor_met':metrics['recall'] is not None and metrics['recall']>=spec.min_recall,
                'split_audit':split_audit,'predictions':predictions,
                'folds':folds,'fold_scores':fold_scores,
                'fit_seconds_per_fold':fit_cost,'validation_predictions':validation_predictions,
                'comparison_protocol':{'schema_version':'model-fold-evidence-v2',
                  'same_validation_rows_and_fold_indices':True,'same_source':True,
                  'equal_realized_compute':False,
                  'split_kind':'leave_device_out' if device_ood else 'temporal_holdout',
                  'protocol_sha256':canonical_sha({'folds':folds,
                      'models':task.model_candidates,'budget_seconds':task.training_budget_seconds})}}

    def _execute_local(self, run_id: str) -> bool:
        row = self.store.run(run_id)
        if row is None or row.status != RunStatus.RUNNING or row.worker_id != self.worker_id:
            raise ValueError("Run not claimed by this worker")
        stop = threading.Event()
        heartbeat = threading.Thread(target=self._heartbeat, args=(run_id, stop), daemon=True)
        heartbeat.start()
        try:
            task = TaskSpec.model_validate_json(row.task_json)
            manifest, df = get_dataset(self.store, self.settings, row.dataset_id)
            if task.prediction_origin is not None:
                cutoff = pd.Timestamp(task.prediction_origin)
                if cutoff.tzinfo is None:
                    raise ValueError("prediction_origin must include timezone")
                df = df.loc[df["available_at"] <= cutoff].copy()
                if df.empty:
                    raise ValueError("No target values were available at requested prediction_origin")
            self._stage(run_id, "validate", 5, lambda: self._validate(task, df, manifest))
            if task.task_kind=='classification':
                classified=self._stage(run_id,'classification',35,lambda:self._classify(run_id,task,df))
                def _class_quality():
                    return {'run_id':run_id,'dataset_id':manifest.dataset_id,
                            'task_kind':'classification','model_id':classified['model_id'],
                            'status':'passed' if classified['test_recall_floor_met'] else 'review_required',
                            'metrics':classified['test'],'validation_metrics':classified['validation'],
                            'evidence_files':['stages/classification_result.json'],
                            'protocol_id':'chronological_matured_labels_v1',
                            'fold_validation_protocol':('leave_device_out' if
                                 task.evaluation.protocol_id=='device_forward_holdout_v1' else 'temporal_holdout'),
                            'independent_test_scope':'chronological_mixed_devices_not_independent_device_ood',
                            'threshold_source':'validation_only',
                            'target_recall':classified['required_recall'],
                            'source_contract_sha256':sha256_bytes(json.dumps(manifest.modeling_contract,sort_keys=True,ensure_ascii=False).encode())}
                report=self._stage(run_id,'quality',92,_class_quality)
                self._cancel_check(run_id)
                self._update_owned(run_id,status=RunStatus.SUCCEEDED,progress=100,stage='completed',
                                   finished_at=utcnow(),lease_until=None)
                self._event(run_id,'completed','Classification completed',quality=report['status'])
                return True
            backtest = self._stage(run_id, "backtest", 20, lambda: self._backtest(run_id, task, df, unit=manifest.unit))
            forecast = self._stage(run_id, "forecast", 75, lambda: self._test_and_future(run_id, task, df, backtest["selected"], unit=manifest.unit))
            def _quality():
                model_labels = ",".join(sorted(set(backtest["selected"].values())))
                result = make_quality(run_id=run_id, dataset_id=manifest.dataset_id,
                                      protocol_id=task.evaluation.protocol_id, model_id=model_labels,
                                      validation=backtest["validation_selected"], test=forecast["test"],
                                      evidence_files=["stages/backtest_result.json", "stages/forecast_result.json"])
                return result.model_dump(mode="json")
            report = self._stage(run_id, "quality", 92, _quality)
            self._cancel_check(run_id)
            self._update_owned(run_id, status=RunStatus.SUCCEEDED, progress=100, stage="completed",
                               finished_at=utcnow(), lease_until=None)
            self._event(run_id, "completed", "Training, independent test, and report completed", quality=report["status"])
            return True
        except RunCancelled as exc:
            self._update_owned(run_id, status=RunStatus.CANCELLED, stage="cancelled", finished_at=utcnow(), lease_until=None,
                               error_code='RUN_CANCELLED',error_message=str(exc))
            self._event(run_id, "cancelled", str(exc))
            return False
        except TemporalLeaseLost:
            # A reclaimed worker must never overwrite a successor's terminal state.
            logger.warning('Temporal run lease lost: %s',run_id)
            return False
        except TemporalBudgetExpired as exc:
            self._update_owned(run_id,status=RunStatus.FAILED,stage='budget_exhausted',
                               finished_at=utcnow(),lease_until=None,
                               error_code='EXPERIMENT_BUDGET_EXHAUSTED',error_message=str(exc))
            self._event(run_id,'budget_exhausted',str(exc))
            return False
        except Exception as exc:
            # Errors are persisted and do not cause infinite retries. Manual retry is explicit.
            logger.exception("Run failed: %s", run_id)
            self._update_owned(run_id, status=RunStatus.FAILED, stage="failed", finished_at=utcnow(),
                               lease_until=None, error_code=type(exc).__name__, error_message=str(exc)[:3000])
            self._event(run_id, "failed", str(exc), traceback=traceback.format_exc(limit=6))
            return False
        finally:
            stop.set()
            heartbeat.join(timeout=1)

    def execute(self,run_id: str) -> bool:
        """Externally supervise the complete legacy run, not only plugin fits.

        A child stuck in reading, transformation or report writing is terminated
        at the *same frozen* deadline. Final state writes are attempt-fenced.
        """
        from timepilot.model_fit_isolation import _terminate
        from sqlalchemy import update
        row=self.store.run(run_id)
        if (row is None or row.status!=RunStatus.RUNNING or
            row.worker_id!=self.worker_id):
            raise ValueError('Run not claimed by this worker')
        attempt=int(row.attempts)
        self._active_attempts[run_id]=attempt
        try:
            self.budgets.assert_active(run_id)
        except TemporalBudgetExpired:
            with self.store.sessions.begin() as session:
                session.execute(update(RunRow).where(RunRow.id==run_id,
                    RunRow.worker_id==self.worker_id,RunRow.attempts==attempt,
                    RunRow.status==RunStatus.RUNNING)
                    .values(status=RunStatus.FAILED,stage='budget_exhausted',
                            finished_at=utcnow(),lease_until=None,
                            error_code='EXPERIMENT_BUDGET_EXHAUSTED',
                            error_message='Frozen temporal deadline expired before child startup'))
            self.budgets.interrupt(run_id,attempt,'EXPERIMENT_BUDGET_EXHAUSTED')
            return False
        env=os.environ.copy()
        env['TIMEPILOT_DB_URL']=str(self.store.engine.url)
        env['TIMEPILOT_LEASE_SECONDS']=str(self.settings.lease_seconds)
        env['PYTHONPATH']=str(Path(__file__).resolve().parent.parent)+os.pathsep+env.get('PYTHONPATH','')
        for key in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS',
                    'NUMEXPR_NUM_THREADS','VECLIB_MAXIMUM_THREADS'):
            env[key]='1'
        command=[sys.executable,'-m','timepilot.temporal_worker_child',
                 '--home',str(self.settings.home),'--run',run_id,
                 '--worker',self.worker_id,'--attempt',str(attempt)]
        from .temporal_worker_log import BoundedWorkerLog
        try:
            proc=subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,env=env,close_fds=True,
                    **({'start_new_session':True} if os.name=='posix' else
                       {'creationflags':subprocess.CREATE_NEW_PROCESS_GROUP}))
        except OSError as exc:
            with self.store.sessions.begin() as session:
                session.execute(update(RunRow).where(RunRow.id==run_id,
                    RunRow.worker_id==self.worker_id,RunRow.attempts==attempt,
                    RunRow.status==RunStatus.RUNNING)
                    .values(status=RunStatus.FAILED,stage='failed',finished_at=utcnow(),
                            lease_until=None,error_code='TEMPORAL_CHILD_START_FAILED',
                            error_message=str(exc)[:300]))
            self.budgets.interrupt(run_id,attempt,'TEMPORAL_CHILD_START_FAILED')
            return False
        capture=BoundedWorkerLog(proc.stdout)
        failure=None
        try:
            while proc.poll() is None:
                current=self.store.run(run_id)
                if current is None or current.worker_id!=self.worker_id or current.attempts!=attempt:
                    failure='TEMPORAL_LEASE_LOST';break
                if current.cancel_requested:
                    failure='RUN_CANCELLED';break
                if current.status!=RunStatus.RUNNING:
                    # Terminal state may already have been committed by child.
                    break
                if not _live_lease(current.lease_until):
                    failure='TEMPORAL_LEASE_LOST';break
                deadline=self.budgets.deadline(run_id)
                if deadline is not None and time.time()>=deadline:
                    failure='EXPERIMENT_BUDGET_EXHAUSTED';break
                time.sleep(.1)
            if failure is not None:
                _terminate(proc)
            else:
                try:proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    _terminate(proc)
                    failure='TEMPORAL_CHILD_EXIT_DELAY'
            current=self.store.run(run_id)
            if (current and current.status==RunStatus.RUNNING and current.worker_id==self.worker_id
                    and current.attempts==attempt):
                error=failure or 'TEMPORAL_CHILD_CRASHED'
                state=RunStatus.CANCELLED if error=='RUN_CANCELLED' else RunStatus.FAILED
                with self.store.sessions.begin() as session:
                    result=session.execute(update(RunRow).where(RunRow.id==run_id,
                        RunRow.worker_id==self.worker_id,RunRow.attempts==attempt,
                        RunRow.status==RunStatus.RUNNING)
                        .values(status=state,stage='cancelled' if state==RunStatus.CANCELLED else 'failed',
                                finished_at=utcnow(),lease_until=None,error_code=error,
                                error_message=f'External temporal watchdog stopped attempt {attempt}: {error}'))
                if result.rowcount:
                    self.budgets.interrupt(run_id,attempt,error)
                    self._event(run_id,'external_watchdog',error,attempt=attempt)
            final=self.store.run(run_id)
            return bool(final and final.status==RunStatus.SUCCEEDED)
        finally:
            if proc.poll() is None:_terminate(proc)
            log=capture.save(self.run_dir(run_id),attempt)
            if failure or (proc.returncode is not None and proc.returncode!=0):
                # No unbounded exception text or potentially sensitive raw
                # worker output is written to the primary run event ledger.
                self._event(run_id,'temporal_worker_log_saved','Bounded child output stored',
                    attempt=attempt,sha256=log['log_sha256'],truncated=log['truncated'])

    def worker_logs(self,run_id,limit=4):
        from .temporal_worker_log import read_log
        row=self.store.run(run_id)
        if row is None:raise KeyError('Temporal run does not exist')
        return [log for attempt in range(max(1,int(row.attempts)-max(1,min(limit,8))+1),
                                         int(row.attempts)+1)
                if (log:=read_log(self.run_dir(run_id),attempt)) is not None]

    def work_once(self, run_id: str | None = None) -> str | None:
        claimed = self.claim(run_id)
        if claimed:
            self.execute(claimed)
        return claimed

    def retry(self, run_id: str) -> str:
        row = self.store.run(run_id)
        if row is None:
            raise KeyError(run_id)
        if row.status != RunStatus.FAILED:
            raise ValueError("Only failed runs can be retried")
        self.store.update_run(run_id, status=RunStatus.QUEUED, stage="queued", progress=0,
                              worker_id=None, lease_until=None, cancel_requested=False,
                              error_code=None, error_message=None, finished_at=None)
        self._event(run_id, "requeued", "Retry requested. Verified checkpoints will be reused.")
        return run_id

    def report(self, run_id: str) -> dict:
        path = self.run_dir(run_id) / "stages" / "quality_result.json"
        if not path.exists():
            raise FileNotFoundError("Quality report not ready")
        return load_json(path)

    def predict_saved(self, run_id: str, series_id: str, horizon: int | None = None) -> list[dict]:
        """Reload the persisted model and historical context for a deterministic local inference check."""
        row = self.store.run(run_id)
        if row is None or row.status != RunStatus.SUCCEEDED:
            raise ValueError("Prediction requires a successfully completed run")
        task = TaskSpec.model_validate_json(row.task_json)
        dataset_row = self.store.dataset_by_name_or_id(row.dataset_id)
        from timepilot.contracts import DatasetManifest
        unit = DatasetManifest.model_validate_json(dataset_row.manifest_json).unit
        h = horizon or task.forecast_horizon
        if h < 1 or h > 24:
            raise ValueError("horizon must be between 1 and 24")
        root = self.run_dir(run_id).resolve()
        model = next((m for m in self.forecast(run_id)["models"] if m["series_id"] == series_id), None)
        if model is None:
            raise KeyError(f"No saved model for series {series_id}")
        def _inside(relative: str):
            path = (root / relative).resolve()
            if not path.is_relative_to(root):
                raise ValueError("Saved model path escapes run storage")
            return path
        history_path = _inside(model["history"])
        if sha256_file(history_path) != model["history_sha256"]:
            raise ValueError("Model history checksum mismatch")
        artifact_path = _inside(model["artifact"])
        plugin = self.plugins.get(model["plugin_id"])
        if plugin.capabilities().version != model["plugin_version"]:
            raise ValueError("Model plugin version does not match saved artifact")
        artifact = plugin.load(artifact_path.parent)
        history = pd.read_csv(history_path)
        history["timestamp"] = pd.to_datetime(history["timestamp"], utc=True)
        history["available_at"] = pd.to_datetime(history["available_at"], utc=True)
        if hasattr(plugin, "predict_from_history"):
            values = plugin.predict_from_history(artifact, history, h, task.frequency)
        else:
            values = plugin.predict(artifact, h, task.frequency)
        physical=task.modeling_contract.get('mechanism') or {}
        if physical.get('enforcement')=='block':
            if physical.get('target_min') is not None and any(x<physical['target_min'] for x in values):
                raise ValueError('保存模型推断违反物理下界')
            if physical.get('target_max') is not None and any(x>physical['target_max'] for x in values):
                raise ValueError('保存模型推断违反物理上界')
            if physical.get('max_step_change') is not None:
                steps = [float(history['target'].iloc[-1]), *map(float, values)]
                if any(abs(b-a)>physical['max_step_change'] for a,b in zip(steps,steps[1:])):
                    raise ValueError('保存模型推断违反最大单步变化率约束')
        dates = pd.date_range(start=history["timestamp"].iloc[-1], periods=h + 1, freq=task.frequency, tz="UTC")[1:]
        origin = history["available_at"].max()
        return [ForecastRecord(run_id=run_id, model_id=model["plugin_id"], series_id=series_id,
                               prediction_origin=origin.to_pydatetime(), target_timestamp=ts.to_pydatetime(),
                               horizon_step=i + 1, mean=float(value), actual=None, unit=unit,
                               split="future").model_dump(mode="json")
                for i, (ts, value) in enumerate(zip(dates, values))]

    def forecast(self, run_id: str) -> dict:
        path = self.run_dir(run_id) / "stages" / "forecast_result.json"
        if not path.exists():
            raise FileNotFoundError("Forecast not ready")
        return load_json(path)
