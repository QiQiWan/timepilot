"""Autonomous *bounded* forecasting experiment over a verified TimePilot DataAsset.

No external weights, arbitrary code, unreviewed imputation or automatic deployment.
All models see identical rolling-origin folds. The target is never made available
as a training feature at or after the forecast origin; publication time is real.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import tempfile
import time
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, model_validator

from timepilot.data_assets import DataAssetCatalog
from timepilot.plugins import PluginRegistry
from timepilot.smart_workbench import ViewSelection

_ALLOWED = frozenset({'last_value','moving_average','seasonal_naive','lag_ridge'})


def _stable_json(obj: dict) -> str:
    return json.dumps(obj,sort_keys=True,ensure_ascii=False,separators=(',', ':'),default=str)


def _digest(obj: dict) -> str:
    return hashlib.sha256(_stable_json(obj).encode()).hexdigest()


class ExperimentSpec(BaseModel):
    model_config=ConfigDict(extra='forbid')
    asset_id: str=Field(min_length=5,max_length=120)
    version_id: str=Field(min_length=16,max_length=64)
    selection: ViewSelection=Field(default_factory=ViewSelection)
    horizon: int=Field(default=1,ge=1,le=6)
    folds: int=Field(default=3,ge=2,le=6)
    min_history: int=Field(default=24,ge=12,le=120)
    model_ids: list[str]=Field(default_factory=lambda:['last_value','moving_average','seasonal_naive','lag_ridge'],min_length=1,max_length=4)
    max_series: int=Field(default=8,ge=1,le=30)
    max_rows: int=Field(default=5000,ge=30,le=15000)
    max_seconds: int=Field(default=180,ge=10,le=1800)
    mechanism_evidence_id: str | None=Field(default=None,pattern=r'^[0-9a-f]{64}$')

    @model_validator(mode='after')
    def check_models(self):
        if set(self.model_ids)-_ALLOWED or len(self.model_ids)!=len(set(self.model_ids)):
            raise ValueError('Model comparison only supports unique, locally verified statistical candidates')
        if self.min_history < 20 and 'lag_ridge' in self.model_ids:
            raise ValueError('lag_ridge needs at least 20 historical observations')
        if self.min_history < 12 and 'seasonal_naive' in self.model_ids:
            raise ValueError('seasonal_naive needs at least 12 observations')
        return self


class AutonomousExperiment:
    def __init__(self, home: Path, assets: DataAssetCatalog, plugins: PluginRegistry | None = None, mechanisms=None):
        self.root=Path(home)/'research_experiments'
        self.root.mkdir(parents=True,exist_ok=True)
        self.assets=assets
        self.plugins=plugins or PluginRegistry()
        self.mechanisms=mechanisms

    @staticmethod
    def _publication(raw: str) -> datetime:
        if not raw:
            raise ValueError('缺失真实 available_at；不得假定发布时间来比较预测模型')
        try:
            dt=datetime.fromisoformat(str(raw).replace('Z','+00:00'))
        except (TypeError,ValueError) as exc:
            raise ValueError('available_at 无法解析，不能用于防泄漏回测') from exc
        if dt.tzinfo is None:
            raise ValueError('available_at 缺少时区，不能用于防泄漏回测')
        return dt

    def _cohort(self, spec: ExperimentSpec) -> tuple[dict,list[tuple[str,pd.DataFrame]],dict]:
        import_id, _, version=self.assets._resolve(spec.asset_id,spec.version_id)
        path=self.assets.imports.version_file(import_id,spec.version_id)
        if not path.is_file():
            raise ValueError('不可变标准数据文件不存在')
        analysis=self.assets.analyze(spec.asset_id,spec.version_id,spec.selection)
        summary=analysis['summary']
        if summary.get('distinct_scopes')!=1 or summary.get('distinct_sources')!=1:
            raise ValueError('必须先选定唯一统计口径与数据来源，再执行自主建模')
        if not summary.get('rows'):
            raise ValueError('选定范围没有观测数据')
        if summary['rows']>spec.max_rows:
            raise ValueError(f'当前选择 {summary["rows"]} 行超过上限 {spec.max_rows}；先在 BI 缩小范围')
        # The read cache verifies immutable source SHA when loaded.
        where, params=self.assets.workbench._where(spec.selection)
        con=self.assets.workbench._connect(import_id,spec.version_id)
        try:
            rows=con.execute('''SELECT series_id,timestamp,numeric_target AS target,
                            available_at,quality_flags,statistical_scope,source_name
                            FROM observations'''+where+' ORDER BY series_id,timestamp,id',params).fetchall()
        finally:
            con.close()
        grouped=defaultdict(list)
        for row in rows:
            obj=dict(row)
            if not obj['series_id']:
                raise ValueError('发现缺失的 series_id，无法区分实体')
            if obj['target'] is None or not math.isfinite(float(obj['target'])):
                raise ValueError('训练范围包含缺失或非有限目标值；禁止静默插值')
            flags=obj.get('quality_flags') or ''
            if any(token in flags for token in ('conflict_review','duplicate_identical',
                                                'scope_unverified','publication_assumed')):
                raise ValueError('数据包含重复/冲突/未核实口径或假定发布时间，必须先审核')
            obj['available_at_utc']=self._publication(obj['available_at'])
            grouped[obj['series_id']].append(obj)
        if len(grouped)>spec.max_series:
            raise ValueError(f'识别 {len(grouped)} 条序列，超出单次上限 {spec.max_series}；请选择具体系列')
        series=[]
        for sid, group in grouped.items():
            timestamp=[str(x['timestamp'])[:10] for x in group]
            try:
                periods=pd.PeriodIndex(pd.to_datetime(timestamp),freq='M')
            except (TypeError,ValueError) as exc:
                raise ValueError(f'序列 {sid} 日期不符合按月观测要求') from exc
            if len(set(periods))!=len(periods):
                raise ValueError(f'序列 {sid} 存在重复月份；拒绝无提示地合并实体观测')
            expected=pd.period_range(periods[0],periods[-1],freq='M')
            if len(expected)!=len(periods) or not all(expected==periods):
                raise ValueError(f'序列 {sid} 不连续；需要显式处理缺失月份')
            if len(group)<spec.min_history+spec.folds*spec.horizon:
                raise ValueError(f'序列 {sid} 历史长度不足指定滚动窗口')
            df=pd.DataFrame(group)
            df['timestamp']=pd.to_datetime(df['timestamp'])
            series.append((sid,df))
        return version,series,{'rows':len(rows),'series':len(series),
                               'scope':rows[0]['statistical_scope'],'source':rows[0]['source_name']}

    def _mechanism_link(self, cfg: ExperimentSpec) -> dict:
        """Mechanism identification is evidence, never a stealth forecast feature.

        The bound asset must be exactly the one selected for backtesting.  A
        simulated or unreviewed measured series cannot authorize physical
        corrections, extra model features, or automatic deployment.
        """
        if not cfg.mechanism_evidence_id:
            return {'status':'no_mechanism_evidence','can_influence_forecast':False}
        if self.mechanisms is None:
            raise ValueError('Mechanism assessment service has not been configured')
        evidence=self.mechanisms.load(cfg.mechanism_evidence_id)
        if evidence.source_asset_id != cfg.asset_id or evidence.source_version_id != cfg.version_id:
            raise ValueError('机理证据与当前资产版本不一致，拒绝跨设备或跨来源关联')
        report=self.mechanisms.assess(cfg.mechanism_evidence_id)
        return {'status':'linked_research_candidate','evidence_id':cfg.mechanism_evidence_id,
                'source_kind':evidence.kind,'candidate_status':report['candidate_status'],
                'verified_on_real_equipment':report['verified_on_real_equipment'],
                'holdout':report['holdout'],'identifiability':report['identifiability'],
                'can_influence_forecast':False,
                'note':'Experimental mechanism hypothesis is archived, not used as a forecasting covariate without validated applicability'}

    @staticmethod
    def _fold_diagnostics(predictions: list[dict]) -> dict:
        """Report all fold residuals without promoting untested significance."""
        buckets=defaultdict(list)
        for point in predictions:
            buckets[(point['series_id'],point['fold'])].append(point)
        folds=[]
        for (sid,fold),points in sorted(buckets.items()):
            y=np.array([x['actual'] for x in points],dtype=float)
            yhat=np.array([x['predicted'] for x in points],dtype=float)
            denom=float(np.abs(y).sum())
            folds.append({'series_id':sid,'fold':fold,'n':len(points),
                          'MAE':float(np.mean(np.abs(yhat-y))),
                          'WAPE':float(np.sum(np.abs(yhat-y))/denom) if denom>0 else None})
        return {'folds':folds,'n_folds':len(folds),
                'note':'paired rolling-origin scores, not a statistical significance claim'}

    def plan(self, spec: ExperimentSpec | dict) -> dict:
        cfg=ExperimentSpec.model_validate(spec)
        version, series, cohort=self._cohort(cfg)
        mechanism=self._mechanism_link(cfg)
        return {'eligible':True,'asset_id':cfg.asset_id,'version_id':cfg.version_id,
                'source_sha256':version['sha256'],'cohort':cohort,
                'models':cfg.model_ids,'expected_fit_calls':len(series)*cfg.folds*len(cfg.model_ids),
                'backtest':{'horizon':cfg.horizon,'folds':cfg.folds,'min_history':cfg.min_history,
                            'origin_policy':'after_last_observed_publication_before_heldout_publication'},
                'requires_approval':True,'model_downloads':False,
                'mechanism_status':mechanism,
                'training_execution':'will_run_only_after_explicit_approval'}

    @staticmethod
    def _verified_report(report_path: Path, expected_digest: str) -> dict:
        contents=report_path.read_bytes()
        report_hash=report_path.with_name('report.sha256')
        if report_hash.is_file():
            if hashlib.sha256(contents).hexdigest()!=report_hash.read_text(encoding='ascii').strip():
                raise ValueError('Experiment report SHA256 mismatch')
        else:
            # Old v0.11.0 reports remain readable, but they are not claimed
            # to have the new independent report-integrity guarantee.
            pass
        report=json.loads(contents)
        if report.get('input_digest') != expected_digest:
            raise ValueError('Experiment receipt identity mismatch')
        predictions_path=report_path.with_name('predictions.json')
        if not predictions_path.is_file():
            raise ValueError('预测逐折明细缺失；拒绝重用不完整实验')
        expected=report.get('predictions_sha256')
        if not isinstance(expected,str) or len(expected)!=64:
            raise ValueError('缺少预测结果完整性证明；拒绝重用')
        if hashlib.sha256(predictions_path.read_bytes()).hexdigest()!=expected:
            raise ValueError('预测结果 SHA256 不一致，拒绝加载或重用')
        return report

    def run(self, spec: ExperimentSpec | dict, *, cancel: Callable[[],bool] | None = None) -> dict:
        cfg=ExperimentSpec.model_validate(spec)
        version, series, cohort=self._cohort(cfg)
        mechanism=self._mechanism_link(cfg)
        digest=_digest({'source_sha256':version['sha256'],'options':cfg.model_dump(mode='json')})
        target=self.root/digest
        report_path=target/'report.json'
        if report_path.is_file():
            report=self._verified_report(report_path,digest)
            if report.get('source_sha256')!=version['sha256']:
                raise ValueError('既有实验源数据哈希不同，拒绝重用')
            return {'reused':True,**report}
        started=time.monotonic()
        staged=Path(tempfile.mkdtemp(prefix='.experiment-',dir=self.root))
        results=[]
        expected_n=cfg.folds*cfg.horizon*len(series)
        failures={}
        try:
            for model_name in cfg.model_ids:
                plugin=self.plugins.get(model_name)
                if 'MS' not in plugin.capabilities().supports_frequency:
                    raise ValueError(f'{model_name} cannot support monthly data')
                predictions=[]
                try:
                    for sid, df in series:
                        for fold in range(cfg.folds):
                            if cancel and cancel():
                                raise RuntimeError('实验已请求取消，丢弃未发布的中间结果')
                            if time.monotonic()-started>cfg.max_seconds:
                                raise TimeoutError('计算预算已耗尽，停止进一步训练')
                            start=len(df)-cfg.folds*cfg.horizon+fold*cfg.horizon
                            train=df.iloc[:start].copy()
                            target_window=df.iloc[start:start+cfg.horizon].copy()
                            if len(train)<cfg.min_history or len(target_window)!=cfg.horizon:
                                raise ValueError('Rolling-origin split is too small')
                            # As-of time is the last release of data used for fitting.
                            origin=max(train['available_at_utc'])
                            if any(x<=origin for x in target_window['available_at_utc']):
                                raise ValueError('数据未来标签在预测时已发布；时序划分不符合防泄漏约束')
                            if origin >= min(target_window['available_at_utc']):
                                raise ValueError('Future holdout label available at model fit time')
                            folder=staged/'fits'/model_name/hashlib.sha256(sid.encode()).hexdigest()[:16]/str(fold)
                            artifact=plugin.fit(train[['timestamp','target']],cfg.horizon,'MS',folder,
                                                 max(1,int(cfg.max_seconds-(time.monotonic()-started))))
                            guess=np.asarray(plugin.predict(artifact,cfg.horizon,'MS'),dtype=float)
                            if len(guess)!=cfg.horizon or not np.isfinite(guess).all():
                                raise ValueError('Candidate model produced invalid predictions')
                            for j,(_,actual) in enumerate(target_window.iterrows()):
                                predictions.append({'series_id':sid,'fold':fold,
                                   'as_of':origin.isoformat(),'timestamp':str(actual['timestamp'].date()),
                                   'actual':float(actual['target']),'predicted':float(guess[j]),
                                   'published_at':actual['available_at_utc'].isoformat()})
                    if len(predictions)!=expected_n:
                        raise ValueError('Model folds are not comparable: predictions missing')
                    actual=np.array([x['actual'] for x in predictions])
                    guess=np.array([x['predicted'] for x in predictions])
                    residual=guess-actual
                    denom=float(np.abs(actual).sum())
                    wape=float(np.abs(residual).sum()/denom) if denom>0 else None
                    metrics={'MAE':float(np.mean(np.abs(residual))),
                             'RMSE':float(np.sqrt(np.mean(residual**2))),
                             'WAPE':wape,
                             'sMAPE':float(np.mean(2*np.abs(residual)/(np.abs(actual)+np.abs(guess)+1e-9))),
                             'bias':float(np.mean(residual))}
                    results.append({'model':model_name,'metrics':metrics,'n_forecasts':len(predictions),
                                    'fold_diagnostics':self._fold_diagnostics(predictions),
                                    'predictions':predictions,'status':'succeeded'})
                except (ValueError,TimeoutError,RuntimeError) as exc:
                    # No partial/censored model is allowed to win.
                    if isinstance(exc,(TimeoutError,RuntimeError)) and (
                        isinstance(exc,TimeoutError) or '取消' in str(exc)):
                        raise
                    failures[model_name]=str(exc)[:400]
            candidates=[r for r in results if r['metrics']['WAPE'] is not None]
            candidates.sort(key=lambda r:(r['metrics']['WAPE'],r['metrics']['MAE'],r['model']))
            winner=candidates[0]['model'] if candidates else None
            baseline=next((x for x in results if x['model']=='last_value'),None)
            improvement=(1-candidates[0]['metrics']['WAPE']/baseline['metrics']['WAPE']) if (
                candidates and baseline and baseline['metrics']['WAPE'] and
                candidates[0]['metrics']['WAPE'] is not None) else None
            if not candidates:
                raise ValueError('没有可比较的候选模型；请检查已记录的逐模型失败原因：'+str(failures))
            by_name={x['model']:x for x in results}
            baseline_details=by_name.get('last_value')
            paired_improvements=[]
            if baseline_details:
                baseline_by_fold={(x['series_id'],x['fold']):x for x in
                                  baseline_details['fold_diagnostics']['folds']}
                for candidate in candidates:
                    if candidate['model']=='last_value':
                        continue
                    fold_scores=[]
                    for fold in candidate['fold_diagnostics']['folds']:
                        b=baseline_by_fold.get((fold['series_id'],fold['fold']))
                        if b and b['WAPE'] is not None and fold['WAPE'] is not None:
                            fold_scores.append({'series_id':fold['series_id'],'fold':fold['fold'],
                                                'delta_wape_candidate_minus_last_value':fold['WAPE']-b['WAPE']})
                    paired_improvements.append({'model':candidate['model'],'matched_folds':len(fold_scores),
                                                'fold_deltas':fold_scores,
                                                'improved_fold_fraction':sum(x['delta_wape_candidate_minus_last_value']<0 for x in fold_scores)/len(fold_scores) if fold_scores else None})
            report={'experiment_id':digest,'input_digest':digest,'source_sha256':version['sha256'],
                    'asset_id':cfg.asset_id,'version_id':cfg.version_id,'options':cfg.model_dump(mode='json'),
                    'cohort':cohort,'leaderboard':[{'model':x['model'],**x['metrics'],
                       'n_forecasts':x['n_forecasts']} for x in candidates],
                    'failed_candidates':failures,'selected_model':winner,
                    'paired_fold_diagnostics':paired_improvements,
                    'feature_engineering':{'lag_ridge':'lags_1_2_3_6_12__rolling_past_3_6__calendar_sin_cos__history_only',
                                          'evaluation':'all candidates fit on identical chronological folds'},
                    'improvement_vs_last_value':improvement,
                    'evaluation_policy':'identical_rolling_origin_folds_actual_release_as_of_no_future_labels',
                    'mechanism_status':mechanism,
                    'status':'completed_review_required','auto_deployed':False,
                    'auto_submitted_training':False,'result_only':True,
                    'wall_seconds':round(time.monotonic()-started,3)}
            predictions_payload=_stable_json({x['model']:x['predictions'] for x in results})+'\n'
            report['predictions_sha256']=hashlib.sha256(predictions_payload.encode('utf-8')).hexdigest()
            staged.joinpath('predictions.json').write_text(predictions_payload,encoding='utf-8')
            report_contents=_stable_json(report)+'\n'
            staged.joinpath('report.json').write_text(report_contents,encoding='utf-8')
            staged.joinpath('report.sha256').write_text(hashlib.sha256(report_contents.encode()).hexdigest()+'\n',encoding='ascii')
            # Mutually-exclusive immutable publication; a losing concurrent writer
            # cannot overwrite another successful experiment.
            if not target.exists():
                try:
                    os.rename(staged,target)
                except OSError:
                    if not report_path.is_file():
                        raise
            if not report_path.exists():
                raise ValueError('Concurrent experiment publication did not produce a valid report')
            saved=self._verified_report(report_path,digest)
            return {'reused':False,**saved}
        finally:
            if staged.exists():
                shutil.rmtree(staged,ignore_errors=True)

    def next_plan(self, experiment_id: str) -> dict:
        """One bounded, evidence-based improvement step; never executes itself."""
        parent=self.report(experiment_id)
        cfg=ExperimentSpec.model_validate(parent['options'])
        failed=parent.get('failed_candidates',{})
        scores=parent.get('leaderboard',[])
        if not scores:
            return {'status':'blocked','parent_experiment_id':experiment_id,
                    'reason':'No valid baseline comparison; inspect candidate failures',
                    'failed_candidates':failed,'auto_executed':False}
        improvement=parent.get('improvement_vs_last_value')
        if cfg.folds<6:
            revised=cfg.model_copy(update={'folds':cfg.folds+1})
            reason='Increase independent rolling folds to check temporal stability'
        elif 'lag_ridge' not in cfg.model_ids and cfg.min_history>=20:
            revised=cfg.model_copy(update={'model_ids':[ *cfg.model_ids,'lag_ridge'][:4]})
            reason='Compare a history-only feature model against the same baselines'
        else:
            return {'status':'needs_more_evidence','parent_experiment_id':experiment_id,
                    'reason':'Candidate set and bounded rolling folds exhausted; gather further reviewed data',
                    'selected_model':parent.get('selected_model'),
                    'measured_improvement_vs_last_value':improvement,
                    'auto_executed':False,'requires_approval':True}
        try:
            proposed=self.plan(revised)
        except (ValueError,KeyError) as exc:
            return {'status':'needs_more_evidence','parent_experiment_id':experiment_id,
                    'reason':'Next comparison is not yet eligible: '+str(exc)[:250],
                    'auto_executed':False,'requires_approval':True}
        return {'status':'plan_requires_human_confirmation',
                'parent_experiment_id':experiment_id,'reason':reason,
                'previous_selected_model':parent.get('selected_model'),
                'previous_measured_improvement_vs_last_value':improvement,
                'proposed_spec':revised.model_dump(mode='json'),
                'plan':proposed,'plan_sha256':_digest(proposed),
                'requires_approval':True,'auto_executed':False,
                'mechanism_is_not_a_forecasting_covariate':True}

    def experiments(self, limit: int=25) -> list[dict]:
        entries=[]
        for path in self.root.glob('*/report.json'):
            try:
                entry=json.loads(path.read_text(encoding='utf-8'))
                entries.append({'experiment_id':entry['experiment_id'],
                    'asset_id':entry['asset_id'],'source_sha256':entry['source_sha256'],
                    'selected_model':entry['selected_model'], 'status':entry['status'],
                    'wall_seconds':entry['wall_seconds']})
            except (KeyError,ValueError):
                continue
        return entries[:max(1,min(limit,100))]

    def report(self, experiment_id: str) -> dict:
        if len(experiment_id)!=64 or any(ch not in '0123456789abcdef' for ch in experiment_id):
            raise ValueError('Invalid experiment identifier')
        return self._verified_report(self.root/experiment_id/'report.json',experiment_id)
