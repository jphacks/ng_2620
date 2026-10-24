#!/usr/bin/env bash
# Start the vvlog GPU server on a Linux GPU VM (see DEPLOY.md).
#   VVLOG_API_TOKEN=... VVLOG_PYTHON=/path/to/pipeline/venv/bin/python ./run_linux.sh
set -euo pipefail
cd "$(dirname "$0")"
: "${VVLOG_API_TOKEN:?set VVLOG_API_TOKEN}"
export VVLOG_DATA_ROOT="${VVLOG_DATA_ROOT:-$PWD/data}"
export VVLOG_CKPT_ROOT="${VVLOG_CKPT_ROOT:-$PWD/../../weights}"
export VVLOG_PYTHON="${VVLOG_PYTHON:-python3}"
export VVLOG_PORT="${VVLOG_PORT:-8765}"
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
  .venv/bin/pip install -q -e .
fi
exec .venv/bin/python -m vvlog_server
