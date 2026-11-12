"""Regression guard for the BI UX consolidation in v0.24.6."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / 'src' / 'timepilot' / 'static'


def source(name: str) -> str:
    return (STATIC / name).read_text(encoding='utf-8')


def test_dialogs_loaded_before_consumers_and_single_helper_title():
    html = source('index.html')
    assert html.index('bi-dialogs.js') < html.index('bi-conversation.js')
    assert html.index('bi-dialogs.js') < html.index('native-bi.js')
    assert 'bi-help.js' in html
    assistant = source('bi-assistant-fab.js')
    assert assistant.count('class="tp-fab-close"') == 1
    assert 'ResizeObserver' in assistant and 'pointermove' in assistant
    assert 'tp-fab-resize-hint' in assistant


def test_no_browser_native_prompts_in_bi_controls():
    import re
    for name in ('native-bi.js', 'bi-conversation.js'):
        body = source(name)
        assert not re.search(r'(?<![.\w])(?:window\.)?prompt\s*\(', body), name
        assert not re.search(r'(?<![.\w])(?:window\.)?confirm\s*\(', body), name
        assert 'TimePilotBIDialogs' in body


def test_manual_and_advanced_controls_remain_available():
    layout = source('bi-workspace-layout.js')
    for needle in ('data-workspace-action="manual"', '使用手册', '高级操作',
                   '#native-query', '#native-edit'):
        assert needle in layout
    help_ui = source('bi-help.js')
    assert 'TimePilotBIHelp' in help_ui
    assert '快速开始' in help_ui
    assert (ROOT / 'docs' / 'BI_USER_GUIDE_CN.md').is_file()
