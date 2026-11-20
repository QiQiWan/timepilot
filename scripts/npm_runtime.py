"""Resolve npm without relying on Windows .cmd implicit shell dispatch.

Portable official Node ZIP includes node.exe and node_modules/npm/bin/npm-cli.js.
Calling that CLI through node.exe avoids a fragile PATHEXT/shell lookup and
keeps all arguments as subprocess argv. macOS/Linux retain normal npm commands.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def npm_argv(*arguments: str, windows: bool | None = None,
             search_path: str | None = None) -> list[str]:
    windows = (sys.platform == 'win32') if windows is None else windows
    if not windows:
        return ['npm', *arguments]
    node = shutil.which('node', path=search_path)
    if node:
        cli = Path(node).parent / 'node_modules' / 'npm' / 'bin' / 'npm-cli.js'
        if cli.is_file():
            return [str(Path(node).resolve()), str(cli.resolve()), *arguments]
    npm = shutil.which('npm', path=search_path)
    if npm:
        return [npm, *arguments]
    raise FileNotFoundError('npm was not found near the active Node installation')
