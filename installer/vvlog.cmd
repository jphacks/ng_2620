@echo off
rem vvlog launcher. Keep this file ASCII-only and CRLF: cmd.exe reads .cmd files in the console code page
rem (CP932 on Japanese Windows), so UTF-8 Japanese text here eats newlines / parens and breaks parsing.
rem First run creates the server Python env (env A) with uv. Env B (analysis) is created from the UI (#32).
setlocal
set "HERE=%~dp0"
if "%HERE:~-1%"=="\" set "HERE=%HERE:~0,-1%"
if not defined LOCALAPPDATA set "LOCALAPPDATA=%USERPROFILE%\AppData\Local"
set "STATE=%LOCALAPPDATA%\vvlog"
set "ENVA=%STATE%\envs\server"
set "UV=%HERE%\bin\uv.exe"

if not exist "%UV%" (
  echo [vvlog] uv.exe not found: "%UV%"
  goto :fail
)
if exist "%ENVA%\Scripts\python.exe" goto :install
echo [vvlog] First-time setup: creating the server Python environment. This takes a few minutes...
"%UV%" venv "%ENVA%" -p 3.12
if errorlevel 1 goto :fail

:install
rem Run every time so a new version gets reinstalled (instant when nothing changed)
"%UV%" pip install -q -p "%ENVA%\Scripts\python.exe" "%HERE%\server"
if errorlevel 1 goto :fail

set "VVLOG_BIN_DIR=%HERE%\bin"
set "VVLOG_UV=%HERE%\bin\uv.exe"
set "VVLOG_SPA_DIR=%HERE%\spa"
set "VVLOG_PIPELINE_ROOT=%HERE%\pipeline"
set "VVLOG_WEIGHTS_MANIFEST=%HERE%\weights\manifest.json"
set "VVLOG_BOOTSTRAP_SPEC=%HERE%\server\bootstrap-spec.json"
"%ENVA%\Scripts\python.exe" -m vvlog_server.launcher %*
if errorlevel 1 goto :fail
exit /b 0

:fail
echo.
echo [vvlog] Failed to start. See the messages above.
echo [vvlog] Logs: "%STATE%\logs"
pause
exit /b 1
