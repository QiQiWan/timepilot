"""Use an already installed desktop Google Chrome for TimePilot browser QA.

No browser binaries are installed or downloaded. The browser is launched with
an isolated, temporary Playwright profile; the normal Chrome profile is not used.

Set TIMEPILOT_CHROME_PATH to a Chrome executable if installed outside default
locations. For macOS, either the executable or the Google Chrome.app bundle
may be supplied. TIMEPILOT_BROWSER_HEADLESS=0 opens a visible test window.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path
from typing import Mapping


class ChromeNotFoundError(RuntimeError):
    """No usable preinstalled Google Chrome executable was located."""


def chrome_executable(*, environment: Mapping[str, str] | None = None,
                      platform: str | None = None) -> Path:
    """Resolve an installed browser without invoking Playwright downloads."""
    env = os.environ if environment is None else environment
    system = sys.platform if platform is None else platform
    overridden = (env.get('TIMEPILOT_CHROME_PATH') or
                  env.get('TIMEPILOT_CHROMIUM_PATH') or '').strip()
    if overridden:
        candidate = Path(overridden).expanduser()
        if candidate.suffix.lower() == '.app':
            candidate = candidate / 'Contents' / 'MacOS' / candidate.stem
        if not candidate.is_file():
            raise ChromeNotFoundError(
                f'Chrome executable override does not exist: {candidate}. '
                'Set TIMEPILOT_CHROME_PATH to the installed Chrome executable.')
        return candidate.resolve()

    candidates: list[Path] = []
    if system == 'darwin':
        candidates += [
            Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'),
            Path.home() / 'Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
        ]
    elif system.startswith('win'):
        for key in ('PROGRAMFILES', 'PROGRAMFILES(X86)', 'LOCALAPPDATA'):
            base = env.get(key)
            if base:
                candidates.append(Path(base) / 'Google' / 'Chrome' / 'Application' / 'chrome.exe')
    else:
        for name in ('google-chrome', 'google-chrome-stable', 'chromium', 'chromium-browser'):
            found = shutil.which(name)
            if found:
                candidates.append(Path(found))
    for path in candidates:
        if path.is_file():
            return path.resolve()
    hint = ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
            if system == 'darwin' else
            "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"
            if system.startswith('win') else '/usr/bin/google-chrome')
    raise ChromeNotFoundError(
        'Google Chrome is not detected in standard installation locations. '
        f'Install Chrome yourself or set TIMEPILOT_CHROME_PATH to its executable (e.g. {hint}). '
        'This acceptance script will not download a browser.')


def chrome_headless(*, environment: Mapping[str, str] | None = None) -> bool:
    env = os.environ if environment is None else environment
    return env.get('TIMEPILOT_BROWSER_HEADLESS', '1').strip().lower() not in {
        '0', 'false', 'off', 'no',
    }


def chrome_launch_options(*, environment: Mapping[str, str] | None = None,
                          platform: str | None = None) -> dict:
    return {'headless': chrome_headless(environment=environment),
            'executable_path': str(chrome_executable(environment=environment, platform=platform))}


def launch_installed_chrome(playwright):
    """Launch local Chrome with Playwright; never use its downloaded Chromium."""
    return playwright.chromium.launch(**chrome_launch_options())


def main() -> int:
    parser = argparse.ArgumentParser(description='Check installed Chrome; never download a browser')
    parser.add_argument('--check', action='store_true', help='Check that Chrome exists')
    parser.add_argument('--smoke', action='store_true', help='Actually launch and close installed Chrome')
    args = parser.parse_args()
    try:
        options = chrome_launch_options()
        print('Local Google Chrome executable:', options['executable_path'], flush=True)
        print('Headless:', options['headless'], flush=True)
        if args.smoke:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(**options)
                try:
                    print('Detected Chrome version:', browser.version)
                finally:
                    browser.close()
        return 0
    except (ChromeNotFoundError, OSError) as exc:
        print('ERROR:', exc, file=sys.stderr)
        return 2
    except Exception as exc:
        print('ERROR: Could not launch installed Google Chrome:', exc, file=sys.stderr)
        return 3


if __name__ == '__main__':
    raise SystemExit(main())
