@echo off
rem Start the vvlog local app (opens the browser). Quit: Ctrl+C in this window, or the Quit button in Settings.
rem See README.md ("Launcher" section).
setlocal
cd /d "%~dp0"
set "PY=python"
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
"%PY%" -m vvlog_server.launcher %*
if errorlevel 1 pause
