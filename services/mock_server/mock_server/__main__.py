"""Mock server = the real server with replaying stages (no GPU, no pipeline).

  cd services/mock_server && ../gpu_server/.venv/bin/python -m mock_server [--port 8766] [--fixture <dir>]

env: VVLOG_MOCK_SPEED (10), VVLOG_MOCK_FAIL (<stage>:<CODE>), VVLOG_MOCK_DROP_SSE (<seconds>),
     VVLOG_MOCK_FIXTURE (fixtures/<name>), VVLOG_DATA_ROOT (a temp dir). The token is "mock".
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "gpu_server"))

from vvlog_server.config import Settings  # noqa: E402
from vvlog_server.main import create_app  # noqa: E402

from .replay import mock_planner  # noqa: E402


class DropSse:
    """Closes every SSE response after `seconds` (to exercise the app's reconnect with Last-Event-ID)."""

    def __init__(self, app, seconds: float):
        self.app, self.seconds = app, seconds

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].endswith("/events"):
            return await self.app(scope, receive, send)
        try:
            await asyncio.wait_for(self.app(scope, receive, send), timeout=self.seconds)
        except asyncio.TimeoutError:
            pass


def build(env: dict | None = None):
    e = dict(os.environ if env is None else env)
    e.setdefault("VVLOG_DATA_ROOT", tempfile.mkdtemp(prefix="vvlog_mock_data_"))
    e["VVLOG_API_TOKEN"] = "mock"
    e["VVLOG_BACKEND"] = "replay"
    s = Settings.from_env(e)
    app = create_app(s, planner=mock_planner(e))
    drop = e.get("VVLOG_MOCK_DROP_SSE")
    return DropSse(app, float(drop)) if drop else app


def main() -> None:
    import uvicorn
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8766)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--fixture", default=None)
    a = ap.parse_args()
    if a.fixture:
        os.environ["VVLOG_MOCK_FIXTURE"] = a.fixture
    print(f"vvlog mock server: http://{a.host}:{a.port}  token: mock  speed: x{os.environ.get('VVLOG_MOCK_SPEED', '10')}")
    uvicorn.run(build(), host=a.host, port=a.port, log_level="info")


if __name__ == "__main__":
    main()
