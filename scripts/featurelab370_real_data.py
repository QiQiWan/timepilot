"""Public physical dataset or explicit factory data adapter and evaluation."""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from timepilot.industrial_dataset_v0370 import (load_nab, load_metropt,
                                                  load_factory_csv, write_normalized)
from timepilot.industrial_eval_v0370 import evaluate_real_industrial, write_evaluation
from timepilot.industrial_physical_v0370 import verify_industrial_parquet
from timepilot.industrial_oracle_v0370 import verify_oracle


def run(argv=None) -> int:
    parser = argparse.ArgumentParser(description='Real industrial corpus ingestion and domain-holdout QA')
    parser.add_argument('--source', choices=['nab', 'metropt3', 'factory'], required=True)
    parser.add_argument('--input', type=Path, required=True, help='NAB checkout root, MetroPT3 CSV, or factory CSV')
    parser.add_argument('--schema', type=Path, help='Required for factory CSV; explicit label timestamps')
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--models', nargs='+', default=['linear'], choices=['linear', 'forest'])
    parser.add_argument('--require-physical', action='store_true', help='Block release unless genuine Parquet QA passes')
    parser.add_argument('--max-model-rows', type=int, default=2_000_000)
    parser.add_argument('--validation-device', help='Immutable outer validation device ID')
    parser.add_argument('--test-device', help='Immutable outer final-test device ID')
    options = parser.parse_args(argv)
    out = options.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    if options.input.resolve() == (out / 'industrial_normalized.csv') or (
        options.schema and options.schema.resolve() == (out / 'DATA_CONTRACT.json')):
        parser.error('输出目录覆盖了原始输入或字段契约，请更换 --out')
    status = {'version': '0.37.0', 'source': options.source, 'status': 'blocked'}
    try:
        if options.source == 'nab':
            frame, provenance = load_nab(options.input)
        elif options.source == 'metropt3':
            frame, provenance = load_metropt(options.input)
        else:
            if not options.schema:
                raise ValueError('企业真实标签数据必须指定 --schema')
            frame, provenance = load_factory_csv(options.input, options.schema)
        if len(frame) > options.max_model_rows:
            raise ValueError('真实数据超出明确的模型计算预算；请调整 --max-model-rows')
        normalized = write_normalized(frame, provenance, out)
        holdout = None
        if bool(options.validation_device) != bool(options.test_device):
            raise ValueError('必须同时声明验证设备和最终测试设备')
        if options.validation_device:
            holdout = (options.validation_device, options.test_device)
        elif options.source == 'nab':
            fixed = json.loads((ROOT / 'benchmarks' / 'featurelab_v0370' / 'PUBLIC_NAB_PIN.json').read_text(encoding='utf-8'))
            holdout = (fixed['predeclared_holdout_devices']['validation'],
                       fixed['predeclared_holdout_devices']['test'])
        evaluation = evaluate_real_industrial(frame, provenance, models=options.models,
                                               max_model_rows=options.max_model_rows,
                                               holdout_devices=holdout)
        write_evaluation(evaluation, out)
        oracle_status = 'not_applicable'
        if options.source == 'nab':
            baseline = ROOT / 'benchmarks/featurelab_v0370/NAB_INDEPENDENT_ORACLE.json'
            oracle = verify_oracle(out, baseline, raw_root=options.input)
            (out / 'NAB_ORACLE_QA.json').write_text(json.dumps(oracle, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            oracle_status = oracle['status']
            if oracle_status != 'passed':
                evaluation['status'] = 'failed'
                evaluation['independent_oracle_status'] = oracle_status
                write_evaluation(evaluation, out)
        try:
            physical = verify_industrial_parquet(out / 'industrial_normalized.csv', out)
        except (ValueError, RuntimeError, AssertionError) as exc:
            physical = {'status':'failed', 'reason': type(exc).__name__ + ': ' + str(exc)}
        (out / 'PHYSICAL_QA.json').write_text(json.dumps(physical,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
        if options.require_physical and physical['status'] != 'passed':
            evaluation['status'] = 'blocked' if physical['status'] == 'blocked' else 'failed'
        status = {'version': '0.37.0', 'source': options.source,
                  'status': evaluation['status'], 'rows': len(frame),
                  'devices': provenance.devices, 'positive_count': provenance.positives,
                  'source_hashes': provenance.source_file_sha256,
                  'normalized_sha256': normalized['normalized_sha256'],
                  'physical_status': physical['status'],
                  'independent_nab_oracle_status': oracle_status,
                  'cross_time': evaluation['folds']['cross_time']['status'],
                  'cross_device': evaluation['folds']['cross_device']['status'],
                  'production_prediction_claim_allowed': False,
                  'results': 'CROSS_TIME_DEVICE_EVAL.json'}
    except (ValueError, OSError, KeyError, TypeError) as exc:
        status['reason'] = type(exc).__name__ + ': ' + str(exc)
    (out / 'ACCEPTANCE.json').write_text(json.dumps(status, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    print(json.dumps(status, indent=2, ensure_ascii=False))
    return 0 if status['status'] == 'passed' else 2 if status['status'] in ('partial', 'blocked') else 1


if __name__ == '__main__':
    raise SystemExit(run())
