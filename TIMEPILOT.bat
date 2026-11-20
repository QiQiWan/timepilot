@echo off
chcp 65001 >nul
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo [FAIL] Activate your EXISTING Python 3.12 environment.
  exit /b 2
)
set "PYTHONPATH=%CD%\src;%PYTHONPATH%"
REM Single command on Windows: TIMEPILOT.bat start
REM Automatically reuses or prepares Node 24 and builds Perspective when online.
REM Launcher selects a stable user-local home, preserving existing runtimes.
python scripts\launcher.py %*
exit /b %ERRORLEVEL%
