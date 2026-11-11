"""Regression: use a locally installed Chrome without Playwright downloads."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def helper():
    spec = importlib.util.spec_from_file_location(
        "timepilot_browser_detection", ROOT / "scripts/system_chrome.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_chrome_override_is_selected(tmp_path):
    module = helper()
    browser = tmp_path / "Google Chrome"
    browser.write_bytes(b"fake local executable")
    env = {"TIMEPILOT_CHROME_PATH": str(browser),
           "TIMEPILOT_BROWSER_HEADLESS": "0"}
    options = module.chrome_launch_options(environment=env, platform="darwin")
    assert options == {"headless": False, "executable_path": str(browser)}


def test_mac_app_bundle_is_supported(tmp_path):
    module = helper()
    app = tmp_path / "Google Chrome.app"
    binary = app / "Contents/MacOS/Google Chrome"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"fake local executable")
    result = module.chrome_executable(
        environment={"TIMEPILOT_CHROME_PATH": str(app)}, platform="darwin")
    assert result == binary


def test_missing_explicit_chrome_fails_without_fallback(tmp_path):
    module = helper()
    with pytest.raises(module.ChromeNotFoundError, match="does not exist"):
        module.chrome_executable(
            environment={"TIMEPILOT_CHROME_PATH": str(tmp_path / "not-here")},
            platform="darwin")


def test_headless_default_and_false_options():
    module = helper()
    assert module.chrome_headless(environment={}) is True
    for flag in ["0", "false", "OFF", "no"]:
        assert module.chrome_headless(environment={"TIMEPILOT_BROWSER_HEADLESS": flag}) is False


def test_mac_per_user_install_discovery(tmp_path, monkeypatch):
    """Mock all candidate locations: the host may have system Chrome installed."""
    module = helper()
    fakehome = tmp_path / "home"
    binary = fakehome / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"fake local executable")
    monkeypatch.setattr(module.Path, "home", lambda: fakehome)
    monkeypatch.setattr(module.Path, "is_file", lambda candidate: candidate == binary)
    assert module.chrome_executable(environment={}, platform="darwin") == binary


def test_mac_system_chrome_preferred_when_both_locations_exist(tmp_path, monkeypatch):
    """Test the real system-first policy without depending on installed Chrome."""
    module = helper()
    fakehome = tmp_path / "home"
    per_user = fakehome / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    system_chrome = module.Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
    monkeypatch.setattr(module.Path, "home", lambda: fakehome)
    monkeypatch.setattr(module.Path, "is_file", lambda candidate: candidate in (system_chrome, per_user))
    assert module.chrome_executable(environment={}, platform="darwin") == system_chrome


def test_mac_no_chrome_reports_actionable_error(tmp_path, monkeypatch):
    """Browser absence reports a user action and never initiates a download."""
    module = helper()
    monkeypatch.setattr(module.Path, "home", lambda: tmp_path / "home")
    monkeypatch.setattr(module.Path, "is_file", lambda candidate: False)
    with pytest.raises(module.ChromeNotFoundError, match="TIMEPILOT_CHROME_PATH"):
        module.chrome_executable(environment={}, platform="darwin")


def test_launch_uses_exact_chrome_and_never_bundled_browser(tmp_path, monkeypatch):
    module = helper()
    binary = tmp_path / "chrome"
    binary.write_bytes(b"fake local executable")
    monkeypatch.setenv("TIMEPILOT_CHROME_PATH", str(binary))

    class BrowserType:
        def launch(self, **kwargs):
            assert kwargs["executable_path"] == str(binary)
            assert "channel" not in kwargs
            return "fake chrome browser"

    class Playwright:
        chromium = BrowserType()

    assert module.launch_installed_chrome(Playwright()) == "fake chrome browser"


def test_acceptance_scripts_never_download_browser():
    launcher = (ROOT / 'scripts/launcher.py').read_text()
    shell = (ROOT / 'TIMEPILOT.sh').read_text()
    windows = (ROOT / 'TIMEPILOT.bat').read_text()
    browser = (ROOT / 'scripts/accept_native_bi_live.py').read_text()
    for text in (launcher, shell, windows, browser):
        assert 'playwright install chromium' not in text
        assert 'chromium.launch(headless=True)' not in text
    assert "'scripts/system_chrome.py','--check'" in launcher
    assert 'launch_installed_chrome(p)' in browser
