"""Composition root: settings -> adapters -> use cases -> FastAPI app.

  python -m vvlog_server            (VVLOG_API_TOKEN, VVLOG_DATA_ROOT, ... see config.py)
"""
from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import logging
import os
import secrets

from .adapters.events import JsonlEventBus
from .adapters.fixture_recorder import FixtureRecorder
from .adapters.fs import FsArtifactStore, FsAvatarStore, FsJobRepository, FsProjectRepository, FsUploadRepository
from .adapters.media_ffmpeg import FfmpegMedia
from .adapters.queue import InProcessJobQueue
from .adapters.subprocess_runner import SubprocessPipelineRunner
from .adapters.system import NvidiaSmiGpuInfo
from .application.deps import Deps
from .application.stages.planner import planner_for
from .application.usecases.maintenance import purge_expired
from .application.worker import Worker
from .config import Settings
from .interface.api import build_app
from .interface.local import bind_local_socket


def create_app(settings: Settings | None = None, runner_factory=None, planner=None):
    s = settings or Settings.from_env()
    root = s.data_root
    root.mkdir(parents=True, exist_ok=True)
    projects, jobs = FsProjectRepository(root), FsJobRepository(root)
    bus, queue = JsonlEventBus(root), InProcessJobQueue()
    store = FsArtifactStore(projects, jobs)
    recorder = FixtureRecorder(s.record_fixture, bus, store, projects) if s.record_fixture else None
    worker = Worker(jobs=jobs, queue=queue, bus=bus, store=store, projects=projects,
                    planner=planner or planner_for(s), runner_factory=runner_factory or SubprocessPipelineRunner,
                    settings=s, gpu_id=s.gpu_ids[0] if s.gpu_ids else "0",
                    on_succeeded=recorder.record if recorder else None)
    deps = Deps(settings=s, projects=projects, uploads=FsUploadRepository(root), avatars=FsAvatarStore(root),
                jobs=jobs, queue=queue, bus=bus, store=store, media=FfmpegMedia(), gpu=NvidiaSmiGpuInfo(worker.gpu_id),
                worker=worker)

    async def housekeeping():
        while True:
            try:
                await asyncio.to_thread(purge_expired, deps, s.artifact_ttl_hours)
            except Exception:  # a failed sweep must never take the server down
                logging.getLogger(__name__).exception("purge failed")
            await asyncio.sleep(3600)

    @contextlib.asynccontextmanager
    async def lifespan(_app):
        worker.recover()
        worker.start()
        sweeper = asyncio.create_task(housekeeping(), name="housekeeping") if s.artifact_ttl_hours > 0 else None
        yield
        if sweeper:
            sweeper.cancel()
        await worker.stop()

    return build_app(deps, lifespan=lifespan)


WEAK_TOKENS = {"token", "password", "changeme", "secret", "test", "test-token", "mock", "admin", "vvlog", "12345678"}


def weak_token_reason(token: str) -> str | None:
    """Why this API token should not guard a server other people can reach (None = fine)."""
    t = token.strip()
    if len(t) < 16:
        return f"短すぎます（{len(t)} 文字。16 文字以上にしてください）"
    if t.lower() in WEAK_TOKENS or len(set(t)) < 6:
        return "推測されやすい値です"
    return None


def main() -> None:
    import uvicorn
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    s = Settings.from_env()
    if s.local:
        # local app server: free port if VVLOG_PORT=0, a fresh token per launch unless one is given
        sock = bind_local_socket(s.port)
        s = dataclasses.replace(s, port=sock.getsockname()[1], api_token=s.api_token or secrets.token_hex(24))
        app = create_app(s)
        server = uvicorn.Server(uvicorn.Config(app, log_level="info"))

        def request_shutdown() -> None:  # POST /v1/local/shutdown -> uvicorn's graceful exit (same path as Ctrl+C)
            server.should_exit = True
        app.state.request_shutdown = request_shutdown
        print(f"VVLOG_LOCAL_READY http://127.0.0.1:{s.port}/", flush=True)  # the launcher (#31) opens this URL
        server.run(sockets=[sock])
        return
    if not s.api_token:
        raise SystemExit("VVLOG_API_TOKEN を設定してください（例: openssl rand -hex 24）")
    weak = weak_token_reason(s.api_token)
    if weak:
        if os.environ.get("VVLOG_ALLOW_WEAK_TOKEN") == "1":
            logging.getLogger(__name__).warning("VVLOG_API_TOKEN が弱い値です: %s", weak)
        else:
            raise SystemExit(f"VVLOG_API_TOKEN が弱い値です: {weak}\n"
                             "公開するサーバーのトークンは推測されにくい値にしてください（例: openssl rand -hex 24）。"
                             "手元だけで試すなら VVLOG_ALLOW_WEAK_TOKEN=1")
    uvicorn.run(create_app(s), host="127.0.0.1", port=s.port, log_level="info")


if __name__ == "__main__":
    main()
