# Start the vvlog GPU server on the team's Windows RTX machine (see DEPLOY.md).
#   $env:VVLOG_API_TOKEN = "..."; $env:VVLOG_PYTHON = "C:\path\to\pipeline\.venv\Scripts\python.exe"; .\run_local.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
if (-not $env:VVLOG_API_TOKEN) { throw "set VVLOG_API_TOKEN" }
if (-not $env:VVLOG_DATA_ROOT) { $env:VVLOG_DATA_ROOT = Join-Path $PSScriptRoot "data" }
if (-not $env:VVLOG_CKPT_ROOT) { $env:VVLOG_CKPT_ROOT = (Resolve-Path (Join-Path $PSScriptRoot "..\..\weights")).Path }
if (-not $env:VVLOG_PYTHON) { $env:VVLOG_PYTHON = (Resolve-Path (Join-Path $PSScriptRoot "..\..\pipeline\.venv\Scripts\python.exe")).Path }
if (-not $env:VVLOG_PORT) { $env:VVLOG_PORT = "8765" }
if (-not (Test-Path ".venv\Scripts\python.exe")) {
  python -m venv .venv
  .venv\Scripts\pip install -q -e .
}
.venv\Scripts\python -m vvlog_server
