"""Stable model/plugin boundary; no API or database dependency."""
from __future__ import annotations

import importlib.util
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd


class PluginUnavailableError(RuntimeError):
    pass


@dataclass(frozen=True)
class Capabilities:
    plugin_id: str
    version: str
    description: str
    supports_frequency: tuple[str, ...] = ("MS", "D", "h")
    supports_quantiles: bool = False
    requires_extra: str | None = None
    supports_native_checkpoint: bool = False
    supports_observed_features: bool = False
    task_kinds: tuple[str,...] = ("continuous_forecast",)


class ModelPlugin(ABC):
    @abstractmethod
    def capabilities(self) -> Capabilities: ...

    @abstractmethod
    def fit(self, history: pd.DataFrame, horizon: int, frequency: str, output_dir: Path, budget_seconds: int,
            *, seasonal_period: int | None = None) -> dict: ...

    @abstractmethod
    def predict(self, artifact: dict, horizon: int, frequency: str) -> np.ndarray: ...

    def load(self, model_dir: Path) -> dict:
        return json.loads((model_dir / "model.json").read_text(encoding="utf-8"))

    def available(self) -> bool:
        return True


class SeasonalNaive(ModelPlugin):
    def capabilities(self):
        return Capabilities("seasonal_naive", "1.0", "Repeats values from the previous season")

    def fit(self, history, horizon, frequency, output_dir, budget_seconds, *, seasonal_period=None):
        season = seasonal_period or (12 if frequency == "MS" else 7 if frequency == "D" else 24)
        values = history["target"].to_numpy(dtype=float).tolist()
        if len(values) < season:
            raise ValueError(f"seasonal_naive needs at least {season} samples")
        artifact = {"values": values[-season:], "season": season}
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "model.json").write_text(json.dumps(artifact), encoding="utf-8")
        return artifact

    def predict(self, artifact, horizon, frequency):
        values = artifact["values"]
        return np.array([values[i % len(values)] for i in range(horizon)], dtype=float)


class MovingAverage(ModelPlugin):
    def capabilities(self):
        return Capabilities("moving_average", "1.0", "Mean of the most recent observations")

    def fit(self, history, horizon, frequency, output_dir, budget_seconds, *, seasonal_period=None):
        values = history["target"].to_numpy(dtype=float)
        if len(values) < 2:
            raise ValueError("moving_average needs two or more observations")
        count = min(3, len(values))
        artifact = {"mean": float(np.mean(values[-count:])), "window": count}
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "model.json").write_text(json.dumps(artifact), encoding="utf-8")
        return artifact

    def predict(self, artifact, horizon, frequency):
        return np.repeat(float(artifact["mean"]), horizon)


class LastValue(ModelPlugin):
    def capabilities(self):
        return Capabilities("last_value", "1.0", "Repeats the last available observation")

    def fit(self, history, horizon, frequency, output_dir, budget_seconds, *, seasonal_period=None):
        artifact = {"last": float(history["target"].iloc[-1])}
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "model.json").write_text(json.dumps(artifact), encoding="utf-8")
        return artifact

    def predict(self, artifact, horizon, frequency):
        return np.repeat(artifact["last"], horizon)


class LagRidge(ModelPlugin):
    """Non-leaking autoregressive ridge model, trained only on prior targets."""
    FEATURE_NAMES=('lag_1','lag_2','lag_3','lag_6','lag_12',
                   'rolling_3','rolling_6','sin_season','cos_season','trend')

    def capabilities(self):
        return Capabilities('lag_ridge','1.0',
            'Autoregressive ridge regression with causal lag, rolling and calendar features',
            supports_frequency=('MS',))

    @staticmethod
    def _features(values: list[float], index: int) -> np.ndarray:
        if len(values)<12:
            raise ValueError('Lag Ridge requires at least 12 preceding observations')
        return np.array([values[-1],values[-2],values[-3],values[-6],values[-12],
                         float(np.mean(values[-3:])),float(np.mean(values[-6:])),
                         float(np.sin(2*np.pi*(index % 12)/12)),
                         float(np.cos(2*np.pi*(index % 12)/12)),float(index)],dtype=float)

    def fit(self, history, horizon, frequency, output_dir, budget_seconds, *, seasonal_period=None):
        if frequency!='MS':
            raise ValueError('Lag Ridge requires month-start frequency MS')
        y=history['target'].to_numpy(dtype=float)
        if len(y)<20 or not np.isfinite(y).all():
            raise ValueError('Lag Ridge requires >=20 continuous finite observations')
        features=np.stack([self._features(y[:t].tolist(),t) for t in range(12,len(y))])
        target=y[12:]
        mu=features.mean(axis=0)
        std=features.std(axis=0)
        std[std<1e-12]=1.0
        scaled=(features-mu)/std
        ymean=float(target.mean())
        yscale=float(target.std()) or 1.0
        design=np.column_stack([np.ones(len(scaled)),scaled])
        lam=np.eye(design.shape[1])*3.0
        lam[0,0]=0.0
        weight=np.linalg.solve(design.T@design+lam,design.T@((target-ymean)/yscale))
        artifact={'coefficients':weight.tolist(),'xmean':mu.tolist(),'xstd':std.tolist(),
                  'ymean':ymean,'yscale':yscale,'recent_values':y[-12:].tolist(),
                  'n_history':len(y),'features':list(self.FEATURE_NAMES),
                  'feature_policy':'history_only_lags_no_future_target',
                  'algorithm':'ridge_closed_form_lambda_3'}
        output_dir.mkdir(parents=True,exist_ok=True)
        (output_dir/'model.json').write_text(json.dumps(artifact,ensure_ascii=False),encoding='utf-8')
        return artifact

    def predict(self, artifact, horizon, frequency):
        if frequency!='MS' or horizon<1:
            raise ValueError('Lag Ridge requires MS and a positive horizon')
        sequence=list(artifact['recent_values'])
        idx=int(artifact['n_history'])
        weight=np.asarray(artifact['coefficients'],dtype=float)
        mu=np.asarray(artifact['xmean'],dtype=float)
        std=np.asarray(artifact['xstd'],dtype=float)
        predictions=[]
        for offset in range(horizon):
            row=self._features(sequence,idx+offset)
            y=float((weight[0]+((row-mu)/std)@weight[1:])*artifact['yscale']+artifact['ymean'])
            if not np.isfinite(y):raise ValueError('Lag Ridge returned a non-finite number')
            predictions.append(y)
            sequence.append(y)
        return np.asarray(predictions,dtype=float)


class AutoGluonPlugin(ModelPlugin):
    """Optional dependency, isolated adapter. Requires real Python 3.12 / autogluon."""
    def capabilities(self):
        return Capabilities("autogluon_timeseries", "1.6.1-adapter-1", "AutoGluon TimeSeriesPredictor", requires_extra="automl")

    def available(self):
        try:
            return importlib.util.find_spec("autogluon.timeseries") is not None
        except ModuleNotFoundError:
            return False

    def fit(self, history, horizon, frequency, output_dir, budget_seconds, *, seasonal_period=None):
        if not self.available():
            raise PluginUnavailableError("AutoGluon missing: install pip extra timepilot[automl]")
        from autogluon.timeseries import TimeSeriesDataFrame, TimeSeriesPredictor
        data = history[["timestamp", "target"]].copy()
        data["timestamp"] = pd.to_datetime(data["timestamp"], utc=True).dt.tz_localize(None)
        data["item_id"] = "single"
        ts = TimeSeriesDataFrame.from_data_frame(data, id_column="item_id", timestamp_column="timestamp")
        model_path = str((output_dir / "autogluon_model").resolve())
        predictor = TimeSeriesPredictor(prediction_length=horizon, target="target", path=model_path, freq=frequency,
                                        eval_metric="WAPE")
        # Explicit CPU/statistical models prevent a deprecated preset from silently
        # selecting downloadable pretrained weights in AutoGluon 1.6.1.
        predictor.fit(train_data=ts, time_limit=max(10, budget_seconds),
                      hyperparameters={"Naive": {}, "SeasonalNaive": {}, "ETS": {}, "Theta": {}})
        artifact = {"path": model_path, "plugin_version": "1.6.1-adapter-1"}
        (output_dir / "model.json").write_text(json.dumps(artifact), encoding="utf-8")
        return artifact

    def predict(self, artifact, horizon, frequency):
        from autogluon.timeseries import TimeSeriesPredictor
        predictor = TimeSeriesPredictor.load(artifact["path"])
        # AutoGluon models require the recent historical context; use predict_from_history instead.
        raise NotImplementedError("AutoGluon requires historical context; call predict_from_history")

    def predict_from_history(self, artifact, history, horizon, frequency):
        from autogluon.timeseries import TimeSeriesDataFrame, TimeSeriesPredictor
        d = history[["timestamp", "target"]].copy()
        d["timestamp"] = pd.to_datetime(d["timestamp"], utc=True).dt.tz_localize(None)
        d["item_id"] = "single"
        ts = TimeSeriesDataFrame.from_data_frame(d, id_column="item_id", timestamp_column="timestamp")
        predictor = TimeSeriesPredictor.load(artifact["path"])
        forecast = predictor.predict(ts)
        return forecast["mean"].to_numpy(dtype=float)[:horizon]


class PluginRegistry:
    def __init__(self):
        self._plugins: dict[str, ModelPlugin] = {}
        from timepilot.industrial_plugins import HistoricalExogenousRidge, NumpyLogisticClassifier
        for plugin in [SeasonalNaive(), MovingAverage(), LastValue(), LagRidge(), AutoGluonPlugin(),
                       HistoricalExogenousRidge(), NumpyLogisticClassifier()]:
            self.register(plugin)

    def register(self, plugin: ModelPlugin):
        key = plugin.capabilities().plugin_id
        if key in self._plugins:
            raise ValueError(f"Duplicate plugin: {key}")
        self._plugins[key] = plugin

    def get(self, key: str) -> ModelPlugin:
        if key not in self._plugins:
            raise KeyError(f"Model plugin '{key}' is not registered.")
        plugin = self._plugins[key]
        if not plugin.available():
            raise PluginUnavailableError(f"'{key}' unavailable. Install optional dependency: {plugin.capabilities().requires_extra}")
        return plugin

    def catalog(self):
        return [{**vars(p.capabilities()), "available": p.available()} for p in self._plugins.values()]
