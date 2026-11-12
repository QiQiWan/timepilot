"""Fixed developer-synthetic corpus validity and deterministic scorer regression."""
from __future__ import annotations
import json
from pathlib import Path
from importlib.util import spec_from_file_location,module_from_spec

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'benchmarks/bi_nl/v2_fuzzy_cases.jsonl'

def test_corpus_has_50_or_more_nonoverwritten_developer_cases():
    cases=[json.loads(s) for s in SOURCE.read_text('utf8').splitlines() if s.strip()]
    assert len(cases)>=50
    assert len({x['case_id'] for x in cases})==len(cases)
    assert {x['category'][0] for x in cases}==set('ABCDEF')
    for case in cases:
        assert case['adjudication_status']=='developer_synthetic_unreviewed'
        assert case['reviewer_1'] is case['reviewer_2'] is None
        for field in ('utterance','expected_intents','feasibility','gold_fields','allowed_defaults','expected_tool_effects','expected_result_shape','unsafe_actions'):
            assert field in case
    # The v1 baseline must still be present and retain 99 cases.
    old=ROOT/'benchmarks/bi_nl/v1_fixed_cases.jsonl'
    assert len([x for x in old.read_text('utf8').splitlines() if x.strip()])==99


def test_scorer_reports_synthetic_only_and_never_fake_execution():
    spec=spec_from_file_location('tp_fuzzy_eval',ROOT/'scripts/evaluate_bi_fuzzy_v0235.py')
    mod=module_from_spec(spec);spec.loader.exec_module(mod)
    cases=[json.loads(s) for s in SOURCE.read_text('utf8').splitlines() if s.strip()]
    outcome=mod.evaluate(cases,engine_ready=True)
    assert outcome['annotation_status']=='developer_synthetic_unreviewed'
    assert outcome['count']==56
    assert outcome['real_statistical_executions']==outcome['actual_browser_renderings']==outcome['llm_calls']==0
    assert outcome['intent_accuracy']>.8
    no_engine=mod.evaluate(cases,engine_ready=False)
    assert no_engine['feasibility_accuracy'] is None
