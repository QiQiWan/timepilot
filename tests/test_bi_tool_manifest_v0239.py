"""v0.23.9: no imaginary tools or unsafe feature enablement."""
from timepilot.bi_tool_registry import TOOLS, catalog, BIAction, validate_action
from timepilot.perspective_capabilities import FEATURES, UNSUPPORTED
from timepilot.bi_tool_manifest import for_workspace
from test_conversational_bi_v0231 import _env,_workspace


def test_all_declared_features_use_registered_tool_ids():
    assert all(x[0] in TOOLS for x in FEATURES.values())
    assert not set(FEATURES)&set(UNSUPPORTED)
    registry={v['tool'] for v in catalog()}
    assert registry==set(TOOLS)
    assert len(registry)>=20


def test_missing_engine_never_advertises_full_viewer_control(tmp_path):
    app,c,pid,aid,service=_env(tmp_path)
    try:
        ws=_workspace(c,pid,aid)
        manifest=for_workspace(service,pid,ws['workspace_id'])
        assert manifest['viewer_version']=='5.6.1'
        statuses={x['feature_id']:x for x in manifest['features']}
        assert statuses['schema.inspect']['available']
        assert not statuses['crossfilter']['available']
        assert not statuses['expression.exprtk']['available']
        enabled=[t for t in manifest['tools'] if t['available']]
        assert enabled
        assert all(t['tool'] in TOOLS for t in enabled)
        assert not any(t['effect_class']=='commit' for t in enabled)
        if not service.native.capabilities().get('perspective_virtual_server_ready'):
            assert not statuses['chart.line']['available']
            assert not statuses['viewer.theme']['available']
    finally:c.__exit__(None,None,None)


def test_viewer_and_query_args_cannot_be_arbitrary():
    import pytest
    with pytest.raises(Exception):validate_action(BIAction(tool='bi.viewer.theme.set',arguments={'theme':'<svg/onload=alert(1)>'}))
    with pytest.raises(Exception):validate_action(BIAction(tool='bi.pivot.split.set',arguments={'fields':['A1'], 'raw_sql':'DROP TABLE data'}))
