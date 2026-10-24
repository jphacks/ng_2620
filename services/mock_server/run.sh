#!/usr/bin/env sh
# Start the mock server (services/mock_server/README.md). The first run creates services/gpu_server/.venv
# with the server's dependencies (uv if present, else python3 -m venv + pip); later runs just start it.
#   sh services/mock_server/run.sh [--port 8766] [--fixture <dir>]
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
SRV="$HERE/../gpu_server"
PY="$SRV/.venv/bin/python"
if ! "$PY" -c "import fastapi, sse_starlette, numpy, PIL" 2>/dev/null; then
  echo "[mock] setting up $SRV/.venv (first run)"
  if command -v uv >/dev/null 2>&1; then
    uv venv -q "$SRV/.venv" -p 3.11 2>/dev/null || uv venv -q "$SRV/.venv"
    uv pip install -q -p "$PY" -e "$SRV"
  else
    python3 -m venv "$SRV/.venv"
    "$SRV/.venv/bin/pip" install -q -e "$SRV"
  fi
fi
command -v ffprobe >/dev/null 2>&1 || echo "[mock] warning: ffprobe not on PATH: uploads will be rejected (brew install ffmpeg)"
cd "$HERE"
exec "$PY" -m mock_server "$@"
