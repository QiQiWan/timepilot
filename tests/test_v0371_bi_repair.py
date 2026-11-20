"""v0.37.1 deterministic safety and HTTP/WS diagnosis regression."""
from pathlib import Path
import sys
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from timepilot.api import create_app

ROOT=Path(__file__).resolve().parents[1]
STATIC=ROOT/'src/timepilot/static'

def test_single_entry_point_for_every_bi_surface():
    shell=(STATIC/'index.html').read_text()
    assert f'/assets/perspective-loader.js?v={__import__("timepilot").__version__}' in shell
    assert shell.index('perspective-loader.js')<shell.index('native-bi.js')
    for name in ('native-bi.js','project-bi-ui.js','data-assets-ui.js','feature-perspective-bridge.js'):
        source=(STATIC/name).read_text()
        assert 'TimePilotPerspectiveLoader.load()' in source
        assert "import('/assets/native-bi/perspective-entry.js" not in source
        assert '/perspective-entry.js?v=' not in source

def test_feature_link_retains_pinned_materialization():
    code=(STATIC/'feature-shell.js').read_text()
    assert "'&auto_bi='+u(proof.artifact.artifact_id)" in code
    assert "'&auto_sid='+u(s.autoSession.session_id)" in code
    assert 'ctx.navigate(uri)' in code
    assert "if(document.getElementById('tp-analysis-workspace'))window.TimePilotAnalysisWorkspace?.openTool('bi')" not in code

def test_bi_agent_request_does_not_duplicate_prefix():
    source=(STATIC/'bi-conversation.js').read_text()
    assert "ctx.req('/api/v1/projects/" not in source
    assert "ctx.req('/projects/" in source
    assert '不能包含重复 /api/v1' in (STATIC/'app.js').read_text()

def test_websocket_diagnostics_and_cross_origin_rejection(tmp_path):
    with TestClient(create_app(home=tmp_path,embedded_worker=False)) as client:
        result=client.get('/api/v1/native-bi/ws-status')
        assert result.status_code==200,result.text
        assert result.json()['active_connections']==0
        assert result.json()['max_connections']>=1
        trace='tp_v0371_denied_0001'
        with pytest.raises(WebSocketDisconnect) as caught:
            with client.websocket_connect(
                    f'/api/v1/native-bi/assets/missing/versions/base/ws?tp_trace={trace}',
                    headers={'origin':'https://outside.invalid'}):
                pass
        assert caught.value.code==1008
        result=client.get('/api/v1/native-bi/ws-status',params={'tp_trace':trace})
        assert result.status_code==200
        info=result.json()['last_failure']
        assert info['stage']=='handshake' and info['code']=='origin_denied'
        assert info['close_code']==1008
        assert result.json()['active_connections']==0
        bad=client.get('/api/v1/native-bi/ws-status',params={'tp_trace':'bad input!'});
        assert bad.status_code==422

def test_feature_comparison_keeps_source_on_panel_failure():
    src=(ROOT/'frontend/native-bi/src/main.js').read_text()
    assert '原始数据已加载，附加特征面板失败' in src
    assert "'timepilot_feature_artifact'" in src
    assert 'tp-feature-result' in src
    assert "if(Number(lastComparison?.panel_count)<2)" in (STATIC/'feature-perspective-bridge.js').read_text()

def test_real_chrome_cycles_are_never_substituted_with_chromium():
    sys.path.insert(0,str(ROOT/'scripts'))
    from featurelab371_chrome_cycles import main
    from featurelab365_chrome_journey import require_actual_google_chrome,Blocked
    with pytest.raises(Blocked):require_actual_google_chrome('/usr/bin/chromium')
    assert 'same_document_bi_feature_switch_cycles' in (ROOT/'scripts/featurelab365_chrome_journey.py').read_text()
    assert "'--bi-cycles','30'" in (ROOT/'scripts/featurelab371_chrome_cycles.py').read_text()

def test_fallback_has_nonmisleading_aggregate_when_all_columns_are_numeric():
    source=(STATIC/'native-bi.js').read_text(encoding='utf-8')
    assert "preferredGroup=dimensionOptions[0]?.name||'__all__'" in source
    assert '全体数据（不分组）' in source
    assert "group_by:group==='__all__'?[]:[group]" in source
    assert "metric==='__count__'?'count':'avg'" in source
    assert "!/^(time|timestamp|date|id|index|row_id|order_index)$/i.test(c.name)" in source


def test_real_chrome_ci_must_record_all_thirty_roundtrips():
    workflow=(ROOT/'.github/workflows/ci.yml').read_text(encoding='utf-8')
    assert 'scripts/featurelab371_chrome_cycles.py' in workflow
    assert "stages[0]['completed_cycles']==30" in workflow
    assert "len(stages[0]['steps'])==60" in workflow
    assert 'no_duplicate_custom_elements' in workflow


def test_perspective_transport_teardown_is_serialized_across_page_navigation():
    source=(ROOT/'frontend/native-bi/src/main.js').read_text(encoding='utf-8')
    assert 'let releaseChain = Promise.resolve()' in source
    assert 'releaseChain.catch(() => {}).then(() => releasePerspectiveResources(previous, bounded))' in source
