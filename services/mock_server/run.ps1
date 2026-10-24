# Start the mock server on Windows (services/mock_server/README.md). The first run creates services\gpu_server\.venv.
#   powershell -ExecutionPolicy Bypass -File services\mock_server\run.ps1 [--port 8766] [--fixture <dir>]
$ErrorActionPreference = "Stop"
$Srv = Join-Path $PSScriptRoot "..\gpu_server"
$Py = Join-Path $Srv ".venv\Scripts\python.exe"
$ok = $false
if (Test-Path $Py) { & $Py -c "import fastapi, sse_starlette, numpy, PIL" 2>$null; $ok = ($LASTEXITCODE -eq 0) }
if (-not $ok) {
  Write-Host "[mock] setting up $Srv\.venv (first run)"
  if (Get-Command uv -ErrorAction SilentlyContinue) {
    uv venv -q (Join-Path $Srv ".venv"); uv pip install -q -p $Py -e $Srv
  } else {
    python -m venv (Join-Path $Srv ".venv"); & (Join-Path $Srv ".venv\Scripts\pip.exe") install -q -e $Srv
  }
}
if (-not (Get-Command ffprobe -ErrorAction SilentlyContinue)) { Write-Warning "ffprobe not on PATH: uploads will be rejected (winget install Gyan.FFmpeg)" }
Set-Location $PSScriptRoot
& $Py -m mock_server @args
