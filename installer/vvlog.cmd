@echo off
rem vvlog ローカルアプリを起動する(ブラウザが開く。終了は Ctrl+C か、画面の「アプリを終了」)。
rem 初回だけ uv でサーバー用の Python 環境(環境 A)を作る。解析用の環境 B は画面のセットアップが作る(#32)。
setlocal
set "HERE=%~dp0"
if "%HERE:~-1%"=="\" set "HERE=%HERE:~0,-1%"
if not defined LOCALAPPDATA set "LOCALAPPDATA=%USERPROFILE%\AppData\Local"
set "STATE=%LOCALAPPDATA%\vvlog"
set "ENVA=%STATE%\envs\server"
set "UV=%HERE%\bin\uv.exe"

if not exist "%ENVA%\Scripts\python.exe" (
  echo [vvlog] 初回セットアップ: サーバー用の Python 環境を作ります(数分かかります)...
  "%UV%" venv "%ENVA%" -p 3.12 || goto :fail
)
rem 版が変わったときも入れ直せるよう毎回走らせる(済んでいれば一瞬で終わる)
"%UV%" pip install -q -p "%ENVA%\Scripts\python.exe" "%HERE%\server" || goto :fail

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
echo [vvlog] 起動に失敗しました。上のメッセージを確認してください。
pause
exit /b 1
