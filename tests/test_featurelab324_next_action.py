"""The user-visible next step never claims unavailable model or BI evidence."""
from timepilot.feature_auto_analysis import next_action_for_user


def test_next_action_tracks_persisted_stage_and_invalid_sources():
    cases=[
      ({'status':'needs_input','stage':'profile'},'partition_required'),
      ({'status':'failed','stage':'preview'},'failed_review'),
      ({'status':'profiling','stage':'profile'},'running'),
      ({'status':'ready','stage':'review','selection':[]},'choose_candidates'),
      ({'status':'ready','stage':'review','selection':['f1']},'confirm_materialization'),
      ({'status':'ready','stage':'materialized'},'evaluation_pending'),
      ({'status':'ready','stage':'evaluated'},'review_delivery'),
      ({'stale':True,'status':'ready','stage':'evaluated'},'rerun_source_changed'),
    ]
    for state,expected in cases:
        result=next_action_for_user(state)
        assert result['code']==expected and result['title'] and result['detail'] and result['action']
        assert '准确率提升' not in result['detail']
    assert next_action_for_user({'status':'ready','stage':'evaluated'})['can_continue']
