# vvlog ローカルアプリを起動する（ブラウザが開く。終了は Ctrl+C か、画面の「アプリを終了」）
Set-Location $PSScriptRoot
$py = if (Test-Path ".venv\Scripts\python.exe") { ".venv\Scripts\python.exe" } else { "python" }
& $py -m vvlog_server.launcher @args
exit $LASTEXITCODE
